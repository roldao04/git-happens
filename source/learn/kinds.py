"""Part 5, the grouper: happenings → kinds → links. See docs/learners.md.

Kinds: per signal, a Gaussian mixture on (log size, log duration). BIC decides how many kinds
the data supports, so nothing like "within 20%" is fixed. Links: event coincidence analysis.
B is linked to A when B follows A more often than chance in this home allows.
"""
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
from scipy.stats import chi2
from sklearn.mixture import GaussianMixture

from . import ALPHA, MAX_LAG_MIN, MIN_OCCURRENCES, Happening
from .rhythm import Rhythm, clock_min, poisson_binomial_sf

UNITS = {"instantPower": "W", "instantCurrent": "mA", "instantVoltage": "V",
         "temperatureValue": "°C", "humidityValue": "%"}
LAG_WINDOWS = (MAX_LAG_MIN / 3, 2 * MAX_LAG_MIN / 3, MAX_LAG_MIN)  # the lag is scanned, like habits
REG = 1e-3  # covariance floor (log space), so one-value kinds still have a spread


@dataclass
class Kind:
    id: str  # "<device>-A"
    signal: str
    count: int
    duration_s: float  # median
    size: float | None = None  # median; None for on/off signals
    value: str | None = None  # most common on/off value
    centre: list[float] = field(default_factory=list)  # log space, for matching across days
    lo: list[float] = field(default_factory=list)
    hi: list[float] = field(default_factory=list)
    mean: list[float] = field(default_factory=list)  # log space, to classify new happenings
    cov: list[list[float]] = field(default_factory=list)

    @property
    def device(self) -> str:
        return self.signal.split(".", 1)[0]

    @property
    def description(self) -> str:
        cap = self.signal.split(".", 1)[-1]
        if self.size is None:
            return f"{self.id} ({cap} {self.value or 'active'}, about {fmt_duration(self.duration_s)})"
        return f"{self.id} (about {fmt_size(self.size)} {UNITS.get(cap, cap)}, about {fmt_duration(self.duration_s)})"


@dataclass
class Link:
    a: str  # a kind id, or a whole signal ("contactSensor.sensorState": any of its happenings)
    b: str
    pairs: list[tuple[Happening, Happening]]  # (a, b): the occurrences
    lag_min: float  # median
    window_min: float
    p: float  # corrected for the number of tries
    null: str


# ---------- kinds ----------

def find_kinds(happenings: list[Happening], known: list[Kind] | None = None) -> list[Kind]:
    """Group each signal's happenings into kinds; sets h.kind (None = fits no kind)."""
    known = known or []
    kinds: list[Kind] = []
    groups: dict[tuple[str, int], list[Happening]] = defaultdict(list)
    for h in happenings:
        h.kind = None
        if not h.cut:  # a cut happening's duration is unknown: it would blur its kind
            groups[(h.signal, 0 if h.size is None else (1 if h.size > 0 else -1))].append(h)
    for (signal, sign), hs in sorted(groups.items()):
        if len(hs) < MIN_OCCURRENCES:
            continue
        X = np.array([_features(h) for h in hs])
        labels = _mixture_labels(X)
        found = []
        for label in sorted(set(labels)):
            idx = [i for i, l in enumerate(labels) if l == label]
            if len(idx) < MIN_OCCURRENCES:
                continue
            members = [hs[i] for i in idx]
            sizes = [abs(h.size) * sign for h in members if h.size is not None]
            values = [h.value for h in members if h.value]
            found.append((members, Kind(
                id="", signal=signal, count=len(members),
                duration_s=float(np.median([h.duration_s for h in members])),
                size=float(np.median(sizes)) if sizes else None,
                value=max(sorted(set(values)), key=values.count) if values else None,  # sorted: same tie-break every run
                centre=np.median(X[idx], axis=0).tolist(),
                lo=X[idx].min(axis=0).tolist(), hi=X[idx].max(axis=0).tolist(),
                mean=X[idx].mean(axis=0).tolist(),
                cov=(np.atleast_2d(np.cov(X[idx], rowvar=False)) + REG * np.eye(X.shape[1])).tolist())))
        _assign_ids(found, [k for k in known if k.signal == signal], {k.id for k in known} | {k.id for k in kinds})
        for members, kind in found:
            for h in members:
                h.kind = kind.id
            kinds.append(kind)
    return kinds


