"""learn_day day by day on the committed 14-day fixture: storage, stable ids, the export shape.

  uv run python -m unittest tests.test_learn_run -v
"""
import json
import sqlite3
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from source.learn import storage
from source.learn.run import learn_day

FIXTURE = Path(__file__).parent / "fixtures" / "happenings-14d.jsonl"


def minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m

FIRST = date(2026, 9, 1)
SENSOR_KEYS = {"id", "name", "named_by", "focus", "learner", "pattern", "learned_at", "confidence",
               "explanation", "status"}


class LearnDayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = sqlite3.connect(":memory:")
        storage.init(cls.db)
        storage.add_happenings(cls.db, [json.loads(l) for l in FIXTURE.read_text().splitlines()])
        cls.learned = {}  # day -> sensors first learned that day
        for i in range(14):
            day = FIRST + timedelta(days=i)
            cls.learned[day] = learn_day(cls.db, day)
        cls.out = storage.export(cls.db, FIRST + timedelta(days=13))

    def sensor(self, part, near=None, within=30):
        """The active sensor with `part` in its parts (and around `near`, if given)."""
        for s in self.out["sensors"]:
            p = s["pattern"]
            if s["status"] == "active" and any(part in x for x in p["parts"]) and (
                    near is None or abs(minutes(p["time_of_day"]) - minutes(near)) <= within):
                return s
        self.fail(f"no active sensor with {part!r} near {near}: {[s['pattern']['parts'] for s in self.out['sensors']]}")

    def test_sensors_appear_gradually(self):
        days = [d for d, new in self.learned.items() if new]
        self.assertGreater(len(days), 1)
        self.assertEqual(self.learned[FIRST], [])  # nothing on day 1

    def test_core_cards_are_active(self):
        self.sensor("2,100 W", near="07:56")  # door → kettle
        self.sensor("quiet after", near="23:16")  # the night's quiet (asleep)
        self.sensor("end of home-", near="07:02")  # its end (awake)
        ids = {s["id"] for s in self.out["sensors"]}
        self.assertTrue(all(s["merged_into"] in ids for s in self.out["sensors"] if s["status"] == "merged"))

    def test_morning_link(self):
        s = self.sensor("2,100 W")
        self.assertEqual(len(s["pattern"]["parts"]), 2)  # door → kettle
        self.assertLessEqual(date.fromisoformat(s["learned_at"]), FIRST + timedelta(days=8))
        self.assertEqual(s["confidence"], round(s["pattern"]["days_seen"] / s["pattern"]["days_total"], 2))

    def test_asleep_and_awake_are_kinds_of_quiet(self):
        night = self.sensor("quiet after", near="23:16")["pattern"]
        self.assertLessEqual(abs(minutes(night["until"]) - minutes("07:02")), 30)
        self.assertGreater(night["duration_min"], 6 * 60)

    def test_agreed_shape(self):
        for s in self.out["sensors"]:
            self.assertEqual(set(s) - {"merged_into"}, SENSOR_KEYS)
            self.assertEqual((s["learner"], s["focus"]), ("habit", "connected_life"))
            self.assertTrue({"parts", "time_of_day", "spread_min", "days_seen", "days_total"} <= set(s["pattern"]))
        ids = {s["id"] for s in self.out["sensors"]}
        self.assertTrue(all(r["sensor_id"] in ids for r in self.out["readings"]))
        self.assertTrue(all(h.kind for h in storage.load_happenings(self.db, datetime(2026, 9, 15))
                            if h.signal == "plug.instantPower" and h.size > 1900))

    def test_rerun_keeps_ids_and_dates(self):
        before = {s["id"]: s["learned_at"] for s in self.out["sensors"]}
        self.assertEqual(learn_day(self.db, FIRST + timedelta(days=13)), [])
        after = {s["id"]: s["learned_at"] for s in storage.export(self.db)["sensors"]}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
