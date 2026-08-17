import { describe, expect, it } from "vitest";
import {
  formatDuration,
  ticketMatchesQuery,
  waitingFor,
  waitTone,
} from "../../lib/ticketUi";
import type { TicketQueueItem } from "../../services/analyticsApi";

const NOW = Date.parse("2026-08-17T12:00:00Z");

function ticket(partial: Partial<TicketQueueItem> = {}): TicketQueueItem {
  return {
    id: "tkt-1",
    status: "open",
    priority: "high",
    reason: "User asked for a human",
    user_query: "my TIN is not working",
    bot_reply: "",
    created_at: NOW / 1000 - 7200,
    updated_at: NOW / 1000,
    ...partial,
  };
}

describe("ticketUi", () => {
  it("formats waiting time for an officer, not as raw seconds", () => {
    expect(waitingFor(NOW / 1000 - 120, NOW)).toBe("2m");
    expect(waitingFor(NOW / 1000 - 7200, NOW)).toBe("2h");
    expect(waitingFor(NOW / 1000 - 90000, NOW)).toBe("1d 1h");
  });

  it("warns at 4 hours and breaches at 24 hours until someone replies", () => {
    expect(waitTone(NOW / 1000 - 3600, undefined, undefined, NOW)).toBe("ok");
    expect(waitTone(NOW / 1000 - 5 * 3600, undefined, undefined, NOW)).toBe("warn");
    expect(waitTone(NOW / 1000 - 26 * 3600, undefined, undefined, NOW)).toBe("breach");
    expect(waitTone(NOW / 1000 - 26 * 3600, NOW / 1000 - 100, undefined, NOW)).toBe("ok");
  });

  it("ages next-reply from the last officer touch on an open case", () => {
    const first = NOW / 1000 - 48 * 3600;
    const lastReply = NOW / 1000 - 5 * 3600;
    expect(waitTone(NOW / 1000 - 72 * 3600, first, lastReply, NOW)).toBe("warn");
    expect(waitTone(NOW / 1000 - 72 * 3600, first, NOW / 1000 - 26 * 3600, NOW)).toBe("breach");
  });

  it("matches a queue search against reason, query and assignee", () => {
    const row = ticket({ assignee: "officer@ura.go.ug", team: "disputes" });
    expect(ticketMatchesQuery(row, "TIN")).toBe(true);
    expect(ticketMatchesQuery(row, "officer@")).toBe(true);
    expect(ticketMatchesQuery(row, "customs")).toBe(false);
  });

  it("renders SLA durations without raw second counts", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(900)).toBe("15m");
    expect(formatDuration(7200)).toBe("2.0h");
  });
});
