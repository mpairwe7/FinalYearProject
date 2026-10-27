/**
 * The name a conversation carries in the sidebar until the model has named it.
 *
 * The first message cut at sixty characters read as a fragment ("Verify TIN
 * <ten digits> for my tax ...") and put a TIN on a screen anyone nearby can read.
 * This is the same message boiled down the way a person would title it: the
 * greeting and the "can you please" dropped, the first clause kept, numbers
 * that are identifiers removed, at most six words. The summary from
 * `/v1/chat/title` (see `hooks/useAutoTitle.ts`) replaces it once it arrives;
 * when the model is off or offline, this is the title that stays.
 */

const MAX_WORDS = 6;
const MAX_CHARS = 60;

const GREETING_RE =
  /^(?:(?:hi|hello|hey|hallo|greetings|good\s+(?:morning|afternoon|evening|day))(?:\s+(?:there|ura|team|sir|madam|all|officer)){0,2}\b[\s,.!:;-]*)+/i;

/** Openers that say how the question is asked, not what it is about. */
const FILLER_RE = new RegExp(
  '^(?:' +
    [
      String.raw`(?:please|pls|kindly|so|ok(?:ay)?|well)\b[\s,]*`,
      String.raw`(?:can|could|would|will)\s+(?:you|u)\s+(?:please\s+)?(?:help\s+me\s+(?:to\s+|with\s+)?|tell\s+me\s+(?:about\s+)?|explain\s+(?:to\s+me\s+)?|show\s+me\s+(?:how\s+to\s+)?)?`,
      String.raw`(?:i|we)\s+(?:want|need|would\s+like|wish|am\s+trying|are\s+trying)\s+to\s+(?:know\s+(?:about\s+|how\s+to\s+)?|understand\s+|learn\s+(?:about\s+)?)?`,
      String.raw`(?:i|we)\s+(?:want|need)\s+`,
      String.raw`how\s+(?:do|can|should|would)\s+(?:i|we)\s+`,
      String.raw`what(?:'s|’s|\s+is|\s+are)\s+`,
      String.raw`help\s+me\s+(?:to\s+|with\s+)?`,
      String.raw`tell\s+me\s+(?:about\s+)?`,
      String.raw`(?:i'?m|i’m|i\s+am|we'?re|we’re|we\s+are)\s+`,
      String.raw`(?:the|a|an)\s+`,
    ].join('|') +
    ')',
  'i',
);

/** Email addresses and links say nothing about the topic. */
const CONTACT_RE = /\bhttps?:\/\/\S+|\b\S+@\S+\.\S+/gi;
/** Five or more digits, alone or inside a token: a TIN, NIN, phone or account number. */
const IDENTIFIER_RE = /[+]?\b[A-Z]{0,4}\d[\d\s-]{3,}\d[A-Z0-9]*\b|\b[A-Z]{0,4}\d{5,}[A-Z0-9]*\b/gi;

const TRAILING_STOPWORDS = new Set([
  'a', 'an', 'and', 'at', 'by', 'for', 'from', 'in', 'of', 'on', 'or', 'the', 'to', 'with', 'my', 'our', 'your',
]);

function words(text: string): string[] {
  return text.split(/\s+/).filter(Boolean);
}

function stripFillers(text: string): string {
  let current = text.replace(GREETING_RE, '').trim();
  // Openers stack ("so can you please tell me about…"), so peel until stable.
  for (let i = 0; i < 6; i += 1) {
    const next = current.replace(FILLER_RE, '').trim();
    if (next === current) break;
    current = next;
  }
  return current;
}

/**
 * The first sentence that says something once its greeting and openers are
 * gone, cut at its first comma when that leaves a phrase of its own.
 */
function topicOf(text: string): string {
  for (const sentence of text.split(/[.?!;:\n]/)) {
    const stripped = stripFillers(sentence.trim());
    if (!stripped) continue;
    const beforeComma = stripped.split(',')[0].trim();
    const title = finish(stripFillers(words(beforeComma).length >= 3 ? beforeComma : stripped));
    if (title) return title;
  }
  return '';
}

function finish(candidate: string): string {
  const kept = words(candidate).slice(0, MAX_WORDS);
  while (kept.length > 1 && TRAILING_STOPWORDS.has(kept[kept.length - 1].toLowerCase())) kept.pop();
  let title = kept.join(' ').replace(/[\s,;:.!?–—-]+$/, '');
  if (title.length > MAX_CHARS) title = title.slice(0, MAX_CHARS).replace(/\s+\S*$/, '');
  if (!title) return '';
  if (title === title.toUpperCase() && /\s/.test(title) && /[A-Z]/.test(title)) {
    title = title.toLowerCase();
  }
  return title.charAt(0).toUpperCase() + title.slice(1);
}

/** A short provisional title for a conversation from its first message, or '' if none. */
export function provisionalTitle(message: string): string {
  const cleaned = (message || '')
    .replace(CONTACT_RE, ' ')
    .replace(IDENTIFIER_RE, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!cleaned) return '';
  // A message that is all greeting ("Hi!") keeps its own words.
  return topicOf(cleaned) || finish(cleaned);
}
