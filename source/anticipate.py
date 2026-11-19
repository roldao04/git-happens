"""Anticipation: Alfred acts ahead of habits and adapts to occasions (docs/anticipation.md).

The loop (source/loop.py) calls them before the triggers: the plan every few minutes, run_due
on every tick.

- plan_decisions(db, now): the plan for the next 24 h as Decisions (shape in
  docs/habits-plan.md), stored in `decisions`, one per habit or occasion per occurrence:
  - prepare: ahead of a forecast habit (source/learn/forecast.py) whose steps include the light:
    a soft lamp before the light usually comes on, a dim lamp before it usually goes off for
    the night (RECIPES). Nothing else: no habit without the light gets a lamp action, and no
    decision ever targets the plug;
  - adapt: while the day looks like a past occasion (source.learn.occasions.match_now), an
    expectation such as "going to sleep late is normal tonight". No device action.
  Confidence is computed here, never by the LLM: for prepare, the forecast's confidence × the
  light step's share × (1 − the confidence of an occasion going on now that explains the habit);
  for adapt, the match's confidence. Both are capped by the evidence factor n / (n + 1). At DO_AT
  (0.75) or more the mode is "do", below it "suggest" (shown and logged, never sent).
- run_due(db, now, actuator, types, fresh): carries out the due "do" decisions. Each write goes
  through actions.run_actions → guard.guarded_write, offered only the lamp as a writable device,
  so a decision can never touch the plug (and the guard never switches it on anyway). Each
  decision ends done, failed or skipped, with why. Never raises.

Missing parts are read defensively: without the `habits` table, `sensors` or the occasions
learner, the plan is just smaller (or empty).
"""
import importlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from .actions import Actuator, resolve_targets, run_actions
from .guard import READ_TOOL, WRITE_TOOL
from .learn import to_ms
from .learn.forecast import (active_habits, as_local, clip, habit_name, has_table, hhmm, number, parse_moment,
                             upcoming)
from .model import State

log = logging.getLogger("source.anticipate")

HORIZON_H = 24  # how far ahead decisions are planned
DO_AT = 0.75  # confidence from which Alfred acts instead of suggesting (docs/habits-plan.md)
MIN_CONFIDENCE = 0.4  # below this a new decision isn't even suggested (it would be noise)
MAX_LATE_MIN = 15  # a "do" decision found this late (the loop was down) is skipped, never run late
SAME_MOMENT_MIN = 20  # two habits asking for the same lamp action this close together: one decision
HOLD_H = 12  # an occasion that explains a habit makes its decisions this far ahead less sure
NIGHT_QUIET_H = 4  # a quiet that starts when the light goes off and lasts this long is the night (or away)


@dataclass(frozen=True)
class Recipe:
    """One thing Alfred may do ahead of a habit: a single guarded write to the lamp."""
    role: str  # what the habit's light step does: light_on | light_off
    lead_min: int  # how long before the light usually changes
    body: dict  # the write (update_device_capabilities body)
    only_if: dict  # the lamp's state when it is due; otherwise the decision is skipped
    says: str  # for the reason


