"""Waking the LLM from what was learned (docs/demo-plan.md C8, docs/anticipation.md).

  learned_wakes(db, now, state) -> list[WakeReason]

The triggers (source/triggers.py) wake the agent on raw changes. These wake it on what Alfred
learned, ahead of time or when the learned rhythm says something is off:

  habit_soon    a forecast habit (probability >= 0.5) starts within its lead time + 5 min and
                has something to prepare (heating, lamp)       habit_soon:<habit>:<date>
  arrival       a known phone connects                         arrival:<phone>:<date>:<hh>
  departure     the last known phone leaves                    departure:<phone>:<date>:<hh>
  habit_missing a habit (importance normal or high) is 20 min past its window's latest, not
                done, and a known phone is home                habit_missing:<habit>:<date>
  occasion      today's match with a past occasion reaches 0.5 occasion:<occasion>:<date>

Each fires once per key: fired keys are kept in the `wakes` table (key, ts, data). The data is
what the LLM needs (the habit's or occasion's card, its forecast, who is home, room and target
temperature, the warm-up, the decisions already planned for it) and `base`, the confidence code
gives the wake (C9): habit confidence × forecast probability × n / (n + 1) for a habit, the
match's confidence for an occasion. The LLM's proposals are judged against it
(source/anticipate.py, decide), never against a number the LLM gives.

Every other part is read defensively: missing habits, forecast, occasions or warm-up only make
fewer wakes. Never raises.
"""
import json
import logging
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta

from . import anticipate
from .learn import to_local, to_ms
from .learn.forecast import active_habits, as_local, clip, clock, habit_name, has_table, hhmm, number, parse_moment
from .model import State, WakeReason

log = logging.getLogger("source.wake")

SOON_PROBABILITY = 0.5  # a forecast habit this likely may wake the agent ahead of it
EXTRA_LEAD_MIN = 5  # wake this much before the lead time, so a proposal can still be on time
DEFAULT_LEAD_MIN = 30  # the heating's lead while the warm-up isn't measured yet (C4: < 5 samples)
LAMP_LEAD_MIN = 10  # a lamp only needs a moment (anticipate's soft lamp: 10 min)
FORECAST_H = 6  # how far ahead habit_soon looks
MISSING_AFTER_MIN = 20  # past the window's latest by this much, a habit is missing
MISSING_FOR_H = 3  # ...and only for this long: later it is old news, not a wake
OCCASION_AT = 0.5  # the match confidence that wakes
PHONE_LOOKBACK_MIN = 10  # phone changes this recent count as an arrival or a departure
PRESENCE_BASE = 0.8  # an arrival or departure is seen, not forecast; a phone is still only a proxy
COMFORT_C = 21.0  # the warm target people set, until the history says otherwise
WARM_FROM_C = 18.0  # targets above this are "warm" (the guard's no-one-home ceiling)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
IMPORTANT = (None, "normal", "high")  # no namer card yet counts as normal

SCHEMA = """
CREATE TABLE IF NOT EXISTS wakes (
    key TEXT PRIMARY KEY,  -- what it fired for: once per habit occurrence, arrival or occasion
    ts INTEGER NOT NULL,   -- when it fired, epoch ms
    data TEXT NOT NULL     -- {kind, reason, base, habit_id, occasion_id, arrival, data}
);
"""


