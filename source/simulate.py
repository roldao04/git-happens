"""Simulator: a household's days as get_home snapshots, one JSON line per snapshot.

  uv run python -m source.simulate --days 14 --household rosa --out data/sim/rosa.jsonl
  uv run python -m source.simulate --days 30 --household home-a --out data/sim/home-a.jsonl
  uv run python -m source.simulate --days 14 --houses 5 --out data/sim/       # five different made-up homes
  uv run python -m source.simulate --list            # the households
  uv run python -m source.simulate --serve           # a form in the browser: fill it in, download the file

Each line is {"ts": <epoch ms>, "home": <get_home>}, the format ReplaySource and ingest read. The snapshot
starts as a real one from the kit (sim_data/home_template.json), so every field and value is the kit's own
(the door says "alarm" for open and "normal" for closed). No time comes from the real clock.

Two kinds of household:
- A config (`Household`): a wake time, an appliance on the plug, a bedtime and so on, each with random
  variation and skipped days. Change the config and the same code gives a different home. Off by default, it
  can also have rarer rhythms (a visit every n days, a monthly outing, the appliance every few hours) and
  parties; "rosa-plus" has them all.
- "home-a": a real kitchen, replayed. Door, light, temperature and humidity are the recordings of one house
  (UMass Smart* Home A, May-July 2012, see tools/extract_smart_home_a.py). Its coffee maker was only metered
  on 17 days; on the other days a recorded use is replayed at a recorded start time.

A snapshot is written whenever something changes and every `interval_s` (5 minutes), so no device goes
stale and ingest sees no gaps.
"""
import argparse
import copy
import gzip
import json
import math
import random
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path

from .clock import TZ

DATA = Path(__file__).parent / "sim_data"
REAL_HOME = "home-a"
DEFAULT_START = date(2026, 9, 1)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
VOLTAGE = 230
CLIMATE_STEP_S = 300
TEMP_REPORT_STEP, HUMIDITY_REPORT_STEP = 0.2, 1.0  # °C and %RH

# (epoch ms, device type, capability, value). Devices are named by type: ids change on re-pairing.
Change = tuple[int, str, str, object]


@dataclass
class Household:
    """A home's habits. Times are "HH:MM" local; a spread is one standard deviation, in minutes."""
    name: str = "rosa"
    wake: str = "07:45"
    wake_spread_min: float = 15
    weekend_shift_min: float = 45  # how much later the day starts on Saturday and Sunday
    bed: str = "23:10"  # earlier than `wake` means after midnight
    bed_spread_min: float = 20
    away_chance: float = 0.05  # a whole day with nobody home
    door_after_wake_chance: float = 0.8  # the door opens a few minutes after waking
    appliance: str = "kettle"  # what is on the plug (a label for the answer key only)
    appliance_watts: float = 2100
    appliance_watts_spread: float = 40
    appliance_minutes: float = 3.0
    appliance_minutes_spread: float = 0.4
    idle_watts: float = 0  # what the plug draws at rest
    morning_use_delay_min: float = 20  # after waking
    morning_use_spread_min: float = 8
    morning_use_chance: float = 0.85
    evening_use: str = ""  # "HH:MM", or empty for none
    evening_use_spread_min: float = 20
    evening_use_chance: float = 0.6
    outing_days: str = "thu"  # weekdays, e.g. "mon,wed,fri"; empty for none
    outing_at: str = "10:00"
    outing_spread_min: float = 10
    outing_minutes: float = 120
    outing_chance: float = 0.9
    extra_door_openings: float = 1.0  # per day, at random times
    lamp_evening_on: str = "18:30"
    # Rarer rhythms and parties (docs/habits-plan.md, part D), all off by default. Each one varies and is sometimes
    # skipped, and none happens while nobody is home or asleep. `household_from_dict` reads a setting as its
    # default's type, so these float defaults are written as floats (0.0, not 0).
    visit_every_days: int = 0  # 14: a visit every other week, on the weekday of `visit_anchor`; 0 for none
    visit_anchor: str = ""  # one visit's day, e.g. "2026-09-05"; the others are every `visit_every_days` from it
    visit_at: str = "15:00"  # they arrive: door, lamp on, the appliance a little later; door and lamp off as they go
    visit_spread_min: float = 20.0
    visit_minutes: float = 120.0
    visit_chance: float = 0.9
    monthly_on: str = ""  # an outing a month: "first sun", "2nd sat", "last fri", or a day like "15"; empty for none
    monthly_at: str = "12:30"
    monthly_spread_min: float = 20.0
    monthly_minutes: float = 240.0
    monthly_chance: float = 0.9
    hourly_every_h: float = 0.0  # the appliance every so many hours (tea, a medicine), e.g. 3; 0 for none
    hourly_from: str = "08:00"  # the first time of the day
    hourly_until: str = "20:00"  # the last time of the day, included
    hourly_spread_min: float = 10.0
    hourly_chance: float = 0.9  # for each time on its own
    parties_per_30_days: float = 0.0  # evenings with friends: many door openings, lamp on late, late to bed; 0: none
    party_days: str = "fri,sat"  # parties fall on these days (15 % on whatever day they are due); empty: any day
    party_at: str = "20:00"  # the first guests arrive
    party_spread_min: float = 20.0
    party_minutes: float = 270.0  # until the last guests leave; bed a little after
    party_lie_in_min: float = 90.0  # how much later the next morning starts
    temp_base: float = 20.5
    temp_swing: float = 1.5  # between the coolest and warmest time of day, each side of the base
    humidity_base: float = 55
    # For the learners' tests; empty by default. Times are local.
    holidays: list = field(default_factory=list)  # "2026-09-20..2026-09-26" or "2026-09-20": nobody home
    outages: list = field(default_factory=list)  # "2026-09-10T22:00..2026-09-11T08:00": no snapshots at all
    changes: list = field(default_factory=list)  # {"from": "2026-10-01", "wake": "09:00"}: settings from that day


TEST_OPTIONS = ("holidays", "outages", "changes")
FIXED = {"name", *TEST_OPTIONS, "idle_watts", "temp_base", "temp_swing", "humidity_base"}  # `changes` can't change these


