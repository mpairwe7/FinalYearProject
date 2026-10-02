import { describe, it, expect, vi } from 'vitest';
import {
  detectDeadlineInMessage,
  generateIcs,
  getGoogleCalendarUrl,
  downloadCalendarEvent,
  type CalendarEventData,
} from '../../lib/calendarEvents';

describe('calendarEvents', () => {
  it('detects 15th monthly returns in message text', () => {
    const event = detectDeadlineInMessage(
      'PAYE returns and payments are due by the 15th day of each month.'
    );
    expect(event).not.toBeNull();
    expect(event?.title).toContain('PAYE Return');
    expect(event?.startDate.getDate()).toBe(15);
    expect(event?.description).toContain('Statutory deadline');
  });

  it('detects 45-day objection window in message text', () => {
    const event = detectDeadlineInMessage(
      'Under Section 24 of the Tax Procedures Code Act, you have 45 days to lodge an objection.'
    );
    expect(event).not.toBeNull();
    expect(event?.title).toContain('45-Day Statutory Objection');
    expect(event?.description).toContain('Commissioner General');
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