RECIPES = {
    # 30 %: the plan's "dim the lamp on". Off → it comes on soft (or is soft when switched on).
    "soft_light": Recipe("light_on", 10, {"type": "brightness", "value": 30}, {"power": "off"},
                         "a soft lamp before the light usually comes on"),
    # Only dims a lamp that is on and brighter; it never switches the lamp on or off.
    "dim_light": Recipe("light_off", 30, {"type": "brightness", "value": 20},
                        {"power": "on", "brightness_above": 20},
                        "a dim lamp before the light usually goes off for the night"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,  -- when it is due (`at`), epoch ms
    data TEXT NOT NULL,   -- the Decision, in the shape of docs/habits-plan.md
    key TEXT,             -- what it is for: one decision per habit or occasion per occurrence
    meta TEXT             -- for run_due: the recipe, the lamp's state it needs, and what happened
);
"""


# ---------- planning ----------

def plan_decisions(db: sqlite3.Connection, now: datetime) -> list[dict]:
    """Plans (or re-plans) the next 24 h and returns the plan, soonest first. Never raises.

    A decision already planned for the same occurrence keeps its id and is updated (its time,
    confidence and mode follow the latest forecast); one that already ran is left as it was.
    A planned "prepare" decision whose occurrence is no longer forecast is skipped.
    """
    try:
        now = as_local(now)
        init(db)
        matches = _safely(_occasions_now, db, now) or []
        prepare, adapt = _safely(_prepare, db, now, matches), _safely(_adapt, db, now, matches)
        wanted = (prepare or []) + (adapt or [])
        plan = [d for key, decision, meta in wanted if (d := _upsert(db, key, decision, meta))]
        if prepare is not None:  # only a forecast that worked may say an occurrence is no longer expected
            _drop_unexpected(db, now, {key for key, _, _ in prepare})
        db.commit()
        return sorted(plan, key=lambda d: (d["at"], d["id"]))
    except Exception:
        log.exception("planning decisions failed")
        return []


def _safely(part, *args) -> list | None:
    """One kind of decision failing (a broken occasions table) must not cost the other its plan.
    None (not []) when it failed."""
    try:
        return part(*args)
    except Exception:
        log.exception("%s failed", part.__name__)
        return None


def init(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
    have = {row[1] for row in db.execute("PRAGMA table_info(decisions)")}
    for col in ("key", "meta"):  # a table made with only the shared columns (id, ts, data)
        if col not in have:
            db.execute(f"ALTER TABLE decisions ADD COLUMN {col} TEXT")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS decisions_key ON decisions (key)")
    db.execute("CREATE INDEX IF NOT EXISTS decisions_ts ON decisions (ts)")


def _prepare(db: sqlite3.Connection, now: datetime, matches: list["_Match"]) -> list[tuple[str, dict, dict]]:
    patterns = _patterns(db)
    planned = {k for k, _, _ in _stored(db, "prepare:")}
    held: dict[str, _Match] = {}  # habit id → the surest occasion going on now that explains it
    for m in matches:
        for hid in m.explains if m.confidence >= MIN_CONFIDENCE else []:
            if hid not in held or m.confidence > held[hid].confidence:
                held[hid] = m
    out = []
    for slot in upcoming(db, now, HORIZON_H, strict=True):
        h = slot.habit
        if (h.get("recurrence") or {}).get("kind") == "hourly" or h.get("importance") == "hidden":
            continue  # several lamp actions a day on a guess is a nuisance; hidden = a duplicate card
        light = light_steps(h, patterns)
        if not light:
            continue  # only a habit whose steps include the light gets a lamp action
        role = light[0][0]  # the first thing the light does in the habit
        after_min = min(after for r, after, _ in light if r == role)
        share = max(share for r, _, share in light if r == role)  # it does it on at least this share of days
        recipe_name, recipe = next((n, r) for n, r in RECIPES.items() if r.role == role)
        moment = slot.expected + timedelta(minutes=after_min)  # when the light usually changes
        confidence = min(slot.confidence * share, _evidence(h.get("occurrences")))
        occasion = held.get(h["id"]) if slot.expected <= now + timedelta(hours=HOLD_H) else None
        if occasion:  # tonight may not follow the habit: only as sure as the occasion isn't
            confidence *= 1 - occasion.confidence
        key = f"prepare:{h['id']}:{slot.day.isoformat()}"
        if confidence < MIN_CONFIDENCE and key not in planned:
            continue
        action = {"tool": WRITE_TOOL, "target": "bulb", "args": {"body": dict(recipe.body)}}
        reason = f"{habit_name(h)} expected around {hhmm(slot.expected)} ({slot.basis}): {recipe.says}"
        if occasion:
            reason += f"; less sure now: looks like {occasion.name}"
        decision = _decision(moment - timedelta(minutes=recipe.lead_min), "prepare", h["id"], None,
                             action, None, confidence, reason)
        out.append((key, decision, {"recipe": recipe_name, "only_if": recipe.only_if,
                                    "until": _iso(moment), "confidence": confidence}))
    return _one_per_moment(out)


def _one_per_moment(plan: list[tuple[str, dict, dict]]) -> list[tuple[str, dict, dict]]:
    """Two habits asking for the same lamp action within SAME_MOMENT_MIN: keep the surer one."""
    kept: list[tuple[str, dict, dict]] = []
    for item in sorted(plan, key=lambda x: -x[2]["confidence"]):
        at = datetime.fromisoformat(item[1]["at"])
        if not any(k[2]["recipe"] == item[2]["recipe"]
                   and abs((datetime.fromisoformat(k[1]["at"]) - at).total_seconds()) <= SAME_MOMENT_MIN * 60
                   for k in kept):
            kept.append(item)
    for _, _, meta in kept:
        meta.pop("confidence", None)
    return sorted(kept, key=lambda x: x[1]["at"])


@dataclass
class _Match:
    """An occasion today looks like (source.learn.occasions.match_now), read once per plan."""
    occasion_id: str
    name: str
    confidence: float  # the match's, capped by the evidence factor of the occasion's episodes
    episodes: float | None
    match: dict
    explains: list[str]  # the habit ids in its `explains` ("habit-4 late" → "habit-4")


def _occasions_now(db: sqlite3.Connection, now: datetime) -> list[_Match]:
    matches = occasion_matches(db, now)
    if not matches:
        return []
    occasions = occasions_by_id(db)
    out = []
    for m in matches:
        oid = str(m["occasion_id"])
        o = occasions.get(oid, {})
        out.append(_Match(oid, occasion_name(o, oid),
                          min(clip(number(m.get("confidence")) or 0.0), _evidence(o.get("episodes"))),
                          number(o.get("episodes")), m,
                          [x.partition(" ")[0] for x in m.get("explains") or [] if isinstance(x, str)]))
    return out


def _adapt(db: sqlite3.Connection, now: datetime, matches: list[_Match]) -> list[tuple[str, dict, dict]]:
    if not matches:
        return []
    planned = {k for k, _, _ in _stored(db, "adapt:")}
    habits = {h["id"]: h for h in active_habits(db)}
    tonight = "tonight" if now.hour >= 17 or now.hour < 5 else "today"
    out = []
    for occ in matches:
        m, oid, name, confidence = occ.match, occ.occasion_id, occ.name, occ.confidence
        since = parse_moment(m.get("since"))
        # one per occasion per evening: keyed by when it started (or by the evening it belongs to)
        key = f"adapt:{oid}:{(since or now - timedelta(hours=6)).date().isoformat()}"
        if confidence < MIN_CONFIDENCE and key not in planned:
            continue
        explained = [_explain(x, habits) for x in (m.get("explains") or []) if isinstance(x, str)]
        if explained:
            items = ", ".join(e for e, _ in explained)
            expectation = f"{items[:1].upper()}{items[1:]} {'is' if len(explained) == 1 else 'are'} normal {tonight}"
        else:
            expectation = f"{'An evening' if tonight == 'tonight' else 'A day'} unlike the usual one is normal {tonight}"
        similarity, episodes = number(m.get("similarity")), occ.episodes
        reason = (f"Looks like {name}"
                  + (f": {similarity:.0%} like" if similarity is not None else ": like")
                  + (f" its {int(episodes)} past times" if episodes else " past times")
                  + (f", since {hhmm(since)}" if since else ""))
        habit_id = next((hid for _, hid in explained if hid), None)
        at = since if since and since <= now else now
        out.append((key, _decision(at, "adapt", habit_id, oid, None, expectation, confidence, reason),
                    {"recipe": "expectation"}))
    return out


def _explain(item: str, habits: dict[str, dict]) -> tuple[str, str | None]:
    """"habit-4 late" → ("going to sleep late", "habit-4"), using the habit's name."""
    hid, _, rest = item.partition(" ")
    if hid not in habits:
        return item, None
    name = habit_name(habits[hid])
    name = name[:1].lower() + name[1:] if name[1:2].islower() else name
    return f"{name} {rest}".strip(), hid


def _decision(at: datetime, kind: str, habit_id, occasion_id, action, expectation, confidence: float,
              reason: str) -> dict:
    confidence = round(clip(confidence), 2)
    return {"id": None, "at": _iso(at), "kind": kind, "habit_id": habit_id, "occasion_id": occasion_id,
            "action": action, "expectation": expectation, "confidence": confidence,
            "mode": "do" if confidence >= DO_AT else "suggest", "status": "planned", "reason": reason}


def _upsert(db: sqlite3.Connection, key: str, decision: dict, meta: dict) -> dict | None:
    found = _rows(db, "key = ?", (key,))
    if not found and db.execute("SELECT 1 FROM decisions WHERE key = ?", (key,)).fetchone():
        return None  # there, but unreadable: left as it is
    if not found:
        decision = {**decision, "id": _next_id(db)}
        db.execute("INSERT INTO decisions (id, ts, data, key, meta) VALUES (?, ?, ?, ?, ?)",
                   (decision["id"], to_ms(datetime.fromisoformat(decision["at"])), json.dumps(decision), key,
                    json.dumps(meta)))
        log.info("decision %s planned: %s %s at %s (%s, confidence %.2f)", decision["id"], decision["kind"],
                 _what(decision), decision["at"][11:16], decision["mode"], decision["confidence"],
                 extra={"data": {"decision": decision}})
        return decision
    old = found[0]
    if old.data.get("status") != "planned":
        return old.data  # it ran (or was dropped): kept as it was
    new = {**decision, "id": old.id}
    if new != old.data:
        db.execute("UPDATE decisions SET ts = ?, data = ?, meta = ? WHERE id = ?",
                   (to_ms(datetime.fromisoformat(new["at"])), json.dumps(new), json.dumps(meta), old.id))
        if new["mode"] != old.data.get("mode"):
            log.info("decision %s is now %s (confidence %.2f)", old.id, new["mode"], new["confidence"],
                     extra={"data": {"decision": new}})
    return new


def _drop_unexpected(db: sqlite3.Connection, now: datetime, keys: set[str]) -> None:
    """A planned "prepare" decision whose occurrence left the forecast (the habit stopped, it already
    happened, its rhythm changed) is skipped rather than run on a guess nobody makes any more."""
    for key, data, meta in _stored(db, "prepare:"):
        at = parse_moment(data.get("at"))
        if key in keys or at is None or at <= now:
            continue
        data["status"] = "skipped"
        meta["ran"] = {"at": _iso(now), "why": "skipped: no longer expected", "actions": []}
        db.execute("UPDATE decisions SET data = ?, meta = ? WHERE key = ?", (json.dumps(data), json.dumps(meta), key))
        log.info("decision %s dropped: no longer expected", data.get("id"), extra={"data": {"decision": data}})


def _stored(db: sqlite3.Connection, prefix: str) -> list[tuple[str, dict, dict]]:
    """The planned decisions whose key starts with `prefix`, as (key, decision, meta)."""
    return [(r.key, r.data, r.meta) for r in _rows(db, "key LIKE ? AND data LIKE ?", (prefix + "%", PLANNED))
            if r.data.get("status") == "planned"]


@dataclass
class _Row:
    id: str
    key: str | None
    data: dict
    meta: dict


PLANNED = '%"status": "planned"%'  # a cheap first filter; each row's JSON is checked after
_unreadable: set[str] = set()  # rows already reported, so a broken one isn't logged on every tick


def _rows(db: sqlite3.Connection, where: str, params: tuple) -> list[_Row]:
    """Stored decisions, soonest first. An unreadable row is reported once and left alone: one
    broken row must not stop the others from running."""
    out = []
    for _id, key, data, meta in db.execute(f"SELECT id, key, data, meta FROM decisions WHERE {where}"
                                           " ORDER BY ts, id", params):
        try:
            d, m = json.loads(data), json.loads(meta or "{}")
            if isinstance(d, dict) and isinstance(m, dict):
                out.append(_Row(_id, key, d, m))
                continue
        except (TypeError, ValueError):
            pass
        if _id not in _unreadable:
            _unreadable.add(_id)
            log.error("decision %s can't be read; left as it is", _id)
    return out


def _next_id(db: sqlite3.Connection) -> str:
    ids = [r[0] for r in db.execute("SELECT id FROM decisions WHERE id LIKE 'decision-%'")]
    return f"decision-{max((int(i.split('-')[1]) for i in ids if i.split('-')[1].isdigit()), default=0) + 1}"


# ---------- the habit's light ----------

def light_steps(habit: dict, patterns: dict[str, tuple[str, dict]]) -> list[tuple[str, float, float]]:
    """Where the light is in the habit's steps: [(light_on | light_off, minutes after it starts, share)].

    Read from each step's pattern (the `sensors` table): a lamp kind or link part is the light
    coming on; the end of a quiet "until bulb" is the light coming on after it (waking up); the
    start of a long quiet "after bulb" is the light going off before it (going to sleep). A
    step's offset is when its own part happens, for a link its second part (the grouper's
    convention), so a lamp in a link's first part comes on `lag_min` earlier.
    """
    out = []
    for step in habit.get("steps") or []:
        if not isinstance(step, dict) or step.get("pattern") not in patterns:
            continue
        signature, sensor = patterns[step["pattern"]]
        p = sensor.get("pattern") or {}
        parts = [str(x) for x in p.get("parts") or []]
        share = number(step.get("share"))
        for role, inner in _light_in(signature, parts, number(p.get("lag_min")) or 0.0):
            out.append((role, (number(step.get("offset_min")) or 0.0) + inner, 1.0 if share is None else share))
    return sorted(out, key=lambda x: x[1])


def _light_in(signature: str, parts: list[str], lag_min: float) -> list[tuple[str, float]]:
    kind, _, rest = signature.partition(":")
    first = parts[0] if parts else ""
    if kind == "kind-end":  # the end of a quiet: did the light coming on end it?
        m = re.search(r"until (\w+)", first)
        return [("light_on", 0.0)] if m and m.group(1) == "bulb" else []
    if kind == "kind" and rest.startswith("home"):  # the start of a quiet: did the light going off begin it?
        m, hours = re.search(r"after (\w+)", first), re.search(r"about ([\d.]+) h\b", first)
        long = hours is not None and float(hours.group(1)) >= NIGHT_QUIET_H
        return [("light_off", 0.0)] if m and m.group(1) == "bulb" and long else []
    if kind == "kind":
        return [("light_on", 0.0)] if rest.startswith("bulb") and _lamp_on(first) else []
    if kind == "link":
        a, _, b = rest.partition(">")
        out = []
        if a.startswith("bulb") and _lamp_on(first):
            out.append(("light_on", -lag_min))
        if b.startswith("bulb") and _lamp_on(parts[1] if len(parts) > 1 else ""):
            out.append(("light_on", 0.0))
        return out
    return []


def _lamp_on(part: str) -> bool:
    """A lamp happening is it being on, unless the home keeps it on and the happening is "power off"."""
    return re.search(r"\bpower off\b", part) is None


def _patterns(db: sqlite3.Connection) -> dict[str, tuple[str, dict]]:
    if not has_table(db, "sensors"):
        return {}
    out = {}
    for sid, signature, data in db.execute("SELECT id, signature, data FROM sensors"):
        try:
            out[sid] = (signature or "", json.loads(data))
        except (TypeError, ValueError):
            continue
    return out


# ---------- running ----------

async def run_due(db: sqlite3.Connection, now: datetime, actuator: Actuator, types: dict[str, str],
                  fresh: dict[str, list[str]], *, state: State | None = None,
                  readback_delay_s: float = 0.0) -> list[dict]:
    """Carries out the due "do" decisions. Never raises.

    `types`: device id → type; `fresh`: type → ids of devices reporting now (source.normalize);
    `state`: the readings just polled, for the lamp's current state (else it is read through
    the actuator). Returns [{"decision", "actions" (the guard's records), "why"}] for each one
    settled. A "suggest" decision is never run.
    """
    out: list[dict] = []
    try:
        now = as_local(now)
        if not has_table(db, "decisions"):
            return []
        rows = [r for r in _rows(db, "ts <= ? AND data LIKE ?", (to_ms(now), PLANNED))
                if r.data.get("status") == "planned" and r.data.get("mode") == "do"]
    except Exception:
        log.exception("reading due decisions failed")
        return []
    statuses: dict[str, dict] | None = None
    for row in rows:
        _id, d, m = row.id, row.data, row.meta
        try:
            if d.get("action") is None:
                status, why, actions = "done", "expectation in force", []
            else:
                if statuses is None and d.get("habit_id"):
                    statuses = habit_statuses(db, now)
                status, why, actions = await _run_one(d, m, now, actuator, types, fresh, state, statuses or {},
                                                      readback_delay_s)
            d["status"] = status
            m["ran"] = {"at": _iso(now), "why": why, "actions": actions}
            db.execute("UPDATE decisions SET data = ?, meta = ? WHERE id = ?", (json.dumps(d), json.dumps(m), _id))
            db.commit()
            level = logging.WARNING if status == "failed" else logging.INFO
            log.log(level, "decision %s %s: %s", _id, status, why, extra={"data": {"decision": d, "actions": actions}})
            out.append({"decision": d, "actions": actions, "why": why})
        except Exception as exc:  # one broken decision must not stop the others, or the loop
            log.exception("decision %s failed to run", _id)
            try:  # settled as failed, so it isn't retried (and logged) on every tick
                d["status"] = "failed"
                m["ran"] = {"at": _iso(now), "why": f"failed: {exc}"[:200], "actions": []}
                db.execute("UPDATE decisions SET data = ?, meta = ? WHERE id = ?", (json.dumps(d), json.dumps(m), _id))
                db.commit()
            except Exception:
                log.exception("decision %s: could not record the failure", _id)
    return out


async def _run_one(d: dict, m: dict, now: datetime, actuator: Actuator, types: dict[str, str],
                   fresh: dict[str, list[str]], state: State | None, statuses: dict[str, dict],
                   readback_delay_s: float) -> tuple[str, str, list[dict]]:
    at, until = datetime.fromisoformat(d["at"]), parse_moment(m.get("until"))
    if now > at + timedelta(minutes=MAX_LATE_MIN) or (until is not None and now >= until):
        return "skipped", "skipped: too late", []
    today = (statuses.get(d.get("habit_id") or "") or {})
    if (today.get("status") or today.get("today")) in ("done", "partly", "now"):  # source.learn.grouping
        return "skipped", "skipped: it already happened today", []
    ready, missing = resolve_targets([d["action"]], fresh)
    if missing:
        return "skipped", f"skipped: {missing[0]['result']}", missing
    device = ready[0]["args"].get("deviceId")
    if types.get(device) == "bulb" and m.get("only_if"):
        why = _unmet(m["only_if"], await _lamp_now(device, state, actuator))
        if why:
            return "skipped", why, []
    # The guard is offered only the lamp: a decision can't change anything else, even plug off.
    writable = {dev: t for dev, t in types.items() if t == "bulb"}
    actions = await run_actions(actuator, f"decision:{d['id']}", ready, writable, readback_delay_s=readback_delay_s)
    a = actions[0]
    if a["status"] == "sent":
        return "done", ("sent and confirmed" if a["verified"] == "confirmed"
                        else "sent, but the lamp didn't confirm it"), actions
    if a["status"] == "blocked":
        return "skipped", a["result"], actions
    return "failed", f"failed: {str(a['result'])[:200]}", actions


async def _lamp_now(device: str, state: State | None, actuator: Actuator) -> dict:
    """The lamp's current values: from the poll just made, else read through the actuator."""
    if state is not None:
        values = {cap: r.value for (dev, cap), r in state.items() if dev == device}
        if values:
            return values
    try:
        details = json.loads(await actuator.call(READ_TOOL, {"deviceId": device}))
        caps = details.get("capabilities") if isinstance(details, dict) else None
        return {c["type"]: c.get("value") for c in caps or [] if isinstance(c, dict) and "type" in c}
    except Exception:
        return {}


def _unmet(only_if: dict, lamp: dict) -> str | None:
    """Why the lamp isn't in the state the decision needs, or None if it is."""
    if "power" in only_if:
        power = lamp.get("power")
        if power is None:
            return "skipped: the lamp's state is unknown"
        if power != only_if["power"]:
            return f"skipped: the lamp is already {power}"
    if "brightness_above" in only_if:
        b = number(lamp.get("brightness"))
        if b is not None and b <= only_if["brightness_above"]:
            return f"skipped: the lamp is already at {b:g} %"
    return None


# ---------- reading decisions, and the other parts' learners ----------

def decisions_between(db: sqlite3.Connection, start: datetime, end: datetime, *, outcome: bool = False) -> list[dict]:
    """Decisions due in [start, end), soonest first. `outcome=True` adds what happened ("sent and
    confirmed", "skipped: the lamp is already on"), for the agent. Never raises."""
    try:
        if not has_table(db, "decisions"):
            return []
        out = []
        for r in _rows(db, "ts >= ? AND ts < ?", (to_ms(as_local(start)), to_ms(as_local(end)))):
            ran = r.meta.get("ran")
            out.append({**r.data, "outcome": ran.get("why")} if outcome and isinstance(ran, dict) else r.data)
        return out
    except Exception:
        log.exception("reading decisions failed")
        return []


def optional(module: str, name: str):
    """A function from a part built elsewhere (habits, occasions), or None while it isn't there."""
    try:
        return getattr(importlib.import_module(module), name, None)
    except Exception as exc:  # missing, or broken: that part is just left out
        log.debug("%s.%s unavailable: %s", module, name, exc)
        return None


def habit_statuses(db: sqlite3.Connection, now: datetime) -> dict[str, dict]:
    """Each habit's status today, from source.learn.grouping.habit_today; {} while it isn't there."""
    fn = optional("source.learn.grouping", "habit_today")
    if fn is None:
        return {}
    try:
        got = fn(db, now)
    except Exception:
        log.exception("habit_today failed")
        return {}
    items = got.get("habits", got) if isinstance(got, dict) else got
    if isinstance(items, dict):  # {habit id: status or {...}}
        return {str(k): v if isinstance(v, dict) else {"status": v} for k, v in items.items()}
    out = {}
    for st in items or []:
        if isinstance(st, dict) and (st.get("habit_id") or st.get("id")):
            out[str(st.get("habit_id") or st.get("id"))] = st
    return out


def occasion_matches(db: sqlite3.Connection, now: datetime) -> list[dict]:
    """The occasions today resembles (source.learn.occasions.match_now); [] while it isn't there."""
    fn = optional("source.learn.occasions", "match_now")
    if fn is None:
        return []
    try:
        got = fn(db, now)
    except Exception:
        log.exception("match_now failed")
        return []
    return [m for m in got or [] if isinstance(m, dict) and m.get("occasion_id")]


def occasions_by_id(db: sqlite3.Connection) -> dict[str, dict]:
    if not has_table(db, "occasions"):
        return {}
    out = {}
    for oid, data in db.execute("SELECT id, data FROM occasions"):
        try:
            out[str(oid)] = json.loads(data)
        except (TypeError, ValueError):
            continue
    return out


def occasion_name(o: dict, oid: str) -> str:
    """The namer's name, or "Occasion 2" for occasion-2 until it has one."""
    if o.get("name"):
        return str(o["name"])
    n = oid.rsplit("-", 1)[-1]
    return f"Occasion {n}" if n.isdigit() else oid


# ---------- helpers ----------

def _evidence(n) -> float:
    """n / (n + 1): one past example allows at most 0.5 (only a suggestion)."""
    n = number(n)
    return 1.0 if n is None else max(0.0, n) / (max(0.0, n) + 1)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat(timespec="seconds")


def _what(d: dict) -> str:
    if d.get("action"):
        body = d["action"].get("args", {}).get("body", {})
        return f"{d['action'].get('target', '?')} {body.get('type')} {body.get('value', '')}".strip()
    return repr(d.get("expectation"))
