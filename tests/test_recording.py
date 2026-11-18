"""The dashboard's recording (source/recording.py) against the dashboard's own rules for the file.

  uv run python -m unittest tests.test_recording -v      # about 10 s
"""
import json
import logging
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from source import pipeline, recording
from source.clock import TZ
from source.model import Event
from source.store import Store

LOCAL_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?")
SCALAR = (str, int, float, bool, type(None))


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=TZ).timestamp() * 1000)


def check(rec: dict) -> None:
    """What dashboard/src/data/recording.ts requires on load; raises AssertionError naming the field."""
    def text(o, name):
        assert isinstance(o.get(name), str), f"{name} is not text in {o}"

    def when(o, name):
        text(o, name)
        assert LOCAL_TIME.fullmatch(o[name]), f"{name} {o[name]!r} is not a local time"
        datetime.fromisoformat(o[name])

    when(rec["meta"], "start")
    when(rec["meta"], "end")
    for d in rec["devices"]:
        for name in ("id", "type", "name"):
            text(d, name)
    for row in rec["initial"] + rec["events"]:
        text(row, "device_id")
        text(row, "capability")
        assert isinstance(row["value"], SCALAR), f"value {row['value']!r} is not text, a number or true/false"
    for e in rec["events"]:
        when(e, "ts")
    stamps = [e["ts"] for e in rec["events"]]
    assert stamps == sorted(stamps), "events are not in time order"
    for s in rec["sensors"]:
        text(s, "id")
        when(s, "learned_at")
        assert isinstance(s["confidence"], (int, float)), f"confidence in {s['id']}"
        assert s["status"] in ("active", "fading", "merged"), f"status in {s['id']}"
        assert all(isinstance(part, str) for part in s["pattern"]["parts"]), f"parts in {s['id']}"
        text(s["pattern"], "time_of_day")
    for r in rec["readings"]:
        text(r, "sensor_id")
        when(r, "start")
        if r["end"] is not None:
            when(r, "end")


class Home:
    """A hand-made history: `Home().add(...)` then `.recording()`."""
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name), record=False)

    def device(self, device_id: str, dtype: str, name: str) -> "Home":
        self.store.save_devices(0, {device_id: (dtype, name)})
        return self

    def add(self, at: str | int, device_id: str, capability: str, new, kind: str = "change") -> "Home":
        ts = at if isinstance(at, int) else ms(at)
        self.store.save(ts, {}, [Event(ts, device_id, device_id, capability, kind, None, new)])
        return self

    def recording(self, **kw) -> dict:
        return recording.build(self.store.db, **kw)

    def close(self) -> None:
        self.store.close()
        self._tmp.cleanup()


class HandMade(unittest.TestCase):
    def setUp(self):
        self.home = Home().device("door", "contactSensor", "Front door").device("cam", "camera", "Camera")
        self.addCleanup(self.home.close)

    def test_first_values_are_initial_and_later_ones_are_events(self):
        self.home.add("2026-09-01T00:00:00", "door", "sensorState", "normal", "initial")
        self.home.add("2026-09-01T08:00:05", "door", "sensorState", "alarm")
        self.home.add("2026-09-03T21:15:00", "door", "sensorState", "normal")
        rec = self.home.recording(household="rosa")
        check(rec)
        self.assertEqual(rec["meta"], {"household": "rosa", "simulated": True, "start": "2026-09-01T00:00:00",
                                       "end": "2026-09-03T23:59:59", "days": 3})
        self.assertEqual(rec["initial"], [{"device_id": "door", "capability": "sensorState", "value": "normal"}])
        self.assertEqual(rec["events"], [
            {"ts": "2026-09-01T08:00:05", "device_id": "door", "capability": "sensorState", "value": "alarm"},
            {"ts": "2026-09-03T21:15:00", "device_id": "door", "capability": "sensorState", "value": "normal"}])
        self.assertIn({"id": "door", "type": "contactSensor", "name": "Front door"}, rec["devices"])

    def test_a_device_that_joins_later_starts_as_an_event(self):
        self.home.add("2026-09-01T00:00:00", "door", "sensorState", "normal", "initial")
        self.home.add("2026-09-02T10:00:00", "door", "batteryStatus", "high", "initial")
        rec = self.home.recording()
        self.assertEqual(len(rec["initial"]), 1)
        self.assertEqual(rec["events"][0]["ts"], "2026-09-02T10:00:00")

    def test_camera_motion_is_left_out(self):
        self.home.add("2026-09-01T00:00:00", "cam", "motionDetected", False, "initial")
        self.home.add("2026-09-01T00:00:00", "cam", "streamingSwitch", False, "initial")
        self.home.add("2026-09-01T09:00:00", "cam", "motionDetected", True)
        self.home.add("2026-09-01T09:00:00", "door", "motionDetected", True)  # only the camera's is hidden
        rec = self.home.recording()
        self.assertEqual([r["capability"] for r in rec["initial"]], ["streamingSwitch"])
        self.assertEqual([e["device_id"] for e in rec["events"]], ["door"])

    def test_values_the_dashboard_cannot_hold_become_text(self):
        self.home.add("2026-09-01T00:00:00", "door", "multi", [{"id": "1", "value": "on"}], "initial")
        self.home.add("2026-09-01T00:05:00", "door", "level", 20.5)
        rec = self.home.recording()
        check(rec)
        self.assertEqual(rec["initial"][0]["value"], '[{"id": "1", "value": "on"}]')
        self.assertEqual(rec["events"][0]["value"], 20.5)

    def test_polling_events_set_no_value_and_a_gone_one_clears_it(self):
        self.home.add("2026-09-01T00:00:00", "door", "sensorState", "normal", "initial")
        self.home.add("2026-09-01T03:00:00", "door", "sensorState", 1788217200000, "stale")
        self.home.add("2026-09-01T04:00:00", "door", "sensorState", 1788217200000, "returned")
        self.home.add("2026-09-01T05:00:00", "", "", 1788217200000, "gap")
        self.home.add("2026-09-01T06:00:00", "door", "sensorState", "anything", "gone")
        rec = self.home.recording()
        self.assertEqual(rec["events"],
                         [{"ts": "2026-09-01T06:00:00", "device_id": "door", "capability": "sensorState", "value": None}])

    def test_events_stay_in_clock_order_when_the_clocks_go_back(self):
        # 25 Oct 2026, Lisbon: 01:59 summer time is followed by 01:00 winter time
        before = int(datetime.fromisoformat("2026-10-25T00:50:00+00:00").timestamp() * 1000)  # 01:50 summer
        after = int(datetime.fromisoformat("2026-10-25T01:10:00+00:00").timestamp() * 1000)   # 01:10 winter
        later = int(datetime.fromisoformat("2026-10-25T02:30:00+00:00").timestamp() * 1000)   # 02:30 winter
        self.home.add(before - 3_600_000, "door", "sensorState", "normal", "initial")
        self.home.add(before, "door", "sensorState", "alarm")
        self.home.add(after, "door", "sensorState", "normal")
        self.home.add(later, "door", "sensorState", "alarm")
        rec = self.home.recording()
        check(rec)
        self.assertEqual([(e["ts"], e["value"]) for e in rec["events"]],
                         [("2026-10-25T01:50:00", "alarm"), ("2026-10-25T01:50:00", "normal"),
                          ("2026-10-25T02:30:00", "alarm")])

    def test_a_history_before_any_learning_has_no_sensors(self):
        self.home.add("2026-09-01T00:00:00", "door", "sensorState", "normal", "initial")
        rec = self.home.recording(simulated=False)
        check(rec)
        self.assertEqual((rec["sensors"], rec["readings"], rec["meta"]["simulated"]), ([], [], False))

    def test_an_empty_history_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no snapshots"):
            self.home.recording()


