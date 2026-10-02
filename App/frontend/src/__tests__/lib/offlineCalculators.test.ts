import { describe, it, expect } from "vitest";
import {
  calculatePayeMonthly,
  calculateVat,
  calculatePresumptiveTax,
  formatUgx,
} from "../../lib/offlineCalculators";

describe("offlineCalculators", () => {
  describe("calculatePayeMonthly (FY2026/27)", () => {
    it("returns 0 tax for monthly income at or below threshold (UGX 335,000)", () => {
      const res = calculatePayeMonthly(335_000);
      expect(res.taxPayable).toBe(0);
      expect(res.netPay).toBe(335_000);
      expect(res.effectiveRate).toBe(0);
      expect(res.marginalRate).toBe(0);
    });

    it("calculates 10% on excess over 335k up to 410k", () => {
      // 400,000 income -> excess is 65,000 -> 10% = 6,500
      const res = calculatePayeMonthly(400_000);
      expect(res.taxPayable).toBe(6_500);
      expect(res.netPay).toBe(393_500);
      expect(res.marginalRate).toBe(10);
    });

    it("calculates 20% on excess over 410k up to 10M", () => {
      // 1,000,000 income:
      // Bracket 1: 0 - 335,000 @ 0% = 0
      // Bracket 2: 335,000 - 410,000 (75,000 @ 10%) = 7,500
      // Bracket 3: 410,000 - 1,000,000 (590,000 @ 20%) = 118,000
      // Total tax = 7,500 + 118,000 = 125,500
      const res = calculatePayeMonthly(1_000_000);
      expect(res.taxPayable).toBe(125_500);
      expect(res.netPay).toBe(874_500);
      expect(res.marginalRate).toBe(20);
      expect(res.effectiveRate).toBe(12.55);
    });

    it("calculates 30% + 10% surcharge on income exceeding 10M", () => {
      // 15,000,000 income:
      // Base tax up to 10M = 1,925,500
      // Excess = 5,000,000
      // 30% on excess = 1,500,000
      // 10% surcharge on excess = 500,000
      // Total tax = 1,925,500 + 1,500,000 + 500,000 = 3,925,500
      const res = calculatePayeMonthly(15_000_000);
      expect(res.taxPayable).toBe(3_925_500);
      expect(res.netPay).toBe(11_074_500);
      expect(res.marginalRate).toBe(40);
    });

    it("handles zero and negative income safely", () => {
      const res = calculatePayeMonthly(-50_000);
      expect(res.taxPayable).toBe(0);
      expect(res.netPay).toBe(0);
    });
  });

  describe("calculateVat", () => {
    it("calculates 18% VAT in exclusive mode", () => {
      const res = calculateVat(100_000, "exclusive");
      expect(res.vatAmount).toBe(18_000);
      expect(res.totalAmount).toBe(118_000);
      expect(res.rate).toBe(18);
    });

    it("extracts 18% VAT in inclusive mode", () => {
      const res = calculateVat(118_000, "inclusive");
      expect(res.vatAmount).toBe(18_000);
      expect(res.baseAmount).toBe(100_000);
      expect(res.totalAmount).toBe(118_000);
    });
  });

  describe("calculatePresumptiveTax", () => {
    it("returns Nil for turnover under 10M", () => {
      const res = calculatePresumptiveTax(8_000_000);
      expect(res.presumptiveTax).toBe(0);
    });

    it("returns UGX 80,000 for 10M to 30M", () => {
      const res = calculatePresumptiveTax(25_000_000);
      expect(res.presumptiveTax).toBe(80_000);
    });

    it("returns UGX 200,000 for 30M to 50M", () => {
      const res = calculatePresumptiveTax(45_000_000);
      expect(res.presumptiveTax).toBe(200_000);
    });

    it("returns UGX 900,000 for 80M to 150M", () => {
      const res = calculatePresumptiveTax(120_000_000);
      expect(res.presumptiveTax).toBe(900_000);
    });
  });

  describe("formatUgx", () => {
    it("formats amounts cleanly", () => {
      expect(formatUgx(150000000)).toContain("150,000,000");
    });
  });
});
