"""The event loop: poll → normalize → diff → store → anticipation → triggers → (maybe) agent.

See docs/event-loop.md. Anticipation (docs/anticipation.md) plans Alfred's own decisions ahead
of the learned habits and runs the due ones, before the triggers. Later steps (action ledger +
expectation checker, attribution) slot in between diff and triggers.
"""
import asyncio
import logging
import time
from datetime import datetime

from .actions import Actuator, DryRunActuator, resolve_targets, run_actions
from .context import build_context
from .diff import diff, initially_stale
from .model import Event, State
from .normalize import device_types, fresh_by_type, normalize
from .sources import Source, SourceError
from .store import Store
from .triggers import Triggers, missing_fresh

log = logging.getLogger("source.loop")

try:  # anticipation must never stop the loop, not even by failing to import
    from . import anticipate
    from .learn import to_local
except Exception:
    anticipate = None
    log.exception("anticipation unavailable: the loop runs without it")

PLAN_EVERY_MS = 5 * 60_000  # the plan is redone this often; due decisions are checked every tick


async def run(source: Source, store: Store, triggers: Triggers, agent, *,
              poll_seconds: float, stale_minutes: float, max_ticks: int | None = None,
              actuator: Actuator | None = None, readback_delay_s: float = 0.0) -> None:
    actuator = actuator or DryRunActuator()
    stale_ms = int(stale_minutes * 60_000)
    prev = None
    prev_now: int | None = None
    last_run_ms: int | None = None
    last_plan_ms: int | None = None
    in_gap = False
    blind: list[str] = []  # safety device types with no fresh device
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

        now_blind = missing_fresh(state, now, stale_ms)
        if now_blind != blind:
            if now_blind:
                log.warning("safety trigger can't fire: no fresh %s", ", ".join(now_blind),
                            extra={"data": {"missing": now_blind}})
            else:
                log.info("safety devices reporting again")
            blind = now_blind

        types = device_types(state)
        fresh = fresh_by_type(state, now, stale_ms)
        rules_ran: list[dict] = []
        if anticipate is not None:  # Alfred's own decisions ahead of habits; replays get the dry-run actuator
            last_plan_ms = await _anticipate(store, now, last_plan_ms, actuator, types, fresh, state,
                                             rules_ran, readback_delay_s)
        fired = triggers.match(events, now, types)
        for r in fired:  # hard rules first: code runs their actions, no LLM
            t = triggers.triggers[r.trigger_id]
            if not t.actions:
                continue
            try:
                ready, skipped = resolve_targets(t.actions, fresh)
                for a in skipped:
                    log.warning("rule   %s skipped: %s", r.trigger_id, a["result"],
                                extra={"data": {"trigger_id": r.trigger_id, "action": a}})
                done = await run_actions(actuator, r.trigger_id, ready, types, readback_delay_s=readback_delay_s)
                store.log_rule_run(now, r.trigger_id, skipped + done)
                rules_ran.append({"trigger_id": r.trigger_id, "actions": [
                    {"deviceId": a["args"].get("deviceId"), "body": a["args"].get("body"),
                     "status": a["status"], "verified": a["verified"]} for a in skipped + done]})
            except Exception:  # a broken rule must never stop the loop (or the smoke trigger)
                log.exception("rule   %s actions failed", r.trigger_id, extra={"data": {"trigger_id": r.trigger_id}})
        reasons = [r for r in fired if triggers.triggers[r.trigger_id].wake_agent]
        rules = [r.trigger_id for r in fired if r not in reasons]
        log.info("tick %d @ %s: %d readings, %d events, %s%s (%.1fs)",
                 ticks, _hhmmss(now), len(state), len(events),
                 "wake: " + ", ".join(r.trigger_id for r in reasons) if reasons else "no wake",
                 ", rules: " + ", ".join(rules) if rules else "",
                 time.monotonic() - started,
                 extra={"data": {"tick": ticks, "home_ts": now, "readings": len(state),
                                 "events": len(events), "wake": [r.trigger_id for r in reasons],
                                 "rules": rules}})
        if reasons:
            ctx = build_context(now, reasons, state, store, last_run_ms, stale_ms, rules_ran)
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


async def _anticipate(store: Store, now: int, last_plan_ms: int | None, actuator: Actuator,
                      types: dict[str, str], fresh: dict[str, list[str]], state: State,
                      rules_ran: list[dict], readback_delay_s: float) -> int | None:
    """Re-plans every PLAN_EVERY_MS, then runs the due "do" decisions (each write through the
    guard). Writes are stored as rule runs and told to the agent like hard rules, since its
    `state` was read before them. Never raises: the triggers after it include smoke."""
    try:
        local = to_local(now)
        if last_plan_ms is None or now - last_plan_ms >= PLAN_EVERY_MS:
            last_plan_ms = now
            anticipate.plan_decisions(store.db, local)
        for r in await anticipate.run_due(store.db, local, actuator, types, fresh, state=state,
                                          readback_delay_s=readback_delay_s):
            if not r["actions"]:
                continue  # an expectation, or skipped before any write: nothing changed in the home
            tid = f"decision:{r['decision']['id']}"
            store.log_rule_run(now, tid, r["actions"])
            rules_ran.append({"trigger_id": tid, "actions": [
                {"deviceId": a["args"].get("deviceId"), "body": a["args"].get("body"),
                 "status": a["status"], "verified": a["verified"]} for a in r["actions"]]})
    except Exception:
        log.exception("anticipation failed this tick")
    return last_plan_ms


def _hhmmss(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M:%S")


async def _sleep(source: Source, poll_seconds: float, started: float) -> None:
    if source.realtime:
        await asyncio.sleep(max(0.0, poll_seconds - (time.monotonic() - started)))
