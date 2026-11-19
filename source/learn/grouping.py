"""Part A, habits: patterns that recur together, in some rhythm, with variation. See docs/habits.md.

A pattern (a sensor of the habit learner, habits.py) is one thing at a consistent time: "light →
kettle about 07:59", "the end of the night's quiet about 07:59", "light → door about 07:50". A
person calls all three one habit: Waking up. This module groups patterns into habits;
recurrence.py says when each habit happens.

- **Together.** Two groups of patterns are one habit when, on most of the rarer one's days, both
  start within a short window, and more often than chance in this home allows: chance is the
  other one landing that close if it followed the home's activity across the day (the "rhythm"
  null of the links in kinds.py). The window is measured, not fixed: the links' lag windows (up to
  MAX_LAG_MIN), then 2 and 3 times MAX_LAG_MIN, whichever beats chance most, corrected for every
  pair and window tried. Groups merge strongest first until nothing beats chance; a pattern with
  nothing to join is a habit of one step.
- **Every n hours.** Patterns of the same thing at evenly spaced times are one habit
  (recurrence.evenly_spaced).
- **Stable ids.** A habit found today is an existing one when most of its occurrences start at the
  same moments, or it shares most of its patterns (a habit that moved); `habit-N`, `learned_at` and
  a name are kept. One missing longer than chance allows, or not found today, fades; one whose
  patterns now belong to another habit is `merged` (two habits learned apart turned out to be one,
  as sensors merge).

It runs after learn_day at each day end, on the stored sensors, readings and happenings:

  learn_habits(db, day)  → the habits first learned that day
  export_habits(db)      → {"habits": [...], "readings": [...]}
  habit_today(db, now)   → each active habit's status now (from today.py's statuses of its patterns)
"""
import json
import math
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median

from . import ALPHA, MAX_LAG_MIN, MIN_OCCURRENCES, is_activity, storage
from .habits import HOLE_MIN
from .kinds import LAG_WINDOWS
from .recurrence import drift, evenly_spaced, find_recurrence, hourly_rhythm, is_on, on_days, weekday_shift
from .rhythm import DAY_MIN, Rhythm, Watched, circular_mean_std, clock_min, hhmm, poisson_binomial_sf

SCHEMA = """
CREATE TABLE IF NOT EXISTS habits (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS habit_readings (
    habit_id TEXT NOT NULL, day TEXT NOT NULL, start TEXT NOT NULL, end TEXT,
    steps_seen TEXT NOT NULL  -- JSON list of the pattern ids seen, in step order
);
CREATE INDEX IF NOT EXISTS habit_readings_habit ON habit_readings (habit_id, start);
"""
# How far apart the steps of one habit may start: the links' lag windows, then up to three links' length.
TOGETHER_MIN = tuple(LAG_WINDOWS) + (2 * MAX_LAG_MIN, 3 * MAX_LAG_MIN)
# The agreed habit shape (docs/habits-plan.md), in its order.
KEYS = ("id", "name", "named_by", "status", "patterns", "steps", "recurrence", "window", "variation",
        "occurrences", "opportunities", "first_seen", "last_seen", "learned_at", "confidence", "trend")


@dataclass
class Step:
    """One occurrence of a pattern: one of its sensor_readings."""
    pattern: str
    start: datetime  # when it began (a link: its first part)
    moment: datetime  # when its own part happened (a link: its second part, the kettle in light → kettle)
    end: datetime | None  # the reading's end; None for the end of a quiet
    reach: datetime  # how far it stretches a habit: a link's end, else its moment. A stretch (a quiet,
    # a lamp left on) counts by its start, or "waking up" would last until the evening lamp


@dataclass
class Pattern:
    id: str
    signature: str
    centre: float  # its usual clock minute
    steps: list[Step]  # one per day


@dataclass
class Occurrence:
    """A habit on one day: the steps that happened, by pattern id."""
    day: date
    steps: dict[str, Step]

    @property
    def start(self) -> datetime:
        return min(s.start for s in self.steps.values())

    @property
    def last(self) -> datetime:
        return max(s.start for s in self.steps.values())

    @property
    def reach(self) -> datetime:
        return max(s.reach for s in self.steps.values())


