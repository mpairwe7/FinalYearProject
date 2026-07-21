/**
 * Official URA (Uganda Revenue Authority) contact constants and helpers.
 *
 * Values mirror the authoritative figures used by the backend service layer
 * (see backend/app/service.py) so the frontend never invents contact details:
 *   - Toll-free contact centre: 0800 117 000 / 0800 217 000
 *   - WhatsApp: 0772 140 000
 *   - Portal: https://ura.go.ug
 *
 * Reused by the Markdown renderer (phone auto-linking) and ChatMessage
 * (escalation contact line + clickable source citations).
 */

export const URA_CONTACTS = {
  /** Toll-free contact-centre lines. */
  tollFree: ["0800 117 000", "0800 217 000"] as const,
  /** WhatsApp self-service line. */
  whatsapp: "0772 140 000",
  /** Official taxpayer portal. */
  website: "https://ura.go.ug",
} as const;

/**
 * Phone-number sub-pattern (source string, no flags) for Ugandan formats:
 *   0800 117 000 · 0772 140 000 · +256 772 140000 · +256-414-270-975
 * Requires a 0xxx or +256 prefix grouped into triplets, which keeps tax
 * figures (amounts, years, TINs) from being mistaken for phone numbers.
 */
export const PHONE_SRC = String.raw`(?:\+256[\s-]?\d{2,3}|0\d{2,3})[\s-]?\d{3}[\s-]?\d{3,4}`;

/**
 * Standalone phone matcher (for tests and reuse). Uses digit lookarounds
 * rather than `\b` so a leading `+` (preceded by a space) still matches.
 */
export const PHONE_RE = new RegExp(String.raw`(?<!\d)(?:${PHONE_SRC})(?!\d)`, "g");

/** Reduce a displayed number to a dialable `tel:` value, keeping a leading `+`. */
export function telDigits(display: string): string {
  return display.replace(/[^\d+]/g, "");
}

/**
 * Map a citation's source to an authoritative URL.
 *
 * Backend citations reference internal URA knowledge-base documents (no URL
 * field), and every indexed document is an official URA publication available
 * on the taxpayer portal — so a recognised source links to ura.go.ug.
 * Returns null for empty sources so the caller renders plain text (graceful).
 */
export function sourceUrl(source?: string | null): string | null {
  if (!source || !source.trim()) return null;
  return URA_CONTACTS.website;
}
