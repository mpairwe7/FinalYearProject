"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { analyticsApi } from "../services/analyticsApi";
import type { TicketPatch } from "../services/analyticsApi";
import { queryKeys } from "../lib/queryKeys";

export function useDashboard(days = 30) {
  return useQuery({
    queryKey: queryKeys.analytics.dashboard(days),
    queryFn: () => analyticsApi.dashboard(days),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useFeedbackSummary(days = 30) {
  return useQuery({
    queryKey: queryKeys.analytics.feedbackSummary(days),
    queryFn: () => analyticsApi.feedbackSummary(days),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    retry: 1,
  });
}

export function useTicketStats(days = 30) {
  return useQuery({
    queryKey: queryKeys.tickets.stats(days),
    queryFn: () => analyticsApi.ticketStats(days),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    retry: 1,
  });
}

export function useTicketQueue(status = "open", limit = 8) {
  return useQuery({
    queryKey: queryKeys.tickets.queue(status, limit),
    queryFn: () => analyticsApi.tickets(status, limit),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    retry: 1,
  });
}

/**
 * The staff queue. Polls harder than the analytics widgets: an
 * escalation that has been waiting is the thing an officer most needs
 * to see. The backend orders urgent-first, then longest-waiting.
 */
export function useTicketQueueFull(
  status = "open",
  priority = "",
  team = "",
  limit = 50,
) {
  return useQuery({
    queryKey: queryKeys.tickets.queueFull(status, priority, team, limit),
    queryFn: () => analyticsApi.tickets(status, limit, priority, team),
    staleTime: 3_000,
    gcTime: 5 * 60_000,
    refetchInterval: 5_000,
    retry: 1,
  });
}

/** One ticket, including the transcript snapshot taken at escalation. */
export function useTicket(id: string | null) {
  return useQuery({
    queryKey: queryKeys.tickets.detail(id || ""),
    queryFn: () => analyticsApi.ticket(id as string),
    enabled: Boolean(id),
    staleTime: 2_000,
    refetchInterval: 5_000,
    retry: 1,
  });
}

export function useTicketSla(days = 30) {
  return useQuery({
    queryKey: queryKeys.tickets.sla(days),
    queryFn: () => analyticsApi.ticketSla(days),
    staleTime: 30_000,
    retry: 1,
  });
}

/**
 * Update a ticket, then refresh the queue, the open detail and the SLA.
 *
 * `officer_reply` reaches the taxpayer on their next turn; `staff_note`
 * stays internal. The form keeps them apart for that reason.
 */
export function useUpdateTicket() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: TicketPatch }) =>
      analyticsApi.updateTicket(id, patch),
    onSuccess: (_data, variables) => {
      client.invalidateQueries({ queryKey: queryKeys.tickets.all() });
      if (variables?.id) {
        client.invalidateQueries({ queryKey: queryKeys.tickets.detail(variables.id) });
      }
    },
  });
}