@dataclass
class Home:
    """What chance looks like in this home, from its own data."""
    counted: list[date]  # days watched with someone in (run.counted_days)
    watched: Watched
    rhythm: Rhythm  # when people's devices are active, across the day
    spread: float  # how scattered that activity is over the day (circular std, minutes)
    second_parts: dict[tuple[str, datetime], datetime]  # (kind or signal, end) → start of that happening


# ---------- the three entry points ----------

def learn_habits(db: sqlite3.Connection, day: date) -> list[dict]:
    """Run after learn_day at the end of `day`. Returns the habits first learned that day."""
    if not all(_has(db, t) for t in ("sensors", "sensor_readings", "happenings")):
        return []  # the learners haven't run yet
    db.executescript(SCHEMA)
    until = datetime.combine(day + timedelta(days=1), time())
    home = _home(db, until)
    patterns = _patterns(db, until, home)
    groups = _group(patterns, home)
    found = [h for h in (_habit(g, series, home, len(groups)) for g, series in groups) if h]
    return _save(db, found, day)


def export_habits(db: sqlite3.Connection) -> dict:
    """Every habit (agreed shape, docs/habits-plan.md) and every habit reading."""
    if not (_has(db, "habits") and _has(db, "habit_readings")):
        return {"habits": [], "readings": []}
    habits = sorted((json.loads(d) for (d,) in db.execute("SELECT data FROM habits")),
                    key=lambda h: (h["learned_at"], _number(h["id"])))
    readings = [{"habit_id": h, "day": d, "start": s, "end": e, "steps_seen": json.loads(seen)}
                for h, d, s, e, seen in db.execute(
                    "SELECT habit_id, day, start, end, steps_seen FROM habit_readings ORDER BY start, habit_id")]
    return {"habits": habits, "readings": readings}


def habit_today(db: sqlite3.Connection, now: datetime) -> list[dict]:
    """Each active habit now: done, partly, now, expected, late, not_today or unknown.

    Built on today.py's status of each of its patterns. The "usual" steps (there on most days,
    share > 1/2) decide; the rest are optional:
      done      every usual step happened          partly    some did, the rest are late
      now       some happened and the rest may still come, or a usual stretch is going on (asleep)
      expected  nothing yet, still in time         late      nothing yet, later than ever seen
      not_today its rhythm skips today             unknown   not watched (the loop isn't polling)
    """
    if not (_has(db, "habits") and _has(db, "sensors")):
        return []
    habits = sorted((json.loads(d) for (d,) in db.execute("SELECT data FROM habits WHERE status = 'active'")),
                    key=lambda h: _number(h["id"]))
    if not habits:
        return []
    from .today import sensor_today  # today.py reads storage's tables; only needed here
    steps = {st["sensor_id"]: st for st in sensor_today(db, now)}
    return [_status_now(h, steps, now) for h in habits]


# ---------- what the home says ----------

def _home(db: sqlite3.Connection, until: datetime) -> Home:
    from .run import counted_days  # run.py will call learn_habits: imported here, not at the top
    hs = storage.load_happenings(db, until)
    activity = [h for h in hs if is_activity(h)]
    watched = Watched(storage.load_polls(db, until), HOLE_MIN)
    last = (until - timedelta(days=1)).date()
    counted = counted_days(activity, min(h.start for h in hs).date(), last, watched) if hs else []
    spread = circular_mean_std([clock_min(h.start) for h in activity])[1] if activity else DAY_MIN / 2
    second_parts = {}
    for h in hs:
        for part in (h.kind, h.signal):
            if part:
                second_parts[(part, h.end.replace(microsecond=0))] = h.start
    return Home(counted, watched, Rhythm([h.start for h in activity] + [h.end for h in activity]),
                spread, second_parts)


