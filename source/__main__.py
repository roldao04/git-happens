"""Run the loop.

  uv run python -m source                         # live: poll the Connected Home MCP
  uv run python -m source --ticks 3 --poll 5      # quick live check
  uv run python -m source --replay tests/fixtures/morning.jsonl   # replay a recording, no kit needed
  uv run python -m source --stub                  # live, but with the stub agent (no LLM, no writes)
  uv run python -m source -v                      # also show DEBUG on the console
  uv run python -m source --look-only --api      # the real home and Alfred, but nothing is sent; the phone's API

Live uses the LangGraph agent (needs OPENROUTER_API_KEY; it can change the lamp and plug).
--look-only keeps the LLM and the phone app but sends no write (the app's npm run home).
Without the key, or with --stub, the stub agent runs instead. --replay gives the LLM no tools.
The live loop learns the home's habits once a night (not in replays).

Logs go to <data dir>/logs/ (loop.log, loop.jsonl). Read history with `python -m source.show`.
"""
import argparse
import asyncio
import logging
import os
import signal
import sys

from .actions import LookOnlyActuator, McpActuator
from .agent import StubAgent
from .config import load_settings
from .logs import setup_logging
from .loop import run
from .sources import McpSource, ReplaySource
from .store import Store
from .triggers import Triggers, seed_triggers

log = logging.getLogger("source.main")


def _make_agent(s, args, source, *, allow_plug_on: bool = False):
    """The loop's agent; with allow_plug_on, the one that answers a person (the phone app's chat)."""
    if args.stub:
        return StubAgent("I'm the stub agent (--stub): no LLM here, so I can't answer that.")
    if not s.openrouter_api_key:
        log.warning("OPENROUTER_API_KEY is not set: falling back to the stub agent")
        return StubAgent("I can't think right now: there's no OPENROUTER_API_KEY in .env. "
                         "The devices and the plan still work.")
    from .agent import LangGraphAgent, make_model, mcp_tools  # lazy: langchain imports are slow

    model = make_model(s.agent_model, s.openrouter_api_key)
    if args.replay:
        # Replay: NO tools, so a replay never touches the real kit.
        return LangGraphAgent(model, tools_provider=None, max_steps=s.agent_max_steps, allow_plug_on=allow_plug_on,
                              send=False)

    async def tools_provider():
        # Share the loop's MCP session; read tools + the lamp/plug write tool.
        return await mcp_tools(await source.session())

    return LangGraphAgent(model, tools_provider=tools_provider, max_steps=s.agent_max_steps, allow_plug_on=allow_plug_on,
                          send=not args.look_only)


async def _main(args) -> None:
    # Stopped with SIGTERM (npm run home, timeout, a service manager): clean up as on Ctrl-C, so the MCP
    # session and the store are closed properly.
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    except (NotImplementedError, RuntimeError, AttributeError):
        pass
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
        source = McpSource(s.api_key)
        store = Store(data_dir)
    dry_run = bool(args.replay or args.stub)
    look_only = bool(args.look_only and not dry_run)
    # replays and --stub never write; --look-only refuses every write (so nothing pretends to be sent);
    # live runs let hard rules and decisions act
    actuator = None if dry_run else LookOnlyActuator() if look_only else McpActuator(source)
    readback_delay_s = 0.0 if dry_run or look_only else 2.0
    api = None
    if args.api:
        from .phone_api import PhoneApi

        api = PhoneApi(store, actuator, _make_agent(s, args, source, allow_plug_on=True),
                       household="replay" if args.replay else "your home", dry_run=dry_run,
                       stale_ms=int(s.stale_minutes * 60_000), readback_delay_s=readback_delay_s, look_only=look_only)
        api.serve(args.api_port)
    try:
        await run(source, store, Triggers(seed_triggers(s.heartbeat_minutes)),
                  _make_agent(s, args, source),
                  poll_seconds=args.poll or s.poll_seconds, stale_minutes=s.stale_minutes,
                  max_ticks=args.ticks, actuator=actuator, readback_delay_s=readback_delay_s, api=api,
                  learn_nightly=not args.replay)
    finally:
        if api is not None:
            api.close()
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
    p.add_argument("--look-only", action="store_true",
                   help="the real home and the LLM, but no write is ever sent (the phone app's npm run home)")
    p.add_argument("--api", action="store_true", help="serve the phone app's API on localhost (source/phone_api.py)")
    p.add_argument("--api-port", type=int, default=int(os.environ.get("ALFRED_API_PORT", "4790")),
                   help="the API's port (default 4790, or ALFRED_API_PORT)")
    args = p.parse_args()
    try:
        asyncio.run(_main(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