class SimulatedRun(unittest.TestCase):
    """Ten days of rosa through the pipeline's own --recording."""
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.dir = Path(cls._tmp.name)
        logging.disable(logging.INFO)  # the pipeline logs one line per day
        try:
            cls.code = pipeline.main(["--household", "rosa", "--days", "10", "--out", str(cls.dir / "run"),
                                      "--recording", str(cls.dir / "web" / "recording.json")])
        finally:
            logging.disable(logging.NOTSET)
        cls.rec = json.loads((cls.dir / "web" / "recording.json").read_text())

    def test_the_file_is_what_the_dashboard_accepts(self):
        self.assertEqual(self.code, 0)
        check(self.rec)
        self.assertEqual(self.rec["meta"], {"household": "rosa", "simulated": True, "start": "2026-09-01T00:00:00",
                                            "end": "2026-09-10T23:59:59", "days": 10})

    def test_every_value_that_changes_has_a_start(self):
        started = {(r["device_id"], r["capability"]) for r in self.rec["initial"]}
        self.assertTrue(started)
        self.assertLessEqual({(e["device_id"], e["capability"]) for e in self.rec["events"]}, started)
        self.assertLessEqual({d for d, _ in started}, {d["id"] for d in self.rec["devices"]})

    def test_it_holds_what_the_learners_stored(self):
        db = sqlite3.connect(self.dir / "run" / "home.sqlite3")
        self.addCleanup(db.close)
        stored = {i for (i,) in db.execute("SELECT id FROM sensors")}
        self.assertTrue(stored)
        self.assertEqual({s["id"] for s in self.rec["sensors"]}, stored)
        self.assertEqual(len(self.rec["readings"]), db.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0])
        changes = db.execute("SELECT COUNT(*) FROM events WHERE kind = 'change'").fetchone()[0]
        self.assertEqual(len(self.rec["events"]), changes)
        tea = [s for s in self.rec["sensors"] if any("plug" in part for part in s["pattern"]["parts"])]
        self.assertTrue(tea, "the morning tea is learned by day 10")

    def test_the_command_writes_the_same_file(self):
        out = self.dir / "again.json"
        self.assertEqual(recording.main([str(self.dir / "run"), "--out", str(out), "--household", "rosa"]), 0)
        self.assertEqual(json.loads(out.read_text()), self.rec)

    def test_the_command_refuses_a_folder_with_no_history(self):
        with self.assertLogs("source.recording", "ERROR"):
            self.assertEqual(recording.main([str(self.dir / "nothing"), "--out", str(self.dir / "no.json")]), 1)
        self.assertFalse((self.dir / "no.json").exists())


if __name__ == "__main__":
    unittest.main()
