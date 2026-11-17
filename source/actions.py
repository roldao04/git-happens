"""Hard-rule actions: tool calls a trigger runs directly, with no LLM.

A trigger's `actions` is a list of {"tool": <MCP tool name>, "args": {...}}. Only the tools in
ALLOWED can run, the same write the agent is limited to. Replays get DryRunActuator, so a replay
never touches the real kit.
"""
import json
import logging
from typing import Protocol

log = logging.getLogger("source.actions")

ALLOWED = frozenset({"update_device_capabilities"})


class Actuator(Protocol):
    async def call(self, tool: str, args: dict) -> str: ...


class McpActuator:
    """Calls tools on the loop's own MCP session (McpSource.session())."""

    def __init__(self, source):
        self.source = source

    async def call(self, tool: str, args: dict) -> str:
        session = await self.source.session()
        result = await session.call_tool(tool, args)
        text = "".join(getattr(c, "text", "") for c in result.content)
        if getattr(result, "isError", False) or "SH_10500" in text or '"error"' in text:
            raise RuntimeError(text[:300] or "tool returned an error")
        return text


class DryRunActuator:
    async def call(self, tool: str, args: dict) -> str:
        return "dry run"


async def run_actions(actuator: Actuator, trigger_id: str, actions: list[dict]) -> list[dict]:
    """Runs each action in order. A failure is logged and recorded, never raised."""
    done = []
    for a in actions:
        tool, args = a.get("tool", ""), a.get("args", {})
        if tool not in ALLOWED:
            out = {"tool": tool, "args": args, "error": True, "result": f"tool {tool!r} is not allowed"}
        else:
            try:
                out = {"tool": tool, "args": args, "error": False, "result": await actuator.call(tool, args)}
            except Exception as exc:
                out = {"tool": tool, "args": args, "error": True, "result": str(exc)}
        level = logging.WARNING if out["error"] else logging.INFO
        log.log(level, "rule   %s → %s %s: %s", trigger_id, tool, json.dumps(args),
                "FAILED " + out["result"] if out["error"] else "ok",
                extra={"data": {"trigger_id": trigger_id, "action": out}})
        done.append(out)
    return done
