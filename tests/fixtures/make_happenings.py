"""Hand-made happenings for the grouper and habit learner, until the simulator and extractor exist.

  uv run python tests/fixtures/make_happenings.py      # writes happenings-14d.jsonl (household A)

A household is a config: routines planted at a clock time (or a few minutes after another
routine), on some weekdays, sometimes skipped, plus random noise during waking hours. The
learner is never told any of this; tests check it finds the routines and nothing else.

Messier data for robustness tests: `poll_s` rounds times up to the poll grid (a change is seen
at the next poll), `offline` and `away` drop whole days (use `polls()` without the offline days),
and a household may wake later at weekends (`wake_weekend`).
"""
import copy
import json
import math
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

HERE = Path(__file__).parent
DOOR, PLUG, LAMP = "contactSensor.sensorState", "plug.instantPower", "bulb.power"
START = date(2026, 9, 1)  # a Tuesday

HOUSEHOLD_A = {
    "wake": ("07:00", 10), "bed": ("23:15", 15),  # clock time, spread (min)
    "routines": [
        {"name": "morning light", "signal": LAMP, "value": "on", "at": "wake", "duration_s": (900, 120)},
        {"name": "door", "signal": DOOR, "value": "alarm", "at": ("07:58", 8), "duration_s": (65, 15),
         "every": 0.86},
        {"name": "kettle", "signal": PLUG, "size": (2100, 40), "after": ("door", 3, 8), "duration_s": (180, 20)},
        {"name": "evening light", "signal": LAMP, "value": "on", "at": ("19:30", 15), "until": "bed"},
        {"name": "weekly", "signal": PLUG, "size": (650, 20), "at": ("10:00", 10), "duration_s": (1500, 180),
         "weekdays": [3]},  # Thursday
    ],
    "noise": [  # per day, uniform between wake and bed
        {"signal": DOOR, "value": "alarm", "per_day": (3, 6), "duration_s": (20, 120)},
        {"signal": PLUG, "per_day": (0, 2), "size": (800, 1200), "duration_s": (60, 240)},
    ],
}

# Night shift: up at 15:00, coffee soon after, light on through the night, asleep 08:00-15:00.
HOUSEHOLD_B = {
    "wake": ("15:00", 10), "bed": ("08:00", 15),
    "routines": [
        {"name": "wake light", "signal": LAMP, "value": "on", "at": "wake", "duration_s": (600, 60)},
        {"name": "coffee", "signal": PLUG, "size": (1400, 30), "after": ("wake light", 10, 20),
         "duration_s": (60, 10)},
        {"name": "night light", "signal": LAMP, "value": "on", "at": ("21:00", 15), "until": "bed"},
    ],
    "noise": [
        {"signal": DOOR, "value": "alarm", "per_day": (2, 5), "duration_s": (20, 120)},
        {"signal": PLUG, "per_day": (0, 2), "size": (800, 1200), "duration_s": (60, 240)},
    ],
}


# Lies in at weekends: up at 07:00 on work days, 09:30 on Sat/Sun; kettle soon after waking.
HOUSEHOLD_C = {
    "wake": ("07:00", 10), "wake_weekend": ("09:30", 10), "bed": ("23:15", 15),
    "routines": [
        {"name": "morning light", "signal": LAMP, "value": "on", "at": "wake", "duration_s": (900, 120)},
        {"name": "kettle", "signal": PLUG, "size": (2100, 40), "after": ("morning light", 20, 30),
         "duration_s": (180, 20)},
        {"name": "evening light", "signal": LAMP, "value": "on", "at": ("19:30", 15), "until": "bed"},
    ],
    "noise": HOUSEHOLD_A["noise"],
}


def independent_kettle(household: dict) -> dict:
    """Household A with the kettle on its own clock (around 08:05), not after the door."""
    h = copy.deepcopy(household)
    for r in h["routines"]:
        if r["name"] == "kettle":
            del r["after"]
            r["at"], r["every"] = ("08:05", 8), 0.86
    return h


