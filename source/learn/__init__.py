"""The learners: plain code that reads the stored history and writes learned sensors.

Every decision is "does this beat chance in this home?" (docs/learners.md). These three knobs
are the only fixed numbers; every other cut-off is measured from the home's own data.

Times: the store keeps epoch ms; the learners think in the home's clock time (Lisbon, naive
datetimes), converted here with `source.clock.TZ`, never the machine's zone.
"""
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from ..clock import TZ
from ..normalize import CONNECTED, GUEST_PHONES, PHONE
from .rest import SLOW as _REST_SLOW

MIN_OCCURRENCES = 5  # smallest kind, link or habit (agreed in docs/starting-plan.md)
ALPHA = 0.01  # significance level for "beats chance", after correcting for the number of tries
MAX_LAG_MIN = 30  # longest gap between the two parts of a link

# Capabilities whose happenings never break the night's quiet: the ones that drift slowly (rest.py's "slow"
# signals) and the thermostat's heating, which the thermostat switches by itself.
SLOW_CAPABILITIES = frozenset(_REST_SLOW) | {"heatingState"}
# Activity that lasts as long as a state holds (a phone at home, a setpoint left at 21 °C): only its edges are
# something a person did, so only they break a quiet (see activity_edges).
EDGE_CAPABILITIES = frozenset({CONNECTED, GUEST_PHONES, "targetTemperature", "mode"})


def device_type(signal: str) -> str:
    """"plug:kettle.instantPower" → "plug"; "bulb.power" → "bulb"; "phone:c-8f3a.connected" → "phone"."""
    return signal.split(".", 1)[0].split(":", 1)[0]


def slug(name: str) -> str:
    """"Washing machine" → "washing-machine". Never has a dot, so a signal still splits on its first one."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def signal_devices(devices: Iterable[tuple[str, str, str]]) -> dict[str, str]:
    """(device_id, type, name) rows → device_id → the device part of its signals (docs/demo-plan.md, C2).

    A type with one device keeps its type ("bulb"); one with devices of several names gets the slugged name
    ("plug:kettle"). Devices of one name are one device (a re-paired plug gets a new id, not a new name).
    A phone is always "phone:<client id>".
    """
    rows = list(devices)
    names: dict[str, set[str]] = {}
    for _, dtype, name in rows:
        names.setdefault(dtype, set()).add(slug(name or "") or dtype)
    out = {}
    for dev_id, dtype, name in rows:
        if dtype == PHONE:
            out[dev_id] = f"{PHONE}:{dev_id}"
        elif len(names[dtype]) > 1:
            out[dev_id] = f"{dtype}:{slug(name or '') or dtype}"
        else:
            out[dev_id] = dtype
    return out


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
    signal: str  # "plug.instantPower", or "plug:kettle.instantPower" when a home has several plugs
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
        """The device type ("plug"), whichever plug it is."""
        return device_type(self.signal)

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


def activity_edges(activity: list[Happening]) -> list[Happening]:
    """The activity as the quiet sees it: a happening that lasts as long as a state holds (a phone connected all
    evening) becomes two moments, its start and its end. Everything else is kept as it is.

    Without this, a phone at home all night would leave no night quiet at all.
    """
    out = []
    for h in activity:
        if h.capability in EDGE_CAPABILITIES:
            out.append(replace(h, end=h.start))
            if not h.cut:  # a cut one's end is a hole in the polls, not something a person did
                out.append(replace(h, start=h.end, id=None))
        else:
            out.append(h)
    return sorted(out, key=lambda h: h.start)
