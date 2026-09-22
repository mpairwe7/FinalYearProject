/**
 * WebSocket client for the taxpayer Call URA phone simulation.
 * Connects to /api/v1/calls/stream without auto-reconnect (dropped calls end like real phone calls).
 */

import { appendAuthToken } from '@/lib/authSession';

export interface CallStartPayload {
  locale: string;
  voice_consent_accepted: boolean;
  sample_rate?: number;
}

export interface CallSocketCallbacks {
  onAudio: (pcmChunk: ArrayBuffer) => void;
  onMessage: (msg: Record<string, unknown>) => void;
  onError: (error: string) => void;
  onClose: (code: number, reason: string) => void;
}

export class CallSocket {
  private ws: WebSocket | null = null;
  private callbacks: CallSocketCallbacks;
  private isClosed = false;

  constructor(callbacks: CallSocketCallbacks) {
    this.callbacks = callbacks;
  }

  connect(startPayload: CallStartPayload): void {
    if (typeof window === 'undefined') return;

    this.isClosed = false;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const rawUrl = `${protocol}//${host}/api/v1/calls/stream`;
    const authenticatedUrl = appendAuthToken(rawUrl);

    try {
      this.ws = new WebSocket(authenticatedUrl);
      this.ws.binaryType = 'arraybuffer';

      this.ws.onopen = () => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          const initMsg = {
            type: 'call_start',
            locale: startPayload.locale,
            voice_consent_accepted: startPayload.voice_consent_accepted,
            sample_rate: startPayload.sample_rate || 16000,
          };
          this.ws.send(JSON.stringify(initMsg));
        }
      };

      this.ws.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer) {
          this.callbacks.onAudio(event.data);
        } else if (typeof event.data === 'string') {
          try {
            const data = JSON.parse(event.data);
            this.callbacks.onMessage(data);
          } catch {
            // Non-JSON text message
          }
        }
      };

      this.ws.onerror = () => {
        if (!this.isClosed) {
          this.callbacks.onError('Connection error encountered during call.');
        }
      };

      this.ws.onclose = (event: CloseEvent) => {
        if (!this.isClosed) {
          this.callbacks.onClose(event.code, event.reason);
        }
      };
    } catch (err: unknown) {
      this.callbacks.onError((err as Error)?.message || 'Failed to open call socket');
    }
  }

  sendAudio(pcmChunk: ArrayBuffer): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(pcmChunk);
    }
  }

  requestOfficer(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'request_officer' }));
    }
  }

  hangup(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'hangup' }));
    }
    this.close();
  }

  close(): void {
    this.isClosed = true;
    if (this.ws) {
      try {
        this.ws.close();
      } catch {}
      this.ws = null;
    }
  }
}