def _patterns(db: sqlite3.Connection, until: datetime, home: Home) -> list[Pattern]:
    """Active habit sensors and their readings, one per day (the nearest its usual time)."""
    rows = db.execute("SELECT id, signature, centre_min FROM sensors WHERE learner = 'habit' AND status = 'active'"
                      " ORDER BY id").fetchall()
    readings = defaultdict(list)
    for sid, start, end in db.execute("SELECT sensor_id, start, end FROM sensor_readings WHERE start < ?"
                                      " ORDER BY start", (until.isoformat(timespec="seconds"),)):
        readings[sid].append((datetime.fromisoformat(start), datetime.fromisoformat(end) if end else None))
    out = []
    for sid, signature, centre in rows:
        by_day: dict[date, Step] = {}
        for start, end in readings.get(sid, []):
            step = _step(sid, signature, start, end, home)
            d = _habit_day(start, centre)
            if d not in by_day or _off(start, centre) < _off(by_day[d].start, centre):
                by_day[d] = step
        if by_day:
            out.append(Pattern(sid, signature, centre, sorted(by_day.values(), key=lambda s: s.start)))
    return out


def _step(pattern: str, signature: str, start: datetime, end: datetime | None, home: Home) -> Step:
    kind, _, rest = signature.partition(":")
    if kind == "link" and end is not None:  # its second part ended at `end`; find when it began
        moment = home.second_parts.get((rest.split(">", 1)[-1], end), start)
        return Step(pattern, start, moment, end, end)
    return Step(pattern, start, start, end, start)


# ---------- grouping ----------

def _group(patterns: list[Pattern], home: Home) -> list[tuple[list[Pattern], tuple | None]]:
    """→ [(patterns, series)]: `series` (evenly_spaced's result) for an every-n-hours habit, else None."""
    by_signature = defaultdict(list)
    for p in patterns:
        by_signature[p.signature].append(p)
    several = [ps for ps in by_signature.values() if len(ps) >= 3]
    out, taken = [], set()
    for ps in several:
        series = evenly_spaced([p.centre for p in ps])
        if series and series[-1] * len(several) < ALPHA and _same_days(ps):
            out.append((ps, series))
            taken.update(p.id for p in ps)
    out += [(g, None) for g in _merge_together([p for p in patterns if p.id not in taken], home)]
    return out


def _same_days(patterns: list[Pattern]) -> bool:
    """An every-n-hours series is one habit: most of each slot's days have another slot too."""
    anchor = circular_mean_std([p.centre for p in patterns])[0]
    days = [{_habit_day(s.start, anchor) for s in p.steps} for p in patterns]
    return all(2 * len(d & set().union(*(o for j, o in enumerate(days) if j != i))) > len(d)
               for i, d in enumerate(days))


