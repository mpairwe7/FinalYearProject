import { describe, expect, it } from "vitest";
import nextConfig from "../../../next.config.mjs";

/** Who may put this application in a frame (ADR-0038, G548). */
describe("frame-ancestors", () => {
  it("names this app and the Hugging Face page that embeds it, and no other Space", async () => {
    const [{ headers }] = await nextConfig.headers!();
    const csp = headers.find((h: { key: string }) => h.key === "Content-Security-Policy")!.value as string;
    const frameAncestors = csp.split(";").find((part) => part.trim().startsWith("frame-ancestors"))!.trim();

    // hf.space is on the Public Suffix List: every Space is another party's site.
    expect(frameAncestors).toBe("frame-ancestors 'self' https://huggingface.co");
  });
});
