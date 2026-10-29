/**
 * Core agentic chat-flow E2E — landing shell, composing + sending, the
 * streamed assistant reply, citations/feedback, language switch, and the
 * human-review escalation banner. Backend is mocked (see helpers.ts).
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent, sendMessage } from "./helpers";

test.describe("Agentic chat flow", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("landing renders the assistant shell", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await expect(page).toHaveTitle(/URA Chatbot/);
    await expect(
      page.getByRole("heading", { level: 1, name: /How can I help with your taxes/i }),
    ).toBeVisible();
    await expect(page.getByLabel("Type your message")).toBeVisible();
  });

  test("the primary action becomes send once text is entered", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    // An empty composer no longer parks a disabled send button in the primary
    // slot — it offers the thing you can actually do, which is talk.
    await expect(page.getByLabel("Enter voice mode")).toBeVisible();
    await expect(page.getByLabel("Send message")).toHaveCount(0);

    await page.getByLabel("Type your message").fill("What is the VAT rate?");
    await expect(page.getByLabel("Send message")).toBeEnabled();
    await expect(page.getByLabel("Enter voice mode")).toHaveCount(0);
  });

  test("sending a message renders user + assistant bubbles", async ({ page }) => {
    await mockBackend(page, { reply: "The standard VAT rate in Uganda is 18%." });
    await page.goto("/");
    await sendMessage(page, "What is the current VAT rate?");
    await expect(page.locator(".message-row-user").last()).toContainText("current VAT rate");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });
  });

  test("falls back to the regular chat endpoint when streaming is explicitly unsupported", async ({ page }) => {
    const faqReply = "URA provides domestic tax, customs, taxpayer registration, and online services.";
    await mockBackend(page, { reply: faqReply });
    await page.route("**/api/v1/chat/stream", (route) => route.fulfill({ status: 405 }));
    await page.goto("/");
    await sendMessage(page, "What services does URA provide?");

    const reply = page.locator(".message-row-assistant").last();
    await expect(reply).toContainText(faqReply, { timeout: 15_000 });
    await expect(reply).not.toContainText("could not reach the URA knowledge base");
  });

  test("a truncated stream keeps partial text, disables actions and never replays the POST", async ({ page }) => {
    await mockBackend(page);
    let syncRequests = 0;
    await page.route("**/api/v1/chat", (route) => { syncRequests++; return route.fulfill({ json: { reply: 'Replayed' } }); });
    await page.route("**/api/v1/chat/stream", (route) => route.fulfill({
      contentType: 'text/event-stream', body: [
        'event:metadata',
        'data:{"faithfulness_score":0.99,"next_actions":["Advance payment"]}', '',
        'event:token', 'data:Partial payment guidance', '', '',
      ].join('\n'),
    }));
    await page.goto('/');
    await sendMessage(page, 'Help with payment');
    const reply = page.locator('.message-row-assistant').last();
    await expect(reply).toContainText('Partial payment guidance');
    await expect(reply.getByRole('status')).toContainText('interrupted');
    await expect(reply.getByText('Well grounded')).toHaveCount(0);
    await expect(reply.getByRole('button', { name: 'Advance payment' })).toHaveCount(0);
    expect(syncRequests).toBe(0);
  });

  test('JSON replies retain the inferred objective card and supersede old workflow controls', async ({ page }) => {
    await mockBackend(page);
    await page.route('**/api/v1/chat/stream', (route) => route.fulfill({ status: 405 }));
    let calls = 0;
    await page.route('**/api/v1/chat', (route) => route.fulfill({ json: ++calls === 1 ? {
      reply: 'Choose the taxpayer type.',
      workflow: { id: 'tin', name: 'TIN Registration', status: 'active', options: ['Individual applicant'] },
    } : {
      reply: 'Would you like help with filing?',
      objective_hypothesis: { objective_id: 'filing', title: 'Return filing support', domain: 'returns',
        workflow_id: 'return_filing', confirm_action: 'Guide me through return filing' },
    } }));
    await page.goto('/');
    await sendMessage(page, 'Help with registration');
    await expect(page.getByRole('button', { name: 'Individual applicant' })).toBeVisible();
    await sendMessage(page, 'I have a filing problem');
    await expect(page.getByTestId('objective-hypothesis-card')).toContainText('Return filing support');
    await expect(page.getByRole('button', { name: 'Individual applicant' })).toHaveCount(0);
  });

  test('network failure does not silently resubmit a stateful chat request', async ({ page }) => {
    await mockBackend(page);
    let syncRequests = 0;
    await page.route('**/api/v1/chat', (route) => { syncRequests++; return route.fulfill({ json: {} }); });
    await page.route('**/api/v1/chat/stream', (route) => route.abort('failed'));
    await page.goto('/');
    await sendMessage(page, 'Continue my registration');
    await expect(page.locator('.message-row-assistant').last().getByRole('status')).toContainText('interrupted');
    expect(syncRequests).toBe(0);
  });

  test('a reply from a previous conversation cannot overwrite a new chat', async ({ page }) => {
    await mockBackend(page);
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    let requestStarted!: () => void;
    const started = new Promise<void>((resolve) => { requestStarted = resolve; });
    await page.route('**/api/v1/chat/stream', async (route) => {
      requestStarted();
      await pending;
      await route.fulfill({ contentType: 'text/event-stream', body: 'event:token\ndata:Old private response\n\nevent:done\ndata:\n\n' }).catch(() => {});
    });
    await page.goto('/');
    await sendMessage(page, 'Help with my registration');
    await started;
    // A new chat remains available while the prior request is pending.
    await page.getByRole('button', { name: 'New chat', exact: true }).first().click();
    release();
    await expect(page.locator('.message-row-assistant')).toHaveCount(0);
    await expect(page.getByRole('heading', { level: 1, name: /How can I help with your taxes/i })).toBeVisible();
    await expect(page.getByText('Old private response')).toHaveCount(0);
  });

  test("a starter prompt seeds the conversation", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    // "How do I register for a TIN?" is present in the live landing prompts.
    await page.getByRole("button", { name: "How do I register for a TIN?" }).click();
    // Either the composer is populated or the message is sent — assert one happened.
    const composer = page.getByLabel("Type your message");
    const sentToList = page.locator(".message-row-user");
    await expect
      .poll(async () =>
        (await composer.inputValue()).includes("TIN") || (await sentToList.count()) > 0,
      )
      .toBeTruthy();
  });

  test("Enter submits the message", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    const composer = page.getByLabel("Type your message");
    await composer.fill("How do I file my annual return?");
    await composer.press("Enter");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });
  });

  test("feedback controls appear on an assistant reply", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await sendMessage(page, "VAT rate?");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });
    // The greeting message also has a feedback group, so target the latest
    // controls directly to stay within strict mode.
    const helpful = page.getByLabel("Helpful response").last();
    const unhelpful = page.getByLabel("Unhelpful response").last();
    await expect(helpful).toBeVisible();
    await expect(unhelpful).toBeVisible();
    // The control is interactive — clicking submits feedback without error.
    await helpful.click();
  });

  test("language switch toggles to Luganda", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: /Response language/ }).click();
    await page.getByRole("radio", { name: /Luganda/i }).click();
    // Selecting closes the picker and the trigger reflects the new locale.
    await expect(
      page.getByRole("button", { name: "Response language: Luganda" }),
    ).toBeVisible();
    // Re-open to confirm the radio state persisted.
    await page.getByRole("button", { name: /Response language/ }).click();
    await expect(page.getByRole("radio", { name: /Luganda/i })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  test("escalation surfaces the human-review banner", async ({ page }) => {
    await mockBackend(page, { escalate: true, reply: "This requires human review." });
    await page.goto("/");
    await sendMessage(page, "I want to dispute my tax assessment");
    await expect(
      page.getByRole("alert").filter({ hasText: /Human review/i }),
    ).toBeVisible({ timeout: 15_000 });
  });
});
