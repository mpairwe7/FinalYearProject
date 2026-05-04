/**
 * WebSocket client for streaming voice chat.
 *
 * Manages the duplex connection to `/v1/voice/chat/stream`, handling
 * binary PCM audio frames (client -> server) and JSON + binary events
 * (server -> client).
 *
 * Auto-reconnects with exponential backoff on disconnection.
 */

import { appendAuthToken } from '@/lib/authSession';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface VoiceWSConfig {
  language: string;
  voice?: string;
  conversation_id?: string;
  sample_rate?: number;
  vad_sensitivity?: 'low' | 'medium' | 'high';
  tts_enabled?: boolean;
  top_k?: number;
  user_id?: string;
  voice_consent_accepted?: boolean;
}

export type VoiceWSEventType =
  | 'session_ready'
  | 'vad_state'
  | 'transcript_partial'
  | 'transcript_final'
  | 'audio_start'
  | 'audio_chunk'
  | 'audio_end'
  | 'reply_text'
  | 'reply_meta'
  | 'latency_report'
  | 'error';

export interface VoiceWSEvent {
  type: VoiceWSEventType;
  [key: string]: unknown;
}

export type VoiceWSListener = (event: VoiceWSEvent) => void;
export type AudioChunkListener = (chunk: ArrayBuffer) => void;

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const WS_URL =
  typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_WS_URL ||
        `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/v1/voice/chat/stream`)
    : '';

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30_000;
const RECONNECT_MAX_ATTEMPTS = 8;
const RECONNECT_JITTER_RATIO = 0.25;
const HEARTBEAT_INTERVAL_MS = 15_000;

// ---------------------------------------------------------------------------
// VoiceWebSocket
// ---------------------------------------------------------------------------

export class VoiceWebSocket {
  private ws: WebSocket | null = null;
  private config: VoiceWSConfig | null = null;
  private listeners: Set<VoiceWSListener> = new Set();
  private audioListeners: Set<AudioChunkListener> = new Set();
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private onlineReconnectHandler: (() => void) | null = null;
  private intentionalClose = false;
  private _connected = false;

  get connected(): boolean {
    return this._connected;
  }

  // ── Connection lifecycle ───────────────────────────────────────

  connect(config: VoiceWSConfig): void {
    if (this.ws || this.reconnectTimer) {
      this.intentionalClose = true;
      this._cleanup();
    }
    this.config = config;
    this.intentionalClose = false;
    this.reconnectAttempt = 0;
    this._openSocket();
  }

  disconnect(): void {
    this.intentionalClose = true;
    this._cleanup();
  }

  // ── Sending ────────────────────────────────────────────────────

