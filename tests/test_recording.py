"""The dashboard's recording (source/recording.py) against the dashboard's own rules for the file.

  uv run python -m unittest tests.test_recording -v      # about 10 s
"""
import io
import json
import logging
import re
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path

from source import pipeline, recording
from source.clock import TZ
from source.learn import to_local
from source.model import Event
from source.store import Store

MORNING = Path(__file__).parent / "fixtures" / "morning.jsonl"

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
    for g in rec["gaps"]:
        when(g, "start")
        when(g, "end")
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

    def add(self, at: str | int, device_id: str, capability: str, new, kind: str = "change", old=None) -> "Home":
        ts = at if isinstance(at, int) else ms(at)
        self.store.save(ts, {}, [Event(ts, device_id, device_id, capability, kind, old, new)])
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

    def test_a_silent_device_loses_its_signal_until_it_is_heard_again(self):
        self.home.device("lamp", "bulb", "Lamp")
        self.home.add("2026-09-01T00:00:00", "lamp", "power", "on", "initial")
        self.home.add("2026-09-01T03:00:00", "lamp", "lastCommunicationTime", ms("2026-09-01T01:58:30"), "stale")
        self.home.add("2026-09-01T04:00:00", "lamp", "lastCommunicationTime", ms("2026-09-01T03:59:00"), "returned")
        self.home.add("2026-09-01T06:00:00", "lamp", "power", "anything", "gone")
        rec = self.home.recording()
        check(rec)
        row = lambda ts, cap, value: {"ts": ts, "device_id": "lamp", "capability": cap, "value": value}
        self.assertEqual(rec["events"], [row("2026-09-01T03:00:00", "reachable", False),
                                         row("2026-09-01T03:00:00", "lastHeard", "2026-09-01T01:58:30"),
                                         row("2026-09-01T04:00:00", "reachable", True),
                                         row("2026-09-01T06:00:00", "power", None)])

    def test_only_devices_that_report_all_the_time_lose_their_signal_by_going_quiet(self):
        # battery sensors and the hub only speak when something happens, so their silence means nothing
        self.home.device("lamp", "bulb", "Lamp").device("smoke", "smokeSensor", "Smoke sensor")
        self.home.add("2026-09-01T00:00:00", "door", "sensorState", "normal", "initial")
        for device_id in ("door", "smoke", "lamp"):
            self.home.add("2026-09-01T03:00:00", device_id, "lastCommunicationTime", ms("2026-09-01T01:58:30"), "stale")
        rec = self.home.recording()
        self.assertEqual({e["device_id"] for e in rec["events"]}, {"lamp"})

    def test_a_device_already_silent_at_the_start_starts_without_signal(self):
        self.home.device("lamp", "bulb", "Lamp")
        self.home.add("2026-09-01T00:00:00", "lamp", "power", "on", "initial")
        self.home.add("2026-09-01T00:00:00", "lamp", "lastCommunicationTime", ms("2026-08-31T22:10:00"), "stale")
        rec = self.home.recording()
        self.assertEqual(rec["events"], [])
        self.assertIn({"device_id": "lamp", "capability": "reachable", "value": False}, rec["initial"])
        self.assertIn({"device_id": "lamp", "capability": "lastHeard", "value": "2026-08-31T22:10:00"}, rec["initial"])

    def test_a_gap_in_the_polling_sets_no_value_and_is_listed_as_not_watched(self):
        self.home.add("2026-09-01T00:00:00", "door", "sensorState", "normal", "initial")
        self.home.add("2026-09-01T05:00:00", "loop", "poll", ms("2026-09-01T05:00:00"), "gap", old=ms("2026-09-01T01:00:00"))
        rec = self.home.recording()
        self.assertEqual(rec["events"], [])
        self.assertEqual(rec["gaps"], [{"start": "2026-09-01T01:00:00", "end": "2026-09-01T05:00:00"}])

    def test_the_hours_before_the_first_reading_were_not_watched(self):
        self.home.add("2026-09-01T11:44:28", "door", "sensorState", "normal", "initial")
        self.home.add("2026-09-01T12:00:00", "door", "sensorState", "alarm")
        self.assertEqual(self.home.recording()["gaps"], [{"start": "2026-09-01T00:00:00", "end": "2026-09-01T11:44:28"}])

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

    def test_a_real_history_says_when_its_last_reading_came_in(self):
        self.home.add("2026-09-01T08:00:00", "door", "sensorState", "normal", "initial")
        self.home.add("2026-09-01T12:23:05", "door", "sensorState", "alarm")
        self.assertEqual(self.home.recording(simulated=False)["meta"]["updated"], "2026-09-01T12:23:05")
        self.assertNotIn("updated", self.home.recording()["meta"])

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

    def test_a_simulated_run_was_watched_the_whole_time(self):
        self.assertEqual(self.rec["gaps"], [])

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


class Writing(unittest.TestCase):
    def setUp(self):
        self.home = Home().device("door", "contactSensor", "Front door")
        self.addCleanup(self.home.close)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "web" / "live.json"

    def test_writing_replaces_the_file_whole_and_leaves_nothing_else(self):
        self.home.add("2026-09-01T08:00:00", "door", "sensorState", "normal", "initial")
        recording.write(self.home.store.db, self.out, simulated=False)
        self.home.add("2026-09-01T09:00:00", "door", "sensorState", "alarm")
        recording.write(self.home.store.db, self.out, simulated=False)
        self.assertEqual(json.loads(self.out.read_text())["meta"]["updated"], "2026-09-01T09:00:00")
        self.assertEqual([f.name for f in self.out.parent.iterdir()], ["live.json"])

    def test_watch_rebuilds_from_the_loops_snapshots_every_few_seconds(self):
        naps: list[float] = []
        code = recording.watch(MORNING, self.out, every=5, rounds=3, sleep=naps.append, simulated=False)
        self.assertEqual((code, naps), (0, [5, 5]))
        rec = json.loads(self.out.read_text())
        check(rec)
        last = [json.loads(line) for line in MORNING.read_text().splitlines() if line.strip()][-1]
        self.assertEqual(rec["meta"]["updated"], to_local(last["ts"]).strftime("%Y-%m-%dT%H:%M:%S"))
        self.assertGreater(len(rec["devices"]), 0)

    def test_watch_waits_for_the_loop_to_write_its_first_snapshot(self):
        naps: list[float] = []
        with self.assertLogs("source.recording", "WARNING"):
            code = recording.watch(Path(self._tmp.name) / "none.jsonl", self.out, every=5, rounds=2, sleep=naps.append)
        self.assertEqual((code, naps, self.out.exists()), (0, [5], False))

    def test_watch_waits_a_round_when_the_last_snapshot_is_half_written(self):
        snaps = Path(self._tmp.name) / "snaps.jsonl"
        snaps.write_text(MORNING.read_text() + '{"ts": 1')
        with self.assertLogs("source.recording", "WARNING"):
            recording.watch(snaps, self.out, every=5, rounds=1, sleep=lambda _: None)
        self.assertFalse(self.out.exists())

    def test_the_command_builds_once_from_snapshots_and_needs_a_source(self):
        self.assertEqual(recording.main(["--snapshots", str(MORNING), "--real", "--out", str(self.out)]), 0)
        self.assertFalse(json.loads(self.out.read_text())["meta"]["simulated"])
        for argv in (["--out", "x.json"], [".", "--snapshots", str(MORNING), "--out", "x.json"],
                     [".", "--every", "5", "--out", "x.json"]):
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                recording.main(argv)
