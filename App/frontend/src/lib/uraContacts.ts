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

/** Words that must not be title-cased when a filename becomes a label. */
const SOURCE_ACRONYMS: Record<string, string> = {
  ura: 'URA',
  vat: 'VAT',
  paye: 'PAYE',
  tin: 'TIN',
  efris: 'EFRIS',
  dts: 'DTS',
  aeo: 'AEO',
  ngo: 'NGO',
  ngos: 'NGOs',
  wht: 'WHT',
  faq: 'FAQ',
  faqs: 'FAQs',
  fy: 'FY',
};

const SOURCE_MINOR_WORDS = new Set(['a', 'an', 'and', 'at', 'for', 'in', 'of', 'on', 'or', 'the', 'to']);

/**
 * Human label for a retrieved source.
 *
 * Citations carry the corpus filename — `ura_about_ura_faqs.csv` — and that is
 * what the Sources list was showing. A filename tells a taxpayer nothing about
 * whether to trust the answer, which is the only reason the list exists.
 *
 * Strips the `ura_` prefix, the `_faqs` suffix and the extension, restores
 * acronyms that title-casing would otherwise mangle (vat -> VAT, not Vat), and
 * leaves anything unrecognisable alone rather than inventing a name.
 */
export function sourceLabel(source?: string | null): string {
  const raw = (source ?? '').trim();
  if (!raw) return 'URA knowledge base';
  // Anything that is not a corpus filename (a URL, a document title) is
  // already meant to be read.
  if (!/\.(csv|jsonl|json|txt|md)$/i.test(raw)) return raw;

  const stem = raw
    .replace(/\.[^.]+$/, '')
    .replace(/^ura[_-]/i, '')
    .replace(/[_-]?faqs?$/i, '');
  // Financial years arrive as "fy2025_26"; joined they title-case to the
  // unreadable "Fy2025 26".
  // `\b` does not fire between `_` and `f` — both are word characters — so the
  // separator has to be matched explicitly.
  const withYears = stem.replace(/(^|[_\-\s])fy(\d{4})[_-](\d{2})(?=$|[_\-\s])/gi, '$1FY$2/$3');
  const words = withYears.split(/[_\-\s]+/).filter(Boolean);
  if (!words.length) return 'URA knowledge base';

  const label = words
    .map((w, i) => {
      const lower = w.toLowerCase();
      if (SOURCE_ACRONYMS[lower]) return SOURCE_ACRONYMS[lower];
      // Casing already applied above (FY2025/26) must survive title-casing.
      if (/[A-Z]/.test(w)) return w;
      // Year ranges like 2025_26 read better joined.
      if (/^\d+$/.test(w)) return w;
      if (i > 0 && SOURCE_MINOR_WORDS.has(lower)) return lower;
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(' ')
    .replace(/\b(FY)\s+(\d{4})\s+(\d{2})\b/, '$1 $2/$3');
  return label || 'URA knowledge base';
}
