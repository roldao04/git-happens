"""The write guard: which device writes may run, checked in code, not in the prompt.

Only the lamp, the plugs and the thermostat can be written, only with the capabilities the MCP
accepts, and switching a plug on needs a person (TECHNICAL.md, Safety). Every write that
runs is then read back by code (§6, level 2): the LLM's own summary never decides "confirmed".

The thermostat (docs/contracts.md C7) has its own limits, checked against `Heat`, what code
knows about it right now: target 16–23 °C, at most 3 °C per write, never above 18 °C while no
known phone is home (unless an arrival is forecast soon: warming ahead of it is the point), at
most one of Alfred's writes per 10 min, and none for 2 h after a person changed it. Without a
`Heat` the thermostat can't be written at all: only code that has checked the home may.
"""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger("source.guard")

WRITE_TOOL = "update_device_capabilities"
READ_TOOL = "get_device_details"
CHANNELS = ("redValue", "greenValue", "blueValue")

WRITE_TYPES = {"bulb": {"power", "brightness", "temperature", "color_rgb"}, "plug": {"power"},
               "thermostat": {"targetTemperature", "mode"}}
PLUG_ON_REASON = "blocked: switching the plug on needs a person's approval"
# --look-only (npm run home): the real home is read and Alfred answers, but no write is ever sent
LOOK_ONLY_REASON = "blocked: look-only mode, nothing is sent (start npm run home:live to switch things)"

# The thermostat's limits (C7)
TARGET_MIN_C, TARGET_MAX_C = 16.0, 23.0
MAX_STEP_C = 3.0  # per write
AWAY_MAX_C = 18.0  # while no known phone is home
PREHEAT_MIN = 120  # ...unless an arrival is forecast within this many minutes
WRITE_EVERY_MIN = 10  # at most one of Alfred's writes this often
PERSON_HOLD_MIN = 120  # after a person changed it, Alfred leaves it alone this long
MODES = ("heat", "eco", "off")


@dataclass
class Heat:
    """What code knows about the thermostat when a write is checked (built in source/anticipate.py)."""
    now_ms: int
    target: float | None = None  # its current target, °C
    someone_home: bool | None = None  # a known phone is connected
    arrival_ms: int | None = None  # the forecast arrival this write prepares for, if any
    last_write_ms: int | None = None  # Alfred's last write to it
    person_ms: int | None = None  # the last time a person changed it


