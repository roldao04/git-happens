"""Rest finder: each signal's resting state or level, learned from time held (starting-plan part 3)."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from source.clock import day_bounds
from source.ingest import ingest, read_jsonl
from source.learn.rest import find_rest, observed_stretches, rest_from_store
from source.model import Event
from source.store import Store
from tests.test_ingest import DAY_MS, MORNING, at, plug

H = 3600_000
DOOR = ("c1", "sensorState")
PLUG = ("p1", "instantPower")
NAMES = {"c1": "Door", "p1": "Plug", "t1": "Temp", "cam": "Camera"}


def init(ts, sig, v):
    return Event(ts, sig[0], NAMES[sig[0]], sig[1], "initial", None, v)


def chg(ts, sig, old, new):
    return Event(ts, sig[0], NAMES[sig[0]], sig[1], "change", old, new)


def gone(ts, sig, old):
    return Event(ts, sig[0], NAMES[sig[0]], sig[1], "gone", old, None)


def gap(old, new):
    return Event(new, "loop", "Loop", "poll", "gap", old, new)


def stale(ts, dev, comm):
    return Event(ts, dev, NAMES[dev], "lastCommunicationTime", "stale", None, comm)


def returned(ts, dev, comm):
    return Event(ts, dev, NAMES[dev], "lastCommunicationTime", "returned", None, comm)


def spans(stretches, sig):
    return [(s.value, s.start, s.end) for s in stretches.get(sig, [])]


class StretchesTest(unittest.TestCase):
    def test_stretches_end_at_observed_until_not_day_end(self):
        got = observed_stretches([init(0, DOOR, "normal")], 10 * H)
        self.assertEqual(spans(got, DOOR), [("normal", 0, 10 * H)])
        self.assertEqual(got[DOOR][0].signal, "Door.sensorState")

    def test_gap_removes_time_for_all_signals(self):
        got = observed_stretches([init(0, DOOR, "normal"), init(0, PLUG, 0), gap(1 * H, 5 * H)], 6 * H)
        self.assertEqual(spans(got, DOOR), [("normal", 0, 1 * H), ("normal", 5 * H, 6 * H)])
        self.assertEqual(spans(got, PLUG), [(0, 0, 1 * H), (0, 5 * H, 6 * H)])

    def test_stale_removed_from_last_contact_until_returned(self):
        events = [init(0, DOOR, "normal"), init(0, PLUG, 0), stale(3 * H, "c1", 2 * H), returned(5 * H, "c1", 5 * H)]
        got = observed_stretches(events, 6 * H)
        self.assertEqual(spans(got, DOOR), [("normal", 0, 2 * H), ("normal", 5 * H, 6 * H)])
        self.assertEqual(spans(got, PLUG), [(0, 0, 6 * H)])

    def test_stale_without_return_ends_the_signal(self):
        got = observed_stretches([init(0, DOOR, "normal"), stale(3 * H, "c1", 2 * H)], 6 * H)
        self.assertEqual(spans(got, DOOR), [("normal", 0, 2 * H)])

    def test_initially_stale_with_contact_before_history(self):
        events = [init(10 * H, DOOR, "normal"), stale(10 * H, "c1", 1 * H), returned(12 * H, "c1", 12 * H)]
        self.assertEqual(spans(observed_stretches(events, 13 * H), DOOR), [("normal", 12 * H, 13 * H)])

    def test_change_and_returned_at_the_same_time(self):
        events = [init(0, DOOR, "normal"), stale(2 * H, "c1", 1 * H),
                  chg(4 * H, DOOR, "normal", "alarm"), returned(4 * H, "c1", 4 * H)]
        self.assertEqual(spans(observed_stretches(events, 5 * H), DOOR),
                         [("normal", 0, 1 * H), ("alarm", 4 * H, 5 * H)])

    def test_gone_ends_the_signal_until_it_comes_back(self):
        events = [init(0, PLUG, 5), gone(2 * H, PLUG, 5), chg(4 * H, PLUG, None, 7)]
        self.assertEqual(spans(observed_stretches(events, 5 * H), PLUG), [(5, 0, 2 * H), (7, 4 * H, 5 * H)])

    def test_none_is_unobserved(self):
        events = [init(0, PLUG, None), chg(1 * H, PLUG, None, 5)]
        self.assertEqual(spans(observed_stretches(events, 2 * H), PLUG), [(5, 1 * H, 2 * H)])

    def test_first_seen_by_a_change_without_initial(self):
        events = [chg(1 * H, PLUG, None, 5), chg(2 * H, PLUG, 5, 0)]
        self.assertEqual(spans(observed_stretches(events, 3 * H), PLUG), [(5, 1 * H, 2 * H), (0, 2 * H, 3 * H)])

    def test_nothing_at_or_after_observed_until(self):
        events = [init(0, DOOR, "normal"), chg(5 * H, DOOR, "normal", "alarm"), chg(6 * H, DOOR, "alarm", "normal")]
        self.assertEqual(spans(observed_stretches(events, 5 * H), DOOR), [("normal", 0, 5 * H)])

    def test_skipped_capabilities_and_loop_events_are_not_signals(self):
        events = [init(0, ("p1", "todayConsumption"), 0.1), init(0, ("p1", "multiPower"), "[]"),
                  init(0, ("p1", "instantVoltage"), 230), init(0, PLUG, 0), gap(1 * H, 2 * H),
                  stale(3 * H, "p1", 2 * H)]
        self.assertEqual(set(observed_stretches(events, 4 * H)), {PLUG})

    def test_out_of_order_events_rejected(self):
        with self.assertRaises(ValueError):
            observed_stretches([init(2 * H, DOOR, "normal"), chg(1 * H, DOOR, "normal", "alarm")], 3 * H)

    def test_empty_history(self):
        self.assertEqual(observed_stretches([], 1 * H), {})


class StateRestTest(unittest.TestCase):
    def test_rest_is_the_state_held_longest_not_the_most_frequent(self):
        events = [init(0, DOOR, "A"), chg(22 * H, DOOR, "A", "B")]
        prev, t = "B", 22 * H
        for nxt in ["C", "B"] * 6:  # B ends up with the most events, A with the most time
            t += 10 * 60_000
            events.append(chg(t, DOOR, prev, nxt))
            prev = nxt
        rest = find_rest(events, 24 * H)[DOOR]
        self.assertEqual((rest.kind, rest.value, rest.observed_ms), ("state", "A", 24 * H))
        self.assertAlmostEqual(rest.share, 22 / 24)
        self.assertTrue(rest.at_rest("A"))
        self.assertFalse(rest.at_rest("B"))

    def test_bool_is_a_state_not_a_number(self):
        sig = ("cam", "lightIndicator")
        rest = find_rest([init(0, sig, True), chg(20 * H, sig, True, False)], 24 * H)[sig]
        self.assertEqual(rest.kind, "state")
        self.assertIs(rest.value, True)

    def test_gap_time_does_not_count(self):
        events = [init(0, DOOR, "alarm"), gap(1 * H, 11 * H), chg(11 * H, DOOR, "alarm", "normal")]
        rest = find_rest(events, 13 * H)[DOOR]
        self.assertEqual((rest.value, rest.observed_ms), ("normal", 3 * H))


def series(sig, start, steps):
    """Events for a signal holding each (value, ms) in turn; returns (events, end)."""
    events, prev, t = [], None, start
    for value, ms in steps:
        events.append(init(t, sig, value) if prev is None and t == start else chg(t, sig, prev, value))
        prev, t = value, t + ms
    return events, t


class LevelRestTest(unittest.TestCase):
    def test_flat_plug_rests_at_zero(self):
        burst = [(2140, 3 * 60_000), (0, 4 * H)]
        events, end = series(PLUG, 0, [(0, 8 * H)] + burst * 3)
        rest = find_rest(events, end)[PLUG]
        self.assertEqual((rest.kind, rest.value, rest.step, rest.wobble), ("level", 0, 1, 1))
        self.assertTrue(rest.at_rest(0.5))
        self.assertFalse(rest.at_rest(2140))

    def test_noisy_idle_beats_a_busy_burst(self):
        idle = [(w, 4 * H) for w in (80, 78, 82, 79, 81)]
        burst = [(2000 + 10 * i, 9_000) for i in range(20)]  # 20 readings in 3 min
        events, end = series(PLUG, 0, idle + burst + [(80, 1 * H)])
        rest = find_rest(events, end)[PLUG]
        self.assertTrue(78 <= rest.value <= 82)
        self.assertTrue(all(rest.at_rest(w) for w in range(78, 83)))
        self.assertFalse(rest.at_rest(2100))
        self.assertGreater(rest.share, 0.99)

    def test_three_levels_never_pick_the_one_held_least(self):
        events, end = series(PLUG, 0, [(0, 4 * H), (60, 2 * H), (80, 4 * H)])
        self.assertEqual(find_rest(events, end)[PLUG].value, 0)

    def test_decimal_resolution(self):
        sig = ("p1", "instantCurrent")
        events, end = series(sig, 0, [(0.0, 20 * H), (9.3, 1 * H), (0.0, 3 * H)])
        rest = find_rest(events, end)[sig]
        self.assertAlmostEqual(rest.step, 0.1)
        self.assertTrue(rest.at_rest(0.05))
        self.assertFalse(rest.at_rest(9.3))

    def test_non_numbers_are_never_at_rest_for_a_level(self):
        events, end = series(PLUG, 0, [(0, 4 * H), (5, 1 * H)])
        rest = find_rest(events, end)[PLUG]
        self.assertFalse(rest.at_rest("on"))
        self.assertFalse(rest.at_rest(None))
        self.assertFalse(rest.at_rest(False))

    def test_numbers_mixed_with_text_are_a_state(self):
        events, end = series(PLUG, 0, [(0, 4 * H), ("off", 1 * H)])
        self.assertEqual(find_rest(events, end)[PLUG].kind, "state")


TEMP = ("t1", "temperatureValue")
HALF_H = H // 2


class SlowRestTest(unittest.TestCase):
    def test_temperature_rest_is_its_normal_drift(self):
        wiggle = [(20.0, HALF_H), (20.1, HALF_H)] * 20
        events, end = series(TEMP, 0, wiggle + [(22.1, HALF_H), (20.1, 2 * H)])
        rest = find_rest(events, end)[TEMP]
        self.assertEqual(rest.kind, "slow")
        self.assertAlmostEqual(rest.rate_per_h, 0.2)
        self.assertTrue(rest.rate_at_rest(0.1, HALF_H))
        self.assertTrue(rest.rate_at_rest(0.0, 2 * H))
        self.assertFalse(rest.rate_at_rest(2.0, HALF_H))
        self.assertGreater(rest.share, 0.9)

    def test_rate_never_measured_across_a_gap(self):
        events = [init(0, TEMP, 20.0), gap(1 * H, 2 * H), chg(2 * H, TEMP, 20.0, 25.0),
                  chg(2 * H + HALF_H, TEMP, 25.0, 25.1)]
        rest = find_rest(events, 3 * H)[TEMP]
        self.assertAlmostEqual(rest.rate_per_h, 0.2)

    def test_slow_signal_has_no_level_check(self):
        events, end = series(TEMP, 0, [(20.0, H), (20.1, H)])
        with self.assertRaises(ValueError):
            find_rest(events, end)[TEMP].at_rest(20.0)

    def test_rate_check_only_for_slow_signals_over_positive_time(self):
        level, end = series(PLUG, 0, [(0, H), (5, H)])
        with self.assertRaises(ValueError):
            find_rest(level, end)[PLUG].rate_at_rest(1, H)
        slow, end = series(TEMP, 0, [(20.0, H), (20.1, H)])
        with self.assertRaises(ValueError):
            find_rest(slow, end)[TEMP].rate_at_rest(0.1, 0)


class FromStoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name), record=False)
        self.addCleanup(self.store.close)

    def test_events_and_snapshots_before_a_time(self):
        ingest([plug(at(2026, 9, 1, 8), 0), plug(at(2026, 9, 1, 9), 5)], self.store, stale_ms=DAY_MS)
        self.assertEqual({e.ts for e in self.store.events_before(at(2026, 9, 1, 9))}, {at(2026, 9, 1, 8)})
        self.assertEqual(self.store.last_snapshot_before(at(2026, 9, 1, 9)), at(2026, 9, 1, 8))
        self.assertIsNone(self.store.last_snapshot_before(at(2026, 9, 1, 8)))

    def test_rest_from_the_ingested_morning(self):
        ingest(read_jsonl(MORNING), self.store, stale_ms=H)
        last = list(read_jsonl(MORNING))[-1][0]
        rests = rest_from_store(self.store, last + 1)
        door = rests[(self.store.devices_of_type("contactSensor")[0], "sensorState")]
        power = rests[(self.store.devices_of_type("plug")[0], "instantPower")]
        self.assertEqual((door.kind, door.value), ("state", "normal"))
        self.assertLess(door.share, 1)  # the door did open
        self.assertEqual((power.kind, power.value), ("level", 0))
        self.assertLess(power.share, 1)  # the kettle did run
        self.assertFalse(power.at_rest(2140))
        self.assertFalse({"todayConsumption", "instantVoltage"} & {cap for _, cap in rests})

    def test_a_day_without_data_adds_no_time(self):
        ingest([plug(at(2026, 9, 1, 8), 0), plug(at(2026, 9, 1, 9), 0)], self.store, stale_ms=DAY_MS,
               gap_ms=DAY_MS)
        rest = rest_from_store(self.store, day_bounds(date(2026, 9, 2))[1])[PLUG]
        self.assertEqual(rest.observed_ms, 1 * H)

    def test_nothing_stored_yet(self):
        self.assertEqual(rest_from_store(self.store, at(2026, 9, 1, 8)), {})


if __name__ == "__main__":
    unittest.main()
