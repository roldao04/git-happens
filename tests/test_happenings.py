"""Happening extractor: departures from rest, one row each, appended day by day (docs/contracts.md part 4)."""
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
from source.ingest import ingest, read_jsonl
from source.learn import Happening as LearnHappening
from source.learn import activity_edges, device_type, is_activity, signal_devices, slug
from source.learn.habits import quiet_happenings
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


DEMO = Path(__file__).parent / "fixtures" / "demo-snapshots.jsonl"


class SignalNamesTest(unittest.TestCase):
    """docs/contracts.md, C2: one signal per device when a type has several, one per phone, a count of guests."""

    def test_device_type(self):
        self.assertEqual(device_type("plug:washing-machine.instantPower"), "plug")
        self.assertEqual(device_type("bulb.power"), "bulb")
        self.assertEqual(device_type("phone:c-8f3a.connected"), "phone")
        self.assertEqual(device_type("home.quiet"), "home")
        self.assertEqual(device_type("plug:kettle"), "plug")

    def test_slug(self):
        self.assertEqual([slug(n) for n in ("Kettle", "TV", "Washing machine", "Mr. Plug  #2")],
                         ["kettle", "tv", "washing-machine", "mr-plug-2"])

    def test_one_device_of_a_type_keeps_the_old_name(self):
        names = signal_devices([("p1", "plug", "Plug"), ("b1", "bulb", "Light")])
        self.assertEqual(names, {"p1": "plug", "b1": "bulb"})

    def test_several_devices_of_a_type_get_their_names(self):
        names = signal_devices([("p1", "plug", "Kettle"), ("p2", "plug", "TV"), ("p3", "plug", "Washing machine"),
                                ("b1", "bulb", "Light")])
        self.assertEqual(names, {"p1": "plug:kettle", "p2": "plug:tv", "p3": "plug:washing-machine", "b1": "bulb"})

    def test_a_repaired_device_is_still_one_device(self):
        self.assertEqual(signal_devices([("p1", "plug", "Plug"), ("p2", "plug", "Plug")]), {"p1": "plug", "p2": "plug"})

    def test_a_phone_is_always_named_by_its_client_id(self):
        names = signal_devices([("c-8f3a", "phone", "Ana's phone"), ("rt-1", "router", "Router")])
        self.assertEqual(names, {"c-8f3a": "phone:c-8f3a", "rt-1": "router"})

    def test_a_happening_knows_its_device_type(self):
        h = LearnHappening("plug:kettle.instantPower", datetime(2026, 11, 19, 18), datetime(2026, 11, 19, 18, 3))
        self.assertEqual((h.device, h.capability), ("plug", "instantPower"))


def _at(hh, mm):
    return int(datetime(2026, 11, 19, hh, mm, tzinfo=TZ).timestamp() * 1000)


