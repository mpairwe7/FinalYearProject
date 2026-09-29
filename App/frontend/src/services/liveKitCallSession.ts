import { Room, RoomEvent, Track } from 'livekit-client';

export interface LiveKitCallCredentials {
  transport: 'livekit';
  url: string;
  room: string;
  identity: string;
  token: string;
}

export interface LiveKitCallSessionCallbacks {
  onMessage: (message: Record<string, unknown>) => void;
  onDisconnected: () => void;
  onReconnecting?: () => void;
  onReconnected?: () => void;
}

/** WebRTC media session for one taxpayer or officer call leg. */
export class LiveKitCallSession {
  private room: Room | null = null;
  private audioElements = new Set<HTMLMediaElement>();
  private callbacks: LiveKitCallSessionCallbacks;
  private closed = false;
  private connected = false;

  constructor(callbacks: LiveKitCallSessionCallbacks) {
    this.callbacks = callbacks;
  }

  async connect(credentials: LiveKitCallCredentials, publishMicrophone = true): Promise<void> {
    if (typeof window === 'undefined') throw new Error('LiveKit audio requires a browser');
    if (!credentials.url.startsWith('wss://') && !credentials.url.startsWith('ws://')) {
      throw new Error('The call media server URL is invalid');
    }

    const room = new Room({ adaptiveStream: true, dynacast: true });
    this.room = room;
    room.on(RoomEvent.TrackSubscribed, (track) => {
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
        // The call screen is opened by a user gesture. Browsers can still
        // defer playback on restrictive policies; the SDK retries on activity.
      });
    });
    room.on(RoomEvent.TrackUnsubscribed, (track) => this.detachTrack(track));
    room.on(RoomEvent.DataReceived, (payload) => {
      try {
        const message = JSON.parse(new TextDecoder().decode(payload));
        if (message && typeof message === 'object' && !Array.isArray(message)) {
          this.callbacks.onMessage(message as Record<string, unknown>);
        }
      } catch {
        // Ignore non-JSON transport data; media itself is never sent here.
      }
    });
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
      if (publishMicrophone) {
        await room.localParticipant.setMicrophoneEnabled(true);
      }
    } catch (error) {
      await this.close();
      throw error;
    }
  }

  async setMuted(muted: boolean): Promise<void> {
    if (this.room && this.connected && !this.closed) {
      await this.room.localParticipant.setMicrophoneEnabled(!muted);
    }
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.connected = false;
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

  private detachTrack(track: { detach: () => HTMLElement[] }): void {
    for (const element of track.detach()) {
      this.audioElements.delete(element as HTMLMediaElement);
      element.remove();
    }
  }
}
