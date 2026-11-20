"""Alfred's alerts when a habit starts and a device isn't as the habit usually finds it.

The expectations are learned (source/learn/expectations.py); this only compares them with the home now. A
habit has started when its decision's time has come, for ALERT_FOR_H. The sentence is fixed, the pitch
film's: no LLM is called. Read by the "Right now" status (source/status.py) as `alerts`.
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta

from . import anticipate
from .learn import to_local, to_ms
from .learn.forecast import as_local, habit_name, has_table
from .model import State

log = logging.getLogger("source.alerts")

ALERT_FOR_H = 2
NO_LONGER_EXPECTED = "skipped: no longer expected"


def _left_on(device_type: str, capability: str, value) -> bool:
    """Only a door or window left open, or something left on, is worth saying: the sentence's "still open/on"."""
    if capability == "sensorState":
        return device_type == "contactSensor" and value == "alarm"
    return capability == "power" and value == "on"


def alert_text(device: str, device_type: str, habit: str, since: datetime) -> str:
    word = "open" if device_type == "contactSensor" else "on"
    return (f"The {device.lower()} is still {word}. {habit} is starting,"
            f" and it has been {word} since {since.strftime('%H:%M')}.")


def alerts_now(db: sqlite3.Connection, state: State, now: datetime, stale_ms: int | None) -> list[dict]:
    """[{id, at, text}], one per habit that started in the last ALERT_FOR_H and device not as it expects."""
    try:
        return _alerts(db, state, as_local(now), stale_ms)
    except Exception:
        log.exception("alerts failed")
        return []


def _alerts(db, state: State, now: datetime, stale_ms: int | None) -> list[dict]:
    started: dict[str, datetime] = {}
    for d, meta in anticipate.rows_between(db, now - timedelta(hours=ALERT_FOR_H), now + timedelta(minutes=1)):
        at, hid = datetime.fromisoformat(d["at"]), d.get("habit_id")
        why = str((meta.get("ran") or {}).get("why") or "")
        if hid and at <= now and not why.startswith(NO_LONGER_EXPECTED):
            started[hid] = min(at, started.get(hid, at))
    out = []
    for at, hid in sorted((at, hid) for hid, at in started.items()):
        habit = _habit(db, hid)
        if habit is None:
            continue
        quiet = set(habit.get("quiet_expects") or [])
        for e in habit.get("expects") or []:
            r = state.get((e["device_id"], e["capability"]))
            if e.get("device") in quiet or r is None or r.value == e["value"]:
                continue
            if not _left_on(r.device_type or e.get("device_type", ""), e["capability"], r.value):
                continue
            if stale_ms is not None and (r.comm_ts is None or to_ms(now) - r.comm_ts > stale_ms):
                continue
            since = _since(db, e["device_id"], e["capability"], r.value)
            if since is None:
                continue
            out.append({"id": f"{hid}:{e['device_id']}:{at.date().isoformat()}", "at": at.isoformat(),
                        "text": alert_text(r.device or e.get("device", ""), r.device_type or e.get("device_type", ""),
                                           habit_name(habit), since)})
    return out


def _habit(db, hid: str) -> dict | None:
    row = db.execute("SELECT data FROM habits WHERE id = ?", (hid,)).fetchone() if has_table(db, "habits") else None
    if row is None:
        return None
    try:
        h = json.loads(row[0])
    except (TypeError, ValueError):
        log.warning("alerts: unreadable habit %s", hid)
        return None
    return h if isinstance(h, dict) else None


def _since(db, device_id: str, capability: str, value) -> datetime | None:
    row = db.execute("SELECT ts FROM events WHERE device_id = ? AND capability = ? AND new = ?"
                     " ORDER BY ts DESC, id DESC LIMIT 1", (device_id, capability, json.dumps(value))).fetchone()
    return to_local(row[0]) if row else None
