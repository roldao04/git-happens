import asyncio
import json
import unittest

from source.actions import resolve_targets, run_actions, slug

PLUG, BULB = "plug-1", "bulb-1"
DEVICES = {PLUG: "plug", BULB: "bulb"}
BLUE = {"type": "color_rgb", "redValue": 0, "greenValue": 0, "blueValue": 255}


class FakeActuator:
    """Answers writes and reads like the MCP; the device reports after each write."""

    def __init__(self):
        self.writes: list[dict] = []
        self.ts = 1000
        self.rgb = (255, 255, 255)

    async def call(self, tool: str, args: dict) -> str:
        if tool == "update_device_capabilities":
            self.writes.append(args)
            b = args["body"]
            self.rgb, self.ts = (b["redValue"], b["greenValue"], b["blueValue"]), self.ts + 1000
            return "{}"
        r, g, b = self.rgb
        return json.dumps({"lastCommunicationTime": self.ts, "capabilities": [
            {"type": "color_rgb", "value": f"{r},{g},{b}", "redValue": r, "greenValue": g, "blueValue": b}]})


def rule(device_id, body):
    return [{"tool": "update_device_capabilities", "args": {"deviceId": device_id, "body": body}}]


class RunActionsTest(unittest.TestCase):
    def test_rule_write_goes_through_guard(self):
        actuator = FakeActuator()
        out = asyncio.run(run_actions(actuator, "x", rule(PLUG, {"type": "power", "value": "on"}), DEVICES))
        self.assertEqual(actuator.writes, [])
        self.assertEqual((out[0]["status"], out[0]["error"]), ("blocked", True))

    def test_rule_write_read_back(self):
        actuator = FakeActuator()
        out = asyncio.run(run_actions(actuator, "x", rule(BULB, BLUE), DEVICES))
        self.assertEqual(len(actuator.writes), 1)
        self.assertEqual((out[0]["status"], out[0]["verified"], out[0]["error"]), ("sent", "confirmed", False))


class ResolveTargetsTest(unittest.TestCase):
    FRESH = {"plug": ["p-kettle", "p-tv"], "bulb": [BULB]}
    NAMES = {"p-kettle": "Kettle", "p-tv": "TV", "p-wash": "Washing machine", BULB: "Light"}

    def action(self, target):
        return {"tool": "update_device_capabilities", "target": target,
                "args": {"body": {"type": "power", "value": "off"}}}

    def test_a_type_or_a_named_device(self):
        ready, skipped = resolve_targets([self.action("bulb"), self.action("plug:tv")], self.FRESH, self.NAMES)
        self.assertEqual([a["args"]["deviceId"] for a in ready], [BULB, "p-tv"])
        self.assertEqual(skipped, [])

    def test_a_named_device_that_isnt_reporting_is_skipped(self):
        ready, skipped = resolve_targets([self.action("plug:washing-machine")], self.FRESH, self.NAMES)
        self.assertEqual(ready, [])
        self.assertEqual(skipped[0]["result"], "no fresh plug:washing-machine")

    def test_slug(self):
        self.assertEqual(slug("Washing machine"), "washing-machine")
        self.assertEqual(slug("TV"), "tv")


if __name__ == "__main__":
    unittest.main()
