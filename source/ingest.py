"""Ingest: JSONL snapshots → events in the store, at full speed, every time taken from the data.

No agent, no triggers, no rules: this only builds the history the learners read.
The live loop (`source/loop.py`) is separate and unchanged.
"""
import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta, tzinfo
from pathlib import Path

from .clock import TZ, local_day
from .diff import diff, initially_stale
from .normalize import normalize
from .store import Store

Snapshot = tuple[int, dict]


@dataclass(frozen=True)
class IngestStats:
    snapshots: int
    events: int
    first_ts: int | None
    last_ts: int | None


def read_jsonl(path: Path) -> Iterator[Snapshot]:
    """Lines of {"ts": <ms>, "home": {...}}, read one at a time so months of data never sit in memory."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                yield int(row["ts"]), row["home"]


def ingest(snaps: Iterable[Snapshot], store: Store, *, stale_ms: int, tz: tzinfo = TZ,
           on_day_end: Callable[[date], None] | None = None) -> IngestStats:
    """Stores each snapshot and its events, one commit per local day.

    on_day_end(day) runs once for every date from the first snapshot to the last (empty days too), after that
    day is committed and before anything later is stored, so whatever it runs never sees the future.
    """
    prev = prev_ts = first_ts = day = None
    count = events = 0
    for ts, home in snaps:
        if prev_ts is not None and ts < prev_ts:
            raise ValueError(f"snapshots out of order: {ts} after {prev_ts}")
        today = local_day(ts, tz)
        if day is not None and today != day:
            _end_days(store, day, today, on_day_end)
        day = today
        state = normalize(home)
        found = diff(prev, state, ts, stale_ms, prev_ts) if prev is not None else initially_stale(state, ts, stale_ms)
        store.save(ts, home, found, commit=False)
        count, events = count + 1, events + len(found)
        first_ts = ts if first_ts is None else first_ts
        prev, prev_ts = state, ts
    if day is not None:
        _end_days(store, day, day + timedelta(days=1), on_day_end)
    return IngestStats(count, events, first_ts, prev_ts)


def _end_days(store: Store, day: date, until: date, on_day_end: Callable[[date], None] | None) -> None:
    store.commit()
    while day < until:
        if on_day_end is not None:
            on_day_end(day)
        day += timedelta(days=1)
