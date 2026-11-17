"""Ask the agent something once, on the live house (no loop).

  uv run python -m source.ask "make the lamp blue"
  uv run python -m source.ask "is everything ok?"

Same LangGraph agent and tools as the live loop (reads + lamp/plug writes), woken by a
"person" reason instead of a trigger. Runs are stored with the loop's history.
"""
import argparse
import asyncio
import json
import logging
import sys

from .agent import LangGraphAgent, make_model, mcp_tools
from .config import load_settings
from .context import build_context
from .logs import setup_logging
from .model import WakeReason
from .normalize import normalize
from .sources import McpSource
from .store import Store

log = logging.getLogger("source.ask")


async def _main(request: str) -> None:
    s = load_settings()
    setup_logging(s.data_dir / "logs", s.log_level, console=True)
    if not s.api_key or not s.openrouter_api_key:
        sys.exit("CGH_API_KEY and OPENROUTER_API_KEY must be set in .env")

    source = McpSource(s.api_key, proxy_log=s.data_dir / "logs" / "mcp-remote.log")
    store = Store(s.data_dir, record=False)
    try:
        now, home = await source.fetch()
        reasons = [WakeReason("person", f"A person asked: {request}")]
        ctx = build_context(now, reasons, normalize(home), store, None, int(s.stale_minutes * 60_000))

        async def tools():
            return await mcp_tools(await source.session())

        agent = LangGraphAgent(make_model(s.agent_model, s.openrouter_api_key),
                               tools_provider=tools, max_steps=s.agent_max_steps, allow_plug_on=True)
        try:
            out = await agent.run(ctx)
        except Exception as exc:  # e.g. the model provider refusing the request
            log.debug("agent run failed", exc_info=True)
            sys.exit(f"\nagent failed: {getattr(exc, 'message', None) or exc}")
        store.log_agent_run(now, ctx["reasons"], ctx, out)

        print("\n" + out["notes"])
        for a in out["actions"]:
            print(f"  {'✗' if a['error'] else '✓'} {a['tool']} {json.dumps(a['args'])}\n    → {a['result'][:300]}")
    finally:
        await source.close()
        store.close()


def main() -> None:
    p = argparse.ArgumentParser(prog="source.ask", description="Ask the agent once, on the live house")
    p.add_argument("request", help='e.g. "make the lamp blue"')
    asyncio.run(_main(p.parse_args().request))


if __name__ == "__main__":
    main()
