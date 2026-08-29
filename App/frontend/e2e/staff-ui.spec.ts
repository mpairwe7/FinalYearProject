/**
 * Staff operations UI on Chromium — /signin, /admin, /agent.
 *
 * These pages were built and verified in Firefox (the only engine that paints in
 * the dev sandbox), so this spec exists to confirm the layout in Blink. It
 * asserts computed styles and geometry rather than comparing screenshots,
 * because the engine-specific risks here are numeric: CSS-grid track resolution,
 * `backdrop-filter` on the fixed rail, and one selector that has to out-specify
 * `:root[data-theme="light"] a` from globals.css. A pixel diff would flag
 * antialiasing noise and miss all three. (The inset accent edges this also used
 * to guard went with the console rebuild — the tiles carry no status colour.)
 *
 * No backend: `/api/**` is intercepted, same as the other browser specs.
 */
import { expect, test, type Page } from "@playwright/test";

import { expectAuthCta, seedConsent } from "./helpers";

const ADMIN_ID = "tkt-admin-1";

function ticket(over: Record<string, unknown> = {}) {
  // Waits are computed from `created_at`, so anchor them to test start rather
  // than a fixed date — otherwise "2d 3h" drifts as the suite ages.
  const now = Math.floor(Date.now() / 1000);
  return {
    id: ADMIN_ID,
    created_at: now - 3600 * 5,
    status: "open",
    priority: "urgent",
    // The staff queue labels a case with `reason`, falling back to the handoff
    // topic (topicLabel()) — /admin/tickets has always worked that way and
    // /agent joined it on the shared QueueRow. The backend's escalation_reason
    // is a sentence rather than a code (see _evaluate_response_judge), so keep
    // the stub that shape or the label under test is not the one shipped.
    reason: "Taxpayer disputes a double VAT charge",
    user_query: "I was charged VAT twice on the same import and URA says it is correct.",
    bot_reply: "I cannot resolve a double-charge dispute.",
    officer_reply: "",
    staff_note: "",
    team: "vat",
    handoff: {
      topic: "Double VAT charge on one import",
      summary: "Taxpayer was charged VAT twice on a single import declaration.",
      opening_guidance: "Acknowledge the double charge before asking for the declaration number.",
      required_details: ["Import declaration number", "Both payment receipts"],
      transfer_style: "warm",
      sentiment: "frustrated",
      turns_before_handoff: 3,
    },
    transcript: [
      {
        created_at: now - 3600 * 5,
        user_message: "I paid VAT twice on one import.",
        bot_reply: "Let me check what I can tell you about VAT on imports.",
      },
    ],
    ...over,
  };
}

/**
 * Sign in as staff and stub every endpoint the pages read.
 *
 * Route order matters: Playwright matches the most recently added route first,
 * so the generic `tickets/:id` pattern is registered BEFORE `sla`/`stats`, which
 * would otherwise be swallowed by it.
 */
async function signedInAs(page: Page, role: string) {
  await seedConsent(page);
  await page.addInitScript((r) => {
    window.localStorage.setItem("ura_auth_token", `e2e-token-${r}`);
  }, role);

  await page.route("**/api/**", (route) => route.fulfill({ json: {} }));

  await page.route("**/api/v1/me", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        role,
        email: `${role}@ura.go.ug`,
        external_id: `ext-${role}`,
        tenant_id: "default",
      },
    }),
  );
  // Mirrors get_authority_status() exactly. It used to fulfil a `sources`
  // ARRAY, which the endpoint has never returned — the page read
  // `sources.length` against it and the assertion below passed on a shape that
  // does not exist, while the real console showed an em-dash on every deploy.
  // A stub that is not the contract is worse than no stub.
  await page.route("**/api/v1/authority/status", (route) =>
    route.fulfill({
      json: {
        ok: true,
        configured: true,
        fresh: true,
        version: "2026-07",
        generated_at: "2026-07-01T00:00:00Z",
        age_days: 12,
        max_age_days: 120,
        sources_checked: 2,
        invalid_sources: [],
        errors: [],
      },
    }),
  );
  // Generic detail route first — see the note above.
  await page.route("**/api/v1/admin/tickets/*", (route) =>
    route.fulfill({ json: ticket() }),
  );
  await page.route("**/api/v1/admin/tickets/stats**", (route) =>
    route.fulfill({
      json: { period_days: 30, total: 5, open: 4, assigned: 0, resolved: 1, wontfix: 0, by_priority: {} },
    }),
  );
  await page.route("**/api/v1/admin/tickets/sla**", (route) =>
    route.fulfill({
      json: {
        period_days: 30,
        tickets: 5,
        responded: 2,
        resolved: 1,
        awaiting_first_response: 3,
        awaiting_next_response: 1,
        // Deliberately larger than the three rows the queue mock returns: the
        // Unassigned tile has to read this, not count the page it loaded.
        unassigned: 42,
        median_response_seconds: 11_820,
        median_resolution_seconds: 7_200,
        median_next_reply_seconds: 3_600,
        breaching_first_response: 2,
        breaching_next_reply: 0,
        breaching: 2,
      },
    }),
  );
  await page.route("**/api/v1/admin/tickets?**", (route) =>
    route.fulfill({
      json: {
        tickets: [
          ticket(),
          ticket({
            id: "tkt-2",
            priority: "high",
            created_at: Math.floor(Date.now() / 1000) - 9_240,
            handoff: { topic: "Non-resident WHT rate, FY2026-27" },
            user_query: "What is the withholding tax rate on professional fees?",
          }),
          ticket({
            id: "tkt-3",
            priority: "low",
            created_at: Math.floor(Date.now() / 1000) - 180_000,
            handoff: { topic: "Late objection to assessment" },
            user_query: "Can I object to an assessment after the 45-day window?",
          }),
        ],
        total: 3,
      },
    }),
  );
}