class DemoSnapshotsTest(unittest.TestCase):
    """A hand-made evening (tests/fixtures/demo-snapshots.jsonl): 3 plugs, 2 known phones, 3 guests, a thermostat."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(cls.tmp.name), record=False)
        ingest(read_jsonl(DEMO), store, stale_ms=60 * 60_000)
        save_happenings(store, _at(23, 59))
        cls.found = stored_happenings(store.db)
        store.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def of(self, signal):
        return [h for h in self.found if h.signal == signal]

    def test_each_plug_is_its_own_signal(self):
        [kettle] = self.of("plug:kettle.instantPower")
        self.assertEqual((kettle.start_ms, kettle.size), (_at(18, 50), 2100))
        [tv] = self.of("plug:tv.instantPower")
        self.assertEqual((tv.start_ms, tv.end_ms), (_at(21, 0), _at(22, 20)))
        self.assertGreater(len(self.of("plug:washing-machine.instantPower")), 1)
        self.assertFalse(self.of("plug.instantPower"))

    def test_single_devices_keep_the_old_names(self):
        self.assertEqual(len(self.of("contactSensor.sensorState")), 5)
        [target] = self.of("thermostat.targetTemperature")
        self.assertEqual((target.start_ms, target.end_ms, target.size), (_at(18, 42), _at(22, 20), 21))

    def test_a_phone_is_on_while_connected_and_rests_off(self):
        [joao] = self.of("phone:c-2b71.connected")
        self.assertEqual((joao.start_ms, joao.end_ms, joao.value, joao.cut), (_at(19, 30), _at(22, 0), "on", False))
        # Ana is home most of the time watched, yet her rest is "off": still connected at the end, so not written
        self.assertFalse(self.of("phone:c-8f3a.connected"))

    def test_guests(self):
        guests = [h for h in self.found if h.signal.startswith("phone:") and h.start_ms == _at(20, 0)]
        self.assertEqual(sorted(h.device_id for h in guests), ["c-55b4", "c-91d0", "c-a7e2"])
        [count] = self.of("router.guestPhones")
        self.assertEqual((count.start_ms, count.end_ms, count.size), (_at(20, 0), _at(21, 20), 3))

    def test_the_laptop_is_not_a_phone(self):
        self.assertFalse([h for h in self.found if h.device_id == "c-0e19"])

    def test_activity_and_slow(self):
        by_signal = {h.signal: LearnHappening(h.signal, datetime(2026, 11, 19), datetime(2026, 11, 19, 1))
                     for h in self.found}
        self.assertTrue(is_activity(by_signal["phone:c-2b71.connected"]))
        self.assertTrue(is_activity(by_signal["router.guestPhones"]))
        self.assertTrue(is_activity(by_signal["thermostat.targetTemperature"]))
        self.assertFalse(is_activity(by_signal["thermostat.temperatureValue"]))
        self.assertFalse(is_activity(by_signal["thermostat.heatingState"]))  # the thermostat switches it itself


class PhoneTest(unittest.TestCase):
    TYPES = {"c-8f3a": "phone:c-8f3a"}

    def test_a_phone_gone_from_the_list_reads_off(self):
        # a history stored without normalize's prev: the phone's reading is "gone" when it leaves
        events = [Event(0, "c-8f3a", "Ana's phone", "connected", "initial", None, "on"),
                  Event(2 * H, "c-8f3a", "Ana's phone", "connected", "gone", "on", None),
                  Event(5 * H, "c-8f3a", "Ana's phone", "connected", "change", None, "on"),
                  Event(23 * H, "c-8f3a", "Ana's phone", "connected", "gone", "on", None)]
        rests = find_rest(events, 24 * H)
        self.assertEqual(rests[("c-8f3a", "connected")].value, "off")
        got = [(h.signal, h.start_ms, h.end_ms, h.cut) for h in find_happenings(events, rests, self.TYPES, 24 * H)]
        self.assertEqual(got, [("phone:c-8f3a.connected", 0, 2 * H, False),
                               ("phone:c-8f3a.connected", 5 * H, 23 * H, False)])


class AlfredTest(unittest.TestCase):
    TYPES = {"th-1": "thermostat"}

    def target(self, source):
        events = [
            Event(0, "th-1", "Thermostat", "targetTemperature", "initial", None, 16.0),
            Event(8 * H, "th-1", "Thermostat", "targetTemperature", "change", 16.0, 21.0, source),
            Event(9 * H, "th-1", "Thermostat", "targetTemperature", "change", 21.0, 16.0)]
        return find_happenings(events, find_rest(events, 24 * H), self.TYPES, 24 * H)

    def test_a_persons_change_is_a_happening(self):
        self.assertEqual([h.start_ms for h in self.target("unknown")], [8 * H])
        self.assertEqual([h.start_ms for h in self.target("human")], [8 * H])

    def test_alfreds_own_write_is_not(self):
        self.assertEqual(self.target("agent"), [])
        self.assertEqual(self.target("scenario:night"), [])


class EdgesTest(unittest.TestCase):
    def test_a_long_presence_is_only_its_edges(self):
        day = datetime(2026, 11, 19)
        phone = LearnHappening("phone:c-8f3a.connected", day.replace(hour=18, minute=40),
                               day.replace(hour=23, minute=50), value="on")
        lamp = LearnHappening("bulb.power", day.replace(hour=19), day.replace(hour=23), value="on")
        cut = LearnHappening("phone:c-2b71.connected", day.replace(hour=20), day.replace(hour=21), cut=True)
        got = [(h.signal, h.start.strftime("%H:%M"), h.duration_s) for h in activity_edges([phone, lamp, cut])]
        self.assertEqual(got, [("phone:c-8f3a.connected", "18:40", 0), ("bulb.power", "19:00", 4 * 3600),
                               ("phone:c-2b71.connected", "20:00", 0), ("phone:c-8f3a.connected", "23:50", 0)])

    def test_with_edges_the_night_is_quiet(self):
        day = datetime(2026, 11, 19)
        phone = LearnHappening("phone:c-8f3a.connected", day.replace(hour=18), day + timedelta(hours=32), value="on")
        lamp = LearnHappening("bulb.power", day.replace(hour=19), day.replace(hour=23), value="on")
        kettle = LearnHappening("plug.instantPower", day + timedelta(hours=31), day + timedelta(hours=31, minutes=3),
                                size=2100)
        self.assertEqual(quiet_happenings([phone, lamp, kettle]), [])
        [night] = [q for q in quiet_happenings(activity_edges([phone, lamp, kettle])) if q.duration_s > 3600]
        self.assertEqual((night.start, night.end), (day.replace(hour=23), day + timedelta(hours=31)))


if __name__ == "__main__":
    unittest.main()
