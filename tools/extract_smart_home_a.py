"""One-off: turn the UMass Smart* "Home A" traces (2013 release) into the small file the simulator replays.

Smart* is a real house in Western Massachusetts, recorded May-July 2012:
https://traces.cs.umass.edu/docs/traces/smartstar (Barker et al., SustKDD 2012).

Download and unpack these four archives into one folder, then run:

  curl -LO https://lass.cs.umass.edu/smarttraces/homeA-{door,switch,meter,environmental}.tar.gz
  for f in homeA-*.tar.gz; do tar -xzf $f; done
  uv run python tools/extract_smart_home_a.py <that folder>

It writes source/sim_data/home_a.json.gz. Our kit has one of each device, so one real sensor is picked
for each, all in the kitchen:

  contact sensor      <- door "kitchen:refrigerator"  (the house's only door sensor)
  bulb                <- switch "kitchen:lights"      (dimmer: on/off and brightness)
  plug                <- meter "kitchen:coffeepot"    (detailed data on 17 of the days only)
  temperature sensor  <- insideTemp (F -> C) and insideHumidity, every 5 minutes

Times are kept as seconds of the house's local day (America/New_York), so the simulator can replay
"06:30 on a Tuesday" as 06:30 on a Tuesday in the simulated home.
"""
import csv
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HOUSE_TZ = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parent.parent / "source" / "sim_data" / "home_a.json.gz"

DOOR, SWITCH, METER = "kitchen:refrigerator", "kitchen:lights", "kitchen:coffeepot"
MAX_OPEN_S = 600     # a longer "open" is a missed close message, not a fridge left open for a day
GLITCH_W = 3000      # the meters sometimes report values in the millions
ON_W = 20            # above this the coffee maker is doing something
BURST_GAP_S = 180    # samples further apart belong to different uses
MIN_BURST_SAMPLES = 5
STEP_S = 20          # keep one plug sample per 20 s, like the live poll


def _rows(folder: Path, kind: str):
    for path in sorted((folder / kind).glob("2012-*.csv")):
        with open(path, newline="") as f:
            for row in csv.reader(f):
                if row:
                    yield [c.strip() for c in row]


def _local(ts: int) -> tuple[str, int]:
    """Epoch seconds -> (local date, second of that local day)."""
    d = datetime.fromtimestamp(ts, HOUSE_TZ)
    return d.date().isoformat(), d.hour * 3600 + d.minute * 60 + d.second


def door(folder: Path) -> dict[str, list]:
    events = sorted({(int(ts), int(v)) for name, ts, v in _rows(folder, "homeA-door") if name == DOOR})
    out, state, opened = defaultdict(list), 0, 0
    for ts, v in events:
        if state == 1 and ts - opened > MAX_OPEN_S:  # the close never arrived: close it ourselves
            day, sec = _local(opened + MAX_OPEN_S)
            out[day].append([sec, 0])
            state = 0
        if v == state:
            continue  # repeated message
        state, opened = v, ts
        day, sec = _local(ts)
        out[day].append([sec, v])
    return out


def light(folder: Path) -> dict[str, list]:
    samples = sorted({(int(r[1]), float(r[3])) for r in _rows(folder, "homeA-switch") if r[0] == SWITCH})
    out, last = defaultdict(list), None
    for ts, dim in samples:
        dim = round(dim, 2)
        if dim != last:
            day, sec = _local(ts)
            out[day].append([sec, dim])
            last = dim
    return out


def climate(folder: Path) -> dict[str, list]:
    out = defaultdict(list)
    for r in sorted(_rows(folder, "homeA-environmental"), key=lambda r: int(r[0])):
        if not r[1] or not r[3]:
            continue
        day, sec = _local(int(r[0]))
        out[day].append([sec, round((float(r[1]) - 32) * 5 / 9, 1), round(float(r[3]), 1)])
    return out


def plug(folder: Path) -> tuple[dict[str, list], list[str]]:
    """Each use of the coffee maker as [second of day, [[offset_s, watts], ...]], and the days the meter covers."""
    samples = sorted({(int(r[1]), float(r[2])) for r in _rows(folder, "homeA-meter") if r[0] == METER})
    covered = sorted({_local(ts)[0] for ts, _ in samples})
    bursts, cur = [], []
    for ts, w in samples:
        if ON_W < w < GLITCH_W:
            if cur and ts - cur[-1][0] > BURST_GAP_S:
                bursts.append(cur)
                cur = []
            cur.append((ts, w))
        elif cur:
            bursts.append(cur)
            cur = []
    if cur:
        bursts.append(cur)

    out = defaultdict(list)
    for b in bursts:
        if len(b) < MIN_BURST_SAMPLES:
            continue  # a lone keep-warm blip
        start = b[0][0]
        points, next_at = [], 0
        for ts, w in b:
            if ts - start >= next_at:
                points.append([ts - start, round(w)])
                next_at = ts - start + STEP_S
        points.append([b[-1][0] - start + 5, 0])  # back to rest just after the last "on" sample
        day, sec = _local(start)
        out[day].append([sec, points])
    return out, covered


def main() -> None:
    folder = Path(sys.argv[1])
    doors, lights, temps = door(folder), light(folder), climate(folder)
    uses, covered = plug(folder)
    days = {}
    for day in sorted(set(doors) | set(lights) | set(temps)):
        days[day] = {"door": doors.get(day, []), "light": lights.get(day, []), "climate": temps.get(day, []),
                     # None = the meter recorded nothing that day, [] = it was recording and saw no use
                     "plug": uses.get(day, []) if day in covered else None}
    data = {
        "source": "UMass Smart* data set, Home A, 2013 release (https://traces.cs.umass.edu/docs/traces/smartstar)",
        "house_tz": str(HOUSE_TZ),
        "devices": {"contactSensor": DOOR, "bulb": SWITCH, "plug": METER,
                    "temperatureSensor": "insideTemp, insideHumidity"},
        "days": days,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB): {len(days)} days, "
          f"{sum(len(d['door']) for d in days.values())} door events, "
          f"{sum(len(d['light']) for d in days.values())} light changes, "
          f"{sum(len(d['plug'] or []) for d in days.values())} plug uses on "
          f"{sum(1 for d in days.values() if d['plug'] is not None)} metered days")


if __name__ == "__main__":
    main()
