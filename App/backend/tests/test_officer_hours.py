"""Officer working hours (app.officer_hours): when a person can take a call or a handoff."""

from __future__ import annotations

import datetime as dt
import os
import unittest
from unittest.mock import patch

from app import officer_hours as oh

KAMPALA = oh.officer_hours().zone  # Africa/Kampala, or fixed EAT without zone data


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> float:
    """A moment in October 2026, Kampala time. 16 October 2026 is a Friday."""
    return dt.datetime(2026, month, day, hour, minute, tzinfo=KAMPALA).timestamp()


class DefaultHoursTests(unittest.TestCase):
    """07:00–23:00 every day: the CXP desk closes at 11 PM."""

    def setUp(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CXP_")}
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_shift_runs_from_seven_to_eleven(self):
        self.assertFalse(oh.on_duty(at(16, 6, 59)))
        self.assertTrue(oh.on_duty(at(16, 7, 0)))
        self.assertTrue(oh.on_duty(at(16, 22, 59)))
        self.assertFalse(oh.on_duty(at(16, 23, 0)))
        self.assertFalse(oh.on_duty(at(17, 2, 30)))

    def test_after_eleven_officers_are_next_on_duty_at_seven(self):
        self.assertEqual(oh.next_on_duty(at(16, 23, 15)), at(17, 7, 0))
        self.assertEqual(oh.next_on_duty(at(17, 3, 0)), at(17, 7, 0))
        self.assertEqual(oh.next_on_duty(at(16, 10, 0)), at(16, 10, 0))  # on duty: now

    def test_the_sla_clock_stops_overnight(self):
        # 22:00 → 08:00 next day: one hour before close, one after opening.
        self.assertEqual(oh.working_seconds(at(16, 22, 0), at(17, 8, 0)), 2 * 3600)
        # Raised at 23:15 and answered at 07:30: thirty minutes count.
        self.assertEqual(oh.working_seconds(at(16, 23, 15), at(17, 7, 30)), 30 * 60)
        self.assertEqual(oh.working_seconds(at(16, 9, 0), at(16, 9, 0)), 0.0)

    def test_the_label_is_for_screens(self):
        self.assertTrue(oh.officer_hours().label(at(16, 12)).startswith("07:00–23:00"))


class ConfiguredHoursTests(unittest.TestCase):
    def hours(self, **env: str) -> oh.OfficerHours:
        with patch.dict(os.environ, env):
            return oh.officer_hours()

    def test_a_night_shift_runs_past_midnight(self):
        night = self.hours(CXP_OPEN_HOUR="20", CXP_CLOSE_HOUR="4")
        self.assertTrue(oh.on_duty(at(17, 2, 0), night))  # the shift that began on the 16th
        self.assertFalse(oh.on_duty(at(17, 5, 0), night))
        self.assertEqual(oh.working_seconds(at(16, 19, 0), at(17, 5, 0), night), 8 * 3600)
        self.assertEqual(oh.next_on_duty(at(17, 5, 0), night), at(17, 20, 0))

    def test_a_desk_that_never_closes(self):
        always = self.hours(CXP_OPEN_HOUR="0", CXP_CLOSE_HOUR="24")
        self.assertTrue(oh.on_duty(at(17, 3, 0), always))
        self.assertEqual(oh.working_seconds(at(16, 0, 0), at(17, 0, 0), always), 24 * 3600)

    def test_working_days_and_public_holidays(self):
        weekdays = self.hours(CXP_WORKING_DAYS="mon,tue,wed,thu,fri", CXP_CLOSED_DATES="2026-10-19")
        self.assertFalse(oh.on_duty(at(17, 10, 0), weekdays))  # Saturday
        self.assertFalse(oh.on_duty(at(19, 10, 0), weekdays))  # Monday, a holiday
        self.assertEqual(oh.next_on_duty(at(17, 10, 0), weekdays), at(20, 7, 0))  # Tuesday
        # Friday 22:00 to Tuesday 08:00: Friday's last hour and Tuesday's first.
        self.assertEqual(oh.working_seconds(at(16, 22, 0), at(20, 8, 0), weekdays), 2 * 3600)

    def test_no_shift_within_a_month_is_none(self):
        closed = self.hours(CXP_WORKING_DAYS="mon", CXP_CLOSED_DATES=",".join(
            (dt.date(2026, 10, 19) + dt.timedelta(weeks=w)).isoformat() for w in range(6)
        ))
        self.assertIsNone(oh.next_on_duty(at(17, 10, 0), closed))

    def test_settings_that_cannot_be_read_keep_the_defaults(self):
        with self.assertLogs("app.officer_hours", level="WARNING"):
            bad = self.hours(CXP_OPEN_HOUR="7am", CXP_CLOSE_HOUR="25", CXP_WORKING_DAYS="funday",
                             CXP_TIMEZONE="Mars/Olympus", CXP_CLOSED_DATES="soon")
        self.assertEqual((bad.open_hour, bad.close_hour, bad.days, bad.closed_dates), (7, 23, frozenset(range(7)), frozenset()))
        self.assertTrue(oh.on_duty(at(16, 12, 0), bad))


if __name__ == "__main__":
    unittest.main()