PRESETS: dict[str, Household] = {
    "rosa": Household(),
    "couple": Household(
        name="couple", wake="06:50", wake_spread_min=8, weekend_shift_min=90, bed="22:40", bed_spread_min=15,
        away_chance=0.03, door_after_wake_chance=0.2, appliance="coffee machine", appliance_watts=1250,
        appliance_watts_spread=25, appliance_minutes=6, appliance_minutes_spread=0.5, morning_use_delay_min=10,
        morning_use_spread_min=4, morning_use_chance=0.95, outing_days="mon,tue,wed,thu,fri", outing_at="08:05",
        outing_spread_min=7, outing_minutes=570, outing_chance=0.95, extra_door_openings=0.5,
        lamp_evening_on="19:00", temp_base=19.5),
    "students": Household(
        name="students", wake="10:15", wake_spread_min=40, weekend_shift_min=60, bed="01:30", bed_spread_min=45,
        away_chance=0.1, door_after_wake_chance=0.3, appliance="games console", appliance_watts=160,
        appliance_watts_spread=10, appliance_minutes=150, appliance_minutes_spread=40, idle_watts=8,
        morning_use_chance=0, evening_use="21:00", evening_use_spread_min=40, evening_use_chance=0.75,
        outing_days="mon,tue,wed,thu,fri", outing_at="13:30", outing_spread_min=30, outing_minutes=240,
        outing_chance=0.8, extra_door_openings=3, lamp_evening_on="19:00", temp_base=21.5, humidity_base=60),
    # rosa, with the rhythms beyond daily and weekly: her grandchildren every other Saturday afternoon, lunch at her
    # daughter's on the first Sunday of the month, tea every 3 hours, and friends to dinner 3 or 4 times a month.
    "rosa-plus": Household(
        name="rosa-plus", visit_every_days=14, visit_anchor="2026-09-05", monthly_on="first sun",
        hourly_every_h=3, hourly_from="11:00", hourly_until="20:00", parties_per_30_days=3.5),
}


# What a made-up house may have on its plug: (label, watts, minutes, watts at rest, used in the evening).
APPLIANCES = (
    ("kettle", 2100, 3, 0, False), ("coffee machine", 1250, 6, 0, False), ("toaster", 900, 2.5, 0, False),
    ("microwave", 1100, 4, 2, False), ("hair dryer", 1600, 8, 0, False), ("television", 120, 150, 6, True),
    ("games console", 160, 120, 8, True), ("electric blanket", 90, 60, 0, True),
)


def random_household(rng: random.Random, name: str) -> Household:
    """A made-up home: every call with a fresh `rng` state gives different people with different habits."""
    def clock(minutes: float) -> str:
        return f"{int(minutes) // 60 % 24:02d}:{int(minutes) % 60:02d}"

    appliance, watts, minutes, idle, evening = rng.choice(APPLIANCES)
    wake = rng.uniform(6 * 60, 10 * 60)
    outing = rng.choice(("", "", "thu", "sat", "tue,fri", "mon,wed,fri", "mon,tue,wed,thu,fri"))
    at_work = outing.count(",") == 4
    return Household(
        name=name, wake=clock(wake), wake_spread_min=round(rng.uniform(5, 30)),
        weekend_shift_min=round(rng.uniform(0, 100)), bed=clock(rng.uniform(22 * 60, 25.5 * 60)),
        bed_spread_min=round(rng.uniform(10, 40)), away_chance=round(rng.uniform(0, 0.1), 2),
        door_after_wake_chance=round(rng.uniform(0.1, 0.9), 2), appliance=appliance,
        appliance_watts=round(watts * rng.uniform(0.9, 1.1)), appliance_watts_spread=round(watts * 0.02),
        appliance_minutes=round(minutes * rng.uniform(0.8, 1.25), 1), appliance_minutes_spread=round(minutes * 0.12, 1),
        idle_watts=idle, morning_use_delay_min=round(rng.uniform(8, 40)),
        morning_use_spread_min=round(rng.uniform(3, 10)),
        morning_use_chance=0 if evening else round(rng.uniform(0.7, 0.97), 2),
        evening_use=clock(rng.uniform(19.5 * 60, 21.5 * 60)) if evening or rng.random() < 0.3 else "",
        evening_use_spread_min=round(rng.uniform(10, 40)), evening_use_chance=round(rng.uniform(0.5, 0.9), 2),
        outing_days=outing, outing_at=clock(max(wake + 60, rng.uniform(8 * 60, 14 * 60))),
        outing_spread_min=round(rng.uniform(5, 30)), outing_minutes=round(rng.uniform(420, 560) if at_work else rng.uniform(60, 240)),
        outing_chance=round(rng.uniform(0.75, 0.97), 2), extra_door_openings=round(rng.uniform(0, 4), 1),
        lamp_evening_on=clock(rng.uniform(18 * 60, 19.5 * 60)), temp_base=round(rng.uniform(18.5, 22.5), 1),
        temp_swing=round(rng.uniform(0.8, 2.2), 1), humidity_base=round(rng.uniform(45, 65)))


def houses(count: int, seed: int = 1) -> list[Household]:
    """`count` different made-up homes, named house-1, house-2, ... The same seed gives the same homes."""
    rng = random.Random(f"houses-{seed}")
    return [random_household(rng, f"house-{n}") for n in range(1, count + 1)]


