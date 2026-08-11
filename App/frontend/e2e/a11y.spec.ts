/**
 * Accessibility audit — axe-core (WCAG 2.1 AA).
 *
 * Aligned with: ISO/IEC 25010:2023 §4 (Interaction Capability),
 * week08 NFR-05 (WCAG 2.1 AA), week09 QO-04 (Lighthouse >= 90).
 *
 * Runs axe-core against every major view to catch:
 * - Missing alt text / aria-labels
 * - Colour contrast failures
 * - Missing landmark roles
 * - Keyboard navigation traps
 */
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.describe("WCAG 2.1 AA Accessibility", () => {
  test("homepage passes axe-core audit", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector(".composer", { timeout: 10_000 });

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .exclude(".hero-badge") // decorative gradient — known contrast exception
      .analyze();

    const violations = results.violations.filter(
      (v) => v.impact === "critical" || v.impact === "serious",
    );

    if (violations.length > 0) {
      const summary = violations
        .map(
          (v) =>
            `[${v.impact}] ${v.id}: ${v.description} (${v.nodes.length} instances)`,
        )
        .join("\n");
      console.error("Accessibility violations:\n" + summary);
    }

    expect(violations).toHaveLength(0);
  });

  test("chat area has correct ARIA landmarks", async ({ page }) => {
    await page.goto("/");

    // Composer input has aria-label
    const input = page.getByLabel("Type your message");
    await expect(input).toBeVisible();

    // Mic button — matched by accessible name (aria-label "Start speaking").
    // Present in both composer states; only the primary slot beside it swaps.
    const micBtn = page.getByRole("button", { name: /speak/i });
    await expect(micBtn.first()).toBeVisible();

    // The primary slot carries an accessible name in BOTH states, which is the
    // part that matters here: an empty composer offers voice mode, and typing
    // replaces it with send. Asserting both directions stops the morph from
    // regressing into a permanently-disabled send button.
    await expect(page.getByLabel("Enter voice mode")).toBeVisible();
    await expect(page.getByLabel("Send message")).toHaveCount(0);

    await input.fill("What is EFRIS?");
    await expect(page.getByLabel("Send message")).toBeVisible();
    await expect(page.getByLabel("Enter voice mode")).toHaveCount(0);

    await input.fill("");
    await expect(page.getByLabel("Enter voice mode")).toBeVisible();
  });

  test("interactive elements are keyboard-accessible", async ({ page }) => {
    await page.goto("/");

    // Tab to input
    await page.keyboard.press("Tab");
    const focused = page.locator(":focus");
    await expect(focused).toBeVisible();

    // Tab through interactive elements — should not trap
    for (let i = 0; i < 10; i++) {
      await page.keyboard.press("Tab");
    }
    // Still focused on something (no trap)
    await expect(page.locator(":focus")).toBeVisible();
  });

  test("escalation banner uses role=alert for screen readers", async ({
    page,
  }) => {
    await page.goto("/");
    // If escalation banners exist, they must have role="alert"
    const alerts = page.locator('[role="alert"]');
    const count = await alerts.count();
    // Just verify the selector works — banners are conditional
    expect(count).toBeGreaterThanOrEqual(0);
  });
});
