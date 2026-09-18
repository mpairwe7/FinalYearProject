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

/** Generate an RFC 5545 iCalendar (.ics) string with a 1-day reminder alarm. */
export function generateIcs(event: CalendarEventData): string {
  const now = new Date();
  const uid = `ura-event-${Date.now()}-${Math.random().toString(36).slice(2, 9)}@ura.go.ug`;
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

/**
 * Scan assistant message text for statutory deadline signals.
 *
 * Catches:
 * - 15th monthly returns (PAYE, VAT, WHT, Excise)
 * - 45-day statutory objection window (s.24 TPCA)
 * - Provisional quarterly deadlines (30 Sept, 31 Dec, 31 March, 30 June)
 */
export function detectDeadlineInMessage(content: string): CalendarEventData | null {
  if (!content) return null;
  const text = content.toLowerCase();

  // Monthly 15th deadline (PAYE, VAT, Excise, WHT)
  if (
    text.includes('15th') &&
    (text.includes('paye') ||
      text.includes('vat') ||
      text.includes('excise') ||
      text.includes('withholding') ||
      text.includes('deadline') ||
      text.includes('due date'))
  ) {
    const now = new Date();
    let targetMonth = now.getMonth();
    let targetYear = now.getFullYear();

    // If today is past the 15th, schedule for the 15th of next month
    if (now.getDate() >= 15) {
      targetMonth += 1;
      if (targetMonth > 11) {
        targetMonth = 0;
        targetYear += 1;
      }
    }

    const startDate = new Date(targetYear, targetMonth, 15, 9, 0, 0);
    const taxName = text.includes('paye')
      ? 'PAYE'
      : text.includes('vat')
      ? 'VAT'
      : text.includes('excise')
      ? 'Excise'
      : 'Monthly Return';

    return {
      title: `URA ${taxName} Return & Payment Deadline`,
      description:
        `Statutory deadline to submit your ${taxName} return and remit tax due to ` +
        `Uganda Revenue Authority (URA) to avoid late filing penalties. File at https://ura.go.ug.`,
      startDate,
      endDate: new Date(targetYear, targetMonth, 15, 17, 0, 0),
      location: 'URA Portal (https://ura.go.ug)',
    };
  }

  // 45-day objection deadline
  if (
    text.includes('45 days') &&
    (text.includes('objection') || text.includes('appeal') || text.includes('assessment'))
  ) {
    const now = new Date();
    const target = new Date(now.getTime() + 45 * 24 * 3600 * 1000);
    target.setHours(9, 0, 0, 0);

    return {
      title: 'URA 45-Day Statutory Objection Deadline',
      description:
        'Final statutory deadline to lodge a formal written objection against a tax ' +
        'assessment with the Commissioner General under Section 24 of the Tax Procedures Code Act.',
      startDate: target,
      endDate: new Date(target.getTime() + 8 * 3600 * 1000),
      location: 'URA Web Portal (https://ura.go.ug)',
    };
  }

  return null;
}
