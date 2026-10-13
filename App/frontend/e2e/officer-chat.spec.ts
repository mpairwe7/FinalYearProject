/**
 * A taxpayer's live chat with a URA officer, end to end in the browser.
 *
 * Escalate from an answer → the case waits (the assistant still answers) →
 * an officer takes it: a notice says who, their message appears and the
 * composer now writes to them → the officer ends the chat: the rating dialog
 * asks once → after the rating, a notice hands the taxpayer back to the
 * assistant. The officer side is the mocked thread the chat polls.
 */
import { expect, test, type Page } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent, sendMessage } from "./helpers";

const TICKET = "abc12345-6789-4def-8123-456789abcdef";

type Phase = "waiting" | "officer" | "ended";

async function mockOfficerChat(page: Page) {
  const state = { phase: "waiting" as Phase, replies: [] as string[], ratings: [] as Array<number | null> };
  await page.route("**/api/v1/escalate", (route) =>
    route.fulfill({
      json: { ok: true, ticket_id: TICKET, status: "open", message: "A URA officer has been asked to look at this." },
    }),
  );
  await page.route(`**/api/v1/escalate/${TICKET}/messages**`, (route) => {
    const officer = state.phase !== "waiting";
    const messages = officer
      ? [{ seq: 1, sender: "officer", text: "Which period was assessed twice?", officer_name: "Officer Nakato", created_at: Date.now() / 1000 }]
      : [];
    if (state.phase === "ended") messages.push({ seq: 2, sender: "system", text: "chat_ended", officer_name: "", created_at: Date.now() / 1000 });
    return route.fulfill({
      json: {
        ticket_id: TICKET,
        chat_state: state.phase,
        officer_name: officer ? "Officer Nakato" : "",
        messages,
        last_seq: messages.length,
        csat: { requested: state.phase === "ended", submitted: state.ratings.length > 0 },
      },
    });
  });
  await page.route(`**/api/v1/escalate/${TICKET}/reply`, async (route) => {
    state.replies.push(JSON.parse(route.request().postData() || "{}").message);
    await route.fulfill({ json: { ok: true, ticket_id: TICKET, status: "assigned", seq: 2, message: "ok" } });
  });
  await page.route(`**/api/v1/escalate/${TICKET}/csat`, async (route) => {
    state.ratings.push(JSON.parse(route.request().postData() || "{}").score);
    await route.fulfill({ json: { ok: true, ticket_id: TICKET, status: "answered" } });
  });
  return state;
}

test.describe("Live chat with an officer", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("an officer takes the chat, ends it, and the taxpayer rates it and is handed back", async ({ page }) => {
    await mockBackend(page, { escalate: true, reply: "This requires human review." });
    const chat = await mockOfficerChat(page);
    await page.goto("/");
    await sendMessage(page, "I was assessed VAT twice for the same month");
    await page.getByTestId("handoff-request").click();

    // Waiting: the case is in the queue and the assistant still answers.
    await expect(page.locator(".chat-case-status").filter({ hasText: "Waiting for a URA officer — the assistant can still help meanwhile." })).toBeVisible();
    await expect(page.locator("#composer-input")).toHaveAttribute("placeholder", /^(?!Message the officer)/);

    // An officer takes the case.
    chat.phase = "officer";
    await expect(page.locator(".chat-notice").filter({ hasText: "Officer Nakato has joined. Your messages now go to them." })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".message-row-officer")).toContainText("Which period was assessed twice?");
    await expect(page.locator(".message-row-officer")).toContainText("URA officer · Officer Nakato");
    await expect(page.locator("#composer-input")).toHaveAttribute("placeholder", "Message the officer…");

    // The taxpayer's message goes to the officer, not the assistant.
    await sendMessage(page, "March 2026.");
    await expect.poll(() => chat.replies).toEqual(["March 2026."]);
    await expect(page.locator(".message-row-user").last()).toContainText("March 2026.");

    // The officer ends the chat: the rating is asked for.
    chat.phase = "ended";
    const dialog = page.getByRole("dialog", { name: "How was your chat with Officer Nakato?" });
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    await dialog.getByRole("radio", { name: "4 of 5, Satisfied" }).click();
    await dialog.getByRole("button", { name: "Send rating" }).click();
    await expect.poll(() => chat.ratings).toEqual([4]);

    // Back with the assistant.
    await expect(dialog).toBeHidden();
    await expect(
      page.locator(".chat-notice").filter({
        hasText: "Your chat with the officer has ended. You're now chatting with the URA AI assistant again.",
      }),
    ).toBeVisible();
    await expect(page.locator(".chat-case-banner")).toHaveCount(0);
    await expect(page.locator("#composer-input")).not.toHaveAttribute("placeholder", "Message the officer…");
  });

  test("skipping the rating also hands back to the assistant", async ({ page }) => {
    await mockBackend(page, { escalate: true, reply: "This requires human review." });
    const chat = await mockOfficerChat(page);
    chat.phase = "officer";
    await page.goto("/");
    await sendMessage(page, "I want to dispute my assessment");
    await page.getByTestId("handoff-request").click();
    await expect(page.locator(".chat-notice").filter({ hasText: "Officer Nakato has joined. Your messages now go to them." })).toBeVisible({ timeout: 15_000 });
    chat.phase = "ended";
    const dialog = page.getByRole("dialog", { name: /How was your chat/ });
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    await dialog.getByRole("button", { name: "Skip" }).click();
    await expect.poll(() => chat.ratings).toEqual([null]);
    await expect(page.locator(".chat-notice").filter({ hasText: /You're now chatting with the URA AI assistant again/ })).toBeVisible();
  });
});
