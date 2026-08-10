/**
 * Playwright E2E configuration.
 *
 * Projects:
 *   - chromium: functional E2E smoke test (chat flow, SSE streaming)
 *   - a11y: axe-core accessibility audit (WCAG 2.1 AA) via @axe-core/playwright
 *
 * Aligned with: ISO/IEC 25010:2023 §4 (Interaction Capability), WCAG 2.1 AA,
 * and week08 NFR-05 (WCAG 2.1 AA, Lighthouse >= 90).
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html"]] : [["html"]],
  timeout: 30_000,

  use: {
    baseURL: process.env.BASE_URL || "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // Grant the microphone up-front so getUserMedia() in the voice flow never
    // shows a permission prompt (which Playwright can't click through).
    permissions: ["microphone"],
    // Chromium sandbox requires specific kernel namespaces; disable in
    // environments where they are unavailable (Docker, shared servers).
    // CI runners (GitHub Actions) provide a compatible environment.
    launchOptions: {
      args: [
        // Voice E2E: auto-accept the mic prompt, feed a synthetic capture device,
        // and let TTS playback start without a user gesture. These are safe for
        // the non-voice specs too (no real device is touched).
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        "--autoplay-policy=no-user-gesture-required",
        ...(process.env.CI
          ? []
          : [
              "--no-sandbox",
              "--disable-setuid-sandbox",
              "--disable-gpu",
              "--disable-software-rasterizer",
              "--disable-dev-shm-usage",
              // Some locked-down containers can't spawn Chromium's zygote/renderer
              // even with --no-sandbox; opt into single-process there via env.
              ...(process.env.PW_SINGLE_PROCESS ? ["--single-process", "--no-zygote"] : []),
            ]),
      ],
    },
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 7"] },
    },
    {
      name: "a11y",
      use: { ...devices["Desktop Chrome"] },
      testMatch: /a11y\.spec\.ts$/,
    },
  ],

  // Start the Next.js dev server before E2E runs (local only — CI builds first)
  webServer: process.env.CI
    ? undefined
    : {
        command: "bun run dev",
        port: 3000,
        reuseExistingServer: true,
        timeout: 60_000,
      },
});
