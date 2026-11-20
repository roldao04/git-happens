"""Hard-rule actions: tool calls a trigger runs directly, with no LLM.

A trigger's `actions` is a list of {"tool": <MCP tool name>, "args": {...}}. Only the tools in
ALLOWED can run, the same write the agent is limited to, and each write goes through
guard.guarded_write (allow-list, never plug-on, read-back). Replays get DryRunActuator, so a
replay never touches the real kit.
"""
import json
import logging
import re
from typing import Protocol

from .guard import LOOK_ONLY_REASON, READ_TOOL, Heat, guarded_write

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
        return "{}" if tool == READ_TOOL else "dry run"  # reads answer "nothing known": never confirmed


class LookOnlyActuator:
    """--look-only: Alfred's own writes (decisions, rules) are refused, so they settle as not done, never as
    a pretend "sent" as with DryRunActuator."""

    async def call(self, tool: str, args: dict) -> str:
        if tool == READ_TOOL:
            return "{}"
        raise RuntimeError(LOOK_ONLY_REASON.removeprefix("blocked: "))


async def run_actions(actuator: Actuator, trigger_id: str, actions: list[dict],
                      devices: dict[str, str] | None = None, *, heat: Heat | None = None,
                      readback_tries: int = 3, readback_delay_s: float = 0.0) -> list[dict]:
    """Runs each action in order through the write guard (never plug-on), then reads it back.
    A failure is logged and recorded, never raised. `devices`: device_id -> type. `heat`: what
    code knows about the thermostat (guard.Heat); without it the guard never writes the thermostat."""
    done = []
    for a in actions:
        tool, args = a.get("tool", ""), a.get("args", {})
        if tool not in ALLOWED:
            out = {"tool": tool, "args": args, "status": "blocked", "verified": None,
                   "error": True, "result": f"tool {tool!r} is not allowed"}
        else:
            out = await guarded_write(actuator.call, args, devices or {}, allow_plug_on=False, heat=heat,
                                      readback_tries=readback_tries, readback_delay_s=readback_delay_s)
        level = logging.WARNING if out["error"] else logging.INFO
        log.log(level, "rule   %s → %s %s: %s%s", trigger_id, tool, json.dumps(args), out["status"],
                f" ({out['verified']})" if out["verified"] else f": {out['result'][:200]}",
                extra={"data": {"trigger_id": trigger_id, "action": out}})
        done.append(out)
    return done


def slug(name: str) -> str:
    """"Washing machine" → "washing-machine", as in the signal names (docs/contracts.md C2)."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


def _find(target: str, fresh: dict[str, list[str]], names: dict[str, str] | None) -> str | None:
    """The fresh device a target names: a type ("bulb": the first one), or a type and the
    device's slugged name ("plug:tv"), when the home has several of that type."""
    kind, _, which = str(target).partition(":")
    ids = fresh.get(kind) or []
    if not which:
        return ids[0] if ids else None
    return next((i for i in ids if slug((names or {}).get(i, "")) == which), None)


def resolve_targets(actions: list[dict], fresh: dict[str, list[str]],
                    names: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """Fills each action's deviceId from its `target`: a type (first fresh device of that type) or
    "type:name" ("plug:tv"; `names`: device id → name). Returns (ready to run, skipped records):
    an action never goes to a device that isn't reporting."""
    ready, skipped = [], []
    for a in actions:
        target = a.get("target")
        if target is None:
            ready.append(a)
        elif (dev := _find(target, fresh, names)) is not None:
            ready.append({"tool": a["tool"], "args": {"deviceId": dev, **a.get("args", {})}})
        else:
            skipped.append({"tool": a.get("tool", ""), "args": a.get("args", {}), "status": "skipped",
                            "verified": None, "error": True, "result": f"no fresh {target}"})
    return ready, skipped
