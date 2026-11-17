"""Run the loop.

  uv run python -m source                         # live: poll the Connected Home MCP
  uv run python -m source --ticks 3 --poll 5      # quick live check
  uv run python -m source --replay tests/fixtures/morning.jsonl   # replay a recording, no kit needed
  uv run python -m source --stub                  # live, but with the stub agent (no LLM, no writes)
  uv run python -m source -v                      # also show DEBUG on the console

Live uses the LangGraph agent (needs OPENROUTER_API_KEY; it can change the lamp and plug).
Without the key, or with --stub, the stub agent runs instead. --replay gives the LLM no tools.

Logs go to <data dir>/logs/ (loop.log, loop.jsonl). Read history with `python -m source.show`.
"""
import argparse
import asyncio
import logging
import sys

from .actions import McpActuator
from .agent import StubAgent
from .config import load_settings
from .logs import setup_logging
from .loop import run
from .sources import McpSource, ReplaySource
from .store import Store
from .triggers import Triggers, seed_triggers

log = logging.getLogger("source.main")


def _make_agent(s, args, source):
    if args.stub:
        return StubAgent()
    if not s.openrouter_api_key:
        log.warning("OPENROUTER_API_KEY is not set: falling back to the stub agent")
        return StubAgent()
    from .agent import LangGraphAgent, make_model, mcp_tools  # lazy: langchain imports are slow

    model = make_model(s.agent_model, s.openrouter_api_key)
    if args.replay:
        # Replay: NO tools, so a replay never touches the real kit.
        return LangGraphAgent(model, tools_provider=None, max_steps=s.agent_max_steps)

    async def tools_provider():
        # Share the loop's mcp-remote session; read tools + the lamp/plug write tool.
        return await mcp_tools(await source.session())

    return LangGraphAgent(model, tools_provider=tools_provider, max_steps=s.agent_max_steps)


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
    dry_run = bool(args.replay or args.stub)
    try:
        await run(source, store, Triggers(seed_triggers(s.heartbeat_minutes)),
                  _make_agent(s, args, source),
                  poll_seconds=args.poll or s.poll_seconds, stale_minutes=s.stale_minutes,
                  max_ticks=args.ticks,
                  # replays and --stub never write; live runs let hard rules act
                  actuator=None if dry_run else McpActuator(source),
                  readback_delay_s=0.0 if dry_run else 2.0)
    finally:
        await source.close()
        store.close()
        log.info("loop stopped")


def main() -> None:
    p = argparse.ArgumentParser(prog="source", description="Connected Home event loop")
    p.add_argument("--replay", help="JSONL recording to replay instead of polling the MCP")
    p.add_argument("--stub", action="store_true", help="use the stub agent (no LLM calls, no device writes)")
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
