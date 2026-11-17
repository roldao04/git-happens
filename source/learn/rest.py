"""Rest: the state or level each signal sits at most of the time (docs/starting-plan.md, part 3).

Everything is measured in observed time, never in event counts. Time between snapshots that were far
apart (gap), while a device was silent (stale) or after a signal left the snapshot (gone) is unknown.
"""
from collections.abc import Iterable
from dataclasses import dataclass
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
        return v == self.value


def find_rest(events: Iterable[Event], observed_until: int) -> dict[SignalId, Rest]:
    out: dict[SignalId, Rest] = {}
    for sig, stretches in observed_stretches(events, observed_until).items():
        held: dict[Any, int] = {}
        for s in stretches:
            held[s.value] = held.get(s.value, 0) + s.end - s.start
        observed = sum(held.values())
        if observed == 0:
            continue
        value = _longest(held)
        out[sig] = Rest(sig, stretches[0].signal, "state", value, 0.0, 0.0, None, None,
                        held[value] / observed, observed)
    return out


def _longest(held: dict[Any, int]) -> Any:
    best = None
    for v, ms in held.items():
        if best is None or ms > held[best]:
            best = v
    return best
