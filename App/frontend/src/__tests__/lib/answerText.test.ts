import { describe, expect, it } from "vitest";
import { cleanMarkdownForSpeech, formatCallCaptionText, stripCitationMarkers } from "../../lib/answerText";

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

  it("keeps standalone TIN/tin and NIN/nin as natural words (never T-I-N or N-I-N) and spells PRN as P R N", () => {
    const raw = "Ensure you have a TIN (or T.I.N. / T-I-N) and PRN, or present your NIN (or N.I.N. / N-I-N).";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("TIN");
    expect(speech).not.toContain("T-I-N");
    expect(speech).toContain("P R N");
    expect(speech).not.toContain("P-R-N");
    expect(speech).toContain("NIN");
    expect(speech).not.toContain("N-I-N");
  });

  it("spaces digits and paces toll-free numbers and steps", () => {
    const raw = "1. Call toll-free 0800 117 000.\n2. Provide TIN 1001234567 or NIN CM930039182LIR."; // gitleaks:allow
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("Step 1: Call toll-free 0 800, 117, 0 0 0");
    expect(speech).toContain("Step 2: Provide TIN 1 0 0 1 2 3 4 5 6 7 or NIN C M 9 3 0 0 3 9 1 8 2 L I R");
  });

  it("preserves redaction placeholders for backend locale normalization while pacing standalone TINs", () => {
    const raw = "Record for [REDACTED_UG_NID], [REDACTED_UG_TIN], 1000123456, and UGX  1000123456.";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("[REDACTED_UG_NID]");
    expect(speech).toContain("[REDACTED_UG_TIN]");
    expect(speech).toContain("1 0 0 0 1 2 3 4 5 6");
    expect(speech).toContain("UGX 1000123456");
  });

  it("normalizes Ugandan .go.ug, .co.ug, and .or.ug government domains for speech", () => {
    const raw =
      "Visit kcca.go.ug, obrs.ursb.go.ug, unbs.go.ug, nema.go.ug, udls.co.ug, or nda.or.ug. Ignore example.go.ug.com.";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("K C C A dot go dot u g");
    expect(speech).toContain("O B R S dot U R S B dot go dot u g");
    expect(speech).toContain("U N B S dot go dot u g");
    expect(speech).toContain("Nema dot go dot u g");
    expect(speech).toContain("U D L S dot co dot u g");
    expect(speech).toContain("N D A dot or dot u g.");
    expect(speech).toContain("example.go.ug.com");
    expect(speech).not.toContain("example dot go dot u g.com");
  });

  it("normalizes USSD shortcodes, +256 / 07.. phones, P.O. Box, emails, and https://portal.ura.go.ug URLs", () => {
    const raw =
      "Call +256 (0)323442055 or +256 (414) 258 641 or (0414) 258 641 or 0772123456 or 0800-117-000 or 999. Dial *165# on MTN or *185#. Email tax_help-24@ura.go.ug or customs+clearance@ura.go.ug or cloaketech@gmail.com. Open https://efris.ura.go.ug:8443/login or https://ura.go.ug/en/download/?wpdmdl=20305 or comesa.int. Mail P.O. Box 7279.";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("plus 2 5 6, 0, 3 2 3, 4 4 2, 0 5 5");
    expect(speech).toContain("plus 2 5 6, 4 1 4, 2 5 8, 6 4 1");
    expect(speech).toContain("0 4 1 4, 2 5 8, 6 4 1");
    expect(speech).toContain("0 7 7 2, 1 2 3, 4 5 6");
    expect(speech).toContain("0 800, 117, 0 0 0");
    expect(speech).toContain("9 9 9");
    expect(speech).toContain("star 1 6 5 hash on M T N or star 1 8 5 hash");
    expect(speech).toContain("tax underscore help dash 2 4 at U R A dot go dot u g");
    expect(speech).toContain("customs plus clearance at U R A dot go dot u g");
    expect(speech).toContain("cloaketech at gmail dot com");
    expect(speech).toContain("Efris dot U R A dot go dot u g");
    expect(speech).toContain("Comesa dot int");
    expect(speech).not.toContain("wpdmdl");
    expect(speech).not.toContain("8443");
    expect(speech).toContain("P O Box 7 2 7 9");
  });

  it("reads pipe table rows as spoken sentences while stripping separator rows", () => {
    const raw = "| Band | Rate |\n| --- | --- |\n| Standard | 18% |";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).not.toContain("---");
    expect(speech).toContain("Band: Rate.");
    expect(speech).toContain("Standard: 18%.");
  });

  it("formats inline call steps and trailing offer prompts onto their own lines", () => {
    const raw =
      "To register: Step 1: Visit the portal. Step 2: Submit the form. Would you like more detail?";
    const formatted = formatCallCaptionText(raw);
    expect(formatted).toBe(
      "To register:\nStep 1: Visit the portal.\nStep 2: Submit the form.\n\nWould you like more detail?",
    );
    expect(formatCallCaptionText("VAT is 18%.")).toBe("VAT is 18%.");
  });

  it("punctuates multi-line bullets and headings for speech and does not turn Sec. 12. into Step 12", () => {
    const raw = "### Required Documents\n- National ID\n- Active phone\nUnder Sec. 12. Every taxpayer must file.";
    const speech = cleanMarkdownForSpeech(raw);
    expect(speech).toContain("Required Documents.");
    expect(speech).toContain("National ID.");
    expect(speech).toContain("Active phone.");
    expect(speech).toContain("Section 12. Every taxpayer must file.");
    expect(speech).not.toContain("Step 12");
  });

  it("restores URA/PAYE/EFRIS acronyms and formats multi-item labelled call captions as bullets", () => {
    const raw =
      "U R A provides: Taxpayer Registration: Instant TIN issuance. Domestic Tax Administration: Managing VAT and Payee. E-Invoicing: Efris receipts.";
    const formatted = formatCallCaptionText(raw);
    expect(formatted).toContain("URA provides:\n• Taxpayer Registration:");
    expect(formatted).toContain("\n• Domestic Tax Administration: Managing VAT and PAYE.");
    expect(formatted).toContain("\n• E-Invoicing: EFRIS receipts.");
  });
});
