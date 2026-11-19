"""SQLite tables for the learners, on the same file as source/store.py (pass it `Store.db`).

`happenings` belongs to the happening extractor (part 4, source/learn/happenings.py, which defines
it); the grouper only fills `kind`, and reads its epoch-ms times as the home's clock time.
`kinds`, `sensors` and `sensor_readings` are the learners' own.
"""
import json
import sqlite3
from collections import Counter
from datetime import date, datetime

from . import Happening, signal_devices, to_local, to_ms
from .habits import Habit
from .happenings import SCHEMA as HAPPENINGS_SCHEMA
from .kinds import Kind
from .rhythm import DAY_MIN

SCHEMA = """
CREATE TABLE IF NOT EXISTS kinds (
    id TEXT PRIMARY KEY, signal TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sensors (
    id TEXT PRIMARY KEY, learner TEXT NOT NULL, focus TEXT NOT NULL,
    signature TEXT NOT NULL, centre_min REAL, width_min REAL,
    learned_at TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]'  -- happening ids it was built on, to follow it across days
);
CREATE TABLE IF NOT EXISTS sensor_readings (
    sensor_id TEXT NOT NULL, start TEXT NOT NULL, end TEXT, value REAL
);
CREATE INDEX IF NOT EXISTS sensor_readings_sensor ON sensor_readings (sensor_id, start);
"""


def init(db: sqlite3.Connection) -> None:
    db.executescript(HAPPENINGS_SCHEMA + SCHEMA)


# ---------- happenings ----------

def add_happenings(db: sqlite3.Connection, rows: list[dict]) -> None:
    """Rows in the agreed shape (ISO `start`/`end` or `start_ms`/`end_ms`); duplicates are ignored."""
    out = []
    for r in rows:
        h = Happening.from_dict(r)
        out.append((h.signal, r.get("device_id") or h.device, r.get("capability") or h.capability,
                    to_ms(h.start), to_ms(h.end), h.duration_s, h.size, r.get("peak"), h.value, int(h.cut), h.kind))
    db.executemany("INSERT OR IGNORE INTO happenings (signal, device_id, capability, start_ms, end_ms, duration_s,"
                   " size, peak, value, cut, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", out)
    db.commit()


def load_happenings(db: sqlite3.Connection, before: datetime, since: datetime | None = None) -> list[Happening]:
    """Happenings that started in [since, before), in the home's clock time."""
    rows = db.execute("SELECT id, signal, start_ms, end_ms, size, value, kind, device_id, cut FROM happenings"
                      " WHERE start_ms < ? AND start_ms >= ? ORDER BY start_ms, id",
                      (to_ms(before), to_ms(since) if since else 0)).fetchall()
    return [Happening(sig, to_local(s), to_local(e), size, value, kind, _id, dev, bool(cut))
            for _id, sig, s, e, size, value, kind, dev, cut in rows]


def load_polls(db: sqlite3.Connection, before: datetime) -> list[datetime] | None:
    """When the loop polled the home (`snapshots`, written by source/store.py), or None if unknown."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'snapshots'").fetchone():
        return None
    rows = db.execute("SELECT ts FROM snapshots WHERE ts < ? ORDER BY ts", (to_ms(before),)).fetchall()
    return [to_local(ts) for (ts,) in rows] or None


def save_happening_kinds(db: sqlite3.Connection, happenings: list[Happening]) -> None:
    db.executemany("UPDATE happenings SET kind = ? WHERE id = ?",
                   [(h.kind, h.id) for h in happenings if h.id is not None])
    db.commit()


# ---------- kinds ----------

def load_kinds(db: sqlite3.Connection) -> list[Kind]:
    fields = set(Kind.__dataclass_fields__)
    return [Kind(**{k: v for k, v in json.loads(d).items() if k in fields})
            for (d,) in db.execute("SELECT data FROM kinds ORDER BY id")]


def save_kinds(db: sqlite3.Connection, kinds: list[Kind]) -> None:
    """Upsert today's kinds; kinds not seen today are kept so their ids are never reused."""
    db.executemany("INSERT OR REPLACE INTO kinds (id, signal, data) VALUES (?, ?, ?)",
                   [(k.id, k.signal, json.dumps(k.__dict__)) for k in kinds])
    db.commit()


# ---------- sensors ----------

