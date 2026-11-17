import unittest

from source.diff import diff
from source.model import Event, Reading

STALE_MS = 60 * 60_000


def state(**caps):
    return {("p1", cap): Reading("p1", "Plug", cap, v, 1_000, "plug") for cap, v in caps.items()}


class GoneTest(unittest.TestCase):
    def test_key_missing_from_next_snapshot_is_gone(self):
        events = diff(state(power="on", instantPower=5), state(power="on"), 2_000, STALE_MS, 1_000)
        self.assertEqual([(e.device_id, e.capability, e.kind, e.old, e.new) for e in events],
                         [("p1", "instantPower", "gone", 5, None)])

    def test_gone_signal_returns_as_change(self):
        events = diff(state(power="on"), state(power="on", instantPower=7), 2_000, STALE_MS, 1_000)
        self.assertEqual([(e.capability, e.kind, e.old, e.new) for e in events],
                         [("instantPower", "change", None, 7)])

    def test_gone_reads_plainly(self):
        self.assertEqual(Event(0, "p1", "Plug", "instantPower", "gone", 5, None).describe(),
                         "Plug.instantPower is no longer reported")


if __name__ == "__main__":
    unittest.main()
