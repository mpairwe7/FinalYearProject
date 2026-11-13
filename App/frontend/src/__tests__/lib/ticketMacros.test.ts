import { describe, expect, it } from "vitest";

import { TICKET_MACROS, unfilledPlaceholders } from "../../lib/ticketMacros";

describe("ticketMacros", () => {
  it("ships catalog replies that do not ask for secrets", () => {
    expect(TICKET_MACROS.map((m) => m.id)).toEqual([
      "ack_wait",
      "need_tin",
      "tin_ok",
      "how_to_pay",
      "office_visit",
      "request_document",
      "efris_verified",
      "objection_recorded",
      "wht_credit_ok",
      "customs_doc_check",
    ]);
    expect(TICKET_MACROS.find((m) => m.id === "need_tin")?.body).toMatch(
      /do not send a password or a one-time code/,
    );
  });

  // G636: one click sent the taxpayer an official finding — an invoice
  // "audited", a signature "authentic", a credit "reconciled against URA
  // ledgers", customs "cleared" — that nothing in the workbench recorded.
  it("states no check's result: the officer writes what they found", () => {
    const outcome = /\b(verified|authentic|reconcile[sd]?|compliant|audited|cleared|is active|has been recorded|have been matched)\b/i;
    for (const macro of TICKET_MACROS) {
      const fixed = macro.body.replace(/\[\[[^\]]*\]\]/g, "");
      expect(fixed, macro.id).not.toMatch(outcome);
      expect(macro.label, macro.id).not.toMatch(/\b(verified|cleared|in order)\b/i);
    }
    for (const id of ["tin_ok", "efris_verified", "objection_recorded", "wht_credit_ok", "customs_doc_check"]) {
      expect(unfilledPlaceholders(TICKET_MACROS.find((m) => m.id === id)!.body).length, id).toBeGreaterThan(0);
    }
  });

  it("does not ask for an upload the officer chat cannot take", () => {
    const body = TICKET_MACROS.find((m) => m.id === "request_document")!.body;
    expect(body).not.toMatch(/in this chat|automated document inspection|attach/i);
  });

  it("finds the parts still to fill in, and ignores the case-reference tag", () => {
    expect(unfilledPlaceholders("I checked [[invoice number]]: [[what you found]].")).toEqual([
      "invoice number",
      "what you found",
    ]);
    expect(unfilledPlaceholders("[Case Reference: #TIC-1234] Thank you.")).toEqual([]);
  });
});
