"""What the agent reads when it wakes (docs/event-loop.md §5).

`baselines` is what is normal for this home (docs/anticipation.md): the learned habits with
today's status, what each will probably do in the next 24 h, the past occasion today looks
like, and what Alfred planned or did on its own today. Every learner is read defensively: a
missing table or module (a part not built yet, or broken) only leaves its piece empty, and
`baselines` is {} until something is learned.
"""
import importlib
import json
import logging
from datetime import datetime, time, timedelta

from .model import State, WakeReason
from .store import Store

log = logging.getLogger("source.context")

FORECAST_HOURS = 24
MAX_FORECAST = 30  # the context goes to the LLM on every wake: keep it short
MAX_DECISIONS = 30
MAX_OCCASIONS = 5
NAMER_FIELDS = ("means", "if_missing", "importance")  # the namer's card, kept for the agent's reasoning
HABIT_EXTRAS = ("person", "windows")  # whose habit it is, and its weekday windows (docs/demo-plan.md C3)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")


def build_context(now_ms: int, reasons: list[WakeReason], state: State, store: Store,
                  last_run_ms: int | None, stale_ms: int, rules_ran: list[dict] | None = None) -> dict:
    devices: dict[str, dict] = {}
    for r in state.values():
        d = devices.setdefault(r.device_id, {"name": r.device, "type": r.device_type})
        d[r.capability] = r.value
        if r.comm_ts is not None:
            d["lastSeen"] = _iso(r.comm_ts)
            age = now_ms - r.comm_ts
            d["fresh"] = age <= stale_ms
            if not d["fresh"]:
                d["staleMinutes"] = age // 60_000

    midnight = datetime.fromtimestamp(now_ms / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
    today = store.events_since(int(midnight.timestamp() * 1000))
    since_last = [e for e in today if last_run_ms is None or e.ts > last_run_ms]

    def line(e):
        return f"{_iso(e.ts)} {e.describe()}"

    out = {
        "now": _iso(now_ms),
        "reasons": [r.to_dict() for r in reasons],
        "rules_just_ran": rules_ran or [],
        "state": devices,
        "events_since_last_run": [line(e) for e in since_last][-200:],
        "timeline_today": [line(e) for e in today][-500:],
        "baselines": baselines(store.db, now_ms),
    }
    home = _guarded(_home, state, default=None)
    if home:
        out["home"] = home
    return out


def _home(state: State) -> dict | None:
    """Who is home (known phones, guests' phones) and the heating, when the home has a router
    or a thermostat (source/wake.py reads them the same way); None otherwise."""
    fn = _optional("source.wake", "home_now")
    if fn is None:
        return None
    h = fn(state)
    if not (h.get("thermostat") or h.get("phones_home") or h.get("guest_phones")
            or any(r.capability in ("connectedClients", "connected") for r in state.values())):
        return None
    return h


def baselines(db, now_ms: int) -> dict:
    """What is normal for this home right now; {} until something is learned. Never raises.

    {"habits": [...], "forecast": [...], "occasion_now": [...], "decisions": [...]}
    """
    try:
        from .learn import to_local
        now = to_local(now_ms)  # the home's clock (Lisbon), as the learners use it
    except Exception:
        log.exception("baselines left out: no home clock")
        return {}
    habits, names = _guarded(_habits, db, now, default=([], {}))
    out = {"habits": habits,
           "forecast": _guarded(_forecast, db, now, names, default=[]),
           "occasion_now": _guarded(_occasion_now, db, now, default=[]),
           "decisions": _guarded(_decisions, db, now, default=[])}
    if not any(out.values()):
        return {}
    warm = _guarded(_warmup, db, now, default=None)
    if warm:
        out["warmup"] = warm
    return out


def _warmup(db, now: datetime) -> dict | None:
    """How fast the heating warms the house (source/learn/warmup.py, C4), once measured."""
    fn = _optional("source.learn.warmup", "warmup")
    got = fn(db, now) if fn else None
    return got if isinstance(got, dict) and got.get("samples") else None


# ---------- the pieces ----------

def _habits(db, now: datetime) -> tuple[list[dict], dict[str, str]]:
    """The active habits (source/learn/forecast.py reads the `habits` table) with today's status;
    before any habit exists, the learned patterns with theirs (source/learn/today.py)."""
    active_habits = _optional("source.learn.forecast", "active_habits")
    habit_name = _optional("source.learn.forecast", "habit_name")
    habits = active_habits(db) if active_habits else []
    if not habits:
        return _patterns(db, now), {}
    status = _habit_status(db, now, habits)
    out, names = [], {}
    for h in habits:
        if h.get("importance") == "hidden":  # the namer's duplicate of another card
            continue
        names[h["id"]] = habit_name(h) if habit_name else (h.get("name") or h["id"])
        st = status.get(h["id"]) or {}
        entry = {"id": h["id"], "name": names[h["id"]], "rhythm": (h.get("recurrence") or {}).get("text"),
                 "window": h.get("window"), "confidence": h.get("confidence"),
                 "today": st.get("status") or st.get("today") or "unknown"}
        if st.get("at"):
            entry["at"] = st["at"]
        entry.update({k: h[k] for k in NAMER_FIELDS + HABIT_EXTRAS if h.get(k)})
        out.append(entry)
    return out, names


def _habit_status(db, now: datetime, habits: list[dict]) -> dict[str, dict]:
    """Today's status per habit: source.learn.grouping.habit_today when it exists; otherwise the
    status (source.learn.today.baselines) of the habit's first step whose pattern has one."""
    habit_statuses = _optional("source.anticipate", "habit_statuses")
    got = habit_statuses(db, now) if habit_statuses else {}
    if got:
        return got
    patterns = {p.get("id"): p for p in _pattern_baselines(db, now)}
    out = {}
    for h in habits:
        steps = sorted((s for s in h.get("steps") or [] if isinstance(s, dict)),
                       key=lambda s: s.get("offset_min") or 0)
        p = next((patterns[s["pattern"]] for s in steps if patterns.get(s.get("pattern"), {}).get("today")), None)
        if p:
            out[h["id"]] = {"status": p["today"], "at": p.get("at")}
    return out


def _patterns(db, now: datetime) -> list[dict]:
    """The learned patterns with today's status (the view before habits), with the namer's fields."""
    pats = _pattern_baselines(db, now)
    if not pats:
        return []
    cards = {}
    for sid, data in db.execute("SELECT id, data FROM sensors"):
        try:
            cards[sid] = json.loads(data)
        except (TypeError, ValueError):
            continue
    out = []
    for p in pats:
        card = cards.get(p.get("id"), {})
        if card.get("importance") == "hidden":
            continue
        out.append({**p, **{k: card[k] for k in NAMER_FIELDS if card.get(k)}})
    return out


def _pattern_baselines(db, now: datetime) -> list[dict]:
    if not _has_table(db, "sensors"):  # also spares importing the learners when nothing is learned
        return []
    fn = _optional("source.learn.today", "baselines")
    return (fn(db, now) or {}).get("habits", []) if fn else []


def _forecast(db, now: datetime, names: dict[str, str]) -> list[dict]:
    fn = _optional("source.learn.forecast", "forecast")
    if fn is None:
        return []
    return [{**f, "habit": names[f["habit_id"]]} for f in fn(db, now, FORECAST_HOURS)
            if f.get("habit_id") in names][:MAX_FORECAST]


def _occasion_now(db, now: datetime) -> list[dict]:
    """The occasions today resembles (source.learn.occasions.match_now), with their names."""
    matches = _optional("source.anticipate", "occasion_matches")
    found = matches(db, now) if matches else []
    if not found:
        return []
    by_id = _optional("source.anticipate", "occasions_by_id")
    name_of = _optional("source.anticipate", "occasion_name")
    occasions = by_id(db) if by_id else {}
    out = []
    for m in found[:MAX_OCCASIONS]:
        oid = str(m["occasion_id"])
        o = occasions.get(oid, {})
        entry = {**m, "name": name_of(o, oid) if name_of else (o.get("name") or oid)}
        entry.update({k: o[k] for k in NAMER_FIELDS if o.get(k)})
        out.append(entry)
    return out


def _decisions(db, now: datetime) -> list[dict]:
    """Alfred's decisions due from midnight to 24 h ahead: done, skipped or failed today, and planned."""
    fn = _optional("source.anticipate", "decisions_between")
    if fn is None:
        return []
    return fn(db, datetime.combine(now.date(), time()), now + timedelta(hours=FORECAST_HOURS),
              outcome=True)[:MAX_DECISIONS]


# ---------- defensive reading ----------

def _optional(module: str, name: str):
    """A learner's function, or None while its module isn't there (or fails to import)."""
    try:
        return getattr(importlib.import_module(module), name, None)
    except Exception as exc:
        log.debug("%s.%s unavailable: %s", module, name, exc)
        return None


def _guarded(fn, *args, default):
    try:
        return fn(*args)
    except Exception:  # a broken learner must never stop the agent from waking
        log.exception("baselines: %s failed", fn.__name__)
        return default


def _has_table(db, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None
