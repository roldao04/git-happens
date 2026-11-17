"""The write guard: which device writes may run, checked in code, not in the prompt.

Only the lamp and the plug can be written, only with the capabilities the MCP accepts, and
switching the plug on needs a person (docs/synapse.md §10, tier 2).
"""

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
        return f"blocked: device {dev_id!r} can't be changed (only the lamp and the plug)"
    if not isinstance(body, dict) or body.get("type") not in WRITE_TYPES[dev_type]:
        return f"blocked: a {dev_type} only accepts {sorted(WRITE_TYPES[dev_type])}"
    fields, valid = BODIES[body["type"]]
    if set(body) != fields or not valid(body):
        return f"blocked: {body['type']} needs exactly {sorted(fields)} with valid values"
    if dev_type == "plug" and body["value"] == "on" and not allow_plug_on:
        return PLUG_ON_REASON
    return None
