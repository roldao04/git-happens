"""The habit forecast (source/learn/forecast.py), on hand-made `habits` and `habit_readings` rows
in the shapes of docs/habits-plan.md (the grouper that writes them is built separately).

  uv run python -m unittest tests.test_forecast -v
"""
import importlib.util
import json
import sqlite3
import unittest
from unittest import mock
from datetime import date, datetime, time, timedelta

from source.learn.forecast import MAX_DRIFT_MIN, PRIOR_DAYS, _rhythm, forecast, upcoming

SCHEMA = """
CREATE TABLE habits (id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE habit_readings (habit_id TEXT NOT NULL, day TEXT NOT NULL, start TEXT NOT NULL, end TEXT,
                             steps_seen TEXT);
"""
FIRST = date(2026, 9, 1)  # a Tuesday
SHAPE = {"habit_id", "date", "probability", "expected", "window", "confidence", "basis"}


def habit(hid="habit-1", *, kind="daily", text="every day", usual="07:59", earliest="07:33", latest="08:27",
          weekdays=None, every_n_days=None, anchor=None, day_of_month=None, every_n_hours=None, by_weekday=None,
          occurrences=20, opportunities=21, confidence=0.84, first_seen="2026-09-01", last_seen="2026-09-21",
          status="active", trend=0) -> dict:
    """A Habit in the contract shape."""
    return {"id": hid, "name": None, "named_by": None, "status": status,
            "patterns": ["pattern-1"], "steps": [{"pattern": "pattern-1", "offset_min": 0, "share": 0.95}],
            "recurrence": {"kind": kind, "weekdays": weekdays, "every_n_days": every_n_days, "anchor": anchor,
                           "day_of_month": day_of_month, "every_n_hours": every_n_hours, "text": text},
            "window": {"usual": usual, "earliest": earliest, "latest": latest},
            "variation": {"spread_min": 14, "by_weekday": by_weekday or {}},
            "occurrences": occurrences, "opportunities": opportunities, "first_seen": first_seen,
            "last_seen": last_seen, "learned_at": "2026-09-06", "confidence": confidence,
            "trend": {"minutes_per_week": trend}}


def make_db(h: dict, readings: list[tuple[date, datetime]] = ()) -> sqlite3.Connection:
    """readings: (the habit's day, when it started)."""
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO habits VALUES (?, ?, ?)", (h["id"], h["status"], json.dumps(h)))
    db.executemany("INSERT INTO habit_readings VALUES (?, ?, ?, ?, ?)",
                   [(h["id"], d.isoformat(), t.isoformat(timespec="seconds"), None, "2") for d, t in readings])
    return db


def at(d: date, hhmm: str, days_later: int = 0) -> datetime:
    return datetime.combine(d + timedelta(days=days_later), time.fromisoformat(hhmm))


def three_weeks(skip=(), weekend_shift=50) -> list[tuple[date, datetime]]:
    """1–21 Sept: 07:59 on weekdays, later at weekends, except the skipped days."""
    out = []
    for i in range(21):
        d = FIRST + timedelta(days=i)
        if d not in skip:
            out.append((d, at(d, "07:59") + timedelta(minutes=weekend_shift if d.weekday() >= 5 else 0)))
    return out


def beta(hits, due, occurrences=20, opportunities=21):
    p0 = (occurrences + 1) / (opportunities + 2)
    return (hits + PRIOR_DAYS * p0) / (due + PRIOR_DAYS)


