"""Shared data shapes. These are the parts that are costly to change later (docs/event-loop.md §5)."""
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Reading:
    device_id: str
    device: str  # human name, e.g. "Plug"
    capability: str  # e.g. "instantPower"
    value: Any
    comm_ts: int | None  # device's lastCommunicationTime (ms), None for home-level values
    device_type: str = ""  # e.g. "contactSensor"; ids change on re-pairing, types don't


# (device_id, capability) -> Reading
State = dict[tuple[str, str], Reading]


@dataclass
class Event:
    ts: int  # when we noticed it (ms)
    device_id: str
    device: str
    capability: str
    kind: str  # change | stale | returned | gap
    old: Any = None
    new: Any = None
    # Who caused it. Always "unknown" for now; attribution later fills
    # agent | scenario:<id> | human.
    source: str = "unknown"
    id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        if self.kind == "change":
            return f"{self.device}.{self.capability}: {self.old!r} → {self.new!r}"
        if self.kind == "stale":
            return f"{self.device} stopped reporting"
        if self.kind == "returned":
            return f"{self.device} is reporting again"
        return f"{self.kind}: {self.new}"


@dataclass
class WakeReason:
    trigger_id: str
    reason: str
    events: list[Event] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"trigger_id": self.trigger_id, "reason": self.reason,
                "events": [e.describe() for e in self.events]}
