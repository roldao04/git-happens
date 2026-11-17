"""When to wake the agent.

Triggers are plain data, so later the agent can add, change and expire them (and they can move
into the database). In v1 they're the seed list below. Locked triggers are the safety floor:
the agent may never remove them, and they ignore cooldowns.

A trigger can also be a hard rule: `actions` are tool calls the loop runs itself, no LLM
(see actions.py), and `wake_agent=False` keeps the LLM out of it entirely.
"""
from dataclasses import dataclass, field

from .model import Event, WakeReason
from .normalize import HOME_SECURITY

SMOKE = "e4566929-1477-4023-8095-af904a0befe3"
CONTACT = "d4d883d1-c83b-4411-b790-f63289402c1d"
PLUG = "a291277c-8ba5-480a-b3a3-35b5f49bb459"
DOOR = "13dc0bee-f750-4cb1-9cac-6897fe0a4bd4"  # the contact sensor that is actually reporting
LIGHT = "90654832-7bca-47dd-89f3-d6e0b5f0a9ee"
CAMERA = "4f4f2408-ffc2-4543-a42b-3757133a6df4"


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
    actions: list[dict] = field(default_factory=list)  # [{"tool": ..., "args": {...}}], run by code
    wake_agent: bool = True


def set_color(device_id: str, r: int, g: int, b: int) -> dict:
    return {"tool": "update_device_capabilities",
            "args": {"deviceId": device_id,
                     "body": {"type": "color_rgb", "redValue": r, "greenValue": g, "blueValue": b}}}


def seed_triggers(heartbeat_minutes: float) -> list[Trigger]:
    return [
        Trigger("heartbeat", "interval", "Regular check, also catches when nothing happens",
                {"period_s": heartbeat_minutes * 60}, locked=True),
        Trigger("smoke", "change", "Smoke detected",
                {"device_id": SMOKE, "capability": "sensorState", "to": "alarm"}, locked=True),
        Trigger("alarm-went-off", "change", "The alarm went off",
                {"device_id": HOME_SECURITY, "capability": "isAlarmed", "to": True}, locked=True),
        Trigger("contact", "change", "Door/window opened or closed",
                {"device_id": CONTACT, "capability": "sensorState"}),
        Trigger("plug-power", "delta", "Big change in plug power",
                {"device_id": PLUG, "capability": "instantPower", "min_delta": 300}),
        Trigger("device-silent", "stale", "A device stopped reporting or came back"),
        # Hard rules: door open → lamp blue, door closed → lamp red. No LLM, no cooldown.
        Trigger("door-open", "change", "The door opened: lamp blue",
                {"device_id": DOOR, "capability": "sensorState", "not_to": "normal"},
                cooldown_s=0, actions=[set_color(LIGHT, 0, 0, 255)], wake_agent=False),
        Trigger("door-closed", "change", "The door closed: lamp red",
                {"device_id": DOOR, "capability": "sensorState", "to": "normal"},
                cooldown_s=0, actions=[set_color(LIGHT, 255, 0, 0)], wake_agent=False),
        # The camera reports no dB level: "loud enough" is its soundSensitivity (set in the app,
        # with soundAlarmSwitch on). soundDetected is "" when quiet, non-empty when it hears sound.
        Trigger("camera-sound", "change", "The camera heard sound (people talking): turn the lamp blue",
                {"device_id": CAMERA, "capability": "soundDetected", "not_to": ""}),
        # Same idea for motion: needs motionAlarmSwitch on; motionSensitivity is the level.
        Trigger("camera-motion", "change", "The camera saw motion: turn the lamp blue",
                {"device_id": CAMERA, "capability": "motionDetected", "not_to": ""}),
    ]


def _matches(t: Trigger, e: Event) -> bool:
    c = t.condition
    if t.kind == "stale":
        return e.kind in ("stale", "returned")
    if e.kind != "change":
        return False
    if c.get("device_id") not in (None, e.device_id) or c.get("capability") not in (None, e.capability):
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

    def match(self, events: list[Event], now_ms: int) -> list[WakeReason]:
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
            hits = [e for e in events if _matches(t, e)]
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
