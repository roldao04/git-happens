"""Part 6, the habit learner: kinds and links → habits. See docs/learners.md.

Quiet stretches (people's devices all at rest) are happenings of the home, `home.quiet`, grouped
into kinds like any signal; their starts and ends can become habits (asleep, away, awake are
whichever quiet kinds recur when, named later by the namer).

One test for everything: slide windows round the clock. A window counts as a habit if more of
the candidate's occurrences fall in it than chance allows. Chance = any moment of the day for
the quiets; for everything else, any moment the home isn't in one of its recurring quiets. Then:
- a weekday test (single days, work days, weekends) checks whether it favours some days;
- an absence test drops it once it has been missing for longer than chance allows.
Only days the home was watched (polls) and someone was in (people's activity) count.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np

from . import ALPHA, MIN_OCCURRENCES, Happening
from .kinds import Kind, Link
from .rhythm import DAY_MIN, Rhythm, Watched, binomial_sf, circular_mean_std, clock_min, hhmm, late_bound

WINDOWS_MIN = (20, 40, 60)
WINDOWS_PER_DAY = sum(DAY_MIN // w for w in WINDOWS_MIN)  # independent windows tried per candidate
HOLE_MIN = 30  # no poll for this long = not watched (the same as ingest's default gap_ms)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_GROUPS = [(d,) for d in range(7)] + [(0, 1, 2, 3, 4), (5, 6)]  # single days, work days, weekend
QUIET = "home.quiet"  # the signal of quiet stretches


@dataclass
class Occurrence:
    start: datetime
    end: datetime | None
    day: date  # the day (or, for the quiet, the night) it belongs to
    t: float  # clock minute the scan uses
    happenings: tuple = ()
    value: float | None = None


@dataclass
class Candidate:
    key: str  # what it is, without the time: "kind:plug-A", "kind-end:home-A", "link:<a>><b>"
    parts: list[str]
    occurrences: list[Occurrence]
    days: list[date]  # the counted history
    facts: dict = field(default_factory=dict)
    quiet: bool = False  # a home.quiet kind (its start or its end)


@dataclass
class Habit:
    key: str
    parts: list[str]
    centre_min: float
    width_min: float
    members: list[Occurrence]  # first occurrence per day inside the window
    days_seen: int
    days_total: int
    weekdays: list[str] | None
    spread_min: float
    p: float
    facts: dict = field(default_factory=dict)

    @property
    def confidence(self) -> float:
        return round(min(1.0, self.days_seen / max(1, self.days_total)), 2)

    def pattern(self) -> dict:
        out = {"parts": self.parts, "time_of_day": hhmm(self.centre_min), "spread_min": round(self.spread_min),
               "days_seen": self.days_seen, "days_total": self.days_total}
        if self.weekdays:
            out["weekdays"] = self.weekdays
        out.update(self.facts)
        return out


def find_habits(happenings: list[Happening], kinds: list[Kind], links: list[Link], days: list[date],
                rhythm: Rhythm, activity: list[Happening], watched: Watched | None = None) -> list[Habit]:
    """`happenings` includes the quiet stretches (see quiet_happenings); `days`: the counted days."""
    watched = watched or Watched(None, HOLE_MIN)
    candidates = _kind_candidates(happenings, kinds, days, activity) + _link_candidates(links, kinds, happenings, days)
    tries = max(1, len(candidates)) * WINDOWS_PER_DAY
    # pass 1: the quiets, against any moment of the day; pass 2: the rest, against the home's other hours
    quiets = [h for c in candidates if c.quiet for h in _habits_of(c, tries, watched, [])]
    spans = [(h.centre_min, _minutes(h.facts["until"])) for h in quiets if h.key.startswith("kind:")]
    others = [h for c in candidates if not c.quiet for h in _habits_of(c, tries, watched, spans)]
    return _drop_covered(quiets + others)


def quiet_happenings(activity: list[Happening], watched: Watched | None = None) -> list[Happening]:
    """Stretches where people's devices all rest, as happenings of the home (`home.quiet`).

    `value` says which devices it sits between ("after bulb until contactSensor"); a stretch that
    wasn't watched all through (a hole in the polls) is cut, so it gets no kind.
    """
    watched = watched or Watched(None, HOLE_MIN)
    hs = sorted(activity, key=lambda h: h.start)
    out: list[Happening] = []
    if not hs:
        return out
    busy_until, last = hs[0].end, hs[0]
    for h in hs[1:]:
        if h.start > busy_until:
            out.append(Happening(QUIET, busy_until, h.start, None, f"after {last.device} until {h.device}",
                                 cut=not watched.unbroken(busy_until, h.start)))
        if h.end >= busy_until:
            busy_until, last = h.end, h
    return out


# ---------- candidates ----------

def _kind_candidates(happenings, kinds, days, activity) -> list[Candidate]:
    counted = set(days)
    by_start = {h.start: h for h in activity}  # what ended each quiet stretch
    out = []
    for k in kinds:
        hs = [h for h in happenings if h.kind == k.id and h.start.date() in counted]
        occ = [Occurrence(h.start, h.end, h.start.date(), clock_min(h.start), (h,), h.size) for h in hs]
        quiet = k.signal == QUIET
        out.append(Candidate(f"kind:{k.id}", [k.description], occ, days, quiet=quiet))
        if quiet:  # its end can be a habit of its own (waking up at 07:00 on work days, 09:30 at weekends)
            ends = [Occurrence(h.end, None, h.end.date(), clock_min(h.end),
                               (by_start[h.end],) if h.end in by_start else (), h.duration_s / 60)
                    for h in hs if h.end.date() in counted]
            out.append(Candidate(f"kind-end:{k.id}", [f"end of {k.description}"], ends, days, quiet=True))
    return out


def _link_candidates(links, kinds, happenings, days) -> list[Candidate]:
    counted = set(days)
    out = []
    for l in links:
        occ = [Occurrence(a.start, b.end, a.start.date(), clock_min(a.start), (a, b), b.size)
               for a, b in l.pairs if a.start.date() in counted]
        parts = [describe_part(l.a, kinds, happenings), describe_part(l.b, kinds, happenings)]
        # named by A's signal, not its kind: "door → kettle" stays one sensor whichever door kind wins
        a_signal = l.pairs[0][0].signal
        out.append(Candidate(f"link:{a_signal}>{l.b}", parts, occ, days, {"lag_min": round(l.lag_min)}))
    return out


def describe_part(part: str, kinds: list[Kind], happenings: list[Happening]) -> str:
    for k in kinds:
        if k.id == part:
            return k.description
    values = Counter(h.value for h in happenings if h.signal == part and h.value)
    device, cap = part.split(".", 1)
    value = values.most_common(1)[0][0] if values else "active"
    return f"{device} {cap} {value} (any)"


# ---------- the test ----------

def _habits_of(c: Candidate, tries: int, watched: Watched, spans: list[tuple[float, float]]) -> list[Habit]:
    occ = sorted(c.occurrences, key=lambda o: o.start)
    if len(occ) < MIN_OCCURRENCES:
        return []
    t = np.array([o.t for o in occ])
    left = np.ones(len(occ), bool)
    removed: list[tuple[float, float]] = []
    habits = []
    while left.sum() >= MIN_OCCURRENCES:
        best = None
        for w in WINDOWS_MIN:
            for s in np.unique(t[left]):
                inside = left & ((t - s) % DAY_MIN < w)
                k = int(inside.sum())
                if k < MIN_OCCURRENCES or len({occ[i].day for i in np.flatnonzero(inside)}) < MIN_OCCURRENCES:
                    continue
                p = binomial_sf(k, int(left.sum()), _chance(s, w, removed, spans))
                if best is None or p < best[0]:
                    best = (p, s, w, inside)
        if best is None or best[0] * tries >= ALPHA:
            break
        p, s, w, inside = best
        habit = _habit(c, [occ[i] for i in np.flatnonzero(inside)], float(s), w, min(1.0, p * tries), watched)
        if habit:
            habits.append(habit)
        left &= ~inside
        removed.append((s, w))
    return habits


_CLOCK = np.arange(DAY_MIN)


def _chance(s: float, w: float, removed: list[tuple[float, float]], spans: list[tuple[float, float]]) -> float:
    """Chance a random moment falls in [s, s+w): any moment outside the home's recurring quiets
    (`spans`, clock start → end) and outside this candidate's earlier peaks. Never below w/1440."""
    keep = np.ones(DAY_MIN, bool)
    for start, end in spans:
        keep &= ~((_CLOCK - start) % DAY_MIN < (end - start) % DAY_MIN)
    for rs, rw in removed:
        keep &= ~((_CLOCK - rs) % DAY_MIN < rw)
    inside = (_CLOCK - s) % DAY_MIN < w
    return max(float((inside & keep).sum() / max(1, keep.sum())), w / DAY_MIN)


