import unittest

from source.guard import Heat, check_write, verify

BULB, PLUG, CAMERA, HUB = "bulb-1", "plug-1", "cam-1", "hub-1"
DEVICES = {BULB: "bulb", PLUG: "plug", CAMERA: "camera", HUB: "gateway", "home-security": "security"}


def write(device_id, **body):
    return {"deviceId": device_id, "body": body}


def check(args, allow_plug_on=False):
    return check_write(args, DEVICES, allow_plug_on=allow_plug_on)


class CheckWriteTest(unittest.TestCase):
    def test_bulb_colour_allowed(self):
        self.assertIsNone(check(write(BULB, type="color_rgb", redValue=255, greenValue=166, blueValue=0)))

    def test_bulb_brightness_and_temperature_allowed(self):
        self.assertIsNone(check(write(BULB, type="brightness", value=60)))
        self.assertIsNone(check(write(BULB, type="temperature", value=0)))

    def test_plug_off_allowed(self):
        self.assertIsNone(check(write(PLUG, type="power", value="off")))

    def test_plug_on_blocked(self):
        self.assertIn("approval", check(write(PLUG, type="power", value="on")))

    def test_plug_on_allowed_when_flag_set(self):
        self.assertIsNone(check(write(PLUG, type="power", value="on"), allow_plug_on=True))

    def test_device_name_only_blocked(self):
        self.assertIsNotNone(check({"deviceName": "Light", "body": {"type": "power", "value": "off"}}))

    def test_extra_keys_blocked(self):
        self.assertIsNotNone(check({**write(BULB, type="power", value="off"), "vendor": "x"}))
        self.assertIsNotNone(check(write(BULB, type="temperature", value=20, operator=">")))

    def test_unknown_or_unwritable_device_blocked(self):
        for dev in ("nope", CAMERA, HUB, "home-security"):
            self.assertIsNotNone(check(write(dev, type="power", value="off")), dev)

    def test_plug_brightness_blocked(self):
        self.assertIsNotNone(check(write(PLUG, type="brightness", value=50)))

    def test_out_of_range_blocked(self):
        self.assertIsNotNone(check(write(BULB, type="brightness", value=0)))
        self.assertIsNotNone(check(write(BULB, type="brightness", value=101)))
        self.assertIsNotNone(check(write(BULB, type="temperature", value=-1)))
        self.assertIsNotNone(check(write(BULB, type="color_rgb", redValue=256, greenValue=0, blueValue=0)))

    def test_non_numbers_blocked(self):
        self.assertIsNotNone(check(write(BULB, type="brightness", value=True)))
        self.assertIsNotNone(check(write(BULB, type="brightness", value="50")))
        self.assertIsNotNone(check(write(BULB, type="color_rgb", redValue=255.5, greenValue=0, blueValue=0)))

    def test_bad_power_value_blocked(self):
        self.assertIsNotNone(check(write(BULB, type="power", value="toggle")))

    def test_missing_body_or_type_blocked(self):
        self.assertIsNotNone(check({"deviceId": BULB}))
        self.assertIsNotNone(check(write(BULB, value="on")))


TH = "th-1"
NOW_MS = 1_800_000_000_000
MIN = 60_000


def heat(**kw):
    return Heat(**{"now_ms": NOW_MS, "target": 18.0, "someone_home": True, **kw})


def thermo(value, typ="targetTemperature", **kw):
    return check_write(write(TH, type=typ, value=value), {**DEVICES, TH: "thermostat"}, allow_plug_on=False,
                       heat=heat(**kw))


