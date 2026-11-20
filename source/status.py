"""The phone app's "Right now" card (source/phone_api.py, mobile-interaction-interface/).

What the home is doing now, what Alfred changed for it, and what Alfred has planned or would like a yes
for. Nothing stores a "mode": the title comes from an occasion today resembles, else from who is home
(the router's phones), else from the habit behind Alfred's last change. The changes are the writes of
Alfred's decisions that ran since then, each with the value before (the `events` row Alfred's write made).
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta

from . import alerts, anticipate
from .learn import to_ms
from .learn.forecast import as_local
from .model import State
from .wake import home_now

log = logging.getLogger("source.status")

LOOK_BACK_H = 12  # Alfred's changes shown when nothing says when the status began; also how far back "not sent" goes
NOT_SENT = "look-only mode"  # in a decision's ran.why when --look-only refused its write (guard.LOOK_ONLY_REASON)
PLAN_AHEAD_H = 24
TONES = ((0, "warm"), (50, "neutral"), (100, "cool"))  # the bulb's white temperature, 0–100 (assumed 0 = warm)
KIND_TITLES = {"prepare": "Getting ready", "adapt": "Today is different", "llm": "Alfred’s idea"}


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat(timespec="seconds")


def _iso_ms(ms: int) -> str:
    from .learn import to_local
    return _iso(to_local(ms))


def habit_names(db: sqlite3.Connection) -> dict[str, str]:
    """habit id -> its name (the namer's, else "Habit 4"); {} before habits exist."""
    active = anticipate.optional("source.learn.forecast", "active_habits")
    name = anticipate.optional("source.learn.forecast", "habit_name")
    try:
        return {h["id"]: (name(h) if name else h.get("name") or h["id"]) for h in (active(db) if active else [])}
    except Exception:
        log.exception("reading habit names failed")
        return {}


def target_words(target: str) -> str:
    """"plug:washing-machine" -> "Washing machine plug", "bulb" -> "Lamp"."""
    if target.startswith("plug:"):
        return f"{target.split(':', 1)[1].replace('-', ' ').capitalize()} plug"
    return {"bulb": "Lamp", "plug": "Plug", "thermostat": "Heating"}.get(target, target)


def does(d: dict) -> str:
    """What a decision does, in a few words: "Lamp at 30%", "Heating to 21 °C", "TV plug off"."""
    action = d.get("action")
    if not action:
        return str(d.get("expectation") or "Nothing to change")
    who = target_words(str(action.get("target") or ""))
    body = (action.get("args") or {}).get("body") or {}
    kind, value = body.get("type"), body.get("value")
    if kind == "power":
        return f"{who} {value}"
    if kind == "brightness":
        return f"{who} at {value}%"
    if kind == "temperature" and isinstance(value, (int, float)):
        return f"{who} {min(TONES, key=lambda t: abs(t[0] - value))[1]}"
    if kind == "targetTemperature":
        return f"Heating to {value:g} °C" if isinstance(value, (int, float)) else "Heating"
    if kind == "mode":
        return f"Heating on {value}"
    if kind == "color_rgb":
        return f"{who} colour"
    return who


def title_of(d: dict, names: dict[str, str], occasions: dict[str, dict]) -> str:
    if d.get("habit_id") in names:
        return names[d["habit_id"]]
    if d.get("occasion_id"):
        return anticipate.occasion_name(occasions.get(d["occasion_id"], {}), d["occasion_id"])
    return KIND_TITLES.get(str(d.get("kind")), "Alfred’s plan")


def plan_item(d: dict, m: dict, names: dict[str, str], occasions: dict[str, dict]) -> dict:
    """A decision as the app shows it (PlanItem in mobile-interaction-interface/src/api/types.ts)."""
    return {"id": d["id"], "at": d["at"], "mode": d.get("mode", "suggest"), "status": d.get("status", "planned"),
            "title": title_of(d, names, occasions), "does": does(d), "reason": str(d.get("reason") or ""),
            "confidence": float(d.get("confidence") or 0), "answer": (m.get("person") or {}).get("answer")}


def plan(db: sqlite3.Connection, now: datetime) -> tuple[list[dict], list[dict]]:
    """The next 24 h: Alfred's "do" decisions still to come (with the ones a person skipped, so they can
    be put back) and the "suggest" ones nobody has answered."""
    now = as_local(now)
    names, occasions = habit_names(db), anticipate.occasions_by_id(db)
    planned, suggestions = [], []
    for d, m in anticipate.rows_between(db, now, now + timedelta(hours=PLAN_AHEAD_H)):
        answer = (m.get("person") or {}).get("answer")
        if d.get("mode") == "do" and (d.get("status") == "planned" or answer == "skip"):
            planned.append(plan_item(d, m, names, occasions))
        elif d.get("mode") == "suggest" and d.get("status") == "planned" and answer is None:
            suggestions.append(plan_item(d, m, names, occasions))
    return planned, suggestions


def not_sent(db: sqlite3.Connection, now: datetime) -> list[dict]:
    """--look-only: the decisions of the last hours whose writes were refused only because nothing is sent.
    The card shows them as what Alfred would have done, so watching in look-only still shows his plans."""
    now = as_local(now)
    names, occasions = habit_names(db), anticipate.occasions_by_id(db)
    return [plan_item(d, m, names, occasions) for d, m in anticipate.rows_between(db, now - timedelta(hours=LOOK_BACK_H), now)
            if d.get("status") == "failed" and NOT_SENT in str((m.get("ran") or {}).get("why") or "")]


def learning(db: sqlite3.Connection) -> dict | None:
    """Before any habit is learned: how many days the loop has watched, so the card can say it's still learning.
    None once there are habits (or for a store with no snapshots, like the tests' hand-made ones)."""
    if habit_names(db):
        return None
    try:
        days = db.execute("SELECT COUNT(DISTINCT date(ts / 1000, 'unixepoch', 'localtime')) FROM snapshots").fetchone()[0]
    except sqlite3.OperationalError:
        return None
    return {"daysWatched": int(days or 0)}


def agent_change(db: sqlite3.Connection, device_id: str, capability: str, after_ms: int) -> tuple | None:
    """The first change Alfred's write made to this capability at or after `after_ms`: (old, new), or None."""
    row = db.execute("SELECT old, new FROM events WHERE device_id = ? AND capability = ? AND source = 'agent'"
                     " AND kind = 'change' AND ts >= ? ORDER BY ts LIMIT 1", (device_id, capability, after_ms)).fetchone()
    if row is None:
        return None
    return tuple(json.loads(v) if v is not None else None for v in row)


def sent_actions(m: dict) -> list[dict]:
    return [a for a in ((m.get("ran") or {}).get("actions") or []) if isinstance(a, dict) and a.get("status") == "sent"]


def ran_ms(m: dict) -> int | None:
    at = (m.get("ran") or {}).get("at")
    try:
        return to_ms(as_local(datetime.fromisoformat(at))) if at else None
    except (TypeError, ValueError):
        return None


def alfred_changes(db: sqlite3.Connection, state: State, start: datetime, end: datetime) -> tuple[dict | None, list[dict]]:
    """Alfred's decisions that ran in [start, end) and weren't undone: the latest one, and every write
    they sent as {deviceId, device, capability, from, to}, the latest per capability."""
    names = {r.device_id: r.device for r in state.values()}
    latest, by_cap = None, {}
    for d, m in anticipate.rows_between(db, start, end):
        if d.get("status") != "done" or (m.get("person") or {}).get("answer") == "undo":
            continue
        actions = sent_actions(m)
        if not actions:
            continue
        latest = d
        since = (ran_ms(m) or 0) - 60_000
        for a in actions:
            args = a.get("args") or {}
            dev, body = args.get("deviceId"), args.get("body") or {}
            if not dev or not body.get("type") or body["type"] == "color_rgb":
                continue
            seen = agent_change(db, dev, body["type"], since)
            old = by_cap.get((dev, body["type"]), {}).get("from", seen[0] if seen else None)
            by_cap[(dev, body["type"])] = {"deviceId": dev, "device": names.get(dev, dev), "capability": body["type"],
                                           "from": old, "to": body.get("value")}
    return latest, list(by_cap.values())


def _presence_since(db: sqlite3.Connection, before_ms: int) -> int | None:
    """When who is home last changed: the latest phone event (a phone connecting or leaving)."""
    row = db.execute("SELECT MAX(ts) FROM events WHERE capability IN ('connected', 'connectedClients', 'guestPhones')"
                     " AND kind = 'change' AND ts <= ?", (before_ms,)).fetchone()
    return row[0] if row and row[0] is not None else None


def overridden(db: sqlite3.Connection, decision_id: str) -> bool:
    try:
        return db.execute("SELECT 1 FROM overrides WHERE decision_id = ?", (decision_id,)).fetchone() is not None
    except sqlite3.OperationalError:  # an older store without the table
        return False


def status_now(db: sqlite3.Connection, state: State, now: datetime, stale_ms: int | None = None) -> dict:
    """The "Right now" card (Status in mobile-interaction-interface/src/api/types.ts). Never raises for
    missing parts: a home with no router, no occasions or no decisions still gets a status."""
    now = as_local(now)
    now_ms = to_ms(now)
    home = home_now(state) if state else {}
    has_router = any(r.capability == "connectedClients" or r.device_type == "phone" for r in state.values())
    since_ms = _presence_since(db, now_ms) if has_router else None
    start = datetime.fromisoformat(_iso_ms(since_ms)) if since_ms else now - timedelta(hours=LOOK_BACK_H)
    latest, changes = alfred_changes(db, state, start - timedelta(minutes=1), now + timedelta(minutes=1))
    names, occasions = habit_names(db), anticipate.occasions_by_id(db)

    matches = anticipate.occasion_matches(db, now)
    if matches:
        oid = matches[0]["occasion_id"]
        title = anticipate.occasion_name(occasions.get(oid, {}), oid)
    elif has_router:
        title = "Guests over" if home.get("guest_phones") else ("At home" if home.get("phones_home") else "Nobody home")
    elif latest is not None:
        title = title_of(latest, names, occasions)
    else:
        title = "Home"

    if since_ms is None and latest is not None:  # no router: the status began when Alfred last acted
        since = latest.get("at")
    else:
        since = _iso_ms(since_ms) if since_ms else None
    planned, suggestions = plan(db, now)
    return {
        "title": title,
        "since": since,
        "changes": changes,
        "why": str(latest.get("reason")) if latest and latest.get("reason") else None,
        "decisionId": latest["id"] if latest else None,
        "byAlfred": bool(latest) and not overridden(db, latest["id"]),
        "planned": planned,
        "suggestions": suggestions,
        "notSent": not_sent(db, now),
        "learning": learning(db),
        "alerts": alerts.alerts_now(db, state, now, stale_ms),
    }
