"""Simulate days and learn from them, in one go (docs/contracts.md): parts 1 to 6.

The simulator's snapshots go straight into ingest, with no JSONL file in between. At the end of each simulated
day the happening extractor (part 4, which finds rest, part 3) runs, then the learners (parts 5 and 6) if
they are installed.

  uv run python -m source.pipeline --household rosa --days 14 --out data/sim/rosa/ --replace
  uv run python -m source.pipeline --houses 5 --days 14 --out data/sim/houses/ --replace   # one folder per house
  uv run python -m source.pipeline --from data/sim/rosa.jsonl --out data/sim/rosa/ --replace
  uv run python -m source.pipeline --household rosa --days 21 --out data/sim/rosa/ --replace \\
      --recording dashboard/public/recording.json                                    # and the dashboard's file
  uv run python -m source.pipeline --household rosa --days 21 --out data/sim/rosa/ --replace --name   # and name sensors (LLM)
"""
import argparse
import json
import logging
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .clock import day_bounds
from .ingest import Snapshot, ingest, read_jsonl
from .learn.happenings import save_happenings
from .simulate import DEFAULT_START, PRESETS, REAL_HOME, Household, generate, household_from_dict, houses
from .recording import write as write_recording
from .store import Store

log = logging.getLogger("source.pipeline")

STALE_MS = 60 * 60_000
GAP_MS = 30 * 60_000


def _learners() -> Callable[[sqlite3.Connection, date], object] | None:
    """learn_day from person 3's branch (parts 5 and 6), or None until it is merged."""
    try:
        from .learn.run import learn_day
    except ModuleNotFoundError as exc:
        if exc.name not in ("source.learn.run", f"{__package__}.learn.run"):
            raise  # the learners are there but something they import is missing
        log.info("no source.learn.run yet: skipping the grouper and habit learner (parts 5 and 6)")
        return None
    return learn_day


def _sensors(db: sqlite3.Connection) -> int | None:
    try:
        return db.execute("SELECT COUNT(*) FROM sensors").fetchone()[0]
    except sqlite3.OperationalError:
        return None


def run(snaps: Iterable[Snapshot], out: Path, *, replace: bool = False,
        before_learn: Callable[[sqlite3.Connection, date], None] | None = None,
        after_learn: Callable[[sqlite3.Connection, date], None] | None = None) -> int:
    """Ingests snaps into out/home.sqlite3 and learns at each day end. Returns how many days ran.

    `before_learn(db, day)` runs at each day end once that day's happenings are saved, before the learners
    see it: the store then holds what Alfred knew during that day (the 3D view's /learned uses it).
    `after_learn(db, day)` runs right after the learners, e.g. the namer (part 7).
    """
    db = out / "home.sqlite3"
    if db.exists():
        if not replace:
            raise FileExistsError(f"{out} already has a history; use --replace to start it again")
        db.unlink()
    learn_day = _learners()
    store = Store(out, record=False)
    days = 0

    def on_day_end(day: date) -> None:
        nonlocal days
        days += 1
        new = save_happenings(store, day_bounds(day)[1])
        line = f"{day}  {new:3d} new happenings"
        if before_learn is not None:
            before_learn(store.db, day)
        if learn_day is not None:
            before = _sensors(store.db) or 0
            learn_day(store.db, day)
            after = _sensors(store.db)
            line += f"  {after - before:3d} new sensors" if after is not None else "  (no sensors table)"
            if after_learn is not None:
                after_learn(store.db, day)
        log.info(line)

    try:
        stats = ingest(snaps, store, stale_ms=STALE_MS, gap_ms=GAP_MS, on_day_end=on_day_end)
    finally:
        store.close()
    log.info("%s: %d snapshots, %d events, %d days", db, stats.snapshots, stats.events, days)
    return days


def _record(out: Path, recording: Path | None, household: str) -> None:
    """Writes the run in out as the dashboard's recording (source/recording.py), when asked to."""
    if recording is None:
        return
    db = sqlite3.connect(out / "home.sqlite3")
    try:
        write_recording(db, recording, household=household)
    finally:
        db.close()


def _namer(enabled: bool) -> Callable[[sqlite3.Connection, date], None] | None:
    """The namer (part 7) as an after_learn step, or None when not asked for. Needs OPENROUTER_API_KEY."""
    if not enabled:
        return None
    from .agent import make_model
    from .config import load_settings
    from .learn.namer import name_all

    settings = load_settings()
    if not settings.openrouter_api_key:
        raise ValueError("--name needs OPENROUTER_API_KEY (in .env)")
    model = make_model(settings.agent_model, settings.openrouter_api_key)

    def name(db: sqlite3.Connection, day: date) -> None:
        name_all(db, model)
    return name


def _parse(lines: Iterator[str]) -> Iterator[Snapshot]:
    for line in lines:
        row = json.loads(line)
        yield row["ts"], row["home"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="source.pipeline", description="Simulate days, ingest them and learn at each day end")
    p.add_argument("--household", default="rosa", help=f"{', '.join(PRESETS)}, or {REAL_HOME}")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--out", type=Path, required=True, help="folder for home.sqlite3 (with --houses: one subfolder per house)")
    p.add_argument("--replace", action="store_true", help="delete the folder's existing history first")
    p.add_argument("--houses", type=int, help="this many made-up homes instead of --household")
    p.add_argument("--from", dest="source", type=Path, help="ingest this JSONL file instead of simulating")
    p.add_argument("--config", type=Path, help="a JSON file of Household settings, applied on top of --household")
    p.add_argument("--truth", type=Path, help="also write the answer key (what was planted) as JSON")
    p.add_argument("--recording", type=Path, help="also write the run as the dashboard's recording (JSON)")
    p.add_argument("--name", action="store_true", help="name new sensors with the LLM at each day end (part 7)")
    p.add_argument("--start", type=date.fromisoformat, help="first day, YYYY-MM-DD (default: the household's own, "
                   f"mostly {DEFAULT_START}; the demo house starts on 2026-09-21)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--interval", type=int, default=300, help="seconds between snapshots when nothing changes")
    args = p.parse_args(argv)
    if args.recording and args.houses:
        p.error("--recording takes one home, not --houses")

    try:
        namer = _namer(args.name)
        if args.source:
            run(read_jsonl(args.source), args.out, replace=args.replace, after_learn=namer)
            _record(args.out, args.recording, args.source.stem)
        elif args.houses:
            for h in houses(args.houses, args.seed):
                lines, _ = generate(h, args.days, start=args.start, seed=args.seed, interval_s=args.interval)
                log.info("%s", h.name)
                run(_parse(lines), args.out / h.name, replace=args.replace, after_learn=namer)
        else:
            household: Household | str = args.household
            if args.config:
                if household == REAL_HOME:
                    p.error(f"{REAL_HOME} has no settings")
                base = asdict(PRESETS[household]) if household in PRESETS else {}
                household = household_from_dict(base | json.loads(args.config.read_text()))
            lines, truth = generate(household, args.days, start=args.start, seed=args.seed, interval_s=args.interval)
            run(_parse(lines), args.out, replace=args.replace, after_learn=namer)
            _record(args.out, args.recording, household if isinstance(household, str) else household.name)
            if args.truth:
                args.truth.parent.mkdir(parents=True, exist_ok=True)
                args.truth.write_text(json.dumps(truth, indent=1))
    except (ValueError, FileExistsError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    from .logs import setup_logging

    setup_logging(None)
    raise SystemExit(main())
