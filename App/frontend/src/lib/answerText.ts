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
 */
export function stripCitationMarkers(text: string): string {
  if (!text) return text;
  return text
    .replace(/\s*\[\d+\](?!\()/g, '') //   the marker, and the space before it
    .replace(/[ \t]{2,}/g, ' ') //         seams left by the removal
    .replace(/[ \t]+([.,;:!?])/g, '$1') // space pushed onto punctuation
    .replace(/[ \t]+$/gm, ''); //          trailing space on a line
}
