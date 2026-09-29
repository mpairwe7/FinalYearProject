import {
  createAudioAnalyser,
  type LocalAudioTrack,
  type LocalTrackPublication,
  type RemoteAudioTrack,
  type RemoteParticipant,
  type RemoteTrack,
  Room,
  RoomEvent,
  Track,
} from 'livekit-client';

export interface LiveKitCallCredentials {
  transport: 'livekit';
  url: string;
  room: string;
  identity: string;
  token: string;
}

/** Microphone and call-audio levels, 0..1, for the call orb and meters. */
export interface CallAudioLevels {
  input: number;
  output: number;
}

export interface LiveKitCallSessionCallbacks {
  onMessage: (message: Record<string, unknown>) => void;
  onDisconnected: () => void;
  onReconnecting?: () => void;
  onReconnected?: () => void;
  /**
   * The browser refused to start call audio until the next user gesture
   * (autoplay policy, most often iOS Safari). The session retries on the next
   * tap or key press by itself; the UI can also offer a button that calls
   * `resumeAudio()`.
   */
  onAudioBlockedChange?: (blocked: boolean) => void;
  /** About twenty readings a second while connected; not called without audio. */
  onLevels?: (levels: CallAudioLevels) => void;
}

/**
 * Only the backend's agent may send call events over the data channel. The
 * server-side grants already stop callers, officers and listeners publishing
 * data; this is the client-side half of the same rule.
 */
const AGENT_IDENTITY_PREFIX = 'ura-agent-';
const LEVEL_INTERVAL_MS = 50;
/**
 * LiveKit's analyser reads conversational speech at roughly 0.3–0.5; the orb
 * was tuned for the PCM path, where normal talking fills it. Doubling keeps the
 * two transports looking the same.
 */
const LEVEL_GAIN = 2;
/** An input level above this counts as the caller speaking (after the gain). */
export const SPEAKING_LEVEL = 0.3;
const GESTURES = ['pointerdown', 'keydown', 'touchend'] as const;

/**
 * `wss://` everywhere; `ws://` only when the page itself is plain HTTP (a local
 * demo). On an HTTPS page a `ws://` media URL would send the room token in the
 * clear, and browsers block it as mixed content anyway.
 */
export function isAllowedMediaUrl(url: string, pageProtocol: string): boolean {
  if (url.startsWith('wss://')) return true;
  return url.startsWith('ws://') && pageProtocol === 'http:';
}

type Analyser = ReturnType<typeof createAudioAnalyser>;

/** WebRTC media session for one taxpayer or officer call leg. */
export class LiveKitCallSession {
  private room: Room | null = null;
  private audioElements = new Set<HTMLMediaElement>();
  private callbacks: LiveKitCallSessionCallbacks;
  private closed = false;
  private connected = false;
  private muted = false;
  private audioBlocked = false;
  private gestureUnlock: (() => void) | null = null;
  private analysers = new Map<string, { remote: boolean; analyser: Analyser }>();
  private levelTimer: ReturnType<typeof setInterval> | null = null;

  constructor(callbacks: LiveKitCallSessionCallbacks) {
    this.callbacks = callbacks;
  }

