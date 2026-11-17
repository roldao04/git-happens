"""Live status of learned habits (source/learn/today.py) and the agent's baselines.

  uv run python -m unittest tests.test_today -v
"""
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from source.context import build_context
from source.learn import Happening, storage, to_ms
from source.learn.kinds import classify
from source.learn.run import learn_day
from source.learn.today import baselines, sensor_today
from source.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "happenings-14d.jsonl"
FIRST = date(2026, 9, 1)


def learned_db(days: int, db=None) -> sqlite3.Connection:
    db = db or sqlite3.connect(":memory:")
    storage.init(db)
    storage.add_happenings(db, [json.loads(l) for l in FIXTURE.read_text().splitlines()])
    for i in range(days):
        learn_day(db, FIRST + timedelta(days=i))
    return db


def minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def by_part(db, now: datetime) -> dict[str, dict]:
    """Status of the three cards these tests follow: the tea (door → kettle), the night's quiet
    (asleep, a kind of quiet around 23:16) and its end (awake, around 07:02)."""
    sensors = {s["id"]: s["pattern"] for s in storage.export(db)["sensors"]}
    out = {}
    for st in sensor_today(db, now):
        p = sensors[st["sensor_id"]]
        first, t = p["parts"][0], minutes(p["time_of_day"])
        if "2,100 W" in p["parts"][-1]:
            key = "tea"
        elif first.startswith("end of home-") and abs(t - minutes("07:02")) <= 45:
            key = "awake"
        elif first.startswith("home-") and abs(t - minutes("23:16")) <= 45:
            key = "asleep"
        else:
            continue
        out.setdefault(key, st)
    return out


class TodayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = learned_db(13)  # learned up to day 13; day 14 is "today"

    def at(self, hhmm, day=14):
        h, m = map(int, hhmm.split(":"))
        return by_part(self.db, datetime(2026, 9, day, h, m))

    def test_night(self):
        now = self.at("02:00")
        self.assertEqual(now["asleep"]["status"], "now")
        self.assertEqual(now["awake"]["status"], "expected")
        self.assertEqual(now["tea"]["status"], "expected")

    def test_morning(self):
        self.assertEqual(self.at("06:00")["awake"]["status"], "expected")
        self.assertEqual(self.at("07:30")["tea"]["status"], "expected")
        later = self.at("08:30")
        self.assertEqual(later["awake"]["status"], "done")
        self.assertEqual(later["tea"]["status"], "done")
        self.assertTrue("07:30" <= later["tea"]["at"] <= "08:15")
        self.assertNotEqual(later["asleep"]["status"], "now")  # a quiet morning is not sleep

    def test_late_when_skipped(self):
        db = learned_db(10)  # day 11 has no tea
        tea = by_part(db, datetime(2026, 9, 11, 10, 0))["tea"]
        self.assertEqual(tea["status"], "late")
        days_seen = next(s for s in storage.export(db)["sensors"] if s["id"] == tea["sensor_id"])["pattern"]["days_seen"]
        self.assertAlmostEqual(tea["chance_still"], 1 / (days_seen + 1), places=3)  # conformal: later than ever

    def test_unknown_when_not_watched(self):
        db = learned_db(10)
        db.execute("CREATE TABLE snapshots (ts INTEGER PRIMARY KEY, home TEXT NOT NULL)")
        db.execute("INSERT INTO snapshots VALUES (?, '{}')", (to_ms(datetime(2026, 9, 11, 6)),))
        self.assertEqual(by_part(db, datetime(2026, 9, 11, 10))["tea"]["status"], "unknown")

    def test_classify_today(self):
        kinds = storage.load_kinds(self.db)
        t = datetime(2026, 9, 14, 8)
        kettle = next(h.kind for h in storage.load_happenings(self.db, datetime(2026, 9, 14))
                      if h.signal == "plug.instantPower" and h.size > 1900)
        self.assertEqual(classify(Happening("plug.instantPower", t, t + timedelta(seconds=170), 2180), kinds), kettle)
        self.assertIsNone(classify(Happening("plug.instantPower", t, t + timedelta(seconds=170), 1500), kinds))


class BaselinesTest(unittest.TestCase):
    """baselines() is ready for the agent's context, but not wired in yet (no AI on this side)."""

    def test_empty_without_sensors(self):
        self.assertEqual(baselines(sqlite3.connect(":memory:"), datetime(2026, 9, 14, 8)), {})

    def test_lists_habits_and_today(self):
        habits = baselines(learned_db(13), datetime(2026, 9, 14, 8, 30))["habits"]
        self.assertGreaterEqual(len(habits), 3)
        self.assertTrue(all({"name", "what", "usual", "today"} <= set(h) for h in habits))
        self.assertIn("done", {h["today"] for h in habits})

    def test_agent_context_does_not_use_them_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp), record=False)
            learned_db(13, store.db)
            ctx = build_context(to_ms(datetime(2026, 9, 14, 8, 30)), [], {}, store, None, 30 * 60_000)
            store.close()
        self.assertEqual(ctx["baselines"], {})


if __name__ == "__main__":
    unittest.main()
