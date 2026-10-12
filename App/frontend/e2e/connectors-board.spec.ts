/**
 * Staff connector board — /admin/connectors.
 *
 * The taxpayer-chat section has to say what each service is (demo data or
 * live), whether it is answering, and what it can look up. It used to show
 * "Available for chat" for every listed service, with styling from utility
 * classes this app's stylesheet does not have, so the chips read as run-on
 * text. No backend: `/api/**` is intercepted, as in the other browser specs.
 */
import { expect, test, type Page } from "@playwright/test";

import { seedConsent } from "./helpers";

const CHAT_CATALOG = {
  ok: true,
  enabled: true,
  protocol_version: "2026-07-28",
  connectors: [
    {
      namespace: "efris",
      label: "EFRIS",
      description: "Demo data only — not connected to live URA systems. Reads a sample taxpayer profile.",
      summary: "Reads a sample taxpayer profile.",
      mode: "simulation",
      status: "available",
      operation_count: 2,
      operations: [
        { name: "efris_taxpayer_status", title: "Taxpayer status", description: "" },
        { name: "efris_invoice_status", title: "Invoice status", description: "" },
      ],
      read_only: true,
      server: { name: "efris-simulator", version: "1.4.0" },
    },
    {
      namespace: "ursb",
      label: "URSB",
      description: "Searches business registrations.",
      summary: "Searches business registrations.",
      mode: "live",
      status: "unavailable",
      operation_count: 1,
      operations: [{ name: "ursb_verify_business", title: "Verify a business", description: "" }],
      read_only: true,
    },
  ],
};

async function signedInAsAdmin(page: Page) {
  await seedConsent(page);
  await page.addInitScript(() => window.localStorage.setItem("ura_auth_token", "e2e-token-ura_admin"));
  await page.route("**/api/**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        role: "ura_admin",
        email: "admin@ura.go.ug",
        external_id: "ext-admin",
        tenant_id: "default",
      },
    }),
  );
  await page.route(
    (url) => url.pathname === "/api/v1/connectors" && url.searchParams.get("view") === "chat",
    (route) => route.fulfill({ json: CHAT_CATALOG }),
  );
  await page.route(
    (url) => url.pathname === "/api/v1/connectors" && !url.searchParams.get("view"),
    (route) =>
      route.fulfill({
        json: {
          ok: true,
          live: false,
          mode: "simulation",
          connectors: [
            {
              id: "efris",
              name: "EFRIS",
              description: "Local EFRIS simulator.",
              connected: true,
              healthy: true,
              status: "connected",
              tools: ["efris_taxpayer_status"],
              protocol: "MCP",
            },
          ],
        },
      }),
  );
}

test.describe("Staff connector board", () => {
  test("says what each chat service is, whether it answers, and what it can look up", async ({ page }) => {
    await signedInAsAdmin(page);
    await page.goto("/admin/connectors");

    await expect(page.getByRole("heading", { name: "Taxpayer-chat integrations" })).toBeVisible();
    const list = page.getByRole("list", { name: "Reviewed taxpayer-chat integrations" });

    const efris = list.getByRole("listitem").filter({ hasText: "EFRIS" });
    await expect(efris.getByText("Available for chat")).toBeVisible();
    await expect(efris.getByText("Demo data")).toBeVisible();
    await expect(efris.getByText("Read-only")).toBeVisible();
    await expect(efris).toContainText("Taxpayer status · Invoice status");
    // Which server software answered is for staff, and only they are sent it.
    await expect(efris).toContainText("efris-simulator 1.4.0");

    const ursb = list.getByRole("listitem").filter({ hasText: "URSB" });
    await expect(ursb.getByText("Not responding")).toBeVisible();
    await expect(ursb.getByText("Live", { exact: true })).toBeVisible();
    await expect(ursb.getByText("Available for chat")).toHaveCount(0);
  });

  test("the chips are laid out, not run together", async ({ page }) => {
    await signedInAsAdmin(page);
    await page.goto("/admin/connectors");

    const list = page.getByRole("list", { name: "Reviewed taxpayer-chat integrations" });
    const efris = list.getByRole("listitem").filter({ hasText: "EFRIS" });
    const chips = efris.locator(".ops-chip");
    await expect(chips.first()).toBeVisible();

    // Utility classes the stylesheet lacks left these as bare inline text that
    // wrapped as one sentence. As laid-out chips they sit on one row and are
    // held apart.
    const boxes = await chips.evaluateAll((nodes) =>
      nodes.map((node) => {
        const rect = node.getBoundingClientRect();
        return { top: Math.round(rect.top), left: rect.left, right: rect.right };
      }),
    );
    expect(boxes.length).toBeGreaterThanOrEqual(3);
    expect(new Set(boxes.map((box) => box.top)).size).toBe(1);
    for (let i = 1; i < boxes.length; i += 1) {
      expect(boxes[i].left - boxes[i - 1].right).toBeGreaterThanOrEqual(4);
    }
  });
});
