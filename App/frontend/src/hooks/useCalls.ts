/**
 * TanStack Query hooks and WebSocket streaming for staff Calls page.
 */

import { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';
import {
  callsApi,
  CallRecord,
  CallTurn,
} from '@/services/callsApi';
import { appendAuthToken } from '@/lib/authSession';
import { AudioRecorder } from '@/services/voiceService';
import { PCMPlayer } from '@/services/pcmPlayer';

// ---------------------------------------------------------------------------
// 1. REST Query Hooks
// ---------------------------------------------------------------------------

export function useCalls(status = 'all', limit = 50) {
  return useQuery({
    queryKey: [...queryKeys.calls.list(status), limit],
    queryFn: () => callsApi.listCalls(status, limit),
    refetchInterval: 5000,
  });
}

export function useCall(callId?: string | null) {
  return useQuery({
    queryKey: callId ? queryKeys.calls.detail(callId) : ['calls', 'detail', 'none'],
    queryFn: () => (callId ? callsApi.getCall(callId) : Promise.reject('No ID')),
    enabled: Boolean(callId),
  });
}

export function useCallMetrics(days = 7) {
  return useQuery({
    queryKey: queryKeys.calls.metrics(days),
    queryFn: () => callsApi.getMetrics(days),
    staleTime: 60000,
  });
}

export function useReviewCall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ callId, rating, note }: { callId: string; rating: number; note?: string }) =>
      callsApi.submitReview(callId, rating, note),
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.calls.all() });
      queryClient.invalidateQueries({ queryKey: queryKeys.calls.detail(vars.callId) });
    },
  });
}

// ---------------------------------------------------------------------------
// 2. Staff Lobby Stream (Live Events)
// ---------------------------------------------------------------------------

export function useCallsLobby() {
  const queryClient = useQueryClient();
  const [liveBanner, setLiveBanner] = useState<{
    callId: string;
    topic: string;
    reason: string;
    ticketId?: string;
    language?: string;
  } | null>(null);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = appendAuthToken(`${protocol}//${host}/api/v1/admin/calls/stream`);

    let ws: WebSocket | null = null;

    try {
      ws = new WebSocket(url);
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (
            payload.type === 'call.started' ||
            payload.type === 'call.ended' ||
            payload.type === 'call.bridged' ||
            payload.type === 'call.language'
          ) {
            queryClient.invalidateQueries({ queryKey: queryKeys.calls.all() });
          }
          if (payload.type === 'call.transfer_requested') {
            queryClient.invalidateQueries({ queryKey: queryKeys.calls.all() });
            setLiveBanner({
              callId: payload.data?.call_id,
              topic: payload.data?.topic || 'General Support',
              reason: payload.data?.reason || 'Transfer requested',
              ticketId: payload.data?.ticket_id,
              language: typeof payload.data?.language === 'string' ? payload.data.language : undefined,
            });
          }
        } catch {}
      };

      ws.onerror = () => {
        // Fallback polling already covered by refetchInterval on useCalls
      };
    } catch {}

    return () => {
      if (ws) {
        try {
          ws.close();
        } catch {}
      }
    };
  }, [queryClient]);

  return { liveBanner, clearBanner: () => setLiveBanner(null) };
}

// ---------------------------------------------------------------------------
// 3. Per-Call Live Stream (Transcript & State Deltas)
// ---------------------------------------------------------------------------

export function useCallLive(callId?: string | null) {
  const [call, setCall] = useState<CallRecord | null>(null);
  const [turns, setTurns] = useState<CallTurn[]>([]);
  const [interimCaption, setInterimCaption] = useState<{ speaker: string; text: string } | null>(null);

  useEffect(() => {
    if (!callId || typeof window === 'undefined') {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = appendAuthToken(`${protocol}//${host}/api/v1/admin/calls/stream?call_id=${encodeURIComponent(callId)}`);

    let ws: WebSocket | null = null;

    try {
      ws = new WebSocket(url);
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'snapshot') {
            setCall(msg.call || null);
            setTurns(msg.turns || []);
          } else if (msg.type === 'turn') {
            setTurns((prev) => [...prev, msg.data]);
            setInterimCaption(null);
          } else if (msg.type === 'caption') {
            setInterimCaption({ speaker: msg.data.speaker, text: msg.data.text });
          } else if (msg.type === 'status') {
            setCall((prev) => (prev ? { ...prev, status: msg.data.status } : null));
          } else if (msg.type === 'language' && typeof msg.data?.language === 'string') {
            setCall((prev) => (prev ? { ...prev, locale: msg.data.language } : null));
          }
        } catch {}
      };
    } catch {}

    return () => {
      setCall(null);
      setTurns([]);
      setInterimCaption(null);
      if (ws) {
        try {
          ws.close();
        } catch {}
      }
    };
  }, [callId]);

  return {
    call: callId ? call : null,
    turns: callId ? turns : [],
    interimCaption: callId ? interimCaption : null,
  };
}

// ---------------------------------------------------------------------------
// 4. Officer Audio Bridge Hook
// ---------------------------------------------------------------------------

export function useOfficerAudio(callId?: string | null) {
  const [isBridged, setIsBridged] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const playerRef = useRef<PCMPlayer | null>(null);
  const micCleanupRef = useRef<(() => void) | null>(null);
  const isMutedRef = useRef(isMuted);

  useEffect(() => {
    isMutedRef.current = isMuted;
  }, [isMuted]);

  const leaveAudio = () => {
    if (micCleanupRef.current) {
      micCleanupRef.current();
      micCleanupRef.current = null;
    }
    if (playerRef.current) {
      playerRef.current.close();
      playerRef.current = null;
    }
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {}
      wsRef.current = null;
    }
    setIsBridged(false);
  };

  const takeCall = async () => {
    if (!callId || typeof window === 'undefined') return;

    leaveAudio();
    setError(null);

    const player = new PCMPlayer(16000);
    playerRef.current = player;
    await player.init().catch(() => {});

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = appendAuthToken(`${protocol}//${host}/api/v1/admin/calls/${encodeURIComponent(callId)}/audio`);

    try {
      const ws = new WebSocket(url);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      ws.onopen = async () => {
        setIsBridged(true);
        // Start officer mic capture
        try {
          const recorder = new AudioRecorder();
          const cleanup = await recorder.startStreaming(
            (pcmChunk) => {
              if (!isMutedRef.current && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                wsRef.current.send(pcmChunk);
              }
            },
            { echoCancellation: true, noiseSuppression: true },
          );
          micCleanupRef.current = cleanup;
        } catch (_err: unknown) {
          setError('Microphone access denied for officer');
          leaveAudio();
        }
      };

      ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          // Caller audio chunk received -> play to officer headset
          player.push(event.data);
        }
      };

      ws.onerror = () => {
        setError('Audio bridge connection failed');
        leaveAudio();
      };

      ws.onclose = () => {
        leaveAudio();
      };
    } catch (err: unknown) {
      setError((err as Error)?.message || 'Failed opening audio bridge');
      leaveAudio();
    }
  };

  useEffect(() => {
    return () => {
      leaveAudio();
    };
  }, []);

  return {
    isBridged,
    isMuted,
    error,
    takeCall,
    leaveAudio,
    toggleMute: () => setIsMuted((v) => !v),
  };
}
