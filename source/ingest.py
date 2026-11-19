"""Ingest: JSONL snapshots → events in the store, at full speed, every time taken from the data.

No agent, no triggers, no rules: this only builds the history the learners read.
The live loop (`source/loop.py`) is separate and unchanged.

  uv run python -m source.ingest data/sim/couple.jsonl --out data/sim/couple/
  uv run python -m source.ingest data/recording.jsonl --out data/sim/live/ --replace
"""
import argparse
import json
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta, tzinfo
from pathlib import Path

from .clock import TZ, local_day
from .diff import diff, initially_stale
from .model import Event, State
from .normalize import normalize
from .store import Store

log = logging.getLogger("source.ingest")

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


def ingest(snaps: Iterable[Snapshot], store: Store, *, stale_ms: int, gap_ms: int = 30 * 60_000,
           tz: tzinfo = TZ, on_day_end: Callable[[date], None] | None = None) -> IngestStats:
    """Stores each snapshot and its events, one commit per local day.

    on_day_end(day) runs once for every date from the first snapshot to the last (empty days too), after that
    day is committed and before anything later is stored, so whatever it runs never sees the future.
    Snapshots more than gap_ms apart add a "gap" event (old = last snapshot before it, new = first after).
    """
    prev = prev_ts = first_ts = day = None
    count = events = day_count = 0
    day_first = 0
    known: set[str] = set()
    for ts, home in snaps:
        if prev_ts is not None and ts < prev_ts:
            raise ValueError(f"snapshots out of order: {ts} after {prev_ts}")
        today = local_day(ts, tz)
        if day is not None and today != day:
            store.save_day(day, day_first, prev_ts, day_count)
            _end_days(store, day, today, on_day_end)
        if today != day:
            day, day_first, day_count = today, ts, 0
        day_count += 1
        state = normalize(home, prev)  # with prev, a phone that left reads "off" instead of being gone
        if prev is None:
            found = _initial(state, ts) + initially_stale(state, ts, stale_ms)
        else:
            found = diff(prev, state, ts, stale_ms, prev_ts)
            if ts - prev_ts > gap_ms:
                found.insert(0, Event(ts, "loop", "Loop", "poll", "gap", prev_ts, ts))
        new_devices = {r.device_id: (r.device_type, r.device) for r in state.values() if r.device_id not in known}
        if new_devices:
            store.save_devices(ts, new_devices)
            known.update(new_devices)
        store.save(ts, home, found, commit=False)
        count, events = count + 1, events + len(found)
        first_ts = ts if first_ts is None else first_ts
        prev, prev_ts = state, ts
    if day is not None:
        store.save_day(day, day_first, prev_ts, day_count)
        _end_days(store, day, day + timedelta(days=1), on_day_end)
    return IngestStats(count, events, first_ts, prev_ts)


def _initial(state: State, ts: int) -> list[Event]:
    """Where every signal starts, so the history says what each value was before its first change."""
    return [Event(ts, r.device_id, r.device, r.capability, "initial", None, r.value) for r in state.values()]


def _end_days(store: Store, day: date, until: date, on_day_end: Callable[[date], None] | None) -> None:
    store.commit()
    while day < until:
        if on_day_end is not None:
            on_day_end(day)
        day += timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="source.ingest", description="Ingest JSONL snapshots into a history store")
    p.add_argument("file", type=Path, help='JSONL of {"ts": <ms>, "home": <get_home>} lines')
    p.add_argument("--out", type=Path, required=True, help="folder for this run's home.sqlite3")
    p.add_argument("--stale-minutes", type=float, default=60, help="a device is stale after this long silent")
    p.add_argument("--gap-minutes", type=float, default=30, help="snapshots further apart add a gap event")
    p.add_argument("--replace", action="store_true", help="delete this folder's existing history first")
    args = p.parse_args(argv)

    db = args.out / "home.sqlite3"
    if db.exists():
        if not args.replace:
            log.error("%s already has a history; use --replace to start it again", args.out)
            return 1
        db.unlink()
    store = Store(args.out, record=False)
    try:
        stats = ingest(read_jsonl(args.file), store, stale_ms=int(args.stale_minutes * 60_000),
                       gap_ms=int(args.gap_minutes * 60_000), on_day_end=lambda day: log.info("day %s done", day))
    finally:
        store.close()
    log.info("ingested %d snapshots, %d events into %s", stats.snapshots, stats.events, db)
    return 0


if __name__ == "__main__":
    from .logs import setup_logging

    setup_logging(None)
    raise SystemExit(main())