def save_habits(db: sqlite3.Connection, habits: list[Habit], day: date) -> list[dict]:
    """Upsert habit sensors and their readings. Returns the sensors first learned today.

    A habit is an existing sensor when it is (best first):
      - the same thing at about the same time (signature, window centre within a window width);
      - built mostly on the same happenings ("kettle at 08:00" refined into "door → kettle");
      - the same thing at a new time, started after the old one was last seen: it **moved**.
    Id, learned_at and a person's name are kept. A sensor not found today is marked "fading",
    never deleted, or "merged" (with `merged_into`) when its happenings now belong to another
    sensor. Readings before the habit's current members are kept as history.
    """
    rows = db.execute("SELECT id, signature, centre_min, width_min, status, data, evidence FROM sensors"
                      " WHERE learner = 'habit'").fetchall()
    existing = {r[0]: dict(id=r[0], signature=r[1], centre=r[2], width=r[3], status=r[4],
                           data=json.loads(r[5]), evidence=set(json.loads(r[6]))) for r in rows}
    evidence = [{x.id for o in h.members for x in o.happenings if x.id is not None} for h in habits]
    scored = sorted(((_same(old, h, ev), i, old["id"]) for i, (h, ev) in enumerate(zip(habits, evidence))
                     for old in existing.values()), reverse=True)
    matched: dict[int, str] = {}
    for score, i, sid in scored:
        if score and i not in matched and sid not in matched.values():
            matched[i] = sid
    new, new_ids = [], {}
    for i, h in enumerate(habits):
        old = existing.get(matched.get(i))
        pattern = h.pattern()
        if old:
            data = old["data"]
            if old["signature"] != h.key and data.get("named_by") != "person":
                data.update(name=None, named_by=None, explanation=None)  # it changed: name it again
                for k in ("means", "if_missing", "importance", "same_as"):  # the rest of the namer's card
                    data.pop(k, None)
            if _moved(old, h):
                after = [o.day for o in h.members if o.day.isoformat() > old["data"]["pattern"]["last_seen"]]
                pattern["moved_from"] = {"time_of_day": data["pattern"]["time_of_day"], "on": after[0].isoformat()}
            elif "moved_from" in data.get("pattern", {}):
                pattern["moved_from"] = data["pattern"]["moved_from"]
        else:
            data = {"id": _next_sensor_id(db), "name": None, "named_by": None, "focus": "connected_life",
                    "learner": "habit", "learned_at": day.isoformat(), "explanation": None}
            new.append(data)
            new_ids[i] = data["id"]
        data.update(pattern=pattern, confidence=h.confidence, status="active")
        data.pop("merged_into", None)
        db.execute("INSERT OR REPLACE INTO sensors (id, learner, focus, signature, centre_min, width_min,"
                   " learned_at, status, data, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (data["id"], "habit", data["focus"], h.key, h.centre_min, h.width_min,
                    data["learned_at"], "active", json.dumps(data), json.dumps(sorted(evidence[i]))))
        first = min(o.start for o in h.members).isoformat(timespec="seconds")
        db.execute("DELETE FROM sensor_readings WHERE sensor_id = ? AND start >= ?", (data["id"], first))
        db.executemany("INSERT INTO sensor_readings (sensor_id, start, end, value) VALUES (?, ?, ?, ?)",
                       [(data["id"], o.start.isoformat(timespec="seconds"),
                         o.end.isoformat(timespec="seconds") if o.end else None, o.value) for o in h.members])
    saved = {matched.get(i) or new_ids[i]: ev for i, ev in enumerate(evidence)}
    for sid, old in existing.items():
        if sid in matched.values() or old["status"] not in ("active", "fading"):
            continue
        into = next((t for t, ev in saved.items() if ev and old["evidence"]
                     and len(ev & old["evidence"]) * 2 > len(old["evidence"])), None)
        if into:  # its happenings now belong to another sensor (door habit → door → kettle)
            old["data"].update(status="merged", merged_into=into)
        elif old["status"] == "active":
            old["data"]["status"] = "fading"
        else:
            continue
        db.execute("UPDATE sensors SET status = ?, data = ? WHERE id = ?",
                   (old["data"]["status"], json.dumps(old["data"]), sid))
    db.commit()
    return new


