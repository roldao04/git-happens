"""Alerts when a habit starts and a device isn't as it usually is then (source/alerts.py).

A temporary store with the learned Night habit, which expects the living-room window closed, its decision
at 23:10, and the window's events.

  uv run python -m unittest tests.test_alerts -v
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from source import alerts, anticipate
from source.learn import grouping, to_ms
from source.model import Reading
from source.store import Store

WINDOW, PLUG = "contact-1", "plug-1"
NIGHT = datetime(2026, 9, 21, 23, 10)
OPENED = datetime(2026, 9, 21, 21, 10)
STALE_MS = 3_600_000
EXPECTS = [{"device_id": WINDOW, "device": "Living-room window", "device_type": "contactSensor",
            "capability": "sensorState", "value": "normal", "agree": 17, "of": 19}]


def window(value: str, seen: datetime) -> dict:
    return {(WINDOW, "sensorState"): Reading(WINDOW, "Living-room window", "sensorState", value, to_ms(seen),
                                              "contactSensor")}


class AlertTextTest(unittest.TestCase):
    def test_the_films_sentence(self):
        self.assertEqual(alerts.alert_text("Living-room window", "contactSensor", "Night", OPENED),
                         "The living-room window is still open. Night is starting, and it has been open since 21:10.")

    def test_a_plug_is_still_on(self):
        self.assertEqual(alerts.alert_text("Kettle", "plug", "Night", OPENED),
                         "The kettle is still on. Night is starting, and it has been on since 21:10.")


class AlertsNowTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name), record=False)
        self.addCleanup(self.store.close)
        db = self.db = self.store.db
        anticipate.init(db)
        db.executescript(grouping.SCHEMA)
        self.habit({})
        d = {"id": "decision-22", "at": NIGHT.isoformat(), "kind": "prepare", "habit_id": "habit-1",
             "occasion_id": None, "action": None, "expectation": None, "confidence": 0.89, "mode": "do",
             "status": "done", "reason": ""}
        anticipate._upsert(db, "prepare:decision-22", d, {})
        db.execute("INSERT INTO events (ts, device_id, device, capability, kind, old, new) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (to_ms(OPENED), WINDOW, "Living-room window", "sensorState", "change", '"normal"', '"alarm"'))
        db.commit()

    def habit(self, extra: dict) -> None:
        h = {"id": "habit-1", "name": "Night", "status": "active", "expects": EXPECTS, **extra}
        self.db.execute("INSERT OR REPLACE INTO habits (id, status, data) VALUES ('habit-1', 'active', ?)",
                        (json.dumps(h),))
        self.db.commit()

    def at(self, now: datetime, value: str = "alarm", seen: datetime | None = None) -> list[dict]:
        return alerts.alerts_now(self.db, window(value, seen or now - timedelta(minutes=1)), now, STALE_MS)

    def test_alert_when_night_started_and_the_window_is_open(self):
        self.assertEqual(self.at(NIGHT + timedelta(minutes=1)), [{
            "id": "habit-1:contact-1:2026-09-21", "at": "2026-09-21T23:10:00",
            "text": "The living-room window is still open. Night is starting, and it has been open since 21:10."}])

    def test_no_alert_before_the_habit_starts(self):
        self.assertEqual(self.at(NIGHT - timedelta(minutes=5)), [])

    def test_no_alert_seconds_before_the_habit_starts(self):
        self.assertEqual(self.at(NIGHT - timedelta(seconds=30)), [])

    def test_no_alert_when_the_habit_was_skipped_as_no_longer_expected(self):
        self.db.execute("UPDATE decisions SET meta = ?", (json.dumps({"ran": {"why": "skipped: no longer expected"}}),))
        self.assertEqual(self.at(NIGHT + timedelta(minutes=1)), [])

    def test_only_an_open_or_switched_on_device_alerts(self):
        self.habit({"expects": [{"device_id": PLUG, "device": "Kettle", "device_type": "plug", "capability": "power",
                                 "value": "on", "agree": 6, "of": 6},
                                {"device_id": "smoke-1", "device": "Smoke alarm", "device_type": "smokeSensor",
                                 "capability": "sensorState", "value": "normal", "agree": 6, "of": 6}]})
        now = NIGHT + timedelta(minutes=1)
        for dev, cap, val in ((PLUG, "power", "off"), ("smoke-1", "sensorState", "alarm")):
            self.db.execute("INSERT INTO events (ts, device_id, device, capability, kind, old, new) VALUES (?, ?, 'x', ?, 'change', NULL, ?)",
                            (to_ms(OPENED), dev, cap, json.dumps(val)))
        state = {(PLUG, "power"): Reading(PLUG, "Kettle", "power", "off", to_ms(now), "plug"),
                 ("smoke-1", "sensorState"): Reading("smoke-1", "Smoke alarm", "sensorState", "alarm", to_ms(now), "smokeSensor")}
        self.assertEqual(alerts.alerts_now(self.db, state, now, STALE_MS), [])

    def test_no_alert_once_the_window_is_closed(self):
        self.assertEqual(self.at(NIGHT + timedelta(minutes=1), value="normal"), [])

    def test_no_alert_after_two_hours(self):
        self.assertEqual(self.at(NIGHT + timedelta(hours=2, minutes=1)), [])

    def test_no_alert_from_a_stale_sensor(self):
        now = NIGHT + timedelta(minutes=1)
        self.assertEqual(self.at(now, seen=now - timedelta(hours=2)), [])

    def test_no_alert_for_a_quieted_expectation(self):
        self.habit({"quiet_expects": ["Living-room window"]})
        self.assertEqual(self.at(NIGHT + timedelta(minutes=1)), [])

    def test_no_alert_without_the_sensor_in_the_home(self):
        self.assertEqual(alerts.alerts_now(self.db, {}, NIGHT + timedelta(minutes=1), STALE_MS), [])

    def test_the_id_is_the_same_on_every_poll(self):
        ids = {a["id"] for t in (1, 6, 30) for a in self.at(NIGHT + timedelta(minutes=t))}
        self.assertEqual(ids, {"habit-1:contact-1:2026-09-21"})

    def test_a_broken_habit_row_gives_no_alerts_and_no_error(self):
        self.db.execute("UPDATE habits SET data = 'not json'")
        self.assertEqual(self.at(NIGHT + timedelta(minutes=1)), [])


if __name__ == "__main__":
    unittest.main()
