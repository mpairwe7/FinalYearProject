import { describe, expect, it } from "vitest";
import { PHONE_RE, sourceUrl, telDigits, URA_CONTACTS } from "../../lib/uraContacts";

describe("uraContacts", () => {
  it("maps any non-empty source to the official portal, null otherwise", () => {
    expect(sourceUrl("URA Income Tax Act 2023")).toBe("https://ura.go.ug");
    expect(sourceUrl("")).toBeNull();
    expect(sourceUrl(undefined)).toBeNull();
    expect(sourceUrl("   ")).toBeNull();
  });

  it("matches Ugandan phone formats but not tax figures or TINs", () => {
    const grab = (s: string) => s.match(PHONE_RE) ?? [];
    expect(grab("Call 0800 117 000 or 0772 140 000")).toEqual(["0800 117 000", "0772 140 000"]);
    expect(grab("Poison line +256-414-270-975")).toEqual(["+256-414-270-975"]);
    expect(grab("Pay UGX 1,000,000 before 2025")).toEqual([]);
    expect(grab("TIN 1014567890")).toEqual([]);
  });

  it("reduces a display number to a dialable value", () => {
    expect(telDigits("0800 117 000")).toBe("0800117000");
    expect(telDigits("+256-414-270-975")).toBe("+256414270975");
  });

  it("exposes verified contact constants", () => {
    expect(URA_CONTACTS.tollFree).toContain("0800 117 000");
    expect(URA_CONTACTS.website).toBe("https://ura.go.ug");
  });
});
