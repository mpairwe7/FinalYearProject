/**
 * The audit trail's translation from ledger vocabulary to sentences, and its
 * evidence export. The export matters most: it leaves the system and is opened
 * in a spreadsheet, where an unguarded cell starting with "=" runs as a formula.
 */
import { describe, expect, it } from "vitest";
import {
  AUDIT_FILTERS,
  auditEventsToCsv,
  checkCoverage,
  eventLabel,
  sealStatus,
  summarizePayload,
} from "../../lib/auditTrail";
import type { AuditEvent, AuditVerification } from "../../services/analyticsApi";

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

const check = (over: Partial<AuditVerification> = {}): AuditVerification => ({
  ledger_enabled: true,
  valid: true,
  scope: "full",
  rows_checked: 40,
  first_seq: 1,
  last_seq: 40,
  head_hash: "f".repeat(64),
  breaks: [],
  anchors_checked: 2,
  anchor_breaks: [],
  latest_anchor: null,
  unsealed_rows: 40,
  verified_at: 1_790_000_000,
  ...over,
});

describe("integrity wording", () => {
  it("names reads, checks and seals of the trail itself", () => {
    expect(eventLabel("audit.trail_viewed")).toBe("Searched the audit trail");
    expect(eventLabel("audit.chain_verified")).toBe("Checked the audit trail is intact");
    expect(eventLabel("audit.sealed")).toBe("Sealed the audit trail");
  });

  it("says what a full check covered", () => {
    expect(checkCoverage(check())).toBe("40 events (#1 to #40) and 2 seals checked");
    expect(checkCoverage(check({ rows_checked: 1, first_seq: 1, last_seq: 1, anchors_checked: 0 }))).toBe(
      "1 event (#1 to #1) checked",
    );
    expect(checkCoverage(check({ rows_checked: 0, anchors_checked: 0 }))).toBe("No events recorded yet");
  });

  it("says a large ledger was checked from its newest seal", () => {
    const text = checkCoverage(check({ scope: "since_seal", rows_checked: 3, first_seq: 41, last_seq: 43 }));
    expect(text).toBe("Newest seal re-checked, plus 3 events after it (#41 to #43)");
  });

  it("states the seal and what has been written since", () => {
    const fmt = () => "1 Sep 2026";
    expect(sealStatus(check(), fmt)).toMatch(/^Not sealed yet/);
    const sealed = check({
      unsealed_rows: 1,
      latest_anchor: {
        anchor_id: "a",
        first_seq: 1,
        last_seq: 39,
        merkle_root: "c".repeat(64),
        head_hash: "f".repeat(64),
        created_at: 1,
      },
    });
    expect(sealStatus(sealed, fmt)).toBe("Sealed through #39 on 1 Sep 2026; 1 event written since.");
  });
});

describe("call desk events", () => {
  it("reads call reads and desk actions as sentences", () => {
    expect(eventLabel("voice_staff_viewed_call")).toBe("Opened a call and its transcript");
    expect(eventLabel("voice_staff_listened_call")).toBe("Listened in to a live call");
    expect(eventLabel("voice_officer_transferred")).toBe("Transferred a call");
    expect(eventLabel("staff.call_reviewed")).toBe("Rated a call");
  });

  it("offers call filters", () => {
    const values = AUDIT_FILTERS.map((f) => f.value);
    expect(values).toContain("voice_staff");
    expect(values).toContain("voice_officer");
  });

  it("keeps ledger plumbing out of the details", () => {
    const text = summarizePayload({
      voice_audit_id: "row-1",
      session_id: "call_abc",
      user_id: "off-1",
      audio_hash: "",
      actor_role: "ura_staff",
      transport: "livekit",
    });
    expect(text).toBe("session id: call_abc · transport: livekit");
  });
});