def classify(h: Happening, kinds: list[Kind]) -> str | None:
    """Which known kind a new happening belongs to, or None.

    It fits a kind when its squared Mahalanobis distance is within the chi-square bound at ALPHA
    (degrees of freedom = 1 or 2 features); the nearest fitting kind wins. Kinds stored without a
    covariance fall back to their min/max box.
    """
    x = np.array(_features(h))
    best = None
    for k in kinds:
        if k.signal != h.signal or len(k.centre) != len(x) or (h.size is not None and k.size is not None
                                                               and (h.size > 0) != (k.size > 0)):
            continue
        if k.cov:
            diff = x - np.array(k.mean)
            d2 = float(diff @ np.linalg.solve(np.array(k.cov), diff))
            if d2 > chi2.ppf(1 - ALPHA, len(x)):
                continue
        elif not _inside(x, k.lo, k.hi):
            continue
        else:
            d2 = math.dist(x, k.centre) ** 2
        if best is None or d2 < best[0]:
            best = (d2, k.id)
    return best[1] if best else None


def _features(h: Happening) -> list[float]:
    dur = math.log(max(h.duration_s, 1.0))
    return [dur] if h.size is None else [math.log(max(abs(h.size), 1e-9)), dur]


def _mixture_labels(X: np.ndarray) -> list[int]:
    """Fit 1, 2, 3… Gaussians; keep the count with the lowest BIC (stop after two worse fits)."""
    best, worse = None, 0
    distinct = len(np.unique(X, axis=0))  # poll-rounded data repeats values; never ask for more kinds
    for k in range(1, min(len(X) // MIN_OCCURRENCES, distinct) + 1):
        gm = GaussianMixture(k, random_state=0, n_init=2, reg_covar=REG).fit(X)
        bic = gm.bic(X)
        if best is None or bic < best[0]:
            best, worse = (bic, gm), 0
        else:
            worse += 1
            if worse == 2:
                break
    return best[1].predict(X).tolist()


def _assign_ids(found: list, known: list[Kind], taken: set[str]) -> None:
    """Reuse a known kind's id when its centre falls in the new group's box (or vice versa)."""
    pairs = []
    for i, (_, k) in enumerate(found):
        for old in known:
            if len(old.centre) == len(k.centre) and (_inside(old.centre, k.lo, k.hi) or _inside(k.centre, old.lo, old.hi)):
                pairs.append((math.dist(old.centre, k.centre), i, old.id))
    used_new, used_old = set(), set()
    for _, i, old_id in sorted(pairs):
        if i not in used_new and old_id not in used_old:
            found[i][1].id = old_id
            used_new.add(i)
            used_old.add(old_id)
    for _, k in sorted(found, key=lambda f: f[0][0].start):
        if not k.id:
            k.id = _next_id(k.device, taken)
        taken.add(k.id)


def _inside(p, lo, hi) -> bool:
    return all(a <= x <= b for x, a, b in zip(p, lo, hi))


def _next_id(device: str, taken: set[str]) -> str:
    n = 0
    while True:
        letters, m = "", n
        while True:
            letters = chr(ord("A") + m % 26) + letters
            m = m // 26 - 1
            if m < 0:
                break
        if f"{device}-{letters}" not in taken:
            return f"{device}-{letters}"
        n += 1


# ---------- links ----------

def find_links(happenings: list[Happening], kinds: list[Kind], days: list[date], rhythm: Rhythm,
               null: str = "rhythm") -> list[Link]:
    """Ordered pairs (A then B, different signals) that coincide more often than chance.

    A part is a kind or a whole on/off signal ("door opened", whatever its kind). For each B,
    "observed" counts B happenings with an A in the window before them. Chance per B:
      rhythm     A follows the home's activity across the day (lenient)
      other_day  A's own happenings on the other days, at the same clock time (strict)
    """
    if null not in ("rhythm", "other_day"):
        raise ValueError(f"unknown null {null!r}")
    parts: dict[str, list[Happening]] = defaultdict(list)
    for h in happenings:
        if h.signal == "home.quiet":  # a quiet always follows *some* happening; how it begins is in its kind
            continue
        if h.kind:
            parts[h.kind].append(h)
        if h.size is None and not h.cut:
            parts[h.signal].append(h)
    parts = {p: sorted(hs, key=lambda h: h.start) for p, hs in parts.items() if len(hs) >= MIN_OCCURRENCES}
    signal_of = {p: hs[0].signal for p, hs in parts.items()}
    # only between devices: two signals of one device (plug power and current) always coincide
    pairs = [(a, b) for a in parts for b in parts if parts[a][0].device != parts[b][0].device]
    tries = max(1, len(pairs) * len(LAG_WINDOWS))
    links: list[Link] = []
    for a, b in pairs:
        best = None
        a_starts = [h.start for h in parts[a]]
        for w in LAG_WINDOWS:
            hits = [_latest_before(parts[a], a_starts, hb.start, w) for hb in parts[b]]
            observed = sum(x is not None for x in hits)
            if observed < MIN_OCCURRENCES:
                continue
            probs = [_chance(parts[a], a_starts, hb.start, w, days, rhythm, null) for hb in parts[b]]
            p = poisson_binomial_sf(observed, probs)
            if best is None or p < best[0]:
                best = (p, w, [(ha, hb) for ha, hb in zip(hits, parts[b]) if ha is not None])
        if best and best[0] * tries < ALPHA:
            p, w, prs = best
            lags = [(hb.start - ha.start).total_seconds() / 60 for ha, hb in prs]
            links.append(Link(a, b, prs, float(np.median(lags)), w, min(1.0, p * tries), null))
    return _strongest_per_target(links, signal_of)


def _latest_before(hs: list[Happening], starts: list[datetime], t: datetime, w_min: float) -> Happening | None:
    i = bisect_right(starts, t) - 1
    if i >= 0 and t - starts[i] <= timedelta(minutes=w_min) and hs[i].start <= t:
        return hs[i]
    return None


def _chance(hs_a, a_starts, t: datetime, w_min: float, days: list[date], rhythm: Rhythm, null: str) -> float:
    if null == "rhythm":  # expected A in [t-w, t] if A followed the home's rhythm
        expected = len(hs_a) / len(days) * rhythm.share(clock_min(t) - w_min, w_min)
        return 1 - math.exp(-expected)
    others = [d for d in days if d != t.date()]
    if not others:
        return 1.0
    hit = 0
    for d in others:
        shifted = t + timedelta(days=(d - t.date()).days)
        lo = bisect_left(a_starts, shifted - timedelta(minutes=w_min))
        hit += lo < len(a_starts) and a_starts[lo] <= shifted
    return hit / len(others)


def _strongest_per_target(links: list[Link], signal_of: dict[str, str]) -> list[Link]:
    """"door → kettle" once: the best door part per B, and a whole-signal B only if none of its kinds is linked."""
    best: dict[tuple, Link] = {}
    for link in links:
        key = (signal_of[link.a], link.b)
        if key not in best or link.p < best[key].p:
            best[key] = link
    kept = list(best.values())
    kind_linked = {(signal_of[l.a], signal_of[l.b]) for l in kept if l.b != signal_of[l.b]}
    return [l for l in kept if l.b != signal_of[l.b] or (signal_of[l.a], l.b) not in kind_linked]


# ---------- formatting ----------

def fmt_size(x: float) -> str:
    if x == 0:
        return "0"
    digits = -math.floor(math.log10(abs(x))) + 1
    return f"{round(x, digits):,.{max(0, digits)}f}"


def fmt_duration(s: float) -> str:
    if s < 90:
        return f"{max(10, round(s / 10) * 10)} s"
    if s < 90 * 60:
        return f"{round(s / 60)} min"
    return f"{s / 3600:.1f} h".replace(".0 h", " h")