def household_from_dict(values: dict) -> Household:
    """Builds a Household from loose input (a JSON file, a web form). Unknown keys and bad values are errors."""
    h = Household()
    known = {f.name: type(getattr(h, f.name)) for f in fields(Household)}
    whole = {f.name for f in fields(Household) if f.type is int}
    for key, raw in values.items():
        if key not in known:
            raise ValueError(f"unknown household setting: {key}")
        if key in TEST_OPTIONS:
            setattr(h, key, _option_list(key, raw))
            continue
        try:
            setattr(h, key, known[key](raw) if known[key] is not str else str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{key}: {raw!r} is not a number") from None
        if key in whole and getattr(h, key) != float(raw):  # 14.5 days between visits: refused, not rounded
            raise ValueError(f"{key}: {raw!r} is not a whole number")
    for key in ("wake", "bed", "outing_at", "lamp_evening_on", "visit_at", "monthly_at", "hourly_from", "hourly_until",
                "party_at"):
        _minutes(getattr(h, key), key)
    if h.evening_use:
        _minutes(h.evening_use, "evening_use")
    _weekdays(h.outing_days)
    _weekdays(h.party_days, "party_days")
    for key in ("away_chance", "door_after_wake_chance", "morning_use_chance", "evening_use_chance", "outing_chance",
                "visit_chance", "monthly_chance", "hourly_chance"):
        if not 0 <= getattr(h, key) <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
    if h.appliance_watts <= 0 or h.appliance_minutes <= 0 or h.idle_watts < 0:
        raise ValueError("appliance_watts and appliance_minutes must be above 0, idle_watts 0 or more")
    _check_rhythms(h)
    _holidays(h)
    _outages(h)
    _phases(h)
    return h


def _check_rhythms(h: Household) -> None:
    """The rarer rhythms and parties: plain errors for settings that could not happen."""
    if h.visit_every_days < 0:
        raise ValueError("visit_every_days must be 0 (no visits) or more")
    if h.visit_every_days:
        _anchor(h)
    _monthly(h.monthly_on)
    if h.hourly_every_h and h.hourly_every_h < 0.5:
        raise ValueError("hourly_every_h must be 0 (none) or at least 0.5")
    if h.hourly_every_h and _minutes(h.hourly_until) < _minutes(h.hourly_from):
        raise ValueError("hourly_until must not be before hourly_from (the times are within one day)")
    if not 0 <= h.parties_per_30_days <= 10:
        raise ValueError("parties_per_30_days must be between 0 and 10")
    if h.visit_minutes <= 0 or h.monthly_minutes <= 0 or h.party_minutes < 120 or h.party_lie_in_min < 0:
        raise ValueError("visit_minutes and monthly_minutes must be above 0, party_minutes 120 or more "
                         "(the guests come and go a few at a time), party_lie_in_min 0 or more")


def _option_list(key: str, raw) -> list:
    if isinstance(raw, str) and key != "changes":
        raw = [part for part in raw.replace(" ", "").split(",") if part]
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be a list")
    return list(raw)


def _holidays(h: Household) -> set[date]:
    out = set()
    for item in h.holidays:
        first, _, last = str(item).partition("..")
        try:
            a, b = date.fromisoformat(first), date.fromisoformat(last or first)
        except ValueError:
            raise ValueError(f"holidays: {item!r} is not a day or days like 2026-09-20..2026-09-26") from None
        if b < a:
            raise ValueError(f"holidays: {item!r} ends before it starts")
        out.update(a + timedelta(days=i) for i in range((b - a).days + 1))
    return out


def _outages(h: Household) -> list[tuple[int, int]]:
    out = []
    for item in h.outages:
        first, sep, last = str(item).partition("..")
        try:
            a, b = (int(datetime.fromisoformat(t).replace(tzinfo=TZ).timestamp() * 1000) for t in (first, last))
        except ValueError:
            raise ValueError(f"outages: {item!r} is not a stretch like 2026-09-10T22:00..2026-09-11T08:00") from None
        if not sep or b <= a:
            raise ValueError(f"outages: {item!r} must end after it starts")
        out.append((a, b))
    return sorted(out)


def _phases(h: Household) -> list[tuple[date, Household, dict]]:
    """The settings from each change's day on: (from, the household then, what changed), in date order."""
    scalars = {k: v for k, v in asdict(h).items() if k not in TEST_OPTIONS}
    out = []
    for change in sorted(h.changes, key=lambda c: str(c.get("from", "")) if isinstance(c, dict) else ""):
        if not isinstance(change, dict) or "from" not in change:
            raise ValueError(f"changes: {change!r} needs a \"from\" day and the settings that change")
        try:
            day = date.fromisoformat(str(change["from"]))
        except ValueError:
            raise ValueError(f"changes: {change['from']!r} is not a day like 2026-10-01") from None
        settings = {k: v for k, v in change.items() if k != "from"}
        if not settings or set(settings) & FIXED:
            raise ValueError(f"changes: {change!r} must change at least one habit (not {', '.join(sorted(FIXED))})")
        settings_h = household_from_dict(scalars | settings)
        scalars = {k: v for k, v in asdict(settings_h).items() if k not in TEST_OPTIONS}
        out.append((day, settings_h, {k: getattr(settings_h, k) for k in settings}))
    return out


def _minutes(hhmm: str, what: str = "time") -> int:
    try:
        hours, minutes = hhmm.split(":")
        if 0 <= int(hours) < 24 and 0 <= int(minutes) < 60:
            return int(hours) * 60 + int(minutes)
    except ValueError:
        pass
    raise ValueError(f"{what}: {hhmm!r} is not a time like 07:45")


def _weekdays(names: str, what: str = "outing_days") -> set[int]:
    out = set()
    for name in names.replace(" ", "").lower().split(","):
        if name and name[:3] not in WEEKDAYS:
            raise ValueError(f"{what}: {name!r} is not a weekday (use mon, tue, wed, thu, fri, sat, sun)")
        if name:
            out.add(WEEKDAYS.index(name[:3]))
    return out


# --- the rarer rhythms: which days they fall on, and how the answer key names them -------------------------------

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NTH = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "last": -1}
PARTY_ON_ANOTHER_DAY = 0.15  # the chance a party falls on the first free day it is due, whatever the weekday
PARTY_WAIT_DAYS = 2.7  # how long a due party waits for its day, on average: taken off the gap so the count holds


def _anchor(h: Household) -> date:
    try:
        return date.fromisoformat(h.visit_anchor)
    except ValueError:
        raise ValueError(f"visit_anchor: {h.visit_anchor!r} is not a day like 2026-09-05 (one visit's day)") from None


