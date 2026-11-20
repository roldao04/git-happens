"""The event loop: poll → normalize → diff → store → anticipation → triggers + learned wakes →
(maybe) agent → its proposals as decisions.

See docs/event-loop.md. Anticipation (docs/anticipation.md) plans Alfred's own decisions ahead
of the learned habits and runs the due ones, before the triggers. Learned wakes
(source/wake.py) wake the agent from what was learned (a habit soon, an arrival, a missing
habit, an occasion); its proposals become decisions with a confidence set by code, and the due
"do" ones run in the same tick. Later steps (action ledger + expectation checker, attribution)
slot in between diff and triggers.
"""
import asyncio
import logging
import time
from datetime import date, datetime, timedelta

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
    from . import anticipate, wake
    from .learn import to_local
    from .learn.happenings import save_happenings
    from .learn.run import learn_day
except Exception:
    anticipate = wake = save_happenings = learn_day = None
    log.exception("anticipation unavailable: the loop runs without it")

PLAN_EVERY_MS = 5 * 60_000  # the plan is redone this often; due decisions are checked every tick
WAKE_EVERY_MS = 60_000  # the learned wakes are looked for at most this often (each look reads the forecast)
OWN_WRITE_MS = 15 * 60_000  # a change to the value Alfred (or a rule) wrote, this soon after, was that write


async def run(source: Source, store: Store, triggers: Triggers, agent, *,
              poll_seconds: float, stale_minutes: float, max_ticks: int | None = None,
              actuator: Actuator | None = None, readback_delay_s: float = 0.0, api=None,
              learn_nightly: bool = False) -> None:
    """`learn_nightly`: the live home learns its habits by itself, once a night (as source/pipeline.py does
    for a simulated one). Off for replays and the demo, whose stores are learned beforehand."""
    actuator = actuator or DryRunActuator()
    stale_ms = int(stale_minutes * 60_000)
    prev = None
    prev_now: int | None = None
    last_run_ms: int | None = None
    last_plan_ms: int | None = None
    last_wake_ms: int | None = None
    in_gap = False
    blind: list[str] = []  # safety device types with no fresh device
    ticks = 0
    known: set[str] = set()  # devices already in the store's `devices` table
    writes: list[tuple[int, str, str, object, str]] = []  # (ms, device, capability, value, source) sent recently
    learned_for: date | None = None  # the last day the nightly learning looked at
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
            if api is not None:
                api.poll_failed(str(exc))
            if not in_gap:  # never treat a failed poll as "all zero"; the gap is saved when polling recovers
                log.warning("poll failed, a gap begins: %s", exc, extra={"data": {"error": str(exc)}})
                in_gap = True
            else:
                log.debug("poll still failing: %s", exc)
            await _sleep(source, poll_seconds, started)
            continue

        if in_gap:
            log.info("polling recovered")
            if prev_now is not None:  # as ingest writes it: old = the last poll before, new = the first after
                store.save_events([Event(now, "loop", "Loop", "poll", "gap", prev_now, now)])
            in_gap = False

        state = normalize(home, prev)  # with prev, a phone that left reads "off"
        events = diff(prev, state, now, stale_ms, prev_now) if prev is not None else initially_stale(state, now, stale_ms)
        if api is not None:  # the phone app's writes since the last poll: the changes they cause are a person's
            writes += api.take_writes()
        writes = [w for w in writes if now - w[0] <= OWN_WRITE_MS]
        _attribute(events, writes)
        new_devices = {r.device_id: (r.device_type, r.device) for r in state.values() if r.device_id not in known}
        if new_devices:  # as ingest does: the learners read names here (a guest's phone has none)
            store.save_devices(now, new_devices)
            known.update(new_devices)
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

        if learn_nightly and learn_day is not None:
            yesterday = to_local(now).date() - timedelta(days=1)
            if yesterday != learned_for:  # the first tick of a day (or of the run)
                learned_for = yesterday
                _learn_night(store, now, yesterday)

        types = device_types(state)
        fresh = fresh_by_type(state, now, stale_ms)
        if api is not None:
            api.update(now, state, types, fresh)
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
                writes += _sent(now, done, f"scenario:{r.trigger_id}")
                store.log_rule_run(now, r.trigger_id, skipped + done)
                rules_ran.append({"trigger_id": r.trigger_id, "actions": [
                    {"deviceId": a["args"].get("deviceId"), "body": a["args"].get("body"),
                     "status": a["status"], "verified": a["verified"]} for a in skipped + done]})
            except Exception:  # a broken rule must never stop the loop (or the smoke trigger)
                log.exception("rule   %s actions failed", r.trigger_id, extra={"data": {"trigger_id": r.trigger_id}})
        reasons = [r for r in fired if triggers.triggers[r.trigger_id].wake_agent]
        rules = [r.trigger_id for r in fired if r not in reasons]
        if wake is not None and (last_wake_ms is None or now - last_wake_ms >= WAKE_EVERY_MS):
            last_wake_ms = now
            reasons += _learned(store, now, state)
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
            if anticipate is not None and out.get("proposals"):
                out["decisions"] = await _proposals(store, now, out["proposals"], actuator, types, fresh,
                                                    state, readback_delay_s, rules_ran)
            store.log_agent_run(now, ctx["reasons"], ctx, out)
            triggers.apply_updates(out.get("trigger_updates", []))
            last_run_ms = now

        for ran in rules_ran:  # Alfred's decisions (the rules' writes were taken above)
            if ran["trigger_id"].startswith("decision:"):
                writes += _sent(now, ran["actions"], "agent")
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
            if save_happenings is not None:  # today so far, as the learners see it: statuses and the live
                save_happenings(store, now)  # occasion match read happenings (only finished ones are written)
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