def _minutes(clock: str) -> float:
    h, m = map(int, clock.split(":"))
    return h * 60 + m


def _habit(c: Candidate, inside: list[Occurrence], s: float, w: float, p: float, watched: Watched) -> Habit | None:
    """Facts for one window, or None when the habit has stopped (its absence beats chance)."""
    firsts: dict[date, Occurrence] = {}
    for o in sorted(inside, key=lambda o: o.start):
        firsts.setdefault(o.day, o)
    members = list(firsts.values())
    # the days it could have happened on: from its first occurrence, with the window watched
    since = [d for d in c.days if d >= members[0].day and watched.saw(*_window_on(d, s, w))]
    weekdays = _favoured_weekdays([o.day for o in members], since)
    pool = [d for d in since if not weekdays or WEEKDAYS[d.weekday()] in weekdays]
    seen = [o for o in members if not weekdays or WEEKDAYS[o.day.weekday()] in weekdays]
    if not seen or _stopped(len(seen), pool, seen[-1].day):
        return None
    centre, spread = circular_mean_std([o.t for o in members])
    after_start = sorted((o.t - s) % DAY_MIN for o in members)  # clock times, unwrapped from the window start
    bound, chance = late_bound(after_start, ALPHA)
    facts = dict(c.facts)
    facts.update(first_seen=members[0].day.isoformat(), last_seen=members[-1].day.isoformat(),
                 earliest=hhmm(s + after_start[0]), latest=hhmm(s + after_start[-1]),
                 late_after=hhmm(s + bound), late_chance=round(chance, 3))
    if c.key.startswith("kind:"):  # a stretch: when it usually ends, how long it lasts
        lasted = [(o.end - o.start).total_seconds() / 60 for o in members]
        facts.update(until=hhmm(circular_mean_std([clock_min(o.end) for o in members])[0]),
                     duration_min=round(float(np.median(lasted))), shortest_min=round(min(lasted)))
    if c.key.startswith("kind-end:"):  # how long the quiet before it lasted, at least
        facts["after_quiet_min"] = round(min(o.value for o in members))
    return Habit(c.key, c.parts, centre, w, members, len(seen), len(pool), weekdays, spread, p, facts)