def _same(old: dict, h: Habit, evidence: set[int]) -> float:
    """How surely `h` is the sensor `old`: 2 same time, 1.5 moved, (0.5, 1] same happenings, 0 no.

    A move beats shared happenings: when the tea moves to 09:40, the kettle card that appears there
    first must not keep the routine from its old card. Sharing happenings can refine a kind into a
    link, never shrink a link back into a kind (the old morning doors must not take over its card).
    """
    if old["signature"] == h.key and _near(old, h):
        return 2.0
    if _moved(old, h):
        return 1.5
    shrinks = h.key.startswith("kind:") and not old["signature"].startswith("kind:")
    if evidence and old["evidence"] and not shrinks:  # refining goes one way: kind → link or Awake, never back
        overlap = len(evidence & old["evidence"]) / min(len(evidence), len(old["evidence"]))
        if overlap > 0.5:
            return overlap
    return 0.0


def _near(old: dict, h: Habit) -> bool:
    d = abs(old["centre"] - h.centre_min) % DAY_MIN
    return min(d, DAY_MIN - d) <= max(old["width"], h.width_min)


def _moved(old: dict, h: Habit) -> bool:
    """Same thing, another time, and most of it happened after the old time was last seen."""
    last = old["data"].get("pattern", {}).get("last_seen")
    if old["signature"] != h.key or _near(old, h) or not last:
        return False
    return sum(o.day.isoformat() > last for o in h.members) * 2 > len(h.members)


def reset_learned(db: sqlite3.Connection) -> None:
    """Forget what the learners wrote (kinds, sensors, habits, occasions and their readings), keeping the
    history and happenings. Decisions stay: they are what Alfred did, not what it learned."""
    db.executescript("DELETE FROM kinds; DELETE FROM sensors; DELETE FROM sensor_readings;"
                     " UPDATE happenings SET kind = NULL;")
    for table in ("habits", "habit_readings", "occasions", "occasion_episodes"):  # later layers, if they ran
        if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone():
            db.execute(f"DELETE FROM {table}")
    db.commit()


def _next_sensor_id(db: sqlite3.Connection) -> str:
    ids = [r[0] for r in db.execute("SELECT id FROM sensors WHERE id LIKE 'pattern-%'")]
    return f"pattern-{max((int(i.split('-')[1]) for i in ids), default=0) + 1}"


def export(db: sqlite3.Connection, as_of: date | None = None, now: datetime | None = None) -> dict:
    """The learners' part of the dashboard JSON: every sensor (agreed shape), its readings and,
    when `now` is given, each habit's status today (source/learn/today.py)."""
    sensors = [json.loads(d) for (d,) in db.execute("SELECT data FROM sensors ORDER BY learned_at, id")]
    readings = [{"sensor_id": s, "start": a, "end": b, "value": v} for s, a, b, v in db.execute(
        "SELECT sensor_id, start, end, value FROM sensor_readings ORDER BY start")]
    out = {"as_of": as_of.isoformat() if as_of else None, "sensors": sensors, "readings": readings}
    if now is not None:
        from .today import sensor_today  # today.py reads this module's tables
        out["today"] = sensor_today(db, now)
    return out


def ongoing_happenings(db: sqlite3.Connection, now: datetime, hs: list[Happening]) -> list[Happening]:
    """On/off signals away from rest at `now` and not stored yet (a happening is written once it ends),
    read from the events table. Their non-rest value is the one their stored happenings hold."""
    if not (_table(db, "events") and _table(db, "devices")):
        return []
    held: dict[tuple, Counter] = {}
    last: dict[tuple, datetime] = {}
    for x in hs:
        key = (x.device_id, x.capability)
        if x.value is not None and x.device_id:
            held.setdefault(key, Counter())[x.value] += 1
        last[key] = max(last.get(key, x.end), x.end)
    types = signal_devices(db.execute("SELECT device_id, type, name FROM devices"))  # "phone:c-8f3a", "plug:tv"
    out = []
    for (dev, cap), values in held.items():
        row = db.execute("SELECT ts, new FROM events WHERE device_id = ? AND capability = ? AND ts <= ?"
                         " AND kind IN ('initial', 'change') ORDER BY ts DESC LIMIT 1", (dev, cap, to_ms(now))).fetchone()
        if not row:
            continue
        try:
            value = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        start = to_local(row[0])
        if value == values.most_common(1)[0][0] and start >= last.get((dev, cap), start):
            out.append(Happening(f"{types.get(dev, dev)}.{cap}", start, now, None, value, None, None, dev))
    return out


def _table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None
