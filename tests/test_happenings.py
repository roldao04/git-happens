"""Happening extractor: departures from rest, one row each, appended day by day (starting-plan part 4)."""
import json
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from source import pipeline
from source.clock import TZ, day_bounds
from source.learn.happenings import find_happenings, save_happenings, stored_happenings
from source.learn.rest import find_rest
from source.model import Event
from source.simulate import generate
from source.store import Store

H = 3600_000
S = 1000
NAMES = {"p1": "Plug", "c1": "Door", "b1": "Light", "t1": "Temp"}
TYPES = {"p1": "plug", "c1": "contactSensor", "b1": "bulb", "t1": "temperatureSensor"}
START = date(2026, 9, 1)


def init(ts, dev, cap, v):
    return Event(ts, dev, NAMES[dev], cap, "initial", None, v)


def chg(ts, dev, cap, old, new):
    return Event(ts, dev, NAMES[dev], cap, "change", old, new)


def gap(old, new):
    return Event(new, "loop", "Loop", "poll", "gap", old, new)


def series(dev, cap, points):
    """[(ts, value), ...] -> an initial event and the changes."""
    out = [init(points[0][0], dev, cap, points[0][1])]
    out += [chg(ts, dev, cap, a, b) for (_, a), (ts, b) in zip(points, points[1:])]
    return out


def happenings(events, until=24 * H):
    events = sorted(events, key=lambda e: e.ts)
    return find_happenings(events, find_rest(events, until), TYPES, until)


KETTLE = [(10 * H, 2100), (10 * H + 30 * S, 2108), (10 * H + 60 * S, 2095), (10 * H + 90 * S, 2103),
          (10 * H + 120 * S, 2100), (10 * H + 150 * S, 2098), (10 * H + 180 * S, 0)]


class FindTest(unittest.TestCase):
    def test_a_kettle_with_wobble_is_one_happening(self):
        [h] = happenings(series("p1", "instantPower", [(0, 0)] + KETTLE))
        self.assertEqual((h.signal, h.start_ms, h.end_ms, h.duration_s), ("plug.instantPower", 10 * H, 10 * H + 180 * S, 180))
        self.assertAlmostEqual(h.size, 2100, delta=5)
        self.assertGreaterEqual(h.peak, h.size)
        self.assertEqual(h.peak, 2108)
        self.assertFalse(h.cut)
        self.assertIsNone(h.value)

    def test_size_ignores_a_short_ramp(self):
        ramp = [(0, 0), (10 * H, 300), (10 * H + 5 * S, 2100), (10 * H + 180 * S, 150), (10 * H + 185 * S, 0)]
        [h] = happenings(series("p1", "instantPower", ramp))
        self.assertEqual(h.size, 2100)

    def test_a_door_opening_is_one_happening_with_its_state(self):
        [h] = happenings(series("c1", "sensorState", [(0, "normal"), (10 * H, "alarm"), (10 * H + 60 * S, "normal")]))
        self.assertEqual((h.signal, h.value, h.duration_s, h.size, h.peak), ("contactSensor.sensorState", "alarm", 60, None, None))

    def test_a_gap_cuts_it_at_the_last_observed_moment(self):
        events = series("p1", "instantPower", [(0, 0), (10 * H, 2100), (11 * H + 60 * S, 0)])
        first, second = happenings(events + [gap(10 * H + 60 * S, 11 * H)])
        self.assertEqual((first.start_ms, first.end_ms, first.cut), (10 * H, 10 * H + 60 * S, True))
        self.assertEqual((second.start_ms, second.end_ms, second.cut), (11 * H, 11 * H + 60 * S, False))

    def test_still_open_at_the_end_is_not_written(self):
        self.assertEqual(happenings(series("p1", "instantPower", [(0, 0), (23 * H, 2100)])), [])

    def test_derived_signals_and_lamp_settings_give_none(self):
        events = (series("p1", "instantCurrent", [(0, 0), (10 * H, 9130), (10 * H + 180 * S, 0)])
                  + series("b1", "brightness", [(0, 100), (10 * H, 40), (10 * H + 600 * S, 100)])
                  + series("b1", "power", [(0, "off"), (10 * H, "on"), (10 * H + 600 * S, "off")]))
        self.assertEqual([h.signal for h in happenings(events)], ["bulb.power"])

    def test_a_slow_signal_moving_fast_is_one_happening_with_the_total_change(self):
        calm = [(i * H, 20 + 0.1 * (i % 2)) for i in range(10)]  # wobbles 0.1 an hour
        jump = [(10 * H, 20.5), (10 * H + 300 * S, 21.0), (10 * H + 600 * S, 21.5)]
        after = [(10 * H + 600 * S + i * H, 21.5 + 0.1 * (i % 2)) for i in range(1, 10)]
        [h] = happenings(series("t1", "temperatureValue", calm + jump + after))
        self.assertEqual(h.signal, "temperatureSensor.temperatureValue")
        self.assertEqual((h.start_ms, h.end_ms), (9 * H, 10 * H + 600 * S))
        self.assertAlmostEqual(h.size, 1.4)

    def test_json_has_lisbon_times(self):
        start = int(datetime(2026, 9, 1, 8, 21, tzinfo=TZ).timestamp() * 1000)
        [h] = happenings(series("p1", "instantPower", [(start - 10 * H, 0)] + [(start - 10 * H + t, v) for t, v in KETTLE]),
                         until=start + 10 * H)
        got = h.to_json()
        self.assertEqual((got["start"], got["end"], got["duration_s"], got["kind"], got["cut"]),
                         ("2026-09-01T08:21:00", "2026-09-01T08:24:00", 180, None, 0))
        self.assertEqual(set(got), {"signal", "start", "end", "duration_s", "size", "kind", "device_id", "capability",
                                    "value", "peak", "cut"})