def _window_on(d: date, s: float, w: float) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time()) + timedelta(minutes=s)
    return start, start + timedelta(minutes=w)


def _stopped(seen: int, pool: list[date], last: date) -> bool:
    """Missing on every counted day since `last`, and that is unlikely by chance.

    Rule of succession: it happens on a counted day with probability r = (seen+1)/(days+2), so
    m misses in a row has probability (1-r)^m.
    """
    m = sum(d > last for d in pool)
    r = (seen + 1) / (len(pool) + 2)
    return (1 - r) ** m < ALPHA


def _favoured_weekdays(seen: list[date], history: list[date]) -> list[str] | None:
    """Single weekdays, or else work days / weekend, holding more occurrences than their share allows."""
    if not history:
        return None
    k = len(seen)
    have = Counter(d.weekday() for d in seen)
    total = Counter(d.weekday() for d in history)

    def significant(group) -> bool:
        k_g = sum(have[d] for d in group)
        return k_g > 0 and binomial_sf(k_g, k, sum(total[d] for d in group) / len(history)) * len(WEEKDAY_GROUPS) < ALPHA

    singles = [g[0] for g in WEEKDAY_GROUPS if len(g) == 1 and significant(g)]
    if singles:
        return [WEEKDAYS[d] for d in singles]
    for g in WEEKDAY_GROUPS:
        if len(g) > 1 and significant(g):
            return [WEEKDAYS[d] for d in g]
    return None


def _drop_covered(habits: list[Habit]) -> list[Habit]:
    """One card per routine:
    - two links ending in mostly the same happenings (door → kettle, lamp → kettle): keep the stronger;
    - a kind habit mostly inside a link, or what ends a recurring quiet (the morning light *is* the wake-up): drop it;
    - a quiet that mostly sits inside a routine (the pause between door and kettle): drop it and its end.
    """
    links, kept_b = [], []
    for l in sorted((h for h in habits if h.key.startswith("link:")), key=lambda h: (-h.days_seen, h.p)):
        b = {id(o.happenings[-1]) for o in l.members}
        if any(len(b & other) * 2 > len(b) for other in kept_b):
            continue
        links.append(l)
        kept_b.append(b)
    ends = [h for h in habits if h.key.startswith("kind-end:")]
    covering = [{id(x) for o in h.members for x in o.happenings} for h in links + ends]
    spans = [(o.start, o.end) for l in links for o in l.members]  # each routine's own time
    quiet_kinds = {h.key.split(":", 1)[1] for h in habits if h.key.startswith("kind:") and _inside_most(h, spans)
                   and h.members[0].happenings[0].signal == QUIET}
    out = []
    for h in habits:
        if h.key.startswith("link:") and h not in links:
            continue
        if h.key.split(":", 1)[-1] in quiet_kinds:  # a pause inside a routine (door … kettle) is part of it
            continue
        if h.key.startswith("kind:") and not h.key.split(":", 1)[1] in quiet_kinds:
            mine = [id(x) for o in h.members for x in o.happenings]
            if any(sum(i in covered for i in mine) * 2 > len(mine) for covered in covering):
                continue
        out.append(h)
    return out


def _inside_most(h: Habit, spans: list[tuple[datetime, datetime]]) -> bool:
    inside = sum(any(a <= o.start and o.end <= b for a, b in spans) for o in h.members)
    return inside * 2 > len(h.members)