  sendAudioChunk(pcm16: ArrayBuffer): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(pcm16);
    }
  }

  bargeIn(): void {
    this._sendJson({ type: 'barge_in' });
  }

  endSession(): void {
    this._sendJson({ type: 'session_end' });
    this.disconnect();
  }

  // ── Event listeners ────────────────────────────────────────────

  onEvent(listener: VoiceWSListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onAudioChunk(listener: AudioChunkListener): () => void {
    this.audioListeners.add(listener);
    return () => this.audioListeners.delete(listener);
  }

  // ── Private ────────────────────────────────────────────────────

  private _openSocket(): void {
    if (typeof WebSocket === 'undefined' || !WS_URL) {
      this._emit({
        type: 'error',
        message: 'WebSocket is not available in this environment.',
        recoverable: false,
      });
      return;
    }

    if (this._isOffline()) {
      this._scheduleReconnect();
      return;
    }

    try {
      this.ws = new WebSocket(appendAuthToken(WS_URL));
      this.ws.binaryType = 'arraybuffer';
    } catch {
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this._connected = true;
      this.reconnectAttempt = 0;
      this._clearOnlineReconnect();
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }

      // Send session_start config frame
      if (this.config) {
        this._sendJson({
          type: 'session_start',
          language: this.config.language,
          voice: this.config.voice || null,
          conversation_id: this.config.conversation_id || null,
          sample_rate: this.config.sample_rate || 16000,
          vad_sensitivity: this.config.vad_sensitivity || 'medium',
          tts_enabled: this.config.tts_enabled ?? true,
          top_k: this.config.top_k || 4,
          user_id: this.config.user_id || '',
          voice_consent_accepted: this.config.voice_consent_accepted ?? true,
        });
      }

      this._startHeartbeat();
    };

    this.ws.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary frame = TTS audio chunk
        for (const listener of this.audioListeners) {
          try {
            listener(event.data);
          } catch {
            // Listener error — ignore
          }
        }
      } else if (typeof event.data === 'string') {
        // JSON text frame = control event
        try {
          const parsed: VoiceWSEvent = JSON.parse(event.data);
          for (const listener of this.listeners) {
            try {
              listener(parsed);
            } catch {
              // Listener error — ignore
            }
          }
        } catch {
          // Malformed JSON — ignore
        }
      }
    };

    this.ws.onclose = () => {
      this._connected = false;
      this._stopHeartbeat();

      if (!this.intentionalClose) {
        this._scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      this._emit({
        type: 'error',
        message: 'Voice WebSocket connection error.',
        recoverable: true,
      });
      // onclose will fire after onerror in most browsers and schedule reconnect.
    };
  }

  private _sendJson(data: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  private _cleanup(): void {
    this._stopHeartbeat();
    this._clearOnlineReconnect();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      const socket = this.ws;
      this.ws = null;
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      try {
        socket.close(1000);
      } catch {
        // Already closed
      }
    }
    this._connected = false;
  }

  private _scheduleReconnect(): void {
    if (this.intentionalClose) return;

    this._connected = false;
    this._stopHeartbeat();

    if (this.reconnectTimer) return;

    if (this._isOffline()) {
      this._emit({
        type: 'error',
        message: 'Network is offline. Voice chat will reconnect when connectivity returns.',
        recoverable: true,
      });
      this._waitForOnline();
      return;
    }

    if (this.reconnectAttempt >= RECONNECT_MAX_ATTEMPTS) {
      this._emit({
        type: 'error',
        message: 'Voice chat connection could not be restored.',
        recoverable: false,
      });
      return;
    }

    const delay = Math.min(
      RECONNECT_BASE_MS * Math.pow(2, this.reconnectAttempt),
      RECONNECT_MAX_MS,
    );
    const jitter = Math.round(delay * RECONNECT_JITTER_RATIO * Math.random());
    this.reconnectAttempt++;

    this._emit({
      type: 'error',
      message: 'Voice chat connection interrupted. Reconnecting...',
      recoverable: true,
      retry_in_ms: delay + jitter,
      reconnect_attempt: this.reconnectAttempt,
    });

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._openSocket();
    }, delay + jitter);
  }

  private _startHeartbeat(): void {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        // WebSocket ping is handled at protocol level;
        // send a lightweight JSON ping as application-level keepalive
        this._sendJson({ type: 'ping' });
      }
    }, HEARTBEAT_INTERVAL_MS);
  }

  private _stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private _emit(event: VoiceWSEvent): void {
    for (const listener of this.listeners) {
      try {
        listener(event);
      } catch {
        // Listener error — ignore
      }
    }
  }

  private _isOffline(): boolean {
    return typeof navigator !== 'undefined' && navigator.onLine === false;
  }

  private _waitForOnline(): void {
    if (typeof window === 'undefined' || this.onlineReconnectHandler) return;

    this.onlineReconnectHandler = () => {
      this._clearOnlineReconnect();
      if (!this.intentionalClose) {
        this.reconnectAttempt = 0;
        this._scheduleReconnect();
      }
    };
    window.addEventListener('online', this.onlineReconnectHandler, { once: true });
  }

  private _clearOnlineReconnect(): void {
    if (typeof window !== 'undefined' && this.onlineReconnectHandler) {
      window.removeEventListener('online', this.onlineReconnectHandler);
    }
    this.onlineReconnectHandler = null;
  }
}
