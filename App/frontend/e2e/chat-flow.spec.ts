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
    await expect(page.getByRole("heading", { name: /URA Tax Assistant/i })).toBeVisible();
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
    // chatv2: the language radios live inside the composer's picker overlay.
    await page.getByRole("button", { name: /Response language/ }).click();
    const lg = page.getByRole("radio", { name: /Luganda/i });
    await lg.click();
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
