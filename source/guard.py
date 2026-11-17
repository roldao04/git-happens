"""The write guard: which device writes may run, checked in code, not in the prompt.

Only the lamp and the plug can be written, only with the capabilities the MCP accepts, and
switching the plug on needs a person (docs/synapse.md §10, tier 2). Every write that runs is
then read back by code (§6, level 2): the LLM's own summary never decides "confirmed".
"""
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("source.guard")

WRITE_TOOL = "update_device_capabilities"
READ_TOOL = "get_device_details"
CHANNELS = ("redValue", "greenValue", "blueValue")

WRITE_TYPES = {"bulb": {"power", "brightness", "temperature", "color_rgb"}, "plug": {"power"}}
PLUG_ON_REASON = "blocked: switching the plug on needs a person's approval"


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
}


def check_write(args: dict, devices: dict[str, str], *, allow_plug_on: bool) -> str | None:
    """None if this update_device_capabilities call may run, otherwise why not (for the LLM to read)."""
    if set(args) != {"deviceId", "body"}:
        return "blocked: give exactly deviceId and body (no deviceName or vendor)"
    dev_id, body = args["deviceId"], args["body"]
    dev_type = devices.get(dev_id)
    if dev_type not in WRITE_TYPES:
        return f"blocked: device {dev_id!r} can't be changed (only the lamp and the plug, while they report)"
    if not isinstance(body, dict) or body.get("type") not in WRITE_TYPES[dev_type]:
        return f"blocked: a {dev_type} only accepts {sorted(WRITE_TYPES[dev_type])}"
    fields, valid = BODIES[body["type"]]
    if set(body) != fields or not valid(body):
        return f"blocked: {body['type']} needs exactly {sorted(fields)} with valid values"
    if dev_type == "plug" and body["value"] == "on" and not allow_plug_on:
        return PLUG_ON_REASON
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
                        devices: dict[str, str], *, allow_plug_on: bool,
                        readback_tries: int = 3, readback_delay_s: float = 0.0) -> dict:
    """Check, read, write, read back. Returns the action record:
    {tool, args, status: blocked|failed|sent, verified: confirmed|unconfirmed|None, result, error[, reason]}.
    """
    action = {"tool": WRITE_TOOL, "args": args, "status": "blocked", "verified": None,
              "result": "", "error": True}
    reason = check_write(args, devices, allow_plug_on=allow_plug_on)
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
