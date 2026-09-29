/**
 * The audit trail's translation from ledger vocabulary to sentences, and its
 * evidence export. The export matters most: it leaves the system and is opened
 * in a spreadsheet, where an unguarded cell starting with "=" runs as a formula.
 */
import { describe, expect, it } from "vitest";
import { auditEventsToCsv, eventLabel, summarizePayload } from "../../lib/auditTrail";
import type { AuditEvent } from "../../services/analyticsApi";

const event = (over: Partial<AuditEvent> = {}): AuditEvent => ({
  seq: 7,
  event_id: "e-7",
  event_type: "staff.ticket_updated",
  actor: "officer-1",
  ts: 1_790_000_000,
  payload: { actor_role: "ura_staff", ticket_id: "t-1", status: "assigned", officer_reply_chars: 24 },
  row_hash: "ab".repeat(32),
  ...over,
});

describe("eventLabel", () => {
  it("names staff actions in plain words", () => {
    expect(eventLabel("staff.ticket_viewed")).toBe("Opened a ticket and its transcript");
    expect(eventLabel("staff.flag_set")).toBe("Changed a feature switch");
  });

  it("reads voice events and unknown types without raw punctuation", () => {
    expect(eventLabel("voice_consent_given")).toBe("Voice: consent given");
    expect(eventLabel("staff.new_thing")).toBe("staff · new thing");
  });
});

describe("summarizePayload", () => {
  it("leaves out the actor's role, which has its own column", () => {
    const text = summarizePayload(event().payload);
    expect(text).toBe("ticket id: t-1 · status: assigned · officer reply chars: 24");
    expect(text).not.toContain("actor");
  });

  it("clips a long payload", () => {
    expect(summarizePayload({ note: "x".repeat(500) }, 40)).toHaveLength(40);
  });
});

describe("auditEventsToCsv", () => {
  it("writes a header and one quoted row per event", () => {
    const lines = auditEventsToCsv([event()]).trim().split("\r\n");
    expect(lines[0]).toBe('"seq","time_utc","event_type","event","actor","actor_role","details","row_hash"');
    expect(lines[1]).toContain('"7","2026-09-21T');
    expect(lines[1]).toContain('"Changed a ticket","officer-1","ura_staff"');
  });

  it("neutralises cells a spreadsheet would run as a formula", () => {
    const csv = auditEventsToCsv([event({ actor: '=HYPERLINK("http://x","click")' })]);
    expect(csv).toContain(`"'=HYPERLINK(""http://x"",""click"")"`);
    expect(csv).not.toContain(',"=HYPERLINK');
  });
});
