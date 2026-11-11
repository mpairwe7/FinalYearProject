import { afterEach, beforeEach, describe, expect, it } from "vitest";
import nextConfig from "../../../next.config.mjs";

/** Who may put this application in a frame (ADR-0038, G548). */
async function headerValue(key: string): Promise<string | undefined> {
  const [{ headers }] = await nextConfig.headers!();
  return headers.find((h: { key: string }) => h.key === key)?.value as string | undefined;
}

async function frameAncestors(): Promise<string> {
  const csp = (await headerValue("Content-Security-Policy")) ?? "";
  return csp.split(";").map((part) => part.trim()).find((part) => part.startsWith("frame-ancestors")) ?? "";
}

describe("frame-ancestors", () => {
  // headers() reads FRAME_ANCESTORS when it runs, so each case sets its own.
  let saved: string | undefined;
  beforeEach(() => {
    saved = process.env.FRAME_ANCESTORS;
    delete process.env.FRAME_ANCESTORS;
  });
  afterEach(() => {
    if (saved === undefined) delete process.env.FRAME_ANCESTORS;
    else process.env.FRAME_ANCESTORS = saved;
  });

  it("names this app and the Hugging Face page that embeds it, and no other Space", async () => {
    // hf.space is on the Public Suffix List: every Space is another party's site.
    expect(await frameAncestors()).toBe("frame-ancestors 'self' https://huggingface.co");
    expect(await headerValue("X-Frame-Options")).toBeUndefined();
  });

  it("refuses every frame in a no-embed build", async () => {
    process.env.FRAME_ANCESTORS = "'none'";
    expect(await frameAncestors()).toBe("frame-ancestors 'none'");
    expect(await headerValue("X-Frame-Options")).toBe("DENY");
  });
});
