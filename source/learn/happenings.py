"""Happenings: each time a signal leaves its rest and comes back (docs/starting-plan.md, part 4).

A happening starts at the first observed stretch that isn't at rest and ends where the signal is back at
rest. Consecutive stretches away from rest are one happening (a kettle wobbling 2,100 → 2,108 → 2,095 W).
A hole (gap, stale device, gone signal) ends it early, marked `cut`. One still away from rest at the end of
the observed time is not written: a later run writes it once it ends.

  uv run python -m source.learn.happenings data/sim/rosa/     # the happenings of an ingested history
"""
import argparse
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..clock import TZ
from ..model import Event
from ..store import Store
from .rest import Rest, SignalId, Stretch, _is_number, _weighted_median, find_rest, observed_stretches

# Signals that never make happenings of their own, or every use would also link "plug power → plug current":
NOT_HAPPENINGS = {
    "instantCurrent",  # the plug's current, computed from its power
    "brightness", "color_rgb", "temperature", "workMode",  # the bulb's settings; its activity is `power`
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS happenings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal TEXT NOT NULL,            -- "<device type>.<capability>"
    device_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    start_ms INTEGER NOT NULL,       -- epoch ms, like events.ts
    end_ms INTEGER NOT NULL,
    duration_s REAL NOT NULL,
    size REAL,                       -- level: time-weighted median; slow: total change; state: NULL
    peak REAL,                       -- level: highest value
    value TEXT,                      -- state: the non-rest state held longest
    cut INTEGER NOT NULL DEFAULT 0,  -- 1 = ended by a gap / stale / gone, not by returning to rest
    kind TEXT,                       -- filled by the grouper (part 5); leave NULL
    UNIQUE (device_id, capability, start_ms)
);
CREATE INDEX IF NOT EXISTS happenings_start ON happenings (start_ms);
"""
COLUMNS = "signal, device_id, capability, start_ms, end_ms, duration_s, size, peak, value, cut, kind"


@dataclass(frozen=True)
class Happening:
    signal: str  # "plug.instantPower"
    device_id: str
    capability: str
    start_ms: int
    end_ms: int
    size: float | None
    peak: float | None
    value: str | None
    cut: bool
    kind: str | None = None
    id: int | None = None

    @property
    def duration_s(self) -> float:
        return (self.end_ms - self.start_ms) / 1000

    def to_json(self) -> dict:
        return {"signal": self.signal, "start": _local(self.start_ms), "end": _local(self.end_ms),
                "duration_s": self.duration_s, "size": self.size, "kind": self.kind, "device_id": self.device_id,
                "capability": self.capability, "value": self.value, "peak": self.peak, "cut": int(self.cut)}


def find_happenings(events: Iterable[Event], rests: dict[SignalId, Rest], device_types: dict[str, str],
                    observed_until: int) -> list[Happening]:
    """Every happening that ended before observed_until, in start order. Pure: reads nothing else."""
    out: list[Happening] = []
    for sig, stretches in observed_stretches(events, observed_until).items():
        rest = rests.get(sig)
        if rest is None or sig[1] in NOT_HAPPENINGS:
            continue
        name = f"{device_types.get(sig[0]) or stretches[0].signal.split('.')[0]}.{sig[1]}"
        walk = _walk_slow if rest.kind == "slow" else _walk
        out += walk(sig, name, stretches, rest, observed_until)
    return sorted(out, key=lambda h: (h.start_ms, h.signal, h.device_id))


def _walk(sig: SignalId, name: str, stretches: list[Stretch], rest: Rest, observed_until: int) -> list[Happening]:
    out: list[Happening] = []
    away: list[Stretch] = []
    for s in stretches:
        if away and s.start != away[-1].end:  # a hole: end it at the last observed moment
            out.append(_held(sig, name, away, away[-1].end, rest, cut=True))
            away = []
        if not rest.at_rest(s.value):
            away.append(s)
        elif away:
            out.append(_held(sig, name, away, s.start, rest, cut=False))
            away = []
    if away and away[-1].end < observed_until:  # stopped being observed (stale or gone) while away
        out.append(_held(sig, name, away, away[-1].end, rest, cut=True))
    return out  # anything still away at observed_until is open: not written yet


def _held(sig: SignalId, name: str, away: list[Stretch], end: int, rest: Rest, cut: bool) -> Happening:
    held: dict[Any, int] = {}
    for s in away:
        held[s.value] = held.get(s.value, 0) + s.end - s.start
    size = peak = value = None
    numbers = [(v, ms) for v, ms in held.items() if _is_number(v)]
    if rest.kind == "level" and numbers:
        size = float(_weighted_median(numbers))
        peak = float(max(v for v, _ in numbers))
    else:
        best = max(held, key=lambda v: held[v])  # first held wins a tie: dicts keep order
        value = best if isinstance(best, str) else json.dumps(best)
    return Happening(name, sig[0], sig[1], away[0].start, end, size, peak, value, cut)


def _walk_slow(sig: SignalId, name: str, stretches: list[Stretch], rest: Rest,
               observed_until: int) -> list[Happening]:
    """A slow signal is away from rest while it moves faster than it normally does, between touching stretches."""
    out: list[Happening] = []
    first = last = None  # the stretches before and after the run of fast changes

    def close(end: int, cut: bool) -> None:
        out.append(Happening(name, sig[0], sig[1], first.start, end, float(last.value - first.value), None, None, cut))

    for a, b in zip(stretches, stretches[1:]):
        if a.end != b.start:
            if first is not None:
                close(a.end, cut=True)
                first = None
            continue
        if not rest.rate_at_rest(b.value - a.value, b.start - a.start):
            first, last = (a if first is None else first), b
        elif first is not None:
            close(last.start, cut=False)
            first = None
    if first is not None and last.end < observed_until:
        close(last.end, cut=True)
    return out


def ensure_table(db: sqlite3.Connection) -> None:
    """Creates the table if needed. Commits anything pending first, as executescript does."""
    db.executescript(SCHEMA)


def save_happenings(store: Store, until_ms: int) -> int:
    """Appends the happenings that ended before until_ms; returns how many rows are new.

    Rest is what rest_from_store(store, until_ms) gives (the same call, without reading the events twice).
    Per signal, only happenings that start after the end of its last stored one are added, so re-running
    never changes or duplicates a row and ids stay stable.
    """
    ensure_table(store.db)
    last = store.last_snapshot_before(until_ms)
    if last is None:
        return 0
    events = store.events_before(until_ms)
    rests = find_rest(events, last)
    types = dict(store.db.execute("SELECT device_id, type FROM devices"))
    ends = {(d, c): e for d, c, e in
            store.db.execute("SELECT device_id, capability, MAX(end_ms) FROM happenings GROUP BY device_id, capability")}
    new = [h for h in find_happenings(events, rests, types, last)
           if h.start_ms > ends.get((h.device_id, h.capability), -1)]
    added = 0
    for h in new:
        cur = store.db.execute(
            f"INSERT OR IGNORE INTO happenings ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (h.signal, h.device_id, h.capability, h.start_ms, h.end_ms, h.duration_s, h.size, h.peak, h.value,
             int(h.cut), h.kind))
        added += cur.rowcount
    store.commit()
    return added


def stored_happenings(db: sqlite3.Connection) -> list[Happening]:
    ensure_table(db)
    rows = db.execute(f"SELECT id, {COLUMNS} FROM happenings ORDER BY start_ms, id").fetchall()
    return [Happening(sig, dev, cap, start, end, size, peak, value, bool(cut), kind, _id)
            for _id, sig, dev, cap, start, end, _dur, size, peak, value, cut, kind in rows]


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%dT%H:%M:%S")


def describe(h: Happening) -> str:
    if h.size is None:
        what = repr(h.value)
    elif h.peak is None:
        what = f"{h.size:+g}"
    else:
        what = f"{h.size:g} (peak {h.peak:g})"
    return (f"{_local(h.start_ms).replace('T', ' ')}  {h.signal:<32} {_minutes(h.duration_s):>8}  {what}"
            f"{'  cut' if h.cut else ''}")


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f} min" if seconds < 3600 else f"{seconds / 3600:.1f} h"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="source.learn.happenings", description="Show the happenings in a history")
    p.add_argument("folder", type=Path, help="a folder with a home.sqlite3, as source.ingest or source.pipeline writes it")
    p.add_argument("--signal", help="only signals containing this, e.g. instantPower")
    p.add_argument("--json", action="store_true", help="one JSON object per line")
    args = p.parse_args(argv)
    if not (args.folder / "home.sqlite3").exists():
        print(f"{args.folder} has no home.sqlite3: ingest a file into it first")
        return 1
    store = Store(args.folder, record=False)
    try:
        found = stored_happenings(store.db)
        where = "stored"
        if not found:  # ingested without the pipeline: work them out, without writing
            last = store.last_snapshot_before(2 ** 62)
            events = store.events_before(2 ** 62) if last is not None else []
            types = dict(store.db.execute("SELECT device_id, type FROM devices"))
            found = [] if last is None else find_happenings(events, find_rest(events, last), types, last)
            where = "found in the whole history, not stored"
    finally:
        store.close()
    shown = [h for h in found if not args.signal or args.signal in h.signal]
    for h in shown:
        print(json.dumps(h.to_json()) if args.json else describe(h))
    if not args.json:
        print(f"{len(shown)} of {len(found)} happenings shown ({where})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