def _monthly(spec: str) -> tuple[int, int | None] | None:
    """"first sun" -> (1, 6), "last fri" -> (-1, 4), "15" -> (15, None) for the 15th, "" -> None."""
    text = " ".join(spec.lower().split())
    if not text:
        return None
    if text.isdigit() and 1 <= int(text) <= 31:
        return int(text), None
    nth, _, weekday = text.partition(" ")
    if nth in NTH and weekday[:3] in WEEKDAYS:
        return NTH[nth], WEEKDAYS.index(weekday[:3])
    raise ValueError(f"monthly_on: {spec!r} is not like \"first sun\", \"last fri\" or a day of the month like \"15\"")


def _visit_day(h: Household, day: date) -> bool:
    return h.visit_every_days > 0 and (day - _anchor(h)).days % h.visit_every_days == 0


def _monthly_day(h: Household, day: date) -> bool:
    """A month without the day (the 31st in September) has no outing."""
    spec = _monthly(h.monthly_on)
    if spec is None:
        return False
    n, weekday = spec
    if weekday is None:
        return day.day == n
    if n == -1:
        return day.weekday() == weekday and (day + timedelta(days=7)).month != day.month
    return day.weekday() == weekday and (day.day - 1) // 7 + 1 == n


def _hhmm(minutes: float) -> str:
    return f"{int(minutes) // 60 % 24:02d}:{int(minutes) % 60:02d}"


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _recurrence(kind: str, text: str, **values) -> dict:
    """The rhythm in the shape of a learned habit's `recurrence` (docs/habits-plan.md, source/learn/recurrence.py),
    with the same sentences, so a test can compare what was found with what was planted."""
    return {"kind": kind, "weekdays": None, "every_n_days": None, "anchor": None, "day_of_month": None,
            "every_n_hours": None, **values, "text": text}


def _visit_rhythm(h: Household) -> tuple[str, dict]:
    """The answer key's label and rhythm: "visit (every other Saturday)". Whole weeks keep the anchor's weekday."""
    n, anchor = h.visit_every_days, _anchor(h)
    day, weekday = DAY_NAMES[anchor.weekday()], [WEEKDAYS[anchor.weekday()]]
    if n == 7:
        return f"visit (every {day})", _recurrence("weekly", f"every {day}", weekdays=weekday)
    if n % 7 == 0:
        text = f"every {({2: 'other', 3: 'third', 4: 'fourth'}).get(n // 7, _ordinal(n // 7))} {day}"
        return f"visit ({text})", _recurrence("every_n_days", text, weekdays=weekday, every_n_days=n,
                                               anchor=anchor.isoformat())
    text = "every other day" if n == 2 else f"every {n} days"
    return f"visit ({text})", _recurrence("every_n_days", text, every_n_days=n, anchor=anchor.isoformat())


def _monthly_rhythm(h: Household, day: date) -> tuple[str, dict]:
    """"monthly outing (first Sunday)" or "monthly outing (the 15th)". For a weekday of the month the anchor is
    `day`, a day it falls on, as a learned habit has it."""
    n, weekday = _monthly(h.monthly_on)
    if weekday is None:
        return (f"monthly outing (the {_ordinal(n)})",
                _recurrence("monthly", f"on the {_ordinal(n)} of the month", day_of_month=n))
    which = f"{({v: k for k, v in NTH.items() if k.isalpha()})[n]} {DAY_NAMES[weekday]}"
    return (f"monthly outing ({which})", _recurrence("monthly", f"on the {which} of the month",
                                                     weekdays=[WEEKDAYS[weekday]], anchor=day.isoformat()))


def _hourly_rhythm(h: Household) -> tuple[str, dict]:
    """"every 3 h, 11:00 to 20:00"; its rhythm says "every 3 hours from 11:00 to 20:00"."""
    n = h.hourly_every_h
    every = int(n) if float(n).is_integer() else n
    first, last = _hhmm(_minutes(h.hourly_from)), _hhmm(_minutes(h.hourly_until))
    sentence = "every hour" if n == 1 else f"every {every} hours" if n == int(n) else f"every {round(n * 60)} minutes"
    return (f"every {every} h, {first} to {last}",
            _recurrence("hourly", f"{sentence} from {first} to {last}", every_n_hours=every))


def _overlaps(start: float, end: float, spans: list[tuple[float, float]], margin: float = 0) -> bool:
    return any(a - margin <= end and start <= b + margin for a, b in spans)