class ForecastTest(unittest.TestCase):
    def test_nothing_learned(self):
        self.assertEqual(forecast(sqlite3.connect(":memory:"), datetime(2026, 9, 21, 22)), [])

    def test_daily_by_weekday(self):
        db = make_db(habit(), three_weeks(skip={date(2026, 9, 16)}))  # missed one Wednesday
        (tue,) = forecast(db, datetime(2026, 9, 21, 22))  # Monday evening → Tuesday morning
        self.assertEqual(set(tue), SHAPE)
        self.assertEqual((tue["habit_id"], tue["date"], tue["expected"], tue["window"]),
                         ("habit-1", "2026-09-22", "07:59", ["07:33", "08:27"]))
        self.assertEqual(tue["probability"], round(beta(3, 3), 2))
        self.assertEqual(tue["confidence"], round(beta(3, 3) * 0.84, 2))  # probability × the habit's confidence
        self.assertEqual(tue["basis"], "Tuesdays: 3 of 3 since 1 Sept")

        (wed,) = forecast(db, datetime(2026, 9, 22, 22))
        self.assertEqual((wed["date"], wed["probability"]), ("2026-09-23", round(beta(2, 3), 2)))
        self.assertEqual(wed["basis"], "Wednesdays: 2 of 3 since 1 Sept")
        self.assertLess(wed["probability"], tue["probability"])

    def test_weekend_runs_later(self):
        """The grouper's weekday shift moves the time; with shifted days its window spans them all,
        so the forecast's window is ± twice the spread (14 min) instead."""
        db = make_db(habit(by_weekday={"sat": 50, "sun": 50}, latest="09:17"), three_weeks())
        (sat,) = forecast(db, datetime(2026, 9, 25, 22))
        self.assertEqual((sat["date"], sat["expected"], sat["window"]), ("2026-09-26", "08:49", ["08:21", "09:17"]))
        self.assertEqual(sat["basis"], "Saturdays: 3 of 3 since 1 Sept, about 50 min later than usual")
        (mon,) = forecast(db, datetime(2026, 9, 27, 22))
        self.assertEqual((mon["expected"], mon["window"]), ("07:59", ["07:31", "08:27"]))

    def test_drift(self):
        """A drifting habit is followed from the middle of its history (11 Sept), up to an hour."""
        db = make_db(habit(trend=10), three_weeks(weekend_shift=0))
        (tue,) = forecast(db, datetime(2026, 9, 21, 22))
        self.assertEqual(tue["expected"], "08:15")  # 07:59 + 10 min × 11 days / 7
        (far,) = forecast(make_db(habit(trend=90), three_weeks(weekend_shift=0)), datetime(2026, 9, 21, 22))
        self.assertEqual((far["expected"], MAX_DRIFT_MIN), ("08:59", 60))  # capped at an hour

    def test_by_weekday_when_no_readings(self):
        """Without readings its overall rate is the probability (no day counts as missed)."""
        db = make_db(habit(by_weekday={"sat": 50, "sun": 55}))
        (sat,) = forecast(db, datetime(2026, 9, 25, 22))
        self.assertEqual((sat["expected"], sat["probability"]), ("08:49", round(21 / 23, 2)))
        (sun,) = forecast(db, datetime(2026, 9, 26, 22))
        self.assertEqual(sun["expected"], "08:54")

    def test_today_already_happened(self):
        db = make_db(habit(), three_weeks() + [(date(2026, 9, 22), at(FIRST, "07:50", 21))])
        dates = [f["date"] for f in forecast(db, datetime(2026, 9, 22, 7, 55), hours=48)]
        self.assertEqual(dates, ["2026-09-23"])  # today's is done; the 24th's 07:59 is past the 48 h
        early = forecast(db, datetime(2026, 9, 22, 7, 0))  # before it happened: a forecast never sees the future
        self.assertEqual([f["date"] for f in early], ["2026-09-22"])

    def test_unlearned_days_are_not_misses(self):
        """Days after the last one the learners looked at count neither way."""
        db = make_db(habit(), three_weeks())
        (tue,) = forecast(db, datetime(2026, 9, 28, 22))  # a week later, nothing new learned
        self.assertEqual(tue["basis"], "Tuesdays: 3 of 3 since 1 Sept")

    def test_opportunities_cap_the_count(self):
        """The grouper's own opportunities leave out days the home was empty or not watched."""
        days = three_weeks(skip={FIRST + timedelta(days=i) for i in range(14, 21)})  # away 15–21 Sept
        db = make_db(habit(occurrences=14, opportunities=14, last_seen="2026-09-14"), days)
        (tue,) = forecast(db, datetime(2026, 9, 21, 22))
        self.assertEqual(tue["basis"], "Tuesdays: 2 of 2 since 1 Sept")

    def test_weekly_only_on_its_days(self):
        h = habit(kind="weekly", weekdays=["mon", "wed", "fri"], text="Mondays, Wednesdays and Fridays")
        db = make_db(h, [(d, t) for d, t in three_weeks() if d.weekday() in (0, 2, 4)])
        self.assertEqual(forecast(db, datetime(2026, 9, 21, 22)), [])  # Tuesday: not one of its days
        (wed,) = forecast(db, datetime(2026, 9, 22, 22))
        self.assertEqual((wed["date"], wed["basis"]), ("2026-09-23", "Wednesdays: 3 of 3 since 1 Sept"))

    def test_every_other_weekend(self):
        h = habit(kind="every_n_days", every_n_days=14, anchor="2026-09-05", weekdays=["sat", "sun"],
                  text="every other weekend", usual="10:00", earliest="09:30", latest="10:30",
                  occurrences=4, opportunities=4, first_seen="2026-09-05", last_seen="2026-09-20")
        days = [date(2026, 9, 5), date(2026, 9, 6), date(2026, 9, 19), date(2026, 9, 20)]
        db = make_db(h, [(d, at(d, "10:00")) for d in days])
        self.assertEqual(forecast(db, datetime(2026, 9, 25, 22)), [])  # 26 Sept: the weekend in between
        (sat,) = forecast(db, datetime(2026, 10, 2, 22))
        self.assertEqual((sat["date"], sat["expected"]), ("2026-10-03", "10:00"))
        self.assertEqual(sat["basis"], "Every other weekend: 4 of 4 since 5 Sept")
        (sun,) = forecast(db, datetime(2026, 10, 3, 22))
        self.assertEqual(sun["date"], "2026-10-04")

    def test_every_n_days(self):
        """Due every third day from 2 Sept; a day off (12 Sept instead of 11) is a miss, as for the grouper."""
        h = habit(kind="every_n_days", every_n_days=3, anchor="2026-09-02", text="every 3 days",
                  occurrences=6, opportunities=7, first_seen="2026-09-02", last_seen="2026-09-20")
        days = [date(2026, 9, d) for d in (2, 5, 8, 12, 14, 17, 20)]
        db = make_db(h, [(d, at(d, "08:00")) for d in days])
        self.assertEqual(forecast(db, datetime(2026, 9, 21, 12)), [])  # the 22nd isn't due
        (f,) = forecast(db, datetime(2026, 9, 22, 12))
        self.assertEqual((f["date"], f["basis"]), ("2026-09-23", "Every 3 days: 6 of 7 since 2 Sept"))

    def test_monthly(self):
        h = habit(kind="monthly", day_of_month=1, text="on the 1st of the month", usual="09:00", earliest="08:40",
                  latest="09:20", occurrences=2, opportunities=2, first_seen="2026-08-01", last_seen="2026-09-01")
        db = make_db(h, [(d, at(d, "09:00")) for d in (date(2026, 8, 1), date(2026, 9, 1))])
        self.assertEqual(forecast(db, datetime(2026, 9, 20, 12)), [])
        (f,) = forecast(db, datetime(2026, 9, 30, 12))
        self.assertEqual((f["date"], f["basis"]), ("2026-10-01", "On the 1st of the month: 2 of 2 since 1 Aug"))

        nth = habit(kind="monthly", weekdays=["sat"], anchor="2026-08-01", text="on the first Saturday of the month",
                    usual="10:00", earliest="09:40", latest="10:20", occurrences=2, opportunities=2,
                    first_seen="2026-08-01", last_seen="2026-09-05")
        db = make_db(nth, [(d, at(d, "10:00")) for d in (date(2026, 8, 1), date(2026, 9, 5))])
        (f,) = forecast(db, datetime(2026, 10, 2, 12))
        self.assertEqual(f["date"], "2026-10-03")
        self.assertEqual(forecast(db, datetime(2026, 10, 9, 12)), [])  # the second Saturday

    def test_hourly_slots(self):
        h = habit(kind="hourly", every_n_hours=3, text="every 3 hours", usual="08:00", earliest="07:50",
                  latest="08:10", occurrences=50, opportunities=80, first_seen="2026-09-01", last_seen="2026-09-10")
        readings = [(d, at(d, hhmm)) for d in (FIRST + timedelta(days=i) for i in range(10))
                    for hhmm in ("08:00", "11:00", "14:00", "17:00", "20:00")]
        out = forecast(make_db(h, readings), datetime(2026, 9, 11, 7))
        self.assertEqual([f["expected"] for f in out], ["08:00", "11:00", "14:00", "17:00", "20:00"])  # no night slots
        self.assertEqual(out[0]["probability"], round(11 / 12, 2))  # Laplace: (10 + 1) / (10 + 2)
        self.assertEqual(out[0]["basis"], "Around 08:00: 10 of 10 days since 1 Sept")

    def test_bedtime_after_midnight_belongs_to_the_evening(self):
        """Readings carry the habit's own day: a Monday-night bedtime at 00:10 is Monday's."""
        h = habit(usual="00:10", earliest="23:50", latest="00:40", occurrences=21, opportunities=21)
        readings = [(FIRST + timedelta(days=i), at(FIRST, "00:10", i + 1)) for i in range(21)]
        db = make_db(h, readings)
        (f,) = forecast(db, datetime(2026, 9, 22, 20))  # Tuesday evening
        self.assertEqual((f["date"], f["expected"], f["window"]), ("2026-09-23", "00:10", ["23:50", "00:40"]))
        self.assertEqual(f["basis"], "Tuesdays: 3 of 3 since 1 Sept")
        (s,) = upcoming(db, datetime(2026, 9, 22, 20))
        self.assertEqual(s.day, date(2026, 9, 22))

    @unittest.skipUnless(importlib.util.find_spec("source.learn.recurrence"), "the grouper isn't there")
    def test_due_days_match_the_grouper(self):
        """The same due days as the grouper's own rule, so its opportunities and these counts agree."""
        from source.learn.recurrence import is_on, rhythm
        sat, sun = date(2026, 9, 5), date(2026, 9, 6)
        recs = [rhythm("daily"), rhythm("weekly", ["mon", "wed", "fri"]), rhythm("weekly", ["sat", "sun"]),
                rhythm("every_n_days", ["sat", "sun"], every_n_days=14, anchor=sat),
                rhythm("every_n_days", ["tue"], every_n_days=21, anchor=date(2026, 9, 8)),
                rhythm("every_n_days", every_n_days=3, anchor=date(2026, 9, 2)),
                rhythm("monthly", day_of_month=15), rhythm("monthly", ["sun"], anchor=sun),
                rhythm("monthly", ["sat"], anchor=date(2026, 9, 19))]
        for rec in recs:
            anchor = date.fromisoformat(rec["anchor"]) if rec["anchor"] else None
            mine = _rhythm(rec, anchor)
            for d in (FIRST + timedelta(days=i) for i in range(150)):
                self.assertEqual(mine.kind_of(d) is not None, is_on(rec, d), f"{rec['text']} on {d}")

    def test_weekday_windows(self):
        """A habit with `windows` (C3) takes the day's own usual time and window: João home about 19:30,
        on Tuesdays 21:00."""
        h = habit(kind="weekly", weekdays=["mon", "tue", "wed", "thu", "fri"], text="every weekday",
                  usual="19:30", earliest="19:15", latest="21:15", by_weekday={"tue": 90})
        h["windows"] = [{"days": ["mon", "wed", "thu", "fri"], "usual": "19:30", "earliest": "19:15", "latest": "19:45"},
                        {"days": ["tue"], "usual": "21:00", "earliest": "20:45", "latest": "21:15"}]
        db = make_db(h)
        (tue,) = forecast(db, datetime(2026, 9, 22, 12))
        self.assertEqual((tue["expected"], tue["window"]), ("21:00", ["20:45", "21:15"]))
        self.assertIn("about 90 min later than usual", tue["basis"])
        (wed,) = forecast(db, datetime(2026, 9, 23, 12))
        self.assertEqual((wed["expected"], wed["window"]), ("19:30", ["19:15", "19:45"]))
        self.assertNotIn("shifted", wed)

    def shifted_db(self, minutes=90, confidence=0.75):
        """A daily habit, and an occasion that moves it tomorrow (occasions.shifts_now, faked)."""
        db = make_db(habit(), three_weeks(weekend_shift=0))
        db.execute("CREATE TABLE occasions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT INTO occasions VALUES ('occasion-1', '{}')")
        moves = [{"occasion_id": "occasion-1", "confidence": confidence, "habit_id": "habit-1",
                  "date": "2026-09-22", "minutes": minutes}]
        patch = mock.patch("source.learn.occasions.shifts_now", return_value=moves)
        patch.start()
        self.addCleanup(patch.stop)
        return db

    def test_shifted_by_an_occasion(self):
        """C6: the expected time and window move; `shifted` keeps what they were."""
        db = self.shifted_db()
        (tue,) = forecast(db, datetime(2026, 9, 21, 22))
        self.assertEqual((tue["expected"], tue["window"]), ("09:29", ["09:03", "09:57"]))
        self.assertEqual(tue["shifted"], {"occasion_id": "occasion-1", "minutes": 90,
                                          "before": {"expected": "07:59", "window": ["07:33", "08:27"]}})
        self.assertTrue(tue["basis"].endswith("about 90 min later after occasion-1"), tue["basis"])
        (slot,) = upcoming(db, datetime(2026, 9, 22, 8, 30), hours=2)  # past its usual time, still ahead of the shifted one
        self.assertEqual(slot.expected, datetime(2026, 9, 22, 9, 29))
        (wed,) = forecast(db, datetime(2026, 9, 22, 22))
        self.assertNotIn("shifted", wed)  # only the day after

    def test_not_shifted_by_an_unsure_match(self):
        (tue,) = forecast(self.shifted_db(confidence=0.4), datetime(2026, 9, 21, 22))
        self.assertEqual(tue["expected"], "07:59")
        self.assertNotIn("shifted", tue)

    def test_only_active_habits(self):
        db = make_db(habit(status="fading"), three_weeks())
        self.assertEqual(forecast(db, datetime(2026, 9, 21, 22)), [])

    def test_never_raises(self):
        db = make_db(habit(), three_weeks())
        db.execute("INSERT INTO habits VALUES ('habit-2', 'active', 'not json')")
        db.execute("INSERT INTO habits VALUES ('habit-3', 'active', ?)", (json.dumps({"id": "habit-3", "window": 5}),))
        self.assertEqual([f["habit_id"] for f in forecast(db, datetime(2026, 9, 21, 22))], ["habit-1"])


if __name__ == "__main__":
    unittest.main()
