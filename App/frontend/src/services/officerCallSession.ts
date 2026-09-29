/**
 * The officer's own call — owned here, outside React, so it survives navigation.
 *
 * The console's pages each mount their own `StaffGuard`; audio owned by a page
 * (the old `useOfficerAudio`) was torn down the moment the officer opened a
 * ticket mid-call, and the caller heard nothing. This module holds the audio
 * socket, the microphone and the player for the whole tab, and mirrors its
 * state into `useCallConsoleStore.activeCall` for the dock and the Desk.
 *
 *     idle → claiming → connecting → bridged → ending → idle
 *
 * Taking a call claims it first (so the caller is held for this officer), then
 * asks for the microphone, then opens the audio bridge — the server announces
 * the officer to the caller when the bridge opens. While not idle, leaving the
 * page asks for confirmation.
 */

import { getAuthToken } from '@/lib/authSession';
import { resetAudioLevels, setInputLevel, setOutputLevel } from '@/services/audioLevelBus';
import { callsApi } from '@/services/callsApi';
import { PCMPlayer } from '@/services/pcmPlayer';
import { LiveKitCallSession } from '@/services/liveKitCallSession';
import { AudioRecorder } from '@/services/voiceService';
import { type ActiveCall, type SessionState, useCallConsoleStore } from '@/store/useCallConsoleStore';

let ws: WebSocket | null = null;
let player: PCMPlayer | null = null;
let stopMic: (() => void) | null = null;
let liveKitSession: LiveKitCallSession | null = null;
let transferInProgress = false;
let mediaDisconnectedDuringTransfer = false;
let muted = false;
let current: ActiveCall | null = null;

function publish(next: ActiveCall | null): void {
  current = next;
  useCallConsoleStore.getState().setActiveCall(next);
  syncUnloadGuard();
}

function update(patch: Partial<ActiveCall>): void {
  if (!current) return;
  const stateChanged = patch.state !== undefined && patch.state !== current.state;
  publish({ ...current, ...patch, since: stateChanged ? Date.now() : current.since });
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  event.preventDefault();
  // Chrome needs returnValue set to show its prompt.
  event.returnValue = '';
}

let unloadGuarded = false;
function syncUnloadGuard(): void {
  if (typeof window === 'undefined') return;
  const want = Boolean(current && current.state !== 'idle');
  if (want && !unloadGuarded) window.addEventListener('beforeunload', onBeforeUnload);
  if (!want && unloadGuarded) window.removeEventListener('beforeunload', onBeforeUnload);
  unloadGuarded = want;
}

function teardownAudio(): void {
  if (liveKitSession) {
    void liveKitSession.close();
    liveKitSession = null;
  }
  if (stopMic) {
    try {
      stopMic();
    } catch {
      /* already stopped */
    }
    stopMic = null;
  }
  if (player) {
    player.close();
    player = null;
  }
  const socket = ws;
  ws = null;
  if (socket) {
    socket.onclose = null;
    try {
      socket.close();
    } catch {
      /* already closed */
    }
  }
  resetAudioLevels();
}

function finish(error: string | null = null): void {
  teardownAudio();
  muted = false;
  if (error && current) {
    // Leave the reason on screen; the officer clears it by taking another call.
    publish({ ...current, state: 'idle', error });
  } else {
    publish(null);
  }
}

/** The call this tab is on, if any. */
export function getActiveCall(): ActiveCall | null {
  return current;
}

export function sessionState(): SessionState {
  return current?.state ?? 'idle';
}

/**
 * Take a waiting call: claim it, then join its audio. Throws the claim's
 * `ClaimConflictError` when another officer got there first.
 */
export async function takeCall(callId: string): Promise<void> {
  if (current && current.state !== 'idle') {
    throw new Error('You are already on a call');
  }
  publish({ callId, state: 'claiming', since: Date.now(), muted: false, officerName: '', error: null });
  let officerName = '';
  try {
    officerName = (await callsApi.claimCall(callId)).officer_name;
  } catch (err) {
    publish(null);
    throw err;
  }
  update({ officerName });
  await joinCall(callId);
}