def _clock(day: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def make(household: dict, days: int, seed: int = 4, start: date = START, poll_s: int | None = None,
         offline: tuple[int, ...] = (), away: tuple[int, ...] = ()) -> list[dict]:
    """`offline` / `away`: day indices dropped after generating, so the other days don't change."""
    rng = random.Random(seed)
    out: list[dict] = []
    for i in range(days):
        day = start + timedelta(days=i)
        wake_spec = household.get("wake_weekend") if day.weekday() >= 5 and "wake_weekend" in household \
            else household["wake"]
        wake = _clock(day, wake_spec[0]) + timedelta(minutes=rng.gauss(0, wake_spec[1]))
        bed = _clock(day, household["bed"][0]) + timedelta(minutes=rng.gauss(0, household["bed"][1]))
        if bed <= wake:
            bed += timedelta(days=1)
        placed: dict[str, datetime] = {"wake": wake}
        todays: list[dict] = []
        for r in household["routines"]:
            if "after" in r:
                anchor, lo, hi = r["after"]
                if anchor not in placed:
                    continue
                t = placed[anchor] + timedelta(minutes=rng.uniform(lo, hi))
            elif r["at"] == "wake":
                t = wake
            else:
                t = _clock(day, r["at"][0]) + timedelta(minutes=rng.gauss(0, r["at"][1]))
                if t < wake:
                    t += timedelta(days=1)
            if "weekdays" in r and t.weekday() not in r["weekdays"]:
                continue
            if rng.random() > r.get("every", 1.0):
                continue
            end = bed if r.get("until") == "bed" else t + timedelta(seconds=max(5, rng.gauss(*r["duration_s"])))
            placed[r["name"]] = t
            todays.append(_happening(r, t, end, rng))
        for n in household["noise"]:
            for _ in range(rng.randint(*n["per_day"])):
                t = wake + timedelta(minutes=15) + (bed - wake - timedelta(minutes=30)) * rng.random()
                h = _happening(n, t, t + timedelta(seconds=rng.uniform(*n["duration_s"])), rng, uniform=True)
                if not any(o["signal"] == h["signal"] and o["start"] < h["end"] and h["start"] < o["end"]
                           for o in todays):  # a real signal can't hold two happenings at once
                    todays.append(h)
        if i not in offline and i not in away:
            out.extend(todays)
    if poll_s:
        out = [_on_poll_grid(h, poll_s) for h in out]
    out.sort(key=lambda h: h["start"])
    return out


def polls(days: int, every_s: int = 60, offline: tuple[int, ...] = (), start: date = START) -> list[datetime]:
    """When the loop polled: every `every_s` seconds, except on offline days."""
    t0 = datetime.combine(start, time())
    return [t0 + timedelta(seconds=k * every_s) for k in range(days * 86400 // every_s)
            if (k * every_s) // 86400 not in offline]


def _on_poll_grid(h: dict, poll_s: int) -> dict:
    def up(iso: str) -> datetime:
        t = datetime.fromisoformat(iso)
        midnight = datetime.combine(t.date(), time())
        return midnight + timedelta(seconds=math.ceil((t - midnight).total_seconds() / poll_s) * poll_s)
    start, end = up(h["start"]), up(h["end"])
    end = max(end, start + timedelta(seconds=poll_s))
    return {**h, "start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
            "duration_s": round((end - start).total_seconds())}


def _happening(spec: dict, start: datetime, end: datetime, rng: random.Random, uniform: bool = False) -> dict:
    size = None
    if "size" in spec:
        size = round(rng.uniform(*spec["size"]) if uniform else rng.gauss(*spec["size"]))
    h = {"signal": spec["signal"], "start": start.isoformat(timespec="seconds"),
         "end": end.isoformat(timespec="seconds"), "duration_s": round((end - start).total_seconds()),
         "size": size, "kind": None}
    if spec.get("value"):
        h["value"] = spec["value"]
    return h


def write(rows: list[dict], path: Path) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def main() -> None:
    rows = make(HOUSEHOLD_A, 14)
    write(rows, HERE / "happenings-14d.jsonl")
    print(f"wrote {len(rows)} happenings to {HERE / 'happenings-14d.jsonl'}")


if __name__ == "__main__":
    main()
