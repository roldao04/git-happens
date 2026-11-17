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
  variation and skipped days. Change the config and the same code gives a different home.
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
from dataclasses import asdict, dataclass, fields
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
    temp_base: float = 20.5
    temp_swing: float = 1.5  # between the coolest and warmest time of day, each side of the base
    humidity_base: float = 55


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
    for key, raw in values.items():
        if key not in known:
            raise ValueError(f"unknown household setting: {key}")
        try:
            setattr(h, key, known[key](raw) if known[key] is not str else str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{key}: {raw!r} is not a number") from None
    for key in ("wake", "bed", "outing_at", "lamp_evening_on"):
        _minutes(getattr(h, key), key)
    if h.evening_use:
        _minutes(h.evening_use, "evening_use")
    _weekdays(h.outing_days)
    for key in ("away_chance", "door_after_wake_chance", "morning_use_chance", "evening_use_chance", "outing_chance"):
        if not 0 <= getattr(h, key) <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
    if h.appliance_watts <= 0 or h.appliance_minutes <= 0 or h.idle_watts < 0:
        raise ValueError("appliance_watts and appliance_minutes must be above 0, idle_watts 0 or more")
    return h


def _minutes(hhmm: str, what: str = "time") -> int:
    try:
        hours, minutes = hhmm.split(":")
        if 0 <= int(hours) < 24 and 0 <= int(minutes) < 60:
            return int(hours) * 60 + int(minutes)
    except ValueError:
        pass
    raise ValueError(f"{what}: {hhmm!r} is not a time like 07:45")


def _weekdays(names: str) -> set[int]:
    out = set()
    for name in names.replace(" ", "").lower().split(","):
        if name and name[:3] not in WEEKDAYS:
            raise ValueError(f"outing_days: {name!r} is not a weekday (use mon, tue, wed, thu, fri, sat, sun)")
        if name:
            out.add(WEEKDAYS.index(name[:3]))
    return out


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

def _config_plan(h: Household, start: date, days: int, rng: random.Random) -> _Plan:
    plan = _Plan()
    outing_days = _weekdays(h.outing_days)
    wake_min, bed_min = _minutes(h.wake), _minutes(h.bed)
    if bed_min <= wake_min:
        bed_min += 24 * 60  # after midnight
    busy: list[tuple[int, int]] = []  # when the appliance runs, so the idle wobble stays out of it

    def use(day_start: int, what: str) -> None:
        watts = max(1.0, rng.gauss(h.appliance_watts, h.appliance_watts_spread))
        seconds = max(30.0, rng.gauss(h.appliance_minutes, h.appliance_minutes_spread) * 60)
        step = 30 if seconds <= 600 else 120
        points = [(0.0, watts)]
        points += [(float(t), watts * (1 + rng.gauss(0, 0.004))) for t in range(step, int(seconds), step)]
        points.append((seconds, h.idle_watts))
        end = plan.burst(day_start, points)
        busy.append((day_start, end))
        plan.note(what, day_start, end, appliance=h.appliance, watts=round(watts))

    for i in range(days):
        day = start + timedelta(days=i)
        if rng.random() < h.away_chance:
            plan.note("away all day", _at(day, 0))
            continue
        shift = h.weekend_shift_min if day.weekday() >= 5 else 0
        wake = (wake_min + shift + rng.gauss(0, h.wake_spread_min)) * 60
        bed = max((bed_min + rng.gauss(0, h.bed_spread_min)) * 60, wake + 4 * 3600)
        plan.note("wake", _at(day, wake))
        plan.note("bed", _at(day, bed))

        plan.lamp(_at(day, wake), True)
        plan.lamp(_at(day, wake + rng.uniform(30, 60) * 60), False)

        if rng.random() < h.door_after_wake_chance:
            opened = _at(day, wake + rng.uniform(5, 12) * 60)
            plan.door(opened, rng.uniform(20, 90))
            plan.note("door after waking", opened)
        if rng.random() < h.morning_use_chance:
            delay = max(2.0, rng.gauss(h.morning_use_delay_min, h.morning_use_spread_min))
            use(_at(day, wake + delay * 60), "morning use")
        if h.evening_use and rng.random() < h.evening_use_chance:
            at = (_minutes(h.evening_use) + rng.gauss(0, h.evening_use_spread_min)) * 60
            if wake < at < bed - 600:
                use(_at(day, at), "evening use")

        out = back = None
        if day.weekday() in outing_days and rng.random() < h.outing_chance:
            leave = max((_minutes(h.outing_at) + rng.gauss(0, h.outing_spread_min)) * 60, wake + 45 * 60)
            ret = leave + h.outing_minutes * 60 * rng.uniform(0.9, 1.1)
            if ret < bed - 1800:
                out, back = leave, ret
                plan.door(_at(day, out), rng.uniform(15, 45))
                plan.door(_at(day, back), rng.uniform(15, 45))
                plan.note("outing", _at(day, out), _at(day, back))

        openings = int(h.extra_door_openings) + (rng.random() < h.extra_door_openings % 1)
        for _ in range(openings):
            at = rng.uniform(wake + 3600, bed - 3600)
            if out is None or not out - 300 <= at <= back + 300:
                plan.door(_at(day, at), rng.uniform(10, 120))

        lamp_on = (_minutes(h.lamp_evening_on) + rng.gauss(0, 15)) * 60
        if back is not None and out <= lamp_on <= back:
            lamp_on = back + 120  # nobody home to switch it on
        if lamp_on < bed - 300:
            plan.lamp(_at(day, lamp_on), True)
        plan.lamp(_at(day, bed), False)

    first, last = _at(start, 0), _at(start + timedelta(days=days), 0)
    plan.set(first, "plug", "instantPower", round(h.idle_watts))
    if h.idle_watts > 0:  # a plug that idles above zero wobbles a little
        for ts in range(first + 600_000, last, 600_000):
            if not any(a - 60_000 <= ts <= b + 60_000 for a, b in busy):
                plan.set(ts, "plug", "instantPower", round(h.idle_watts) + rng.choice((-1, 0, 0, 0, 1)))

    # The room: warmest mid-afternoon, coolest before dawn, with a slow random drift.
    # Like a battery sensor, it only reports once the value has moved by a step.
    drift = damp = 0.0
    sent_temp = sent_humidity = None
    for ts in range(first, last, CLIMATE_STEP_S * 1000):
        local = datetime.fromtimestamp(ts / 1000, TZ)
        hour = local.hour + local.minute / 60
        drift = 0.98 * drift + rng.gauss(0, 0.04)
        damp = 0.98 * damp + rng.gauss(0, 0.08)
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


def _lines(changes: list[Change], start_ms: int, end_ms: int, interval_s: int) -> Iterator[str]:
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
    if household == REAL_HOME:
        plan = _home_a_plan(start, days, rng)
    else:
        if isinstance(household, str):
            if household not in PRESETS:
                raise ValueError(f"unknown household {household!r}: use {', '.join(PRESETS)} or {REAL_HOME}")
            household = PRESETS[household]
        plan = _config_plan(household, start, days, rng)
    end_ms = _at(start + timedelta(days=days), 0)
    truth = sorted((t for t in plan.truth if t["start"] < _iso(end_ms)), key=lambda t: t["start"])
    return _lines(plan.changes, _at(start, 0), end_ms, interval_s), truth


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