async function joinCall(callId: string): Promise<void> {
  update({ state: 'connecting' });
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(
    `${protocol}//${window.location.host}/api/v1/admin/calls/${encodeURIComponent(callId)}/audio`,
  );
  socket.binaryType = 'arraybuffer';
  ws = socket;
  socket.onopen = () => {
    socket.send(JSON.stringify({ type: 'authenticate', access_token: getAuthToken() }));
  };
  socket.onmessage = (event: MessageEvent) => {
    if (event.data instanceof ArrayBuffer) {
      player?.push(event.data);
    } else if (typeof event.data === 'string') {
      try {
        const message = JSON.parse(event.data);
        if (ws !== socket) return;
        if (message?.type === 'livekit_ready') {
          // One media session per call leg: a repeated `livekit_ready` replaces
          // the old room connection instead of leaving it open alongside.
          if (liveKitSession) void liveKitSession.close();
          const session = new LiveKitCallSession({
            onMessage: () => {},
            onLevels: ({ input, output }) => {
              if (liveKitSession !== session) return;
              setInputLevel(muted || Boolean(current?.onHold) ? 0 : input);
              setOutputLevel(output);
            },
            onReconnecting: () => {
              if (!transferInProgress && liveKitSession === session && current?.state === 'bridged') {
                update({ state: 'reconnecting' });
              }
            },
            onReconnected: () => {
              if (!transferInProgress && liveKitSession === session && current?.state === 'reconnecting') {
                update({ state: 'bridged' });
              }
            },
            onDisconnected: () => {
              if (
                liveKitSession === session
                && (current?.state === 'bridged' || current?.state === 'reconnecting')
              ) {
                if (transferInProgress) {
                  mediaDisconnectedDuringTransfer = true;
                  return;
                }
                finish('Call media disconnected.');
              }
            },
          });
          liveKitSession = session;
          void session.connect({
            transport: 'livekit',
            url: String(message.url || ''),
            room: String(message.room || ''),
            identity: String(message.identity || ''),
            token: String(message.token || ''),
          }, false).then(async () => {
            if (liveKitSession === session) {
              await session.setMuted(muted || Boolean(current?.onHold));
              update({ state: 'bridged' });
            }
          }).catch(async () => {
            await callsApi.releaseCall(callId).catch(() => {});
            finish('Call media could not connect. The call went back to the queue.');
          });
        } else if (message?.type === 'authenticated' && ws === socket) {
          // Compatibility transport for local/demo deployments. Production
          // sends `livekit_ready` next and never streams PCM on this socket.
          update({ state: 'bridged' });
          void startLegacyOfficerMedia(callId, socket);
        }
      } catch {
        /* Ignore non-JSON control frames. */
      }
    }
  };
  socket.onclose = (event: CloseEvent) => {
    if (ws !== socket) return;
    ws = null;
    if (transferInProgress) {
      mediaDisconnectedDuringTransfer = true;
    } else if (current?.state === 'ending') {
      teardownAudio();
      update({ state: 'wrap_up' });
    } else if (current?.state === 'wrap_up') {
      /* already in wrap-up */
    } else if (event.code === 4409) {
      finish('Another officer has this call.');
    } else if (current?.state === 'connecting') {
      finish('The call audio could not connect.');
    } else {
      finish('The call ended.');
    }
  };
}

async function startLegacyOfficerMedia(callId: string, socket: WebSocket): Promise<void> {
  if (ws !== socket || player || stopMic) return;
  const nextPlayer = new PCMPlayer(16000);
  nextPlayer.onLevel((level) => setOutputLevel(level));
  await nextPlayer.init().catch(() => {});
  player = nextPlayer;
  try {
    stopMic = await new AudioRecorder().startStreaming(
      (chunk: ArrayBuffer) => {
        const samples = new Int16Array(chunk);
        let sum = 0;
        for (let i = 0; i < samples.length; i += 2) sum += samples[i] * samples[i];
        setInputLevel(muted || Boolean(current?.onHold) ? 0 : Math.sqrt(sum / Math.max(1, samples.length / 2)) / 6000);
        if (!muted && !current?.onHold && ws === socket && socket.readyState === WebSocket.OPEN) socket.send(chunk);
      },
      { echoCancellation: true, noiseSuppression: true },
    );
  } catch {
    await callsApi.releaseCall(callId).catch(() => {});
    finish('Microphone access was denied — the call went back to the queue.');
  }
}

export function toggleMute(): void {
  if (!current || current.state !== 'bridged') return;
  muted = !muted;
  if (liveKitSession) void liveKitSession.setMuted(muted || Boolean(current.onHold));
  if (muted) setInputLevel(0);
  update({ muted });
}

export async function toggleHold(targetOn?: boolean): Promise<void> {
  if (!current || current.state !== 'bridged') return;
  const on = targetOn !== undefined ? targetOn : !current.onHold;
  try {
    await callsApi.holdCall(current.callId, on);
    if (liveKitSession) await liveKitSession.setMuted(on || muted);
    update({ onHold: on });
  } catch (err) {
    update({ error: (err as Error).message || 'Could not change hold status' });
  }
}

export async function transferCall(payload: { team?: string; officer_id?: string; note?: string }): Promise<void> {
  if (!current || current.state !== 'bridged') return;
  const { callId } = current;
  transferInProgress = true;
  mediaDisconnectedDuringTransfer = false;
  try {
    await callsApi.transferCall(callId, payload);
    teardownAudio();
    update({ state: 'wrap_up' });
  } catch (err) {
    if (mediaDisconnectedDuringTransfer) {
      finish('Call media disconnected while the transfer failed.');
    } else {
      update({ error: (err as Error).message || 'Could not transfer the call' });
    }
    throw err;
  } finally {
    transferInProgress = false;
    mediaDisconnectedDuringTransfer = false;
  }
}

/** End the call: the caller hears a closing line, then the server hangs up both sides. */
export async function endCall(): Promise<void> {
  if (!current || current.state !== 'bridged') return;
  const { callId } = current;
  update({ state: 'ending' });
  try {
    await callsApi.endCall(callId);
  } catch (err) {
    update({ state: 'bridged', error: (err as Error).message || 'Could not end the call' });
    return;
  }
  // If the server socket closed already:
  if (!ws) {
    teardownAudio();
    update({ state: 'wrap_up' });
  }
}

export async function wrapUpCall(payload: {
  outcome: 'resolved' | 'follow_up' | 'callback' | 'referred' | 'abandoned' | 'ai_resolved';
  note: string;
  ticket_action?: 'resolve' | 'keep_open' | 'create';
  rating?: number;
  rating_note?: string;
}): Promise<void> {
  if (!current || (current.state !== 'wrap_up' && current.state !== 'bridged')) return;
  const { callId } = current;
  try {
    await callsApi.wrapUpCall(callId, payload);
    finish();
  } catch (err) {
    update({ error: (err as Error).message || 'Could not save wrap-up' });
    throw err;
  }
}

export function finishWrapUp(): void {
  finish();
}

/** Clear a finished call's leftover message. */
export function dismissSessionError(): void {
  if (current && current.state === 'idle') publish(null);
}

/** Tests only. */
export function resetSessionForTests(): void {
  teardownAudio();
  transferInProgress = false;
  mediaDisconnectedDuringTransfer = false;
  muted = false;
  publish(null);
}
