"""When a habit happens: the simplest rhythm that explains its days, and how its time of day varies.

Part A of docs/habits-plan.md (see docs/habits.md). Plain code and statistics, no LLM.

A rhythm says on which of the home's counted days (watched, someone in: run.counted_days) a habit
should happen. Daily is the simplest. A finer rhythm is kept only when it beats its simpler parent
on this home's own days, by an exact test: a habit happens at most once a day, so its days are
drawn without replacement from the parent's days (hypergeometric). Holidays and outages aren't
counted days, so they are never misses.

    daily ─┬─ weekly (single weekdays, else work days / weekend) ─┬─ every 2, 3 or 4 weeks
           │                                                      └─ the nth weekday of the month
           ├─ every n days (2 to 13, whole weeks aside)
           └─ monthly: a day of the month, or the nth weekday of the month

p-values are corrected for the number of candidates tried at each step (ALPHA, as in the learners),
and a finer rhythm needs MIN_OCCURRENCES of its own days, like any habit.

Within the day, `evenly_spaced` finds "every n hours": one thing at several evenly spaced times.
How the time varies: `weekday_shift` (later at weekends), `weekday_windows` (one window per group of
days whose times differ) and `drift` (a few minutes later each week).
"""
import math
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from statistics import median

import numpy as np
from scipy.stats import binom, hypergeom, linregress, mannwhitneyu

from . import ALPHA, MIN_OCCURRENCES
from .habits import WEEKDAY_GROUPS, WEEKDAYS
from .rhythm import DAY_MIN, hhmm

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WORK_DAYS, WEEKEND = ["mon", "tue", "wed", "thu", "fri"], ["sat", "sun"]
WEEK_PERIODS = (2, 3, 4)  # every other week, every third, every fourth
DAY_PERIODS = tuple(n for n in range(2, 14) if n % 7)  # every n days; whole weeks are the weekly branch
NTH = ("first", "second", "third", "fourth")  # a fifth weekday isn't in every month
EVERY = {2: "other", 3: "third", 4: "fourth"}

# A candidate rhythm: its days among the parent's, and how to build it once its first day is known.
Candidate = tuple[list[date], Callable[[date], dict]]


# ---------- the rhythm ----------

def rhythm(kind: str, weekdays: list[str] | None = None, every_n_days: int | None = None,
           anchor: date | None = None, day_of_month: int | None = None, every_n_hours: float | None = None,
           hours: tuple[float, float] | None = None) -> dict:
    """The agreed `recurrence` shape (docs/habits-plan.md), with its plain sentence.

    `anchor` is a day it happened on, which fixes the phase of "every other weekend" and the n of
    "the first Sunday of the month". `hours` (first, last clock minute) only feeds the sentence of
    an every-n-hours habit.
    """
    rec = {"kind": kind, "weekdays": weekdays, "every_n_days": every_n_days,
           "anchor": anchor.isoformat() if anchor else None, "day_of_month": day_of_month,
           "every_n_hours": every_n_hours, "text": ""}
    rec["text"] = describe(rec, hours)
    return rec


def find_recurrence(seen: Iterable[date], counted: Iterable[date]) -> dict:
    """The simplest rhythm that explains the days `seen`, among `counted`: the days the home was
    watched with someone in, since the habit began. Each step down the tree above must beat chance."""
    counted = sorted(set(counted))
    seen = set(seen) & set(counted)
    if not counted:
        return rhythm("daily")
    weekly = _weekly(seen, counted)
    if weekly is None:
        return _finer(seen, counted, _every_n_days(counted) + _day_of_month(counted)
                      + _nth_weekday(counted, range(7))) or rhythm("daily")
    parent = on_days(weekly, counted)
    candidates = _every_n_weeks(parent, weekly["weekdays"])
    if len(weekly["weekdays"]) == 1:
        candidates += _nth_weekday(parent, [WEEKDAYS.index(weekly["weekdays"][0])])
    return _finer(seen, parent, candidates) or weekly


