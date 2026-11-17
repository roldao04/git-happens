"""Run the loop.

  uv run python -m source                         # live: poll the Connected Home MCP
  uv run python -m source --ticks 3 --poll 5      # quick live check
  uv run python -m source --replay tests/fixtures/morning.jsonl   # replay a recording, no kit needed
  uv run python -m source -v                      # also show DEBUG on the console

Logs go to <data dir>/logs/ (loop.log, loop.jsonl). Read history with `python -m source.show`.
"""
import argparse
import asyncio
import logging
import sys

from .agent import StubAgent
from .config import load_settings
from .logs import setup_logging
from .loop import run
from .sources import McpSource, ReplaySource
from .store import Store
from .triggers import Triggers, seed_triggers

log = logging.getLogger("source.main")


async def _main(args) -> None:
    s = load_settings()
    data_dir = s.data_dir / "replay" if args.replay else s.data_dir
    setup_logging(data_dir / "logs", "DEBUG" if args.verbose else s.log_level, console=not args.quiet)
    log.info("data in %s", data_dir)

    if args.replay:
        source = ReplaySource(args.replay)
        (data_dir / "home.sqlite3").unlink(missing_ok=True)  # each replay starts clean
        store = Store(data_dir, record=False)
    else:
        if not s.api_key:
            sys.exit("CGH_API_KEY is not set: copy .env.example to .env and add the team API key")
        source = McpSource(s.api_key, proxy_log=data_dir / "logs" / "mcp-remote.log")
        store = Store(data_dir)
    try:
        await run(source, store, Triggers(seed_triggers(s.heartbeat_minutes)), StubAgent(),
                  poll_seconds=args.poll or s.poll_seconds, stale_minutes=s.stale_minutes,
                  max_ticks=args.ticks)
    finally:
        await source.close()
        store.close()
        log.info("loop stopped")


def main() -> None:
    p = argparse.ArgumentParser(prog="source", description="Connected Home event loop")
    p.add_argument("--replay", help="JSONL recording to replay instead of polling the MCP")
    p.add_argument("--ticks", type=int, help="stop after this many polls")
    p.add_argument("--poll", type=float, help="seconds between polls (overrides LOOP_POLL_SECONDS)")
    p.add_argument("-v", "--verbose", action="store_true", help="show DEBUG on the console")
    p.add_argument("-q", "--quiet", action="store_true", help="no console output (files only)")
    args = p.parse_args()
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
