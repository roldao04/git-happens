"""Forecast: what each learned habit will probably do in the next 24 hours.

  uv run python -m source.learn.forecast --db data/sim/rosa/home.sqlite3 --at 2026-09-21T22:00

Plain code over the `habits` table and its `habit_readings` (shapes in docs/contracts.md,
written by the grouper, source/learn/grouping.py). For each active habit, every occurrence its
rhythm (`recurrence`) makes due with an expected time in [now, now + hours) gets
(docs/anticipation.md):

- probability: how often the habit happened on that kind of day: the same weekday for daily and
  weekly habits, its due days for every-n-days and monthly ones, the same time slot for hourly
  ones. A beta estimate pulls that count towards the habit's overall rate by PRIOR_DAYS
  imaginary days, so "3 Mondays of 3" doesn't read as certain;
- expected time: its usual time, moved by its weekday shift (`variation.by_weekday`, "later at
  weekends") and by its drift (`trend`) since the middle of its history; window: its own window,
  or ± twice its spread when some weekdays are shifted. A habit with weekday `windows` (C3 in
  docs/contracts.md) takes the usual time and window of the day's own window instead;
- shifted: when an occasion going on now (or the one that ended the night before) has moved
  this habit the next day in its past episodes (occasions.shifts_now, `shifts`), the expected time
  and window move by that much, and `shifted` keeps what they were before;
- confidence = probability × the habit's confidence, computed here, never by the LLM;
- basis: the counts behind it, in plain words ("Mondays: 9 of 10 since 1 Sept").

Due days follow the grouper's own rule (recurrence.is_on), so its `opportunities` and these
counts agree. Times are the home's clock time (naive, Europe/Lisbon). Never raises: [] when
there is nothing to forecast, e.g. before the `habits` table exists.
"""
import argparse
import json
import logging
import math
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from statistics import median

from ..clock import TZ
from . import to_local

log = logging.getLogger("source.learn.forecast")

PRIOR_DAYS = 2  # weight of the habit's overall rate in each kind of day's estimate, in days
MIN_PROBABILITY = 0.1  # occurrences less likely than this are left out (an hourly habit's night slots)
MAX_DRIFT_MIN = 60  # a drift is followed this far at most: it is a trend, not a promise
RECENT = 8  # an hourly slot's time: the median of its last readings
SHIFT_CONFIDENCE = 0.5  # an occasion moves tomorrow's habits once its match is this sure (the occasion wake's bar, C8)
DAY_MIN = 1440

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_PLURAL = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec")
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5, "last": -1}


@dataclass
class Reading:
    """One time a habit happened (a `habit_readings` row)."""
    day: date  # the habit's own day (the grouper's: its usual time falls on that date)
    start: datetime


@dataclass
class Slot:
    """One forecast occurrence. `forecast()` returns its shared shape; anticipation needs the datetimes."""
    habit: dict
    day: date  # the habit's own day (decisions are one per habit per day); hourly: the calendar day
    expected: datetime
    earliest: datetime
    latest: datetime
    probability: float
    confidence: float
    basis: str
    slot: str | None = None  # hourly habits: the slot's clock time
    shifted: dict | None = None  # moved by an occasion: {"occasion_id", "minutes", "before": {"expected", "window"}}

    def to_dict(self) -> dict:
        out = {"habit_id": self.habit["id"], "date": self.expected.date().isoformat(),
               "probability": round(self.probability, 2), "expected": hhmm(self.expected),
               "window": [hhmm(self.earliest), hhmm(self.latest)],
               "confidence": round(self.confidence, 2), "basis": self.basis}
        if self.shifted:
            out["shifted"] = self.shifted
        return out


def forecast(db: sqlite3.Connection, now: datetime, hours: int = 24) -> list[dict]:
    """Each active habit's occurrences expected in [now, now + hours), soonest first (Forecast shape)."""
    return [s.to_dict() for s in upcoming(db, now, hours)]


