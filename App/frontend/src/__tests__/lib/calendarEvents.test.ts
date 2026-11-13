import { describe, it, expect, vi } from 'vitest';
import {
  detectDeadlineInMessage,
  generateIcs,
  getGoogleCalendarUrl,
  downloadCalendarEvent,
  type CalendarEventData,
} from '../../lib/calendarEvents';

describe('calendarEvents', () => {
  it('detects the monthly 15th deadline for a named tax head', () => {
    const due = detectDeadlineInMessage(
      'PAYE returns and payments are due by the 15th day of the following month.',
      new Date(2026, 10, 3, 10),
    );
    expect(due).toEqual({ taxHead: 'PAYE', due: new Date(2026, 10, 15, 9, 0, 0) });
  });

  // G619: the date the chat offered was the client's own inference.
  it('on the 15th itself, the deadline is today, not next month', () => {
    const due = detectDeadlineInMessage('VAT is due by the 15th of the following month.', new Date(2026, 10, 15, 8));
    expect(due?.due).toEqual(new Date(2026, 10, 15, 9, 0, 0));
    const after = detectDeadlineInMessage('VAT is due by the 15th of the following month.', new Date(2026, 10, 16, 8));
    expect(after?.due).toEqual(new Date(2026, 11, 15, 9, 0, 0));
  });

  it('never dates an objection window from today', () => {
    // 45 days run from service of the assessment, which the chat does not know.
    expect(
      detectDeadlineInMessage('Under Section 24 of the Tax Procedures Code Act, you have 45 days to lodge an objection.'),
    ).toBeNull();
    expect(detectDeadlineInMessage('Object within 45 days of the assessment notice.')).toBeNull();
  });

  it('needs a monthly tax head and the monthly rule, not just "15th" and "deadline"', () => {
    expect(detectDeadlineInMessage('The deadline for your appeal hearing is the 15th of December.')).toBeNull();
    expect(detectDeadlineInMessage('PAYE was introduced on the 15th anniversary of the Act.')).toBeNull();
  });

  // CodeRabbit on PR 727: the head, the 15th and the monthly rule must be one
  // affirmative statement, and a stated period is not replaced by the next 15th.
  it('needs the head, the 15th and the monthly rule in one affirmative sentence', () => {
    expect(
      detectDeadlineInMessage('VAT is due at month-end. Your appeal hearing is on the 15th of next month.'),
    ).toBeNull();
    expect(detectDeadlineInMessage('VAT is not due on the 15th of every month.')).toBeNull();
    expect(detectDeadlineInMessage("PAYE isn't paid by the 15th of the following month for casual workers.")).toBeNull();
  });

  it('does not guess when the sentence names its period', () => {
    expect(
      detectDeadlineInMessage('The November VAT return is due by the 15th of the following month.', new Date(2026, 10, 3)),
    ).toBeNull();
    expect(detectDeadlineInMessage('PAYE for FY2026/27 is due by the 15th of the following month.')).toBeNull();
    expect(detectDeadlineInMessage('The VAT return for May is due by the 15th of the following month.')).toBeNull();
    // "may" the verb is not a period.
    expect(detectDeadlineInMessage('PAYE may be paid by the 15th of the following month.')?.taxHead).toBe('PAYE');
  });

  it('does not pick one head when the answer names several', () => {
    const due = detectDeadlineInMessage('PAYE and VAT returns are due by the 15th of the following month.', new Date(2026, 0, 2));
    expect(due?.taxHead).toBeNull();
  });

  it('returns null when no deadline is mentioned', () => {
    expect(detectDeadlineInMessage('What is Value Added Tax?')).toBeNull();
  });

  it('generates valid RFC 5545 iCalendar string with alarm', () => {
    const data: CalendarEventData = {
      title: 'URA VAT Deadline',
      description: 'Submit monthly VAT return online.',
      startDate: new Date(2026, 8, 15, 9, 0, 0),
    };
    const ics = generateIcs(data);
    expect(ics).toContain('BEGIN:VCALENDAR');
    expect(ics).toContain('SUMMARY:URA VAT Deadline');
    expect(ics).toContain('LOCATION:https://ura.go.ug');
    expect(ics).toContain('BEGIN:VALARM');
    expect(ics).toContain('TRIGGER:-P1D');
    expect(ics).toContain('END:VCALENDAR');
  });

  it('generates correct Google Calendar template URL', () => {
    const data: CalendarEventData = {
      title: 'URA PAYE Deadline',
      description: 'Remit PAYE taxes.',
      startDate: new Date(2026, 8, 15, 9, 0, 0),
    };
    const url = getGoogleCalendarUrl(data);
    expect(url).toContain('calendar.google.com');
    expect(url).toContain('URA+PAYE+Deadline');
  });

  it('handles client-side download without crashing', () => {
    const data: CalendarEventData = {
      title: 'URA Test Event',
      description: 'Test event description.',
      startDate: new Date(2026, 8, 15, 9, 0, 0),
    };
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const createObjectURL = vi.fn().mockReturnValue('blob:test');
    const revokeObjectURL = vi.fn();
    window.URL.createObjectURL = createObjectURL;
    window.URL.revokeObjectURL = revokeObjectURL;

    expect(() => downloadCalendarEvent(data)).not.toThrow();
    expect(clickSpy).toHaveBeenCalled();
  });
});