  async connect(credentials: LiveKitCallCredentials, publishMicrophone = true): Promise<void> {
    if (typeof window === 'undefined') throw new Error('LiveKit audio requires a browser');
    if (!isAllowedMediaUrl(credentials.url, window.location.protocol)) {
      throw new Error('The call media server URL is invalid');
    }

    const room = new Room({ adaptiveStream: true, dynacast: true });
    this.room = room;
    room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
      if (track.kind !== Track.Kind.Audio || this.closed) return;
      const element = track.attach() as HTMLMediaElement;
      element.autoplay = true;
      element.setAttribute('playsinline', '');
      element.style.position = 'fixed';
      element.style.width = '1px';
      element.style.height = '1px';
      element.style.opacity = '0';
      element.style.pointerEvents = 'none';
      document.body.appendChild(element);
      this.audioElements.add(element);
      void element.play().catch(() => {
        // Autoplay refused: the room reports it through
        // AudioPlaybackStatusChanged, which arms the gesture retry below.
        this.syncPlaybackState();
      });
      this.watchLevel(track.sid ?? `remote-${this.analysers.size}`, track as RemoteAudioTrack, true);
    });
    room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
      this.detachTrack(track);
      if (track.sid) this.unwatchLevel(track.sid);
    });
    room.on(RoomEvent.LocalTrackPublished, (publication: LocalTrackPublication) => {
      const track = publication.track;
      if (track && track.kind === Track.Kind.Audio) {
        this.watchLevel(publication.trackSid, track as LocalAudioTrack, false);
      }
    });
    room.on(RoomEvent.LocalTrackUnpublished, (publication: LocalTrackPublication) => {
      this.unwatchLevel(publication.trackSid);
    });
    room.on(RoomEvent.DataReceived, (payload: Uint8Array, participant?: RemoteParticipant) => {
      if (!participant?.identity.startsWith(AGENT_IDENTITY_PREFIX)) return;
      try {
        const message = JSON.parse(new TextDecoder().decode(payload));
        if (message && typeof message === 'object' && !Array.isArray(message)) {
          this.callbacks.onMessage(message as Record<string, unknown>);
        }
      } catch {
        // Ignore non-JSON transport data; media itself is never sent here.
      }
    });
    room.on(RoomEvent.AudioPlaybackStatusChanged, () => this.syncPlaybackState());
    room.on(RoomEvent.Disconnected, () => {
      if (!this.closed) this.callbacks.onDisconnected();
    });
    room.on(RoomEvent.Reconnecting, () => {
      if (!this.closed) this.callbacks.onReconnecting?.();
    });
    room.on(RoomEvent.Reconnected, () => {
      if (!this.closed) this.callbacks.onReconnected?.();
    });

    try {
      await room.connect(credentials.url, credentials.token, { autoSubscribe: true });
      if (this.closed) {
        await room.disconnect();
        return;
      }
      this.connected = true;
      this.syncPlaybackState();
      if (publishMicrophone) {
        await room.localParticipant.setMicrophoneEnabled(true);
      }
    } catch (error) {
      await this.close();
      throw error;
    }
  }

  async setMuted(muted: boolean): Promise<void> {
    this.muted = muted;
    if (this.room && this.connected && !this.closed) {
      await this.room.localParticipant.setMicrophoneEnabled(!muted);
    }
  }

  /** Start call audio the browser held back; call it from a click or tap handler. */
  async resumeAudio(): Promise<void> {
    const room = this.room;
    if (!room || this.closed) return;
    try {
      await room.startAudio();
    } catch {
      // Still blocked: the next gesture tries again.
    }
    this.syncPlaybackState();
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.connected = false;
    this.disarmGestureUnlock();
    if (this.levelTimer) {
      clearInterval(this.levelTimer);
      this.levelTimer = null;
    }
    for (const key of [...this.analysers.keys()]) this.unwatchLevel(key);
    const room = this.room;
    this.room = null;
    if (room) {
      try {
        await room.disconnect();
      } catch {
        // The room may already have disconnected due to network failure.
      }
    }
    for (const element of this.audioElements) {
      element.pause();
      element.srcObject = null;
      element.remove();
    }
    this.audioElements.clear();
  }

  private syncPlaybackState(): void {
    const room = this.room;
    if (!room || this.closed) return;
    const blocked = !room.canPlaybackAudio;
    if (blocked === this.audioBlocked) return;
    this.audioBlocked = blocked;
    if (blocked) this.armGestureUnlock();
    else this.disarmGestureUnlock();
    this.callbacks.onAudioBlockedChange?.(blocked);
  }

  private armGestureUnlock(): void {
    if (this.gestureUnlock || typeof document === 'undefined') return;
    const unlock = () => {
      void this.resumeAudio();
    };
    this.gestureUnlock = unlock;
    for (const type of GESTURES) document.addEventListener(type, unlock, { capture: true });
  }

  private disarmGestureUnlock(): void {
    const unlock = this.gestureUnlock;
    if (!unlock || typeof document === 'undefined') return;
    this.gestureUnlock = null;
    for (const type of GESTURES) document.removeEventListener(type, unlock, { capture: true });
  }

  private watchLevel(key: string, track: LocalAudioTrack | RemoteAudioTrack, remote: boolean): void {
    if (!this.callbacks.onLevels || this.closed || this.analysers.has(key)) return;
    let analyser: Analyser;
    try {
      analyser = createAudioAnalyser(track);
    } catch {
      return; // No Web Audio: the call works, the orb just stays still.
    }
    this.analysers.set(key, { remote, analyser });
    if (!this.levelTimer) {
      this.levelTimer = setInterval(() => this.postLevels(), LEVEL_INTERVAL_MS);
    }
  }

  private unwatchLevel(key: string): void {
    const entry = this.analysers.get(key);
    if (!entry) return;
    this.analysers.delete(key);
    void entry.analyser.cleanup().catch(() => {});
  }

  private postLevels(): void {
    let input = 0;
    let output = 0;
    for (const { remote, analyser } of this.analysers.values()) {
      const volume = analyser.calculateVolume();
      if (remote) output = Math.max(output, volume);
      else input = Math.max(input, volume);
    }
    this.callbacks.onLevels?.({
      input: this.muted ? 0 : Math.min(1, input * LEVEL_GAIN),
      output: Math.min(1, output * LEVEL_GAIN),
    });
  }

  private detachTrack(track: { detach: () => HTMLElement[] }): void {
    for (const element of track.detach()) {
      this.audioElements.delete(element as HTMLMediaElement);
      element.remove();
    }
  }
}
