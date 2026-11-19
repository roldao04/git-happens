"""A run's history as the dashboard's recording: one JSON file the jury dashboard plays back.

The shape is the dashboard's contract (dashboard/src/data/recording.ts): the devices, their values at the
start, every change after it, and what the learners found. Times are the home's clock time, without an
offset, to the second.

  uv run python -m source.recording data/sim/rosa/ --out dashboard/public/recording.json --household rosa
  uv run python -m source.pipeline --household rosa --days 21 --out data/sim/rosa/ --replace \\
      --recording dashboard/public/recording.json
  uv run python -m source --stub      # live, read-only: poll the real kit into data/home.sqlite3
  uv run python -m source.recording --snapshots data/recording.jsonl --real --every 5 --out dashboard/public/live.json
"""
import argparse
import json
import logging
import os
import sqlite3
import tempfile
import time as clock_time
from collections.abc import Callable
from datetime import datetime, time
from pathlib import Path

from .clock import local_day
from .ingest import ingest, read_jsonl
from .learn import storage, to_local
from .store import Store

log = logging.getLogger("source.recording")

# The dashboard never shows camera motion (its spec's anti-goals), so it never gets it.
HIDDEN = (("camera", "motion"),)  # (device type, capability prefix)
VALUE_KINDS = ("initial", "change", "gone")  # the event kinds (source/model.py) that set a value
SIGNAL_KINDS = ("stale", "returned")  # a device went silent, or was heard again
CHATTY = ("bulb", "plug", "camera")  # report every few minutes while powered; the others only when something happens


def _value(raw: str | None) -> str | int | float | bool | None:
    """An event's stored JSON value as the dashboard takes it: text, a number, true/false or nothing."""
    v = json.loads(raw) if raw is not None else None
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return json.dumps(v)