def hourly_rhythm(series: tuple[float, float, float, float], seen: Iterable[date], counted: Iterable[date]) -> dict:
    """An every-n-hours habit (`series` from evenly_spaced), on every day or on some weekdays."""
    every_min, first, last, _ = series
    counted = sorted(set(counted))
    weekly = _weekly(set(seen) & set(counted), counted) if counted else None
    every = int(every_min // 60) if every_min % 60 == 0 else round(every_min / 60, 2)
    return rhythm("hourly", weekly["weekdays"] if weekly else None, every_n_hours=every, hours=(first, last))


def is_on(rec: dict, day: date) -> bool:
    """Whether the rhythm says the habit should happen on `day`."""
    weekdays = rec.get("weekdays")
    if weekdays and WEEKDAYS[day.weekday()] not in weekdays:
        return False
    anchor = date.fromisoformat(rec["anchor"]) if rec.get("anchor") else None
    if rec["kind"] == "every_n_days" and anchor:
        n = rec["every_n_days"]
        if weekdays:  # every n weeks, on these weekdays
            return (_monday(day) - _monday(anchor)).days // 7 % (n // 7) == 0
        return (day - anchor).days % n == 0
    if rec["kind"] == "monthly":
        if rec.get("day_of_month"):
            return day.day == rec["day_of_month"]
        if anchor:  # the nth weekday: the same week of the month as the anchor
            return (day.day - 1) // 7 == (anchor.day - 1) // 7
    return True


def on_days(rec: dict, days: Iterable[date]) -> list[date]:
    return [d for d in days if is_on(rec, d)]


def describe(rec: dict, hours: tuple[float, float] | None = None) -> str:
    """The plain sentence the UI shows: "every weekday", "every other Saturday", "on the first
    Sunday of the month", "every 3 hours from 08:00 to 20:00"."""
    kind, weekdays = rec["kind"], rec.get("weekdays") or []
    if kind == "hourly":
        n = rec["every_n_hours"]
        every = "every hour" if n == 1 else f"every {int(n)} hours" if n == int(n) else f"every {round(n * 60)} minutes"
        text = f"{every} from {hhmm(hours[0])} to {hhmm(hours[1])}" if hours else every
        return f"{text}, {_weekly_text(weekdays)}" if weekdays else text
    if kind == "every_n_days":
        n = rec["every_n_days"]
        if weekdays:
            weeks = n // 7
            return f"every {EVERY.get(weeks, f'{weeks}th')} {_week_noun(weekdays)}"
        return "every other day" if n == 2 else f"every {n} days"
    if kind == "monthly":
        if rec.get("day_of_month"):
            return f"on the {_ordinal(rec['day_of_month'])} of the month"
        nth = NTH[(date.fromisoformat(rec["anchor"]).day - 1) // 7]
        return f"on the {nth} {DAY_NAMES[WEEKDAYS.index(weekdays[0])]} of the month"
    if kind == "weekly":
        return _weekly_text(weekdays)
    return "every day"


# ---------- the tests ----------

def _concentrated(seen: set[date], parent: list[date], on: list[date]) -> tuple[float, int]:
    """How unlikely it is that this many of its days fall on `on` if its days were any of the
    parent's (hypergeometric upper tail), and how many did."""
    x = len(seen.intersection(on))
    k = len(seen.intersection(parent))
    if x == 0 or len(on) >= len(parent):
        return 1.0, x
    return float(hypergeom.sf(x - 1, len(parent), k, len(on))), x


def _weekly(seen: set[date], counted: list[date]) -> dict | None:
    """Single weekdays, or else work days / weekend (the habit learner's groups, habits.WEEKDAY_GROUPS),
    holding more of its days than their share of the counted days allows.

    The habit learner tests where its occurrences fall (binomial); this test also counts the misses
    on the other days, which three weeks of a weekday habit need to beat chance.
    """
    tries = len(WEEKDAY_GROUPS)

    def on(group) -> list[date]:
        return [d for d in counted if d.weekday() in group]

    def significant(group) -> bool:
        p, _ = _concentrated(seen, counted, on(group))
        return p * tries < ALPHA

    singles = [g[0] for g in WEEKDAY_GROUPS if len(g) == 1 and significant(g)]
    choices = ([singles] if singles else []) + [_without_absent(seen, counted, list(g))
                                                 for g in WEEKDAY_GROUPS if len(g) > 1 and significant(g)]
    for days in choices:
        if len(seen.intersection(on(days))) >= MIN_OCCURRENCES:
            return rhythm("weekly", [WEEKDAYS[d] for d in sorted(days)])
    return None


def _without_absent(seen: set[date], counted: list[date], group: list[int]) -> list[int]:
    """The group's weekdays without those it is missing on far more than its rate on the others allows (binomial
    lower tail, corrected for the weekdays tried): João comes home on weekdays, but never on Wednesdays."""
    out = list(group)
    for wd in group:
        mine = [d for d in counted if d.weekday() == wd]
        rest = [d for d in counted if d.weekday() in out and d.weekday() != wd]
        if not mine or not rest:
            continue
        rate = (len(seen.intersection(rest)) + 1) / (len(rest) + 2)  # rule of succession: one miss isn't news
        if binom.cdf(len(seen.intersection(mine)), len(mine), rate) * len(group) < ALPHA:
            out.remove(wd)
    return out


def _finer(seen: set[date], parent: list[date], candidates: list[Candidate]) -> dict | None:
    """The candidate whose days hold its occurrences most surely beyond chance (corrected for all
    the candidates tried), or None."""
    best = None
    for on, make in candidates:
        p, x = _concentrated(seen, parent, on)
        if x >= MIN_OCCURRENCES and p * len(candidates) < ALPHA and (best is None or p < best[0]):
            best = (p, on, make)
    if best is None:
        return None
    _, on, make = best
    return make(min(seen.intersection(on)))  # anchored on the first day it happened on that rhythm


def _every_n_weeks(parent: list[date], weekdays: list[str]) -> list[Candidate]:
    """Every 2, 3 or 4 weeks on the weekly rhythm's weekdays, each phase a candidate."""
    week0 = _monday(parent[0])
    out = []
    for n in WEEK_PERIODS:
        for phase in range(n):
            on = [d for d in parent if (_monday(d) - week0).days // 7 % n == phase]
            out.append((on, lambda a, n=n: rhythm("every_n_days", weekdays, every_n_days=7 * n, anchor=a)))
    return out


def _every_n_days(counted: list[date]) -> list[Candidate]:
    out = []
    for n in DAY_PERIODS:
        for phase in range(n):
            on = [d for d in counted if (d - counted[0]).days % n == phase]
            out.append((on, lambda a, n=n: rhythm("every_n_days", every_n_days=n, anchor=a)))
    return out


def _day_of_month(counted: list[date]) -> list[Candidate]:
    return [([d for d in counted if d.day == dom], lambda a, dom=dom: rhythm("monthly", day_of_month=dom))
            for dom in range(1, 32)]


def _nth_weekday(parent: list[date], weekdays: Iterable[int]) -> list[Candidate]:
    """The first to fourth of a weekday in the month (the first Sunday)."""
    out = []
    for wd in weekdays:
        for nth in range(len(NTH)):
            on = [d for d in parent if d.weekday() == wd and (d.day - 1) // 7 == nth]
            out.append((on, lambda a, wd=wd: rhythm("monthly", [WEEKDAYS[wd]], anchor=a)))
    return out


# ---------- every n hours ----------

def evenly_spaced(times: list[float]) -> tuple[float, float, float, float] | None:
    """Clock times (minutes) of one thing's patterns at several times of day → (every_min, first,
    last, p) when they are evenly spaced, else None. The caller corrects p for its tries.

    The series runs from the time after its longest gap (the night) round to the time before it.
    Chance: given the first and last, the m-2 others could fall anywhere between them; all of them
    land as close to the even slots as they did (within the largest miss, `dev`) with probability
    (m-2)! (2·dev / span)^(m-2). Times are known to the minute, so `dev` is at least 1.
    `every_min` is a whole number of hours when such a grid (placed where it fits best) misses no
    slot by more than `dev`: then "every 3 hours" says it as well as "every 178 minutes".
    """
    m = len(times)
    if m < 3:
        return None
    t = sorted(x % DAY_MIN for x in times)
    gaps = [(t[(i + 1) % m] - t[i]) % DAY_MIN for i in range(m)]
    first = t[(int(np.argmax(gaps)) + 1) % m]
    series = sorted((x - first) % DAY_MIN for x in t)
    span = series[-1]
    if span <= 0:
        return None
    step = span / (m - 1)
    dev = max(1.0, max(abs(series[j] - j * step) for j in range(1, m - 1)))
    p = min(1.0, math.factorial(m - 2) * (2 * dev / span) ** (m - 2))
    hours = 60 * round(step / 60)
    misses = [series[j] - j * hours for j in range(m)]
    if hours and (max(misses) - min(misses)) / 2 <= dev:
        step = hours
    return step, first, (first + span) % DAY_MIN, p


# ---------- how the time varies ----------

def weekday_shift(days: list[date], minutes: list[float]) -> tuple[float, dict[str, int], list[float]]:
    """(usual, by_weekday, residuals) of a habit's start times (minutes, unwrapped: no midnight jump).

    Some days can start later or earlier (the weekend lie-in): single days, or else work days /
    weekend, whose times differ from the other days' more than chance allows (Mann-Whitney,
    corrected for the groups tried). `usual` is the median on the other days; `by_weekday` the
    shift of each shifted day; `residuals` what is left of each time once its shift is taken out.
    """
    shifted = _shifted_days(days, minutes)
    base = [m for d, m in zip(days, minutes) if d.weekday() not in shifted] or list(minutes)
    usual = float(median(base))
    by_weekday = {}
    for wd in sorted(shifted):
        on_day = [m for d, m in zip(days, minutes) if d.weekday() == wd]
        if on_day:
            by_weekday[WEEKDAYS[wd]] = round(median(on_day) - usual)
    residuals = [m - usual - by_weekday.get(WEEKDAYS[d.weekday()], 0) for d, m in zip(days, minutes)]
    return usual, by_weekday, residuals


def weekday_windows(days: list[date], minutes: list[float], weekdays: list[str] | None = None) -> list[dict]:
    """One window per group of days whose times differ (docs/habits.md, "Windows by weekday"):
    [{"days": ["mon", ...], "usual", "earliest", "latest"}], in weekday order.

    The shifted days are weekday_shift's. Shifted days whose times don't differ from each other
    (Mann-Whitney, as above) share a window: Saturday and Sunday are one weekend. The other days of
    the rhythm (`weekdays`, all seven when None) are one window. With no shift: one window, all days.
    `usual` is the median, `earliest` and `latest` the extremes of the group's times.
    """
    if not days:
        return []
    allowed = [WEEKDAYS.index(w) for w in weekdays] if weekdays else list(range(7))
    shifted = _shifted_days(days, minutes)
    times = lambda group: [m for d, m in zip(days, minutes) if d.weekday() in group]  # noqa: E731
    groups: list[list[int]] = []
    for wd in sorted(shifted):
        if not times([wd]):
            continue
        same = next((g for g in groups if not _differ(times([wd]), times(g))), None)
        if same is None:
            groups.append([wd])
        else:
            same.append(wd)
    base = [wd for wd in allowed if wd not in shifted]
    if base and times(base) or not groups:
        groups.append(base or allowed)
    out = []
    for g in sorted(groups, key=min):
        ms = times(g) or list(minutes)
        out.append({"days": [WEEKDAYS[wd] for wd in sorted(g)], "usual": hhmm(median(ms)),
                    "earliest": hhmm(min(ms)), "latest": hhmm(max(ms))})
    return out


def _shifted_days(days: list[date], minutes: list[float]) -> set[int]:
    """The weekdays (0 = Monday) whose times differ from the other days' beyond chance: single days,
    else work days / weekend; at most three (the few that differ, against the many that don't)."""
    tries = len(WEEKDAY_GROUPS)

    def significant(group) -> bool:
        inside = [m for d, m in zip(days, minutes) if d.weekday() in group]
        outside = [m for d, m in zip(days, minutes) if d.weekday() not in group]
        return bool(inside and outside) and _differ(inside, outside, tries)

    shifted = {g[0] for g in WEEKDAY_GROUPS if len(g) == 1 and significant(g)}
    while shifted:  # a day the shifted ones hid (Sunday beside Saturday): against the days left
        more = {wd for wd in set(range(7)) - shifted
                if _differ([m for d, m in zip(days, minutes) if d.weekday() == wd],
                           [m for d, m in zip(days, minutes) if d.weekday() not in shifted | {wd}], tries)}
        if not more:
            break
        shifted |= more
    if not shifted:
        shifted = next((set(g) for g in WEEKDAY_GROUPS if len(g) > 1 and significant(g)), set())
    if len(shifted) > 3:  # report the few days that differ, against the many that don't
        shifted = set(range(7)) - shifted
    return shifted


def _differ(a: list[float], b: list[float], tries: int = len(WEEKDAY_GROUPS)) -> bool:
    """Two sets of times differ beyond chance (Mann-Whitney U, corrected for `tries`)."""
    if not a or not b:
        return False
    return bool(mannwhitneyu(a, b, alternative="two-sided").pvalue * tries < ALPHA)


def drift(days: list[date], minutes: list[float], tries: int = 1) -> int:
    """Minutes per week the habit moves (later > 0) when the slope beats chance (corrected for
    `tries`, the habits tested), else 0. `minutes`: residuals from weekday_shift."""
    if len(days) < MIN_OCCURRENCES or len({d for d in days}) < 3:
        return 0
    fit = linregress([d.toordinal() for d in days], minutes)
    if not fit.pvalue * tries < ALPHA:  # also false for a nan p-value (all times equal)
        return 0
    return round(fit.slope * 7)


# ---------- words ----------

def _weekly_text(weekdays: list[str]) -> str:
    if weekdays == WORK_DAYS:
        return "every weekday"
    if weekdays == WEEKEND:
        return "every weekend"
    return f"every {_join([DAY_NAMES[WEEKDAYS.index(d)] for d in weekdays])}"


def _week_noun(weekdays: list[str]) -> str:
    """What "every other …" is about: "weekend", "Saturday", "week on Monday and Thursday"."""
    if weekdays == WEEKEND:
        return "weekend"
    if weekdays == WORK_DAYS:
        return "week, on weekdays"
    if len(weekdays) == 1:
        return DAY_NAMES[WEEKDAYS.index(weekdays[0])]
    return f"week on {_join([DAY_NAMES[WEEKDAYS.index(d)] for d in weekdays])}"


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())
