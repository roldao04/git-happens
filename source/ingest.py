"""Ingest: JSONL snapshots → events in the store, at full speed, every time taken from the data.

No agent, no triggers, no rules: this only builds the history the learners read.
The live loop (`source/loop.py`) is separate and unchanged.
"""
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

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


def ingest(snaps: Iterable[Snapshot], store: Store, *, stale_ms: int) -> IngestStats:
    prev = prev_ts = first_ts = None
    count = events = 0
    for ts, home in snaps:
        state = normalize(home)
        found = diff(prev, state, ts, stale_ms, prev_ts) if prev is not None else initially_stale(state, ts, stale_ms)
        store.save(ts, home, found)
        count, events = count + 1, events + len(found)
        first_ts = ts if first_ts is None else first_ts
        prev, prev_ts = state, ts
    return IngestStats(count, events, first_ts, prev_ts)