def _merge_together(patterns: list[Pattern], home: Home) -> list[list[Pattern]]:
    """Agglomerative: merge the two groups that are most surely together, until none beats chance.
    The tries are every pair of patterns and every window, as at the start."""
    groups = [[p] for p in patterns]
    occ = [_occurrences(g) for g in groups]
    tries = max(1, len(groups) * (len(groups) - 1) // 2 * len(TOGETHER_MIN))
    p = {(i, j): _together(occ[i], occ[j], home) for i in range(len(groups)) for j in range(i + 1, len(groups))}
    alive = set(range(len(groups)))
    while True:
        best = min(((v, k) for k, v in p.items() if k[0] in alive and k[1] in alive), default=None)
        if best is None or best[0] * tries >= ALPHA:
            return [groups[i] for i in sorted(alive)]
        _, (i, j) = best
        groups[i] += groups[j]
        alive.discard(j)
        occ[i] = _occurrences(groups[i])
        for k in alive - {i}:
            p[(min(i, k), max(i, k))] = _together(occ[i], occ[k], home)


def _together(a: list[Occurrence], b: list[Occurrence], home: Home) -> float:
    """p-value of the two happening together this often by chance, at the best window; 1 when
    most of the rarer one's occurrences aren't with the other (they don't recur together).

    Together on a day: all their steps start within w minutes. Chance, per occurrence of
    the rarer one: the other, at its own rate per counted day, landing within ±w if it followed
    the home's activity across the day (1 - exp(-rate · share of activity in the window)).
    """
    small, big = sorted((a, b), key=len)
    if len(small) < MIN_OCCURRENCES or not home.counted:
        return 1.0
    rate = len(big) / len(home.counted)
    starts = [o.start for o in big]
    best = 1.0
    for w in TOGETHER_MIN:
        window = timedelta(minutes=w)
        k, chance = 0, []
        for o in small:
            near = big[bisect_left(starts, o.start - window):bisect_right(starts, o.start + window)]
            k += any(max(o.last, x.last) - min(o.start, x.start) <= window for x in near)
            chance.append(1 - math.exp(-rate * home.rhythm.share(clock_min(o.start) - w, 2 * w)))
        if k >= MIN_OCCURRENCES and 2 * k > len(small):
            best = min(best, poisson_binomial_sf(k, chance))
    return best


def _occurrences(patterns: list[Pattern]) -> list[Occurrence]:
    """The group's steps, one occurrence per habit day (12 h either side of its usual time)."""
    steps = [s for p in patterns for s in p.steps]
    anchor = circular_mean_std([clock_min(s.start) for s in steps])[0]
    by_day: dict[date, dict[str, Step]] = defaultdict(dict)
    for s in sorted(steps, key=lambda s: s.start):
        by_day[_habit_day(s.start, anchor)].setdefault(s.pattern, s)
    return [Occurrence(d, st) for d, st in sorted(by_day.items())]


# ---------- one habit ----------

def _habit(patterns: list[Pattern], series: tuple | None, home: Home, tries: int) -> dict | None:
    """The habit's facts (agreed shape, without id, name and learned_at) and its readings, or None
    when fewer than MIN_OCCURRENCES of its occurrences fall on days its rhythm expects it."""
    occ = _occurrences(patterns)
    ids = [p.id for p in patterns]
    start_off, moment_off, share = {}, {}, {}
    for pid in ids:  # where each step sits in the habit, and how often it is there
        present = [o for o in occ if pid in o.steps]
        start_off[pid] = median(_min(o.steps[pid].start - o.start) for o in present)
        moment_off[pid] = median(_min(o.steps[pid].moment - o.start) for o in present)
        share[pid] = len(present) / len(occ)
    order = sorted(ids, key=lambda pid: (moment_off[pid], -share[pid], _number(pid)))
    # when each occurrence began, even on a day its first step was missing
    began = [min(s.start - timedelta(minutes=start_off[pid]) for pid, s in o.steps.items()) for o in occ]
    days = [o.day for o in occ]
    centre = circular_mean_std([clock_min(t) for t in began])[0]
    mins = [centre + _wrap(clock_min(t) - centre) for t in began]  # no midnight jump
    usual, by_weekday, residuals = weekday_shift(days, mins)
    lo, hi = min(mins), max(mins)
    # the days it could have happened on: counted, from its first day on, with its window watched
    could = [d for d in home.counted if d >= days[0] and home.watched.saw(*_window_on(d, lo, hi))]
    rec = hourly_rhythm(series, days, could) if series else find_recurrence(days, could)
    on = on_days(rec, could)
    hits = sorted(set(days) & set(on))
    if len(hits) < MIN_OCCURRENCES:
        return None
    spread = float(math.sqrt(sum(r * r for r in residuals) / len(residuals)))
    habit = {
        "status": "fading" if _stopped(len(hits), on, hits[-1]) else "active",
        "patterns": order,
        "steps": [{"pattern": pid, "offset_min": round(moment_off[pid]), "share": round(share[pid], 2)}
                  for pid in order],
        "recurrence": rec,
        "window": {"usual": hhmm(usual), "earliest": hhmm(lo), "latest": hhmm(hi)},
        "variation": {"spread_min": round(spread), "by_weekday": by_weekday},
        "occurrences": len(hits), "opportunities": len(on),
        "first_seen": days[0].isoformat(), "last_seen": days[-1].isoformat(),
        "confidence": confidence(len(hits), len(on), spread, home.spread),
        "trend": {"minutes_per_week": drift(days, residuals, tries)},
    }
    readings = [{"day": o.day.isoformat(), "start": _iso(o.start), "end": _iso(_reading_end(o, len(ids))),
                 "steps_seen": [pid for pid in order if pid in o.steps]} for o in occ]
    return {"habit": habit, "readings": readings, "starts": [o.start for o in occ]}


def confidence(occurrences: int, opportunities: int, spread_min: float, chance_spread_min: float) -> float:
    """confidence = hit rate × evidence × timing, in [0, 1] (docs/habits.md):

      hit rate  occurrences / opportunities: how often it happens when its rhythm says it should
      evidence  n / (n + 1), n = occurrences: one example gives at most 0.5 (docs/habits-plan.md)
      timing    1 - spread / chance spread: 1 when it always starts at the same minute, 0 when its
                start (weekday shifts taken out) is as scattered as the home's activity over the day
    """
    if not occurrences or not opportunities:
        return 0.0
    hit = min(1.0, occurrences / opportunities)
    evidence = occurrences / (occurrences + 1)
    timing = max(0.0, 1 - spread_min / chance_spread_min) if chance_spread_min > 0 else 1.0
    return round(hit * evidence * timing, 2)


def _stopped(hits: int, on: list[date], last: date) -> bool:
    """Missing on every day its rhythm expected it since it was last seen, and that is unlikely by
    chance (habits.py's rule of succession: r = (hits + 1) / (days + 2), m misses: (1 - r)^m)."""
    m = sum(d > last for d in on)
    r = (hits + 1) / (len(on) + 2)
    return (1 - r) ** m < ALPHA


def _reading_end(o: Occurrence, steps: int) -> datetime | None:
    """A habit of one step keeps its reading's own end (asleep until 07:59); a routine ends with its last step."""
    if steps == 1:
        return next(iter(o.steps.values())).end
    return o.reach


# ---------- storage ----------

def _save(db: sqlite3.Connection, found: list[dict], day: date) -> list[dict]:
    """Upsert today's habits and their readings; returns those first learned today.

    A found habit is an existing one when most of the smaller one's occurrences start within
    MAX_LAG_MIN of each other, or most of the smaller one's patterns are shared (see _same). Id,
    learned_at and a name are kept, unless most of its patterns changed (two habits merged): then
    the LLM's name and card go, so the namer names it again (a person's name is never touched).
    Readings of the days before its first occurrence today are kept as history. One not found
    today is never deleted: it is "merged" when most of its patterns are now in another habit,
    else "fading".
    """
    old = {hid: json.loads(d) for hid, d in db.execute("SELECT id, data FROM habits")}
    old_starts: dict[str, list[datetime]] = defaultdict(list)
    for hid, start in db.execute("SELECT habit_id, start FROM habit_readings ORDER BY start"):
        old_starts[hid].append(datetime.fromisoformat(start))
    scored = sorted(((_same(h, f, old_starts[hid]), i, hid) for i, f in enumerate(found) for hid, h in old.items()),
                    key=lambda x: (tuple(-v for v in x[0]), _number(x[2]), x[1]))  # best, then the oldest
    matched: dict[int, str] = {}
    for score, i, hid in scored:
        if score[0] and i not in matched and hid not in matched.values():
            matched[i] = hid
    next_n = max((_number(hid) for hid in old), default=0)
    new, kept = [], []
    for i, f in enumerate(found):
        before = old.get(matched.get(i))
        if before is None and f["habit"]["status"] != "active":
            continue  # never start a habit that has already stopped
        if before is None:
            next_n += 1
            before = {"id": f"habit-{next_n}", "name": None, "named_by": None, "learned_at": day.isoformat(),
                      "patterns": []}
        elif _changed(before["patterns"], f["habit"]["patterns"]) and before.get("named_by") != "person":
            before = {k: v for k, v in before.items() if k in KEYS} | {"name": None, "named_by": None}
        card = {k: v for k, v in before.items() if k not in KEYS}  # what the namer may add, kept
        habit = {"id": before["id"], "name": before.get("name"), "named_by": before.get("named_by"),
                 **f["habit"], "learned_at": before["learned_at"]}
        habit = {k: habit[k] for k in KEYS} | card
        if before["id"] not in old:
            new.append(habit)
        kept.append(set(habit["patterns"]))
        db.execute("INSERT OR REPLACE INTO habits (id, status, data) VALUES (?, ?, ?)",
                   (habit["id"], habit["status"], json.dumps(habit)))
        db.execute("DELETE FROM habit_readings WHERE habit_id = ? AND day >= ?",  # one reading per day
                   (habit["id"], f["readings"][0]["day"]))
        db.executemany("INSERT INTO habit_readings (habit_id, day, start, end, steps_seen) VALUES (?, ?, ?, ?, ?)",
                       [(habit["id"], r["day"], r["start"], r["end"], json.dumps(r["steps_seen"]))
                        for r in f["readings"]])
    for hid, h in old.items():
        if hid in matched.values() or h["status"] == "merged":
            continue
        mine = set(h["patterns"])
        status = "merged" if any(2 * len(mine & k) > len(mine) for k in kept) else "fading"
        if status != h["status"]:
            h["status"] = status
            db.execute("UPDATE habits SET status = ?, data = ? WHERE id = ?", (status, json.dumps(h), hid))
    db.commit()
    return new


def _changed(old: list[str], new: list[str]) -> bool:
    """Most of the habit is different: more than half of all its patterns came or went."""
    a, b = set(old), set(new)
    return 2 * len(a ^ b) > len(a | b)


def _same(old: dict, f: dict, old_starts: list[datetime]) -> tuple[int, int, int]:
    """How surely `f` is the habit `old`: (criteria met, occurrences at the same moments, patterns
    shared), compared best first. The criteria: most of the smaller one's occurrences start within
    MAX_LAG_MIN of the other's (the ground truth: pattern ids can be refined into other things),
    and most of the smaller one's patterns are shared (a habit that moved to another time). The
    counts break ties, so a one-pattern leftover of a habit never takes its id."""
    a, b = set(old["patterns"]), set(f["habit"]["patterns"])
    shared = len(a & b)
    gap = timedelta(minutes=MAX_LAG_MIN)
    same = sum(bisect_right(old_starts, s + gap) > bisect_left(old_starts, s - gap) for s in f["starts"])
    met = int(bool(a and b) and 2 * shared > min(len(a), len(b)))
    met += int(bool(old_starts) and 2 * same > min(len(old_starts), len(f["starts"])))
    return met, same, shared


# ---------- today ----------

def _status_now(habit: dict, steps: dict[str, dict], now: datetime) -> dict:
    out = {"habit_id": habit["id"], "status": None, "at": None, "usual": habit["window"]["usual"], "steps_done": []}
    day = _habit_day(now, _clock(habit["window"]["usual"]))
    mine = [(s, steps[s["pattern"]]) for s in habit["steps"] if s["pattern"] in steps]
    today = [(s, st) for s, st in mine if st["status"] != "not_today"]
    if not today or not is_on(habit["recurrence"], day):
        return {**out, "status": "not_today"}
    begun = [(s, st) for s, st in today if st["status"] in ("done", "now")]
    usual = {st["status"] for s, st in today if s["share"] > 0.5} or {st["status"] for _, st in today}
    if begun:
        out.update(steps_done=[s["pattern"] for s, _ in begun], at=begun[0][1]["at"])
        if usual <= {"done", "now"}:
            status = "now" if "now" in usual else "done"
        else:
            status = "now" if "expected" in usual else "partly"
    elif {st["status"] for _, st in today} == {"unknown"}:
        status = "unknown"
    else:
        status = "expected" if "expected" in usual else "late" if "late" in usual else "unknown"
    return {**out, "status": status}


# ---------- small helpers ----------

def _habit_day(t: datetime, usual_min: float) -> date:
    """The day `t` counts for: days run from 12 h before the usual time to 12 h after (as in
    today.py), so a habit around midnight keeps its steps on one day."""
    return (t - timedelta(minutes=usual_min - DAY_MIN / 2)).date()


def _window_on(d: date, lo: float, hi: float) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time()) + timedelta(minutes=lo)
    return start, start + timedelta(minutes=hi - lo)


def _off(t: datetime, centre: float) -> float:
    return abs(_wrap(clock_min(t) - centre))


def _wrap(minutes: float) -> float:
    """A clock difference folded into (-12 h, 12 h]."""
    return (minutes + DAY_MIN / 2) % DAY_MIN - DAY_MIN / 2


def _min(d: timedelta) -> float:
    return d.total_seconds() / 60


def _clock(hhmm_: str) -> float:
    h, m = map(int, hhmm_.split(":"))
    return h * 60 + m


def _iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


def _number(hid: str) -> int:
    return int(hid.rsplit("-", 1)[-1])


def _has(db: sqlite3.Connection, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None
