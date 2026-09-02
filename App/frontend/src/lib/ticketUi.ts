/**
 * Shared presentation helpers for the staff ticket workbench.
 *
 * First-response SLA follows a common public-sector contact-centre
 * pattern: warn at 4 hours, breach at 24 hours, unless someone has
 * already replied. Resolution time is not used for the row tone —
 * an assigned case that is being worked should not flash red.
 */
import { useEffect, useState } from "react";
import type { TicketQueueItem } from "../services/analyticsApi";

export const STATUSES = ["open", "assigned", "resolved", "wontfix"] as const;
export const PRIORITIES = ["urgent", "high", "normal", "low"] as const;

/**
 * The queue toolbar's choices — the four real statuses plus "any".
 *
 * Separate from STATUSES because that list is also the composer's status
 * stepper, and "any" is not a state a ticket can be moved into. The token is
 * mapped to an empty `status=` at the API boundary (see analyticsApi.tickets);
 * the backend rejects anything outside the four, and treats absent as all.
 *
 * It exists so the overview's live tiles have somewhere honest to link: they
 * count open *and* in-progress cases, which no single-status view can show.
 */
export const ANY_STATUS = "any";
export const QUEUE_STATUSES = [ANY_STATUS, ...STATUSES] as const;

export const PRIORITY_RANK: Record<string, number> = {
  urgent: 0,
  high: 1,
  normal: 2,
  low: 3,
};

export const STATUS_LABEL: Record<string, string> = {
  [ANY_STATUS]: "Any",
  open: "New",
  assigned: "In progress",
  resolved: "Resolved",
  wontfix: "Won't fix",
};

export const SLA_WARN_SECONDS = 4 * 3600;
export const SLA_BREACH_SECONDS = 24 * 3600;

export type WaitTone = "ok" | "warn" | "breach";

export function waitingSeconds(createdAt: number, now = Date.now()): number {
  return Math.max(0, now / 1000 - createdAt);
}

export function waitingFor(createdAt: number, now = Date.now()): string {
  const seconds = waitingSeconds(createdAt, now);
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return m ? `${h}h ${m}m` : `${h}h`;
  }
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  return h ? `${d}d ${h}h` : `${d}d`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const h = seconds / 3600;
  return h >= 10 ? `${Math.round(h)}h` : `${h.toFixed(1)}h`;
}

export function waitTone(
  createdAt: number,
  firstResponseAt?: number | null,
  replyAt?: number | null,
  now = Date.now(),
): WaitTone {
  // After the first touch the clock is next-reply: how long since we last
  // wrote to the taxpayer on a case that is still open.
  const lastTouch = firstResponseAt ? replyAt || firstResponseAt : null;
  const age = lastTouch
    ? Math.max(0, now / 1000 - lastTouch)
    : waitingSeconds(createdAt, now);
  if (age >= SLA_BREACH_SECONDS) return "breach";
  if (age >= SLA_WARN_SECONDS) return "warn";
  return "ok";
}

