/**
 * TanStack Query hooks for the service-quality audit (/admin/quality).
 *
 * Read-only and not live: an audit reads a period, so these re-fetch on a
 * refresh or a filter change rather than on a poll.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../lib/queryKeys";
import {
  analyticsApi,
  type AiChatQuery,
  type NeedsReviewItem,
  type NeedsReviewQuery,
  type RatingsQuery,
  type ReviewStatus,
} from "../services/analyticsApi";
import { callsApi } from "../services/callsApi";

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

/** One satisfaction stream (officer chats now): figures, per officer, and a page of ratings. */
export function useRatingsAudit({
  source = "chat_officer_csat",
  days = 30,
  score = null,
  officer = "",
  status = "",
  sentiment = "",
  limit = 25,
}: RatingsQuery = {}) {
  return useQuery({
    queryKey: queryKeys.quality.ratings(source, days, score ?? null, officer, status, limit, sentiment),
    queryFn: () => analyticsApi.qualityRatings({ source, days, score, officer, status, sentiment, limit }),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
    retry: 1,
  });
}

/**
 * One case as the audit opens it. Not live and not re-polled: every read of a
 * case is recorded in the audit trail (`staff.ticket_viewed`), so the audit
 * reads it once rather than every few seconds as the officer's workbench does.
 */
export function useAuditedTicket(ticketId: string | null) {
  return useQuery({
    queryKey: queryKeys.quality.ticket(ticketId || ""),
    queryFn: () => analyticsApi.ticket(ticketId as string),
    enabled: Boolean(ticketId),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}

/** One call as the audit opens it: read once, like a case (each read is recorded). */
export function useAuditedCall(callId: string | null) {
  return useQuery({
    queryKey: queryKeys.quality.call(callId || ""),
    queryFn: () => callsApi.getCall(callId as string),
    enabled: Boolean(callId),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
}

/** The Needs review queue: counts, and a page of items newest first. */
export function useNeedsReview({ days = 30, status = "open", stream = "", limit = 25 }: NeedsReviewQuery = {}) {
  return useQuery({
    queryKey: queryKeys.quality.needsReview(days, status, stream, limit),
    queryFn: () => analyticsApi.qualityNeedsReview({ days, status, stream, limit }),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
    retry: 1,
  });
}

/** Record a decision on an item; the queue, the counts and the overview re-read. */
export function useSetReview() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      item,
      status,
      note,
    }: {
      item: Pick<NeedsReviewItem, "item_type" | "item_id">;
      status: ReviewStatus | "open";
      note?: string;
    }) => analyticsApi.setQualityReview(item.item_type, item.item_id, status, note ?? ""),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.quality.all() });
    },
  });
}
