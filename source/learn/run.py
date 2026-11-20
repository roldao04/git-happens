"""Run the learners: `learn_day` for the daily loop, `learn` for tests, and a CLI.

  uv run python -m source.learn.run --happenings tests/fixtures/happenings-14d.jsonl --out data/sensors.json
  uv run python -m source.learn.run --store data/sim/rosa/        # a history from source.pipeline

The CLI replays a history one day at a time, so sensors appear on the day they're learned. The
pipeline (source/pipeline.py) calls learn_day itself at each simulated day end.
"""
import argparse
import importlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import SLOW_CAPABILITIES, Happening, activity_edges, is_activity, storage
from .habits import HOLE_MIN, Habit, find_habits, quiet_happenings
from .kinds import Kind, Link, find_kinds, find_links
from .rhythm import Rhythm, Watched

log = logging.getLogger("source.learn.run")


@dataclass
class Learned:
    kinds: list[Kind] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    habits: list[Habit] = field(default_factory=list)


def learn(happenings: list[Happening], as_of: date, known_kinds: list[Kind] | None = None,
          link_null: str = "rhythm", slow: frozenset[str] = SLOW_CAPABILITIES,
          polls: list[datetime] | None = None) -> Learned:
    """Kinds → links → habits from every happening that started by the end of `as_of`.

    `polls`: when the loop polled the home (its heartbeat). None = always watched.
    """
    end = datetime.combine(as_of + timedelta(days=1), time())
    hs = [h for h in happenings if h.start < end]
    if not hs:
        return Learned()
    watched = Watched(polls, HOLE_MIN)
    # People's devices (not the room's slow signals, which move by themselves) say whether someone
    # is in and when the home is quiet. Everything else treats all signals alike: the room's daily
    # swing is a recurring fact too, so it is grouped and can become a habit like any other.
    # A phone at home all evening is activity only where it connects and leaves (activity_edges): else it would
    # leave no quiet night at all, and no "Waking up".
    activity = activity_edges([h for h in hs if is_activity(h, slow)])
    days = counted_days(activity, min(h.start for h in hs).date(), as_of, watched)
    rhythm = Rhythm([h.start for h in hs] + [h.end for h in hs])
    everything = hs + quiet_happenings(activity, watched)
    kinds = find_kinds(everything, known_kinds)
    links = find_links(everything, kinds, days, rhythm, link_null) if days else []
    habits = find_habits(everything, kinds, links, days, rhythm, activity, watched) if days else []
    return Learned(kinds, links, habits)


def counted_days(activity: list[Happening], first: date, last: date, watched: Watched) -> list[date]:
    """Days that tell us something: the home was watched and someone was in (any activity).

    A day with polls but no activity at all is a day away; a day with no polls is an outage.
    Neither counts as a missed habit (heartbeat + expected activity, docs/learners.md).
    """
    active = {h.start.date() for h in activity}
    out = []
    d = first
    while d <= last:
        start = datetime.combine(d, time())
        if d in active and watched.saw(start, start + timedelta(days=1)):
            out.append(d)
        d += timedelta(days=1)
    return out


def learn_day(db: sqlite3.Connection, day: date, link_null: str = "rhythm") -> list[dict]:
    """Run at the end of each (simulated) day. Returns the sensors (patterns) first learned that day.

    Then, on top of the patterns: habits (source/learn/grouping.py) and occasions (source/learn/occasions.py,
    which reads the habits to say what an occasion moves). Each is a layer on what is already stored, so a
    failure in one is logged and never loses the patterns.
    """
    storage.init(db)
    until = datetime.combine(day + timedelta(days=1), time())
    hs = storage.load_happenings(db, until)
    result = learn(hs, day, storage.load_kinds(db), link_null, polls=storage.load_polls(db, until))
    storage.save_kinds(db, result.kinds)
    storage.save_happening_kinds(db, hs)
    new = storage.save_habits(db, result.habits, day)
    for module, step in ((".grouping", "learn_habits"), (".occasions", "learn_occasions"),
                         (".expectations", "learn_expectations")):
        try:  # imported here, not at the top: they import this module
            getattr(importlib.import_module(module, __package__), step)(db, day)
        except Exception:
            log.exception("%s failed on %s", step, day)
    return new


def describe(sensor: dict) -> str:
    p = sensor["pattern"]
    when = f"{', '.join(p['weekdays'])} " if p.get("weekdays") else ""
    return (f"{sensor['id']}  {' → '.join(p['parts'])}  {when}around {p['time_of_day']}"
            f" ({p['days_seen']}/{p['days_total']} days)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay a history day by day through the learners.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--happenings", type=Path, help="JSONL, one happening per line (hand-made data)")
    src.add_argument("--store", type=Path, help="a folder with an ingested home.sqlite3 (source.pipeline's --out); "
                                                 "its learned sensors are cleared and learned again")
    ap.add_argument("--out", type=Path, help="write the sensors JSON here")
    ap.add_argument("--link-null", choices=("rhythm", "other_day"), default="rhythm",
                    help="chance model for links: rhythm (lenient) or other_day (strict)")
    ap.add_argument("--db", default=":memory:", help="with --happenings: SQLite file (default: in memory)")
    args = ap.parse_args()

    if args.store:
        db = sqlite3.connect(args.store / "home.sqlite3")
        storage.init(db)
        storage.reset_learned(db)
        days = [date.fromisoformat(d) for (d,) in db.execute("SELECT day FROM days ORDER BY day")]
    else:
        rows = [json.loads(l) for l in args.happenings.read_text().splitlines() if l.strip()]
        db = sqlite3.connect(args.db)
        storage.init(db)
        storage.add_happenings(db, rows)
        first = min(Happening.from_dict(r).start for r in rows).date()
        last = max(Happening.from_dict(r).start for r in rows).date()
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    if not days:
        print("no days to learn from")
        return
    for n, day in enumerate(days, 1):
        for s in learn_day(db, day, args.link_null):
            print(f"day {n} ({day}): learned {describe(s)}")

    out = storage.export(db, days[-1])
    print(f"\n{len(out['sensors'])} sensors after {len(days)} days:")
    for s in out["sensors"]:
        print(f"  [{s['status']}] {describe(s)}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, indent=2))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