/**
 * Open queue row `n` on /agent, from whichever pane is currently on screen.
 *
 * Above 960px the queue and the case sit side by side and this is one click.
 * Below it they are alternating views (`.ag-split.is-open` hides the queue),
 * so reaching a different row means going Back first. Unlike the off-canvas
 * rail in helpers.ts, these panes are switched with `display: none`, so
 * isVisible() reports them honestly and can be branched on.
 */
async function openRow(page: Page, n: number) {
  if (!(await page.locator(".ag-queue-pane").isVisible())) {
    await page.getByRole("button", { name: "Back to queue" }).click();
  }
  await page.locator(".st-row").nth(n).click();
}

/** Grid tracks resolve to px in getComputedStyle; count them to read the layout. */
async function columnCount(page: Page, selector: string): Promise<number> {
  return page.locator(selector).evaluate(
    (el) => getComputedStyle(el).gridTemplateColumns.trim().split(/\s+/).filter(Boolean).length,
  );
}

test.describe("Staff UI on Chromium", () => {
  test.describe("access gate", () => {
    test("anonymous visitor is asked to sign in, not shown empty panels", async ({ page }) => {
      await seedConsent(page);
      await page.route("**/api/**", (route) => route.fulfill({ json: {} }));
      await page.goto("/admin");
      await expect(page.getByRole("heading", { name: "Sign in to continue" })).toBeVisible();

      // The CTA sits on a gradient, so its label must stay white. globals.css has
      // `:root[data-theme="light"] a { color: var(--ura-blue) }` at (0,2,1); the
      // three-class rule in staffGuard.css exists to beat it.
      const cta = page.getByRole("link", { name: "Go to sign-in" });
      await expect(cta).toBeVisible();
      await expect(cta).toHaveCSS("color", "rgb(255, 255, 255)");
    });

    test("signed-in non-staff is refused by name and role", async ({ page }) => {
      await signedInAs(page, "verified_taxpayer");
      await page.goto("/admin");
      await expect(
        page.getByRole("heading", { name: "You do not have access to this page" }),
      ).toBeVisible();
      await expect(page.getByText(/limited to ura_admin, ura_auditor/)).toBeVisible();
    });

    test("ura_staff is refused the admin overview but keeps the queue", async ({ page }) => {
      await signedInAs(page, "ura_staff");
      await page.goto("/admin");
      await expect(page.getByText(/limited to ura_admin, ura_auditor/)).toBeVisible();

      await page.goto("/agent");
      await expect(page.getByRole("heading", { name: "My queue" })).toBeVisible();
    });
  });

  test.describe("shared nav", () => {
    test("is a fixed rail, scoped to the role, and survives backdrop-filter", async ({ page }) => {
      await signedInAs(page, "ura_staff");
      await page.goto("/agent");

      // The nav is a fixed vertical rail, not a sticky bar — see
      // components/staffGuard.css for why the horizontal strip was dropped.
      const nav = page.locator("nav.staff-rail");
      await expect(nav).toBeVisible();
      await expect(nav).toHaveCSS("position", "fixed");
      // Blink needs the -webkit- prefix on some builds; either property resolving
      // to a blur means the effect is live rather than silently dropped.
      const blurred = await nav.evaluate((el) => {
        const s = getComputedStyle(el);
        // The prefixed property is not in the DOM typings, so read it by name.
        return (
          /blur/.test(s.backdropFilter || "") ||
          /blur/.test(s.getPropertyValue("-webkit-backdrop-filter") || "")
        );
      });
      expect(blurred).toBe(true);

      // ura_staff has no Analytics or Overview entry (StaffGuard NAV roles).
      // Labels are opacity-0 while collapsed but still in the a11y tree, so
      // these match on accessible name regardless of rail width.
      await expect(nav.getByRole("link", { name: "My queue" })).toBeAttached();
      await expect(nav.getByRole("link", { name: "All tickets" })).toBeAttached();
      await expect(nav.getByRole("link", { name: "Analytics" })).toHaveCount(0);
      await expect(nav.getByRole("link", { name: "Overview" })).toHaveCount(0);

      // The role used to sit in its own `.staff-role-pill`. The rail rebuild
      // folded the toolbar, the pill, the address and "Sign out" into the one
      // account row at the foot, so the role now rides in that row's trigger as
      // the short label ("Staff", not "Tax agent") — see AccountMenu. Asserted
      // on the accessible name rather than the visible span, because the span is
      // `display: none` while the rail is collapsed and the name is what the
      // role is announced by in either state.
      await expect(nav.locator(".staff-acct-trigger")).toHaveAttribute("aria-label", /\bStaff\b/);
    });
  });

  test.describe("/admin overview", () => {
    test.beforeEach(async ({ page }) => {
      await signedInAs(page, "ura_admin");
      await page.goto("/admin");
      await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
    });

    test("renders every metric from the API, humanised", async ({ page }) => {
      // Six since the SLA/assignment board landed: open, awaiting first reply,
      // past the 24h SLA, unassigned, then the two medians. The tiles are the
      // console's shared `.ops-stat` since the redesign; the numbers and their
      // order are unchanged.
      const metrics = page.locator(".ops-stat-grid .ops-stat");
      await expect(metrics).toHaveCount(6);
      // 11_820s → 3.3h and 7_200s → 2.0h: the formatDuration() contract, which
      // reports medians in decimal hours (h+m is kept for per-ticket waits,
      // where the minutes are what an officer is actually watching).
      await expect(metrics.nth(4)).toContainText("3.3h");
      await expect(metrics.nth(5)).toContainText("2.0h");
      await expect(metrics.nth(0)).toContainText("4");
      await expect(metrics.nth(1)).toContainText("3");
      // The last two come from the SLA payload's live counts. `unassigned` used
      // to be counted from the queue rows this page had loaded — three of them,
      // all unclaimed — so the tile agreed with the stub by accident and was
      // capped at the page size against a real backend.
      await expect(metrics.nth(2)).toContainText("2");
      await expect(metrics.nth(3)).toContainText("42");
    });

    test("attention is carried by the figures, not by tinting the tiles", async ({ page }) => {
      // The tiles used to take a `tone` and paint a 3px inset accent edge for
      // it. The console rebuild dropped both: a coloured bar down the side of a
      // card is decoration this console does not use, and what a tile means is
      // in its label, value and hint. So the two tiles that would once have been
      // tinted — awaiting_first_response = 3 and 3 unassigned — have to read
      // just as clearly with nothing colouring them.
      const metrics = page.locator(".ops-stat-grid .ops-stat");
      await expect(metrics.nth(1)).toContainText("Awaiting first response");
      await expect(metrics.nth(1)).toContainText("3");
      await expect(metrics.nth(1)).toContainText("nobody has replied yet");
      await expect(metrics.nth(3)).toContainText("Unassigned");
      // 42 from the SLA payload, not 3 — the number of rows the queue mock
      // returns. Deriving this from the loaded page capped it at the page
      // size, so a queue of thousands reported the page length.
      await expect(metrics.nth(3)).toContainText("42");
      await expect(metrics.nth(3)).toContainText("waiting to be claimed");

      // No tile carries a status class, and every tile shares one surface —
      // the guard against the accent edge or a colour wash coming back.
      await expect(page.locator(".ops-stat.is-warn, .ops-stat.is-danger, .ops-stat.is-good")).toHaveCount(
        0,
      );
      const backgrounds = new Set(
        await metrics.evaluateAll((els) => els.map((el) => getComputedStyle(el).backgroundColor)),
      );
      expect(backgrounds.size).toBe(1);
    });

    test("queue is ordered urgent-first with real waits", async ({ page }) => {
      const rows = page.locator(".ov-queue li");
      await expect(rows).toHaveCount(3);
      await expect(rows.nth(0).locator(".ov-pri")).toHaveText("urgent");
      await expect(rows.nth(2).locator(".ov-pri")).toHaveText("low");
      await expect(page.locator(".ov-queue-panel .ops-chip.is-danger")).toContainText("1 urgent");
      // 5h of waiting, formatted by waitingFor().
      await expect(rows.nth(0).locator(".ov-q-wait")).toContainText("5h");
    });

    test("answer-authority panel reports the manifest state", async ({ page }) => {
      await expect(page.getByRole("heading", { name: "Answer authority" })).toBeVisible();
      await expect(page.locator(".ov-chip.good")).toHaveText("Fresh");
      const kv = page.locator(".ov-kv dd");
      await expect(kv.nth(1)).toHaveText("2026-07");
      await expect(kv.nth(2)).toHaveText("12 of 120 days");
      // sources_checked, the key the endpoint actually emits.
      await expect(kv.nth(3)).toHaveText("2 checked");
    });

    test("live tiles link to the population they count", async ({ page }) => {
      // Awaiting-first-response and past-SLA count open AND in-progress cases,
      // so a link filtered to open lands on fewer rows than the tile promised.
      for (const label of ["Awaiting first response", "Past 24-hour SLA", "Unassigned"]) {
        const tile = page.locator(".ops-stat", { hasText: label });
        await expect(tile).toHaveAttribute("href", "/admin/tickets?status=any");
      }
    });

    test("two-column at desktop, single column below 900px", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 900 });
      expect(await columnCount(page, ".ov-cols")).toBe(2);
      // The 1.45fr/1fr split must actually favour the queue. The right column
      // is now a stack of two panels (authority + workload), so measure the
      // columns rather than assuming each is a single section.
      const [q, a] = await page.locator(".ov-cols > *").evaluateAll((els) =>
        els.map((e) => e.getBoundingClientRect().width),
      );
      expect(q).toBeGreaterThan(a);

      await page.setViewportSize({ width: 860, height: 900 });
      expect(await columnCount(page, ".ov-cols")).toBe(1);
    });

    test("no horizontal page scroll at any breakpoint", async ({ page }) => {
      for (const width of [1440, 1024, 900, 768, 560, 390]) {
        await page.setViewportSize({ width, height: 900 });
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `horizontal overflow at ${width}px`).toBeLessThanOrEqual(1);
      }
    });
  });

  test.describe("/agent queue", () => {
    test.beforeEach(async ({ page }) => {
      await signedInAs(page, "ura_admin");
      await page.goto("/agent");
      await expect(page.getByRole("heading", { name: "My queue" })).toBeVisible();
    });

    test("lands on the top ticket instead of an empty pane", async ({ page }) => {
      // Nothing clicked yet: the top of the queue is already the live case, so
      // the placeholder never renders at any width.
      await expect(page.locator(".st-row.is-selected")).toHaveCount(1);
      await expect(page.locator(".st-row").first()).toHaveClass(/is-selected/);
      await expect(page.getByText("Pick a ticket to see the brief")).toHaveCount(0);

      await openRow(page, 0);
      await expect(
        page.getByRole("heading", { name: "Taxpayer disputes a double VAT charge" }),
      ).toBeVisible();
    });

    test("handoff brief shows the warm-transfer pill and what to have ready", async ({ page }) => {
      await openRow(page, 0);
      // The warm-transfer and sentiment markers moved out of the brief and into
      // the case header pills when /agent and /admin/tickets were merged onto
      // the shared TicketCase; the brief itself still carries the guidance.
      const pills = page.locator(".st-case-pills");
      await expect(pills.getByText("warm transfer")).toBeVisible();
      await expect(pills.getByText("frustrated")).toBeVisible();

      // The brief used to repeat the warm-transfer signal as its own accent
      // edge. The console rebuild dropped the edge along with the tiles' — the
      // pill above is where a warm transfer is announced now, and the brief is
      // left to carry the guidance.
      const brief = page.locator(".st-brief");
      await expect(brief).toBeVisible();
      await expect(brief.getByText("Import declaration number")).toBeVisible();
      await expect(brief.getByText(/Acknowledge the double charge/)).toBeVisible();
    });

    test("transcript is shown in full, not summarised away", async ({ page }) => {
      await openRow(page, 0);
      await expect(page.locator(".st-transcript")).toContainText(
        "I paid VAT twice on one import.",
      );
      // Counts the turns actually rendered rather than handoff.turns_before_handoff,
      // so the label can never claim more conversation than the pane shows.
      await expect(page.getByText(/1 turn, as it stood/)).toBeVisible();
    });

    test("taxpayer reply and internal note stay separate inputs", async ({ page }) => {
      // Merging these would leak an internal note to the taxpayer, so the
      // separation is load-bearing rather than cosmetic.
      await openRow(page, 0);
      const boxes = page.locator(".st-field textarea");
      await expect(boxes).toHaveCount(2);
      await expect(page.getByText("They see this on their next turn")).toBeVisible();
      await expect(page.getByText("Never shown to the taxpayer.")).toBeVisible();
    });

    test("send is disabled until something is typed", async ({ page }) => {
      await openRow(page, 0);
      const send = page.getByRole("button", { name: "Send reply" });
      await expect(send).toBeDisabled();
      await page.locator(".st-field textarea").first().fill("Send both receipts and we will refund.");
      await expect(send).toBeEnabled();
    });

    test("switching ticket clears a half-typed reply", async ({ page }) => {
      await openRow(page, 0);
      const box = page.locator(".st-field textarea").first();
      await box.fill("half-written answer for the wrong taxpayer");
      await openRow(page, 1);
      await expect(box).toHaveValue("");
    });

    test("split pane collapses to one column below 960px", async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 900 });
      expect(await columnCount(page, ".ag-split")).toBe(2);
      await page.setViewportSize({ width: 900, height: 900 });
      expect(await columnCount(page, ".ag-split")).toBe(1);
    });

    test("no horizontal page scroll at any breakpoint", async ({ page }) => {
      for (const width of [1440, 1024, 960, 768, 560, 390]) {
        await page.setViewportSize({ width, height: 900 });
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `horizontal overflow at ${width}px`).toBeLessThanOrEqual(1);
      }
    });
  });

  /**
   * Every console route, not just the two that happened to have a test.
   *
   * `/admin/tickets` overflowed by 363px at 360px wide — its five-column SLA
   * strip sat in the page header's actions slot, where a flex item's default
   * `min-width: auto` let it push the whole document sideways. Neither of the
   * per-page checks above covered that route, so nothing caught it.
   */
  test.describe("layout at every width", () => {
    const ROUTES = [
      "/admin",
      "/agent",
      "/admin/tickets",
      "/admin/flags",
      "/admin/overrides",
      "/admin/outbox",
      "/analytics",
      "/analytics/evaluation",
    ];

    test("no console route scrolls the page sideways", async ({ page }) => {
      test.slow();
      await signedInAs(page, "ura_admin");
      for (const width of [1440, 1024, 900, 720, 560, 412, 360]) {
        await page.setViewportSize({ width, height: 900 });
        for (const route of ROUTES) {
          await page.goto(route);
          await expect(page.locator("nav.staff-rail")).toBeVisible();
          const overflow = await page.evaluate(
            () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
          );
          expect(overflow, `${route} overflows at ${width}px`).toBeLessThanOrEqual(1);
        }
      }
    });
  });

  test.describe("/signin", () => {
    test("offers the OIDC path, live or disabled to match the deployment", async ({ page }) => {
      await seedConsent(page);
      await page.route("**/api/**", (route) => route.fulfill({ json: {} }));
      await page.goto("/signin");
      await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();

      await expectAuthCta(page, /Continue with URA identity provider/);
    });

    test("dark and light both render the card readably", async ({ page }) => {
      await seedConsent(page);
      await page.route("**/api/**", (route) => route.fulfill({ json: {} }));
      for (const scheme of ["dark", "light"] as const) {
        await page.emulateMedia({ colorScheme: scheme });
        await page.goto("/signin");
        const card = page.locator(".signin-card");
        await expect(card).toBeVisible();
        // A transparent card means the token did not resolve on this canvas.
        const bg = await card.evaluate((el) => getComputedStyle(el).backgroundColor);
        expect(bg, `signin card background in ${scheme}`).not.toBe("rgba(0, 0, 0, 0)");
      }
    });
  });
});
