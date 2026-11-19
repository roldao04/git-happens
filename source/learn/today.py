"""Each learned habit, right now: done, now, expected, late, not today, or unknown.

Plain code over stored sensors, stored kinds and the last 36 h of happenings, so it is cheap
enough for every poll. It feeds the dashboard (`storage.export(..., now=)`) and the agent's
`baselines` (source/context.py), so the heartbeat can notice "no tea yet".

  uv run python -m source.learn.today --db data/home.sqlite3 --at 2026-09-14T08:30

A habit's day runs from 12 h before its usual time to 12 h after (no midnight rule, so it
works for night shifts too). "Late" means later than the conformal bound on its own past
times: the latest so far, with `chance_still` = 1/(n+1) that it still comes (rhythm.late_bound).
"""
import argparse
import json
import logging
import sqlite3
from datetime import datetime, time, timedelta

from . import MAX_LAG_MIN, Happening, is_activity, storage, to_local, to_ms
from .habits import HOLE_MIN, QUIET, WEEKDAYS
from .kinds import classify
from .rhythm import DAY_MIN, hhmm

log = logging.getLogger("source.learn.today")
HALF_DAY = timedelta(minutes=DAY_MIN / 2)


def sensor_today(db: sqlite3.Connection, now: datetime) -> list[dict]:
    rows = db.execute("SELECT signature, width_min, data FROM sensors WHERE learner = 'habit' AND status = 'active'"
                      " ORDER BY id").fetchall()
    if not rows:
        return []
    hs = storage.load_happenings(db, now + timedelta(seconds=1), now - timedelta(hours=36))
    kinds = storage.load_kinds(db)
    for h in hs:
        h.kind = h.kind or classify(h, kinds)  # stored kinds are filled nightly; today's are classified live
    activity = [h for h in hs if is_activity(h)]
    blind = _not_watched(db, now)
    quiet_kinds = {k.id for k in kinds if k.signal == QUIET}
    return [_status(sig, width, json.loads(data), hs, activity, now, blind, quiet_kinds) for sig, width, data in rows]


def baselines(db: sqlite3.Connection, now: datetime) -> dict:
    """What the agent reads as "normal for this home". Never raises: {} if there is nothing yet."""
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sensors'").fetchone():
            return {}
        names = {s["id"]: s for s in (json.loads(d) for (d,) in db.execute(
            "SELECT data FROM sensors WHERE learner = 'habit' AND status = 'active'"))}
        habits = []
        for st in sensor_today(db, now):
            s = names[st["sensor_id"]]
            p = s["pattern"]
            habits.append({"id": s["id"], "name": s["name"] or f"Pattern {s['id'].split('-')[-1]}",
                           "what": " → ".join(p["parts"]), "usual": p["time_of_day"],
                           "weekdays": p.get("weekdays"), "days": f"{p['days_seen']}/{p['days_total']}",
                           "today": st["status"], "at": st["at"], "late_after": st["late_after"]})
        return {"habits": habits} if habits else {}
    except Exception:  # a broken learner must never stop the loop
        log.exception("baselines failed")
        return {}


# ---------- one habit ----------

def _status(signature: str, width: float, sensor: dict, hs: list[Happening], activity: list[Happening],
            now: datetime, blind: bool, quiet_kinds: set[str]) -> dict:
    p = sensor["pattern"]
    usual = _minutes(p["time_of_day"])
    day_start = _day_start(now, usual)

    def at(clock: str) -> datetime:  # a clock time inside this habit's day
        return _at(day_start, _minutes(clock))
    out = {"sensor_id": sensor["id"], "status": None, "at": None, "usual": p["time_of_day"],
           "late_after": p.get("late_after", p.get("latest", p["time_of_day"]))}
    if p.get("weekdays") and WEEKDAYS[(day_start + HALF_DAY).weekday()] not in p["weekdays"]:
        return {**out, "status": "not_today"}
    half_w = timedelta(minutes=width / 2)
    lo, hi = at(p.get("earliest", p["time_of_day"])) - half_w, at(p.get("latest", p["time_of_day"])) + half_w
    found = _occurred(signature, p, hs, activity, day_start, lo, hi, now, quiet_kinds)
    if found:
        status, when = found
        return {**out, "status": status, "at": hhmm(when.hour * 60 + when.minute)}
    if blind:  # the loop isn't polling: absence means nothing
        return {**out, "status": "unknown"}
    if now <= at(out["late_after"]):
        return {**out, "status": "expected"}
    return {**out, "status": "late", "chance_still": p.get("late_chance")}


