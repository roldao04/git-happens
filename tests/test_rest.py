"""Rest finder: each signal's resting state or level, learned from time held (starting-plan part 3)."""
import unittest

from source.learn.rest import observed_stretches
from source.model import Event

H = 3600_000
DOOR = ("c1", "sensorState")
PLUG = ("p1", "instantPower")
NAMES = {"c1": "Door", "p1": "Plug", "t1": "Temp"}


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


if __name__ == "__main__":
    unittest.main()