def upcoming(db: sqlite3.Connection, now: datetime, hours: float = 24, *, strict: bool = False) -> list[Slot]:
    """Like forecast(), with datetimes. Never raises, unless `strict`: then a failure to read the
    habits raises instead of looking like "nothing expected" (the planner must tell them apart)."""
    try:
        now = as_local(now)
        habits = active_habits(db)
        if not habits:
            return []
        readings = load_readings(db, now)
        end = _history_end(habits, readings, now)
        seen_any = {r.day for rs in readings.values() for r in rs}  # days some habit happened: someone was in
        shifts = occasion_shifts(db, now)
        out: list[Slot] = []
        for h in habits:
            try:
                out += _habit_slots(h, readings.get(h["id"], []), now, hours, end, seen_any, shifts)
            except Exception:  # one odd habit must not hide the others
                log.exception("forecast failed for %s", h.get("id"))
        return sorted(out, key=lambda s: (s.expected, s.habit["id"]))
    except Exception:  # the loop and the agent call this: never stop them
        if strict:
            raise
        log.exception("forecast failed")
        return []


# ---------- reading the habits ----------

def has_table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def active_habits(db: sqlite3.Connection) -> list[dict]:
    """The active habits (Habit shape), oldest id first; [] if the `habits` table isn't there yet."""
    if not has_table(db, "habits"):
        return []
    out = []
    for _id, status, data in db.execute("SELECT id, status, data FROM habits ORDER BY id"):
        try:
            h = json.loads(data)
        except (TypeError, ValueError):
            continue
        if isinstance(h, dict) and (status or h.get("status")) == "active":
            h.setdefault("id", _id)
            out.append(h)
    return sorted(out, key=lambda h: _id_order(h["id"]))


def load_readings(db: sqlite3.Connection, before: datetime) -> dict[str, list[Reading]]:
    """habit id → its readings that started before `before` (a forecast never sees the future)."""
    if not has_table(db, "habit_readings"):
        return {}
    out: dict[str, list[Reading]] = defaultdict(list)
    for habit_id, day, start in db.execute("SELECT habit_id, day, start FROM habit_readings"):
        d = _date(day)
        t = parse_moment(start, d)
        if t is None or t >= before:
            continue
        out[habit_id].append(Reading(d or t.date(), t))
    for rs in out.values():
        rs.sort(key=lambda r: r.start)
    return dict(out)


def occasion_shifts(db: sqlite3.Connection, now: datetime) -> dict[tuple[str, str], tuple[str, int]]:
    """(habit id, date) → (occasion id, minutes): how an occasion going on now, or the one the night
    before, moves a habit (occasions.shifts_now), once its match reaches SHIFT_CONFIDENCE. The most
    confident occasion wins. {} when there are no occasions; never raises."""
    if not has_table(db, "occasions") or not db.execute("SELECT 1 FROM occasions LIMIT 1").fetchone():
        return {}
    try:
        from .occasions import shifts_now  # occasions reads the habits; only needed here
        out: dict[tuple[str, str], tuple[str, int, float]] = {}
        for s in shifts_now(db, now):
            key = (s["habit_id"], s["date"])
            if s["confidence"] >= SHIFT_CONFIDENCE and (key not in out or s["confidence"] > out[key][2]):
                out[key] = (s["occasion_id"], int(s["minutes"]), s["confidence"])
        return {k: v[:2] for k, v in out.items()}
    except Exception:
        log.exception("occasion shifts failed")
        return {}


