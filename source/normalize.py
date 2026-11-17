"""get_home JSON → flat readings."""
import json

from .model import Reading, State

HOME_SECURITY = "home-security"

# Fields that change without meaning anything (command flags, internal counters).
IGNORED = {
    "factoryReset", "restart", "sdFormat", "sdFormatState", "ptzStop", "ptzControl",
}


def _value(v):
    # Lists/dicts (e.g. the plug's multiPower) become stable strings so they compare cleanly.
    return json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v


def normalize(home: dict) -> State:
    state: State = {}
    sec = home.get("security") or {}
    for cap in ("status", "isAlarmed", "alarmType"):
        if cap in sec:
            state[(HOME_SECURITY, cap)] = Reading(HOME_SECURITY, "Alarm", cap, sec[cap], None, "security")

    for dev in home.get("devices", []):
        dev_id, dtype = dev["id"], dev.get("type", "")
        name = dev.get("name") or dtype or "?"
        comm = dev.get("lastCommunicationTime")
        for cap in dev.get("capabilities", []):
            ctype = cap.get("type")
            if not ctype or ctype in IGNORED:
                continue
            state[(dev_id, ctype)] = Reading(dev_id, name, ctype, _value(cap.get("value")), comm, dtype)
    return state


def device_types(state: State) -> dict[str, str]:
    """device_id -> device type, including the alarm ("security")."""
    return {r.device_id: r.device_type for r in state.values()}


def device_last_seen(state: State) -> dict[str, tuple[str, int]]:
    """device_id -> (name, lastCommunicationTime) for real devices."""
    out: dict[str, tuple[str, int]] = {}
    for r in state.values():
        if r.comm_ts is not None:
            out[r.device_id] = (r.device, r.comm_ts)
    return out


def fresh_by_type(state: State, now_ms: int, stale_ms: int) -> dict[str, list[str]]:
    """device type -> ids of devices of that type that reported within stale_ms."""
    out: dict[str, list[str]] = {}
    for r in state.values():
        if r.comm_ts is not None and now_ms - r.comm_ts <= stale_ms:
            ids = out.setdefault(r.device_type, [])
            if r.device_id not in ids:
                ids.append(r.device_id)
    return out