def _occurred(signature, p, hs, activity, day_start, lo, hi, now, quiet_kinds) -> tuple[str, datetime] | None:
    kind, _, rest = signature.partition(":")
    today = [h for h in hs if day_start <= h.start <= now]
    if kind == "link":  # the first B with an A shortly before it; report that A (the latest one)
        a_part, b_part = rest.split(">", 1)
        lag = timedelta(minutes=MAX_LAG_MIN)
        for b in (h for h in today if _is(h, b_part)):
            before = [a for a in today if _is(a, a_part) and lo <= a.start <= hi
                      and timedelta(0) <= b.start - a.start <= lag]
            if before:
                return "done", max(before, key=lambda a: a.start).start
        return None
    if rest not in quiet_kinds:  # a kind of happening: one started in the usual range
        hit = next((h for h in today if h.kind == rest and lo <= h.start <= hi), None)
        return ("done", hit.start) if hit else None
    quiets = _quiets(activity, now)  # a kind of quiet, worked out live from people's devices
    if kind == "kind-end":  # its end: a quiet at least that long ended in the usual range
        need = timedelta(minutes=p.get("after_quiet_min", 0))
        hit = next((e for s, e in quiets if e is not None and e >= day_start and lo <= e <= hi
                    and e - s >= need), None)
        return ("done", hit) if hit else None
    need = timedelta(minutes=p.get("shortest_min", 0))  # its start: began in the usual range, still on or long
    for s, e in reversed(quiets):
        if lo <= s <= hi and s >= day_start:
            if e is None:
                return "now", s
            if e - s >= need:
                return "done", s
    return None


def _quiets(activity: list[Happening], now: datetime) -> list[tuple[datetime, datetime | None]]:
    """Stretches with no activity, oldest first; the last one has end None if it is still going."""
    out, busy_until = [], None
    for h in sorted(activity, key=lambda h: h.start):
        if h.start > now:
            break
        if busy_until is not None and h.start > busy_until:
            out.append((busy_until, h.start))
        busy_until = h.end if busy_until is None else max(busy_until, h.end)
    if busy_until is not None and busy_until <= now:
        out.append((busy_until, None))
    return out


def _is(h: Happening, part: str) -> bool:
    return h.kind == part or h.signal == part


def _not_watched(db: sqlite3.Connection, now: datetime) -> bool:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'snapshots'").fetchone():
        return False  # unknown: assume watched (hand-made data)
    (last,) = db.execute("SELECT max(ts) FROM snapshots WHERE ts <= ?", (to_ms(now),)).fetchone()
    return last is None or now - to_local(last) > timedelta(minutes=HOLE_MIN)


def _minutes(hhmm_: str) -> float:
    h, m = map(int, hhmm_.split(":"))
    return h * 60 + m


def _day_start(now: datetime, usual_min: float) -> datetime:
    """The last moment 12 h before the habit's usual time."""
    start = datetime.combine(now.date(), time()) + timedelta(minutes=(usual_min - DAY_MIN / 2) % DAY_MIN)
    return start - timedelta(days=1) if start > now else start


def _at(day_start: datetime, clock: float) -> datetime:
    return day_start + timedelta(minutes=(clock - (day_start.hour * 60 + day_start.minute)) % DAY_MIN)


def main() -> None:
    ap = argparse.ArgumentParser(description="Each learned habit's status at a given moment.")
    ap.add_argument("--db", required=True, help="SQLite file with the learners' tables")
    ap.add_argument("--at", required=True, help="the moment, e.g. 2026-09-14T08:30")
    args = ap.parse_args()
    db = sqlite3.connect(args.db)
    for st in sensor_today(db, datetime.fromisoformat(args.at)):
        extra = (f" since {st['at']}" if st["status"] == "now" else f" at {st['at']}" if st["at"]
                 else f", usually by {st['late_after']}" if st["status"] == "late" else "")
        print(f"{st['sensor_id']:<11} usual {st['usual']}  {st['status']}{extra}")


if __name__ == "__main__":
    main()
