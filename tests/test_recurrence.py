"""A habit's rhythm (source/learn/recurrence.py) on made-up days: the simplest rhythm that beats chance.

  uv run python -m unittest tests.test_recurrence -v
"""
import random
import unittest
from datetime import date, timedelta

from source.learn import ALPHA
from source.learn.recurrence import (describe, drift, evenly_spaced, find_recurrence, hourly_rhythm, is_on,
                                     on_days, rhythm, weekday_shift)

START = date(2026, 9, 1)  # a Tuesday


def days(n: int, start: date = START) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def week(d: date) -> int:
    """Weeks since the Monday before START."""
    return (d - date(2026, 8, 31)).days // 7


class RhythmTest(unittest.TestCase):
    def test_every_day(self):
        rng = random.Random(3)
        counted = days(21)
        rec = find_recurrence([d for d in counted if rng.random() < 0.85], counted)
        self.assertEqual((rec["kind"], rec["text"]), ("daily", "every day"))
        self.assertIsNone(rec["weekdays"])

    def test_weekdays_in_three_weeks(self):
        """The couple's weekday mornings: three weeks are enough, because the misses at weekends count."""
        counted = days(21)
        seen = [d for d in counted if d.weekday() < 5]
        del seen[3]  # one weekday skipped
        rec = find_recurrence(seen, counted)
        self.assertEqual((rec["kind"], rec["weekdays"], rec["text"]),
                         ("weekly", ["mon", "tue", "wed", "thu", "fri"], "every weekday"))
        self.assertEqual(len(on_days(rec, counted)), 15)

    def test_weekend(self):
        counted = days(17, date(2026, 9, 5))  # from its first Saturday
        rec = find_recurrence([d for d in counted if d.weekday() >= 5], counted)
        self.assertEqual((rec["weekdays"], rec["text"]), (["sat", "sun"], "every weekend"))

    def test_one_weekday(self):
        counted = days(42)
        rec = find_recurrence([d for d in counted if d.weekday() == 3], counted)
        self.assertEqual((rec["kind"], rec["weekdays"], rec["text"]), ("weekly", ["thu"], "every Thursday"))

    def test_a_few_weekdays(self):
        counted = days(70)
        rec = find_recurrence([d for d in counted if d.weekday() in (0, 2, 4)], counted)
        self.assertEqual((rec["weekdays"], rec["text"]), (["mon", "wed", "fri"], "every Monday, Wednesday and Friday"))

    def test_every_other_weekend(self):
        counted = days(60)
        seen = [d for d in counted if d.weekday() >= 5 and week(d) % 2 == 0]
        rec = find_recurrence(seen, counted)
        self.assertEqual((rec["kind"], rec["every_n_days"], rec["weekdays"]), ("every_n_days", 14, ["sat", "sun"]))
        self.assertEqual((rec["anchor"], rec["text"]), ("2026-09-05", "every other weekend"))
        self.assertEqual(on_days(rec, counted), seen)

    def test_every_other_weekend_needs_enough_weeks(self):
        """Five weekends can't tell "every other weekend" from "every weekend, half the time"."""
        counted = days(35)
        rec = find_recurrence([d for d in counted if d.weekday() >= 5 and week(d) % 2 == 0], counted)
        self.assertEqual((rec["kind"], rec["text"]), ("weekly", "every weekend"))

    def test_every_other_saturday(self):
        counted = days(112)
        rec = find_recurrence([d for d in counted if d.weekday() == 5 and week(d) % 2 == 1], counted)
        self.assertEqual((rec["every_n_days"], rec["weekdays"], rec["text"]), (14, ["sat"], "every other Saturday"))
        self.assertEqual(rec["anchor"], "2026-09-12")

    def test_every_three_days(self):
        counted = days(30)
        rec = find_recurrence(counted[1::3], counted)
        self.assertEqual((rec["kind"], rec["every_n_days"], rec["weekdays"], rec["text"]),
                         ("every_n_days", 3, None, "every 3 days"))
        self.assertEqual(on_days(rec, counted), counted[1::3])

    def test_day_of_the_month(self):
        counted = days(180)
        rec = find_recurrence([d for d in counted if d.day == 1], counted)
        self.assertEqual((rec["kind"], rec["day_of_month"], rec["text"]), ("monthly", 1, "on the 1st of the month"))

    def test_first_sunday_of_the_month(self):
        counted = days(180)
        seen = [d for d in counted if d.weekday() == 6 and d.day <= 7]
        rec = find_recurrence(seen, counted)
        self.assertEqual((rec["kind"], rec["weekdays"], rec["day_of_month"]), ("monthly", ["sun"], None))
        self.assertEqual(rec["text"], "on the first Sunday of the month")
        self.assertEqual(on_days(rec, counted), seen)

    def test_holidays_are_not_misses(self):
        """Days the home wasn't watched or nobody was in aren't counted days: a weekday habit stays one."""
        holiday = set(days(9, date(2026, 9, 12)))
        counted = [d for d in days(28) if d not in holiday]
        rec = find_recurrence([d for d in counted if d.weekday() < 5], counted)
        self.assertEqual(rec["text"], "every weekday")
        self.assertEqual(len(on_days(rec, counted)), len([d for d in counted if d.weekday() < 5]))

    def test_noise_is_daily(self):
        """Random days rarely beat chance: a finer rhythm about as often as ALPHA allows, else "every day"."""
        counted = days(90)
        finer = sum(find_recurrence([d for d in counted if rng.random() < 0.4], counted)["kind"] != "daily"
                    for rng in (random.Random(i) for i in range(300)))
        self.assertLessEqual(finer / 300, 2 * ALPHA)

    def test_shape(self):
        rec = find_recurrence([], days(7))
        self.assertEqual(set(rec), {"kind", "weekdays", "every_n_days", "anchor", "day_of_month", "every_n_hours", "text"})


