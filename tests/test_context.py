import copy
import json
import tempfile
import unittest
from pathlib import Path

from source.context import build_context
from source.normalize import HOME_SECURITY, normalize
from source.store import Store

LIVE = json.loads((Path(__file__).parent / "fixtures" / "get_home_live.json").read_text())
LIVE_TS = 1794913759185  # 17 Nov 11:09:19, door open
STALE_MS = 60 * 60_000
SMOKE = "e4566929-1477-4023-8095-af904a0befe3"
CONTACT = "276b9ad8-89ce-491a-a177-fe42358bee1c"
LIGHT = "90654832-7bca-47dd-89f3-d6e0b5f0a9ee"
PLUG = "a291277c-8ba5-480a-b3a3-35b5f49bb459"


class ContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name), record=False)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def state_of(self, home):
        return build_context(LIVE_TS, [], normalize(home), self.store, None, STALE_MS)["state"]

    def test_rules_just_ran_listed(self):
        ran = [{"trigger_id": "door-open", "actions": [{"deviceId": "x", "status": "sent"}]}]
        ctx = build_context(LIVE_TS, [], normalize(LIVE), self.store, None, STALE_MS, rules_ran=ran)
        self.assertEqual(ctx["rules_just_ran"], ran)
        self.assertEqual(build_context(LIVE_TS, [], normalize(LIVE), self.store, None, STALE_MS)["rules_just_ran"], [])

    def test_baselines_empty_when_nothing_is_learned(self):
        """Habits, forecast, occasions and decisions arrive once learned (tests/test_anticipate.py)."""
        ctx = build_context(LIVE_TS, [], normalize(LIVE), self.store, None, STALE_MS)
        self.assertEqual(ctx["baselines"], {})

    def test_smoke_marked_stale(self):
        smoke = self.state_of(LIVE)[SMOKE]
        self.assertIs(smoke["fresh"], False)
        self.assertGreater(smoke["staleMinutes"], 4000)
        self.assertEqual(smoke["type"], "smokeSensor")

    def test_live_devices_fresh(self):
        state = self.state_of(LIVE)
        for dev in (LIGHT, PLUG, CONTACT):
            self.assertIs(state[dev]["fresh"], True, dev)
            self.assertNotIn("staleMinutes", state[dev])
        self.assertEqual(state[LIGHT]["name"], "Light")
        self.assertEqual(state[HOME_SECURITY]["type"], "security")
        self.assertNotIn("fresh", state[HOME_SECURITY])

    def test_two_contact_sensors_kept_apart(self):
        home = copy.deepcopy(LIVE)
        old = copy.deepcopy(next(d for d in home["devices"] if d["id"] == CONTACT))
        old["id"], old["lastCommunicationTime"] = "old-contact", 1794625392000  # 14 Nov
        for c in old["capabilities"]:
            if c["type"] == "sensorState":
                c["value"] = "normal"
        home["devices"].append(old)

        state = self.state_of(home)
        self.assertEqual((state[CONTACT]["sensorState"], state[CONTACT]["fresh"]), ("alarm", True))
        self.assertEqual((state["old-contact"]["sensorState"], state["old-contact"]["fresh"]), ("normal", False))


if __name__ == "__main__":
    unittest.main()