def _simulated(test: unittest.TestCase, days: int, **kw) -> tuple[Path, list[dict]]:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    lines, truth = generate("rosa", days, start=START, **kw)
    snaps = ((row["ts"], row["home"]) for row in map(json.loads, lines))
    with test.assertLogs("source.pipeline", "INFO") as logs:
        test.assertEqual(pipeline.run(snaps, Path(tmp.name)), days)
    test.day_lines = [line for line in logs.output if " new happenings" in line]
    return Path(tmp.name), truth


class SaveTest(unittest.TestCase):
    def test_saving_again_adds_nothing_and_keeps_the_ids(self):
        folder, _ = _simulated(self, 3)
        store = Store(folder, record=False)
        self.addCleanup(store.close)
        before = [(h.id, h.signal, h.start_ms, h.end_ms) for h in stored_happenings(store.db)]
        self.assertTrue(before)
        until = day_bounds(START + timedelta(days=2))[1]
        self.assertEqual(save_happenings(store, until), 0)
        self.assertEqual(save_happenings(store, until), 0)
        self.assertEqual([(h.id, h.signal, h.start_ms, h.end_ms) for h in stored_happenings(store.db)], before)

    def test_fourteen_days_of_rosa(self):
        folder, truth = _simulated(self, 14)
        self.assertEqual(len(self.day_lines), 14)
        store = Store(folder, record=False)
        self.addCleanup(store.close)
        found = stored_happenings(store.db)
        local = lambda ms: datetime.fromtimestamp(ms / 1000, TZ)

        kettle = [h for h in found if h.signal == "plug.instantPower"]
        self.assertEqual([local(h.start_ms).strftime("%Y-%m-%dT%H:%M:%S") for h in kettle],
                         [t["start"] for t in truth if t["what"] == "morning use"])
        for h in kettle:
            self.assertTrue(1950 <= h.size <= 2250 and 100 <= h.duration_s <= 280, (h.size, h.duration_s))
        self.assertTrue(all(h.value == "alarm" for h in found if h.signal == "contactSensor.sensorState"))
        self.assertTrue(any(h.signal == "bulb.power" and h.value == "on" for h in found))
        for h in found:
            if h.signal in ("plug.instantPower", "contactSensor.sensorState", "bulb.power"):
                for t in (local(h.start_ms), local(h.end_ms)):
                    self.assertFalse(1 <= t.hour < 6, (h.signal, t))
                self.assertLess(h.duration_s, 6 * 3600)


class PipelineTest(unittest.TestCase):
    def test_runs_the_learners_when_they_are_there(self):
        calls = []

        def learn_day(db, day):
            db.execute("CREATE TABLE IF NOT EXISTS sensors (id TEXT)")
            db.execute("INSERT INTO sensors VALUES (?)", (day.isoformat(),))
            calls.append(day)

        fake = types.ModuleType("source.learn.run")
        fake.learn_day = learn_day
        with mock.patch.dict(sys.modules, {"source.learn.run": fake}):
            _simulated(self, 2)
        self.assertEqual(calls, [START, START + timedelta(days=1)])
        self.assertTrue(all("  1 new sensors" in line for line in self.day_lines), self.day_lines)

    def test_refuses_an_existing_history_without_replace(self):
        folder, _ = _simulated(self, 1)
        with self.assertRaises(FileExistsError):
            pipeline.run(iter(()), folder)


if __name__ == "__main__":
    unittest.main()
