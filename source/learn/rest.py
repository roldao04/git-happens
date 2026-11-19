"""Rest: the state or level each signal sits at most of the time (docs/starting-plan.md, part 3).

Everything is measured in observed time, never in event counts. Time between snapshots that were far
apart (gap), while a device was silent (stale) or after a signal left the snapshot (gone) is unknown.

  uv run python -m source.learn.rest data/sim/rosa/      # the rests of an ingested history
"""
import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..model import Event
from ..normalize import CONNECTED
from ..store import Store

SignalId = tuple[str, str]  # (device_id, capability)
Span = tuple[int, int]  # [start, end) in ms

SKIP = {
    "todayConsumption", "yesterdayConsumption", "additionalEnergyConsumption", "sdAvailable", "sdStorage",
    "batteryLevel", "batteryStatus", "signalStrength", "wifiSignalStrength", "cellularSignalStrength",
    "cellularState", "cellularConnectionState", "chargeState", "lastCommunicationTime",
    "multiPower", "instantVoltage",  # multiPower repeats `power`; mains voltage is noise
}
VALUE_KINDS = {"initial", "change", "gone"}
SLOW = {"temperatureValue", "humidityValue"}
# Signals whose rest is fixed, not measured: a phone at home most of the day still rests "off" (not connected).
# A phone's reading that is gone (a history ingested without normalize's prev) also means "off".
FIXED_REST = {CONNECTED: "off"}
HOUR_MS = 3600_000
WOBBLE_COVERS = 0.995  # of the time held in the resting group; the few readings further out are not rest


@dataclass(frozen=True)
class Stretch:
    signal_id: SignalId
    signal: str  # "Plug.instantPower", for display: re-paired devices can share it
    value: Any
    start: int
    end: int
    source: str = "unknown"  # who caused the event that began it (Event.source)


def observed_stretches(events: Iterable[Event], observed_until: int) -> dict[SignalId, list[Stretch]]:
    """Per signal, the observed stretches [start, end) of each non-None value, in time order.

    events must be in time order, as the store returns them. observed_until is the time of the last
    snapshot to use; nothing at or after it counts.
    """
    gaps: list[Span] = []
    silent: dict[str, list[Span]] = {}
    silent_since: dict[str, int] = {}
    values: dict[SignalId, list[Event]] = {}
    prev_ts = None
    for e in events:
        if prev_ts is not None and e.ts < prev_ts:
            raise ValueError(f"events out of order: {e.ts} after {prev_ts}")
        prev_ts = e.ts
        if e.ts >= observed_until:
            continue
        if e.kind == "gap":
            gaps.append((e.old, e.new))
        elif e.kind == "stale":
            silent_since[e.device_id] = e.new
        elif e.kind == "returned" and e.device_id in silent_since:
            silent.setdefault(e.device_id, []).append((silent_since.pop(e.device_id), e.ts))
        elif e.kind in VALUE_KINDS and e.capability not in SKIP:
            values.setdefault((e.device_id, e.capability), []).append(e)
    for dev, since in silent_since.items():
        silent.setdefault(dev, []).append((since, observed_until))

    out: dict[SignalId, list[Stretch]] = {}
    for sig, evs in values.items():
        holes = sorted(gaps + silent.get(sig[0], []))
        for e, nxt in zip(evs, evs[1:] + [None]):
            value = FIXED_REST[sig[1]] if e.new is None and sig[1] in FIXED_REST else e.new
            if value is None:
                continue
            end = nxt.ts if nxt is not None else observed_until
            for start, stop in _subtract((e.ts, end), holes):
                out.setdefault(sig, []).append(Stretch(sig, f"{e.device}.{e.capability}", value, start, stop, e.source))
    return out


def _subtract(span: Span, holes: list[Span]) -> list[Span]:
    start, end = span
    pieces = []
    for a, b in holes:
        if b <= start or a >= end:
            continue
        if a > start:
            pieces.append((start, a))
        start = max(start, b)
    if start < end:
        pieces.append((start, end))
    return pieces


@dataclass(frozen=True)
class Rest:
    signal_id: SignalId
    signal: str
    kind: str  # state | level | slow
    value: Any
    wobble: float
    step: float
    rate_per_h: float | None
    rate_wobble: float | None
    share: float  # of observed time spent at rest
    observed_ms: int

    def at_rest(self, v: Any) -> bool:
        if self.kind == "slow":
            raise ValueError(f"{self.signal} is slow: check its rate with rate_at_rest")
        if self.kind == "state":
            return v == self.value
        return _is_number(v) and abs(v - self.value) <= self.wobble

    def rate_at_rest(self, dv: float, dt_ms: int) -> bool:
        """A slow signal is at rest while it moves no faster than it normally does."""
        if self.kind != "slow" or self.rate_per_h is None or self.rate_wobble is None:
            raise ValueError(f"{self.signal} is not slow: check its value with at_rest")
        if dt_ms <= 0:
            raise ValueError(f"dt_ms must be positive, got {dt_ms}")
        return abs(dv) / dt_ms * HOUR_MS <= self.rate_per_h + self.rate_wobble


