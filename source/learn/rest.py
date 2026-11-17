"""Rest: the state or level each signal sits at most of the time (docs/starting-plan.md, part 3).

Everything is measured in observed time, never in event counts. Time between snapshots that were far
apart (gap), while a device was silent (stale) or after a signal left the snapshot (gone) is unknown.
"""
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..model import Event

SignalId = tuple[str, str]  # (device_id, capability)
Span = tuple[int, int]  # [start, end) in ms

SKIP = {
    "todayConsumption", "yesterdayConsumption", "additionalEnergyConsumption", "sdAvailable", "sdStorage",
    "batteryLevel", "batteryStatus", "signalStrength", "wifiSignalStrength", "cellularSignalStrength",
    "cellularState", "cellularConnectionState", "chargeState", "lastCommunicationTime",
    "multiPower", "instantVoltage",  # multiPower repeats `power`; mains voltage is noise
}
VALUE_KINDS = {"initial", "change", "gone"}


@dataclass(frozen=True)
class Stretch:
    signal_id: SignalId
    signal: str  # "Plug.instantPower", for display: re-paired devices can share it
    value: Any
    start: int
    end: int


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
            if e.new is None:
                continue
            end = nxt.ts if nxt is not None else observed_until
            for start, stop in _subtract((e.ts, end), holes):
                out.setdefault(sig, []).append(Stretch(sig, f"{e.device}.{e.capability}", e.new, start, stop))
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
        if self.kind == "state":
            return v == self.value
        return _is_number(v) and abs(v - self.value) <= self.wobble


def find_rest(events: Iterable[Event], observed_until: int) -> dict[SignalId, Rest]:
    out: dict[SignalId, Rest] = {}
    for sig, stretches in observed_stretches(events, observed_until).items():
        held: dict[Any, int] = {}
        for s in stretches:
            held[s.value] = held.get(s.value, 0) + s.end - s.start
        observed = sum(held.values())
        if observed == 0:
            continue
        if all(_is_number(v) for v in held):
            out[sig] = _level(sig, stretches[0].signal, held, observed)
        else:
            value = _longest(held)
            out[sig] = Rest(sig, stretches[0].signal, "state", value, 0.0, 0.0, None, None,
                            held[value] / observed, observed)
    return out


def _level(sig: SignalId, signal: str, held: dict[Any, int], observed: int) -> Rest:
    step = _resolution(held)
    group = _busiest_group(held, step)
    value = _weighted_median([(v, held[v]) for v in group])
    wobble = max(step, max(abs(v - value) for v in group))
    at_rest = sum(ms for v, ms in held.items() if abs(v - value) <= wobble)
    return Rest(sig, signal, "level", value, wobble, step, None, None, at_rest / observed, observed)


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
    pairs = sorted(pairs)
    half, acc = sum(ms for _, ms in pairs) / 2, 0
    for v, ms in pairs:
        acc += ms
        if acc >= half:
            return v
    return pairs[-1][0]


def _longest(held: dict[Any, int]) -> Any:
    best = None
    for v, ms in held.items():
        if best is None or ms > held[best]:
            best = v
    return best
