"""History: SQLite (snapshots, events, agent runs) + a JSONL recording of raw snapshots.

The MCP keeps no history, so this is the only record. Full snapshots are kept so the state at
any moment can be rebuilt later (needed for override learning, docs/event-loop.md §7).
"""
import json
import sqlite3
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
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, reasons TEXT NOT NULL, context TEXT NOT NULL, output TEXT
);
"""


class Store:
    def __init__(self, data_dir: Path, record: bool = True):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(data_dir / "home.sqlite3")
        self.db.executescript(SCHEMA)
        self._recording = open(data_dir / "recording.jsonl", "a") if record else None

    def save(self, ts: int, home: dict, events: list[Event]) -> None:
        self.db.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?)", (ts, json.dumps(home)))
        self.save_events(events)  # commits
        if self._recording:
            self._recording.write(json.dumps({"ts": ts, "home": home}) + "\n")
            self._recording.flush()

    def save_events(self, events: list[Event]) -> None:
        for e in events:
            cur = self.db.execute(
                "INSERT INTO events (ts, device_id, device, capability, kind, old, new, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (e.ts, e.device_id, e.device, e.capability, e.kind,
                 json.dumps(e.old), json.dumps(e.new), e.source),
            )
            e.id = cur.lastrowid
        self.db.commit()

    def events_since(self, ts_ms: int) -> list[Event]:
        rows = self.db.execute(
            "SELECT id, ts, device_id, device, capability, kind, old, new, source"
            " FROM events WHERE ts > ? ORDER BY ts, id", (ts_ms,)
        ).fetchall()
        return [
            Event(ts, dev_id, dev, cap, kind, json.loads(old), json.loads(new), source, id=_id)
            for _id, ts, dev_id, dev, cap, kind, old, new, source in rows
        ]

    def log_agent_run(self, ts: int, reasons: list[dict], context: dict, output: dict | None) -> None:
        self.db.execute(
            "INSERT INTO agent_runs (ts, reasons, context, output) VALUES (?, ?, ?, ?)",
            (ts, json.dumps(reasons), json.dumps(context, default=str), json.dumps(output, default=str)),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
        if self._recording:
            self._recording.close()
