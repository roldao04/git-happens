"""The agent step.

StubAgent only logs what it was given. LangGraphAgent runs a small graph per wake:

    START -> agent <-> tools (ToolNode) -> report -> END

Contract (same for both): context dict in, {notes, trigger_updates, actions} out.
`actions` is built from the tool calls actually made, never taken from the LLM: every write
goes through guard.guarded_write (allow-list, then read-back by code).
"""
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel
from typing_extensions import TypedDict

from .guard import WRITE_TOOL, guarded_write

log = logging.getLogger("source.agent")

READ_TOOLS = frozenset({"get_home", "get_device_details", "get_home_security_status", "get_home_scenarios"})
WRITE_TOOLS = frozenset({"update_device_capabilities"})

PROMPT_PATH = Path(__file__).parent / "prompts" / "agent.md"

REPORT_ASK = ("Now give your final report. Reply with ONLY a JSON object, no other text: "
              '{"notes": "<short plain words: what you saw, what you did, whether it was confirmed>", '
              '"trigger_updates": []}  (trigger_updates is usually empty).')


class Report(BaseModel):
    notes: str
    trigger_updates: list[dict] = []


class StubAgent:
    async def run(self, ctx: dict) -> dict:
        why = ", ".join(r["trigger_id"] for r in ctx["reasons"])
        log.info("wake   (%s) %d new events since last run",
                 why, len(ctx["events_since_last_run"]),
                 extra={"data": {"reasons": ctx["reasons"], "events": ctx["events_since_last_run"]}})
        for line in ctx["events_since_last_run"][-10:]:
            log.debug("  %s", line)
        return {"notes": "stub", "trigger_updates": [], "actions": []}


OPENROUTER_URL = "https://openrouter.ai/api/v1"


def make_model(model_name: str, api_key: str) -> BaseChatModel:
    """Any OpenRouter model slug, e.g. 'anthropic/claude-sonnet-4.5' (OpenAI-compatible API)."""
    return ChatOpenAI(model=model_name, api_key=api_key, base_url=OPENROUTER_URL)


async def mcp_tools(session) -> list[BaseTool]:
    from langchain_mcp_adapters.tools import load_mcp_tools
    tools = await load_mcp_tools(session)
    return [t for t in tools if t.name in READ_TOOLS | WRITE_TOOLS]


class _State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    report: Report | None


def build_graph(model: BaseChatModel, tools: list[BaseTool]):
    llm = model.bind_tools(tools) if tools else model

    async def agent(state: _State):
        return {"messages": [await llm.ainvoke(state["messages"])]}

    async def report(state: _State):
        # Plain JSON-in-text, not with_structured_output: providers differ in what they support
        # (e.g. Meta's endpoint accepts only tool_choice "auto" and only closed JSON schemas).
        msg = await model.ainvoke(state["messages"] + [HumanMessage(REPORT_ASK)])
        return {"report": parse_report(_text(msg.content))}

    g = StateGraph(_State)
    g.add_node("agent", agent)
    g.add_node("report", report)
    g.add_edge(START, "agent")
    if tools:
        g.add_node("tools", ToolNode(tools, handle_tool_errors=True))
        g.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "report"})
        g.add_edge("tools", "agent")
    else:
        g.add_edge("agent", "report")
    g.add_edge("report", END)
    return g.compile()


