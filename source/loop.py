"""The event loop: poll → normalize → diff → store → triggers → (maybe) agent.

See docs/event-loop.md. Later steps (action ledger + expectation checker, attribution) slot in
between diff and triggers.
"""
import asyncio
import logging
import time
from datetime import datetime

from .context import build_context
from .diff import diff, initially_stale
from .model import Event
from .normalize import normalize
from .sources import Source, SourceError
from .store import Store
from .triggers import Triggers

log = logging.getLogger("source.loop")


async def run(source: Source, store: Store, triggers: Triggers, agent, *,
              poll_seconds: float, stale_minutes: float, max_ticks: int | None = None) -> None:
    stale_ms = int(stale_minutes * 60_000)
    prev = None
    prev_now: int | None = None
    last_run_ms: int | None = None
    in_gap = False
    ticks = 0
    log.info("loop started: %s, poll every %ss, stale after %s min",
             type(source).__name__, poll_seconds, stale_minutes,
             extra={"data": {"source": type(source).__name__, "poll_seconds": poll_seconds,
                             "stale_minutes": stale_minutes, "triggers": list(triggers.triggers)}})

    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        started = time.monotonic()
        try:
            now, home = await source.fetch()
        except StopAsyncIteration:
            log.info("replay finished")
            break
        except SourceError as exc:
            if not in_gap:  # record the gap once; never treat a failed poll as "all zero"
                now = int(time.time() * 1000)
                store.save_events([Event(now, "loop", "Loop", "poll", "gap", None, str(exc))])
                log.warning("poll failed, recording a gap: %s", exc, extra={"data": {"error": str(exc)}})
                in_gap = True
            else:
                log.debug("poll still failing: %s", exc)
            await _sleep(source, poll_seconds, started)
            continue

        if in_gap:
            log.info("polling recovered")
            store.save_events([Event(now, "loop", "Loop", "poll", "gap", None, "recovered")])
            in_gap = False

        state = normalize(home)
        events = diff(prev, state, now, stale_ms, prev_now) if prev is not None else initially_stale(state, now, stale_ms)
        store.save(now, home, events)
        for e in events:
            log.info("event  %s", e.describe(), extra={"data": {"event": e.to_dict()}})

        reasons = triggers.match(events, now)
        log.info("tick %d @ %s: %d readings, %d events, %s (%.1fs)",
                 ticks, _hhmmss(now), len(state), len(events),
                 "wake: " + ", ".join(r.trigger_id for r in reasons) if reasons else "no wake",
                 time.monotonic() - started,
                 extra={"data": {"tick": ticks, "home_ts": now, "readings": len(state),
                                 "events": len(events), "wake": [r.trigger_id for r in reasons]}})
        if reasons:
            ctx = build_context(now, reasons, state, store, last_run_ms)
            log.debug("agent context", extra={"data": {"context": ctx}})
            try:
                out = await agent.run(ctx)
            except Exception:  # a broken agent must never stop the loop
                log.exception("agent run failed")
                out = {"error": "agent run failed"}
            store.log_agent_run(now, ctx["reasons"], ctx, out)
            triggers.apply_updates(out.get("trigger_updates", []))
            last_run_ms = now

        prev, prev_now = state, now
        await _sleep(source, poll_seconds, started)


def _hhmmss(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M:%S")


async def _sleep(source: Source, poll_seconds: float, started: float) -> None:
    if source.realtime:
        await asyncio.sleep(max(0.0, poll_seconds - (time.monotonic() - started)))