def _stamp(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat()


def build(db: sqlite3.Connection, *, household: str = "", simulated: bool = True) -> dict:
    """The recording of everything in db. Raises ValueError when db has no history."""
    bounds = db.execute("SELECT MIN(ts), MAX(ts) FROM snapshots").fetchone()
    if bounds[0] is None:
        raise ValueError("this history has no snapshots")
    first, last = local_day(bounds[0]), local_day(bounds[1])

    types = dict(db.execute("SELECT device_id, type FROM devices"))
    devices = [{"id": i, "type": t, "name": n} for i, t, n in
               db.execute("SELECT device_id, type, name FROM devices ORDER BY type, device_id")]

    def shown(device_id: str, capability: str) -> bool:
        return not any(types.get(device_id) == t and capability.startswith(p) for t, p in HIDDEN)

    initial, events = [], []
    clock = _stamp(datetime.combine(first, time()))
    first_reading = _stamp(to_local(bounds[0]))
    gaps = [] if first_reading == clock else [{"start": clock, "end": first_reading}]  # before the first poll
    for ts, device_id, capability, kind, old, new in db.execute(
            "SELECT ts, device_id, capability, kind, old, new FROM events ORDER BY ts, id"):
        if kind in SIGNAL_KINDS:
            if types.get(device_id) not in CHATTY:
                continue
            rows = _signal_rows(device_id, kind, new)
        elif kind in VALUE_KINDS and shown(device_id, capability):
            rows = [{"device_id": device_id, "capability": capability, "value": None if kind == "gone" else _value(new)}]
        else:
            if kind == "gap":  # nobody was polling: says nothing about the home
                gaps.append({"start": _stamp(to_local(int(json.loads(old)))), "end": _stamp(to_local(ts))})
            continue
        if kind in ("initial", "stale") and ts == bounds[0]:
            initial.extend(rows)
            continue
        # The dashboard needs events in clock order. When the clocks go back an hour, the repeated
        # hour's events keep their order and wait at the latest time shown so far.
        clock = max(clock, _stamp(to_local(ts)))
        events.extend({"ts": clock, **row} for row in rows)

    learned = storage.export(db) if _has_sensors(db) else {"sensors": [], "readings": []}
    meta = {"household": household, "simulated": simulated,
            "start": _stamp(datetime.combine(first, time())),
            "end": _stamp(datetime.combine(last, time(23, 59, 59))),
            "days": (last - first).days + 1}
    if not simulated:
        meta["updated"] = _stamp(to_local(bounds[1]))  # the real home's last reading: "now" for a live view
    return {
        "meta": meta,
        "devices": devices,
        "initial": initial,
        "events": events,
        "sensors": learned["sensors"],
        "readings": learned["readings"],
        "gaps": gaps,
    }


def _has_sensors(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sensors'").fetchone() is not None


def _signal_rows(device_id: str, kind: str, new: str) -> list[dict]:
    if kind == "returned":
        return [{"device_id": device_id, "capability": "reachable", "value": True}]
    heard = _stamp(to_local(int(json.loads(new))))  # the stale event comes an hour after the device last spoke
    return [{"device_id": device_id, "capability": "reachable", "value": False},
            {"device_id": device_id, "capability": "lastHeard", "value": heard}]


def write(db: sqlite3.Connection, out: Path, *, household: str = "", simulated: bool = True) -> dict:
    """Builds the recording and writes it to out. Returns it."""
    rec = build(db, household=household, simulated=simulated)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    part.write_text(json.dumps(rec, separators=(",", ":"), allow_nan=False) + "\n")
    os.replace(part, out)  # a reader polling the file never sees half of it
    log.info("%s: %d days, %d events, %d sensors, %d readings", out, rec["meta"]["days"], len(rec["events"]),
             len(rec["sensors"]), len(rec["readings"]))
    return rec


def watch(snapshots: Path, out: Path, *, every: float, household: str = "", simulated: bool = True,
          stale_minutes: float = 60, rounds: int | None = None,
          sleep: Callable[[float], None] = clock_time.sleep) -> int:
    """Rebuilds out from the loop's JSONL snapshots every `every` seconds, for a live view while it polls the kit."""
    done = 0
    while rounds is None or done < rounds:
        if done:
            sleep(every)
        done += 1
        if not snapshots.exists():
            log.warning("%s has no snapshots yet", snapshots)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp), record=False)
            try:
                ingest(read_jsonl(snapshots), store, stale_ms=int(stale_minutes * 60_000))
                write(store.db, out, household=household, simulated=simulated)
            except (ValueError, sqlite3.OperationalError) as exc:
                log.warning("not written yet: %s", exc)
            finally:
                store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="source.recording", description="Write a run's history as the dashboard's recording")
    p.add_argument("store", type=Path, nargs="?", help="the folder with home.sqlite3 (the pipeline's --out)")
    p.add_argument("--snapshots", type=Path, metavar="JSONL",
                   help="build from the live loop's snapshots instead, e.g. data/recording.jsonl")
    p.add_argument("--out", type=Path, required=True, help="the JSON file to write, e.g. dashboard/public/recording.json")
    p.add_argument("--household", default="", help="the household's name, shown by the dashboard")
    p.add_argument("--real", action="store_true", help="this history came from the real kit, not the simulator")
    p.add_argument("--every", type=float, metavar="SECONDS", help="with --snapshots: keep rebuilding this often")
    args = p.parse_args(argv)
    if (args.store is None) == (args.snapshots is None):
        p.error("give either a store folder or --snapshots")
    if args.every is not None and args.snapshots is None:
        p.error("--every needs --snapshots")
    if args.snapshots is not None:
        try:
            return watch(args.snapshots, args.out, every=args.every or 0, household=args.household,
                         simulated=not args.real, rounds=None if args.every is not None else 1)
        except KeyboardInterrupt:
            return 0

    path = args.store / "home.sqlite3"
    if not path.exists():
        log.error("%s has no home.sqlite3", args.store)
        return 1
    db = sqlite3.connect(path)
    try:
        write(db, args.out, household=args.household, simulated=not args.real)
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    from .logs import setup_logging

    setup_logging(None)
    raise SystemExit(main())
