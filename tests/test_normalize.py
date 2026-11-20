import json
import unittest
from pathlib import Path

from source.normalize import HOME_SECURITY, device_types, normalize

LIVE = json.loads((Path(__file__).parent / "fixtures" / "get_home_live.json").read_text())
LIVE_TS = 1794913759185  # 17 Nov 11:09:19, door open
CONTACT = "276b9ad8-89ce-491a-a177-fe42358bee1c"
LIGHT = "90654832-7bca-47dd-89f3-d6e0b5f0a9ee"


class NormalizeTest(unittest.TestCase):
    def test_reading_has_type(self):
        state = normalize(LIVE)
        self.assertEqual(state[(CONTACT, "sensorState")].device_type, "contactSensor")
        self.assertEqual(state[(HOME_SECURITY, "status")].device_type, "security")

    def test_device_types_map(self):
        types = device_types(normalize(LIVE))
        self.assertEqual(len(types), 8)  # 7 devices + the alarm
        self.assertEqual(types[LIGHT], "bulb")
        self.assertEqual(types[HOME_SECURITY], "security")


def router(*clients, comm=5):
    """A home with only the router (docs/contracts.md, C1), its clients as (id, name, known) or dicts."""
    value = [c if isinstance(c, dict) else {"id": c[0], "name": c[1], "kind": "phone", "known": c[2]}
             for c in clients]
    return {"devices": [{"id": "rt-1", "name": "Router", "type": "router", "lastCommunicationTime": comm,
                         "capabilities": [{"type": "connectedClients", "value": value}]}]}


ANA = ("c-8f3a", "Ana's phone", True)
GUEST = ("c-91d0", "", False)


class RouterTest(unittest.TestCase):
    def test_one_reading_per_phone_and_a_count_of_guests(self):
        state = normalize(router(ANA, GUEST, ("c-55b4", "", False)))
        ana = state[("c-8f3a", "connected")]
        self.assertEqual((ana.value, ana.device, ana.device_type, ana.comm_ts), ("on", "Ana's phone", "phone", 5))
        self.assertEqual(state[("c-91d0", "connected")].device, "")
        guests = state[("rt-1", "guestPhones")]
        self.assertEqual((guests.value, guests.device_type), (2, "router"))
        self.assertNotIn(("rt-1", "connectedClients"), state)  # the raw list is not a reading

    def test_only_phones_count(self):
        laptop = {"id": "c-0e19", "name": "Laptop", "kind": "laptop", "known": False}
        state = normalize(router(ANA, laptop))
        self.assertNotIn(("c-0e19", "connected"), state)
        self.assertEqual(state[("rt-1", "guestPhones")].value, 0)

    def test_no_one_home_is_zero_guests(self):
        state = normalize(router())
        self.assertEqual(list(state), [("rt-1", "guestPhones")])
        self.assertEqual(state[("rt-1", "guestPhones")].value, 0)

    def test_a_phone_that_left_reads_off_given_the_state_before(self):
        before = normalize(router(ANA, GUEST))
        after = normalize(router(GUEST, comm=9), before)
        self.assertEqual(after[("c-8f3a", "connected")].value, "off")
        self.assertEqual(after[("c-8f3a", "connected")].device, "Ana's phone")
        self.assertEqual(after[("c-8f3a", "connected")].comm_ts, 9)  # heard through the router, now
        again = normalize(router(ANA, comm=12), after)
        self.assertEqual((again[("c-8f3a", "connected")].value, again[("c-91d0", "connected")].value), ("on", "off"))

    def test_without_the_state_before_a_phone_that_left_is_simply_not_there(self):
        self.assertNotIn(("c-8f3a", "connected"), normalize(router(GUEST)))

    def test_demo_fixture_reads_in_the_contract_shapes(self):
        line = (Path(__file__).parent / "fixtures" / "demo-snapshots.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        state = normalize(json.loads(line)["home"])
        self.assertEqual(state[("th-1", "targetTemperature")].value, 16.0)
        self.assertEqual(state[("th-1", "heatingState")].device_type, "thermostat")
        self.assertEqual(sorted(r.device for r in state.values() if r.device_type == "plug" and
                                r.capability == "instantPower"), ["Kettle", "TV", "Washing machine"])
        self.assertEqual(state[("c-8f3a", "connected")].value, "on")


if __name__ == "__main__":
    unittest.main()
