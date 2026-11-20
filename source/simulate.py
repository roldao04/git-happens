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
  parties; "rosa-plus" has them all. With `people` it is a household of people with phones, a thermostat they set
  and three named plugs: "demo", the demo house (docs/contracts.md).
- "home-a": a real kitchen, replayed. Door, light, temperature and humidity are the recordings of one house
  (UMass Smart* Home A, May-July 2012, see tools/extract_smart_home_a.py). Its coffee maker was only metered
  on 17 days; on the other days a recorded use is replayed at a recorded start time.

A snapshot is written whenever something changes and every `interval_s` (5 minutes), so no device goes
stale and ingest sees no gaps.
"""
import argparse
import copy
import gzip
import hashlib
import json
import math
import random
import sys
from collections import Counter
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
    # Rarer rhythms and parties (docs/simulator.md), all off by default. Each one varies and is sometimes
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
    # A household of people (docs/contracts.md, C12), off by default. With `people`, the home has the demo kit
    # (phones on the router, a thermostat, plugs named Kettle, TV and Washing machine) and the room's temperature
    # follows the heating. `wake`, `bed`, the kettle (`appliance_*`, `morning_use_*`), `outing_*` (out together),
    # `extra_door_openings`, `lamp_evening_on` and `party_at`/`party_minutes`/`party_lie_in_min` keep their meaning.
    people: list = field(default_factory=list)  # {"name": "Ana", "phone": "c-8f3a", "leave": "08:10", "home": "18:40", ...}
    start: str = ""  # the first day it is meant to run from, e.g. "2026-09-21"; empty: 2026-09-01
    weekend_wake_spread_min: float = 0.0  # 0: as on weekdays
    gatherings: list = field(default_factory=list)  # evenings with guests: "2026-10-02", ...
    guests_min: int = 5
    guests_max: int = 6
    tv_at: str = "21:00"
    tv_spread_min: float = 15.0
    tv_minutes: float = 120.0
    tv_chance: float = 0.85
    tv_watts: float = 100.0
    washing_days: str = "mon"  # empty for none
    washing_at: str = "19:30"
    washing_spread_min: float = 15.0
    washing_chance: float = 0.9
    heat_home_c: float = 21.0  # what people set when they wake up or get home
    heat_away_c: float = 16.0  # at night, when the last one leaves, and the "eco" setting
    heat_rate_c_per_h: float = 5.0  # how fast the heating warms the room
    heat_loss_per_h: float = 0.035  # the room loses this share of its difference with outside each hour
    heat_gain_per_person_c_per_h: float = 0.05  # people (and guests) warm the room a little
    outside_from: str = ""  # outside temperature falls from `outside_from_c` on this day...
    outside_from_c: float = 18.0
    outside_to_c: float = 10.0  # ...to this, `outside_days` later (and stays there)
    outside_days: int = 60
    outside_swing_c: float = 3.0  # each side of the day's mean: coolest before dawn, warmest mid-afternoon
    scripted: dict = field(default_factory=dict)  # {"2026-11-19": {"Ana home": "18:41"}}: times pinned on a day


TEST_OPTIONS = ("holidays", "outages", "changes")
PEOPLE_OPTIONS = ("people", "gatherings", "scripted")
FIXED = {"name", *TEST_OPTIONS, *PEOPLE_OPTIONS, "start", "idle_watts", "temp_base", "temp_swing",
         "humidity_base"}  # `changes` can't change these


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
    # The demo house (docs/contracts.md, C12): Ana and João, their phones, a thermostat they set, a kettle, a TV and
    # a washing machine, and four Friday evenings with friends. 60 days from Monday 21 Sep: day 60 is Thu 19 Nov.
    "demo": Household(
        name="demo", start="2026-09-21", wake="07:12", wake_spread_min=12, weekend_shift_min=93,
        weekend_wake_spread_min=17, bed="23:15", bed_spread_min=20, away_chance=0, door_after_wake_chance=0.85,
        morning_use_delay_min=4, morning_use_spread_min=2, morning_use_chance=0.95,
        outing_days="sat,sun", outing_at="11:00", outing_spread_min=60, outing_minutes=150, outing_chance=0.5,
        extra_door_openings=0.5, lamp_evening_on="18:30", humidity_base=58,
        party_at="20:00", party_spread_min=10, party_minutes=300, party_lie_in_min=90,
        people=[{"name": "Ana", "phone": "c-8f3a", "leave": "08:10", "home": "18:40"},
                {"name": "João", "phone": "c-2b71", "leave": "08:30", "home": "19:30", "home_at": {"tue": "21:00"},
                 "remote_days": "wed"}],
        gatherings=["2026-10-02", "2026-10-16", "2026-10-30", "2026-11-13"], outside_from="2026-09-21",
        # The demo's moments (DEMO_MOMENTS), pinned so they show what they are about whatever the draws.
        scripted={"2026-11-13": {"gathering": "20:04"}, "2026-11-14": {"wake": "10:08"},
                  "2026-11-18": {"wake": "07:08"}, "2026-11-19": {"Ana home": "18:41"}}),
}

# The demo's moments (docs/contracts.md, C11 and C12): day 1 is the household's start. `planted`: the answer key's
# rows each one is about; the integration fills `focus` with the habits Alfred found for them.
DEMO_MOMENTS = [
    {"id": "weekday-morning", "title": "Waking up, a weekday", "day": 59, "time": "06:30", "planted": ["waking-up"],
     "focus": [], "say": "Wednesday, 06:30: Alfred starts the heating before anyone is up."},
    {"id": "ana-arrives", "title": "Ana gets home", "day": 60, "time": "18:00", "planted": ["ana-home", "warmup"],
     "focus": [], "say": "Ana usually gets home at 18:40: the house is warm when her phone connects."},
    {"id": "gathering", "title": "A Friday gathering, live", "day": 54, "time": "20:30", "planted": ["gathering"],
     "focus": [], "say": "Guests' phones, the door again and again: Alfred recognises a gathering."},
    {"id": "morning-after", "title": "The morning after", "day": 55, "time": "07:45",
     "planted": ["waking-up", "gathering"], "focus": [], "say": "Saturday after a gathering: the warm-up moves later."},
]


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
        if key in TEST_OPTIONS or key == "gatherings":
            setattr(h, key, _option_list(key, raw))
            continue
        if key in PEOPLE_OPTIONS:
            if not isinstance(raw, known[key]):
                raise ValueError(f"{key} must be a {'list' if known[key] is list else 'mapping'}")
            setattr(h, key, copy.deepcopy(raw))
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
    _check_people(h)
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
    """The rhythm in the shape of a learned habit's `recurrence` (docs/contracts.md, source/learn/recurrence.py),
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

    def burst(self, start: int, points: list[tuple[float, float]], plug: str = "plug") -> int:
        """Plug power as (seconds after start, watts) points. Returns when it ends."""
        for offset, watts in points:
            self.set(start + int(offset * 1000), plug, "instantPower", round(watts))
        return start + int(points[-1][0] * 1000)