def find_rest(events: Iterable[Event], observed_until: int) -> dict[SignalId, Rest]:
    out: dict[SignalId, Rest] = {}
    for sig, stretches in observed_stretches(events, observed_until).items():
        held: dict[Any, int] = {}
        for s in stretches:
            held[s.value] = held.get(s.value, 0) + s.end - s.start
        observed = sum(held.values())
        if observed == 0:
            continue
        if sig[1] in FIXED_REST:
            value = FIXED_REST[sig[1]]
            out[sig] = Rest(sig, stretches[0].signal, "state", value, 0.0, 0.0, None, None,
                            held.get(value, 0) / observed, observed)
        elif sig[1] in SLOW and all(_is_number(v) for v in held):
            out[sig] = _slow(sig, stretches, held, observed)
        elif all(_is_number(v) for v in held):
            out[sig] = _level(sig, stretches[0].signal, held, observed)
        else:
            value = _longest(held)
            out[sig] = Rest(sig, stretches[0].signal, "state", value, 0.0, 0.0, None, None,
                            held[value] / observed, observed)
    return out


def rest_from_store(store: Store, until_ms: int) -> dict[SignalId, Rest]:
    """Rest from everything stored before until_ms, observed up to the last snapshot before it."""
    last = store.last_snapshot_before(until_ms)
    if last is None:
        return {}
    return find_rest(store.events_before(until_ms), last)


def _level(sig: SignalId, signal: str, held: dict[Any, int], observed: int) -> Rest:
    step = _resolution(held)
    group = _busiest_group(held, step)
    value = _weighted_median([(v, held[v]) for v in group])
    wobble = max(step, _spread([(abs(v - value), held[v]) for v in group]))
    at_rest = sum(ms for v, ms in held.items() if abs(v - value) <= wobble)
    return Rest(sig, signal, "level", value, wobble, step, None, None, at_rest / observed, observed)


def _slow(sig: SignalId, stretches: list[Stretch], held: dict[Any, int], observed: int) -> Rest:
    step = _resolution(held)
    rates = [(abs(b.value - a.value) / (b.start - a.start) * HOUR_MS, b.start - a.start)
             for a, b in zip(stretches, stretches[1:]) if a.end == b.start]
    rate = _weighted_median(rates) if rates else 0.0
    wobble = max(step, _weighted_median([(abs(r - rate), ms) for r, ms in rates]) if rates else 0.0)
    calm = sum(ms for r, ms in rates if r <= rate + wobble)
    share = calm / sum(ms for _, ms in rates) if rates else 1.0
    return Rest(sig, stretches[0].signal, "slow", _weighted_median(list(held.items())), 0.0, step,
                rate, wobble, share, observed)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _resolution(held: dict[Any, int]) -> float:
    """The finest unit the values are reported in: 1 for whole numbers, 0.1 for 22.9."""
    exponent = min(min(Decimal(str(v)).normalize().as_tuple().exponent, 0) for v in held)
    return float(Decimal(1).scaleb(exponent))


def _busiest_group(held: dict[Any, int], step: float) -> list[Any]:
    """Values close together form a group; the group held longest is rest (ties: the lowest)."""
    values = sorted(held)
    split = max(step, 0.05 * (values[-1] - values[0]))
    groups = [[values[0]]]
    for a, b in zip(values, values[1:]):
        if b - a > split:
            groups.append([])
        groups[-1].append(b)
    best = groups[0]
    for g in groups[1:]:
        if sum(held[v] for v in g) > sum(held[v] for v in best):
            best = g
    return best


def _weighted_median(pairs: list[tuple[Any, int]]) -> Any:
    return _weighted_quantile(pairs, 0.5)


def _spread(distances: list[tuple[float, int]]) -> float:
    """How far from rest the group reaches, without its rarest readings.

    A burst passes through low values on its way up and down. They can chain onto the resting group,
    but they hold almost none of its time, so they must not widen the wobble.
    """
    return _weighted_quantile(distances, WOBBLE_COVERS)


def _weighted_quantile(pairs: list[tuple[Any, int]], q: float) -> Any:
    pairs = sorted(pairs)
    target, acc = sum(ms for _, ms in pairs) * q, 0
    for v, ms in pairs:
        acc += ms
        if acc >= target:
            return v
    return pairs[-1][0]


def _longest(held: dict[Any, int]) -> Any:
    best = None
    for v, ms in held.items():
        if best is None or ms > held[best]:
            best = v
    return best


def describe(rest: Rest) -> str:
    if rest.kind == "state":
        at = repr(rest.value)
    elif rest.kind == "level":
        at = f"{rest.value:g} ± {rest.wobble:g}"
    else:
        at = f"around {rest.value:g}, moving {rest.rate_per_h:.2f} ± {rest.rate_wobble:.2f} per hour"
    return f"{rest.signal:<38} {rest.kind:<5} {at:<46} {rest.share:6.1%} of {rest.observed_ms / HOUR_MS:,.1f} h"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="source.learn.rest", description="Show each signal's rest in a history")
    p.add_argument("folder", type=Path, help="a folder with a home.sqlite3, as source.ingest writes it")
    p.add_argument("--all", action="store_true", help="also show signals that never left rest")
    args = p.parse_args(argv)
    if not (args.folder / "home.sqlite3").exists():
        print(f"{args.folder} has no home.sqlite3: ingest a file into it first")
        return 1
    store = Store(args.folder, record=False)
    try:
        last = store.last_snapshot_before(2 ** 62)
        rests = {} if last is None else rest_from_store(store, last + 1)
    finally:
        store.close()
    shown = sorted((r for r in rests.values() if args.all or r.share < 1), key=lambda r: (r.signal, r.signal_id))
    for rest in shown:
        print(describe(rest))
    hidden = "" if args.all else " (the others never left rest; --all shows them)"
    print(f"{len(shown)} of {len(rests)} signals shown{hidden}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
