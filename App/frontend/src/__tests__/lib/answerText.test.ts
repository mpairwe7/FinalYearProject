import { describe, expect, it } from "vitest";
import { cleanMarkdownForSpeech, stripCitationMarkers } from "../../lib/answerText";

describe("answerText", () => {
  it("strips citation markers cleanly", () => {
    expect(stripCitationMarkers("Standard VAT rate is 18% [1], per the Act [2, 3].")).toBe(
      "Standard VAT rate is 18%, per the Act.",
    );
  });

  it("normalizes URA and government/MDA acronyms for clear phonetic speech synthesis", () => {
    const raw =
      "Register on EFRIS with URA to file PAYE, VAT, and WHT, or resolve KCCA, URSB, NEMA, UNBS, NIRA, DCIC, UDLS, MLHUD, UPF, PPDA, IFMS, OBRS, and UESW holds.";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("Efris");
    expect(speech).toContain("U R A");
    expect(speech).toContain("Payee");
    expect(speech).toContain("VAT");
    expect(speech).toContain("Withholding Tax");
    expect(speech).toContain("K C C A");
    expect(speech).toContain("U R S B");
    expect(speech).toContain("Nema");
    expect(speech).toContain("U N B S");
    expect(speech).toContain("Nira");
    expect(speech).toContain("D C I C");
    expect(speech).toContain("U D L S");
    expect(speech).toContain("M L H U D");
    expect(speech).toContain("U P F");
    expect(speech).toContain("P P D A");
    expect(speech).toContain("I F M S");
    expect(speech).toContain("O B R S");
    expect(speech).toContain("U E S W");
  });

  it("handles lowercase government acronyms (kcca, ursb, nema, unbs, tin, nin)", () => {
    const raw = "Check kcca, ursb, nema, and unbs with your tin and nin.";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("K C C A");
    expect(speech).toContain("U R S B");
    expect(speech).toContain("Nema");
    expect(speech).toContain("U N B S");
    expect(speech).toContain("tin");
    expect(speech).toContain("nin");
    expect(speech).not.toContain("T-I-N");
    expect(speech).not.toContain("N-I-N");
  });

  it("keeps standalone TIN/tin and NIN/nin as natural words (never T-I-N or N-I-N) and spells PRN as P-R-N", () => {
    const raw = "Ensure you have a TIN (or T.I.N. / T-I-N) and PRN, or present your NIN (or N.I.N. / N-I-N).";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("TIN");
    expect(speech).not.toContain("T-I-N");
    expect(speech).toContain("P-R-N");
    expect(speech).toContain("NIN");
    expect(speech).not.toContain("N-I-N");
  });

  it("spaces digits and paces toll-free numbers and steps", () => {
    const raw = "1. Call toll-free 0800 117 000.\n2. Provide TIN 1001234567."; // gitleaks:allow
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("Step 1: Call toll-free 0 800, 117, 0 0 0");
    expect(speech).toContain("Step 2: Provide TIN 1 0 0 1 2 3 4 5 6 7");
  });
});
