/**
 * The console's CSV evidence files: every cell quoted, inner quotes doubled,
 * and a cell a spreadsheet would run as a formula defused — a taxpayer's
 * comment reading "=HYPERLINK(...)" must open as text in the auditor's sheet.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { csvCell, csvFilename, csvTime, downloadCsv, toCsv } from "../../lib/csv";
import { needsReviewCsv } from "../../components/quality/NeedsReviewPanel";
import type { NeedsReviewItem } from "../../services/analyticsApi";

describe("csvCell", () => {
  it("quotes every cell and doubles the quotes inside", () => {
    expect(csvCell('He said "no"')).toBe('"He said ""no"""');
    expect(csvCell(3)).toBe('"3"');
    expect(csvCell(null)).toBe('""');
    expect(csvCell(undefined)).toBe('""');
  });

  it("defuses a cell a spreadsheet would run as a formula", () => {
    for (const lead of ["=", "+", "-", "@", "\t", "\r"]) {
      expect(csvCell(`${lead}HYPERLINK("http://x")`).startsWith(`"'${lead}`)).toBe(true);
    }
    expect(csvCell("Fine = good")).toBe('"Fine = good"');
  });
});

describe("toCsv", () => {
  it("writes the header, then a row per item, each ending in CRLF", () => {
    expect(toCsv(["a", "b"], [["1", "=2"], [null, "x,y"]])).toBe('"a","b"\r\n"1","\'=2"\r\n"","x,y"\r\n');
  });

  it("names the file by the day it was taken", () => {
    expect(csvFilename("quality-needs-review", new Date("2026-10-13T09:00:00Z"))).toBe("quality-needs-review-2026-10-13.csv");
    expect(csvTime(0)).toBe("");
    expect(csvTime(1_760_000_000)).toBe(new Date(1_760_000_000_000).toISOString());
  });
});

describe("downloadCsv", () => {
  afterEach(() => vi.restoreAllMocks());

  it("saves the file through a temporary link and lets the URL go", () => {
    const create = vi.fn(() => "blob:x");
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    downloadCsv('"a"\r\n', "x.csv");
    expect(click).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith("blob:x");
    expect(document.querySelector('a[download="x.csv"]')).toBeNull();
  });
});

describe("the Needs review export", () => {
  it("carries each item, its decision and who made it — and defuses what a taxpayer wrote", () => {
    const item: NeedsReviewItem = {
      item_type: "rating",
      item_id: "r-low",
      stream: "officer_chat",
      title: "Officer chat rated 2/5 — Officer Okello",
      quote: "",
      comment: "=cmd|' /C calc'!A0",
      topic: "vat",
      locale: "en",
      officer_name: "Officer Okello",
      occurred_at: 1_760_000_000,
      link: { tab: "officer-chat", param: "ticket", value: "tkt-1" },
      review: { status: "action_needed", note: "Coach on refunds", reviewer_name: "Auditor A", updated_at: 1_760_000_100 },
    };
    const [header, row] = needsReviewCsv([item]).trimEnd().split("\r\n");
    expect(header).toContain('"decision","note","decided_by"');
    expect(row).toContain('"Officer chat"');
    expect(row).toContain(`"'=cmd|' /C calc'!A0"`);
    expect(row).toContain('"Action needed","Coach on refunds","Auditor A"');
    expect(needsReviewCsv([{ ...item, review: null }])).toContain('"Open","",""');
  });
});
