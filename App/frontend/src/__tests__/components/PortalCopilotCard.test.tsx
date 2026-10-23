import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import PortalCopilotCard from "../../components/PortalCopilotCard";
import { sanitizeReturnTo } from "../../lib/oidcFlow";
import {
  consumePendingPortalContinuation,
  savePendingPortalContinuation,
} from "../../lib/portalContinuation";
import { applyTurnMeta } from "../../lib/turnMeta";
import type { ChatTurn } from "../../store/useChatStore";

describe("sanitizeReturnTo (CWE-601 Open Redirect Protection)", () => {
  it("allows safe same-origin relative paths and rejects external URLs", () => {
    expect(sanitizeReturnTo("/?conversation_id=conv-1&auth_resumed=1")).toBe(
      "/?conversation_id=conv-1&auth_resumed=1",
    );
    expect(sanitizeReturnTo("/portal/prn_payments?copilot=1")).toBe(
      "/portal/prn_payments?copilot=1",
    );
    expect(sanitizeReturnTo("https://evil.example.com")).toBeUndefined();
    expect(sanitizeReturnTo("//evil.example.com/phish")).toBeUndefined();
    expect(sanitizeReturnTo("/\\evil.example.com")).toBeUndefined();
    expect(sanitizeReturnTo("javascript:alert(1)")).toBeUndefined();
  });
});

describe("PortalContinuation sessionStorage round-trip", () => {
  it("saves and consumes pending continuation state once", () => {
    savePendingPortalContinuation({
      conversationId: "conv-123",
      taskId: "tin_tax_obligations",
      portalKey: "e_services",
      collectedFields: { tin: "1000023456" },
      returnMode: "chat",
    });

    const consumed = consumePendingPortalContinuation();
    expect(consumed).not.toBeNull();
    expect(consumed?.conversationId).toBe("conv-123");
    expect(consumed?.taskId).toBe("tin_tax_obligations");
    expect(consumed?.collectedFields.tin).toBe("1000023456");

    // Second consume returns null
    expect(consumePendingPortalContinuation()).toBeNull();
  });
});

describe("PortalCopilotCard interactive sub-components", () => {
  it("renders PortalInlineFormCard and submits entered field values", () => {
    const onSubmit = vi.fn();
    render(
      <PortalCopilotCard
        conversationId="conv-form-1"
        onSubmitInlineForm={onSubmit}
        copilot={{
          card_type: "inline_form",
          task_id: "payment_generate_prn",
          portal_key: "prn_payments",
          portal_name: "URA PRN & Payment Gateway Portal",
          title: "Generate Payment Registration Number (PRN)",
          fields: [
            {
              name: "tin",
              label: "10-Digit Taxpayer TIN",
              input_type: "text",
              required: true,
              value: "",
            },
            {
              name: "amount_ugx",
              label: "Payment Amount (UGX)",
              input_type: "number",
              required: true,
              value: "2500000",
            },
          ],
          copilot_portal_url: "/portal/prn_payments?copilot=1",
        }}
      />,
    );

    expect(screen.getByTestId("portal-inline-form-card")).toBeDefined();
    const tinInput = screen.getByLabelText(/10-Digit Taxpayer TIN/i) as HTMLInputElement;
    fireEvent.change(tinInput, { target: { value: "1000023456" } });
    fireEvent.click(screen.getByTestId("portal-inline-form-submit-btn"));

    expect(onSubmit).toHaveBeenCalledWith("payment_generate_prn", {
      tin: "1000023456",
      amount_ugx: "2500000",
    });
  });

  it("renders PortalConfirmationCard, reveals masked NIN, and executes Allow / Disallow", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        status: "executed",
        summary: "Completed Generate Payment Registration Number (PRN) in simulation mode.",
        result: { prn: "226000009999" },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <PortalCopilotCard
        conversationId="conv-confirm-1"
        copilot={{
          card_type: "confirmation_required",
          task_id: "tin_apply_individual",
          portal_key: "tin_registration",
          title: "Apply for Instant Individual TIN",
          verification_prompt:
            "Please carefully verify all information below before allowing the URA AI to execute this action.",
          live: false,
          display_fields: [
            {
              name: "nin",
              label: "14-Character NIN",
              value: "CM92*******23A",
              raw_value: "CM92018100123A",
              sensitive: true,
            },
          ],
          fields: [
            {
              name: "nin",
              label: "14-Character NIN",
              input_type: "text",
              required: true,
              value: "CM92018100123A",
            },
          ],
          request_state: { alg: "hmac-sha256", sig: "abc", payload: {} },
          idempotency_key: "ura_task_12345678",
        }}
      />,
    );

    expect(screen.getByTestId("portal-confirmation-card")).toBeDefined();
    expect(screen.getByTestId("portal-confirm-value-nin").textContent).toBe("CM92*******23A");

    // Reveal masked NIN
    fireEvent.click(screen.getByTestId("portal-sensitive-toggle-nin"));
    expect(screen.getByTestId("portal-confirm-value-nin").textContent).toBe("CM92018100123A");

    // Click Allow & Execute
    fireEvent.click(screen.getByTestId("portal-task-allow-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("portal-task-decision-result")).toBeDefined();
    });
    expect(screen.getByTestId("portal-generated-prn").textContent).toContain("226000009999");
    vi.unstubAllGlobals();
  });

  it("applies portal_copilot metadata via applyTurnMeta", () => {
    const baseTurn: ChatTurn = {
      id: "t-1",
      role: "assistant",
      content: "Verify details",
      timestamp: Date.now(),
    };
    const updated = applyTurnMeta(baseTurn, {
      portal_copilot: {
        card_type: "step_up_login",
        task_id: "tin_tax_obligations",
        title: "Check Taxpayer Filing Obligations",
      },
    });
    expect(updated.portalCopilot?.card_type).toBe("step_up_login");
    expect(updated.portalCopilot?.task_id).toBe("tin_tax_obligations");
  });
});
