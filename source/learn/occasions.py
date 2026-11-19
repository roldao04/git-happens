"""Part C, occasions: rare departures from the usual day, recognised by resemblance (docs/occasions.md).

Plain code and statistics, no LLM. Every cut-off is measured against this home's own history; the
only fixed numbers are the knobs in source/learn/__init__.py (ALPHA, MIN_OCCURRENCES).

1. **The usual day.** Each day is cut at the home's quietest hour, so an evening that runs past
   midnight stays in one day. It is measured minute by minute: door openings, light-on minutes, plug
   uses by kind, camera events, and the minutes the home was awake (from the end of one night's
   quiet to the start of the next: how late the night's quiet starts). Only counted days (watched,
   someone in: run.counted_days) are usual, and only where they held no earlier episode.
2. **Episodes.** Every stretch of whole hours of a finished day is compared with the same stretch
   on the usual days. A count's chance is a Poisson rate with Jeffreys' prior (a negative binomial
   predictive); minutes are ranked (conformal). Fisher's method adds up the features. A stretch is
   an episode when at least two features are beyond the home's history (chance at most 1/(n+1))
   and the combined chance, times the 300 stretches tried, is below ALPHA. End hours that are
   ordinary on their own are then dropped.
3. **Occasions.** A new episode joins the occasion it resembles most, unless it is beyond chance
   different from every one: then it starts a new occasion. Resemblance compares when it started
   and ended, when the night's quiet began, and how many doors, light-hours and plug uses it held.
   confidence = n/(n+1) (one example: at most 0.5) x how tight the group is. `explains`: the
   learned patterns it moves (bedtime late), as "habit-N late" once part A's habits exist.
   Guests' phones (arrivals of phones that aren't the household's) are a count like door openings,
   and the fingerprint keeps `guest_phones`, the most connected at once.
4. **Live.** match_now(db, now): when a stretch of today so far is unusual (on one feature, or
   several), it is compared from where it began up to now with each occasion's episodes over the
   same clock hours. confidence = similarity x the occasion's confidence. `shifts`: how the
   occasion moved habits the next day in its past episodes (median), e.g. waking up 90 min later;
   shifts_now(db, now) gives them for the occasion under way and the one the night before.

  uv run python -m source.learn.occasions --db data/sim/rosa/home.sqlite3 --learn   # replay every day
  uv run python -m source.learn.occasions --db data/sim/rosa/home.sqlite3 --at 2026-09-21T22:30
"""
import argparse
import copy
import json
import logging
import math
import sqlite3
from bisect import bisect_left
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np
from scipy.stats import chi2, nbinom, norm

from . import (ALPHA, MIN_OCCURRENCES, Happening, activity_edges, device_type, is_activity, signal_devices, to_local,
               to_ms)
from .forecast import day_window
from . import storage
from .habits import HOLE_MIN, QUIET, WEEKDAYS, quiet_happenings
from .recurrence import is_on
from .rhythm import DAY_MIN, Watched, circular_mean_std, hhmm

log = logging.getLogger("source.learn.occasions")

SCHEMA = """
CREATE TABLE IF NOT EXISTS occasions (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS occasion_episodes (
    occasion_id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, features TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS occasion_episodes_occasion ON occasion_episodes (occasion_id, start);
"""

DOOR, LIGHT, AWAKE, GUESTS = "door", "light", "awake", "guests"
# Every stretch of whole hours inside one day: 300 of them, the number of tries for one day.
STRETCHES = [(a * 60, (a + n) * 60) for a in range(24) for n in range(1, 25 - a)]
JEFFREYS = 0.5  # Jeffreys' prior on a Poisson rate: a count never seen at that hour still has a finite chance
CLOCKS = ("start", "end", "quiet")  # in hours; the usual day is judged in whole hours, so one hour is one unit
AMOUNTS = ("door", "light", "plug", "camera")  # counts (light in hours), with Poisson noise sqrt(x + y + 1)
SHIFT_BEFORE_H, SHIFT_AFTER_H = 3, 6  # a habit the next day is looked for this long before and after its usual time


# ---------- the usual day ----------

@dataclass
class History:
    """Every day of the history, minute by minute, cut at the home's quietest hour."""
    first: date  # day 0
    hour: int  # the hour the home's day starts at
    features: list[str]  # "door", "light", "plug:<kind>", "camera:<capability>", "awake"
    counts: list[bool]  # per feature: a count (door openings) or minutes (light, awake)
    cum: np.ndarray  # (days, 1441, features): running totals over the minutes of each day
    blind: np.ndarray  # (days, 1441): running count of minutes not watched (a hole in the polls)
    held: np.ndarray  # (days, 1441): running count of minutes inside episodes already stored
    counted: np.ndarray  # (days,): watched and someone in (run.counted_days)
    weekend: np.ndarray  # (days,)
    nights: list[tuple[datetime | None, datetime | None]]  # each day's night quiet: start, end
    happenings: list[Happening]  # people's activity, ends clipped at the moment it was read
    guests: frozenset[str] = frozenset()  # the signals of guests' phones ("phone:c-91d0.connected")
    guest_counts: list[tuple[datetime, datetime, float]] = ()  # router.guestPhones: start, end, most at once

    @property
    def n(self) -> int:
        return len(self.counted)

    def day(self, j: int) -> date:
        return self.first + timedelta(days=j)

    def start(self, j: int) -> datetime:
        return datetime.combine(self.day(j), time(self.hour))

    def index(self, t: datetime) -> int:
        """The day a moment belongs to (an evening's 01:00 belongs to the evening)."""
        return ((t - timedelta(hours=self.hour)).date() - self.first).days

    def minute(self, j: int, t: datetime) -> float:
        return (t - self.start(j)).total_seconds() / 60

    def amounts(self, j: int, a: int, b: int) -> dict:
        """Door openings, light-hours, plug uses and camera events in minutes [a, b) of day j."""
        v = self.cum[j, b] - self.cum[j, a]
        pick = lambda test: float(sum(v[i] for i, f in enumerate(self.features) if test(f)))
        return {"door": pick(lambda f: f == DOOR), "light": pick(lambda f: f == LIGHT) / 60,
                "plug": pick(lambda f: f.startswith("plug:")), "camera": pick(lambda f: f.startswith("camera:"))}

    @property
    def amount_keys(self) -> tuple[str, ...]:
        return AMOUNTS if any(f.startswith("camera:") for f in self.features) else AMOUNTS[:-1]


def _feature(h: Happening, guests: frozenset[str]) -> str | None:
    """feature(), plus the arrivals of guests' phones (a count, like door openings)."""
    return GUESTS if h.signal in guests else feature(h)