# --- a household from a config ---------------------------------------------------------------------

def _config_plan(h: Household, start: date, days: int, rng: random.Random,
                 room_rng: random.Random | None = None, writes: list = ()) -> _Plan:
    """`room_rng` draws the plug's idle wobble and the room's drift. They are drawn after every day's habits,
    so on the habits' `rng` they would differ with the number of days. With their own, day N is the same
    however many days are simulated, and so is what the learners find in it."""
    room_rng = room_rng or rng
    if h.people:
        return _people_plan(h, start, days, rng, room_rng, writes)
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


# --- a household of people: phones, a thermostat and named plugs (docs/contracts.md, C12) ------------------------

KETTLE, TV, WASHER = "plug:kettle", "plug:tv", "plug:washing-machine"  # the home has three plugs: named by name
THERMOSTAT, ROUTER = "thermostat", "router"
PERSON = {"name": "", "phone": "", "leave": "08:00", "leave_spread_min": 5.0, "home": "18:30", "home_spread_min": 10.0,
          "work_days": "mon,tue,wed,thu,fri", "remote_days": "", "home_at": {}}
SCRIPT_KEYS = ("wake", "bed", "gathering")  # and "<name> leaves", "<name> home"
HEAT_ON_BELOW, HEAT_OFF_ABOVE = 0.3, 0.2  # the thermostat's hysteresis around its target, °C
PHYSICS_STEP_S = 60
PLURAL = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")


