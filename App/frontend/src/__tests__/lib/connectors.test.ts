/**
 * What the chat reads about connectors from the server: which services an
 * answer used (the stream's `agent_trace`) and which chosen ones it went
 * without (`connector_notices`). Both are display hints — anything malformed
 * is dropped, never shown.
 */
import { describe, expect, it } from "vitest";
import {
  connectorsUsedFromTrace,
  fallbackConnectorLabel,
  parseConnectorNotices,
  sanitizeConnectorSelection,
  sanitizeConnectorUses,
} from "../../lib/connectors";

const completed = (connector: string, outcome: string, extra: Record<string, unknown> = {}) => ({
  type: "tool_call.completed",
  name: `${connector}_lookup`,
  ok: true,
  connector,
  connector_mode: "simulation",
  connector_label: connector.toUpperCase(),
  connector_outcome: outcome,
  ...extra,
});

describe("connectorsUsedFromTrace", () => {
  it("lists each service once, in the order first asked, with its last outcome", () => {
    const used = connectorsUsedFromTrace([
      { type: "tool_call.started", name: "efris_lookup", connector: "efris" },
      completed("efris", "needs_input"),
      completed("ursb", "no_record", { connector_mode: "live", connector_label: "URSB" }),
      completed("efris", "found"),
    ]);

    expect(used).toEqual([
      { namespace: "efris", label: "EFRIS", mode: "simulation", outcome: "found" },
      { namespace: "ursb", label: "URSB", mode: "live", outcome: "no_record" },
    ]);
  });

  it("ignores local tools, unknown outcomes and anything that is not a trace", () => {
    expect(
      connectorsUsedFromTrace([
        { type: "tool_call.completed", name: "calculate_vat", ok: true },
        completed("Bad Namespace!", "found"),
        completed("efris", "exploded", { connector_label: "" }),
      ]),
    ).toEqual([{ namespace: "efris", label: "Efris", mode: "simulation" }]);
    expect(connectorsUsedFromTrace({ type: "tool_call.completed" })).toEqual([]);
    expect(connectorsUsedFromTrace(null)).toEqual([]);
  });
});

describe("parseConnectorNotices", () => {
  it("keeps only well-formed notices with a known reason", () => {
    expect(
      parseConnectorNotices([
        { namespace: "payment_system", label: "Payments", reason: "unavailable" },
        { namespace: "efris", label: "EFRIS", reason: "not_offered" },
        { namespace: "dts", reason: "disabled" },
        { namespace: "efris", label: "EFRIS", reason: "exploded" },
        { namespace: "<script>", label: "x", reason: "disabled" },
        "efris",
      ]),
    ).toEqual([
      { namespace: "payment_system", label: "Payments", reason: "unavailable" },
      { namespace: "efris", label: "EFRIS", reason: "not_offered" },
      { namespace: "dts", label: "Dts", reason: "disabled" },
    ]);
    expect(parseConnectorNotices(undefined)).toEqual([]);
  });
});

describe("stored connector state", () => {
  it("a selection is namespaces only, de-duplicated and bounded", () => {
    const many = Array.from({ length: 12 }, (_, i) => `svc_${i}`);
    expect(sanitizeConnectorSelection(["efris", "efris", "Not Valid", 3, ...many])).toHaveLength(8);
    expect(sanitizeConnectorSelection(["efris", "efris", "ursb"])).toEqual(["efris", "ursb"]);
    expect(sanitizeConnectorSelection("efris")).toEqual([]);
  });

  it("stored uses are validated on the way back in", () => {
    expect(
      sanitizeConnectorUses([
        { namespace: "efris", label: "EFRIS", mode: "simulation", outcome: "found" },
        { namespace: "ursb", label: "URSB", mode: "staging", outcome: "maybe" },
        { label: "no namespace" },
      ]),
    ).toEqual([
      { namespace: "efris", label: "EFRIS", mode: "simulation", outcome: "found" },
      { namespace: "ursb", label: "URSB" },
    ]);
  });

  it("names an undescribed service readably", () => {
    expect(fallbackConnectorLabel("payment_system")).toBe("Payment System");
    expect(fallbackConnectorLabel("efris")).toBe("Efris");
  });
});