def init(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


# ---------- the entry point ----------

def learned_wakes(db: sqlite3.Connection, now: datetime, state: State) -> list[WakeReason]:
    """The wakes due now that haven't fired yet; each is recorded so it never fires again."""
    try:
        now = as_local(now)
        init(db)
        home = home_now(state)
        home["comfort_c"] = _guarded(comfort_c, db, now, state, default=COMFORT_C)
        found: list[dict] = []
        for part in (_habit_soon, _presence, _habit_missing, _occasion):
            found += _guarded(part, db, now, home, default=[])
        out = []
        for w in found:
            if db.execute("SELECT 1 FROM wakes WHERE key = ?", (w["key"],)).fetchone():
                continue
            row = {k: w.get(k) for k in ("kind", "reason", "base", "habit_id", "occasion_id", "arrival")}
            row["base"] = round(clip(row["base"] or 0.0), 3)
            db.execute("INSERT INTO wakes (key, ts, data) VALUES (?, ?, ?)",
                       (w["key"], to_ms(now), json.dumps({**row, "data": w["data"]}, default=str)))
            data = {**w["data"], "confidence": row["base"]}
            out.append(WakeReason(w["kind"], w["reason"], kind=w["kind"], key=w["key"], data=data))
            log.info("wake   %s: %s (base %.2f)", w["key"], w["reason"], row["base"],
                     extra={"data": {"wake": w["key"], "base": row["base"]}})
        db.commit()
        return out
    except Exception:
        log.exception("learned wakes failed")
        return []


def wake_record(db: sqlite3.Connection, key: str | None) -> dict | None:
    """A fired wake as stored ({kind, reason, base, habit_id, occasion_id, arrival, data, ts}), or None."""
    if not key or not has_table(db, "wakes"):
        return None
    row = db.execute("SELECT ts, data FROM wakes WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row[1])
    except (TypeError, ValueError):
        return None
    return {**data, "ts": row[0]} if isinstance(data, dict) else None


def wakes_between(db: sqlite3.Connection, start: datetime, end: datetime) -> list[dict]:
    """The wakes fired in [start, end), oldest first, each with its `key` (for the visualizer)."""
    if not has_table(db, "wakes"):
        return []
    out = []
    for key, ts, data in db.execute("SELECT key, ts, data FROM wakes WHERE ts >= ? AND ts < ? ORDER BY ts, key",
                                    (to_ms(as_local(start)), to_ms(as_local(end)))):
        try:
            d = json.loads(data)
        except (TypeError, ValueError):
            continue
        out.append({"key": key, "ts": ts, **d})
    return out


# ---------- the home now ----------

def phones(state: State) -> list[dict]:
    """The phones connected now: [{"id", "name", "known"}]. Read from the router's connected
    clients (C1), or from per-phone `connected` readings once normalize gives them (C2)."""
    out: dict[str, dict] = {}
    for r in state.values():
        if r.capability == "connectedClients":
            for c in _clients(r.value):
                if c.get("kind", "phone") == "phone" and c.get("id"):
                    out[str(c["id"])] = {"id": str(c["id"]), "name": c.get("name") or "",
                                         "known": bool(c.get("known"))}
        elif r.capability == "connected" and r.value in ("on", True):
            pid = _phone_id(r.device_id)
            name = _phone_name(r.device, r.device_id, r.device_type)
            out.setdefault(pid, {"id": pid, "name": name, "known": bool(name)})  # guests have no name
    return sorted(out.values(), key=lambda p: p["id"])


def home_now(state: State) -> dict:
    """Who is home and the heating, as the LLM and the guard see it."""
    ph = phones(state)
    guests = sum(not p["known"] for p in ph)
    for r in state.values():
        if r.capability == "guestPhones" and number(r.value) is not None:
            guests = max(guests, int(r.value))
    out: dict = {"phones_home": [{"id": p["id"], "name": p["name"]} for p in ph if p["known"]],
                 "guest_phones": guests}
    thermo = {r.capability: r.value for r in state.values() if r.device_type == "thermostat"}
    room = number(thermo.get("temperatureValue"))
    if room is None:  # the room's own sensor, when there is no thermostat reading
        room = next((number(r.value) for r in state.values()
                     if r.capability == "temperatureValue" and number(r.value) is not None), None)
    out.update(room_c=room, target_c=number(thermo.get("targetTemperature")),
               mode=thermo.get("mode"), heating=thermo.get("heatingState"),
               thermostat=bool(thermo))
    return out


def someone_home(state: State) -> bool:
    return any(p["known"] for p in phones(state))


def comfort_c(db: sqlite3.Connection, now: datetime, state: State) -> float:
    """The warm target this home uses: the most common warm target in the last 30 days, else 21 °C."""
    if not has_table(db, "events"):
        return COMFORT_C
    ids = {r.device_id for r in state.values() if r.device_type == "thermostat"}
    if not ids:
        return COMFORT_C
    since = to_ms(now - timedelta(days=30))
    marks = ",".join("?" * len(ids))
    seen: Counter = Counter()
    for (new,) in db.execute(f"SELECT new FROM events WHERE capability = 'targetTemperature' AND kind = 'change'"
                             f" AND ts >= ? AND device_id IN ({marks})", (since, *ids)):
        v = number(_loads(new))
        if v is not None and v > WARM_FROM_C:
            seen[v] += 1
    return seen.most_common(1)[0][0] if seen else COMFORT_C


# ---------- habit_soon ----------

def _habit_soon(db: sqlite3.Connection, now: datetime, home: dict) -> list[dict]:
    fc = anticipate.optional("source.learn.forecast", "forecast")
    entries = fc(db, now, FORECAST_H) if fc else []
    if not entries:
        return []
    habits = {h["id"]: h for h in active_habits(db)}
    patterns = anticipate._patterns(db)
    lead_heat: float | None = None
    out = []
    for f in entries:
        h = habits.get(f.get("habit_id"))
        p = number(f.get("probability"))
        if h is None or p is None or p < SOON_PROBABILITY or h.get("importance") == "hidden" \
                or (h.get("recurrence") or {}).get("kind") == "hourly":
            continue
        expected = _at(f.get("date"), f.get("expected"))
        if expected is None or expected <= now:
            continue
        prepare = []
        if _heating_helps(home):
            prepare.append("heating")
        if anticipate.light_steps(h, patterns):
            prepare.append("lamp")
        if not prepare:
            continue
        if "heating" in prepare:
            if lead_heat is None:
                lead_heat = _lead(db, now, home)
            lead = lead_heat
        else:
            lead = LAMP_LEAD_MIN
        if expected - now > timedelta(minutes=lead + EXTRA_LEAD_MIN):
            continue
        base = clip(number(h.get("confidence")) or 0.0) * clip(p) * _evidence(h.get("occurrences"))
        name = habit_name(h)
        out.append({
            "kind": "habit_soon", "key": f"habit_soon:{h['id']}:{f.get('date')}", "habit_id": h["id"],
            "occasion_id": None, "base": base, "arrival": _iso(expected),
            "reason": f"{name} expected around {hhmm(expected)} (in {_minutes(expected - now)} min); "
                      f"{' and '.join(prepare)} to prepare, about {round(lead)} min ahead",
            "data": {"habit": habit_card(h), "forecast": f, "expected_at": _iso(expected),
                     "lead_minutes": round(lead, 1), "prepare_at": _iso(max(now, expected - timedelta(minutes=lead))),
                     "prepare": prepare, "home": home, "warmup": _warmup(db, now),
                     "planned": _planned(db, now, habit_id=h["id"])}})
    return out


def _heating_helps(home: dict) -> bool:
    """There is a thermostat, and its target or the room is below the warm target."""
    if not home.get("thermostat"):
        return False
    comfort = home.get("comfort_c") or COMFORT_C
    target, room = home.get("target_c"), home.get("room_c")
    return (target is not None and target < comfort) or (room is not None and room < comfort - 0.5)


def _lead(db: sqlite3.Connection, now: datetime, home: dict) -> float:
    """Minutes the heating needs from the room's temperature to the warm target (C4)."""
    fn = anticipate.optional("source.learn.warmup", "lead_minutes")
    room, comfort = home.get("room_c"), home.get("comfort_c") or COMFORT_C
    if fn is None or room is None:
        return DEFAULT_LEAD_MIN
    try:
        lead = number(fn(db, now, float(room), float(comfort)))
    except Exception:
        log.exception("lead_minutes failed")
        return DEFAULT_LEAD_MIN
    return DEFAULT_LEAD_MIN if lead is None else max(0.0, lead)


def _warmup(db: sqlite3.Connection, now: datetime) -> dict | None:
    fn = anticipate.optional("source.learn.warmup", "warmup")
    if fn is None:
        return None
    try:
        got = fn(db, now)
    except Exception:
        log.exception("warmup failed")
        return None
    return got if isinstance(got, dict) else None


# ---------- arrival and departure ----------

def _presence(db: sqlite3.Connection, now: datetime, home: dict) -> list[dict]:
    if not has_table(db, "events"):
        return []
    known_now = {p["id"]: p["name"] for p in home["phones_home"]}
    since = to_ms(now - timedelta(minutes=PHONE_LOOKBACK_MIN))
    rows = db.execute("SELECT ts, device_id, device, capability, old, new FROM events WHERE kind = 'change'"
                      " AND ts > ? AND ts <= ? AND capability IN ('connectedClients', 'connected') ORDER BY ts, id",
                      (since, to_ms(now))).fetchall()
    habits = active_habits(db)
    out = []
    for ts, dev_id, dev, cap, old, new in rows:
        came, left = _phone_changes(dev_id, dev, cap, _loads(old), _loads(new))
        at = to_local(ts)
        for pid, name in came:
            if pid not in known_now:
                continue  # it left again, or it is a guest's
            out.append(_presence_wake("arrival", pid, known_now[pid] or name, at, now, home, habits, db))
        if not known_now:  # the last known phone left
            for pid, name in left:
                out.append(_presence_wake("departure", pid, name, at, now, home, habits, db))
    return out


def _presence_wake(kind: str, pid: str, name: str, at: datetime, now: datetime, home: dict,
                   habits: list[dict], db: sqlite3.Connection) -> dict:
    mine = [h for h in habits if ((h.get("person") or {}).get("phone")) == pid]
    who = name or pid
    return {"kind": kind, "key": f"{kind}:{pid}:{at.date().isoformat()}:{at.hour:02d}",
            "habit_id": mine[0]["id"] if mine else None, "occasion_id": None, "base": PRESENCE_BASE,
            "arrival": None,
            "reason": f"{who} {'connected: someone is home' if kind == 'arrival' else 'left: no known phone is home'}"
                      f" ({hhmm(at)})",
            "data": {"phone": {"id": pid, "name": name}, "at": _iso(at), "home": home,
                     "habits": [habit_card(h) for h in mine], "warmup": _warmup(db, now),
                     "planned": _planned(db, now, habit_id=mine[0]["id"] if mine else None)}}


def _phone_changes(dev_id: str, dev: str, cap: str, old, new) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(known phones that connected, known phones that left) in one stored change."""
    if cap == "connectedClients":
        before = {str(c["id"]): c for c in _clients(old) if c.get("id") and c.get("kind", "phone") == "phone"}
        after = {str(c["id"]): c for c in _clients(new) if c.get("id") and c.get("kind", "phone") == "phone"}
        came = [(i, c.get("name") or "") for i, c in after.items() if i not in before and c.get("known")]
        left = [(i, c.get("name") or "") for i, c in before.items() if i not in after and c.get("known")]
        return came, left
    on_before, on_after = old in ("on", True), new in ("on", True)
    pid, name = _phone_id(dev_id), _phone_name(dev, dev_id, "phone")
    if on_after and not on_before:
        return [(pid, name)], []
    if on_before and not on_after and name:
        return [], [(pid, name)]
    return [], []


# ---------- habit_missing ----------

def _habit_missing(db: sqlite3.Connection, now: datetime, home: dict) -> list[dict]:
    if not home["phones_home"]:
        return []  # nobody known is home: absence says nothing
    habits = active_habits(db)
    if not habits:
        return []
    statuses = anticipate.habit_statuses(db, now)
    is_on = anticipate.optional("source.learn.recurrence", "is_on")
    out = []
    for h in habits:
        rec = h.get("recurrence") or {}
        if h.get("importance") not in IMPORTANT or rec.get("kind") == "hourly":
            continue
        deadline = _deadline(h, now)
        if deadline is None:
            continue
        day = deadline.date()
        st = statuses.get(h["id"])
        if statuses:
            if (st or {}).get("status") not in ("late", "expected"):
                continue
        else:  # no status: no reading for that day, and the rhythm says it was due
            if _has_reading(db, h["id"], day) or (is_on and not _safe_is_on(is_on, rec, day)):
                continue
        base = clip(number(h.get("confidence")) or 0.0) * _evidence(h.get("occurrences"))
        out.append({
            "kind": "habit_missing", "key": f"habit_missing:{h['id']}:{day.isoformat()}", "habit_id": h["id"],
            "occasion_id": None, "base": base, "arrival": None,
            "reason": f"{habit_name(h)} hasn't happened: usually by {hhmm(deadline - timedelta(minutes=MISSING_AFTER_MIN))}"
                      f" and someone is home",
            "data": {"habit": habit_card(h), "status": st, "deadline": _iso(deadline), "home": home,
                     "planned": _planned(db, now, habit_id=h["id"])}})
    return out


def window_on(h: dict, day: date) -> dict | None:
    """The habit's window on that day: its weekday window (C3 `windows`) when it has them, else `window`.
    None when its weekday windows leave that day out."""
    wins = [w for w in h.get("windows") or [] if isinstance(w, dict)]
    if wins:
        wd = WEEKDAYS[day.weekday()]
        return next((w for w in wins if wd in (w.get("days") or [])), None)
    return h.get("window") if isinstance(h.get("window"), dict) else None


def _deadline(h: dict, now: datetime) -> datetime | None:
    """The most recent "latest + 20 min" that has passed, within MISSING_FOR_H; None otherwise."""
    for day in (now.date(), now.date() - timedelta(days=1)):
        w = window_on(h, day)
        latest = clock((w or {}).get("latest"))
        if latest is None:
            continue
        deadline = datetime.combine(day, datetime.min.time()) + timedelta(minutes=latest + MISSING_AFTER_MIN)
        if deadline <= now <= deadline + timedelta(hours=MISSING_FOR_H):
            return deadline
    return None


def _has_reading(db: sqlite3.Connection, hid: str, day: date) -> bool:
    if not has_table(db, "habit_readings"):
        return False
    return db.execute("SELECT 1 FROM habit_readings WHERE habit_id = ? AND day = ?",
                      (hid, day.isoformat())).fetchone() is not None


def _safe_is_on(is_on, rec: dict, day: date) -> bool:
    try:
        return bool(is_on(rec, day))
    except Exception:
        return True  # unsure: let the status decide


# ---------- occasion ----------

def _occasion(db: sqlite3.Connection, now: datetime, home: dict) -> list[dict]:
    matches = anticipate.occasion_matches(db, now)
    if not matches:
        return []
    occasions = anticipate.occasions_by_id(db)
    fc = anticipate.optional("source.learn.forecast", "forecast")
    out = []
    for m in matches:
        conf = clip(number(m.get("confidence")) or 0.0)
        if conf < OCCASION_AT:
            continue
        oid = str(m["occasion_id"])
        o = occasions.get(oid, {})
        since = parse_moment(m.get("since"))
        day = (since or now - timedelta(hours=6)).date()
        moved = {s.get("habit_id") for s in m.get("shifts") or [] if isinstance(s, dict)}
        shifted = [f for f in (fc(db, now, 24) if fc else [])
                   if (f.get("shifted") or {}).get("occasion_id") == oid or f.get("habit_id") in moved]
        out.append({
            "kind": "occasion", "key": f"occasion:{oid}:{day.isoformat()}", "habit_id": None, "occasion_id": oid,
            "base": min(conf, _evidence(o.get("episodes"))), "arrival": None,
            "reason": f"Looks like {anticipate.occasion_name(o, oid)} ({conf:.0%} sure"
                      + (f", since {hhmm(since)}" if since else "") + ")",
            "data": {"occasion": occasion_card(o, oid), "match": m, "forecast_shifted": shifted, "home": home,
                     "warmup": _warmup(db, now), "planned": _planned(db, now, occasion_id=oid)}})
    return out


# ---------- cards ----------

CARD = ("explanation", "means", "if_missing", "importance")


def habit_card(h: dict) -> dict:
    out = {"id": h["id"], "name": habit_name(h), "rhythm": (h.get("recurrence") or {}).get("text"),
           "window": h.get("window"), "confidence": h.get("confidence"), "occurrences": h.get("occurrences")}
    for k in CARD + ("windows", "person"):
        if h.get(k):
            out[k] = h[k]
    return out


def occasion_card(o: dict, oid: str) -> dict:
    out = {"id": oid, "name": anticipate.occasion_name(o, oid)}
    for k in CARD + ("episodes", "fingerprint", "usual_hours", "weekdays", "confidence"):
        if o.get(k) is not None:
            out[k] = o[k]
    return out


def _planned(db: sqlite3.Connection, now: datetime, *, habit_id=None, occasion_id=None) -> list[dict]:
    """The decisions from 12 h ago to 24 h ahead for this habit or occasion."""
    if habit_id is None and occasion_id is None:
        return []
    got = anticipate.decisions_between(db, now - timedelta(hours=12), now + timedelta(hours=24), outcome=True)
    return [d for d in got if (habit_id and d.get("habit_id") == habit_id)
            or (occasion_id and d.get("occasion_id") == occasion_id)]


# ---------- helpers ----------

def _guarded(fn, *args, default):
    try:
        return fn(*args)
    except Exception:  # one kind of wake failing must not cost the others
        log.exception("%s failed", fn.__name__)
        return default


def _clients(v) -> list[dict]:
    v = _loads(v)
    return [c for c in v if isinstance(c, dict)] if isinstance(v, list) else []


def _loads(v):
    """A value as stored (JSON text) or as read (normalize keeps lists as JSON text): decoded."""
    for _ in range(2):
        if not isinstance(v, str):
            break
        try:
            v = json.loads(v)
        except ValueError:
            break
    return v


def _phone_name(name: str, device_id: str, device_type: str) -> str:
    """A per-phone reading's name, or "" for a guest's (normalize names a nameless device by its type)."""
    return "" if not name or name in ("?", "phone", device_type, device_id) else name


def _phone_id(device_id: str) -> str:
    return device_id.split(":", 1)[1] if device_id.startswith("phone:") else device_id


def _at(day, hhmm_) -> datetime | None:
    try:
        d = date.fromisoformat(str(day))
    except ValueError:
        return None
    return parse_moment(hhmm_, d)


def _evidence(n) -> float:
    return anticipate._evidence(n)


def _minutes(d: timedelta) -> int:
    return max(0, round(d.total_seconds() / 60))


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat(timespec="seconds")