def day_window(h: dict, d: date) -> tuple[float, float, float] | None:
    """(usual, earliest, latest) clock minutes of a habit on d's weekday, before any drift: its own
    weekday window when it has `windows`, else its usual time moved by its weekday shift and its
    overall window (± twice its spread when some weekdays are shifted). None without a usual time."""
    wd = WEEKDAYS[d.weekday()]
    for w in h.get("windows") or []:
        if isinstance(w, dict) and wd in (w.get("days") or []) and clock(w.get("usual")) is not None:
            u = clock(w["usual"])
            lo, hi = clock(w.get("earliest")), clock(w.get("latest"))
            return u, u if lo is None else u + min(_wrap(lo - u), 0.0), u if hi is None else u + max(_wrap(hi - u), 0.0)
    usual = clock(_get(h, "window", "usual"))
    if usual is None:
        return None
    u = usual + _weekday_shift(h, d)
    before, after = _window(h, usual)
    return u, u + before, u + after


def habit_name(h: dict) -> str:
    """The namer's name, or "Habit 3" for habit-3 until it has one."""
    if h.get("name"):
        return str(h["name"])
    hid = str(h.get("id", "habit"))
    n = hid.rsplit("-", 1)[-1]
    return f"Habit {n}" if n.isdigit() else hid


# ---------- one habit ----------

@dataclass
class _Rhythm:
    """Which days a habit is due, and the kind of day each one is (its hit rate is counted per kind)."""
    kind_of: Callable[[date], str | None]  # None: not due that day
    label: Callable[[str], str]  # kind of day → words for the basis ("Mondays")


@dataclass
class _Counts:
    hits: Counter = field(default_factory=Counter)  # kind of day → days it happened
    opportunities: Counter = field(default_factory=Counter)  # kind of day → days it was due
    first: date | None = None


def _habit_slots(h: dict, rs: list[Reading], now: datetime, hours: float, end: date | None,
                 seen_any: set[date] = frozenset(), shifts: dict | None = None) -> list[Slot]:
    rec = h.get("recurrence") or {}
    usual = clock(_get(h, "window", "usual"))
    if usual is None and rs:
        usual = _clock_median([_minute(r.start) for r in rs])
    if usual is None:
        return []
    first = _date(h.get("first_seen")) or (rs[0].day if rs else None)
    if rec.get("kind") == "hourly":
        return _hourly(h, rec, rs, usual, now, hours, first, end)
    anchor = _date(rec.get("anchor")) or (rs[-1].day if rs else None) or _date(h.get("last_seen")) or first
    rhythm = _rhythm(rec, anchor)
    if rhythm is None:
        return []
    lag = _day_lag(rs, usual)
    counts = _count(rhythm, rs, first, end, h.get("opportunities"), seen_any)
    p0 = _overall_rate(h, counts)
    conf = _habit_confidence(h, p0, counts)
    seen = {r.day for r in rs}
    horizon = now + timedelta(hours=hours)

    out = []
    for d in _days(now.date() - timedelta(days=2), horizon.date() + timedelta(days=1)):
        kind = rhythm.kind_of(d)
        if kind is None or d in seen:
            continue  # not due that day, or it already happened
        own = day_window(h, d) if h.get("windows") else None
        if own:  # the day's own weekday window
            weekday, before, after = _wrap(own[0] - usual), own[1] - own[0], own[2] - own[0]
        else:
            weekday, (before, after) = _weekday_shift(h, d), _window(h, usual)
        expected = _nominal(d, lag, usual) + timedelta(minutes=weekday + _drift(h, d))
        basis = None
        shifted = None
        move = (shifts or {}).get((h["id"], expected.date().isoformat()))
        if move:
            oid, minutes = move
            shifted = {"occasion_id": oid, "minutes": minutes,
                       "before": {"expected": hhmm(expected), "window": [hhmm(expected + timedelta(minutes=before)),
                                                                         hhmm(expected + timedelta(minutes=after))]}}
            expected += timedelta(minutes=minutes)
            basis = f"about {abs(minutes)} min {'later' if minutes > 0 else 'earlier'} after {oid}"
        if not now <= expected < horizon:
            continue
        p = _rate(counts, kind, p0)
        if move:  # that weekday's own rate counts the earlier mornings after the occasion, all late, as misses
            p = max(p, p0)
        if p < MIN_PROBABILITY:
            continue
        words = _basis(rhythm, kind, counts, weekday, h)
        out.append(Slot(h, d, expected, expected + timedelta(minutes=before), expected + timedelta(minutes=after),
                        p, clip(p * conf), f"{words}; {basis}" if basis else words, shifted=shifted))
    return out


