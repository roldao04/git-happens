"""LangGraphAgent driven by a scripted fake chat model and fake tools (no network, no API keys).

  uv run python -m unittest discover -s tests -t . -v
"""
import unittest
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from source.agent import REPORT_ASK, LangGraphAgent, parse_report

CTX = {
    "now": "2026-11-17T10:00:00",
    "reasons": [{"trigger_id": "smoke", "detail": "smoke detected"}],
    "state": {"dev1": {"switch": "on", "lastSeen": "2026-11-17T09:59:50"}},
    "events_since_last_run": ["2026-11-17T09:59:50 dev1 switch=on"],
    "timeline_today": [],
    "baselines": {},
}


def call(name: str, args: dict, id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id, "type": "tool_call"}])


class ScriptedModel(BaseChatModel):
    """Replays `script` in order (repeating the last message once exhausted)."""
    script: list[Any]
    step: int = 0
    structured_calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if messages[-1].content == REPORT_ASK:  # the report step: answer it, don't consume the script
            self.structured_calls += 1
            msg = AIMessage(content='Here: {"notes": "report-notes", "trigger_updates": []}')
            return ChatResult(generations=[ChatGeneration(message=msg)])
        msg = self.script[min(self.step, len(self.script) - 1)]
        self.step += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self._generate(messages, stop)

    def bind_tools(self, tools, **kwargs):
        return self



class FakeTools:
    def __init__(self, write_result: str = '{"ok": true}'):
        self.calls: list[tuple[str, dict]] = []
        calls = self.calls

        @tool
        async def update_device_capabilities(device_id: str, value: str) -> str:
            """Set a capability."""
            calls.append(("update_device_capabilities", {"device_id": device_id, "value": value}))
            return write_result

        @tool
        def get_device_details(device_id: str) -> str:
            """Read a device."""
            calls.append(("get_device_details", {"device_id": device_id}))
            return '{"id": "dev1", "value": "off"}'

        self.tools = [update_device_capabilities, get_device_details]

    async def provider(self):
        return self.tools


class AgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_write_then_read_back(self):
        fake = FakeTools()
        write_args = {"device_id": "dev1", "value": "off"}
        model = ScriptedModel(script=[
            call("update_device_capabilities", write_args, "c1"),
            call("get_device_details", {"device_id": "dev1"}, "c2"),
            AIMessage(content="done"),
        ])
        out = await LangGraphAgent(model, fake.provider).run(CTX)

        self.assertEqual(set(out), {"notes", "trigger_updates", "actions"})
        self.assertEqual(out["notes"], "report-notes")
        self.assertEqual(out["trigger_updates"], [])
        self.assertEqual(fake.calls, [("update_device_capabilities", write_args),
                                      ("get_device_details", {"device_id": "dev1"})])
        self.assertEqual(len(out["actions"]), 1)
        action = out["actions"][0]
        self.assertEqual(action["tool"], "update_device_capabilities")
        self.assertEqual(action["args"], write_args)
        self.assertIn("ok", action["result"])
        self.assertIs(action["error"], False)

    async def test_failed_write_is_flagged(self):
        fake = FakeTools(write_result='{"message":"SH_10500: x","error":"tool_error"}')
        model = ScriptedModel(script=[
            call("update_device_capabilities", {"device_id": "dev1", "value": "off"}, "c1"),
            AIMessage(content="done"),
        ])
        out = await LangGraphAgent(model, fake.provider).run(CTX)
        self.assertEqual(len(out["actions"]), 1)
        self.assertIs(out["actions"][0]["error"], True)
        self.assertIn("SH_10500", out["actions"][0]["result"])

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
        fake = FakeTools()
        model = ScriptedModel(script=[call("get_device_details", {"device_id": "dev1"}, "c1")])
        out = await LangGraphAgent(model, fake.provider, max_steps=3).run(CTX)
        self.assertTrue(out["notes"])
        self.assertEqual(out["actions"], [])
        self.assertLess(len(fake.calls), 10)


if __name__ == "__main__":
    unittest.main()


class ParseReportTest(unittest.TestCase):
    def test_json_inside_text(self):
        r = parse_report('Sure. {"notes": "all fine", "trigger_updates": []}')
        self.assertEqual(r.notes, "all fine")

    def test_not_json_becomes_notes(self):
        self.assertEqual(parse_report("The plug is on.").notes, "The plug is on.")
