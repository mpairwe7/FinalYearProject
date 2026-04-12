"use client";

import { useQuery } from "@tanstack/react-query";
import { analyticsApi } from "../services/analyticsApi";

export function useDashboard(days = 30) {
  return useQuery({
    queryKey: ["dashboard", days],
    queryFn: () => analyticsApi.dashboard(days),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useFeedbackSummary(days = 30) {
  return useQuery({
    queryKey: ["feedbackSummary", days],
    queryFn: () => analyticsApi.feedbackSummary(days),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    retry: 1,
  });
}

export function useTicketStats(days = 30) {
  return useQuery({
    queryKey: ["ticketStats", days],
    queryFn: () => analyticsApi.ticketStats(days),
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    retry: 1,
  });
}
