/**
 * Presentation helpers for the audit trail (/admin/audit).
 *
 * The ledger speaks in event types ("staff.ticket_updated", "generate"); an
 * auditor reads sentences. These keep that translation, the one-line payload
 * summary and the CSV evidence export in one tested place, apart from the page.
 */
import type { AuditEvent } from "../services/analyticsApi";

const EVENT_LABEL: Record<string, string> = {
  "staff.ticket_updated": "Changed a ticket",
  "staff.ticket_viewed": "Opened a ticket and its transcript",
  "staff.flag_set": "Changed a feature switch",
  "staff.flag_cleared": "Reset a feature switch to its default",
  "staff.override_saved": "Saved a staff-written answer",
  "staff.override_deleted": "Deleted a staff-written answer",
  generate: "Assistant answered a question",
  tool_confirm: "Taxpayer confirmed or refused an action",
  erasure_tombstone: "Personal data erased on request",
};

/** The filters the page offers, as ledger event-type prefixes. */
export const AUDIT_FILTERS: readonly { value: string; label: string }[] = [
  { value: "", label: "Everything" },
  { value: "staff.", label: "All staff actions" },
  { value: "staff.ticket_viewed", label: "Transcripts opened" },
  { value: "staff.ticket_updated", label: "Ticket changes" },
  { value: "staff.flag", label: "Feature switch changes" },
  { value: "staff.override", label: "Staff-written answer changes" },
  { value: "generate", label: "Assistant answers" },
  { value: "voice_", label: "Voice consent and recordings" },
  { value: "erasure", label: "Erasures" },
];

export function eventLabel(eventType: string): string {
  if (EVENT_LABEL[eventType]) return EVENT_LABEL[eventType];
  if (eventType.startsWith("voice_")) return `Voice: ${eventType.slice(6).replaceAll("_", " ")}`;
  return eventType.replaceAll("_", " ").replaceAll(".", " · ");
}

/** Payload keys that are chain plumbing, not something an auditor reads. */
const HIDDEN_KEYS = new Set(["actor_role"]);

function display(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** "status: assigned · officer_reply_chars: 24", at most *max* characters. */
export function summarizePayload(payload: Record<string, unknown>, max = 140): string {
  const parts = Object.entries(payload ?? {})
    .filter(([key]) => !HIDDEN_KEYS.has(key))
    .map(([key, value]) => `${key.replaceAll("_", " ")}: ${display(value)}`);
  const text = parts.join(" · ");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text || "—";
}

/**
 * A spreadsheet treats a cell starting with = + - @ (or a tab / carriage
 * return) as a formula, so an exported value like "=HYPERLINK(...)" would run
 * when an auditor opens the file. Such cells are prefixed with a quote
 * (OWASP CSV injection guidance); every cell is quoted and inner quotes doubled.
 */
function csvCell(value: string): string {
  const safe = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
  return `"${safe.replaceAll('"', '""')}"`;
}

/** CSV evidence file for *events*, one row per event, oldest last as shown. */
export function auditEventsToCsv(events: AuditEvent[]): string {
  const header = ["seq", "time_utc", "event_type", "event", "actor", "actor_role", "details", "row_hash"];
  const rows = events.map((e) => [
    String(e.seq),
    new Date(e.ts * 1000).toISOString(),
    e.event_type,
    eventLabel(e.event_type),
    e.actor || "",
    display(e.payload?.actor_role ?? ""),
    summarizePayload(e.payload, 10_000),
    e.row_hash,
  ]);
  return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}
