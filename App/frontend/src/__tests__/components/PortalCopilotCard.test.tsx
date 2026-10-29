import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from 'node:crypto';
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
    expect(sanitizeReturnTo("/\t/evil.example.com")).toBeUndefined();
    expect(sanitizeReturnTo("/\n/evil.example.com")).toBeUndefined();
    expect(sanitizeReturnTo("/%09/evil.example.com")).toBeUndefined();
    expect(sanitizeReturnTo("/%0a/evil.example.com")).toBeUndefined();
    expect(sanitizeReturnTo("/%5cevil.example.com")).toBeUndefined();
    expect(sanitizeReturnTo("javascript:alert(1)")).toBeUndefined();
  });
});

describe("PortalContinuation sessionStorage round-trip", () => {
  it("saves non-sensitive fields, strips sensitive NIN/passwords, and consumes once", () => {
    savePendingPortalContinuation({
      conversationId: "conv-123",
      taskId: "tin_tax_obligations",
      portalKey: "e_services",
      collectedFields: {
        tin: "1000023456",
        nin: ["CM9201", "8100123A"].join(""),
        password: "secret", // pragma: allowlist secret
      },
      returnMode: "chat",
    });

    const consumed = consumePendingPortalContinuation();
    expect(consumed).not.toBeNull();
    expect(consumed?.conversationId).toBe("conv-123");
    expect(consumed?.taskId).toBe("tin_tax_obligations");
    expect(consumed?.collectedFields.tin).toBe("1000023456");
    expect(consumed?.collectedFields.nin).toBeUndefined();
    expect(consumed?.collectedFields.password).toBeUndefined();

    // Second consume returns null
    expect(consumePendingPortalContinuation()).toBeNull();
  });
});