def _hourly(h, rec, rs, usual, now, hours, first, end) -> list[Slot]:
    """Every n hours: the day's slots repeat at the same clock times, each with its own hit rate
    (Laplace, (hits + 1) / (days + 2): night and day slots differ too much to share a prior)."""
    n = number(rec.get("every_n_hours"))
    if n is None or not 0.5 <= n <= 12:
        return []
    weekdays = set(rec.get("weekdays") or WEEKDAYS)

    def due(d: date) -> bool:
        return WEEKDAYS[d.weekday()] in weekdays

    step = n * 60
    slots = sorted({round((usual + k * step) % DAY_MIN) for k in range(int(DAY_MIN // step))})
    hit_days: dict[int, set[date]] = defaultdict(set)
    offsets: dict[int, list[float]] = defaultdict(list)
    for r in rs:
        t = _minute(r.start)
        s = min(slots, key=lambda s: abs(_wrap(t - s)))
        if abs(_wrap(t - s)) <= step / 2:
            hit_days[s].add(r.start.date())
            offsets[s].append(_wrap(t - s))
    days = [d for d in _days(first, end) if due(d)] if rs and first and end and first <= end else []
    counts = _Counts(first=first)
    for s in slots:
        counts.opportunities[str(s)] = len(days)
        counts.hits[str(s)] = sum(d in hit_days[s] for d in days)
    conf = _habit_confidence(h, _overall_rate(h, counts), counts)
    half = min(step / 2, max(10.0, 2 * (number(_get(h, "variation", "spread_min")) or 7.5)))
    out = []
    for d in _days(now.date() - timedelta(days=1), (now + timedelta(hours=hours)).date()):
        if not due(d):
            continue
        for s in slots:
            recent = offsets[s][-RECENT:]
            expected = datetime.combine(d, time()) + timedelta(minutes=s + (median(recent) if len(recent) >= 2 else 0))
            if not now <= expected < now + timedelta(hours=hours) or d in hit_days[s]:
                continue
            p = _rate(counts, str(s), 0.5)
            if p < MIN_PROBABILITY:
                continue
            basis = (f"Around {hhmm(s)}: {counts.hits[str(s)]} of {counts.opportunities[str(s)]} days"
                     f" since {_day_words(first)}" if counts.opportunities[str(s)] else "No days counted yet")
            out.append(Slot(h, d, expected, expected - timedelta(minutes=half), expected + timedelta(minutes=half),
                            p, clip(p * conf), basis, slot=hhmm(s)))
    return out


def _rhythm(rec: dict, anchor: date | None) -> _Rhythm | None:
    """The habit's `recurrence` as a rule over days (the grouper's recurrence.is_on); None if it
    can't be placed on the calendar."""
    kind = rec.get("kind") or "daily"
    weekdays = {w for w in (rec.get("weekdays") or []) if w in WEEKDAYS} or set(WEEKDAYS)
    text = str(rec.get("text") or "").strip()

    def said(default: str) -> Callable[[str], str]:
        return lambda _kind: (text[:1].upper() + text[1:]) if text else default

    def on(rule: Callable[[date], bool]) -> Callable[[date], str | None]:
        return lambda d: "due" if WEEKDAYS[d.weekday()] in weekdays and rule(d) else None

    if kind not in ("every_n_days", "monthly"):  # daily, weekly (and anything unknown: every day)
        return _Rhythm(lambda d: str(d.weekday()) if WEEKDAYS[d.weekday()] in weekdays else None,
                       lambda k: WEEKDAY_PLURAL[int(k)])
    if kind == "every_n_days":
        n = number(rec.get("every_n_days"))
        if not n or n < 1 or anchor is None:
            return None
        n = int(n)
        if rec.get("weekdays") and n % 7 == 0:  # "every other weekend": those weekdays, every (n/7)th week
            return _Rhythm(on(lambda d: ((_monday(d) - _monday(anchor)).days // 7) % (n // 7) == 0),
                           said(f"Every {n // 7} weeks"))
        return _Rhythm(on(lambda d: (d - anchor).days % n == 0), said(f"Every {n} days"))
    # monthly: a day of the month, or the nth weekday ("the first Sunday of the month")
    dom = number(rec.get("day_of_month"))
    if dom:
        return _Rhythm(on(lambda d: d.day == int(dom)), said(f"Day {int(dom)} of the month"))
    nth, weekday = _nth(text, anchor)
    if nth is None or weekday is None:
        return None
    if not rec.get("weekdays"):  # the weekday is in the sentence ("first Sunday") or the anchor
        weekdays = {weekday}
    if nth == -1:  # "the last Friday": a week later is another month
        return _Rhythm(on(lambda d: (d + timedelta(days=7)).month != d.month), said("Once a month"))
    return _Rhythm(on(lambda d: (d.day - 1) // 7 + 1 == nth), said("Once a month"))


def _nth(text: str, anchor: date | None) -> tuple[int | None, str | None]:
    """Which week of the month, and which weekday: from the sentence ("first Sunday"), else the anchor's."""
    m = re.search(r"\b(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)\s+(mon|tue|wed|thu|fri|sat|sun)",
                  text.lower())
    if m:
        return ORDINALS[m.group(1)], m.group(2)
    return ((anchor.day - 1) // 7 + 1, WEEKDAYS[anchor.weekday()]) if anchor else (None, None)


def _count(rhythm: _Rhythm, rs: list[Reading], first: date | None, end: date | None, opportunities,
           seen_any: set[date] = frozenset()) -> _Counts:
    """Per kind of day: the days it was due and the days it happened, from its first day to the
    end of history. Without readings, which days it happened is unknown: nothing is counted.

    Days the home wasn't watched or was empty (a holiday, an outage) are not misses. The grouper
    knows how many days counted (`opportunities`); when the calendar has more, the days on which
    no habit at all happened go first, and anything still over is shared out in proportion.
    """
    counts = _Counts(first=first)
    if not rs or first is None or end is None or first > end:
        return counts
    seen = {r.day for r in rs}
    due = [(d, kind) for d in _days(first, end) if (kind := rhythm.kind_of(d)) is not None]
    cap = number(opportunities)
    if cap and 0 < cap < len(due):
        empty = [d for d, _ in due if d not in seen_any and d not in seen][:len(due) - int(cap)]
        due = [(d, kind) for d, kind in due if d not in empty]
    for d, kind in due:
        counts.opportunities[kind] += 1
        counts.hits[kind] += d in seen
    total = sum(counts.opportunities.values())
    if cap and 0 < cap < total:
        for k in counts.opportunities:
            counts.opportunities[k] = max(counts.hits[k], counts.opportunities[k] * cap / total)
    return counts


def _overall_rate(h: dict, counts: _Counts) -> float:
    """The habit's overall rate, Laplace-smoothed: (occurrences + 1) / (opportunities + 2)."""
    occ, opp = number(h.get("occurrences")), number(h.get("opportunities"))
    if occ is None or not opp:
        occ, opp = sum(counts.hits.values()), sum(counts.opportunities.values())
    return (min(occ, opp) + 1) / (opp + 2)


def _rate(counts: _Counts, kind: str, p0: float) -> float:
    """Beta estimate for one kind of day: (hits + PRIOR_DAYS·p0) / (due days + PRIOR_DAYS)."""
    return (counts.hits[kind] + PRIOR_DAYS * p0) / (counts.opportunities[kind] + PRIOR_DAYS)


def _habit_confidence(h: dict, p0: float, counts: _Counts) -> float:
    """The grouper's confidence; without one, the overall rate × the evidence factor n / (n + 1)."""
    c = number(h.get("confidence"))
    if c is not None:
        return clip(c)
    n = number(h.get("occurrences"))
    n = sum(counts.hits.values()) if n is None else n
    return clip(p0 * n / (n + 1))


def _weekday_shift(h: dict, d: date) -> float:
    """Minutes later (or earlier) than usual on d's weekday (the grouper's tested `by_weekday`)."""
    v = (_get(h, "variation", "by_weekday") or {}).get(WEEKDAYS[d.weekday()])
    if isinstance(v, str) and clock(v) is not None:  # a usual time for that day, not a shift
        usual = clock(_get(h, "window", "usual"))
        return _wrap(clock(v) - usual) if usual is not None else 0.0
    return number(v) or 0.0


def _drift(h: dict, d: date) -> float:
    """A drifting habit (`trend.minutes_per_week`, the grouper's tested slope) followed from the
    middle of its history, where its usual time sits, to d, at most MAX_DRIFT_MIN."""
    per_week = number(_get(h, "trend", "minutes_per_week"))
    first, last = _date(h.get("first_seen")), _date(h.get("last_seen"))
    if not per_week or not first or not last:
        return 0.0
    middle = first + (last - first) / 2
    return max(-MAX_DRIFT_MIN, min(MAX_DRIFT_MIN, per_week * (d - middle).days / 7))


def _window(h: dict, usual: float) -> tuple[float, float]:
    """The window around the expected time, in minutes (earliest ≤ 0 ≤ latest).

    The habit's own window when no weekday is shifted. When some are, its earliest and latest
    span every day (weekends included), so the window is ± twice its spread instead.
    """
    spread = number(_get(h, "variation", "spread_min"))
    lo, hi = clock(_get(h, "window", "earliest")), clock(_get(h, "window", "latest"))
    if lo is not None and hi is not None and not _get(h, "variation", "by_weekday"):
        return min(_wrap(lo - usual), 0.0), max(_wrap(hi - usual), 0.0)
    half = max(10.0, 2 * spread) if spread is not None else 30.0
    return -half, half


def _basis(rhythm: _Rhythm, kind: str, counts: _Counts, shift: float, h: dict) -> str:
    label, hits, opp = rhythm.label(kind), counts.hits[kind], counts.opportunities[kind]
    if opp:
        out = f"{label}: {hits} of {max(hits, round(opp))} since {_day_words(counts.first)}"
    elif counts.opportunities:
        hits_all, opp_all = sum(counts.hits.values()), round(sum(counts.opportunities.values()))
        out = f"{label}: none counted yet; {hits_all} of {opp_all} days overall since {_day_words(counts.first)}"
    elif number(h.get("opportunities")):
        out = f"{label}: {h.get('occurrences', 0)} of {h['opportunities']} days overall"
    else:
        out = f"{label}: no days counted yet"
    if abs(shift) >= 15:
        out += f", about {round(abs(shift))} min {'later' if shift > 0 else 'earlier'} than usual"
    return out


def _history_end(habits: list[dict], readings: dict[str, list[Reading]], now: datetime) -> date | None:
    """The last day the learners have looked at: the latest day any habit was seen, before today.

    Days after it (the learners haven't run on them yet) are neither hits nor misses.
    """
    today = now.date()
    days = [r.day for rs in readings.values() for r in rs if r.day < today]
    days += [d for h in habits if (d := _date(h.get("last_seen"))) and d < today]
    return max(days) if days else None


def _day_lag(rs: list[Reading], usual: float) -> int:
    """Days between a reading's own day and the date its usual time falls on: 0 for the grouper's
    readings; 1 where a bedtime after midnight is counted for the evening before."""
    votes = Counter(min((-1, 0, 1), key=lambda k: abs(_minutes_between(_nominal(r.day, k, usual), r.start)))
                    for r in rs)
    return votes.most_common(1)[0][0] if votes else 0


def _nominal(day: date, lag: int, usual: float) -> datetime:
    return datetime.combine(day + timedelta(days=lag), time()) + timedelta(minutes=usual)


# ---------- small helpers (anticipate.py uses the public ones) ----------

def as_local(t: datetime) -> datetime:
    """The home's clock time (naive); an aware datetime in any zone is converted."""
    return t.astimezone(TZ).replace(tzinfo=None) if t.tzinfo else t


def number(v) -> float | None:
    """A real number, or None (bools, text and NaN are not)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
        return None
    return float(v)


def clip(x: float) -> float:
    return min(1.0, max(0.0, x))


def parse_moment(v, day: date | None = None) -> datetime | None:
    """A time as the home's clock time: ISO datetime, epoch ms, or "HH:MM" on `day`."""
    if isinstance(v, datetime):
        return as_local(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return to_local(int(v)) if v > 1e11 else None
    if not isinstance(v, str) or not v:
        return None
    try:
        return as_local(datetime.fromisoformat(v))
    except ValueError:
        pass
    m = clock(v)
    return datetime.combine(day, time()) + timedelta(minutes=m) if m is not None and day else None


def clock(v) -> float | None:
    """"07:59" → minutes after midnight; None if it isn't a clock time."""
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", v)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3) or 0) / 60


def hhmm(t) -> str:
    """Minutes after midnight, or a datetime, as "07:59"."""
    m = round(t if isinstance(t, (int, float)) else _minute(t)) % DAY_MIN
    return f"{m // 60:02d}:{m % 60:02d}"


def _get(d: dict, *path):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def _date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _minute(t: datetime) -> float:
    return t.hour * 60 + t.minute + t.second / 60


def _clock_median(minutes: list[float]) -> float | None:
    """Median clock time that respects midnight: measured around the circular mean."""
    if not minutes:
        return None
    a = [m * 2 * math.pi / DAY_MIN for m in minutes]
    centre = (math.atan2(sum(map(math.sin, a)), sum(map(math.cos, a))) * DAY_MIN / (2 * math.pi)) % DAY_MIN
    return (centre + median(_wrap(m - centre) for m in minutes)) % DAY_MIN


def _wrap(minutes: float) -> float:
    """A difference of clock times into [-720, 720)."""
    return (minutes + DAY_MIN / 2) % DAY_MIN - DAY_MIN / 2


def _minutes_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _days(first: date | None, last: date | None):
    d = first
    while d is not None and last is not None and d <= last:
        yield d
        d += timedelta(days=1)


def _day_words(d: date | None) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}" if d else "the start"


def _id_order(hid: str):
    tail = str(hid).rsplit("-", 1)[-1]
    return (0, int(tail), "") if tail.isdigit() else (1, 0, str(hid))


def main() -> None:
    ap = argparse.ArgumentParser(description="What each learned habit will probably do next.")
    ap.add_argument("--db", required=True, help="SQLite file with the `habits` and `habit_readings` tables")
    ap.add_argument("--at", required=True, help="the moment, e.g. 2026-09-21T22:00")
    ap.add_argument("--hours", type=float, default=24)
    args = ap.parse_args()
    db = sqlite3.connect(args.db)
    names = {h["id"]: habit_name(h) for h in active_habits(db)}
    for f in forecast(db, datetime.fromisoformat(args.at), args.hours):
        print(f"{f['date']} {f['expected']} ({f['window'][0]}–{f['window'][1]})  {names.get(f['habit_id'], f['habit_id'])}:"
              f" {f['probability']:.0%}, confidence {f['confidence']:.2f}  [{f['basis']}]")


if __name__ == "__main__":
    main()