export function ticketMatchesQuery(ticket: TicketQueueItem, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [
    ticket.id,
    ticket.reason,
    ticket.user_query,
    ticket.assignee,
    ticket.team,
    ticket.handoff?.topic,
    ticket.handoff?.summary,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(q);
}

export function sortQueue(tickets: TicketQueueItem[]): TicketQueueItem[] {
  return [...tickets].sort(
    (a, b) =>
      (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9) ||
      a.created_at - b.created_at,
  );
}

export function officerHandle(who: { email?: string; external_id?: string } | null | undefined): string {
  return (who?.email || who?.external_id || "").trim();
}

export function isMine(ticket: TicketQueueItem, handle: string): boolean {
  if (!handle) return false;
  return (ticket.assignee || "").trim().toLowerCase() === handle.toLowerCase();
}

export function topicLabel(ticket: Pick<TicketQueueItem, "reason" | "handoff">): string {
  const topic = ticket.handoff?.topic?.replace(/_/g, " ");
  return topic || ticket.reason || "Escalation";
}

export type QueueView = {
  status: string;
  priority: string;
  team: string;
  ticket: string;
  q: string;
  mine: boolean;
};

const DEFAULT_VIEW: QueueView = {
  status: "open",
  priority: "",
  team: "",
  ticket: "",
  q: "",
  mine: false,
};

function readView(): QueueView {
  if (typeof window === "undefined") return { ...DEFAULT_VIEW };
  const p = new URLSearchParams(window.location.search);
  return {
    status: p.get("status") || DEFAULT_VIEW.status,
    priority: p.get("priority") || "",
    team: p.get("team") || "",
    ticket: p.get("ticket") || "",
    q: p.get("q") || "",
    mine: p.get("mine") === "1",
  };
}

function writeView(view: QueueView): void {
  if (typeof window === "undefined") return;
  const p = new URLSearchParams();
  if (view.status && view.status !== "open") p.set("status", view.status);
  if (view.priority) p.set("priority", view.priority);
  if (view.team) p.set("team", view.team);
  if (view.ticket) p.set("ticket", view.ticket);
  if (view.q) p.set("q", view.q);
  if (view.mine) p.set("mine", "1");
  const qs = p.toString();
  const next = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  try {
    window.history.replaceState(null, "", next);
  } catch {
    /* jsdom without a fully writable Location */
  }
}

/** URL-backed queue filters so a view can be refreshed, shared, or deep-linked. */
export function useQueueView(initial?: Partial<QueueView>): [QueueView, (patch: Partial<QueueView>) => void] {
  // URL wins so a shared or refreshed view is not overwritten by page defaults.
  // Re-read on mount: the SSR initializer has no window, so a deep link
  // such as ?ticket= would otherwise hydrate as an empty selection.
  const [view, setView] = useState<QueueView>(() => ({
    ...DEFAULT_VIEW,
    ...initial,
    ...readView(),
  }));

  useEffect(() => {
    const fromUrl = { ...DEFAULT_VIEW, ...initial, ...readView() };
    setView((prev) => (queueViewEqual(prev, fromUrl) ? prev : fromUrl));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount sync only
  }, []);

  const update = (patch: Partial<QueueView>) => {
    setView((prev) => {
      const next = { ...prev, ...patch };
      writeView(next);
      return next;
    });
  };

  return [view, update];
}

function queueViewEqual(a: QueueView, b: QueueView): boolean {
  return (
    a.status === b.status &&
    a.priority === b.priority &&
    a.team === b.team &&
    a.ticket === b.ticket &&
    a.q === b.q &&
    a.mine === b.mine
  );
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

/**
 * Linear / Intercom-style queue keys. Ignored while typing so a "j" in
 * a reply does not jump tickets.
 */
export function useQueueHotkeys(opts: {
  ids: string[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAssignMe?: () => void;
  replySelector?: string;
  searchSelector?: string;
  enabled?: boolean;
}): void {
  const { ids, selectedId, onSelect, onAssignMe, replySelector, searchSelector, enabled = true } =
    opts;

  useEffect(() => {
    if (!enabled) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) {
        if (event.key === "Escape") (event.target as HTMLElement).blur();
        return;
      }

      if (event.key === "/" && searchSelector) {
        event.preventDefault();
        document.querySelector<HTMLInputElement>(searchSelector)?.focus();
        return;
      }
      if (event.key === "r" && replySelector) {
        event.preventDefault();
        document.querySelector<HTMLTextAreaElement>(replySelector)?.focus();
        return;
      }
      if (event.key === "a" && onAssignMe) {
        event.preventDefault();
        onAssignMe();
        return;
      }
      if (event.key !== "j" && event.key !== "k" && event.key !== "ArrowDown" && event.key !== "ArrowUp") {
        return;
      }
      if (!ids.length) return;
      event.preventDefault();
      const idx = selectedId ? ids.indexOf(selectedId) : -1;
      const delta = event.key === "j" || event.key === "ArrowDown" ? 1 : -1;
      const next = ids[Math.max(0, Math.min(ids.length - 1, (idx < 0 ? 0 : idx) + delta))];
      if (next) onSelect(next);
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, ids, selectedId, onSelect, onAssignMe, replySelector, searchSelector]);
}