def _slug(name: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


def _device_keys(devices: list[dict]) -> list[str]:
    """How changes name each device: its type, or "<type>:<slug of its name>" when the home has more than one of
    that type (docs/contracts.md, C2), so today's kit keeps its names."""
    count = Counter(d["type"] for d in devices)
    return [d["type"] if count[d["type"]] == 1 else f"{d['type']}:{_slug(d['name'])}" for d in devices]


def _person(raw) -> dict:
    """One person's settings with the defaults filled in. `home_at`: weekdays they get home at another time."""
    if not isinstance(raw, dict) or not str(raw.get("name", "")).strip() or not str(raw.get("phone", "")).strip():
        raise ValueError(f"people: {raw!r} needs a name and a phone (the router's client id, like c-8f3a)")
    unknown = set(raw) - set(PERSON)
    if unknown:
        raise ValueError(f"people: {raw['name']} has unknown settings: {', '.join(sorted(unknown))}")
    p = PERSON | raw
    for key in ("leave", "home"):
        _minutes(str(p[key]), f"{p['name']}'s {key}")
    for key in ("leave_spread_min", "home_spread_min"):
        try:
            p[key] = float(p[key])
        except (TypeError, ValueError):
            raise ValueError(f"{p['name']}'s {key}: {p[key]!r} is not a number") from None
        if p[key] < 0:
            raise ValueError(f"{p['name']}'s {key} must be 0 or more")
    for key in ("work_days", "remote_days"):
        _weekdays(str(p[key]), f"{p['name']}'s {key}")
    if not isinstance(p["home_at"], dict):
        raise ValueError(f"{p['name']}'s home_at must be like {{\"tue\": \"21:00\"}}")
    home_at = {}
    for day, at in p["home_at"].items():
        days = _weekdays(str(day), f"{p['name']}'s home_at")
        if len(days) != 1:
            raise ValueError(f"{p['name']}'s home_at: {day!r} is not one weekday")
        home_at[WEEKDAYS[days.pop()]] = _hhmm(_minutes(str(at), f"{p['name']}'s home_at"))
    return p | {"home_at": home_at}


def _check_people(h: Household) -> None:
    """A household of people: plain errors for settings that could not happen."""
    if not isinstance(h.people, list) or not isinstance(h.scripted, dict):
        raise ValueError("people must be a list and scripted a mapping of days to times")
    for key in ("start", "outside_from"):
        if getattr(h, key):
            try:
                date.fromisoformat(getattr(h, key))
            except ValueError:
                raise ValueError(f"{key}: {getattr(h, key)!r} is not a day like 2026-09-21") from None
    if not h.people:
        if h.gatherings or h.scripted:
            raise ValueError("gatherings and scripted need people")
        return
    people = [_person(p) for p in h.people]
    for key in ("name", "phone"):
        if len({p[key] for p in people}) != len(people):
            raise ValueError(f"people: two people have the same {key}")
    if h.holidays or h.changes:
        raise ValueError("holidays and changes don't work with people yet")
    for day in h.gatherings:
        try:
            date.fromisoformat(str(day))
        except ValueError:
            raise ValueError(f"gatherings: {day!r} is not a day like 2026-10-02") from None
    allowed = {*SCRIPT_KEYS, *(f"{p['name']} {k}" for p in people for k in ("leaves", "home"))}
    for day, pins in h.scripted.items():
        try:
            date.fromisoformat(str(day))
        except ValueError:
            raise ValueError(f"scripted: {day!r} is not a day like 2026-11-19") from None
        if not isinstance(pins, dict) or set(pins) - allowed:
            raise ValueError(f"scripted: {day} pins times by name, of {', '.join(sorted(allowed))}")
        for key, at in pins.items():
            _minutes(str(at), f"scripted {day} {key}")
    _minutes(h.tv_at, "tv_at")
    _minutes(h.washing_at, "washing_at")
    _weekdays(h.washing_days, "washing_days")
    for key in ("tv_chance", "washing_chance"):
        if not 0 <= getattr(h, key) <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
    if not 0 <= h.guests_min <= h.guests_max:
        raise ValueError("guests_min must be 0 or more, and no more than guests_max")
    if not 5 <= h.heat_away_c <= h.heat_home_c <= 30:
        raise ValueError("heat_away_c and heat_home_c must be between 5 and 30 °C, away no warmer than home")
    if h.heat_rate_c_per_h <= 0 or not 0 < h.heat_loss_per_h < 1 or h.heat_gain_per_person_c_per_h < 0:
        raise ValueError("heat_rate_c_per_h must be above 0, heat_loss_per_h between 0 and 1, the gain 0 or more")
    if h.outside_days < 1 or h.tv_minutes <= 0 or h.tv_watts <= 0:
        raise ValueError("outside_days must be 1 or more, tv_minutes and tv_watts above 0")


def start_for(household: "Household | str") -> date:
    """The first day a household is meant to run from: its `start`, or 2026-09-01."""
    h = PRESETS.get(household) if isinstance(household, str) else household
    return date.fromisoformat(h.start) if h is not None and h.start else DEFAULT_START


def _outside_c(h: Household, ts: int, since: date) -> float:
    """Outside: falling from `outside_from_c` to `outside_to_c` over `outside_days`, coolest before dawn."""
    local = datetime.fromtimestamp(ts / 1000, TZ)
    days = (local.date() - since).days + (local.hour * 60 + local.minute) / 1440
    mean = h.outside_from_c + (h.outside_to_c - h.outside_from_c) * min(1.0, max(0.0, days / h.outside_days))
    return mean + h.outside_swing_c * math.sin(2 * math.pi * (local.hour + local.minute / 60 - 9) / 24)


def _guest_id(day: date, n: int) -> str:
    """A guest phone's client id: new for each gathering, the same in every run."""
    return "c-" + hashlib.sha256(f"guest-{day}-{n}".encode()).hexdigest()[:4]


def _days_text(days: list[str]) -> str:
    """["mon", ..., "fri"] -> "weekdays"; ["mon"] -> "Mondays"; ["sat", "sun"] -> "weekends"."""
    names = [d for d in WEEKDAYS if d in days]
    if len(names) == 7:
        return "every day"
    if names == list(WEEKDAYS[:5]):
        return "weekdays"
    if names == ["sat", "sun"]:
        return "weekends"
    words = [PLURAL[WEEKDAYS.index(d)] for d in names]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _window(days: list[str], usual_min: float, spread_min: float) -> dict:
    """A habit's window in the shape of docs/contracts.md C3: usual time, ± 1.5 spreads, to 5 minutes."""
    def five(m: float) -> str:
        return _hhmm(5 * round(m / 5))
    return {"days": [d for d in WEEKDAYS if d in days], "usual": _hhmm(round(usual_min)),
            "earliest": five(usual_min - 1.5 * spread_min), "latest": five(usual_min + 1.5 * spread_min)}


def _row(key: str, kind: str, text: str, *, person: dict | None = None, windows: list | None = None,
         signals: list | None = None, recurrence: dict | None = None, **extra) -> dict:
    return {"key": key, "kind": kind, "planted": text, "person": person, "windows": windows or [],
            "signals": signals or [], "recurrence": recurrence, **extra}


def planted(household: "Household | str", start: date | None = None, days: int | None = None) -> list[dict]:
    """The rhythms planted in a household of people, one row each, in the words of the demo's "planted" column
    (docs/contracts.md, C11): what the learners should find. Each answer key entry of `generate` says which row it
    belongs to (its "planted"). `start` and `days` only count the gatherings in a run. Empty for other households."""
    h = PRESETS[household] if isinstance(household, str) else household
    if not h.people:
        return []
    people = [_person(p) for p in h.people]
    weekdays, weekend, every = list(WEEKDAYS[:5]), ["sat", "sun"], list(WEEKDAYS)
    door = "contactSensor.sensorState"
    rows = []

    wake = _minutes(h.wake)
    on_weekdays = _window(weekdays, wake, h.wake_spread_min)
    at_weekends = _window(weekend, wake + h.weekend_shift_min, h.weekend_wake_spread_min or h.wake_spread_min)
    then = ", then the front door" if h.door_after_wake_chance else ""
    rows.append(_row("waking-up", "habit", f"Waking up: weekdays {on_weekdays['earliest']}–{on_weekdays['latest']} · "
                     f"weekends {at_weekends['earliest']}–{at_weekends['latest']}. Kettle{then}",
                     windows=[on_weekdays, at_weekends], signals=[f"{KETTLE}.instantPower", door],
                     recurrence=_recurrence("daily", "every day")))

    for p in people:
        name, slug, signal = p["name"], _slug(p["name"]), f"phone:{p['phone']}.connected"
        who = {"phone": p["phone"], "name": f"{name}'s phone"}
        work, remote = _weekdays(p["work_days"]), _weekdays(p["remote_days"])
        out = [WEEKDAYS[d] for d in sorted(work - remote)]
        if not out:
            continue
        but = f", not {_days_text([WEEKDAYS[d] for d in sorted(remote & work)])}" if remote & work else ""
        span = _days_text([WEEKDAYS[d] for d in sorted(work)])
        rows.append(_row(f"{slug}-leaves", "habit", f"{name} leaves {span} {p['leave']}{but}", person=who,
                         windows=[_window(out, _minutes(p["leave"]), p["leave_spread_min"])], signals=[door, signal],
                         recurrence=_recurrence("weekly", _days_text(out), weekdays=out)))
        usual = [d for d in out if d not in p["home_at"]]
        others = [d for d in out if d in p["home_at"]]
        windows = [_window(usual, _minutes(p["home"]), p["home_spread_min"])] if usual else []
        windows += [_window([d], _minutes(p["home_at"][d]), p["home_spread_min"]) for d in others]
        extra = "".join(f", {_days_text([d])} {p['home_at'][d]}" for d in others)
        rows.append(_row(f"{slug}-home", "habit", f"{name} home {span} {p['home']}{extra}{but}", person=who,
                         windows=windows, signals=[signal, door],
                         recurrence=_recurrence("weekly", _days_text(out), weekdays=out)))
        if remote & work:
            home_days = [WEEKDAYS[d] for d in sorted(remote & work)]
            rows.append(_row(f"{slug}-at-home", "note", f"{name} works from home on {_days_text(home_days)}",
                             person=who, signals=[signal]))

    if h.tv_chance:
        rows.append(_row("tv", "habit", f"TV evenings {h.tv_at}–{_hhmm(_minutes(h.tv_at) + h.tv_minutes)}",
                         windows=[_window(every, _minutes(h.tv_at), h.tv_spread_min)], signals=[f"{TV}.instantPower"],
                         recurrence=_recurrence("daily", "every day")))
    washing = [WEEKDAYS[d] for d in sorted(_weekdays(h.washing_days))]
    if washing and h.washing_chance:
        rows.append(_row("washing", "habit", f"Washing machine {_days_text(washing)} {h.washing_at}",
                         windows=[_window(washing, _minutes(h.washing_at), h.washing_spread_min)],
                         signals=[f"{WASHER}.instantPower"], recurrence=_recurrence("weekly", _days_text(washing),
                                                                                     weekdays=washing)))
    rows.append(_row("bed", "habit", f"Bed about {h.bed}: lamp off, heating down to {h.heat_away_c:g} °C",
                     windows=[_window(every, _minutes(h.bed), h.bed_spread_min)],
                     signals=["bulb.power", "thermostat.targetTemperature"], recurrence=_recurrence("daily", "every day")))
    outings = [WEEKDAYS[d] for d in sorted(_weekdays(h.outing_days))]
    if outings and h.outing_chance:
        rows.append(_row("outings", "habit", f"Out together on {_days_text(outings)} about {h.outing_at}, "
                         f"{round(h.outing_minutes / 60, 1):g} h, some weeks",
                         windows=[_window(outings, _minutes(h.outing_at), h.outing_spread_min)],
                         signals=[door, *(f"phone:{p['phone']}.connected" for p in people)],
                         recurrence=_recurrence("weekly", _days_text(outings), weekdays=outings)))

    dates = sorted(date.fromisoformat(d) for d in h.gatherings)
    if start is not None and days is not None:
        dates = [d for d in dates if start <= d < start + timedelta(days=days)]
    if dates:
        weekday = {d.weekday() for d in dates}
        when = f"{DAY_NAMES[weekday.pop()]} night" if len(weekday) == 1 else "Evening"
        rows.append(_row("gathering", "occasion",
                         f"{when} gathering, {len(dates)} times: guests' phones, door often, late night, "
                         f"next morning ~{round(h.party_lie_in_min)} min later",
                         signals=["router.guestPhones", door], dates=[d.isoformat() for d in dates],
                         guest_phones=h.guests_max, usual_hours=[h.party_at, _hhmm(_minutes(h.party_at) + h.party_minutes)],
                         lie_in_min=h.party_lie_in_min))

    rows.append(_row("heating", "rule", f"Heating: {h.heat_home_c:g} °C when someone gets home or wakes up, "
                     f"{h.heat_away_c:g} °C at night and when the last one leaves",
                     signals=["thermostat.targetTemperature"]))
    outside = (h.outside_from_c + h.outside_to_c) / 2
    net = h.heat_rate_c_per_h - h.heat_loss_per_h * (h.heat_home_c - 2 - outside)  # about 19 °C, a mean day outside
    rows.append(_row("warmup", "warmup", f"The house warms 1 °C in about {round(60 / net)} min",
                     signals=["thermostat.temperatureValue", "thermostat.heatingState"],
                     rate_c_per_h=round(net, 2), loss_c_per_h=round(h.heat_loss_per_h * (h.heat_home_c - 2 - outside), 2)))
    return rows


def _people_plan(h: Household, start: date, days: int, rng: random.Random, room_rng: random.Random,
                 writes: list = ()) -> _Plan:
    """Each person's day (up, out to work, home, bed) and the evenings with guests, drawn day by day; then the
    heating and the room minute by minute: people set the thermostat, the physics answers.
    `writes`: (epoch ms, "targetTemperature" | "mode", value), someone else at the thermostat (Alfred)."""
    plan = _Plan()
    rows = {r["key"]: r["planted"] for r in planted(h)}
    people = [_person(p) for p in h.people]
    names = [p["name"] for p in people]
    by_name = {p["name"]: p for p in people}
    gatherings = {date.fromisoformat(d) for d in h.gatherings}
    scripted = {date.fromisoformat(d): pins for d, pins in h.scripted.items()}
    outing_days, washing_days = _weekdays(h.outing_days), _weekdays(h.washing_days)
    phones: list[tuple[int, str, bool]] = []  # (ms, client id, connected)
    guests: list[str] = []  # guest phones, in the order they first came
    settings: list[tuple[int, float, str, str]] = []  # (ms, target °C, who, why): people at the thermostat
    crowd: list[tuple[int, int]] = []  # (ms, +n or -n): people in the house, for their warmth

    def kettle(at: int, what: str) -> None:
        watts = max(1.0, rng.gauss(h.appliance_watts, h.appliance_watts_spread))
        seconds = max(30.0, rng.gauss(h.appliance_minutes, h.appliance_minutes_spread) * 60)
        points = [(0.0, watts)] + [(float(t), watts * (1 + rng.gauss(0, 0.004))) for t in range(30, int(seconds), 30)]
        end = plan.burst(at, points + [(seconds, 0)], KETTLE)
        plan.note(what, at, end, appliance="kettle", watts=round(watts), planted=rows["waking-up"])

    def live(day: date, lie_in: float) -> float:
        """One day. `lie_in`: minutes later than usual the morning starts (after a gathering). Every draw is made
        whatever happens, so a time pinned in `scripted` leaves the other days alone. Returns the next lie-in."""
        pins = scripted.get(day, {})

        def pin(key: str, seconds: float) -> float:
            return _minutes(pins[key]) * 60 if key in pins else seconds

        wd, weekend = day.weekday(), day.weekday() >= 5
        spread = (h.weekend_wake_spread_min or h.wake_spread_min) if weekend else h.wake_spread_min
        spread /= 2 if lie_in else 1  # after a late night the morning is late, and less varied
        wake = pin("wake", (_minutes(h.wake) + (h.weekend_shift_min if weekend else 0) + rng.gauss(0, spread)
                            + lie_in) * 60)
        bed_min = _minutes(h.bed) + (24 * 60 if _minutes(h.bed) <= _minutes(h.wake) else 0)
        bed = (bed_min + rng.gauss(0, h.bed_spread_min)) * 60
        riser = rng.choice(names)
        set_at_wake = rng.uniform(0.5, 3) * 60
        kettle_go = rng.random() < h.morning_use_chance
        kettle_at = wake + max(1.0, rng.gauss(h.morning_use_delay_min, h.morning_use_spread_min)) * 60
        door_go = rng.random() < h.door_after_wake_chance
        door_at, door_s = kettle_at + rng.uniform(15, 25) * 60, rng.uniform(20, 90)
        lamp_off = wake + rng.uniform(40, 70) * 60

        trips = {}  # name -> (leave, back), seconds after midnight: out to work
        for p in people:
            leave = pin(f"{p['name']} leaves", (_minutes(p["leave"]) + rng.gauss(0, p["leave_spread_min"])) * 60)
            back = (_minutes(p["home_at"].get(WEEKDAYS[wd], p["home"])) + rng.gauss(0, p["home_spread_min"])) * 60
            back = pin(f"{p['name']} home", back)
            if wd in _weekdays(p["work_days"]) and wd not in _weekdays(p["remote_days"]):
                trips[p["name"]] = (max(leave, wake + 25 * 60), back)
        if trips and not weekend:
            riser = min(trips, key=lambda n: trips[n][0])  # who leaves first is up first

        gathering = None
        if day in gatherings:
            arrive = pin("gathering", (_minutes(h.party_at) + rng.gauss(0, h.party_spread_min)) * 60)
            gathering = (arrive, arrive + h.party_minutes * 60 * rng.uniform(0.92, 1.08))
            bed = max(bed, gathering[1] + rng.uniform(15, 40) * 60)
        if "bed" in pins:
            bed = _minutes(pins["bed"]) * 60 + (86400 if _minutes(pins["bed"]) * 60 < wake else 0)
        bed = max(bed, *(back + 1800 for _, back in trips.values()), wake + 4 * 3600)

        out_go = rng.random() < h.outing_chance
        out_at = max((_minutes(h.outing_at) + rng.gauss(0, h.outing_spread_min)) * 60, wake + 45 * 60)
        out_back = out_at + h.outing_minutes * 60 * rng.uniform(0.8, 1.2)
        together = (wd in outing_days and out_go and not trips and out_back < bed - 3600
                    and (gathering is None or out_back < gathering[0] - 3600))

        # Out and back: the door, the phones (they leave the WiFi a minute or two after the door, and join it a
        # little before), and the thermostat when the house empties or fills.
        moves = [(at, kind, [name]) for name, (leave, back) in trips.items()
                 for at, kind in ((leave, "leave"), (back, "home"))]
        if together:
            moves += [(out_at, "leave", names), (out_back, "home", names)]
        home, empty, since = set(names), [], 0.0  # empty: (from, to) while nobody is home
        for at, kind, who in sorted(moves, key=lambda m: m[0]):
            seconds, lag, fiddle = rng.uniform(15, 45), rng.uniform(30, 120), rng.uniform(30, 120)
            if kind == "leave":
                home -= set(who)
                if not home:
                    settings.append((_at(day, at - fiddle), h.heat_away_c, who[0], "last out"))
                    since = at
                plan.door(_at(day, at), seconds)
                for name in who:
                    phones.append((_at(day, at + lag), by_name[name]["phone"], False))
            else:
                for name in who:
                    phones.append((_at(day, at - lag), by_name[name]["phone"], True))
                plan.door(_at(day, at), seconds)
                if not home:
                    settings.append((_at(day, at + fiddle), h.heat_home_c, who[0], "home"))
                    empty.append((since, at))
                home |= set(who)
            crowd.append((_at(day, at), len(who) if kind == "home" else -len(who)))
        for name, (leave, back) in trips.items():
            p = by_name[name]
            plan.note("leave", _at(day, leave), person=name, phone=p["phone"], planted=rows[f"{_slug(name)}-leaves"])
            plan.note("home", _at(day, back), person=name, phone=p["phone"], planted=rows[f"{_slug(name)}-home"])
        if together:
            plan.note("out together", _at(day, out_at), _at(day, out_back), people=names, planted=rows["outings"])

        # The morning: up, the lamp, the heating, the kettle, then the door on weekdays.
        plan.note("wake", _at(day, wake), person=riser, planted=rows["waking-up"],
                  **({"after": "gathering"} if lie_in else {}))
        plan.lamp(_at(day, wake), True)
        plan.lamp(_at(day, min([lamp_off] + [a for a, _ in empty])), False)
        settings.append((_at(day, wake + set_at_wake), h.heat_home_c, riser, "wake"))
        if kettle_go:
            kettle(_at(day, kettle_at), "kettle")
        first_out = min((leave for leave, _ in trips.values()), default=None)
        if not weekend and door_go and (first_out is None or door_at + door_s < first_out - 180):
            plan.door(_at(day, door_at), door_s)
            plan.note("door after waking", _at(day, door_at), planted=rows["waking-up"])

        # The evening: the lamp, the TV, the washing machine, guests, bed.
        evening = (_minutes(h.lamp_evening_on) + rng.gauss(0, 15)) * 60
        for a, b in empty:
            if a <= evening <= b:
                evening = b + 90  # nobody home to switch it on
        if gathering:
            evening = min(evening, gathering[0])
        if evening < bed - 300:
            plan.lamp(_at(day, evening), True)

        tv_go = rng.random() < h.tv_chance
        tv_at = (_minutes(h.tv_at) + rng.gauss(0, h.tv_spread_min)) * 60
        tv_end = min(tv_at + h.tv_minutes * 60 * rng.uniform(0.8, 1.1), bed - 300)
        if tv_go and not gathering and tv_end - tv_at > 1800 and not _overlaps(tv_at, tv_end, empty):
            points, t = [], 0.0
            while t < tv_end - tv_at:
                points.append((t, h.tv_watts * (1 + rng.gauss(0, 0.04))))
                t += rng.uniform(120, 240)
            end = plan.burst(_at(day, tv_at), points + [(tv_end - tv_at, 0)], TV)
            plan.note("tv", _at(day, tv_at), end, planted=rows["tv"])

        wash_go = rng.random() < h.washing_chance
        wash_at = (_minutes(h.washing_at) + rng.gauss(0, h.washing_spread_min)) * 60
        if wd in washing_days and wash_go and wake + 3600 < wash_at < bed - 1800 and not _overlaps(wash_at, wash_at, empty):
            heat = rng.gauss(2000, 40)  # the water first: 2 kW for 15 minutes, then the drum turning, in pulses
            points = [(float(t), heat * (1 + rng.gauss(0, 0.005))) for t in range(0, 900, 30)]
            t, stop = 900.0, 900 + 3600 * rng.uniform(0.9, 1.1)
            while t < stop:
                points.append((t, rng.uniform(200, 500)))
                t += rng.uniform(60, 150)
                points.append((t, rng.uniform(3, 8)))
                t += rng.uniform(20, 60)
            end = plan.burst(_at(day, wash_at), points + [(t, 0)], WASHER)
            plan.note("washing machine", _at(day, wash_at), end, planted=rows["washing"])

        lie_in_next = 0.0
        if gathering:
            evening_with_guests(day, *gathering)
            lie_in_next = h.party_lie_in_min * rng.uniform(0.85, 1.15)

        plan.lamp(_at(day, bed), False)
        settings.append((_at(day, bed - rng.uniform(1, 3) * 60), h.heat_away_c, rng.choice(names), "night"))
        plan.note("bed", _at(day, bed), planted=rows["bed"], **({"after": "gathering"} if gathering else {}))

        openings = int(h.extra_door_openings) + (rng.random() < h.extra_door_openings % 1)
        for _ in range(openings):
            at = rng.uniform(wake + 3600, bed - 3600)
            if not _overlaps(at, at, empty, 300):
                plan.door(_at(day, at), rng.uniform(10, 120))
        return lie_in_next

    def evening_with_guests(day: date, arrive: float, end: float) -> None:
        """Guests arrive a few minutes apart (now and then two together), their phones join the WiFi, someone
        steps out now and then, and they leave in two or three groups. No TV tonight."""
        n = rng.randint(h.guests_min, h.guests_max)
        ids = [_guest_id(day, k) for k in range(n)]
        guests.extend(ids)
        opened, at, k = [], arrive, 0
        while k < n:
            size = 2 if k + 1 < n and rng.random() < 0.3 else 1
            for client in ids[k:k + size]:
                phones.append((_at(day, at - rng.uniform(20, 90)), client, True))
            crowd.append((_at(day, at), size))
            opened.append(at)
            k, at = k + size, at + rng.uniform(2, 9) * 60
        settled = at
        groups = rng.randint(2, 3) if n > 1 else 1
        cuts = [round(n * g / groups) for g in range(groups + 1)]
        going = sorted(end - g * rng.uniform(5, 15) * 60 for g in range(groups))
        for t, (a, b) in zip(going, zip(cuts, cuts[1:])):
            opened.append(t)
            crowd.append((_at(day, t), -(b - a)))
            for client in ids[a:b]:
                phones.append((_at(day, t + rng.uniform(60, 150)), client, False))
        for _ in range(rng.randint(2, 4)):
            t = rng.uniform(settled + 600, going[0] - 600)
            if all(abs(t - o) > 120 for o in opened):
                opened.append(t)
        for t in opened:
            plan.door(_at(day, t), rng.uniform(5, 30))
        if rng.random() < 0.5:  # tea before they go
            kettle(_at(day, rng.uniform(going[0] - 90 * 60, going[0] - 30 * 60)), "kettle (gathering)")
        plan.note("gathering", _at(day, arrive), _at(day, end), guests=n, guest_phones=ids, door_openings=len(opened),
                  planted=rows["gathering"])

    lie_in = 0.0
    for i in range(days):
        lie_in = live(start + timedelta(days=i), lie_in)

    first, last = _at(start, 0), _at(start + timedelta(days=days), 0)
    for plug in (KETTLE, TV, WASHER):
        plan.set(first, plug, "instantPower", 0)
    plan.set(first, "bulb", "power", "off")
    plan.set(first, "contactSensor", "sensorState", "normal")

    # The router: everyone's phone at home at midnight, then each join and leave.
    known = [{"id": p["phone"], "name": f"{p['name']}'s phone", "kind": "phone", "known": True} for p in people]
    online = {p["phone"] for p in people}

    def clients() -> list[dict]:
        return ([c for c in known if c["id"] in online]
                + [{"id": g, "name": "", "kind": "phone", "known": False} for g in guests if g in online])

    plan.set(first, ROUTER, "connectedClients", clients())
    for ms, client, on in sorted(phones, key=lambda c: c[0]):
        (online.add if on else online.discard)(client)
        plan.set(ms, ROUTER, "connectedClients", clients())

    # The heating and the room, minute by minute: dT/dt = heat × rate + people × gain − (T − outside) × loss.
    acts = sorted([(ms, "targetTemperature", value, who, why) for ms, value, who, why in settings]
                  + [(int(ms), cap, value, None, None) for ms, cap, value in writes], key=lambda a: a[0])
    crowd.sort()
    since = date.fromisoformat(h.outside_from) if h.outside_from else start
    target, mode, heating, temp, inside = h.heat_away_c, "heat", False, h.heat_away_c + 3, len(people)
    plan.set(first, THERMOSTAT, "targetTemperature", target)
    plan.set(first, THERMOSTAT, "mode", mode)
    plan.set(first, THERMOSTAT, "heatingState", "off")
    drift = damp = 0.0
    sent_temp = sent_sensor = sent_humidity = None
    a = c = 0
    for ts in range(first, last, PHYSICS_STEP_S * 1000):
        while a < len(acts) and acts[a][0] <= ts:
            ms, cap, value, who, why = acts[a]
            a += 1
            if who is None:  # someone else (Alfred): applied as it comes
                if cap == "mode":
                    mode = value
                else:
                    target = float(value)
                plan.set(ms, THERMOSTAT, cap, value)
            elif value != target or mode != "heat":  # a person: only when it changes something
                target, mode = value, "heat"
                plan.set(ms, THERMOSTAT, "targetTemperature", value)
                plan.set(ms, THERMOSTAT, "mode", "heat")
                plan.note("heating set", ms, value=value, by=who, why=why, planted=rows["heating"])
        while c < len(crowd) and crowd[c][0] <= ts:
            inside += crowd[c][1]
            c += 1
        setpoint = target if mode == "heat" else h.heat_away_c if mode == "eco" else None
        want = setpoint is not None and (temp < setpoint + HEAT_OFF_ABOVE if heating
                                         else temp <= setpoint - HEAT_ON_BELOW)
        if want != heating:
            heating = want
            plan.set(ts, THERMOSTAT, "heatingState", "on" if heating else "off")
        temp += (h.heat_rate_c_per_h * heating + h.heat_gain_per_person_c_per_h * max(0, inside)
                 - h.heat_loss_per_h * (temp - _outside_c(h, ts, since))) * PHYSICS_STEP_S / 3600
        # The thermostat and the sensor read the same room; the sensor wobbles a little and reports by steps.
        drift = 0.98 * drift + room_rng.gauss(0, 0.01)
        damp = 0.98 * damp + room_rng.gauss(0, 0.08)
        sensor, humidity = temp + drift, h.humidity_base - 2 * (temp - 20) + damp
        if sent_temp is None or abs(temp - sent_temp) >= TEMP_REPORT_STEP:
            sent_temp = round(temp, 1)
            plan.set(ts, THERMOSTAT, "temperatureValue", sent_temp)
        if sent_sensor is None or abs(sensor - sent_sensor) >= TEMP_REPORT_STEP:
            sent_sensor = round(sensor, 1)
            plan.set(ts, "temperatureSensor", "temperatureValue", sent_sensor)
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


def _template(kit: str = "") -> dict:
    """A real get_home snapshot of the kit, set to a quiet home. kit "demo": its plug becomes three plugs named
    Kettle, TV and Washing machine, and it has a thermostat and the WiFi router (docs/contracts.md, C1)."""
    home = json.loads(_template_text())
    demo = home.pop("demo_devices")
    if kit == "demo":
        at = next(n for n, dev in enumerate(home["devices"]) if dev["type"] == "plug")
        home["devices"][at:at + 1] = [dev for dev in demo if dev["type"] == "plug"]
        home["devices"] += [dev for dev in demo if dev["type"] != "plug"]
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
           outages: list[tuple[int, int]] = (), kit: str = "") -> Iterator[str]:
    """outages: [start, end) stretches with no snapshot at all; the home carries on underneath.
    A change names its device by type, or "<type>:<slug of its name>" when the kit has several (`_device_keys`)."""
    home = _template(kit)
    keys = _device_keys(home["devices"])
    caps = {(key, cap["type"]): cap for key, dev in zip(keys, home["devices"]) for cap in dev["capabilities"]}
    plugs = [key for key, dev in zip(keys, home["devices"]) if dev["type"] == "plug"]

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

    watts, kwh, last_ts, day = dict.fromkeys(plugs, 0.0), dict.fromkeys(plugs, 0.0), start_ms, None
    for ts in sorted(set(by_ts) | set(range(start_ms, end_ms, interval_s * 1000))):
        for plug in plugs:
            kwh[plug] += watts[plug] * (ts - last_ts) / 3_600_000_000  # energy used since the last snapshot
        last_ts = ts
        today = datetime.fromtimestamp(ts / 1000, TZ).date()
        if day is not None and today != day:  # the plugs' counters start again at local midnight
            for plug in plugs:
                caps[(plug, "yesterdayConsumption")]["value"] = round(kwh[plug], 3)
                kwh[plug] = 0.0
        day = today
        for _, device_type, capability, value in by_ts.get(ts, ()):
            caps[(device_type, capability)]["value"] = value
            if device_type in watts and capability == "instantPower":
                watts[device_type] = float(value)
                caps[(device_type, "instantCurrent")]["value"] = round(watts[device_type] / VOLTAGE * 1000)  # mA
        for plug in plugs:
            caps[(plug, "todayConsumption")]["value"] = round(kwh[plug], 3)
        home["energy"]["todayConsumption"] = round(sum(kwh.values()), 3)
        for n, dev in enumerate(home["devices"]):
            dev["lastCommunicationTime"] = ts - 1000 * (n + 1)  # every device reported just before the poll
        if any(a <= ts < b for a, b in outages):
            continue
        yield json.dumps({"ts": ts, "home": home}, separators=(",", ":")) + "\n"


