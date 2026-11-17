"""Read back what the loop stored.

  uv run python -m source.show events            # last 30 events
  uv run python -m source.show events --since 2h -n 100
  uv run python -m source.show wakes             # agent wake-ups and why
  uv run python -m source.show wakes --context   # ...with the full context the agent got
  uv run python -m source.show state             # latest snapshot, one line per device
  uv run python -m source.show --replay events   # same, for the replay data dir
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime

from .config import load_settings
from .normalize import normalize


def _t(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M:%S")


def _since_ms(spec: str | None) -> int:
    if not spec:
        return 0
    m = re.fullmatch(r"(\d+)([smhd])", spec)
    if not m:
        sys.exit("--since takes e.g. 30m, 2h, 1d")
    secs = int(m[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]
    return int((time.time() - secs) * 1000)


def events(db, args):
    rows = db.execute(
        "SELECT ts, kind, device, capability, old, new, source FROM events"
        " WHERE ts >= ? ORDER BY ts DESC, id DESC LIMIT ?", (_since_ms(args.since), args.n)).fetchall()
    for ts, kind, dev, cap, old, new, src in reversed(rows):
        old, new = json.loads(old), json.loads(new)
        what = f"{dev}.{cap}: {old!r} → {new!r}" if kind == "change" else f"{dev} {kind} {new!r}"
        print(f"{_t(ts)}  {kind:<8} {what}" + (f"  [{src}]" if src != "unknown" else ""))
    if not rows:
        print("no events")


def wakes(db, args):
    rows = db.execute(
        "SELECT ts, reasons, context, output FROM agent_runs WHERE ts >= ?"
        " ORDER BY ts DESC, id DESC LIMIT ?", (_since_ms(args.since), args.n)).fetchall()
    for ts, reasons, context, output in reversed(rows):
        rs = json.loads(reasons)
        print(f"{_t(ts)}  wake: {', '.join(r['trigger_id'] for r in rs)}")
        for r in rs:
            for e in r["events"]:
                print(f"           {r['trigger_id']}: {e}")
        if args.context:
            print(json.dumps(json.loads(context), indent=2, ensure_ascii=False))
            print("output:", output)
    if not rows:
        print("no agent runs")


def state(db, args):
    row = db.execute("SELECT ts, home FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        print("no snapshots")
        return
    ts, home = row
    print(f"snapshot at {_t(ts)}")
    by_dev: dict[str, list] = {}
    seen: dict[str, int] = {}
    for r in normalize(json.loads(home)).values():
        by_dev.setdefault(r.device, []).append(f"{r.capability}={r.value}")
        if r.comm_ts:
            seen[r.device] = r.comm_ts
    for dev, caps in by_dev.items():
        last = f" (last seen {_t(seen[dev])})" if dev in seen else ""
        print(f"  {dev}{last}\n    " + ", ".join(caps))


def main() -> None:
    p = argparse.ArgumentParser(prog="source.show", description="Read what the loop stored")
    p.add_argument("--replay", action="store_true", help="read the replay data dir")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("events", "wakes"):
        sp = sub.add_parser(name)
        sp.add_argument("-n", type=int, default=30)
        sp.add_argument("--since", help="e.g. 30m, 2h, 1d")
        if name == "wakes":
            sp.add_argument("--context", action="store_true")
    sub.add_parser("state")
    args = p.parse_args()

    s = load_settings()
    path = (s.data_dir / "replay" if args.replay else s.data_dir) / "home.sqlite3"
    if not path.exists():
        sys.exit(f"no database at {path}: run the loop first")
    db = sqlite3.connect(path)
    {"events": events, "wakes": wakes, "state": state}[args.cmd](db, args)


if __name__ == "__main__":
    main()
