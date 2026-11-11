/**
 * TanStack Query hooks and WebSocket streaming for staff Calls page.
 */

import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';
import {
  callsApi,
  CallBrief,
  CallRecord,
  CallTurn,
  ListCallsParams,
} from '@/services/callsApi';
import { getAuthToken } from '@/lib/authSession';

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

export function useCallHistory(filters: ListCallsParams) {
  return useQuery({
    queryKey: ['calls', 'history', filters],
    queryFn: () => callsApi.listCalls(filters),
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

/** Who is on duty: every officer's presence, re-read every 10 s. Read-only. */
export function useOfficerPresence() {
  return useQuery({
    queryKey: queryKeys.calls.presence(),
    queryFn: () => callsApi.getPresence(),
    staleTime: 5000,
    refetchInterval: 10000,
    retry: 1,
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
// 2. Per-Call Live Stream (transcript, status, brief)
// ---------------------------------------------------------------------------
//
// The lobby (every live call's metadata) and the officer's own audio live
// outside React now — services/callLobbySocket.ts and
// services/officerCallSession.ts — so they survive page navigation.

/** Closed for good: calls off, session over, not staff, no such call. */
const LIVE_FINAL_CLOSE_CODES = new Set([1001, 4401, 4403, 4404]);
const LIVE_MAX_BACKOFF_MS = 15000;

/** The per-call stream: `connecting`, `live`, `reconnecting` after a drop, `stopped` for good. */
export type CallLiveStatus = 'connecting' | 'live' | 'reconnecting' | 'stopped';

export function useCallLive(callId?: string | null) {
  const [status, setStatus] = useState<CallLiveStatus>('connecting');
  const [secondsRemaining, setSecondsRemaining] = useState<number | null>(null);
  const [call, setCall] = useState<CallRecord | null>(null);
  const [turns, setTurns] = useState<CallTurn[]>([]);
  const [interimCaption, setInterimCaption] = useState<{ speaker: string; text: string } | null>(null);
  const [brief, setBrief] = useState<CallBrief | null>(null);

  useEffect(() => {
    if (!callId || typeof window === 'undefined') {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = `${protocol}//${host}/api/v1/admin/calls/stream?call_id=${encodeURIComponent(callId)}`;

    // The transcript used to stop without a sign when its socket dropped, and
    // never came back (G606). It reconnects with a backoff now, and each
    // reconnect's snapshot replaces what was shown.
    let ws: WebSocket | null = null;
    let disposed = false;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoffMs = 1000;
    let callEnded = false;
    setStatus('connecting');
    setSecondsRemaining(null);

    const open = () => {
      if (disposed) return;
      let socket: WebSocket;
      try {
        socket = new WebSocket(url);
      } catch {
        scheduleRetry();
        return;
      }
      ws = socket;
      socket.onopen = () => {
        socket.send(JSON.stringify({ type: 'authenticate', access_token: getAuthToken() }));
      };
      socket.onclose = (event: CloseEvent) => {
        if (disposed || ws !== socket) return;
        ws = null;
        if (callEnded || LIVE_FINAL_CLOSE_CODES.has(event.code)) {
          setStatus('stopped');
          return;
        }
        setStatus('reconnecting');
        scheduleRetry();
      };
      socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'ready' || msg.type === 'snapshot') {
            backoffMs = 1000;
            setStatus('live');
          }
          if (msg.type === 'snapshot') {
            callEnded = msg.call?.status === 'ended';
            setCall(msg.call || null);
            setTurns(msg.turns || []);
            setBrief(msg.call?.brief || null);
          } else if (msg.type === 'duration_warning') {
            const seconds = Number(msg.data?.seconds_remaining);
            setSecondsRemaining(Number.isFinite(seconds) && seconds > 0 ? seconds : 60);
          } else if (msg.type === 'brief') {
            setBrief(msg.data || null);
          } else if (msg.type === 'turn') {
            setTurns((prev) => [...prev, msg.data]);
            setInterimCaption(null);
          } else if (msg.type === 'caption') {
            setInterimCaption({ speaker: msg.data.speaker, text: msg.data.text });
          } else if (msg.type === 'status') {
            if (msg.data?.status === 'ended') callEnded = true;
            setCall((prev) => (prev ? { ...prev, status: msg.data.status } : null));
          } else if (msg.type === 'language' && typeof msg.data?.language === 'string') {
            setCall((prev) => (prev ? { ...prev, locale: msg.data.language } : null));
          } else if (msg.type === 'priority' && typeof msg.data?.priority === 'string') {
            setCall((prev) =>
              prev ? { ...prev, priority: msg.data.priority, transfer_reason: msg.data.reason || prev.transfer_reason } : null,
            );
          }
        } catch {}
      };
    };

    function scheduleRetry() {
      if (disposed || retry) return;
      retry = setTimeout(() => {
        retry = null;
        open();
      }, backoffMs);
      backoffMs = Math.min(backoffMs * 2, LIVE_MAX_BACKOFF_MS);
    }

    open();

    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      setCall(null);
      setTurns([]);
      setInterimCaption(null);
      setBrief(null);
      const socket = ws;
      ws = null;
      if (socket) {
        try {
          socket.close();
        } catch {}
      }
    };
  }, [callId]);

  return {
    status: callId ? status : ('stopped' as CallLiveStatus),
    secondsRemaining: callId ? secondsRemaining : null,
    call: callId ? call : null,
    turns: callId ? turns : [],
    interimCaption: callId ? interimCaption : null,
    brief: callId ? brief : null,
  };
}

// ---------------------------------------------------------------------------
// 3. The officer's brief
// ---------------------------------------------------------------------------

/**
 * A call's brief: fetched once (the rolling one is usually there already),
 * then kept current by the per-call socket's `brief` events (`live`).
 * `refresh()` asks the server to rebuild it now.
 */
export function useCallBrief(callId: string | null, live: CallBrief | null) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: callId ? queryKeys.calls.brief(callId) : ['calls', 'brief', 'none'],
    queryFn: () => (callId ? callsApi.getBrief(callId) : Promise.resolve(null)),
    enabled: Boolean(callId),
    // 202 "building": look again shortly; the live event usually wins the race.
    refetchInterval: (q) => (q.state.data === null ? 3000 : false),
  });
  const refresh = useMutation({
    mutationFn: () => (callId ? callsApi.getBrief(callId, true) : Promise.resolve(null)),
    onSuccess: (brief) => {
      if (callId && brief) queryClient.setQueryData(queryKeys.calls.brief(callId), brief);
    },
  });
  const fetched = query.data ?? null;
  const brief = !live ? fetched : !fetched || live.generated_at >= fetched.generated_at ? live : fetched;
  return { brief, loading: query.isLoading, refresh: () => refresh.mutate(), refreshing: refresh.isPending };
}
