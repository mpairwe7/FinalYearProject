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

/**
 * URA and Ugandan tax term phonetic normalization map.
 * Ensures acronyms are sounded as individual letters or clear full words,
 * rather than slurred words (e.g. "TIN" sounded as T-I-N not tin can).
 */
const URA_SPEECH_TERMS: Array<[RegExp, string | ((match: string, ...args: string[]) => string)]> = [
  [/\bTIN(?::|\s+is|\s+number|\s+no\.?)?\s*([1-9]\d{8,9})\b/gi, (_: string, num: string) => `T I N ${num.split('').join(' ')}`],
  [/\bPRN(?::|\s+is|\s+number|\s+no\.?)?\s*(\d{10,14})\b/gi, (_: string, num: string) => `P R N ${num.split('').join(' ')}`],
  [/\b0800\s*([12]17)\s*000\b/g, '0 800, $1, 0 0 0'],
  [/\b0800\s*(\d{3})\s*(\d{3})\b/g, '0 800, $1, $2'],
  [/\bEFRIS\b/g, 'E-F-R-I-S'],
  [/\bURA\b/g, 'U-R-A'],
  [/\bPAYE\b/g, 'P-A-Y-E'],
  [/\bWHT\b/g, 'Withholding Tax'],
  [/\bVAT\b/g, 'V-A-T'],
  [/\bCIT\b/g, 'C-I-T'],
  [/\bPIT\b/g, 'P-I-T'],
  [/\bLED\b/g, 'Local Excise Duty'],
  [/\bDTS\b/g, 'Digital Tax Stamps'],
  [/\bNSSF\b/g, 'N-S-S-F'],
  [/\bLST\b/g, 'Local Service Tax'],
  [/\bURSB\b/g, 'U-R-S-B'],
  [/\bBOU\b/g, 'Bank of Uganda'],
  [/\bTAT\b/g, 'Tax Appeals Tribunal'],
  [/\bPRN\b/g, 'P-R-N'],
  [/\bTIN\b/g, 'T-I-N'],
  [/\bNIN\b/g, 'N-I-N'],
  [/\bBRN\b/g, 'B-R-N'],
  [/\bDT-(\d{3,4})\b/g, 'D-T $1'],
];

/**
 * Pre-process text for TTS narration: strips citations, code blocks, links,
 * markdown formatting (headings, bold, lists, tables), expands URA tax acronyms
 * for proper phonetic sounding, and paces numbers/steps.
 */
export function cleanMarkdownForSpeech(text: string): string {
  if (!text) return '';
  let cleaned = stripCitationMarkers(text);
  // Strip links [Title](url) -> Title
  cleaned = cleaned.replace(/\[([^\]]+)\]\([^\)]+\)/g, '$1');
  // Strip code blocks
  cleaned = cleaned.replace(/```[\s\S]*?```/g, ' ');
  cleaned = cleaned.replace(/`([^`]+)`/g, '$1');
  // Strip table rows
  cleaned = cleaned.replace(/^\s*\|.*\|\s*$/gm, ' ');
  cleaned = cleaned.replace(/\s*\|\s*/g, ', ');
  // Paced numbered steps: 1. -> Step 1:
  cleaned = cleaned.replace(/^\s*(\d+)\.\s+/gm, 'Step $1: ');
  // Strip headers and list bullets
  cleaned = cleaned.replace(/^\s*#{1,6}\s+/gm, '');
  cleaned = cleaned.replace(/^\s*[-*+•]\s+/gm, ', ');
  // Strip bold and italics
  cleaned = cleaned.replace(/\*\*([^*]+)\*\*/g, '$1');
  cleaned = cleaned.replace(/\*([^*]+)\*/g, '$1');
  cleaned = cleaned.replace(/__([^_]+)__/g, '$1');
  cleaned = cleaned.replace(/_([^_]+)_/g, '$1');

  // URA terms and proper sounding
  for (const [pattern, replacement] of URA_SPEECH_TERMS) {
    if (typeof replacement === 'string') {
      cleaned = cleaned.replace(pattern, replacement);
    } else {
      cleaned = cleaned.replace(pattern, replacement as (substring: string, ...args: string[]) => string);
    }
  }

  // Collapse whitespace and commas
  cleaned = cleaned.replace(/[,;]\s*[,;]+/g, ',');
  cleaned = cleaned.replace(/[ \t]+/g, ' ');
  cleaned = cleaned.replace(/\n\s*\n+/g, '\n\n');
  return cleaned.trim();
}