def guest_phones(db: sqlite3.Connection, hs: list[Happening]) -> frozenset[str]:
    """The `phone:<id>.connected` signals of phones that aren't the household's. Known phones have a
    name in the `devices` table (C1, C2); a phone the table doesn't name is a guest's, and so is one
    it doesn't list that came on fewer than MIN_OCCURRENCES days (hand-made data)."""
    names = dict(db.execute("SELECT device_id, name FROM devices")) if _has(db, "devices") else {}
    days: dict[str, set[date]] = {}
    listed: dict[str, bool] = {}
    for h in hs:
        if device_type(h.signal) == "phone" and h.capability == "connected":
            days.setdefault(h.signal, set()).add(h.start.date())
            if h.device_id in names:
                listed[h.signal] = bool(names[h.device_id])
    return frozenset(sig for sig, ds in days.items()
                     if (not listed[sig] if sig in listed else len(ds) < MIN_OCCURRENCES))


def feature(h: Happening) -> str | None:
    """Which usual-day feature a happening feeds, or None (the room's slow signals, settings, the hub)."""
    if h.device == "contactSensor":
        return DOOR
    if h.device == "bulb" and h.capability == "power":
        return LIGHT if h.value == "on" else None
    if h.device == "plug" and h.capability == "instantPower":
        return f"plug:{h.kind or '?'}"
    if h.device == "camera":
        return f"camera:{h.capability}"
    return None


def history(db: sqlite3.Connection, until: datetime, episodes: list["Row"] = (), live: bool = False) -> History | None:
    """Everything the usual day needs from what happened before `until`, or None with no activity.

    `episodes` are masked out of the usual day. `live`: also count on/off signals that are away from
    rest right now (a happening is only stored once it ends).
    """
    hs = _load_happenings(db, until)
    if live:
        hs += storage.ongoing_happenings(db, until, hs)
    activity = sorted((h for h in hs if is_activity(h)), key=lambda h: h.start)
    if not activity:
        return None
    polls = _load_polls(db, until)
    watched = Watched(polls, HOLE_MIN)
    hour = quietest_hour(activity)
    first = (activity[0].start - timedelta(hours=hour)).date()
    last = (until - timedelta(hours=hour, microseconds=1)).date()
    n = (last - first).days + 1
    origin = datetime.combine(first, time(hour))
    total = n * DAY_MIN

    def at(t: datetime) -> float:
        return min(max((t - origin).total_seconds() / 60, 0.0), float(total))

    guests = guest_phones(db, activity)
    features = sorted({f for h in activity if (f := _feature(h, guests))}, key=_feature_order) + [AWAKE]
    col = {f: i for i, f in enumerate(features)}
    counts = np.zeros((total + 1, len(features)))
    edges = np.zeros((total + 1, len(features)))  # +1 / -1 where a stretch of minutes starts / ends
    for h in activity:
        f = _feature(h, guests)
        if f == LIGHT:
            _span(edges[:, col[f]], at(h.start), at(h.end))
        elif f is not None and origin <= h.start < origin + timedelta(minutes=total):
            counts[int(at(h.start)), col[f]] += 1

    moments = activity_edges(activity)  # a phone at home all night is only its edges: the night stays quiet
    quiets = quiet_happenings(moments, watched)  # in start order
    q_starts, a_starts = [q.start for q in quiets], [h.start for h in activity]
    nights = []
    for j in range(n):  # tonight's quiet: the longest one starting in the second half of the day
        lo, hi = origin + timedelta(days=j, hours=12), origin + timedelta(days=j + 1)
        best = max(quiets[bisect_left(q_starts, lo):bisect_left(q_starts, hi)],
                   key=lambda q: q.end - q.start, default=None)
        nights.append((best.start, best.end) if best else (None, None))
    for j in range(n):  # awake: from the end of the last night's quiet to the start of tonight's
        s, e = origin + timedelta(days=j), origin + timedelta(days=j + 1)
        mine = activity[bisect_left(a_starts, s):bisect_left(a_starts, e)]
        woke = nights[j - 1][1] if j else None
        wake = max(s, woke) if woke is not None and woke < e else (mine[0].start if mine else e)
        bed = nights[j][0]
        if bed is None:  # up all night, or (today) not asleep yet: awake until the last activity
            bed = e if j < n - 1 else max((min(h.end, until) for h in mine), default=wake)
        if bed > wake:
            _span(edges[:, col[AWAKE]], at(wake), at(min(bed, e)))
    values = counts[:total] + np.cumsum(edges, axis=0)[:total]
    cum = np.concatenate([np.zeros((n, 1, len(features))),
                          np.cumsum(values.reshape(n, DAY_MIN, len(features)), axis=1)], axis=1)

    blind = np.zeros(total + 1)
    if polls is not None:
        p = [at(t) for t in polls]
        _span(blind, 0, p[0])
        for x, y in zip(p, p[1:]):
            if y - x > HOLE_MIN:
                _span(blind, x, y)
        _span(blind, p[-1] + HOLE_MIN, total)
    _span(blind, at(until), total)  # the future
    held = np.zeros(total + 1)
    for r in episodes:
        _span(held, at(r.start), at(r.end))

    from .run import counted_days  # here, not at the top: run.py imports this module to run it daily
    counted = set(counted_days(moments, activity[0].start.date(), last, watched))
    days = [first + timedelta(days=j) for j in range(n)]
    return History(first, hour, features, [f not in (LIGHT, AWAKE) for f in features], cum,
                   _running(blind, n), _running(held, n), np.array([d in counted for d in days]),
                   np.array([d.weekday() >= 5 for d in days]), nights, activity, guests,
                   _guest_counts(db, until))


def _guest_counts(db: sqlite3.Connection, until: datetime) -> list[tuple[datetime, datetime, float]]:
    """router.guestPhones above rest: (start, end, the most at once), from its happenings' peaks."""
    if not _has(db, "happenings"):
        return []
    rows = db.execute("SELECT signal, start_ms, end_ms, COALESCE(peak, size) FROM happenings"
                      " WHERE capability = 'guestPhones' AND start_ms < ?", (to_ms(until),)).fetchall()
    return [(to_local(s), to_local(e), float(v)) for sig, s, e, v in rows
            if device_type(sig) == "router" and v is not None]


