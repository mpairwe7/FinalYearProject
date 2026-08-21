/**
 * Desktop sidebar: collapse, hover peek, and the Ctrl+B shortcut.
 *
 * The peek is the part worth pinning down. Collapsed, hovering the toggle
 * floats the rail in *over* the conversation and the transcript does not move;
 * only a click docks it, and only then does the grid track open and the
 * transcript reflow. Asserting the composer's x-position on both sides of the
 * hover is what separates the two, and it is the behaviour that would quietly
 * regress if the rail ever went back to being a plain two-state toggle.
 *
 * Desktop-only by construction: below 1024px the rail is a drawer and there is
 * no hover to peek with.
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent } from "./helpers";

test.describe("Sidebar collapse and hover peek", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page);
    await page.goto("/");
    // The shortcut and the peek are both client-side, so wait for hydration
    // rather than racing the server-rendered markup.
    await expect(page.getByLabel("Type your message")).toBeVisible();
  });

  test("hover peeks without moving the transcript; leaving retracts; clicking docks", async ({
    page,
  }) => {
    const shell = page.locator(".app-shell.chatv2");
    const composer = page.getByLabel("Type your message");

    await page.locator(".rail-toggle-desktop").click();
    await expect(shell).toHaveAttribute("data-sidebar", "collapsed");
    const xCollapsed = (await composer.boundingBox())!.x;

    // Peek: the rail floats in and the conversation stays exactly where it was.
    await page.locator(".hdrv2-collapse").hover();
    await expect(shell).toHaveAttribute("data-rail-peek", "true");
    await expect(page.locator("aside.conversation-rail")).toBeVisible();
    await expect(shell).toHaveAttribute("data-sidebar", "collapsed");
    expect((await composer.boundingBox())!.x).toBe(xCollapsed);

    // Pointer away: it retracts.
    await composer.hover();
    await expect(shell).not.toHaveAttribute("data-rail-peek", "true");

    // Click: it docks, and only now does the transcript reflow.
    await page.locator(".hdrv2-collapse").click();
    await expect(shell).toHaveAttribute("data-sidebar", "open");
    expect((await composer.boundingBox())!.x).toBeGreaterThan(xCollapsed);
  });

  test("Ctrl+B toggles the rail and the choice survives a reload", async ({ page }) => {
    const shell = page.locator(".app-shell.chatv2");

    // The shortcut is a document listener registered by an effect, so it only
    // exists once React has hydrated. The composer is server-rendered and is
    // therefore visible before that — collapse by click first, which both
    // proves the page is live and sets up the state the shortcut will undo.
    await expect(shell).toHaveAttribute("data-sidebar", "open");
    await page.locator(".rail-toggle-desktop").click();
    await expect(shell).toHaveAttribute("data-sidebar", "collapsed");

    await page.keyboard.press("Control+b");
    await expect(shell).toHaveAttribute("data-sidebar", "open");
    await page.keyboard.press("Control+b");
    await expect(shell).toHaveAttribute("data-sidebar", "collapsed");

    // The preference is restored after mount, never rendered on the server —
    // so this assertion is also the signal that the page has hydrated again.
    await page.reload();
    await expect(shell).toHaveAttribute("data-sidebar", "collapsed");

    await page.keyboard.press("Control+b");
    await expect(shell).toHaveAttribute("data-sidebar", "open");
  });
});
