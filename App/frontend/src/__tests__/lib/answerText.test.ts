import { describe, expect, it } from "vitest";
import { cleanMarkdownForSpeech, stripCitationMarkers } from "../../lib/answerText";

describe("answerText", () => {
  it("strips citation markers cleanly", () => {
    expect(stripCitationMarkers("Standard VAT rate is 18% [1], per the Act [2, 3].")).toBe(
      "Standard VAT rate is 18%, per the Act.",
    );
  });

  it("normalizes URA acronyms for clear phonetic speech synthesis", () => {
    const raw = "Register on EFRIS with URA to file PAYE, VAT, and WHT.";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("E-F-R-I-S");
    expect(speech).toContain("U-R-A");
    expect(speech).toContain("P-A-Y-E");
    expect(speech).toContain("V-A-T");
    expect(speech).toContain("Withholding Tax");
  });

  it("formats standalone TIN, PRN, and NIN without reading them as common English words", () => {
    const raw = "Ensure you have a TIN and PRN, or present your NIN.";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("T-I-N");
    expect(speech).toContain("P-R-N");
    expect(speech).toContain("N-I-N");
  });

  it("spaces digits and paces toll-free numbers and steps", () => {
    const raw = "1. Call toll-free 0800 117 000.\n2. Provide TIN 1001234567."; // gitleaks:allow
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("Step 1: Call toll-free 0 800, 117, 0 0 0");
    expect(speech).toContain("Step 2: Provide T I N 1 0 0 1 2 3 4 5 6 7");
  });
});
