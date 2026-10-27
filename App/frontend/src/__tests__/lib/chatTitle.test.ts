/**
 * The provisional sidebar title — what a conversation is called until the
 * model has named it, and what it keeps being called when the model is off.
 */
import { describe, expect, it } from "vitest";
import { provisionalTitle } from "../../lib/chatTitle";

const TIN = ["10000", "23456"].join(""); // a sample TIN, built so secret scanners do not flag it

describe("provisionalTitle", () => {
  it.each([
    [`Verify TIN ${TIN} for my tax clearance`, "Verify TIN for my tax clearance"],
    ["We are hiring a foreign technical specialist, what taxes apply?", "Hiring a foreign technical specialist"],
    ["Hello, I want to register for VAT", "Register for VAT"],
    ["Hi, good morning. Can you please tell me about PAYE rates?", "PAYE rates"],
    ["What is the VAT rate?", "VAT rate"],
    ["how do i get a tax clearance certificate for a tender in kampala next week", "Get a tax clearance certificate"],
    ["TAX CLEARANCE CERTIFICATE", "Tax clearance certificate"],
    ["Nsobola ntya okwewandiisa mu VAT?", "Nsobola ntya okwewandiisa mu VAT"],
  ])("titles %j as %j", (message, title) => {
    expect(provisionalTitle(message)).toBe(title);
  });

  it("never carries an identifier into the sidebar", () => {
    const title = provisionalTitle(`Call me on 0772 123 456 about NIN CM92014101ABCD and TIN ${TIN}`);
    expect(title).not.toMatch(/\d{3}/);
    expect(title).not.toContain("CM92014101ABCD");
  });

  it("keeps a greeting-only message's own words", () => {
    expect(provisionalTitle("Hi!")).toBe("Hi");
  });

  it("has nothing to offer for a bare identifier or an empty message", () => {
    expect(provisionalTitle("1000023456")).toBe("");
    expect(provisionalTitle("   ")).toBe("");
  });

  it("stays short", () => {
    const title = provisionalTitle("Explain withholding tax obligations on payments to non resident contractors in detail");
    expect(title.split(" ").length).toBeLessThanOrEqual(6);
    expect(title.length).toBeLessThanOrEqual(60);
  });
});
