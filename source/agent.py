"""The agent step.

StubAgent only logs what it was given. LangGraphAgent runs a small graph per wake:

    START -> agent <-> tools (ToolNode) -> report -> END

Contract (same for both): context dict in, {notes, trigger_updates, actions} out.
`actions` is built from the tool calls actually made, never taken from the LLM.
"""
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel
from typing_extensions import TypedDict

log = logging.getLogger("source.agent")

READ_TOOLS = frozenset({"get_home", "get_device_details", "get_home_security_status", "get_home_scenarios"})
WRITE_TOOLS = frozenset({"update_device_capabilities"})

PROMPT_PATH = Path(__file__).parent / "prompts" / "agent.md"

REPORT_ASK = ("Now give your final report: short plain-words notes (what you saw, what you did, "
              "whether it was confirmed) and trigger_updates (usually empty).")


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
        out = await model.with_structured_output(Report, method="function_calling").ainvoke(
            state["messages"] + [HumanMessage(REPORT_ASK)])
        if not isinstance(out, Report):
            out = Report.model_validate(out)
        return {"report": out}

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
    return content if isinstance(content, str) else json.dumps(content, default=str)


def extract_actions(messages: list) -> list[dict]:
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    actions = []
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        for call in m.tool_calls:
            if call["name"] not in WRITE_TOOLS:
                continue
            tm = results.get(call["id"])
            result = _text(tm.content) if tm is not None else ""
            error = tm is None or tm.status == "error" or "SH_10500" in result or '"error"' in result
            actions.append({"tool": call["name"], "args": call["args"], "result": result, "error": error})
    return actions


class LangGraphAgent:
    def __init__(self, model: BaseChatModel,
                 tools_provider: Callable[[], Awaitable[list[BaseTool]]] | None = None,
                 *, max_steps: int = 12):
        self.model = model
        self.tools_provider = tools_provider
        self.max_steps = max_steps

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

        actions = extract_actions(final["messages"])
        for a in actions:
            log.info("action %s %s -> %s", a["tool"], json.dumps(a["args"], default=str),
                     "ERROR" if a["error"] else "ok",
                     extra={"data": a})

        rep: Report | None = final.get("report")
        if limit_hit or rep is None:
            notes = (f"Stopped: reached the step limit ({self.max_steps}) before finishing. "
                     f"{len(actions)} change(s) were sent; none can be assumed confirmed.")
            return {"notes": notes, "trigger_updates": [], "actions": actions}
        return {"notes": rep.notes, "trigger_updates": rep.trigger_updates, "actions": actions}
