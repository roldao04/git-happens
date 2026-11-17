import unittest

from source.guard import check_write, verify

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
