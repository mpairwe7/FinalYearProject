/**
 * Tax Calendar & Statutory Deadline Event Helpers (2026).
 *
 * Provides client-side RFC 5545 (.ics) iCalendar generation, Google Calendar
 * web deep-links, and automatic statutory deadline detection in assistant replies.
 * Enables 1-click deadline sync for taxpayers to avoid late filing and payment penalties.
 */

export interface CalendarEventData {
  title: string;
  description: string;
  startDate: Date;
  endDate?: Date;
  location?: string;
  url?: string;
}

/** Format a Date to iCalendar UTC compact string (YYYYMMDDTHHMMSSZ). */
function formatUtcCompact(d: Date): string {
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`);
  return (
    `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}` +
    `T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}00Z`
  );
}

function getRandomNonce(): string {
  if (typeof crypto !== 'undefined') {
    if (typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    if (typeof crypto.getRandomValues === 'function') {
      const arr = new Uint32Array(2);
      crypto.getRandomValues(arr);
      return `${arr[0].toString(36)}-${arr[1].toString(36)}`;
    }
  }
  return `${Date.now()}-${Math.floor(Date.now() / 1000)}`;
}

/** Generate an RFC 5545 iCalendar (.ics) string with a 1-day reminder alarm. */
export function generateIcs(event: CalendarEventData): string {
  const now = new Date();
  const uid = `ura-event-${Date.now()}-${getRandomNonce()}@ura.go.ug`;
  const dtStart = formatUtcCompact(event.startDate);
  const dtEnd = formatUtcCompact(
    event.endDate || new Date(event.startDate.getTime() + 8 * 3600 * 1000)
  );
  const dtStamp = formatUtcCompact(now);
  const safeDesc = event.description.replace(/\r?\n/g, '\\n');

  return [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Uganda Revenue Authority//Taxpayer Assistant//EN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${uid}`,
    `DTSTAMP:${dtStamp}`,
    `DTSTART:${dtStart}`,
    `DTEND:${dtEnd}`,
    `SUMMARY:${event.title}`,
    `DESCRIPTION:${safeDesc}`,
    `LOCATION:${event.location || 'https://ura.go.ug'}`,
    'STATUS:CONFIRMED',
    'BEGIN:VALARM',
    'TRIGGER:-P1D',
    'DESCRIPTION:Reminder: URA statutory tax deadline tomorrow',
    'ACTION:DISPLAY',
    'END:VALARM',
    'END:VEVENT',
    'END:VCALENDAR',
  ].join('\r\n');
}

/** Download the event as a .ics file to the taxpayer's device. */
export function downloadCalendarEvent(event: CalendarEventData): void {
  if (typeof window === 'undefined') return;
  const ics = generateIcs(event);
  const blob = new Blob([ics], { type: 'text/calendar;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const slug = event.title.toLowerCase().replace(/[^a-z0-9]+/g, '_').slice(0, 32);
  a.download = `${slug}.ics`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** Generate a direct Google Calendar web event creation link. */
export function getGoogleCalendarUrl(event: CalendarEventData): string {
  const dtStart = formatUtcCompact(event.startDate);
  const dtEnd = formatUtcCompact(
    event.endDate || new Date(event.startDate.getTime() + 8 * 3600 * 1000)
  );
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: event.title,
    dates: `${dtStart}/${dtEnd}`,
    details: `${event.description}\n\nFile/Pay online: https://ura.go.ug`,
    location: event.location || 'URA Web Portal (https://ura.go.ug)',
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

/** A monthly tax head whose return and payment fall due on the 15th. */
export type MonthlyTaxHead = 'PAYE' | 'VAT' | 'WHT' | 'Excise';

/** A monthly deadline an answer states. */
export interface MonthlyDeadline {
  /** The one head the answer names, or null when it names several. */
  taxHead: MonthlyTaxHead | null;
  /** The next 15th, at 09:00 local time: today when today is the 15th. */
  due: Date;
}

const MONTHLY_HEADS: ReadonlyArray<[MonthlyTaxHead, RegExp]> = [
  ['PAYE', /\bpaye\b/],
  ['VAT', /\bvat\b/],
  ['WHT', /\b(?:wht|withholding)\b/],
  ['Excise', /\bexcise\b/],
];

/** A statement that the rule does not apply. */
const NEGATION = /\b(?:not|no longer|never|isn't|aren't|doesn't|don't|exempt)\b/;
/** A sentence that names the period it is about: a month, a year or a fiscal year. */
const NAMED_PERIOD =
  /\b(?:january|february|march|april|june|july|august|september|october|november|december|fy\s?\d{2,4}|20\d\d)\b/i;
/** "May" the month, not "may" the verb: only capitalised mid-sentence. */
const MAY_MONTH = /(?<=\S\s+)May\b|\bMay\s+(?:return|20\d\d|\d{1,2}\b)/;

/**
 * The monthly 15th deadline an answer states, if it states one (G619).
 *
 * Only the statutory monthly rule is recognised: a monthly tax head and "the
 * 15th of the following (or each) month". The date it gives is the next 15th.
 * Nothing else is dated: a window that runs from an event the chat does not
 * know — 45 days from service of an assessment — would be counted from today
 * and be wrong, and "15th" next to "deadline" is not a monthly return.
 */
export function detectDeadlineInMessage(content: string, now: Date = new Date()): MonthlyDeadline | null {
  if (!content) return null;
  // One statement must carry the whole rule: a head, "the 15th" and a monthly
  // recurrence, affirmatively. Two clauses that each carry part of it ("VAT is
  // due at month-end. Your hearing is on the 15th of next month.") are not it.
  const sentences = content.split(/(?<=[.!?;])\s+|\n+/);
  const heads = new Set<MonthlyTaxHead>();
  for (const original of sentences) {
    const sentence = original.toLowerCase();
    if (!/\b15th\b/.test(sentence) || !/\b(?:following|next|each|every) month\b/.test(sentence)) continue;
    if (NEGATION.test(sentence)) continue;
    // A sentence naming its own period ("the November return", "FY2026/27")
    // has a date the next 15th may not be. Say nothing rather than guess.
    if (NAMED_PERIOD.test(sentence) || MAY_MONTH.test(original)) return null;
    for (const [head, rx] of MONTHLY_HEADS) if (rx.test(sentence)) heads.add(head);
  }
  if (!heads.size) return null;
  const month = now.getDate() > 15 ? now.getMonth() + 1 : now.getMonth();
  return {
    taxHead: heads.size === 1 ? [...heads][0] : null,
    due: new Date(now.getFullYear(), month, 15, 9, 0, 0),
  };
}
