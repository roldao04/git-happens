"""What each habit expects of the home's devices (source/learn/expectations.py).

A hand-made store: one habit with six nightly readings, and the events of a window sensor and a lamp.

  uv run python -m unittest tests.test_expectations -v
"""
import json
import sqlite3
import unittest
from datetime import date, datetime, timedelta

from source.learn import expectations, grouping, storage, to_ms
from source.store import SCHEMA

DAY = date(2026, 9, 20)
WINDOW, LAMP = "contact-1", "bulb-1"
NIGHTS = [datetime(2026, 9, 15 + i, 23, 7) for i in range(6)]


def make_db(habit_tables: bool = True) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    storage.init(db)
    db.executemany("INSERT INTO devices (device_id, type, name, first_ts) VALUES (?, ?, ?, 0)",
                   [(WINDOW, "contactSensor", "Living-room window"), (LAMP, "bulb", "Light")])
    db.execute("INSERT INTO sensors (id, learner, focus, signature, learned_at, status, data)"
               " VALUES ('pattern-1', 'grouper', 'f', 'kind:bulb.power off', '2026-09-10', 'active', '{}')")
    if habit_tables:
        db.executescript(grouping.SCHEMA)
        habit = {"id": "habit-1", "name": "Night", "status": "active",
                 "steps": [{"pattern": "pattern-1", "offset_min": 0, "share": 1.0}]}
        db.execute("INSERT INTO habits (id, status, data) VALUES ('habit-1', 'active', ?)", (json.dumps(habit),))
        db.executemany("INSERT INTO habit_readings (habit_id, day, start, end, steps_seen) VALUES (?, ?, ?, NULL, '[]')",
                       [("habit-1", n.date().isoformat(), n.isoformat()) for n in NIGHTS])
    return db


def event(db: sqlite3.Connection, t: datetime, device_id: str, capability: str, new, kind: str = "change") -> None:
    name = "Living-room window" if device_id == WINDOW else "Light"
    db.execute("INSERT INTO events (ts, device_id, device, capability, kind, old, new) VALUES (?, ?, ?, ?, ?, NULL, ?)",
               (to_ms(t), device_id, name, capability, kind, json.dumps(new)))


def window_on(db: sqlite3.Connection, states: list[str]) -> None:
    """The window's state set one hour before each night, in order."""
    for n, s in zip(NIGHTS, states):
        event(db, n - timedelta(hours=1), WINDOW, "sensorState", s)


def expects(db: sqlite3.Connection) -> list[dict]:
    return json.loads(db.execute("SELECT data FROM habits WHERE id = 'habit-1'").fetchone()[0])["expects"]


class LearnExpectationsTest(unittest.TestCase):
    def make(self, **kw) -> sqlite3.Connection:
        db = make_db(**kw)
        self.addCleanup(db.close)
        return db

    def test_a_window_closed_on_6_of_6_nights_is_expected_closed(self):
        db = self.make()
        window_on(db, ["normal"] * 6)
        expectations.learn_expectations(db, DAY)
        self.assertEqual(expects(db), [{"device_id": WINDOW, "device": "Living-room window",
                                        "device_type": "contactSensor", "capability": "sensorState",
                                        "value": "normal", "agree": 6, "of": 6}])

    def test_4_of_6_is_not_enough(self):
        db = self.make()
        window_on(db, ["normal", "alarm", "normal", "alarm", "normal", "normal"])
        expectations.learn_expectations(db, DAY)
        self.assertEqual(expects(db), [])

    def test_a_device_with_no_history_before_a_night_doesnt_count_that_night(self):
        db = self.make()
        for n in NIGHTS[1:]:
            event(db, n - timedelta(hours=1), WINDOW, "sensorState", "normal")
        expectations.learn_expectations(db, DAY)
        self.assertEqual([(e["agree"], e["of"]) for e in expects(db)], [(5, 5)])

    def test_the_habits_own_lamp_is_not_an_expectation(self):
        db = self.make()
        for n in NIGHTS:
            event(db, n - timedelta(hours=2), LAMP, "power", "on")
        expectations.learn_expectations(db, DAY)
        self.assertEqual(expects(db), [])

    def test_values_are_compared_decoded(self):
        db = self.make()
        window_on(db, ["alarm"] * 6)
        expectations.learn_expectations(db, DAY)
        self.assertEqual(expects(db)[0]["value"], "alarm")

    def test_a_stale_or_gone_row_is_not_a_state(self):
        db = self.make()
        window_on(db, ["normal"] * 6)
        for n in NIGHTS:
            event(db, n - timedelta(minutes=5), WINDOW, "sensorState", None, kind="stale")
        expectations.learn_expectations(db, DAY)
        self.assertEqual(expects(db)[0]["value"], "normal")

    def test_expectations_are_recomputed_each_night(self):
        db = self.make()
        window_on(db, ["normal"] * 6)
        expectations.learn_expectations(db, DAY)
        for n in NIGHTS[3:]:
            event(db, n - timedelta(minutes=30), WINDOW, "sensorState", "alarm")
        expectations.learn_expectations(db, DAY)
        self.assertEqual(expects(db), [])

    def test_no_habit_tables_does_nothing(self):
        db = self.make(habit_tables=False)
        window_on(db, ["normal"] * 6)
        expectations.learn_expectations(db, DAY)  # no error


if __name__ == "__main__":
    unittest.main()
