"""LangGraphAgent driven by a scripted fake chat model and a fake house (no network, no API keys).

  uv run python -m unittest discover -s tests -t . -v
"""
import json
import unittest
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import ToolException, tool

from source.agent import REPORT_ASK, LangGraphAgent, parse_report

BULB, PLUG = "bulb-1", "plug-1"
CTX = {
    "now": "2026-11-17T10:00:00",
    "reasons": [{"trigger_id": "person", "reason": "a person asked"}],
    "state": {BULB: {"name": "Light", "type": "bulb", "fresh": True, "power": "on"},
              PLUG: {"name": "Plug", "type": "plug", "fresh": True, "power": "off"}},
    "events_since_last_run": [],
    "timeline_today": [],
    "baselines": {},
}
BLUE = {"type": "color_rgb", "redValue": 0, "greenValue": 0, "blueValue": 255}


def call(name: str, args: dict, id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id, "type": "tool_call"}])


class ScriptedModel(BaseChatModel):
    """Replays `script` in order (repeating the last message once exhausted)."""
    script: list[Any]
    step: int = 0
    seen: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen = list(messages)
        if messages[-1].content == REPORT_ASK:  # the report step: answer it, don't consume the script
            msg = AIMessage(content='Here: {"notes": "report-notes", "trigger_updates": []}')
            return ChatResult(generations=[ChatGeneration(message=msg)])
        msg = self.script[min(self.step, len(self.script) - 1)]
        self.step += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self._generate(messages, stop)

    def bind_tools(self, tools, **kwargs):
        return self


class FakeHouse:
    """The two MCP tools the agent uses, over one in-memory device.

    `reports=False` = the device never sends a newer lastCommunicationTime (offline or slow).
    """

    def __init__(self, write_result: str = '{"ok": true}', reports: bool = True, rejects: str = ""):
        self.calls: list[str] = []
        self.ts = 1000
        self.caps: dict = {"power": "on", "rgb": (255, 255, 255)}
        house = self

        @tool
        async def update_device_capabilities(deviceId: str, body: dict) -> str:
            """Set a capability."""
            house.calls.append("update")
            if rejects:  # like an MCP isError reply: the adapter returns it as content, not an exception
                raise ToolException(rejects)
            if "SH_10500" not in write_result:
                if body["type"] == "color_rgb":
                    house.caps["rgb"] = (body["redValue"], body["greenValue"], body["blueValue"])
                else:
                    house.caps[body["type"]] = body["value"]
                if reports:
                    house.ts += 1000
            return write_result

        @tool
        def get_device_details(deviceId: str) -> str:
            """Read a device."""
            house.calls.append("details")
            r, g, b = house.caps["rgb"]
            caps = [{"type": "color_rgb", "value": f"{r},{g},{b}", "redValue": r, "greenValue": g, "blueValue": b}]
            caps += [{"type": k, "value": v} for k, v in house.caps.items() if k != "rgb"]
            return json.dumps({"id": deviceId, "lastCommunicationTime": house.ts, "capabilities": caps})

        update_device_capabilities.handle_tool_error = True
        self.tools = [update_device_capabilities, get_device_details]

    async def provider(self):
        return self.tools


def agent(model, house, **kw):
    return LangGraphAgent(model, house.provider, readback_delay_s=0, **kw)


class AgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_write_confirmed_by_code(self):
        house = FakeHouse()
        model = ScriptedModel(script=[call("update_device_capabilities", {"deviceId": BULB, "body": BLUE}, "c1"),
                                      AIMessage(content="done")])
        out = await agent(model, house).run(CTX)

        self.assertEqual(out["notes"], "report-notes")
        self.assertEqual(house.calls, ["details", "update", "details"])
        self.assertEqual(len(out["actions"]), 1)
        a = out["actions"][0]
        self.assertEqual((a["tool"], a["args"]), ("update_device_capabilities", {"deviceId": BULB, "body": BLUE}))
        self.assertEqual((a["status"], a["verified"], a["error"]), ("sent", "confirmed", False))

    async def test_plug_on_blocked_in_loop(self):
        house = FakeHouse()
        on = {"deviceId": PLUG, "body": {"type": "power", "value": "on"}}
        model = ScriptedModel(script=[call("update_device_capabilities", on, "c1"), AIMessage(content="done")])
        out = await agent(model, house).run(CTX)

        self.assertNotIn("update", house.calls)
        self.assertEqual((out["actions"][0]["status"], out["actions"][0]["error"]), ("blocked", True))
        told = [m.content for m in model.seen if isinstance(m, ToolMessage)]
        self.assertTrue(any("approval" in t for t in told))
        self.assertEqual(out["notes"], "report-notes")

    async def test_plug_on_allowed_for_ask(self):
        house = FakeHouse()
        on = {"deviceId": PLUG, "body": {"type": "power", "value": "on"}}
        model = ScriptedModel(script=[call("update_device_capabilities", on, "c1"), AIMessage(content="done")])
        out = await agent(model, house, allow_plug_on=True).run(CTX)
        self.assertEqual(out["actions"][0]["status"], "sent")

    async def test_slow_readback_unconfirmed(self):
        house = FakeHouse(reports=False)
        model = ScriptedModel(script=[call("update_device_capabilities", {"deviceId": BULB, "body": BLUE}, "c1"),
                                      AIMessage(content="done")])
        out = await agent(model, house, readback_tries=3).run(CTX)

        a = out["actions"][0]
        self.assertEqual((a["status"], a["verified"], a["error"]), ("sent", "unconfirmed", True))
        self.assertEqual(house.calls.count("details"), 1 + 3)
        told = [m.content for m in model.seen if isinstance(m, ToolMessage)]
        self.assertTrue(any("NOT confirmed" in t for t in told))

    async def test_failed_write_not_verified(self):
        house = FakeHouse(write_result='{"message":"SH_10500: x","error":"tool_error"}')
        model = ScriptedModel(script=[call("update_device_capabilities", {"deviceId": BULB, "body": BLUE}, "c1"),
                                      AIMessage(content="done")])
        out = await agent(model, house).run(CTX)

        a = out["actions"][0]
        self.assertEqual((a["status"], a["verified"], a["error"]), ("failed", None, True))
        self.assertIn("SH_10500", a["result"])
        self.assertEqual(house.calls, ["details", "update"])

    async def test_stale_device_write_blocked(self):
        house = FakeHouse()
        ctx = {**CTX, "state": {**CTX["state"], BULB: {**CTX["state"][BULB], "fresh": False, "staleMinutes": 180}}}
        model = ScriptedModel(script=[call("update_device_capabilities", {"deviceId": BULB, "body": BLUE}, "c1"),
                                      AIMessage(content="done")])
        out = await agent(model, house).run(ctx)

        self.assertEqual(house.calls, [])
        self.assertEqual((out["actions"][0]["status"], out["actions"][0]["error"]), ("blocked", True))

    async def test_mcp_error_reply_is_failed(self):
        house = FakeHouse(rejects="Device offline")
        model = ScriptedModel(script=[call("update_device_capabilities", {"deviceId": BULB, "body": BLUE}, "c1"),
                                      AIMessage(content="done")])
        out = await agent(model, house).run(CTX)

        a = out["actions"][0]
        self.assertEqual((a["status"], a["verified"], a["error"]), ("failed", None, True))
        self.assertIn("Device offline", a["result"])
        self.assertEqual(house.calls, ["details", "update"])

    async def test_no_tools(self):
        model = ScriptedModel(script=[AIMessage(content="nothing to do")])
        out = await LangGraphAgent(model, tools_provider=None).run(CTX)
        self.assertEqual(out["actions"], [])
        self.assertEqual(out["notes"], "report-notes")

    async def test_tools_provider_raises(self):
        async def broken():
            raise RuntimeError("mcp down")
        model = ScriptedModel(script=[AIMessage(content="no tools")])
        out = await LangGraphAgent(model, broken).run(CTX)
        self.assertEqual(out["actions"], [])
        self.assertEqual(out["notes"], "report-notes")

    async def test_looping_model_stops_at_max_steps(self):
        house = FakeHouse()
        model = ScriptedModel(script=[call("get_device_details", {"deviceId": BULB}, "c1")])
        out = await agent(model, house, max_steps=3).run(CTX)
        self.assertTrue(out["notes"])
        self.assertEqual(out["actions"], [])
        self.assertLess(len(house.calls), 10)


class ParseReportTest(unittest.TestCase):
    def test_json_inside_text(self):
        r = parse_report('Sure. {"notes": "all fine", "trigger_updates": []}')
        self.assertEqual(r.notes, "all fine")

    def test_not_json_becomes_notes(self):
        self.assertEqual(parse_report("The plug is on.").notes, "The plug is on.")


if __name__ == "__main__":
    unittest.main()