def quietest_hour(activity: list[Happening]) -> int:
    """The middle of the longest run of the hours with the fewest starts and ends of activity.

    The home's day starts there, so a party that goes on past midnight is one evening, not two days.
    """
    moments = np.zeros(24)
    for h in activity:
        moments[h.start.hour] += 1
        moments[h.end.hour] += 1
    low = moments == moments.min()
    best, length = int(np.argmin(moments)), 0
    for s in range(24):
        if low[s] and not low[s - 1]:
            run = next((k for k in range(1, 25) if not low[(s + k) % 24]), 24)
            if run > length:
                best, length = (s + run // 2) % 24, run
    return best


def _feature_order(f: str) -> tuple[int, str]:
    return ([DOOR, LIGHT].index(f) if f in (DOOR, LIGHT) else 2 if f.startswith("plug:") else 3 if f == GUESTS else 4, f)


def _span(edges: np.ndarray, a: float, b: float) -> None:
    a, b = int(round(a)), int(round(b))
    if b > a:
        edges[a] += 1
        edges[b] -= 1


def _running(edges: np.ndarray, n: int) -> np.ndarray:
    per_minute = np.cumsum(edges)[:n * DAY_MIN].reshape(n, DAY_MIN) > 0
    return np.concatenate([np.zeros((n, 1)), np.cumsum(per_minute, axis=1)], axis=1)


# ---------- judging a stretch against the usual day ----------

@dataclass
class Judged:
    p: np.ndarray  # (stretches, features): chance of at least this much on a usual day
    n: np.ndarray  # (stretches, features): usual days it was compared with
    fisher: np.ndarray  # (stretches,): the features' chances combined (Fisher's method)
    beyond: np.ndarray  # (stretches, features): more than the home's history (chance <= 1/(n+1))
    ok: np.ndarray  # (stretches,): the day was watched all through the stretch
    usual: np.ndarray  # (stretches,): usual days with the whole stretch watched and free


def judge(h: History, d: int, windows: list[tuple[int, int]]) -> Judged:
    """Each window [a, b) (minutes from the start of day d) against the same window on the usual days.

    Usual days: counted, earlier than d, watched all through the window, no stored episode in it. A
    count's chance comes from a Poisson rate with Jeffreys' prior, given the usual days' total
    (negative binomial predictive), so 10 door openings where 0.3 are usual is very unlikely. Minutes
    (light, awake) are ranked: (1 + usual days with at least as much) / (n + 1).
    """
    a = np.fromiter((w[0] for w in windows), int, len(windows))
    b = np.fromiter((w[1] for w in windows), int, len(windows))
    vals = h.cum[:, b, :] - h.cum[:, a, :]  # (days, stretches, features)
    blind, held = h.blind[:, b] - h.blind[:, a], h.held[:, b] - h.held[:, a]
    usual = (np.arange(h.n) < d)[:, None] & h.counted[:, None] & (blind == 0) & (held == 0)
    use = usual[:, :, None] & _kind_of_day(h, d, vals, usual)
    n = use.sum(axis=0)
    x = vals[d]
    p = np.ones(x.shape)
    for f, is_count in enumerate(h.counts):
        live = (x[:, f] > 0) & (n[:, f] > 0)
        if not live.any():
            continue
        nf = n[live, f]
        if is_count:
            total = (vals[:, :, f] * use[:, :, f]).sum(axis=0)[live]
            p[live, f] = nbinom.sf(np.rint(x[live, f]) - 1, total + JEFFREYS, nf / (nf + 1))
        else:
            at_least = ((vals[:, :, f] >= x[None, :, f]) & use[:, :, f]).sum(axis=0)[live]
            p[live, f] = (1 + at_least) / (nf + 1)
    p = np.clip(p, 1e-300, 1.0)
    fisher = chi2.sf(-2 * np.log(p).sum(axis=1), 2 * len(h.features))
    beyond = (x > 0) & (n > 0) & (p <= 1 / (n + 1))
    return Judged(p, n, fisher, beyond, (blind[d] == 0) & bool(h.counted[d]), usual.sum(axis=0))


def _kind_of_day(h: History, d: int, vals: np.ndarray, usual: np.ndarray) -> np.ndarray:
    """(days, stretches, features): the usual days to compare with. Weekdays and weekends are pooled,
    unless they differ there (Mann-Whitney U at ALPHA) and day d's kind has MIN_OCCURRENCES days."""
    days, stretches, features = vals.shape
    out = np.ones(vals.shape, bool)
    weekend, weekday = usual & h.weekend[:, None], usual & ~h.weekend[:, None]
    n1, n2 = weekend.sum(axis=0), weekday.sum(axis=0)
    can = ((n1 if h.weekend[d] else n2) >= MIN_OCCURRENCES) & (n1 > 0) & (n2 > 0)
    if not can.any():
        return out
    pairs = weekend[:, None, :] & weekday[None, :, :]  # (a weekend day, a weekday) per stretch
    sd = np.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    same = (h.weekend == h.weekend[d])[:, None]
    for f in range(features):
        v = vals[:, :, f]
        u = (((v[:, None, :] > v[None, :, :]) & pairs).sum(axis=(0, 1))
             + 0.5 * ((v[:, None, :] == v[None, :, :]) & pairs).sum(axis=(0, 1)))
        z = np.divide(u - n1 * n2 / 2, sd, out=np.zeros(stretches), where=sd > 0)
        out[:, can & (2 * norm.sf(np.abs(z)) < ALPHA), f] = same
    return out


# ---------- episodes ----------

@dataclass
class Row:
    """A stored episode."""
    rowid: int | None
    occasion_id: str
    start: datetime
    end: datetime
    features: dict

    @property
    def day(self) -> date:
        return date.fromisoformat(self.features["day"])


def find_episodes(h: History, d: int) -> list[Row]:
    """The unusual stretches of day d, most unusual first (not yet given to an occasion)."""
    if not 0 <= d < h.n or not h.counted[d]:
        return []
    found: list[Row] = []
    taken = np.zeros(DAY_MIN, bool)
    alone = None
    while True:
        windows = [(a, b) for a, b in STRETCHES if not taken[a:b].any()]
        if not windows:
            break
        j = judge(h, d, windows)
        enough = j.ok & (j.usual >= MIN_OCCURRENCES) & (j.beyond.sum(axis=1) >= 2)
        if not enough.any():
            break
        order = np.lexsort(([b - a for a, b in windows], j.fisher))  # most unusual; the shorter on a tie
        best = next(int(i) for i in order if enough[i])
        if j.fisher[best] * len(STRETCHES) >= ALPHA:
            break
        a, b = windows[best]
        if alone is None:  # each hour of the day on its own: its least likely feature
            alone = judge(h, d, [(k * 60, k * 60 + 60) for k in range(24)]).p.min(axis=1)
        i = _narrow(windows, j.fisher, best, alone)
        found.append(_episode(h, d, *windows[i], j, i))
        taken[a:b] = True
    return found


def _narrow(windows: list[tuple[int, int]], fisher: np.ndarray, best: int, alone: np.ndarray) -> int:
    """Drop end hours that are ordinary on their own, while the stretch stays nearly as unusual.

    The most unusual stretch tends to reach out to ordinary activity next to it (a door at 15:32
    before an evening party). An end hour is dropped when none of its features has a chance below
    ALPHA in that hour alone (`alone`), and only while the stretch's chance stays within a factor
    1/ALPHA of the most unusual one's (so evidence spread thinly over the night is not narrowed
    away). Returns the index of the narrowed window.
    """
    where = {w: i for i, w in enumerate(windows)}
    i = best
    while windows[i][1] - windows[i][0] > 60:
        a, b = windows[i]
        shorter = [where[w] for w, edge in (((a + 60, b), a), ((a, b - 60), b - 60))
                   if w in where and alone[edge // 60] >= ALPHA]
        weaker = min(shorter, key=lambda k: fisher[k], default=None)
        if weaker is None or fisher[weaker] * ALPHA >= fisher[best]:
            break
        i = weaker
    return i


def _episode(h: History, d: int, a: int, b: int, j: Judged, i: int) -> Row:
    start, end = trim(h, d, a, b)
    s0 = h.start(d)
    lo, hi = int(math.floor(h.minute(d, start))), min(DAY_MIN, int(math.ceil(h.minute(d, end))))
    amounts = h.amounts(d, lo, max(hi, lo))
    nine = ((21 - h.hour) % 24) * 60  # 21:00 within the day
    after = h.amounts(d, max(lo, nine), max(hi, nine))["light"] if hi > nine else 0.0
    quiet = h.nights[d][0]
    fingerprint = {"door_openings": int(amounts["door"]), "light_hours_after_21": round(after, 1),
                   "quiet_start": hhmm(_clock(quiet)) if quiet else None, "plug_uses": int(amounts["plug"])}
    if "camera" in h.amount_keys:
        fingerprint["camera_events"] = int(amounts["camera"])
    if h.guests or h.guest_counts:
        fingerprint["guest_phones"] = most_guests(h, start, end)
    features = {"day": h.day(d).isoformat(), "fingerprint": fingerprint,
                **{k: round(amounts[k], 2) for k in h.amount_keys},
                "quiet_at": quiet.isoformat(timespec="seconds") if quiet else None,
                "unusual": [f for f, yes in zip(h.features, j.beyond[i]) if yes],
                "p": float(j.fisher[i]), "stretch": [hhmm(_clock(s0) + a), hhmm(_clock(s0) + b)]}
    return Row(None, "", start, end, features)


def most_guests(h: History, start: datetime, end: datetime) -> int:
    """The most guests' phones connected at once in [start, end]: the router's count at its peak, or
    else the guests' own phones that overlap."""
    peak = max((v for s, e, v in h.guest_counts if s < end and e > start), default=0.0)
    moments = sorted([(max(x.start, start), 1) for x in h.happenings if x.signal in h.guests and x.start < end
                      and x.end > start] + [(min(x.end, end), -1) for x in h.happenings
                                            if x.signal in h.guests and x.start < end and x.end > start],
                     key=lambda m: (m[0], m[1]))
    now = most = 0
    for _, step in moments:
        now += step
        most = max(most, now)
    return int(max(peak, most))


def trim(h: History, d: int, a: int, b: int) -> tuple[datetime, datetime]:
    """A stretch of whole hours, narrowed to its activity: from the first happening that starts in it
    to the last moment something that was on in it went back to rest (the lamp at 01:10)."""
    w0, w1 = h.start(d) + timedelta(minutes=a), h.start(d) + timedelta(minutes=b)
    inside = [x for x in h.happenings if _feature(x, h.guests) and x.start < w1 and x.end > w0]
    start = min((x.start for x in inside if x.start >= w0), default=w0)
    end = min(max((x.end for x in inside), default=w1), h.start(d) + timedelta(days=1))
    return start, max(start, end)


# ---------- what an occasion changes ----------

def effects(h: History, d: int, start: datetime, end: datetime, patterns: list[dict],
            quiet_kinds: set[str]) -> list[dict]:
    """Learned patterns that were late or early on this episode's day, beyond their own history.

    - the night's quiet (a quiet kind whose usual start is the home's usual bedtime) and its end;
    - any other pattern usually due during the episode, if it happened outside its usual range.
    Late = after its `late_after` (the latest ever seen); early = before its `earliest`.
    """
    night_start, night_end = h.nights[d]
    usual = _usual_night(h, d)
    s0, out = h.start(d), []
    for p in patterns:
        pat = p["pattern"]
        if pat.get("weekdays") and WEEKDAYS[h.day(d).weekday()] not in pat["weekdays"]:
            continue
        kind, _, part = p["signature"].partition(":")
        if kind == "link":  # "link:<A's signal>><B>": B is what happens (door → kettle: the kettle)
            part = part.split(">", 1)[-1]
        t = _minutes(pat["time_of_day"])
        lo = _minutes(pat.get("earliest", pat["time_of_day"]))
        hi = _minutes(pat.get("late_after", pat.get("latest", pat["time_of_day"])))
        half = (p.get("width") or 0) / 2
        if kind in ("kind", "kind-end") and part in quiet_kinds:
            night = usual[0 if kind == "kind" else 1]
            if night is None or not _inside(night, lo - half, hi + half):
                continue  # a quiet, but not the night's
            seen = night_start if kind == "kind" else night_end
            effect = "late" if seen is None else _effect(_clock(seen), t, lo, hi)
        else:
            due = s0 + timedelta(minutes=(t - h.hour * 60) % DAY_MIN)
            if not start <= due <= end:
                continue
            hits = [x.start for x in h.happenings if (x.kind == part or x.signal == part)
                    and s0 <= x.start < s0 + timedelta(days=1)]
            if not hits or any(_effect(_clock(x), t, lo, hi) is None for x in hits):
                continue  # not seen that day (absence is not reported), or seen on time
            seen = min((x for x in hits if x > due), default=max(hits))
            effect = _effect(_clock(seen), t, lo, hi)
        if effect:
            out.append({"pattern": p["id"], "effect": effect, "usual": pat["time_of_day"],
                        "seen": hhmm(_clock(seen)) if seen else None})
    return out


def _usual_night(h: History, d: int) -> tuple[float | None, float | None]:
    """The home's usual night: when its quiet starts and ends, as clock minutes (circular mean)."""
    days = [j for j in range(d) if h.counted[j] and h.held[j, -1] == 0 and h.nights[j][0] is not None]
    if not days:
        return None, None
    return (circular_mean_std([_clock(h.nights[j][0]) for j in days])[0],
            circular_mean_std([_clock(h.nights[j][1]) for j in days])[0])


def _effect(seen: float, usual: float, earliest: float, late_after: float) -> str | None:
    rel = lambda m: (m - usual + DAY_MIN / 2) % DAY_MIN - DAY_MIN / 2
    if rel(seen) > rel(late_after):
        return "late"
    if rel(seen) < rel(earliest):
        return "early"
    return None


def _inside(m: float, lo: float, hi: float) -> bool:
    return (m - lo) % DAY_MIN <= (hi - lo) % DAY_MIN


def _clock(t: datetime) -> float:
    return t.hour * 60 + t.minute + t.second / 60


def _minutes(hhmm_: str) -> float:
    hours, minutes = map(int, hhmm_.split(":"))
    return hours * 60 + minutes


# ---------- resemblance and occasions ----------

def shape(r: Row, hour: int) -> dict:
    """An episode as numbers to compare: when (hours into its day) and how much."""
    s0 = datetime.combine((r.start - timedelta(hours=hour)).date(), time(hour))
    at = lambda t: (t - s0).total_seconds() / 3600
    quiet = r.features.get("quiet_at")
    return {"start": at(r.start), "end": at(r.end), "quiet": at(datetime.fromisoformat(quiet)) if quiet else 24.0,
            **{k: float(r.features.get(k, 0)) for k in AMOUNTS}}


def similarity(v: dict, group: list[dict], clocks=CLOCKS, amounts=AMOUNTS) -> float:
    """How much `v` looks like the episodes in `group`, in [0, 1].

    Per number, the distance outside the range the group has shown, in noise units: one hour for
    times, sqrt(x + y + 1) for counts (Poisson). Differences within one unit count as none. The
    squares are added and read as a chi-square: 1 means "could be one of them", ~0 "clearly not".
    """
    total = 0.0
    for k in clocks:
        lo, hi = min(g[k] for g in group), max(g[k] for g in group)
        total += max(0.0, abs(v[k] - min(max(v[k], lo), hi)) - 1) ** 2
    for k in amounts:
        lo, hi = min(g[k] for g in group), max(g[k] for g in group)
        y = min(max(v[k], lo), hi)
        total += max(0.0, abs(v[k] - y) / math.sqrt(v[k] + y + 1) - 1) ** 2
    return float(chi2.sf(total, len(clocks) + len(amounts)))


def tightness(shapes: list[dict], amounts=AMOUNTS) -> float:
    """How well each episode looks like the others (mean leave-one-out similarity); 1 for one episode."""
    if len(shapes) < 2:
        return 1.0
    return float(np.mean([similarity(v, shapes[:i] + shapes[i + 1:], amounts=amounts)
                          for i, v in enumerate(shapes)]))


def summary(occasion_id: str, rows: list[Row], old: dict, hour: int, amounts, links: dict[str, list[str]]) -> dict:
    """The occasion card (the agreed shape, plus first/last seen and what it changes)."""
    n = len(rows)
    shapes = [shape(r, hour) for r in rows]
    prints = [r.features["fingerprint"] for r in rows]
    fingerprint = {}
    for k in prints[0]:
        got = [x[k] for x in prints if x.get(k) is not None]
        if k == "quiet_start":
            fingerprint[k] = hhmm(circular_mean_std([_minutes(x) for x in got])[0]) if got else None
        elif k == "light_hours_after_21":
            fingerprint[k] = round(float(np.median(got)), 1)
        else:
            fingerprint[k] = int(math.floor(float(np.median(got)) + 0.5))
    days = sorted(r.day for r in rows)
    changes = []
    tally = Counter((c["pattern"], c["effect"]) for r in rows for c in r.features.get("changes", []))
    for (pattern, effect), k in tally.items():
        if k * 2 > n:  # on most of its episodes
            seen = [_minutes(c["seen"]) for r in rows for c in r.features.get("changes", [])
                    if c["pattern"] == pattern and c["effect"] == effect and c.get("seen")]
            usual = next(c["usual"] for r in rows for c in r.features.get("changes", []) if c["pattern"] == pattern)
            changes.append({"pattern": pattern, "effect": effect, "usual": usual,
                            "seen": hhmm(circular_mean_std(seen)[0]) if seen else None})
    return {"id": occasion_id, "name": old.get("name"), "named_by": old.get("named_by"), "episodes": n,
            "fingerprint": fingerprint,
            "usual_hours": [hhmm(circular_mean_std([_clock(r.start) for r in rows])[0]),
                            hhmm(circular_mean_std([_clock(r.end) for r in rows])[0])] if n >= 2 else None,
            "weekdays": [WEEKDAYS[i] for i in sorted({x.weekday() for x in days})] if n >= 2 else None,
            "confidence": round(n / (n + 1) * tightness(shapes, amounts), 2),
            "explains": explain(changes, links), "changes": changes,
            "first_seen": days[0].isoformat(), "last_seen": days[-1].isoformat(),
            "learned_at": old.get("learned_at"),
            # the rest of the namer's card (source/learn/namer.py), kept with its name
            **{k: old[k] for k in ("explanation", "means", "if_missing", "importance", "same_as") if k in old}}


def explain(changes: list[dict], links: dict[str, list[str]]) -> list[str]:
    """["habit-4 late"]: the habits (or, without the habits table, the patterns) an occasion moves."""
    out = []
    for c in changes:
        for target in links.get(c["pattern"]) or [c["pattern"]]:
            if f"{target} {c['effect']}" not in out:
                out.append(f"{target} {c['effect']}")
    return out


# ---------- the three entry points ----------

def learn_occasions(db: sqlite3.Connection, day: date) -> list[dict]:
    """Run at the end of each day, after the other learners. Returns the occasions first seen today.

    Yesterday's day (which ended at the home's quietest hour today) is finished now, so it is the one
    judged; call it every day, in order. Running a day again first forgets what that day and later
    ones found, so a replay gives the same ids.
    """
    db.executescript(SCHEMA)
    target = day - timedelta(days=1)
    touched = _rewind(db, target, day)
    rows = load_rows(db)
    h = history(db, datetime.combine(day + timedelta(days=1), time()), rows)
    links = _habit_links(db)
    occasions = load_occasions(db)
    groups = {oid: [r for r in rows if r.occasion_id == oid] for oid in occasions}
    hour = h.hour if h else 0
    amounts = h.amount_keys if h else AMOUNTS[:-1]
    for oid in touched & set(occasions):  # a replay took some of their episodes away
        _save(db, summary(oid, groups[oid], occasions[oid], hour, amounts, links))
    d = h.index(datetime.combine(target, time(h.hour))) if h else -1
    found = find_episodes(h, d) if h else []
    new = []
    if found:
        patterns, quiet_kinds = _patterns(db)
        for ep in found:
            ep.features["changes"] = effects(h, d, ep.start, ep.end, patterns, quiet_kinds)
            v = shape(ep, hour)
            scored = [(similarity(v, [shape(r, hour) for r in g], amounts=amounts), oid)
                      for oid, g in groups.items() if g]
            best, oid = max(scored, default=(0.0, None))
            if oid is None or best < ALPHA:  # unlike every known occasion: a new one
                oid = _next_id(occasions)
                occasions[oid] = {"learned_at": day.isoformat()}
                groups[oid] = []
                new.append(oid)
            ep.occasion_id = oid
            db.execute("INSERT INTO occasion_episodes (occasion_id, start, end, features) VALUES (?, ?, ?, ?)",
                       (oid, ep.start.isoformat(timespec="seconds"), ep.end.isoformat(timespec="seconds"),
                        json.dumps(ep.features)))
            groups[oid].append(ep)
            occasions[oid] = summary(oid, groups[oid], occasions[oid], hour, amounts, links)
            _save(db, occasions[oid])
    db.commit()
    return [occasions[oid] for oid in new]


def match_now(db: sqlite3.Connection, now: datetime) -> list[dict]:
    """Known occasions the stretch under way looks like, most confident first. Never raises.

    Under way: a stretch of whole hours of today so far is unusual, on one feature (its smallest
    chance times the features) or on several (Fisher), times 2 for asking both and times the start
    hours tried, below ALPHA. Learning asks for two features; live, one is enough, because looking
    like a known occasion is the second line of evidence. From its first happening up to now it is
    compared with each occasion's episodes over the same clock window: their start, door openings,
    light-hours and plug uses by then. Returns [{"occasion_id", "similarity", "confidence", "since",
    "explains", "shifts"}], confidence = similarity x the occasion's confidence; occasions it is
    beyond chance unlike (similarity < ALPHA) are left out. `shifts`: [{"habit_id", "date",
    "minutes"}], how its past episodes moved habits the next day (see next_day_shifts), dated
    the day after today's.
    """
    return _match(db, now)[0]


def shifts_now(db: sqlite3.Connection, now: datetime) -> list[dict]:
    """[{"occasion_id", "confidence", "habit_id", "date", "minutes"}]: the habits an occasion under way
    moves tomorrow, and those the one that ended at the start of today's day moves today (the morning
    after: the stretch under way at 07:45 is ordinary, yet waking up is still late). Never raises."""
    try:
        matches, h = _match(db, now)
        if h is not None:  # the day before, as it was matched at its last minute
            matches += _match(db, h.start(h.n - 1) - timedelta(minutes=1))[0]
        return [{"occasion_id": m["occasion_id"], "confidence": m["confidence"], **s}
                for m in matches for s in m["shifts"]]
    except Exception:
        log.exception("shifts_now failed")
        return []


_matched: "OrderedDict[tuple, tuple[sqlite3.Connection, list[dict], History | None]]" = OrderedDict()
MATCH_CACHE = 8


def _match(db: sqlite3.Connection, now: datetime) -> tuple[list[dict], History | None]:
    """match_now, and the history it read (None when there was nothing to read). Remembered for the same
    store, moment and learned state: one tick of the loop asks it several times (the forecast's shifts, the
    occasion wakes, the plan), each a read of the whole history."""
    try:
        key = (id(db), now, *_state(db, now))
    except sqlite3.Error:
        key = None
    if key is not None and key in _matched and _matched[key][0] is db:  # the entry keeps its db: the id is its own
        _matched.move_to_end(key)
        return copy.deepcopy(_matched[key][1]), _matched[key][2]
    out = _match_once(db, now)
    if key is not None:
        _matched[key] = (db, copy.deepcopy(out[0]), out[1])
        while len(_matched) > MATCH_CACHE:
            _matched.popitem(last=False)
    return out


def _state(db: sqlite3.Connection, now: datetime) -> tuple:
    """What a match at `now` reads, in a few numbers: the happenings, the events up to then (live) and the
    occasions as stored. Later events change nothing, so yesterday's last minute is read once a day."""
    if not all(_has(db, t) for t in ("occasions", "occasion_episodes", "happenings", "events")):
        return (None,)
    return (db.execute("SELECT max(id), count(*) FROM happenings").fetchone()
            + db.execute("SELECT max(id) FROM events WHERE ts <= ?", (to_ms(now),)).fetchone()
            + db.execute("SELECT count(*), max(rowid) FROM occasion_episodes").fetchone()
            + (db.execute("SELECT group_concat(length(data)) FROM occasions").fetchone()[0],))


def _match_once(db: sqlite3.Connection, now: datetime) -> tuple[list[dict], History | None]:
    try:
        if not all(_has(db, t) for t in ("occasions", "occasion_episodes", "happenings")):
            return [], None
        occasions = load_occasions(db)
        if not occasions:
            return [], None
        rows = [r for r in load_rows(db) if r.start < now]
        h = history(db, now, rows, live=True)
        if h is None:
            return [], None
        d = h.n - 1
        m = int(h.minute(d, now))
        if m < 1:
            return [], h
        ends = sorted({e * 60 for e in range(1, 25) if e * 60 <= m} | {m})
        windows = [(a, b) for b in ends for a in range(0, b, 60)]  # every stretch of today so far
        j = judge(h, d, windows)
        # Unusual on one feature (its chance times the features: arrivals at 20:00) or on several
        # (Fisher: doors, light and a late night at 23:50); x2 for asking both, x the start hours tried.
        single = j.p.min(axis=1) * len(h.features)
        gate = np.minimum(1.0, np.minimum(single, j.fisher) * 2 * len({a for a, _ in windows}))
        enough = j.ok & (j.usual >= MIN_OCCURRENCES) & (gate < ALPHA)
        if not enough.any():
            return [], h
        # Where the stretch began: the start hour of an unusual window, if that hour is itself beyond the
        # home's history (an ordinary 11:50 door is no beginning, even though the window from 11:00 to
        # the party is unusual). Late in a party its last hour is the most unusual, yet it began at
        # 19:00, so every such start is a candidate, compared from its first happening up to now; per
        # occasion the best alignment wins.
        hours = [(a, min(a + 60, m)) for a in range(0, m, 60)]
        lively = {a for (a, _), yes in zip(hours, judge(h, d, hours).beyond.any(axis=1)) if yes}
        begins: dict[int, int] = {}
        for i in np.flatnonzero(enough):
            a = windows[i][0]
            if a in lively and (a not in begins or gate[i] < gate[begins[a]]):
                begins[a] = int(i)
        candidates = []
        for a, i in begins.items():
            since, _ = trim(h, d, a, windows[i][1])
            candidates.append((a, since, gate[i], {"start": h.minute(d, since) / 60, **h.amounts(d, a, m)}))
        links = _habit_links(db)
        habits = _habits_for_shifts(db)
        out = []
        for oid, data in occasions.items():
            days = [(h.index(r.start), r) for r in rows if r.occasion_id == oid]
            days = [(e, r) for e, r in days if 0 <= e < d]
            if not days:
                continue
            scored = []
            for a, since, g, live in candidates:
                group = [{"start": h.minute(e, r.start) / 60, **h.amounts(e, a, m)} for e, r in days]
                scored.append((similarity(live, group, clocks=("start",), amounts=h.amount_keys), -g, since))
            sim, _, since = max(scored, key=lambda s: (s[0], s[1]))
            if sim < ALPHA:
                continue
            tomorrow = h.day(d) + timedelta(days=1)
            out.append({"occasion_id": oid, "similarity": round(sim, 2),
                        "confidence": round(sim * float(data.get("confidence") or 0), 2),
                        "since": since.isoformat(timespec="seconds"),
                        "explains": explain(data.get("changes", []), links),
                        "shifts": [{"habit_id": hid, "date": tomorrow.isoformat(), "minutes": mins}
                                   for hid, mins in next_day_shifts(h, days, habits, now, tomorrow)]})
        return sorted(out, key=lambda x: (-x["confidence"], x["occasion_id"])), h
    except Exception:  # a broken learner must never stop the loop
        log.exception("match_now failed")
        return [], None


# ---------- the next day ----------

@dataclass
class ShiftHabit:
    """An active habit, with what to look for to see when it happened."""
    data: dict
    steps: list[tuple[str, str, float, tuple | None]]  # (how: "quiet-end" | "part", kind or signal,
    # offset_min, for a quiet's end the clock range its pattern has shown), its usual steps


def _habits_for_shifts(db: sqlite3.Connection) -> list[ShiftHabit]:
    """Active, non-hourly habits (part A's `habits` table) and how to spot each of their usual steps
    (share > 1/2) in the happenings: a kind or signal (a link's second part), or the end of the
    night's quiet, when the pattern is that one (its range holds the home's usual wake-up, as in
    effects). A quiet's start (bedtime) is left out: `explains` covers it. [] without habits."""
    if not (_has(db, "habits") and _has(db, "sensors")):
        return []
    try:
        signatures, ranges = {}, {}
        for sid, sig, width, data in db.execute("SELECT id, signature, width_min, data FROM sensors"):
            signatures[sid] = sig
            pat = json.loads(data).get("pattern", {}) if data else {}
            if "time_of_day" in pat:
                half = (width or 0) / 2
                ranges[sid] = (_minutes(pat.get("earliest", pat["time_of_day"])) - half,
                               _minutes(pat.get("late_after", pat.get("latest", pat["time_of_day"]))) + half)
        quiet = {k for (k,) in db.execute("SELECT id FROM kinds WHERE signal = ?", (QUIET,))} if _has(db, "kinds") else set()
        out = []
        for (data,) in db.execute("SELECT data FROM habits WHERE status = 'active' ORDER BY id"):
            habit = json.loads(data)
            if (habit.get("recurrence") or {}).get("kind") == "hourly":
                continue
            steps = []
            for st in habit.get("steps") or []:
                sig = signatures.get(st.get("pattern"))
                if not sig or (st.get("share") or 0) <= 0.5:
                    continue
                kind, _, part = sig.partition(":")
                if kind == "link":  # "link:<A's signal>><B>": B is what happens at the step's offset
                    part = part.split(">", 1)[-1]
                if kind == "kind-end" and part in quiet:
                    steps.append(("quiet-end", part, float(st.get("offset_min") or 0), ranges.get(st["pattern"])))
                elif part not in quiet:
                    steps.append(("part", part, float(st.get("offset_min") or 0), None))
            if steps:
                out.append(ShiftHabit(habit, steps))
        return out
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return []


def next_day_shifts(h: History, episodes: list[tuple[int, "Row"]], habits: list[ShiftHabit], now: datetime,
                    tomorrow: date) -> list[tuple[str, int]]:
    """[(habit id, minutes)]: the habits an occasion moved on the day after its past episodes.

    For each episode (day e) and each habit due on the next calendar day, the habit's start that day
    is the median over its usual steps of the happening nearest its usual moment (the usual time for
    that weekday, day_window, plus the step's offset) within SHIFT_BEFORE_H before and SHIFT_AFTER_H
    after it, and after the episode ended, less the offset. The end of the night's quiet is read from
    the night after the episode. Its shift is that start less the usual time. A habit is moved when, on most of the
    episodes, it moved the same way by more than twice its spread (what its times vary by once the
    weekday is taken out); `minutes` is the median shift. Only habits due `tomorrow` are listed, and
    only next days that are over by `now` count.
    """
    wake = _usual_night(h, h.n)[1]
    seen: dict[str, list[float]] = {}
    for e, r in episodes:
        nxt = h.day(e) + timedelta(days=1)
        for sh in habits:
            hd = sh.data
            usual = day_window(hd, nxt)
            if usual is None or not is_on(hd.get("recurrence") or {}, nxt):
                continue
            expected = datetime.combine(nxt, time()) + timedelta(minutes=usual[0])
            lo = max(r.end, expected - timedelta(hours=SHIFT_BEFORE_H))
            hi = expected + timedelta(hours=SHIFT_AFTER_H)
            if hi > now:
                continue  # that day isn't over yet
            starts = []
            for how, part, offset, span in sh.steps:
                if how == "quiet-end":  # only the night's: the home's usual wake-up is in its range
                    t = h.nights[e][1] if 0 <= e < h.n and span and wake is not None and _inside(wake, *span) else None
                    t = t if t is not None and lo <= t < hi else None
                else:
                    target = expected + timedelta(minutes=offset)
                    t = min((x.start for x in h.happenings if lo <= x.start < hi and (x.kind == part or x.signal == part)),
                            key=lambda x: abs(x - target), default=None)
                if t is not None:
                    starts.append((t - expected).total_seconds() / 60 - offset)
            if starts:
                seen.setdefault(hd["id"], []).append(float(np.median(starts)))
    n = len(episodes)
    out = []
    for sh in habits:
        hd = sh.data
        got = seen.get(hd["id"])
        if not got or not is_on(hd.get("recurrence") or {}, tomorrow):
            continue
        spread = max(1.0, float((hd.get("variation") or {}).get("spread_min") or 0))
        mid = float(np.median(got))
        moved = [x for x in got if abs(x) > 2 * spread and (x > 0) == (mid > 0)]
        if 2 * len(moved) > n:
            out.append((hd["id"], int(round(mid))))
    return out


def export_occasions(db: sqlite3.Connection) -> dict:
    """{"occasions": [...], "episodes": [...]}, for the dashboard; empty without the tables."""
    if not (_has(db, "occasions") and _has(db, "occasion_episodes")):
        return {"occasions": [], "episodes": []}
    occasions = sorted(load_occasions(db).values(), key=lambda o: _number(o["id"]))
    episodes = [{"occasion_id": r.occasion_id, "start": r.start.isoformat(timespec="seconds"),
                 "end": r.end.isoformat(timespec="seconds"), "features": r.features}
                for r in sorted(load_rows(db), key=lambda r: r.start)]
    return {"occasions": occasions, "episodes": episodes}


# ---------- storage ----------

def load_rows(db: sqlite3.Connection) -> list[Row]:
    if not _has(db, "occasion_episodes"):
        return []
    return [Row(rowid, oid, datetime.fromisoformat(s), datetime.fromisoformat(e), json.loads(f))
            for rowid, oid, s, e, f in db.execute(
                "SELECT rowid, occasion_id, start, end, features FROM occasion_episodes ORDER BY start, rowid")]


def load_occasions(db: sqlite3.Connection) -> dict[str, dict]:
    if not _has(db, "occasions"):
        return {}
    return {oid: json.loads(d) for oid, d in db.execute("SELECT id, data FROM occasions")}


def _save(db: sqlite3.Connection, data: dict) -> None:
    db.execute("INSERT OR REPLACE INTO occasions (id, data) VALUES (?, ?)", (data["id"], json.dumps(data)))


def _rewind(db: sqlite3.Connection, target: date, day: date) -> set[str]:
    """Forget episodes from `target` on and occasions first seen from `day` on (an earlier run of
    these days); returns the occasions that lost episodes."""
    gone = [r for r in load_rows(db) if r.day >= target]
    db.executemany("DELETE FROM occasion_episodes WHERE rowid = ?", [(r.rowid,) for r in gone])
    late = [oid for oid, d in load_occasions(db).items() if (d.get("learned_at") or "") >= day.isoformat()]
    db.executemany("DELETE FROM occasions WHERE id = ?", [(oid,) for oid in late])
    db.executemany("DELETE FROM occasion_episodes WHERE occasion_id = ?", [(oid,) for oid in late])
    left = {r.occasion_id for r in load_rows(db)}
    empty = [oid for oid in load_occasions(db) if oid not in left]
    db.executemany("DELETE FROM occasions WHERE id = ?", [(oid,) for oid in empty])
    return {r.occasion_id for r in gone} - set(late) - set(empty)


def _next_id(occasions: dict) -> str:
    return f"occasion-{max((_number(o) for o in occasions), default=0) + 1}"


def _number(oid: str) -> int:
    try:
        return int(oid.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _has(db: sqlite3.Connection, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _load_happenings(db: sqlite3.Connection, until: datetime) -> list[Happening]:
    """Happenings that started before `until`, ends clipped at it (as they were known then)."""
    if not _has(db, "happenings"):
        return []
    cut = to_ms(until)
    rows = db.execute("SELECT id, signal, start_ms, end_ms, size, value, kind, device_id, cut FROM happenings"
                      " WHERE start_ms < ? ORDER BY start_ms, id", (cut,)).fetchall()
    return [Happening(sig, to_local(s), to_local(min(e, cut)), size, value, kind, _id, dev, bool(c))
            for _id, sig, s, e, size, value, kind, dev, c in rows]


def _load_polls(db: sqlite3.Connection, until: datetime) -> list[datetime] | None:
    """When the loop polled the home (snapshots), or None if unknown (hand-made data)."""
    if not _has(db, "snapshots"):
        return None
    rows = db.execute("SELECT ts FROM snapshots WHERE ts < ? ORDER BY ts", (to_ms(until),)).fetchall()
    return [to_local(ts) for (ts,) in rows] or None


def _patterns(db: sqlite3.Connection) -> tuple[list[dict], set[str]]:
    """Active learned patterns (the `sensors` table) and the kinds that are quiets."""
    if not _has(db, "sensors"):
        return [], set()
    rows = db.execute("SELECT id, signature, width_min, data FROM sensors WHERE learner = 'habit'"
                      " AND status = 'active' ORDER BY id").fetchall()
    patterns = [{"id": sid, "signature": sig, "width": width, "pattern": json.loads(data).get("pattern", {})}
                for sid, sig, width, data in rows]
    quiet = {k for (k,) in db.execute("SELECT id FROM kinds WHERE signal = ?", (QUIET,))} if _has(db, "kinds") else set()
    return [p for p in patterns if "time_of_day" in p["pattern"]], quiet


def _habit_links(db: sqlite3.Connection) -> dict[str, list[str]]:
    """pattern id -> the habits (part A's `habits` table) it belongs to; {} until that table exists."""
    if not _has(db, "habits"):
        return {}
    out: dict[str, list[str]] = {}
    try:
        for hid, status, data in db.execute("SELECT id, status, data FROM habits ORDER BY id"):
            if status == "merged":
                continue
            for pattern in json.loads(data).get("patterns") or []:
                out.setdefault(pattern, []).append(hid)
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return {}
    return out


# ---------- CLI ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="source.learn.occasions", description="Occasions: learn them, or match a moment")
    ap.add_argument("--db", required=True, help="a home.sqlite3 with happenings (source.pipeline's --out)")
    ap.add_argument("--learn", action="store_true", help="forget the stored occasions and replay every day")
    ap.add_argument("--at", help="a moment to match live, e.g. 2026-09-21T22:30")
    args = ap.parse_args(argv)
    db = sqlite3.connect(args.db)
    if args.learn:
        db.executescript(SCHEMA + "DELETE FROM occasions; DELETE FROM occasion_episodes;")
        days = [date.fromisoformat(d) for (d,) in db.execute("SELECT day FROM days ORDER BY day")] if _has(db, "days") else []
        for day in days:
            for o in learn_occasions(db, day):
                print(f"{day}: new {o['id']} {json.dumps(o['fingerprint'])}")
    for o in export_occasions(db)["occasions"]:
        print(f"{o['id']}  {o['episodes']} episode(s)  confidence {o['confidence']}  {'-'.join(o['usual_hours'] or [])}"
              f"  {','.join(o['weekdays'] or [])}  {json.dumps(o['fingerprint'])}  {', '.join(o['explains'])}")
    if args.at:
        for m in match_now(db, datetime.fromisoformat(args.at)):
            print(f"{args.at}: looks like {m['occasion_id']} since {m['since']}  similarity {m['similarity']}"
                  f"  confidence {m['confidence']}  {', '.join(m['explains'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