def _learn_night(store: Store, now: int, day: date) -> None:
    """The learners on everything up to the end of `day`, once (as source/demo.py's night()): its happenings,
    then kinds, habits and occasions. Then the plan and the wakes find the habits. Never raises."""
    from .clock import day_bounds

    try:
        key = day.isoformat()
        if store.db.execute("SELECT 1 FROM learned_days WHERE day = ?", (key,)).fetchone():
            return
        end = day_bounds(day)[1]
        if not store.db.execute("SELECT 1 FROM snapshots WHERE ts < ? LIMIT 1", (end,)).fetchone():
            return  # the loop wasn't watching yet
        started = time.monotonic()
        save_happenings(store, end)
        new = learn_day(store.db, day)
        store.db.execute("INSERT OR REPLACE INTO learned_days (day, ts) VALUES (?, ?)", (key, now))
        store.db.commit()
        log.info("learned %s: %d new pattern(s) (%.1fs)", key, len(new), time.monotonic() - started,
                 extra={"data": {"day": key, "new": len(new)}})
    except Exception:
        log.exception("nightly learning failed for %s", day)


def _learned(store: Store, now: int, state: State) -> list:
    """The learned wakes due now (source/wake.py). Never raises."""
    try:
        return wake.learned_wakes(store.db, to_local(now), state)
    except Exception:
        log.exception("learned wakes failed this tick")
        return []


async def _proposals(store: Store, now: int, proposals: list, actuator: Actuator, types: dict[str, str],
                     fresh: dict[str, list[str]], state: State, readback_delay_s: float,
                     rules_ran: list[dict] | None = None) -> list[dict]:
    """The agent's proposals as decisions (code sets the confidence), then the due "do" ones run
    now, through the guard. Returns the decisions as they ended this tick. Never raises."""
    try:
        local = to_local(now)
        decided = anticipate.decide(store.db, local, proposals)
        ran = {r["decision"]["id"]: r for r in await anticipate.run_due(
            store.db, local, actuator, types, fresh, state=state, readback_delay_s=readback_delay_s)}
        for r in ran.values():
            if r["actions"]:
                tid = f"decision:{r['decision']['id']}"
                store.log_rule_run(now, tid, r["actions"])
                if rules_ran is not None:
                    rules_ran.append({"trigger_id": tid, "actions": [
                        {"deviceId": a["args"].get("deviceId"), "body": a["args"].get("body"),
                         "status": a["status"], "verified": a["verified"]} for a in r["actions"]]})
        return [ran[d["id"]]["decision"] if d["id"] in ran else d for d in decided]
    except Exception:
        log.exception("proposals failed this tick")
        return []


def _sent(now: int, actions: list[dict], source: str) -> list[tuple[int, str, str, object, str]]:
    """The writes among `actions` that were sent: (ms, device, capability, value, who)."""
    out = []
    for a in actions:
        args = a.get("args") if isinstance(a.get("args"), dict) else a
        body = args.get("body") if isinstance(args, dict) else None
        if a.get("status") == "sent" and isinstance(body, dict) and body.get("type") and args.get("deviceId"):
            out.append((now, args["deviceId"], body["type"], body.get("value"), source))
    return out


def _attribute(events: list[Event], writes: list[tuple[int, str, str, object, str]]) -> None:
    """A change to the value Alfred (or a rule) just wrote is theirs, not a person's: Event.source "agent" (or
    "scenario:<id>"), so the learners never learn Alfred's own heating as the home's habit
    (source/learn/happenings.py, by_person). A write from the phone app is labelled "app": a person's.
    Each write explains one change."""
    left = list(writes)
    for e in events:
        if e.kind != "change":
            continue
        for w in left:
            if w[1] == e.device_id and w[2] == e.capability and _same(w[3], e.new):
                e.source = w[4]
                left.remove(w)
                break


def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return a == b


def _hhmmss(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M:%S")


async def _sleep(source: Source, poll_seconds: float, started: float) -> None:
    if source.realtime:
        await asyncio.sleep(max(0.0, poll_seconds - (time.monotonic() - started)))
