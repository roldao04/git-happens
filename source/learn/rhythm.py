"""The home's activity rhythm (its "chance" baseline) and the small amount of probability we need."""
import math
from datetime import datetime

import numpy as np
from scipy.stats import binom

from ..clock import TZ

DAY_MIN = 1440


def clock_min(t: datetime) -> float:
    return t.hour * 60 + t.minute + t.second / 60


class Rhythm:
    """When this home is active: the clock times of every activity happening's start and end."""

    def __init__(self, moments: list[datetime]):
        self.t = np.sort(np.array([clock_min(m) for m in moments], dtype=float))

    def _in(self, start: float, width: float) -> np.ndarray:
        return (self.t - start) % DAY_MIN < width

    def share(self, start: float, width: float, removed: list[tuple[float, float]] = ()) -> float:
        """Share of activity in [start, start + width) on the clock, ignoring removed windows."""
        if not len(self.t):
            return width / DAY_MIN
        keep = np.ones(len(self.t), bool)
        for s, w in removed:
            keep &= ~self._in(s, w)
        if not keep.any():
            return width / DAY_MIN
        return float((self._in(start, width) & keep).sum() / keep.sum())


def binomial_sf(k: int, n: int, p: float) -> float:
    """P(X >= k), X ~ Binomial(n, p)."""
    return float(binom.sf(k - 1, n, min(max(p, 0.0), 1.0)))


def poisson_binomial_sf(k: int, probs: list[float]) -> float:
    """P(X >= k) where X is a sum of independent coin flips with these probabilities."""
    dist = np.zeros(len(probs) + 1)
    dist[0] = 1.0
    for p in probs:
        dist[1:] = dist[1:] * (1 - p) + dist[:-1] * p
        dist[0] *= 1 - p
    return float(min(1.0, dist[k:].sum()))


def circular_mean_std(minutes: list[float]) -> tuple[float, float]:
    """Mean clock time and spread (circular std), both in minutes."""
    a = np.array(minutes) * 2 * math.pi / DAY_MIN
    c, s = np.cos(a).mean(), np.sin(a).mean()
    r = min(1.0, math.hypot(c, s))
    mean = (math.atan2(s, c) * DAY_MIN / (2 * math.pi)) % DAY_MIN
    std = math.sqrt(-2 * math.log(r)) * DAY_MIN / (2 * math.pi) if r > 0 else DAY_MIN / 2
    return mean, std


def hhmm(minutes: float) -> str:
    m = round(minutes) % DAY_MIN
    return f"{m // 60:02d}:{m % 60:02d}"


class Watched:
    """When the home was being watched: the loop's polls are its heartbeat (docs/learners.md).

    No poll for longer than `hole_min` is a hole: we can't tell whether anything happened.
    `Watched(None)` means "always watched" (hand-made files with no polls).
    """

    def __init__(self, polls: list[datetime] | None, hole_min: float):
        self.t = None if polls is None else np.sort(np.array([_s(p) for p in polls], dtype=float))
        self.hole_s = hole_min * 60

    def saw(self, start: datetime, end: datetime) -> bool:
        """At least one poll in [start, end]."""
        if self.t is None:
            return True
        i = int(np.searchsorted(self.t, _s(start)))
        return i < len(self.t) and self.t[i] <= _s(end)

    def unbroken(self, start: datetime, end: datetime) -> bool:
        """Polled all the way through [start, end], with no hole."""
        if self.t is None:
            return True
        i = int(np.searchsorted(self.t, _s(start), side="right")) - 1
        j = int(np.searchsorted(self.t, _s(end), side="left"))
        if i < 0 or j >= len(self.t):
            return False
        return bool(np.all(np.diff(self.t[i:j + 1]) <= self.hole_s))


def _s(t: datetime) -> float:
    """Seconds since the epoch of a clock time in the home's zone."""
    return (t if t.tzinfo else t.replace(tzinfo=TZ)).timestamp()


def late_bound(minutes_after_start: list[float], alpha: float) -> tuple[float, float]:
    """Conformal bound on "it still happens later": (bound, chance a new one comes after it).

    With n past times, a new one lands after the k-th smallest with probability (n+1-k)/(n+1).
    k = ceil((1-alpha)(n+1)), capped at n, so the bound is the latest so far until n >= 99.
    """
    xs = sorted(minutes_after_start)
    n = len(xs)
    k = min(n, math.ceil((1 - alpha) * (n + 1)))
    return xs[k - 1], (n + 1 - k) / (n + 1)
