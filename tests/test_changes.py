"""Habits over time and messy data: moved, stopped, holidays, outages, poll rounding, weekends.

  uv run python -m unittest tests.test_changes -v
"""
import copy
import sqlite3
import unittest
from datetime import timedelta

from source.learn import Happening, storage
from source.learn.run import learn, learn_day
from tests.fixtures.make_happenings import HOUSEHOLD_A, HOUSEHOLD_C, START, make, polls


def habits(rows, days, **kw):
    return learn([Happening.from_dict(r) for r in rows], START + timedelta(days=days - 1), **kw).habits


def tea(hs):
    return [h for h in hs if "2,100 W" in h.parts[-1]]


def minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def with_door_at(household, hhmm):
    h = copy.deepcopy(household)
    next(r for r in h["routines"] if r["name"] == "door")["at"] = (hhmm, 8)
    return h


class MovedTest(unittest.TestCase):
    """24 days with the tea around 08:00, then 16 days around 09:40, learned day by day."""

    @classmethod
    def setUpClass(cls):
        rows = make(HOUSEHOLD_A, 24) + make(with_door_at(HOUSEHOLD_A, "09:40"), 16, seed=9,
                                             start=START + timedelta(days=24))
        cls.db = sqlite3.connect(":memory:")
        storage.init(cls.db)
        storage.add_happenings(cls.db, rows)
        cls.status = {}  # day index -> tea sensors (id, status, time)
        for i in range(40):
            learn_day(cls.db, START + timedelta(days=i))
            cls.status[i] = [(s["id"], s["status"], s["pattern"]["time_of_day"])
                             for s in storage.export(cls.db)["sensors"] if "2,100 W" in s["pattern"]["parts"][-1]]
        cls.final = [s for s in storage.export(cls.db)["sensors"] if "2,100 W" in s["pattern"]["parts"][-1]]

    def test_one_card_that_moved(self):
        self.assertEqual(len(self.final), 1)
        s = self.final[0]
        self.assertEqual(s["status"], "active")
        self.assertLessEqual(abs(minutes(s["pattern"]["time_of_day"]) - minutes("09:40")), 15)
        self.assertLessEqual(abs(minutes(s["pattern"]["moved_from"]["time_of_day"]) - minutes("07:58")), 15)
        self.assertGreaterEqual(s["pattern"]["moved_from"]["on"], (START + timedelta(days=24)).isoformat())

    def test_old_time_fades_first(self):
        fading_days = [i for i, ss in self.status.items() if any(st == "fading" for _, st, _ in ss)]
        self.assertTrue(fading_days)
        self.assertLessEqual(fading_days[0], 24 + 5)  # a few missed days, not weeks

    def test_counts_start_at_the_new_time(self):
        p = self.final[0]["pattern"]
        self.assertLessEqual(p["days_total"], 16)
        self.assertGreaterEqual(p["days_seen"] / p["days_total"], 0.7)

    def test_old_readings_are_kept(self):
        sid = self.final[0]["id"]
        times = [r[0][11:16] for r in self.db.execute("SELECT start FROM sensor_readings WHERE sensor_id = ?", (sid,))]
        self.assertTrue(any(t < "08:30" for t in times) and any(t > "09:00" for t in times))


class StoppedOrAwayTest(unittest.TestCase):
    def test_stopped_tea_is_dropped(self):
        stopped = copy.deepcopy(HOUSEHOLD_A)
        next(r for r in stopped["routines"] if r["name"] == "kettle")["every"] = 0.0
        rows = make(HOUSEHOLD_A, 21) + make(stopped, 6, seed=9, start=START + timedelta(days=21))
        self.assertTrue(tea(habits(rows, 21)))
        self.assertEqual(tea(habits(rows, 27)), [])  # gone after a few days without it

    def test_holiday_does_not_fade(self):
        rows = make(HOUSEHOLD_A, 42, away=tuple(range(21, 28)))
        for days in (27, 28, 42):
            with self.subTest(days=days):
                self.assertEqual(len(tea(habits(rows, days, polls=polls(42)))), 1)


class MessyDataTest(unittest.TestCase):
    def test_offline_day_is_not_counted(self):
        found = habits(make(HOUSEHOLD_A, 14, offline=(5,)), 14, polls=polls(14, offline=(5,)))
        self.assertEqual(tea(found)[0].days_total, 13)
        night = [h for h in found if h.key.startswith("kind:home-") and abs(h.centre_min - minutes("23:15")) <= 30]
        self.assertTrue(night, [h.key for h in found])
        self.assertLessEqual(night[0].days_total, 13)  # the offline day isn't counted
        self.assertTrue(all(o.end - o.start < timedelta(hours=20) for o in night[0].members))  # no 40-hour "sleep"

    def test_poll_rounding_changes_nothing(self):
        clean = {h.key for h in habits(make(HOUSEHOLD_A, 14), 14)}
        rounded = {h.key for h in habits(make(HOUSEHOLD_A, 14, poll_s=60), 14)}
        self.assertEqual({k.split(":")[0] for k in clean}, {k.split(":")[0] for k in rounded})
        self.assertEqual(len(clean), len(rounded))

    def test_weekend_lie_in(self):
        ends = [h for h in habits(make(HOUSEHOLD_C, 42), 42) if h.key.startswith("kind-end:home-")]  # wake-ups
        work = [h for h in ends if abs(h.centre_min - minutes("07:00")) <= 15]
        weekend = [h for h in ends if abs(h.centre_min - minutes("09:30")) <= 15]
        self.assertTrue(work and weekend, [(h.key, h.pattern()["time_of_day"], h.weekdays) for h in ends])
        self.assertEqual(work[0].weekdays, ["mon", "tue", "wed", "thu", "fri"])
        self.assertEqual(weekend[0].weekdays, ["sat", "sun"])


if __name__ == "__main__":
    unittest.main()
