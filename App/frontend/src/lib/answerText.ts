/**
 * Answer-body text handling shared by everything that presents a reply.
 *
 * Assistant replies carry inline `[n]` citation markers. They belong in the
 * Sources block under the answer, not in the body, where a pill mid-sentence
 * reads as a stray number — "Core services include: 1" directly above a list.
 *
 * Three surfaces consume the same reply and all three need it clean: the
 * rendered message, the Copy button (a marker on the clipboard is noise in
 * whatever the reader pastes it into), and Listen, where a narrator otherwise
 * voices the marker in the middle of a sentence.
 */

/**
 * Strip inline `[n]` citation markers, closing the seam they leave behind.
 *
 * The whitespace matters as much as the marker: removing "[1]" from
 * "portal. [1]" naively leaves a trailing space, and from "the Act [1]
 * requires" leaves a double space.
 *
 * `[1](url)` is a link whose text happens to be a number, not a citation, so a
 * marker followed by "(" is left alone — stripping it would leave a bare
 * "(https://…)" behind.
 *
 * Grouped markers ("[1, 3]") are stripped too. A model told to cite "like [1]"
 * groups its references routinely, and the backend now expands those before
 * they reach here — but this is the last surface before a reader sees the
 * text, and a marker that slips through is visible as a literal "[1, 3]" in
 * the middle of a sentence.
 */
export function stripCitationMarkers(text: string): string {
  if (!text) return text;
  return text
    .replace(/\s*\[\d+(?:\s*[,;]\s*\d+)*\](?!\()/g, '') // the marker + leading space
    .replace(/[ \t]{2,}/g, ' ') //         seams left by the removal
    .replace(/[ \t]+([.,;:!?])/g, '$1') // space pushed onto punctuation
    .replace(/[ \t]+$/gm, ''); //          trailing space on a line
}