def _text(content) -> str:
    """Message or tool content as plain text (MCP tools may return a list of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b if isinstance(b, str) else str(b.get("text", "")) if isinstance(b, dict) else str(b)
                       for b in content)
    return json.dumps(content, default=str)


def parse_report(text: str) -> Report:
    """The first {...} in the reply as a Report; if that fails, the whole reply becomes the notes."""
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return Report.model_validate(json.loads(text[start:end + 1]))
        except ValueError:
            pass
    return Report(notes=text.strip())


def _tell_llm(action: dict) -> str:
    if action["status"] == "blocked":
        return action["result"]
    if action["status"] == "failed":
        return f"failed: {action['result']}"
    if action["verified"] == "confirmed":
        return "sent and confirmed: the device now reports the new value."
    return "sent but NOT confirmed: the device did not report the new value. Say you tried but couldn't confirm."


def guard_writes(tools: list[BaseTool], devices: dict[str, str], actions: list[dict], *,
                 allow_plug_on: bool, readback_tries: int, readback_delay_s: float) -> list[BaseTool]:
    """Replaces the write tool with one that runs guarded_write and records each action."""
    by_name = {t.name: t for t in tools}
    write = by_name.get(WRITE_TOOL)
    if write is None:
        return tools

    async def call_tool(name: str, args: dict) -> str:
        return _text(await by_name[name].ainvoke(args))

    async def guarded(**args) -> str:
        action = await guarded_write(call_tool, args, devices, allow_plug_on=allow_plug_on,
                                     readback_tries=readback_tries, readback_delay_s=readback_delay_s)
        actions.append(action)
        return _tell_llm(action)

    wrapped = StructuredTool.from_function(coroutine=guarded, name=write.name,
                                           description=write.description, args_schema=write.args_schema)
    return [wrapped if t is write else t for t in tools]


class LangGraphAgent:
    def __init__(self, model: BaseChatModel,
                 tools_provider: Callable[[], Awaitable[list[BaseTool]]] | None = None,
                 *, max_steps: int = 12, allow_plug_on: bool = False,
                 readback_tries: int = 3, readback_delay_s: float = 2.0):
        self.model = model
        self.tools_provider = tools_provider
        self.max_steps = max_steps
        self.allow_plug_on = allow_plug_on  # only source.ask: a person asked directly (tier 2)
        self.readback_tries = readback_tries
        self.readback_delay_s = readback_delay_s

    async def run(self, ctx: dict) -> dict:
        why = ", ".join(r["trigger_id"] for r in ctx["reasons"])
        log.info("wake   (%s) %d new events since last run",
                 why, len(ctx["events_since_last_run"]),
                 extra={"data": {"reasons": ctx["reasons"], "events": ctx["events_since_last_run"]}})

        tools: list[BaseTool] = []
        if self.tools_provider is not None:
            try:
                tools = await self.tools_provider()
            except Exception as e:
                log.warning("tools unavailable, running without tools: %s", e)
                tools = []
        actions: list[dict] = []
        devices = {dev_id: d.get("type", "") for dev_id, d in ctx["state"].items()}
        tools = guard_writes(tools, devices, actions, allow_plug_on=self.allow_plug_on,
                             readback_tries=self.readback_tries, readback_delay_s=self.readback_delay_s)

        graph = build_graph(self.model, tools)
        messages = [SystemMessage(PROMPT_PATH.read_text()),
                    HumanMessage(json.dumps(ctx, default=str))]

        final: dict = {"messages": messages, "report": None}
        limit_hit = False
        try:
            async for state in graph.astream({"messages": messages, "report": None},
                                             config={"recursion_limit": self.max_steps},
                                             stream_mode="values"):
                final = state
        except GraphRecursionError:
            limit_hit = True
            log.warning("agent hit the step limit (%d)", self.max_steps)

        for a in actions:
            log.log(logging.WARNING if a["error"] else logging.INFO, "action %s %s -> %s%s",
                    a["tool"], json.dumps(a["args"], default=str), a["status"],
                    f" ({a['verified']})" if a["verified"] else "", extra={"data": a})

        rep: Report | None = final.get("report")
        if limit_hit or rep is None:
            notes = (f"Stopped: reached the step limit ({self.max_steps}) before finishing. "
                     f"{len(actions)} change(s) were sent; none can be assumed confirmed.")
            return {"notes": notes, "trigger_updates": [], "actions": actions}
        return {"notes": rep.notes, "trigger_updates": rep.trigger_updates, "actions": actions}
