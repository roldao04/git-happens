"""A run's history as the dashboard's recording: one JSON file the jury dashboard plays back.

The shape is the dashboard's contract (dashboard/src/data/recording.ts): the devices, their values at the
start, every change after it, and what the learners found. Times are the home's clock time, without an
offset, to the second.

  uv run python -m source.recording data/sim/rosa/ --out dashboard/public/recording.json --household rosa
  uv run python -m source.pipeline --household rosa --days 21 --out data/sim/rosa/ --replace \\
      --recording dashboard/public/recording.json
"""
import argparse
import json
import logging
import sqlite3
from datetime import datetime, time
from pathlib import Path

from .clock import local_day
from .learn import storage, to_local

log = logging.getLogger("source.recording")

# The dashboard never shows camera motion (its spec's anti-goals), so it never gets it.
HIDDEN = (("camera", "motion"),)  # (device type, capability prefix)
VALUE_KINDS = ("initial", "change", "gone")  # the event kinds (source/model.py) that set a value


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
    for ts, device_id, capability, kind, new in db.execute(
            "SELECT ts, device_id, capability, kind, new FROM events ORDER BY ts, id"):
        # "stale", "returned" and "gap" say something about the polling, not a new value
        if kind not in VALUE_KINDS or not shown(device_id, capability):
            continue
        row = {"device_id": device_id, "capability": capability, "value": None if kind == "gone" else _value(new)}
        if kind == "initial" and ts == bounds[0]:
            initial.append(row)
            continue
        # The dashboard needs events in clock order. When the clocks go back an hour, the repeated
        # hour's events keep their order and wait at the latest time shown so far.
        clock = max(clock, _stamp(to_local(ts)))
        events.append({"ts": clock, **row})

    learned = storage.export(db) if _has_sensors(db) else {"sensors": [], "readings": []}
    return {
        "meta": {"household": household, "simulated": simulated,
                 "start": _stamp(datetime.combine(first, time())),
                 "end": _stamp(datetime.combine(last, time(23, 59, 59))),
                 "days": (last - first).days + 1},
        "devices": devices,
        "initial": initial,
        "events": events,
        "sensors": learned["sensors"],
        "readings": learned["readings"],
    }


def _has_sensors(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sensors'").fetchone() is not None


def write(db: sqlite3.Connection, out: Path, *, household: str = "", simulated: bool = True) -> dict:
    """Builds the recording and writes it to out. Returns it."""
    rec = build(db, household=household, simulated=simulated)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, separators=(",", ":"), allow_nan=False) + "\n")
    log.info("%s: %d days, %d events, %d sensors, %d readings", out, rec["meta"]["days"], len(rec["events"]),
             len(rec["sensors"]), len(rec["readings"]))
    return rec


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="source.recording", description="Write a run's history as the dashboard's recording")
    p.add_argument("store", type=Path, help="the folder with home.sqlite3 (the pipeline's --out)")
    p.add_argument("--out", type=Path, required=True, help="the JSON file to write, e.g. dashboard/public/recording.json")
    p.add_argument("--household", default="", help="the household's name, shown by the dashboard")
    p.add_argument("--real", action="store_true", help="this history came from the real kit, not the simulator")
    args = p.parse_args(argv)

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