class ThermostatTest(unittest.TestCase):
    """docs/contracts.md C7: the limits are code's, not the prompt's."""

    def test_within_limits(self):
        self.assertIsNone(thermo(21))
        self.assertIsNone(thermo(16.5))
        self.assertIsNone(thermo("eco", typ="mode"))

    def test_range_16_to_23(self):
        self.assertIsNotNone(thermo(15.5, target=16))
        self.assertIsNotNone(thermo(23.5, target=22))
        self.assertIsNotNone(thermo("21"))
        self.assertIsNotNone(thermo("boost", typ="mode"))

    def test_at_most_3_degrees_per_write(self):
        self.assertIn("3 °C", thermo(21.5, target=18))
        self.assertIsNone(thermo(21, target=18))
        self.assertIn("unknown", thermo(20, target=None))

    def test_never_above_18_with_no_one_home(self):
        self.assertIn("no one is home", thermo(19, someone_home=False))
        self.assertIn("no one is home", thermo(19, someone_home=None))
        self.assertIsNone(thermo(18, someone_home=False))
        # ...unless it warms the house ahead of a forecast arrival
        self.assertIsNone(thermo(21, someone_home=False, arrival_ms=NOW_MS + 25 * MIN))
        self.assertIsNotNone(thermo(21, someone_home=False, arrival_ms=NOW_MS + 5 * 60 * MIN))
        self.assertIsNotNone(thermo(21, someone_home=False, arrival_ms=NOW_MS - 5 * MIN))

    def test_one_write_per_10_min(self):
        self.assertIn("10 min", thermo(20, last_write_ms=NOW_MS - 9 * MIN))
        self.assertIsNone(thermo(20, last_write_ms=NOW_MS - 11 * MIN))

    def test_a_person_wins_for_2_h(self):
        self.assertIn("person", thermo(20, person_ms=NOW_MS - 119 * MIN))
        self.assertIn("person", thermo("eco", typ="mode", person_ms=NOW_MS - 30 * MIN))
        self.assertIsNone(thermo(20, person_ms=NOW_MS - 121 * MIN))

    def test_a_person_asking_skips_the_rate_and_hold(self):
        devices = {TH: "thermostat"}
        self.assertIsNone(check_write(write(TH, type="targetTemperature", value=20), devices, allow_plug_on=True,
                                      heat=heat(person_ms=NOW_MS - MIN, last_write_ms=NOW_MS - MIN)))

    def test_no_heat_no_write(self):
        """The LLM's own tool calls carry no Heat: the thermostat is changed only through proposals."""
        self.assertIn("proposal", check_write(write(TH, type="targetTemperature", value=20), {TH: "thermostat"},
                                              allow_plug_on=False))

    def test_thermostat_fields_only(self):
        self.assertIsNotNone(thermo(50, typ="brightness"))
        self.assertIsNotNone(check_write(write(BULB, type="targetTemperature", value=20), DEVICES,
                                         allow_plug_on=False, heat=heat()))


BLUE = {"type": "color_rgb", "redValue": 0, "greenValue": 0, "blueValue": 255}


def details(ts, **caps):
    out = [{"type": "color_rgb", "value": "{redValue},{greenValue},{blueValue}".format(**caps["rgb"]), **caps["rgb"]}
           ] if "rgb" in caps else []
    out += [{"type": k, "value": v} for k, v in caps.items() if k != "rgb"]
    return {"id": BULB, "type": "bulb", "lastCommunicationTime": ts, "capabilities": out}


class VerifyTest(unittest.TestCase):
    def test_colour_match_with_newer_report(self):
        self.assertTrue(verify(BLUE, 1000, details(2000, rgb={"redValue": 0, "greenValue": 0, "blueValue": 255})))

    def test_colour_mismatch(self):
        self.assertFalse(verify(BLUE, 1000, details(2000, rgb={"redValue": 255, "greenValue": 0, "blueValue": 0})))

    def test_value_match(self):
        self.assertTrue(verify({"type": "brightness", "value": 60}, 1000, details(2000, brightness=60)))

    def test_same_report_time_not_confirmed(self):
        self.assertFalse(verify({"type": "power", "value": "off"}, 2000, details(2000, power="off")))

    def test_no_before_time_not_confirmed(self):
        self.assertFalse(verify({"type": "power", "value": "off"}, None, details(2000, power="off")))

    def test_bad_details_not_confirmed(self):
        self.assertFalse(verify({"type": "power", "value": "off"}, 1000, None))
        self.assertFalse(verify({"type": "power", "value": "off"}, 1000, {"lastCommunicationTime": 2000}))

    def test_malformed_capabilities_not_confirmed(self):
        off = {"type": "power", "value": "off"}
        self.assertFalse(verify(off, 1000, {"lastCommunicationTime": 2000, "capabilities": None}))
        self.assertFalse(verify(off, 1000, {"lastCommunicationTime": 2000, "capabilities": ["power"]}))


if __name__ == "__main__":
    unittest.main()