def _number(v, lo: float, hi: float) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def _channel(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255


# body type -> (exact fields allowed, validator for the body)
BODIES = {
    "power": ({"type", "value"}, lambda b: b.get("value") in ("on", "off")),
    "brightness": ({"type", "value"}, lambda b: _number(b.get("value"), 1, 100)),
    "temperature": ({"type", "value"}, lambda b: _number(b.get("value"), 0, 100)),
    "color_rgb": ({"type", "redValue", "greenValue", "blueValue"},
                  lambda b: all(_channel(b.get(k)) for k in ("redValue", "greenValue", "blueValue"))),
    "targetTemperature": ({"type", "value"}, lambda b: _number(b.get("value"), TARGET_MIN_C, TARGET_MAX_C)),
    "mode": ({"type", "value"}, lambda b: b.get("value") in MODES),
}


def check_heat(body: dict, heat: "Heat | None", *, by_person: bool = False) -> str | None:
    """The thermostat's own limits (C7). None if the write may run. `by_person`: a person asked,
    so the rate limit and the 2 h hold (which protect people from Alfred) don't apply."""
    if heat is None:
        return "blocked: the thermostat is changed only by code that checked its limits (make a proposal)"
    now = heat.now_ms
    if not by_person:
        if heat.person_ms is not None and now - heat.person_ms < PERSON_HOLD_MIN * 60_000:
            return f"blocked: a person changed the thermostat in the last {PERSON_HOLD_MIN // 60} h"
        if heat.last_write_ms is not None and now - heat.last_write_ms < WRITE_EVERY_MIN * 60_000:
            return f"blocked: at most one thermostat change every {WRITE_EVERY_MIN} min"
    if body["type"] != "targetTemperature":
        return None
    value = float(body["value"])
    if heat.target is None:
        return "blocked: the thermostat's current target is unknown"
    if abs(value - heat.target) > MAX_STEP_C:
        return f"blocked: at most {MAX_STEP_C:g} °C change per write (now {heat.target:g} °C)"
    if value > AWAY_MAX_C and heat.someone_home is not True:
        soon = heat.arrival_ms is not None and 0 <= heat.arrival_ms - now <= PREHEAT_MIN * 60_000
        if not soon:
            return f"blocked: never above {AWAY_MAX_C:g} °C while no one is home"
    return None


def check_write(args: dict, devices: dict[str, str], *, allow_plug_on: bool,
                heat: Heat | None = None) -> str | None:
    """None if this update_device_capabilities call may run, otherwise why not (for the LLM to read).
    `heat`: what code knows about the thermostat; without it the thermostat can't be written."""
    if set(args) != {"deviceId", "body"}:
        return "blocked: give exactly deviceId and body (no deviceName or vendor)"
    dev_id, body = args["deviceId"], args["body"]
    dev_type = devices.get(dev_id)
    if dev_type not in WRITE_TYPES:
        return (f"blocked: device {dev_id!r} can't be changed "
                "(only the lamp, the plugs and the thermostat, while they report)")
    if not isinstance(body, dict) or body.get("type") not in WRITE_TYPES[dev_type]:
        return f"blocked: a {dev_type} only accepts {sorted(WRITE_TYPES[dev_type])}"
    fields, valid = BODIES[body["type"]]
    if set(body) != fields or not valid(body):
        return f"blocked: {body['type']} needs exactly {sorted(fields)} with valid values"
    if dev_type == "plug" and body["value"] == "on" and not allow_plug_on:
        return PLUG_ON_REASON
    if dev_type == "thermostat":
        return check_heat(body, heat, by_person=allow_plug_on)
    return None


def verify(body: dict, before_ts: int | None, details) -> bool:
    """True only if the device shows the new value in a report newer than before the write."""
    if not isinstance(details, dict) or before_ts is None:
        return False
    ts = details.get("lastCommunicationTime")
    if not isinstance(ts, (int, float)) or ts <= before_ts:
        return False
    caps = details.get("capabilities")
    if not isinstance(caps, list):
        return False
    cap = next((c for c in caps if isinstance(c, dict) and c.get("type") == body["type"]), None)
    if cap is None:
        return False
    if body["type"] == "color_rgb":
        return all(cap.get(k) == body[k] for k in CHANNELS)
    return cap.get("value") == body["value"]


def is_error(text: str) -> bool:
    return "SH_10500" in text or '"error"' in text


async def _read(call_tool: Callable[[str, dict], Awaitable[str]], dev_id: str) -> dict | None:
    try:
        details = json.loads(await call_tool(READ_TOOL, {"deviceId": dev_id}))
    except Exception as exc:  # a failed read only means "can't confirm", never a crash
        log.warning("read-back of %s failed: %s", dev_id, exc)
        return None
    return details if isinstance(details, dict) else None


async def guarded_write(call_tool: Callable[[str, dict], Awaitable[str]], args: dict,
                        devices: dict[str, str], *, allow_plug_on: bool, heat: Heat | None = None,
                        readback_tries: int = 3, readback_delay_s: float = 0.0) -> dict:
    """Check, read, write, read back. Returns the action record:
    {tool, args, status: blocked|failed|sent, verified: confirmed|unconfirmed|None, result, error[, reason]}.
    """
    action = {"tool": WRITE_TOOL, "args": args, "status": "blocked", "verified": None,
              "result": "", "error": True}
    reason = check_write(args, devices, allow_plug_on=allow_plug_on, heat=heat)
    if reason:
        action.update(result=reason, reason=reason)
        return action

    before = await _read(call_tool, args["deviceId"])
    try:
        text = await call_tool(WRITE_TOOL, args)
    except Exception as exc:
        action.update(status="failed", result=str(exc))
        return action
    action["result"] = text
    if is_error(text):
        action["status"] = "failed"
        return action

    action.update(status="sent", verified="unconfirmed")
    before_ts = before.get("lastCommunicationTime") if before else None
    for _ in range(readback_tries):
        await asyncio.sleep(readback_delay_s)
        if verify(args["body"], before_ts, await _read(call_tool, args["deviceId"])):
            action["verified"] = "confirmed"
            break
    action["error"] = action["verified"] != "confirmed"
    return action