class IsOnTest(unittest.TestCase):
    def test_rhythms(self):
        sat = date(2026, 9, 5)
        other = rhythm("every_n_days", ["sat", "sun"], every_n_days=14, anchor=sat)
        self.assertTrue(is_on(other, sat + timedelta(days=15)))  # the Sunday two weeks on
        self.assertFalse(is_on(other, sat + timedelta(days=7)))
        self.assertFalse(is_on(other, sat + timedelta(days=2)))  # a Monday
        self.assertTrue(is_on(other, sat - timedelta(days=14)))  # it works backwards too
        first_sunday = rhythm("monthly", ["sun"], anchor=date(2026, 9, 6))
        self.assertTrue(is_on(first_sunday, date(2026, 10, 4)))
        self.assertFalse(is_on(first_sunday, date(2026, 10, 11)))
        self.assertTrue(is_on(rhythm("monthly", day_of_month=15), date(2026, 11, 15)))
        self.assertTrue(is_on(rhythm("daily"), date(2026, 11, 15)))
        self.assertFalse(is_on(rhythm("weekly", ["mon", "tue", "wed", "thu", "fri"]), date(2026, 9, 6)))


class EveryNHoursTest(unittest.TestCase):
    def test_evenly_spaced(self):
        every, first, last, p = evenly_spaced([480, 660, 840, 1020, 1200])
        self.assertEqual((every, first, last), (180, 480, 1200))
        self.assertLess(p, 1e-5)

    def test_slightly_off(self):
        """A few minutes off each slot: still "every 3 hours", as a whole-hour grid fits them as well."""
        series = evenly_spaced([485, 655, 843, 1018, 1196])
        self.assertEqual(series[:3], (180, 485, 1196))
        self.assertLess(series[-1], ALPHA)
        counted = days(21)
        self.assertEqual(hourly_rhythm(series, counted, counted)["text"], "every 3 hours from 08:05 to 19:56")

    def test_not_whole_hours(self):
        every, first, last, p = evenly_spaced([480, 630, 780, 930, 1080])
        self.assertEqual(every, 150)
        counted = days(21)
        self.assertEqual(hourly_rhythm((every, first, last, p), counted, counted)["text"],
                         "every 150 minutes from 08:00 to 18:00")

    def test_uneven_is_chance(self):
        self.assertGreater(evenly_spaced([480, 600, 900, 1300])[-1], 0.1)
        self.assertIsNone(evenly_spaced([480, 900]))

    def test_round_the_clock(self):
        every, first, last, p = evenly_spaced([60, 300, 540, 780, 1020, 1260])
        self.assertEqual(every, 240)
        self.assertEqual((last - first) % 1440, 1200)

    def test_text(self):
        counted = days(21)
        rec = hourly_rhythm(evenly_spaced([480, 660, 840, 1020, 1200]), counted, counted)
        self.assertEqual((rec["kind"], rec["every_n_hours"], rec["weekdays"]), ("hourly", 3, None))
        self.assertEqual(rec["text"], "every 3 hours from 08:00 to 20:00")
        weekdays = hourly_rhythm(evenly_spaced([480, 540, 600, 660, 720]), [d for d in counted if d.weekday() < 5], counted)
        self.assertEqual(weekdays["text"], "every hour from 08:00 to 12:00, every weekday")
        self.assertEqual(describe(rhythm("hourly", every_n_hours=1.5), (480, 840)), "every 90 minutes from 08:00 to 14:00")


class TimingTest(unittest.TestCase):
    def test_later_at_weekends(self):
        rng = random.Random(5)
        ds = days(28)
        mins = [480 + (45 if d.weekday() >= 5 else 0) + rng.gauss(0, 10) for d in ds]
        usual, by_weekday, residuals = weekday_shift(ds, mins)
        self.assertEqual(set(by_weekday), {"sat", "sun"})
        self.assertTrue(all(35 <= v <= 55 for v in by_weekday.values()), by_weekday)
        self.assertLess(abs(usual - 480), 8)
        self.assertLess(max(abs(r) for r in residuals), 40)

    def test_no_shift(self):
        rng = random.Random(6)
        ds = days(28)
        self.assertEqual(weekday_shift(ds, [480 + rng.gauss(0, 10) for _ in ds])[1], {})

    def test_drift(self):
        rng = random.Random(7)
        ds = days(42)
        self.assertEqual(drift(ds, [i * 10 / 7 + rng.gauss(0, 4) for i in range(len(ds))]), 10)
        self.assertEqual(drift(ds, [rng.gauss(0, 4) for _ in ds]), 0)
        self.assertEqual(drift(ds[:3], [0, 10, 20]), 0)  # too few to tell


if __name__ == "__main__":
    unittest.main()
