"""What each habit expects of the home's devices: a window almost always closed when Night starts.

Run nightly after the habits. For each active habit, the state of every on/off-like device is rebuilt from
`events` at each of its readings' starts; a value held on at least EXPECT_SHARE of at least MIN_OCCURRENCES
starts is stored on the habit as `expects`. The habit's own devices (the lamp Night turns off) are left out.
Recomputed every night, so an expectation the home stops keeping goes away by itself.
"""
import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import date, datetime

from . import MIN_OCCURRENCES, signal_devices, to_ms

log = logging.getLogger("source.learn.expectations")

EXPECT_SHARE = 0.8
ON_OFF = {"sensorState": ("normal", "alarm"), "power": ("on", "off")}
NOT_A_STATE = ("gone", "stale", "gap")


def learn_expectations(db: sqlite3.Connection, day: date) -> None:
    if not (_has(db, "habits") and _has(db, "habit_readings") and _has(db, "events")):
        return
    devices = db.execute("SELECT device_id, type, name FROM devices").fetchall()
    signals = signal_devices(devices)
    info = {d: (t, n) for d, t, n in devices}
    signatures = dict(db.execute("SELECT id, signature FROM sensors")) if _has(db, "sensors") else {}
    for hid, data in db.execute("SELECT id, data FROM habits WHERE status = 'active'").fetchall():
        habit = json.loads(data)
        starts = [datetime.fromisoformat(s) for (s,) in db.execute(
            "SELECT start FROM habit_readings WHERE habit_id = ? AND day <= ? ORDER BY start", (hid, day.isoformat()))]
        own = " ".join(signatures.get(st.get("pattern"), "") for st in habit.get("steps") or [] if isinstance(st, dict))
        habit["expects"] = _expects(db, starts, signals, info, own)
        db.execute("UPDATE habits SET data = ? WHERE id = ?", (json.dumps(habit), hid))
    db.commit()


def _expects(db, starts: list[datetime], signals: dict[str, str], info: dict, own: str) -> list[dict]:
    if len(starts) < MIN_OCCURRENCES:
        return []
    out = []
    for (device_id, capability), name in _candidates(db).items():
        if device_id in signals and re.search(rf"(?<![\w-]){re.escape(signals[device_id])}(?![\w:-])", own):
            continue
        seen = Counter(v for v in (_value_at(db, device_id, capability, t) for t in starts) if v is not None)
        of = sum(seen.values())
        if of < MIN_OCCURRENCES:
            continue
        value, agree = seen.most_common(1)[0]
        if agree / of >= EXPECT_SHARE:
            dtype, dname = info.get(device_id, ("", name))
            out.append({"device_id": device_id, "device": dname or name, "device_type": dtype,
                        "capability": capability, "value": value, "agree": agree, "of": of})
    return out


def _candidates(db) -> dict[tuple[str, str], str]:
    marks = ",".join("?" * len(ON_OFF))
    return {(d, c): n for d, c, n in db.execute(
        f"SELECT device_id, capability, MAX(device) FROM events WHERE capability IN ({marks})"
        " GROUP BY device_id, capability ORDER BY device_id, capability", tuple(ON_OFF))}


def _value_at(db, device_id: str, capability: str, t: datetime):
    marks = ",".join("?" * len(NOT_A_STATE))
    row = db.execute(f"SELECT new FROM events WHERE device_id = ? AND capability = ? AND ts <= ?"
                     f" AND kind NOT IN ({marks}) ORDER BY ts DESC, id DESC LIMIT 1",
                     (device_id, capability, to_ms(t), *NOT_A_STATE)).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        value = json.loads(row[0])
    except ValueError:
        log.warning("expectations: unreadable value %r for %s %s", row[0], device_id, capability)
        return None
    return value if value in ON_OFF[capability] else None


def _has(db, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None