def generate(household: Household | str, days: int, *, start: date | None = None, seed: int = 1,
             interval_s: int = 300, thermostat_writes: list = ()) -> tuple[Iterator[str], list[dict]]:
    """JSONL lines for `days` local days from `start`, and the answer key of what was planted.

    `household` is a Household, a preset name, or "home-a" for the real house. `start` defaults to the household's
    own (`start_for`). `thermostat_writes`, for a household of people only: (epoch ms, "targetTemperature" or
    "mode", value), someone other than the people at the thermostat (Alfred); the room answers. The same
    arguments always give the same lines.
    """
    if days < 1:
        raise ValueError("days must be at least 1")
    if not 60 <= interval_s <= 1800:
        raise ValueError("the snapshot interval must be between 1 and 30 minutes (ingest reports longer gaps)")
    start = start or start_for(household)
    rng = random.Random(seed)
    outages: list[tuple[int, int]] = []
    kit = ""
    if household == REAL_HOME:
        plan = _home_a_plan(start, days, rng)
    else:
        if isinstance(household, str):
            if household not in PRESETS:
                raise ValueError(f"unknown household {household!r}: use {', '.join(PRESETS)} or {REAL_HOME}")
            household = PRESETS[household]
        if thermostat_writes and not household.people:
            raise ValueError("thermostat_writes need a household of people (it has the thermostat)")
        for write in thermostat_writes:
            if len(write) != 3 or write[1] not in ("targetTemperature", "mode"):
                raise ValueError(f"thermostat_writes: {write!r} is not (ms, \"targetTemperature\" or \"mode\", value)")
        plan = _config_plan(household, start, days, rng, random.Random(f"{seed}-room"), list(thermostat_writes))
        outages = _outages(household)
        kit = "demo" if household.people else ""
    end_ms = _at(start + timedelta(days=days), 0)
    truth = sorted((t for t in plan.truth if t["start"] < _iso(end_ms)), key=lambda t: t["start"])
    return _lines(plan.changes, _at(start, 0), end_ms, interval_s, outages, kit), truth


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="source.simulate", description="Simulate a household as get_home snapshots")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--household", default="rosa", help=f"{', '.join(PRESETS)}, or {REAL_HOME} (a real house, replayed)")
    p.add_argument("--config", type=Path, help="a JSON file of Household settings, applied on top of --household")
    p.add_argument("--houses", type=int, help="make this many different made-up homes instead; --out is then a folder")
    p.add_argument("--out", type=Path, help="the JSONL file to write, e.g. data/sim/rosa.jsonl")
    p.add_argument("--truth", type=Path, help="also write the answer key (what was planted) as JSON")
    p.add_argument("--start", type=date.fromisoformat, help="first day, YYYY-MM-DD (default: the household's, "
                   "mostly 2026-09-01)")
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
        print(f"{REAL_HOME}: a real kitchen replayed (up to {home_a_max_days(args.start or DEFAULT_START)} days), no settings")
        return 0
    if not args.out:
        p.error("--out is required")
    if args.houses:
        try:
            for h in houses(args.houses, args.seed):
                lines, _ = generate(h, args.days, start=args.start, seed=args.seed, interval_s=args.interval)
                _write(lines, args.out / f"{h.name}.jsonl", args.days, args.start or start_for(h))
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
    _write(lines, args.out, args.days, args.start or start_for(household))
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
