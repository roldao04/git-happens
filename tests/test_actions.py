import asyncio
import json
import unittest

from source.actions import run_actions

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


if __name__ == "__main__":
    unittest.main()
