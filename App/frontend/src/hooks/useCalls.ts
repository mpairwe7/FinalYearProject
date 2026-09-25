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
import { appendAuthToken } from '@/lib/authSession';

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

export function useCallLive(callId?: string | null) {
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
            setBrief(msg.call?.brief || null);
          } else if (msg.type === 'brief') {
            setBrief(msg.data || null);
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
      setBrief(null);
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
