/**
 * Pure client-side offline statutory tax calculators for Uganda (FY2026/27).
 *
 * Runs completely offline in the browser without network dependencies or LLM token guessing.
 * Matches versioned statutory formulas in `App/backend/app/tables.py`.
 */

export interface PayeResult {
  chargeableIncome: number;
  taxPayable: number;
  netPay: number;
  effectiveRate: number;
  marginalRate: number;
  breakdown: string;
}

export interface VatResult {
  baseAmount: number;
  vatAmount: number;
  totalAmount: number;
  rate: number;
  mode: "exclusive" | "inclusive";
}

export interface PresumptiveResult {
  annualTurnover: number;
  presumptiveTax: number;
  bracket: string;
}

/**
 * Calculates resident individual monthly PAYE under the Income Tax Act (FY2026/27).
 */
export function calculatePayeMonthly(monthlyGross: number): PayeResult {
  const income = Math.max(0, Number(monthlyGross) || 0);

  let tax = 0;
  let marginalRate = 0;
  let breakdown = "";

  if (income <= 335_000) {
    tax = 0;
    marginalRate = 0;
    breakdown = "0 - 335,000: Tax-free threshold (0%)";
  } else if (income <= 410_000) {
    const taxableInBracket = income - 335_000;
    tax = taxableInBracket * 0.1;
    marginalRate = 10;
    breakdown = `10% on excess over 335,000 (${formatUgx(taxableInBracket)})`;
  } else if (income <= 10_000_000) {
    const taxableInBracket = income - 410_000;
    tax = 7_500 + taxableInBracket * 0.2;
    marginalRate = 20;
    breakdown = `7,500 + 20% on excess over 410,000 (${formatUgx(taxableInBracket)})`;
  } else {
    const excessOver10m = income - 10_000_000;
    // Base bracket 30% + 10% additional surcharge on income exceeding 10m
    const baseTax = 1_925_500 + excessOver10m * 0.3;
    const surcharge = excessOver10m * 0.1;
    tax = baseTax + surcharge;
    marginalRate = 40;
    breakdown = `1,925,500 + 30% on excess over 10M + 10% high-earner surcharge (${formatUgx(excessOver10m)})`;
  }

  const roundedTax = Math.round(tax);
  const netPay = Math.round(income - roundedTax);
  const effectiveRate = income > 0 ? Number(((roundedTax / income) * 100).toFixed(2)) : 0;

  return {
    chargeableIncome: income,
    taxPayable: roundedTax,
    netPay,
    effectiveRate,
    marginalRate,
    breakdown,
  };
}

/**
 * Calculates VAT at the statutory standard rate of 18% under the VAT Act.
 */
export function calculateVat(amount: number, mode: "exclusive" | "inclusive" = "exclusive"): VatResult {
  const amt = Math.max(0, Number(amount) || 0);
  const rate = 0.18;

  if (mode === "inclusive") {
    // Amount already contains 18% VAT: VAT = amt * (18 / 118)
    const vatAmount = Math.round((amt * 18) / 118);
    const baseAmount = amt - vatAmount;
    return {
      baseAmount,
      vatAmount,
      totalAmount: amt,
      rate: 18,
      mode: "inclusive",
    };
  }

  // Exclusive: VAT is added on top of the net amount
  const vatAmount = Math.round(amt * rate);
  const totalAmount = amt + vatAmount;
  return {
    baseAmount: amt,
    vatAmount,
    totalAmount,
    rate: 18,
    mode: "exclusive",
  };
}

/**
 * Calculates small business presumptive tax on annual turnover (< UGX 150M).
 */
export function calculatePresumptiveTax(annualTurnover: number): PresumptiveResult {
  const turnover = Math.max(0, Number(annualTurnover) || 0);

  if (turnover <= 10_000_000) {
    return {
      annualTurnover: turnover,
      presumptiveTax: 0,
      bracket: "Below UGX 10,000,000: Nil",
    };
  } else if (turnover <= 30_000_000) {
    return {
      annualTurnover: turnover,
      presumptiveTax: 80_000,
      bracket: "UGX 10M to 30M: Fixed UGX 80,000",
    };
  } else if (turnover <= 50_000_000) {
    return {
      annualTurnover: turnover,
      presumptiveTax: 200_000,
      bracket: "UGX 30M to 50M: Fixed UGX 200,000",
    };
  } else if (turnover <= 80_000_000) {
    return {
      annualTurnover: turnover,
      presumptiveTax: 400_000,
      bracket: "UGX 50M to 80M: Fixed UGX 400,000",
    };
  } else if (turnover <= 150_000_000) {
    return {
      annualTurnover: turnover,
      presumptiveTax: 900_000,
      bracket: "UGX 80M to 150M: Fixed UGX 900,000",
    };
  }

  return {
    annualTurnover: turnover,
    presumptiveTax: Math.round(turnover * 0.3), // Standard corporate rate fallback
    bracket: "Exceeds UGX 150,000,000: Ineligible for presumptive tax; subject to standard income tax with books of account",
  };
}

/**
 * Utility to format numbers with commas and UGX prefix.
 */
export function formatUgx(amount: number): string {
  const val = Math.round(Number(amount) || 0);
  return `UGX ${val.toLocaleString("en-UG")}`;
}