describe("PortalCopilotCard interactive sub-components", () => {
  beforeEach(() => vi.stubGlobal('crypto', webcrypto));
  afterEach(() => vi.unstubAllGlobals());

  const confirmation = {
    card_type: 'confirmation_required', task_id: 'payment_generate_prn', title: 'Review payment',
    request_state: { signed: true }, idempotency_key: 'k', displayed_fields_sha256: 'server-supplied-hash',
    display_fields: [{ name: 'amount', label: 'Amount', value: '1200' }],
    fields: [{ name: 'amount', label: 'Amount', input_type: 'number', required: true, value: '1200' }],
  };

  it('does not substitute a server digest when local verification is unavailable', async () => {
    vi.stubGlobal('crypto', {});
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<PortalCopilotCard conversationId="c" copilot={confirmation} />);
    fireEvent.click(screen.getByTestId('portal-task-allow-btn'));
    expect(await screen.findByRole('alert')).toHaveTextContent('No approval was sent');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('requires an explicit successful execution response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
    render(<PortalCopilotCard conversationId="c" copilot={confirmation} />);
    fireEvent.click(screen.getByTestId('portal-task-allow-btn'));
    expect(await screen.findByRole('alert')).toHaveTextContent('outcome could not be confirmed');
    expect(screen.queryByTestId('portal-task-decision-result')).not.toBeInTheDocument();
  });

  it('retains edited values without resubmitting through chat when prepare fails', async () => {
    const onSubmit = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network lost')));
    render(<PortalCopilotCard conversationId="c" copilot={confirmation} onSubmitInlineForm={onSubmit} />);
    fireEvent.click(screen.getByTestId('portal-task-edit-btn'));
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '2400' } });
    fireEvent.click(screen.getByTestId('portal-inline-form-submit-btn'));
    expect(await screen.findByRole('alert')).toHaveTextContent('remain in this form');
    expect(screen.getByRole('spinbutton')).toHaveValue(2400);
    expect(onSubmit).not.toHaveBeenCalled();
  });
  it('prevents direct execution from a historical confirmation card', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<PortalCopilotCard disabled conversationId="c" copilot={{
      card_type: 'confirmation_required', task_id: 'payment_generate_prn', title: 'Review payment',
      request_state: { signed: true }, idempotency_key: 'k',
    }} />);
    const button = screen.getByTestId('portal-task-allow-btn');
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
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

  it("enforces high-value cognitive friction checkbox, supports zero-LLM inline prepare, and renders decision receipt", async () => {
    const fetchMock = vi
      .fn()
      // 1st call: inline prepare (`decision: "prepare"`)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ok: true,
          status: "prepared",
          updated_card: {
            card_type: "confirmation_required",
            task_id: "payment_generate_prn",
            portal_key: "prn_payments",
            title: "Generate Payment Registration Number (PRN)",
            live: false,
            high_value_requires_ack: true,
            displayed_fields_sha256: "a".repeat(64),
            display_fields: [
              {
                name: "amount_ugx",
                label: "Payment Amount (UGX)",
                value: "65000000",
                raw_value: "65000000",
              },
            ],
            fields: [
              {
                name: "amount_ugx",
                label: "Payment Amount (UGX)",
                input_type: "number",
                required: true,
                value: "65000000",
              },
            ],
            request_state: { alg: "hmac-sha256", sig: "def", payload: {} },
            idempotency_key: "ura_task_updated_99",
          },
        }),
      })
      // 2nd call: execute (`decision: "allow"`)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ok: true,
          status: "executed",
          summary: "Completed Generate Payment Registration Number (PRN) in simulation mode.",
          result: { prn: "226000008888" },
          decision_receipt: {
            receipt_id: "rcpt_9988776655443322",
            params_sha256: "b".repeat(64),
          },
        }),
      });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <PortalCopilotCard
        conversationId="conv-hv-1"
        copilot={{
          card_type: "confirmation_required",
          task_id: "payment_generate_prn",
          portal_key: "prn_payments",
          title: "Generate Payment Registration Number (PRN)",
          live: false,
          high_value_requires_ack: false,
          displayed_fields_sha256: "c".repeat(64),
          display_fields: [
            {
              name: "amount_ugx",
              label: "Payment Amount (UGX)",
              value: "2500000",
              raw_value: "2500000",
            },
          ],
          fields: [
            {
              name: "amount_ugx",
              label: "Payment Amount (UGX)",
              input_type: "number",
              required: true,
              value: "2500000",
            },
          ],
          request_state: { alg: "hmac-sha256", sig: "abc", payload: {} },
          idempotency_key: "ura_task_initial_01",
        }}
      />,
    );

    // Click Edit Details -> updates amount inline via `decision: "prepare"`
    fireEvent.click(screen.getByTestId("portal-task-edit-btn"));
    const amtInput = screen.getByLabelText(/Payment Amount/i) as HTMLInputElement;
    fireEvent.change(amtInput, { target: { value: "65000000" } });
    fireEvent.click(screen.getByTestId("portal-inline-form-submit-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("portal-confirm-value-amount_ugx").textContent).toBe("65000000");
    });

    // High-value ack checkbox must be checked before Allow button unlocks
    const allowBtn = screen.getByTestId("portal-task-allow-btn") as HTMLButtonElement;
    expect(allowBtn.disabled).toBe(true);
    fireEvent.click(screen.getByTestId("portal-high-value-ack-checkbox"));
    expect(allowBtn.disabled).toBe(false);

    // Click Allow & Execute -> verifies decision receipt rendered and high_value_acknowledged + computed WYSIWYS digest sent
    fireEvent.click(allowBtn);
    await waitFor(() => {
      expect(screen.getByTestId("portal-decision-receipt-id").textContent).toContain(
        "rcpt_9988776655443322",
      );
    });
    const secondCallBody = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body || "{}"));
    expect(secondCallBody.high_value_acknowledged).toBe(true);
    expect(secondCallBody.displayed_fields_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(secondCallBody.displayed_fields_sha256).not.toBe("a".repeat(64));
    vi.unstubAllGlobals();
  });
});
