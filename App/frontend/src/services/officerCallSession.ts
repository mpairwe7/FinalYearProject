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

import { appendAuthToken } from '@/lib/authSession';
import { resetAudioLevels, setInputLevel, setOutputLevel } from '@/services/audioLevelBus';
import { callsApi } from '@/services/callsApi';
import { PCMPlayer } from '@/services/pcmPlayer';
import { AudioRecorder } from '@/services/voiceService';
import { type ActiveCall, type SessionState, useCallConsoleStore } from '@/store/useCallConsoleStore';

let ws: WebSocket | null = null;
let player: PCMPlayer | null = null;
let stopMic: (() => void) | null = null;
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
  const nextPlayer = new PCMPlayer(16000);
  nextPlayer.onLevel((level) => setOutputLevel(level));
  await nextPlayer.init().catch(() => {});
  player = nextPlayer;

  // The microphone before the bridge: the caller should never hear an officer
  // who cannot speak yet. The claim already holds the call while we ask.
  try {
    stopMic = await new AudioRecorder().startStreaming(
      (chunk: ArrayBuffer) => {
        const samples = new Int16Array(chunk);
        let sum = 0;
        for (let i = 0; i < samples.length; i += 2) sum += samples[i] * samples[i];
        setInputLevel(muted ? 0 : Math.sqrt(sum / Math.max(1, samples.length / 2)) / 6000);
        if (!muted && ws && ws.readyState === WebSocket.OPEN) ws.send(chunk);
      },
      { echoCancellation: true, noiseSuppression: true },
    );
  } catch {
    await callsApi.releaseCall(callId).catch(() => {});
    finish('Microphone access was denied — the call went back to the queue.');
    return;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(
    appendAuthToken(`${protocol}//${window.location.host}/api/v1/admin/calls/${encodeURIComponent(callId)}/audio`),
  );
  socket.binaryType = 'arraybuffer';
  ws = socket;
  socket.onopen = () => {
    if (ws === socket) update({ state: 'bridged' });
  };
  socket.onmessage = (event: MessageEvent) => {
    if (event.data instanceof ArrayBuffer) player?.push(event.data);
  };
  socket.onclose = (event: CloseEvent) => {
    if (ws !== socket) return;
    ws = null;
    if (current?.state === 'ending') {
      finish();
    } else if (event.code === 4409) {
      finish('Another officer has this call.');
    } else if (current?.state === 'connecting') {
      finish('The call audio could not connect.');
    } else {
      finish('The call ended.');
    }
  };
}

export function toggleMute(): void {
  if (!current || current.state !== 'bridged') return;
  muted = !muted;
  if (muted) setInputLevel(0);
  update({ muted });
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
  // The server closes the audio socket once the caller is hung up; if it is
  // already gone, finish here.
  if (!ws) finish();
}

/** Clear a finished call's leftover message. */
export function dismissSessionError(): void {
  if (current && current.state === 'idle') publish(null);
}

/** Tests only. */
export function resetSessionForTests(): void {
  teardownAudio();
  muted = false;
  publish(null);
}
