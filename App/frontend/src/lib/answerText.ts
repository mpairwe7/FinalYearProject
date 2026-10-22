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
 * URA and Ugandan tax/government term phonetic normalization map.
 * Ensures letter-by-letter initialisms (e.g. URA -> U R A, KCCA -> K C C A,
 * URSB -> U R S B, UNBS -> U N B S) are spaced out while pronounceable
 * acronyms (e.g. TIN, NIN, VAT, Efris, Payee, Nema, Nira, Iras) are spoken
 * naturally as words.
 */
const URA_SPEECH_TERMS: Array<[RegExp, string | ((match: string, ...args: string[]) => string)]> = [
  [/\b(?:T-?I-?N|T\.I\.N\.)(?::|\s+is|\s+number|\s+no\.?)?\s*([1-9]\d{8,9})\b/gi, (_: string, num: string) => `TIN ${num.split('').join(' ')}`],
  [/\b(?:P-?R-?N|P\.R\.N\.)(?::|\s+is|\s+number|\s+no\.?)?\s*(\d{10,14})\b/gi, (_: string, num: string) => `P R N ${num.split('').join(' ')}`],
  [/\bNIN(?::|\s+is|\s+number|\s+no\.?)?\s*([CF][MF][0-9A-Za-z]{12})\b/gi, (_: string, nin: string) => `NIN ${nin.toUpperCase().split('').join(' ')}`],
  [/\b0800\s*([12]17)\s*000\b/g, '0 800, $1, 0 0 0'],
  [/\b0800\s*(\d{3})\s*(\d{3})\b/g, '0 800, $1, $2'],
  [/\bE-?F-?R-?I-?S\b/gi, 'Efris'],
  [/\bU\.R\.A\.?|\bU-R-A\b|\bURA\b/gi, 'U R A'],
  [/\bP\.A\.Y\.E\.?|\bP-A-Y-E\b|\bPAYE\b/gi, 'Payee'],
  [/\bWHT\b|\bW\.H\.T\.?\b/gi, 'Withholding Tax'],
  [/\bT\.I\.N\.?|\bT-I-N\b/gi, 'TIN'],
  [/\bN\.I\.N\.?|\bN-I-N\b/gi, 'NIN'],
  [/\bV\.A\.T\.?|\bV-A-T\b/gi, 'VAT'],
  [/\bCIT\b|\bC\.I\.T\.?|\bC-I-T\b/gi, 'C-I-T'],
  [/\bPIT\b|\bP\.I\.T\.?|\bP-I-T\b/g, 'P-I-T'],
  [/\bLED\b|\bL\.E\.D\.?\b/g, 'Local Excise Duty'],
  [/\bDTS\b|\bD\.T\.S\.?\b/gi, 'Digital Tax Stamps'],
  [/\bNSSF\b|\bN\.S\.S\.F\.?|\bN-S-S-F\b/gi, 'N-S-S-F'],
  [/\bLST\b|\bL\.S\.T\.?\b/gi, 'Local Service Tax'],
  [/\bURSB\b|\bU\.R\.S\.B\.?|\bU-R-S-B\b/gi, 'U R S B'],
  [/\bKCCA\b|\bK\.C\.C\.A\.?|\bK-C-C-A\b/gi, 'K C C A'],
  [/\bUNBS\b|\bU\.N\.B\.S\.?|\bU-N-B-S\b/gi, 'U N B S'],
  [/\bNEMA\b|\bN\.E\.M\.A\.?|\bN-E-M-A\b/gi, 'Nema'],
  [/\bNIRA\b|\bN\.I\.R\.A\.?|\bN-I-R-A\b/gi, 'Nira'],
  [/\bIRAS\b|\bI\.R\.A\.S\.?|\bI-R-A-S\b/gi, 'Iras'],
  [/\bBWIMS\b|\bB\.W\.I\.M\.S\.?|\bB-W-I-M-S\b/gi, 'Bwims'],
  [/\bCOMESA\b|\bC\.O\.M\.E\.S\.A\.?|\bC-O-M-E-S-A\b/gi, 'Comesa'],
  [/\bULII\b|\bU\.L\.I\.I\.?|\bU-L-I-I\b/gi, 'Ulii'],
  [/\bIRIMS\b|\bI\.R\.I\.M\.S\.?|\bI-R-I-M-S\b/gi, 'Irims'],
  [/\bNITA-U\b/gi, 'Nita U'],
  [/\bNITA\b|\bN\.I\.T\.A\.?|\bN-I-T-A\b/gi, 'Nita'],
  [/\bDCIC\b|\bD\.C\.I\.C\.?|\bD-C-I-C\b/gi, 'D C I C'],
  [/\bUDLS\b|\bU\.D\.L\.S\.?|\bU-D-L-S\b/gi, 'U D L S'],
  [/\bMLHUD\b|\bM\.L\.H\.U\.D\.?|\bM-L-H-U-D\b/gi, 'M L H U D'],
  [/\bUgNLIS\b|\bU\.G\.N\.L\.I\.S\.?|\bU-G-N-L-I-S\b/gi, 'U G N L I S'],
  [/\bUPF\b|\bU\.P\.F\.?|\bU-P-F\b/gi, 'U P F'],
  [/\bEPS\b|\bE\.P\.S\.?|\bE-P-S\b/gi, 'E P S'],
  [/\bNDAMIS\b|\bN\.D\.A\.M\.I\.S\.?|\bN-D-A-M-I-S\b/gi, 'N D A M I S'],
  [/\bNDA\b|\bN\.D\.A\.?|\bN-D-A\b/gi, 'N D A'],
  [/\bPPDA\b|\bP\.P\.D\.A\.?|\bP-P-D-A\b/gi, 'P P D A'],
  [/\bIFMS\b|\bI\.F\.M\.S\.?|\bI-F-M-S\b/gi, 'I F M S'],
  [/\bOBRS\b|\bO\.B\.R\.S\.?|\bO-B-R-S\b/gi, 'O B R S'],
  [/\bUESW\b|\bU\.E\.S\.W\.?|\bU-E-S-W\b/gi, 'U E S W'],
  [/\bONTR\b|\bO\.N\.T\.R\.?|\bO-N-T-R\b/gi, 'O N T R'],
  [/\bNTR\b|\bN\.T\.R\.?|\bN-T-R\b/gi, 'N T R'],
  [/\bMoWT\b|\bM\.O\.W\.T\.?|\bM-O-W-T\b/gi, 'M O W T'],
  [/\bMoFPED\b|\bM\.O\.F\.P\.E\.D\.?|\bM-O-F-P-E-D\b/gi, 'M O F P E D'],
  [/\bMoLG\b|\bM\.O\.L\.G\.?|\bM-O-L-G\b/gi, 'M O L G'],
  [/\bMoIA\b|\bM\.O\.I\.A\.?|\bM-O-I-A\b/gi, 'M O I A'],
  [/\bMAAIF\b|\bM\.A\.A\.I\.F\.?|\bM-A-A-I-F\b/gi, 'M A A I F'],
  [/\bUCDA\b|\bU\.C\.D\.A\.?|\bU-C-D-A\b/gi, 'U C D A'],
  [/\bUIA\b|\bU\.I\.A\.?|\bU-I-A\b/gi, 'U I A'],
  [/\bUFZA\b|\bU\.F\.Z\.A\.?|\bU-F-Z-A\b/gi, 'U F Z A'],
  [/\bUCC\b|\bU\.C\.C\.?|\bU-C-C\b/gi, 'U C C'],
  [/\bNPA\b|\bN\.P\.A\.?|\bN-P-A\b/gi, 'N P A'],
  [/\bNWSC\b|\bN\.W\.S\.C\.?|\bN-W-S-C\b/gi, 'N W S C'],
  [/\bUNRA\b|\bU\.N\.R\.A\.?|\bU-N-R-A\b/gi, 'U N R A'],
  [/\bPVoC\b|\bP\.V\.O\.C\.?|\bP-V-O-C\b/gi, 'P V O C'],
  [/\bCoC\b|\bC\.O\.C\.?|\bC-O-C\b/gi, 'C O C'],
  [/\bCGV\b|\bC\.G\.V\.?|\bC-G-V\b/gi, 'C G V'],
  [/\bMZO\b|\bM\.Z\.O\.?|\bM-Z-O\b/gi, 'M Z O'],
  [/\bEIA\b|\bE\.I\.A\.?|\bE-I-A\b/gi, 'E I A'],
  [/\bFDN\b|\bF\.D\.N\.?|\bF-D-N\b/gi, 'F D N'],
  [/\bEFD\b|\bE\.F\.D\.?|\bE-F-D\b/gi, 'E F D'],
  [/\bEDC\b|\bE\.D\.C\.?|\bE-D-C\b/gi, 'E D C'],
  [/\bOTP\b|\bO\.T\.P\.?|\bO-T-P\b/gi, 'O T P'],
  [/\bEAC\b|\bE\.A\.C\.?|\bE-A-C\b/gi, 'E A C'],
  [/\bCGT\b|\bC\.G\.T\.?|\bC-G-T\b/gi, 'C G T'],
  [/\bDST\b|\bD\.S\.T\.?|\bD-S-T\b/gi, 'D S T'],
  [/\bFOB\b|\bF\.O\.B\.?|\bF-O-B\b/gi, 'F O B'],
  [/\bCIF\b|\bC\.I\.F\.?|\bC-I-F\b/gi, 'C I F'],
  [/\bCXP\b|\bC\.X\.P\.?|\bC-X-P\b/gi, 'C X P'],
  [/\bMDAs\b|\bM\.D\.A\.?s\b|\bM-D-As\b/gi, 'M D As'],
  [/\bMDA\b|\bM\.D\.A\.?|\bM-D-A\b/gi, 'M D A'],
  [/\bB2G\b/gi, 'B 2 G'],
  [/\bB2B\b/gi, 'B 2 B'],
  [/\bB2C\b/gi, 'B 2 C'],
  [/\be-?GP\b/gi, 'E G P'],
  [/\be-?Citie\b/gi, 'E-City'],
  [/\bBOU\b/g, 'Bank of Uganda'],
  [/\bTAT\b/g, 'Tax Appeals Tribunal'],
  [/\bTCC\b|\bT\.C\.C\.?|\bT-C-C\b/gi, 'T-C-C'],
  [/\bPRN\b|\bP\.R\.N\.?|\bP-R-N\b/gi, 'P R N'],
  [/\bBRN\b|\bB\.R\.N\.?|\bB-R-N\b/gi, 'B-R-N'],
  [/\bDT-(\d{3,5})\b/gi, 'D-T $1'],
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
  // Normalize Ugandan .go.ug, .co.ug, and .or.ug domains before acronym expansion
  cleaned = cleaned.replace(
    /\b([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)\.(go|co|or)\.ug\b(?![A-Za-z0-9-]|\.[A-Za-z0-9-])/gi,
    (_: string, host: string, tld: string) => `${host.replace(/\./g, ' dot ')} dot ${tld.toLowerCase()} dot u g`,
  );

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
