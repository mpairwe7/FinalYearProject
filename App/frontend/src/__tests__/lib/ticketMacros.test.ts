import { describe, expect, it } from "vitest";

import { TICKET_MACROS } from "../../lib/ticketMacros";

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
});
