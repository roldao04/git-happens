"""History: SQLite (snapshots, events, agent runs, rule runs) + a JSONL recording of raw snapshots.

The MCP keeps no history, so this is the only record. Full snapshots are kept so the state at
any moment can be rebuilt later (needed for override learning, docs/event-loop.md §7).
"""
import json
import sqlite3
from datetime import date
from pathlib import Path

from .model import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (ts INTEGER PRIMARY KEY, home TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, device_id TEXT NOT NULL, device TEXT NOT NULL,
    capability TEXT NOT NULL, kind TEXT NOT NULL,
    old TEXT, new TEXT, source TEXT NOT NULL DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT NOT NULL UNIQUE, type TEXT NOT NULL, name TEXT NOT NULL, first_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS days (
    day TEXT NOT NULL UNIQUE, first_ts INTEGER NOT NULL, last_ts INTEGER NOT NULL, snapshots INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, reasons TEXT NOT NULL, context TEXT NOT NULL, output TEXT
);
CREATE TABLE IF NOT EXISTS rule_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, trigger_id TEXT NOT NULL, actions TEXT NOT NULL
);
"""


class Store:
    def __init__(self, data_dir: Path, record: bool = True):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "home.sqlite3")
        self.db.executescript(SCHEMA)
        self._recording = open(data_dir / "recording.jsonl", "a") if record else None

    def save(self, ts: int, home: dict, events: list[Event], *, commit: bool = True) -> None:
        self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?)", (ts, json.dumps(home)))
        self.save_events(events, commit=commit)
        if self._recording:
            self._recording.write(json.dumps({"ts": ts, "home": home}) + "\n")
            self._recording.flush()

    def save_events(self, events: list[Event], *, commit: bool = True) -> None:
        for e in events:
            cur = self.db.execute(
                "INSERT INTO events (ts, device_id, device, capability, kind, old, new, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (e.ts, e.device_id, e.device, e.capability, e.kind,
                 json.dumps(e.old), json.dumps(e.new), e.source),
            )
            e.id = cur.lastrowid
        if commit:
            self.db.commit()

    def commit(self) -> None:
        self.db.commit()

    def save_devices(self, ts: int, devices: dict[str, tuple[str, str]]) -> None:
        """device_id -> (type, name); keeps the first time each id was seen. Commits with the next commit."""
        self.db.executemany("INSERT OR IGNORE INTO devices (device_id, type, name, first_ts) VALUES (?, ?, ?, ?)",
                            [(dev_id, dtype, name, ts) for dev_id, (dtype, name) in devices.items()])

    def save_day(self, day: date, first_ts: int, last_ts: int, snapshots: int) -> None:
        """A local day that has data. Commits with the next commit."""
        self.db.execute("INSERT OR REPLACE INTO days (day, first_ts, last_ts, snapshots) VALUES (?, ?, ?, ?)",
                        (day.isoformat(), first_ts, last_ts, snapshots))

    def days(self) -> list[tuple[date, int, int, int]]:
        rows = self.db.execute("SELECT day, first_ts, last_ts, snapshots FROM days ORDER BY day").fetchall()
        return [(date.fromisoformat(d), first, last, n) for d, first, last, n in rows]

    def devices_of_type(self, device_type: str) -> list[str]:
        rows = self.db.execute("SELECT device_id FROM devices WHERE type = ? ORDER BY first_ts, device_id",
                               (device_type,)).fetchall()
        return [r[0] for r in rows]

    def events_since(self, ts_ms: int) -> list[Event]:
        rows = self.db.execute(
            "SELECT id, ts, device_id, device, capability, kind, old, new, source"
            " FROM events WHERE ts > ? ORDER BY ts, id", (ts_ms,)
        ).fetchall()
        return [
            Event(ts, dev_id, dev, cap, kind, json.loads(old), json.loads(new), source, id=_id)
            for _id, ts, dev_id, dev, cap, kind, old, new, source in rows
        ]

    def events_before(self, ts_ms: int) -> list[Event]:
        rows = self.db.execute(
            "SELECT id, ts, device_id, device, capability, kind, old, new, source"
            " FROM events WHERE ts < ? ORDER BY ts, id", (ts_ms,)
        ).fetchall()
        return [
            Event(ts, dev_id, dev, cap, kind, json.loads(old), json.loads(new), source, id=_id)
            for _id, ts, dev_id, dev, cap, kind, old, new, source in rows
        ]

    def last_snapshot_before(self, ts_ms: int) -> int | None:
        return self.db.execute("SELECT MAX(ts) FROM snapshots WHERE ts < ?", (ts_ms,)).fetchone()[0]

    def log_agent_run(self, ts: int, reasons: list[dict], context: dict, output: dict | None) -> None:
        self.db.execute(
            "INSERT INTO agent_runs (ts, reasons, context, output) VALUES (?, ?, ?, ?)",
            (ts, json.dumps(reasons), json.dumps(context, default=str), json.dumps(output, default=str)),
        )
        self.db.commit()

    def log_rule_run(self, ts: int, trigger_id: str, actions: list[dict]) -> None:
        self.db.execute("INSERT INTO rule_runs (ts, trigger_id, actions) VALUES (?, ?, ?)",
                        (ts, trigger_id, json.dumps(actions, default=str)))
        self.db.commit()

    def rule_runs(self) -> list[tuple[int, str, list[dict]]]:
        rows = self.db.execute("SELECT ts, trigger_id, actions FROM rule_runs ORDER BY id").fetchall()
        return [(ts, tid, json.loads(a)) for ts, tid, a in rows]

    def close(self) -> None:
        self.db.close()
        if self._recording:
            self._recording.close()
