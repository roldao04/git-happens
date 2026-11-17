"""When to wake the agent.

Triggers are plain data, so later the agent can add, change and expire them (and they can move
into the database). In v1 they're the seed list below. Locked triggers are the safety floor:
the agent may never remove them, and they ignore cooldowns.

A trigger can also be a hard rule: `actions` are tool calls the loop runs itself, no LLM
(see actions.py), and `wake_agent=False` keeps the LLM out of it entirely.
"""
from dataclasses import dataclass, field

from .model import Event, State, WakeReason
from .normalize import fresh_by_type

# Devices are found by type: ids change when a device is re-paired (docs/mcp-capabilities.md).
SAFETY_TYPES = ("smokeSensor",)


@dataclass
class Trigger:
    id: str
    kind: str  # interval | change | delta | stale
    reason: str
    condition: dict = field(default_factory=dict)
    locked: bool = False
    cooldown_s: float = 120
    created_by: str = "seed"  # seed | agent | human
    expires_at: int | None = None  # ms
    enabled: bool = True
    actions: list[dict] = field(default_factory=list)  # [{"tool", "target": <type>, "args"}], run by code
    wake_agent: bool = True


def set_color(target_type: str, r: int, g: int, b: int) -> dict:
    """The loop sends it to a fresh device of `target_type` (e.g. "bulb") when the rule fires."""
    return {"tool": "update_device_capabilities", "target": target_type,
            "args": {"body": {"type": "color_rgb", "redValue": r, "greenValue": g, "blueValue": b}}}


def seed_triggers(heartbeat_minutes: float) -> list[Trigger]:
    return [
        Trigger("heartbeat", "interval", "Regular check, also catches when nothing happens",
                {"period_s": heartbeat_minutes * 60}, locked=True),
        Trigger("smoke", "change", "Smoke detected",
                {"device_type": "smokeSensor", "capability": "sensorState", "to": "alarm"}, locked=True),
        Trigger("alarm-went-off", "change", "The alarm went off",
                {"device_type": "security", "capability": "isAlarmed", "to": True}, locked=True),
        Trigger("contact", "change", "Door/window opened or closed",
                {"device_type": "contactSensor", "capability": "sensorState"}),
        Trigger("plug-power", "delta", "Big change in plug power",
                {"device_type": "plug", "capability": "instantPower", "min_delta": 300}),
        Trigger("device-silent", "stale", "A device stopped reporting or came back"),
        # Hard rules: door open → lamp blue, door closed → lamp red. No LLM, no cooldown.
        Trigger("door-open", "change", "The door opened: lamp blue",
                {"device_type": "contactSensor", "capability": "sensorState", "not_to": "normal"},
                cooldown_s=0, actions=[set_color("bulb", 0, 0, 255)], wake_agent=False),
        Trigger("door-closed", "change", "The door closed: lamp red",
                {"device_type": "contactSensor", "capability": "sensorState", "to": "normal"},
                cooldown_s=0, actions=[set_color("bulb", 255, 0, 0)], wake_agent=False),
        # The camera reports no dB level: "loud enough" is its soundSensitivity (set in the app,
        # with soundAlarmSwitch on). soundDetected is "" when quiet, non-empty when it hears sound.
        Trigger("camera-sound", "change", "The camera heard sound (people talking): turn the lamp blue",
                {"device_type": "camera", "capability": "soundDetected", "not_to": ""}),
        # Same idea for motion: needs motionAlarmSwitch on; motionSensitivity is the level.
        Trigger("camera-motion", "change", "The camera saw motion: turn the lamp blue",
                {"device_type": "camera", "capability": "motionDetected", "not_to": ""}),
    ]


def _matches(t: Trigger, e: Event, types: dict[str, str]) -> bool:
    c = t.condition
    if t.kind == "stale":
        return e.kind in ("stale", "returned")
    if e.kind != "change":
        return False
    if c.get("device_id") not in (None, e.device_id) or c.get("capability") not in (None, e.capability):
        return False
    if c.get("device_type") not in (None, types.get(e.device_id)):
        return False
    if t.kind == "change":
        if "not_to" in c and e.new == c["not_to"]:
            return False
        return "to" not in c or e.new == c["to"]
    if t.kind == "delta":
        try:
            return abs(float(e.new) - float(e.old)) >= c["min_delta"]
        except (TypeError, ValueError):
            return False
    return False


class Triggers:
    def __init__(self, triggers: list[Trigger]):
        self.triggers = {t.id: t for t in triggers}
        self._last_fired: dict[str, int] = {}

    def match(self, events: list[Event], now_ms: int,
              types: dict[str, str] | None = None) -> list[WakeReason]:
        """types: device_id -> device type, for conditions on `device_type`."""
        types = types or {}
        reasons: list[WakeReason] = []
        for t in self.triggers.values():
            if not t.enabled or (t.expires_at and now_ms > t.expires_at):
                continue
            last = self._last_fired.get(t.id)
            if t.kind == "interval":
                if last is None or now_ms - last >= t.condition["period_s"] * 1000:
                    reasons.append(WakeReason(t.id, t.reason))
                    self._last_fired[t.id] = now_ms
                continue
            hits = [e for e in events if _matches(t, e, types)]
            if not hits:
                continue
            if not t.locked and last is not None and now_ms - last < t.cooldown_s * 1000:
                continue  # events are still stored and reach the agent on its next run
            reasons.append(WakeReason(t.id, t.reason, hits))
            self._last_fired[t.id] = now_ms
        return reasons

    def apply_updates(self, updates: list[dict]) -> None:
        """Agent-proposed changes. v1: not used yet; locked triggers can't be disabled."""
        for u in updates:
            t = self.triggers.get(u.get("id", ""))
            if t and t.locked and u.get("enabled") is False:
                continue
            # TODO: validate and apply add / change / expire once the agent proposes them.


def missing_fresh(state: State, now_ms: int, stale_ms: int,
                  required: tuple[str, ...] = SAFETY_TYPES) -> list[str]:
    """Device types a locked safety trigger needs but no fresh device provides."""
    fresh = fresh_by_type(state, now_ms, stale_ms)
    return [t for t in required if not fresh.get(t)]