def _at(day: date, seconds: float) -> int:
    """Epoch ms of `seconds` after local midnight of `day`, on the home's wall clock."""
    local = datetime(day.year, day.month, day.day, tzinfo=TZ) + timedelta(seconds=round(seconds))
    return int(local.timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%dT%H:%M:%S")


class _Plan:
    """The changes a household makes, plus an answer key of what was planted (never shown to the learners)."""

    def __init__(self) -> None:
        self.changes: list[Change] = []
        self.truth: list[dict] = []

    def set(self, ts: int, device_type: str, capability: str, value) -> None:
        self.changes.append((ts, device_type, capability, value))

    def note(self, what: str, start: int, end: int | None = None, **extra) -> None:
        self.truth.append({"what": what, "start": _iso(start), **({"end": _iso(end)} if end else {}), **extra})

    def door(self, opened: int, seconds: float) -> None:
        self.set(opened, "contactSensor", "sensorState", "alarm")
        self.set(opened + int(seconds * 1000), "contactSensor", "sensorState", "normal")

    def lamp(self, ts: int, on: bool) -> None:
        self.set(ts, "bulb", "power", "on" if on else "off")

    def burst(self, start: int, points: list[tuple[float, float]]) -> int:
        """Plug power as (seconds after start, watts) points. Returns when it ends."""
        for offset, watts in points:
            self.set(start + int(offset * 1000), "plug", "instantPower", round(watts))
        return start + int(points[-1][0] * 1000)


# --- a household from a config ---------------------------------------------------------------------

def _config_plan(h: Household, start: date, days: int, rng: random.Random,
                 room_rng: random.Random | None = None) -> _Plan:
    """`room_rng` draws the plug's idle wobble and the room's drift. They are drawn after every day's habits,
    so on the habits' `rng` they would differ with the number of days. With their own, day N is the same
    however many days are simulated, and so is what the learners find in it."""
    room_rng = room_rng or rng
    plan = _Plan()
    holidays, phases = _holidays(h), _phases(h)
    busy: list[tuple[int, int]] = []  # when the appliance runs, so the idle wobble stays out of it

    def settings_on(day: date) -> Household:
        current = h
        for since, then, _ in phases:
            if since <= day:
                current = then
        return current

    def use(h: Household, day_start: int, what: str, **extra) -> None:
        watts = max(1.0, rng.gauss(h.appliance_watts, h.appliance_watts_spread))
        seconds = max(30.0, rng.gauss(h.appliance_minutes, h.appliance_minutes_spread) * 60)
        step = 30 if seconds <= 600 else 120
        points = [(0.0, watts)]
        points += [(float(t), watts * (1 + rng.gauss(0, 0.004))) for t in range(step, int(seconds), step)]
        points.append((seconds, h.idle_watts))
        end = plan.burst(day_start, points)
        busy.append((day_start, end))
        plan.note(what, day_start, end, appliance=h.appliance, watts=round(watts), **extra)

    def plug_free(h: Household, at: int) -> bool:
        """Nothing else runs on the plug within 2 minutes of a use starting at `at` (ms): uses never overlap."""
        runs = (h.appliance_minutes + 3 * h.appliance_minutes_spread + 2) * 60_000  # this one, at its longest
        for a, b in reversed(busy):
            if b < at - 86_400_000:
                break  # the days before
            if a - runs <= at <= b + 120_000:
                return False
        return True

    def live(h: Household, day: date, lie_in: float = 0) -> float:
        """One day of `h`. `lie_in`: minutes later than usual the morning starts (after a party).
        Returns the next morning's lie-in: 0 unless friends came tonight."""
        outing_days = _weekdays(h.outing_days)
        wake_min, bed_min = _minutes(h.wake), _minutes(h.bed)
        if bed_min <= wake_min:
            bed_min += 24 * 60  # after midnight
        if rng.random() < h.away_chance:
            plan.note("away all day", _at(day, 0))
            return 0
        friends = party_tonight(h, day)  # after the away check: a party planned for a day away comes later
        shift = h.weekend_shift_min if day.weekday() >= 5 else 0
        wake = (wake_min + shift + rng.gauss(0, h.wake_spread_min)) * 60
        if lie_in:
            wake += lie_in * 60
        bed = max((bed_min + rng.gauss(0, h.bed_spread_min)) * 60, wake + 4 * 3600)
        party = None  # (first guest in, last guest out), seconds after midnight; bed once they have gone
        if friends:
            arrive = max((_minutes(h.party_at) + rng.gauss(0, h.party_spread_min)) * 60, wake + 2 * 3600)
            party = (arrive, arrive + h.party_minutes * 60 * rng.uniform(0.85, 1.15))
            bed = max(bed, party[1] + rng.uniform(15, 40) * 60)
        plan.note("wake", _at(day, wake), **({"after": "party"} if lie_in else {}))
        plan.note("bed", _at(day, bed), **({"after": "party"} if party else {}))

        plan.lamp(_at(day, wake), True)
        plan.lamp(_at(day, wake + rng.uniform(30, 60) * 60), False)

        if rng.random() < h.door_after_wake_chance:
            opened = _at(day, wake + rng.uniform(5, 12) * 60)
            plan.door(opened, rng.uniform(20, 90))
            plan.note("door after waking", opened)
        if rng.random() < h.morning_use_chance:
            delay = max(2.0, rng.gauss(h.morning_use_delay_min, h.morning_use_spread_min))
            use(h, _at(day, wake + delay * 60), "morning use")
        if h.evening_use and rng.random() < h.evening_use_chance:
            at = (_minutes(h.evening_use) + rng.gauss(0, h.evening_use_spread_min)) * 60
            if wake < at < bed - 600:
                use(h, _at(day, at), "evening use")

        out = back = None
        if day.weekday() in outing_days and rng.random() < h.outing_chance:
            leave = max((_minutes(h.outing_at) + rng.gauss(0, h.outing_spread_min)) * 60, wake + 45 * 60)
            ret = leave + h.outing_minutes * 60 * rng.uniform(0.9, 1.1)
            if ret < bed - 1800 and (party is None or ret < party[0] - 1800):
                out, back = leave, ret
                plan.door(_at(day, out), rng.uniform(15, 45))
                plan.door(_at(day, back), rng.uniform(15, 45))
                plan.note("outing", _at(day, out), _at(day, back))
        absent = [] if out is None else [(out, back)]  # nobody home: no door, no appliance, nobody at the lamp

        if _monthly_day(h, day):
            going = rng.random() < h.monthly_chance
            leave = max((_minutes(h.monthly_at) + rng.gauss(0, h.monthly_spread_min)) * 60, wake + 45 * 60)
            ret = leave + h.monthly_minutes * 60 * rng.uniform(0.9, 1.1)
            if going and ret < bed - 1800 and not _overlaps(leave, ret, absent, 1800):
                plan.door(_at(day, leave), rng.uniform(15, 45))
                plan.door(_at(day, ret), rng.uniform(15, 45))
                what, rhythm = _monthly_rhythm(h, day)
                plan.note(what, _at(day, leave), _at(day, ret), recurrence=rhythm)
                absent.append((leave, ret))

        openings = int(h.extra_door_openings) + (rng.random() < h.extra_door_openings % 1)
        for _ in range(openings):
            at = rng.uniform(wake + 3600, bed - 3600)
            if not _overlaps(at, at, absent, 300):
                plan.door(_at(day, at), rng.uniform(10, 120))

        lamp_on = (_minutes(h.lamp_evening_on) + rng.gauss(0, 15)) * 60
        for a, b in sorted(absent):
            if a <= lamp_on <= b:
                lamp_on = b + 120  # nobody home to switch it on
        if party:
            lamp_on = min(lamp_on, party[0])  # on for the guests, until bed
        if lamp_on < bed - 300:
            plan.lamp(_at(day, lamp_on), True)
        plan.lamp(_at(day, bed), False)

        lie_in_next = 0.0
        if party:
            party_night(h, day, *party)
            lie_in_next = h.party_lie_in_min * rng.uniform(0.75, 1.25)
        if _visit_day(h, day):
            visit(h, day, wake, bed, absent, lamp_on if lamp_on < bed - 300 else None)
        if h.hourly_every_h > 0:
            hourly(h, day, wake, bed, absent)
        return lie_in_next

    def party_night(h: Household, day: date, arrive: float, leave: float) -> None:
        """Friends to dinner: they arrive a few minutes apart, someone steps out now and then, the appliance runs
        once or more, and they leave a few minutes apart. The lamp and the late bed are `live`'s."""
        opened, at = [], arrive
        for _ in range(rng.randint(4, 7)):
            opened.append(at)
            at += rng.uniform(1, 7) * 60
        settled, going = at, [leave]
        for _ in range(rng.randint(1, 3)):
            going.insert(0, going[0] - rng.uniform(2, 10) * 60)
        for _ in range(rng.randint(0, 2)):
            at = rng.uniform(settled + 600, going[0] - 600)
            if settled + 600 < going[0] - 600 and all(abs(at - t) > 120 for t in opened):
                opened.append(at)
        opened += going
        for at in opened:
            plan.door(_at(day, at), rng.uniform(5, 30))
        uses = 0
        for _ in range(rng.randint(1, 3)):
            at = _at(day, rng.uniform(arrive + 15 * 60, leave - 30 * 60))
            if plug_free(h, at):
                use(h, at, "party use")
                uses += 1
        plan.note("party", _at(day, arrive), _at(day, leave), door_openings=len(opened), plug_uses=uses)

    def visit(h: Household, day: date, wake: float, bed: float, absent: list, evening_lamp: float | None) -> None:
        """Visitors: door, the lamp on, the appliance a little later (tea), door again as they go and the lamp off,
        unless the evening has begun. Only while someone is home and awake."""
        coming = rng.random() < h.visit_chance
        arrive = (_minutes(h.visit_at) + rng.gauss(0, h.visit_spread_min)) * 60
        leave = arrive + h.visit_minutes * 60 * rng.uniform(0.8, 1.2)
        if not coming or arrive < wake + 3600 or leave > bed - 1800 or _overlaps(arrive, leave, absent, 600):
            return
        plan.door(_at(day, arrive), rng.uniform(20, 60))
        plan.lamp(_at(day, arrive + rng.uniform(1, 3) * 60), True)
        tea = _at(day, arrive + rng.uniform(8, 20) * 60)
        if plug_free(h, tea):
            use(h, tea, "visit use")
        plan.door(_at(day, leave), rng.uniform(20, 60))
        if evening_lamp is None or leave + 300 < evening_lamp:
            plan.lamp(_at(day, leave + rng.uniform(1, 3) * 60), False)
        what, rhythm = _visit_rhythm(h)
        plan.note(what, _at(day, arrive), _at(day, leave), recurrence=rhythm)

    def hourly(h: Household, day: date, wake: float, bed: float, absent: list) -> None:
        """The appliance every `hourly_every_h` hours at about the same clock times, whenever someone is home and
        awake and the plug is free; each time can be skipped on its own."""
        what, rhythm = _hourly_rhythm(h)
        first, step = _minutes(h.hourly_from), h.hourly_every_h * 60
        for k in range(int((_minutes(h.hourly_until) - first) // step) + 1):
            due = rng.random() < h.hourly_chance
            at = (first + k * step + rng.gauss(0, h.hourly_spread_min)) * 60
            home = wake + 600 <= at <= bed - 600 and not _overlaps(at, at, absent, 300)
            if due and home and plug_free(h, _at(day, at)):
                use(h, _at(day, at), what, recurrence=rhythm)

    next_party: date | None = None  # planned the day the party before it happens (or the first day)

    def party_free(h: Household, day: date) -> bool:
        return not _visit_day(h, day) and not _monthly_day(h, day)  # one thing at a time, for a clear answer key

    def plan_party(h: Household, after: date, first: bool = False) -> date:
        """The next party's day: about 30 / `parties_per_30_days` days on (the wait for its day is taken off the
        gap, so the count holds), on a random free one of `party_days` in the week from there, or now and then on
        the first free day, whichever it is. Free: no visit and no monthly outing."""
        gap = 30 / h.parties_per_30_days - PARTY_WAIT_DAYS
        gap = rng.uniform(0, gap + 3) if first else max(2.0, rng.uniform(0.5, 1.5) * gap)
        week = [after + timedelta(days=round(gap) + k) for k in range(7)]
        free_days = [d for d in week if party_free(h, d)] or week
        preferred = _weekdays(h.party_days, "party_days") or set(range(7))
        liked = [d for d in free_days if d.weekday() in preferred]
        return free_days[0] if rng.random() < PARTY_ON_ANOTHER_DAY or not liked else rng.choice(liked)

    def party_tonight(h: Household, day: date) -> bool:
        """Whether friends come tonight. Decided day by day, and the next party is planned the day one happens, so
        day N never depends on the days after it. A party whose day is missed (away all day) waits for the next
        free day of `party_days`."""
        nonlocal next_party
        if h.parties_per_30_days <= 0:
            next_party = None
            return False
        if next_party is None:
            next_party = plan_party(h, day, first=True)
        preferred = _weekdays(h.party_days, "party_days") or set(range(7))
        if day < next_party or not party_free(h, day) or (day != next_party and day.weekday() not in preferred):
            return False
        next_party = plan_party(h, day)
        return True

    for since, _, changed in phases:
        plan.note("change", _at(since, 0), settings=changed)
    lie_in = 0.0
    for i in range(days):
        day = start + timedelta(days=i)
        changes, truth = len(plan.changes), len(plan.truth)
        lie_in = live(settings_on(day), day, lie_in)  # drawn even on a holiday, so the other days stay exactly the same
        if day in holidays:
            del plan.changes[changes:], plan.truth[truth:]  # `busy` keeps its runs, for the same reason
            plan.note("away (holiday)", _at(day, 0))
            lie_in = 0.0  # no party happened, so no lie-in after it
    for a, b in _outages(h):
        plan.note("outage", a, b)

    first, last = _at(start, 0), _at(start + timedelta(days=days), 0)
    plan.set(first, "plug", "instantPower", round(h.idle_watts))
    if h.idle_watts > 0:  # a plug that idles above zero wobbles a little
        for ts in range(first + 600_000, last, 600_000):
            if not any(a - 60_000 <= ts <= b + 60_000 for a, b in busy):
                plan.set(ts, "plug", "instantPower", round(h.idle_watts) + room_rng.choice((-1, 0, 0, 0, 1)))

    # The room: warmest mid-afternoon, coolest before dawn, with a slow random drift.
    # Like a battery sensor, it only reports once the value has moved by a step.
    drift = damp = 0.0
    sent_temp = sent_humidity = None
    for ts in range(first, last, CLIMATE_STEP_S * 1000):
        local = datetime.fromtimestamp(ts / 1000, TZ)
        hour = local.hour + local.minute / 60
        drift = 0.98 * drift + room_rng.gauss(0, 0.04)
        damp = 0.98 * damp + room_rng.gauss(0, 0.08)
        temp = h.temp_base + h.temp_swing * math.sin(2 * math.pi * (hour - 10) / 24) + drift
        humidity = h.humidity_base - 3 * (temp - h.temp_base) + damp
        if sent_temp is None or abs(temp - sent_temp) >= TEMP_REPORT_STEP:
            sent_temp = round(temp, 1)
            plan.set(ts, "temperatureSensor", "temperatureValue", sent_temp)
        if sent_humidity is None or abs(humidity - sent_humidity) >= HUMIDITY_REPORT_STEP:
            sent_humidity = round(humidity, 1)
            plan.set(ts, "temperatureSensor", "humidityValue", sent_humidity)
    return plan


# --- the real house --------------------------------------------------------------------------------

REAL_FIRST, REAL_LAST = date(2012, 5, 1), date(2012, 7, 30)
REAL_METERED_FROM = date(2012, 5, 31)  # where the coffee maker's meter starts recording


@cache
def _home_a() -> dict:
    with gzip.open(DATA / "home_a.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


def _real_start(sim_start: date, days: int) -> date:
    """The recorded day that stands in for the first simulated day: same weekday, and metered if it fits."""
    for anchor in (REAL_METERED_FROM, REAL_FIRST):
        first = anchor + timedelta(days=(sim_start.weekday() - anchor.weekday()) % 7)
        if first + timedelta(days=days - 1) <= REAL_LAST:
            return first
    raise ValueError(f"{REAL_HOME} has {home_a_max_days(sim_start)} recorded days from a "
                     f"{sim_start.strftime('%A')}; asked for {days}")


def home_a_max_days(sim_start: date = DEFAULT_START) -> int:
    first = REAL_FIRST + timedelta(days=(sim_start.weekday() - REAL_FIRST.weekday()) % 7)
    return (REAL_LAST - first).days + 1


def _home_a_plan(start: date, days: int, rng: random.Random) -> _Plan:
    plan = _Plan()
    recorded = _home_a()["days"]
    real_start = _real_start(start, days)
    # For days the meter missed: recorded uses, and recorded start times for the same kind of day.
    uses = [(date.fromisoformat(d), sec, points) for d, rec in recorded.items()
            for sec, points in (rec["plug"] or [])[:1] if points[-1][0] >= 300]
    starts = {True: [sec for d, sec, _ in uses if d.weekday() == 5],
              False: [sec for d, sec, _ in uses if d.weekday() < 5]}

    first = _at(start, 0)
    plan.set(first, "contactSensor", "sensorState", "normal")
    plan.set(first, "bulb", "power", "off")
    plan.set(first, "plug", "instantPower", 0)
    for i in range(days):
        day, real_day = start + timedelta(days=i), real_start + timedelta(days=i)
        rec = recorded.get(real_day.isoformat())
        if rec is None:
            continue
        for sec, is_open in rec["door"]:
            plan.set(_at(day, sec), "contactSensor", "sensorState", "alarm" if is_open else "normal")
        for sec, dim in rec["light"]:
            ts = _at(day, sec)
            if dim > 0:
                plan.set(ts, "bulb", "brightness", max(1, round(dim * 100)))
            plan.lamp(ts, dim > 0)
        for sec, temp, humidity in rec["climate"]:
            plan.set(_at(day, sec), "temperatureSensor", "temperatureValue", temp)
            plan.set(_at(day, sec), "temperatureSensor", "humidityValue", humidity)
        if rec["plug"] is not None:
            for sec, points in rec["plug"]:
                at = _at(day, sec)
                plan.note("coffee maker", at, plan.burst(at, points), source="recorded")
        elif rec["door"] and day.weekday() < 6:  # somebody is home, and it never ran on a Sunday
            _, _, points = rng.choice(uses)
            at = _at(day, rng.choice(starts[day.weekday() == 5]))
            plan.note("coffee maker", at, plan.burst(at, points), source="recorded use, replayed on an unmetered day")
    plan.note(f"replays {real_start} to {real_start + timedelta(days=days - 1)} of Smart* Home A", first)
    return plan


# --- changes -> snapshots --------------------------------------------------------------------------

@cache
def _template_text() -> str:
    return (DATA / "home_template.json").read_text()


def _template() -> dict:
    """A real get_home snapshot of the kit, set to a quiet home."""
    home = json.loads(_template_text())
    home["security"]["isAlarmed"] = False
    home.get("energy", {})["todayConsumption"] = 0
    quiet = {("contactSensor", "sensorState"): "normal", ("bulb", "power"): "off", ("plug", "instantPower"): 0,
             ("plug", "instantCurrent"): 0, ("plug", "instantVoltage"): VOLTAGE,
             ("plug", "todayConsumption"): 0, ("plug", "yesterdayConsumption"): 0}
    for dev in home["devices"]:
        for cap in dev["capabilities"]:
            if (dev["type"], cap["type"]) in quiet:
                cap["value"] = quiet[(dev["type"], cap["type"])]
    return home


def _lines(changes: list[Change], start_ms: int, end_ms: int, interval_s: int,
           outages: list[tuple[int, int]] = ()) -> Iterator[str]:
    """outages: [start, end) stretches with no snapshot at all; the home carries on underneath."""
    home = _template()
    caps = {(dev["type"], cap["type"]): cap for dev in home["devices"] for cap in dev["capabilities"]}

    # Keep only real changes, grouped by time: a repeated value must not add a snapshot.
    by_ts: dict[int, list[Change]] = {}
    current = {key: cap["value"] for key, cap in caps.items()}
    for change in sorted(changes, key=lambda c: c[0]):
        ts, device_type, capability, value = change
        if (device_type, capability) not in caps:
            raise ValueError(f"the kit has no {device_type}.{capability}")
        if start_ms <= ts < end_ms and current[(device_type, capability)] != value:
            current[(device_type, capability)] = value
            by_ts.setdefault(ts, []).append(change)

    watts, kwh, last_ts, day = 0.0, 0.0, start_ms, None
    for ts in sorted(set(by_ts) | set(range(start_ms, end_ms, interval_s * 1000))):
        kwh += watts * (ts - last_ts) / 3_600_000_000  # energy used since the last snapshot
        last_ts = ts
        today = datetime.fromtimestamp(ts / 1000, TZ).date()
        if day is not None and today != day:  # the plug's counter starts again at local midnight
            caps[("plug", "yesterdayConsumption")]["value"] = round(kwh, 3)
            kwh = 0.0
        day = today
        for _, device_type, capability, value in by_ts.get(ts, ()):
            caps[(device_type, capability)]["value"] = value
            if (device_type, capability) == ("plug", "instantPower"):
                watts = float(value)
                caps[("plug", "instantCurrent")]["value"] = round(watts / VOLTAGE * 1000)  # mA, as the kit reports
        caps[("plug", "todayConsumption")]["value"] = round(kwh, 3)
        home["energy"]["todayConsumption"] = round(kwh, 3)
        for n, dev in enumerate(home["devices"]):
            dev["lastCommunicationTime"] = ts - 1000 * (n + 1)  # every device reported just before the poll
        if any(a <= ts < b for a, b in outages):
            continue
        yield json.dumps({"ts": ts, "home": home}, separators=(",", ":")) + "\n"


def generate(household: Household | str, days: int, *, start: date = DEFAULT_START, seed: int = 1,
             interval_s: int = 300) -> tuple[Iterator[str], list[dict]]:
    """JSONL lines for `days` local days from `start`, and the answer key of what was planted.

    `household` is a Household, a preset name, or "home-a" for the real house. The same arguments always
    give the same lines.
    """
    if days < 1:
        raise ValueError("days must be at least 1")
    if not 60 <= interval_s <= 1800:
        raise ValueError("the snapshot interval must be between 1 and 30 minutes (ingest reports longer gaps)")
    rng = random.Random(seed)
    outages: list[tuple[int, int]] = []
    if household == REAL_HOME:
        plan = _home_a_plan(start, days, rng)
    else:
        if isinstance(household, str):
            if household not in PRESETS:
                raise ValueError(f"unknown household {household!r}: use {', '.join(PRESETS)} or {REAL_HOME}")
            household = PRESETS[household]
        plan = _config_plan(household, start, days, rng, random.Random(f"{seed}-room"))
        outages = _outages(household)
    end_ms = _at(start + timedelta(days=days), 0)
    truth = sorted((t for t in plan.truth if t["start"] < _iso(end_ms)), key=lambda t: t["start"])
    return _lines(plan.changes, _at(start, 0), end_ms, interval_s, outages), truth


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="source.simulate", description="Simulate a household as get_home snapshots")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--household", default="rosa", help=f"{', '.join(PRESETS)}, or {REAL_HOME} (a real house, replayed)")
    p.add_argument("--config", type=Path, help="a JSON file of Household settings, applied on top of --household")
    p.add_argument("--houses", type=int, help="make this many different made-up homes instead; --out is then a folder")
    p.add_argument("--out", type=Path, help="the JSONL file to write, e.g. data/sim/rosa.jsonl")
    p.add_argument("--truth", type=Path, help="also write the answer key (what was planted) as JSON")
    p.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START, help="first day, YYYY-MM-DD")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--interval", type=int, default=300, help="seconds between snapshots when nothing changes")
    p.add_argument("--list", action="store_true", help="show the households and their settings")
    p.add_argument("--serve", action="store_true", help="open the form in the browser instead")
    p.add_argument("--port", type=int, default=4781)
    args = p.parse_args(argv)

    if args.serve:
        from .simulate_web import serve
        serve(args.port)
        return 0
    if args.list:
        for name, h in PRESETS.items():
            print(f"{name}: {json.dumps(asdict(h))}")
        print(f"{REAL_HOME}: a real kitchen replayed (up to {home_a_max_days(args.start)} days), no settings")
        return 0
    if not args.out:
        p.error("--out is required")
    if args.houses:
        try:
            for h in houses(args.houses, args.seed):
                lines, _ = generate(h, args.days, start=args.start, seed=args.seed, interval_s=args.interval)
                _write(lines, args.out / f"{h.name}.jsonl", args.days, args.start)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        household: Household | str = args.household
        if args.config:
            if household == REAL_HOME:
                p.error(f"{REAL_HOME} has no settings")
            base = asdict(PRESETS[household]) if household in PRESETS else {}
            household = household_from_dict(base | json.loads(args.config.read_text()))
        lines, truth = generate(household, args.days, start=args.start, seed=args.seed, interval_s=args.interval)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _write(lines, args.out, args.days, args.start)
    if args.truth:
        args.truth.write_text(json.dumps(truth, indent=1))
    return 0


def _write(lines: Iterator[str], out: Path, days: int, start: date) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    count = size = 0
    with open(out, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line)
            count, size = count + 1, size + len(line)
    print(f"wrote {count} snapshots ({size / 1e6:.1f} MB) to {out}: {days} days from {start}")


if __name__ == "__main__":
    raise SystemExit(main())
