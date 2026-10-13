/**
 * TanStack Query hooks for the service-quality audit (/admin/quality).
 *
 * Read-only and not live: an audit reads a period, so these re-fetch on a
 * refresh or a filter change rather than on a poll.
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { queryKeys } from "../lib/queryKeys";
import { analyticsApi, type AiChatQuery } from "../services/analyticsApi";

export function useQualitySummary(days = 30) {
  return useQuery({
    queryKey: queryKeys.quality.summary(days),
    queryFn: () => analyticsApi.qualitySummary(days),
    staleTime: 60_000,
    retry: 1,
  });
}

/** Breakdowns plus one page of rated conversations, for the AI-answers tab. */
export function useAiChatAudit({
  days = 30,
  pattern = "",
  topic = "",
  locale = "",
  withComment = false,
  limit = 25,
}: AiChatQuery = {}) {
  return useQuery({
    queryKey: queryKeys.quality.aiChat(days, pattern, topic, locale, withComment, limit),
    queryFn: () => analyticsApi.qualityAiChat({ days, pattern, topic, locale, withComment, limit }),
    staleTime: 60_000,
    // A filter change keeps the old list on screen until the new one lands.
    placeholderData: keepPreviousData,
    retry: 1,
  });
}

/** One conversation, answer by answer. */
export function useAiChatTimeline(conversationId: string | null) {
  return useQuery({
    queryKey: queryKeys.quality.aiChatTimeline(conversationId || ""),
    queryFn: () => analyticsApi.qualityAiChatTimeline(conversationId as string),
    enabled: Boolean(conversationId),
    staleTime: 60_000,
    retry: 1,
  });
}
