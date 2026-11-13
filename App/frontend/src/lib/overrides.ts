/**
 * The key an answer override is stored and matched on, as the server makes it
 * (`App/backend/app/cms.py:normalize_query`): case folded, punctuation dropped
 * except a `.` or `-` between digits, spacing collapsed (G637).
 *
 * The console shows it so an administrator sees what will match, and which
 * live answer a save would replace. `toLowerCase` stands in for Python's
 * `casefold`; they differ only on a few letters such as `ß`.
 */
const PUNCT = /(?!(?<=\d)[.-](?=\d))[^\p{L}\p{N}_\s]+/gu;

export function normalizeOverrideQuery(text: string): string {
  return String(text || '')
    .toLowerCase()
    .replace(PUNCT, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}
