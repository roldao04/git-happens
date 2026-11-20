"""get_home JSON → flat readings."""
import json

from .model import Reading, State

HOME_SECURITY = "home-security"

# Fields that change without meaning anything (command flags, internal counters).
IGNORED = {
    "factoryReset", "restart", "sdFormat", "sdFormatState", "ptzStop", "ptzControl",
}

# The router's list of connected devices becomes one on/off reading per phone plus a count of guests'
# phones (docs/contracts.md, C2). The raw list itself is not a reading.
CLIENTS = "connectedClients"
PHONE = "phone"  # device type of a phone; its device id is the router's client id
CONNECTED = "connected"  # "on" while connected, "off" once a phone seen before has left
GUEST_PHONES = "guestPhones"  # on the router: connected phones that are not the household's


def _value(v):
    # Lists/dicts (e.g. the plug's multiPower) become stable strings so they compare cleanly.
    return json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v


def normalize(home: dict, prev: State | None = None) -> State:
    """`prev`: the state before. A phone it had that is no longer connected reads "off" (else it would be gone)."""
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
            if ctype == CLIENTS:
                _phones(state, dev_id, name, dtype, comm, cap.get("value") or [], prev)
                continue
            state[(dev_id, ctype)] = Reading(dev_id, name, ctype, _value(cap.get("value")), comm, dtype)
    return state


def _phones(state: State, router_id: str, router: str, rtype: str, comm, clients: list, prev: State | None) -> None:
    guests = 0
    for c in clients:
        if not isinstance(c, dict) or c.get("kind") != PHONE or not c.get("id"):
            continue
        known = bool(c.get("known"))
        guests += not known
        # A guest's phone has no name: that is how the learners and the wakes tell it from the household's
        # (the `devices` table keeps the name; source/learn/occasions.py, source/wake.py).
        name = c.get("name") or ("Phone" if known else "")
        state[(c["id"], CONNECTED)] = Reading(c["id"], name, CONNECTED, "on", comm, PHONE)
    state[(router_id, GUEST_PHONES)] = Reading(router_id, router, GUEST_PHONES, guests, comm, rtype)
    for key, r in (prev or {}).items():  # phones seen before that have left: "off", heard through the router
        if r.device_type == PHONE and r.capability == CONNECTED and key not in state:
            state[key] = Reading(r.device_id, r.device, CONNECTED, "off", comm, PHONE)


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
