"""The learners: plain code that reads the stored history and writes learned sensors.

Every decision is "does this beat chance in this home?" (docs/learners.md). These three knobs
are the only fixed numbers; every other cut-off is measured from the home's own data.

Times: the store keeps epoch ms; the learners think in the home's clock time (Lisbon, naive
datetimes), converted here with `source.clock.TZ`, never the machine's zone.
"""
from dataclasses import dataclass
from datetime import datetime

from ..clock import TZ
from .rest import SLOW as _REST_SLOW

MIN_OCCURRENCES = 5  # smallest kind, link or habit (agreed in docs/starting-plan.md)
ALPHA = 0.01  # significance level for "beats chance", after correcting for the number of tries
MAX_LAG_MIN = 30  # longest gap between the two parts of a link

# Capabilities that drift slowly (rest.py's "slow" signals); their happenings never break the night's quiet.
SLOW_CAPABILITIES = frozenset(_REST_SLOW)


def to_local(ms: int) -> datetime:
    """Epoch ms → the home's clock time (naive)."""
    return datetime.fromtimestamp(ms / 1000, TZ).replace(tzinfo=None)


def to_ms(t: datetime) -> int:
    """The home's clock time (naive, or aware in any zone) → epoch ms."""
    return int((t if t.tzinfo else t.replace(tzinfo=TZ)).timestamp() * 1000)


def _clock_time(d: dict, key: str) -> datetime:
    if f"{key}_ms" in d:
        return to_local(int(d[f"{key}_ms"]))
    t = datetime.fromisoformat(d[key])
    return t.astimezone(TZ).replace(tzinfo=None) if t.tzinfo else t


@dataclass
class Happening:
    """A signal leaving rest and coming back (docs/starting-plan.md, "A happening")."""
    signal: str  # "<device type>.<capability>", e.g. "plug.instantPower"
    start: datetime  # the home's clock time
    end: datetime
    size: float | None = None  # None for on/off signals
    value: str | None = None  # the non-rest state of an on/off signal, e.g. "alarm"
    kind: str | None = None  # filled by the grouper
    id: int | None = None
    device_id: str | None = None
    cut: bool = False  # ended by a gap, a stale device or a gone signal: its duration is unknown

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def device(self) -> str:
        return self.signal.split(".", 1)[0]

    @property
    def capability(self) -> str:
        return self.signal.split(".", 1)[-1]

    @classmethod
    def from_dict(cls, d: dict) -> "Happening":
        """The agreed shape, with ISO `start`/`end` or epoch-ms `start_ms`/`end_ms`."""
        return cls(d["signal"], _clock_time(d, "start"), _clock_time(d, "end"), d.get("size"), d.get("value"),
                   d.get("kind"), d.get("id"), d.get("device_id"), bool(d.get("cut", False)))

    def to_dict(self) -> dict:
        out = {"signal": self.signal, "start": self.start.isoformat(timespec="seconds"),
               "end": self.end.isoformat(timespec="seconds"), "duration_s": round(self.duration_s),
               "size": self.size, "kind": self.kind}
        if self.value is not None:
            out["value"] = self.value
        if self.device_id is not None:
            out["device_id"] = self.device_id
        if self.cut:
            out["cut"] = True
        return out


def is_activity(h: Happening, slow: frozenset[str] = SLOW_CAPABILITIES) -> bool:
    return h.capability not in slow
