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


if __name__ == "__main__":
    unittest.main()
