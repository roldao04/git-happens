"""Warm-up: how fast the heating warms the house, and how fast the house cools (C4 in docs/demo-plan.md).

  uv run python -m source.learn.warmup --db data/sim/demo/home.sqlite3 --at 2026-11-19T18:00

Plain code over what the thermostat reported in the last DAYS days, no LLM:

- **Heating stretches**: the thermostat's `heatingState` happenings with value "on" (C2:
  `thermostat.heatingState`), ended by going back to rest, not by a hole in the polls.
- **One sample per stretch**: the room temperature the thermostat reported (`temperatureValue`,
  read from the `events` table: a slow signal's happenings don't keep its values) from the start of
  the stretch to the first time it reached its highest. Its rise per hour is the sample's rate, if it
  rose at all over at least MIN_RISE_MIN minutes. Holding a set temperature afterwards doesn't count.
- **rate_c_per_h**: the median of the samples: how fast the house really warms, its losses included.
- **loss_c_per_h**: the median fall per hour between two stretches at least MIN_COOL_H apart, when the
  temperature fell.
- **confidence** = n/(n+1) × (1 − spread / rate), with spread the samples' MAD × 1.4826 (a standard
  deviation that one odd stretch doesn't move): many alike samples give nearly 1.
- `lead_minutes(db, now, from_c, to_c)`: (to − from) / rate, in minutes; None until MIN_SAMPLES.

Both never raise: an empty warm-up (`samples` 0) when there is nothing to read.
"""
import argparse
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from statistics import median

from . import MIN_OCCURRENCES, to_ms

log = logging.getLogger("source.learn.warmup")

DAYS = 30  # most recent days measured (C4)
MIN_SAMPLES = MIN_OCCURRENCES  # lead_minutes says nothing before this many heating stretches (C4: 5)
MIN_RISE_MIN = 5  # a sample's rise must take at least this long: two readings a minute apart say little
MIN_COOL_H = 1  # a cooling sample spans at least this long, or 0.1 °C steps swamp it
MAD_TO_SD = 1.4826  # the MAD of a normal spread, as a standard deviation


def warmup(db: sqlite3.Connection, now: datetime) -> dict:
    """{"rate_c_per_h", "loss_c_per_h", "samples", "confidence", "as_of"} from the last DAYS days."""
    out = {"rate_c_per_h": None, "loss_c_per_h": None, "samples": 0, "confidence": 0.0,
           "as_of": now.date().isoformat()}
    try:
        rates, losses = _samples(db, now)
    except Exception:  # the loop and the agent read this: never stop them
        log.exception("warm-up failed")
        return out
    if rates:
        rate = median(rates)
        spread = MAD_TO_SD * median(abs(r - rate) for r in rates)
        n = len(rates)
        out.update(rate_c_per_h=round(rate, 2), samples=n,
                   confidence=round(n / (n + 1) * max(0.0, 1 - spread / rate), 2))
    if losses:
        out["loss_c_per_h"] = round(median(losses), 2)
    return out


def lead_minutes(db: sqlite3.Connection, now: datetime, from_c: float, to_c: float) -> float | None:
    """Minutes the heating needs to take the house from `from_c` to `to_c` (0 when it is warm
    enough already); None until there are MIN_SAMPLES heating stretches to go by."""
    w = warmup(db, now)
    if w["samples"] < MIN_SAMPLES or not w["rate_c_per_h"]:
        return None
    return round(max(0.0, to_c - from_c) / w["rate_c_per_h"] * 60, 1)


# ---------- reading the thermostat ----------

def _samples(db: sqlite3.Connection, now: datetime) -> tuple[list[float], list[float]]:
    """(warming rates, cooling rates), °C per hour, from the heating stretches of the last DAYS days."""
    if not (_has(db, "happenings") and _has(db, "events")):
        return [], []
    end = to_ms(now)
    since = to_ms(now - timedelta(days=DAYS))
    stretches: dict[str, list[tuple[int, int, bool]]] = {}
    for signal, dev, s, e, cut in db.execute(
            "SELECT signal, device_id, start_ms, end_ms, cut FROM happenings WHERE capability = 'heatingState'"
            " AND value = 'on' AND start_ms >= ? AND end_ms <= ? ORDER BY start_ms", (since, end)):
        if _device_type(signal) == "thermostat":
            stretches.setdefault(dev, []).append((s, e, bool(cut)))
    rates, losses = [], []
    for dev, runs in stretches.items():
        temps = _temperatures(db, dev, since - 24 * 3600_000, end)
        if not temps:
            continue
        for s, e, cut in runs:
            if not cut and (r := _rise(temps, s, e)) is not None:
                rates.append(r)
        for (_, e, cut_a), (s, _, cut_b) in zip(runs, runs[1:]):
            hours = (s - e) / 3600_000
            if not (cut_a or cut_b) and hours >= MIN_COOL_H:
                fall = _at(temps, e) - _at(temps, s) if _at(temps, e) is not None else None
                if fall is not None and fall > 0:
                    losses.append(fall / hours)
    return rates, losses


def _temperatures(db: sqlite3.Connection, device_id: str, since: int, until: int) -> list[tuple[int, float]]:
    """The device's reported temperatures, (epoch ms, °C), in time order."""
    out = []
    for ts, new in db.execute("SELECT ts, new FROM events WHERE device_id = ? AND capability = 'temperatureValue'"
                              " AND kind IN ('initial', 'change') AND ts >= ? AND ts <= ? ORDER BY ts, id",
                              (device_id, since, until)):
        try:
            v = json.loads(new) if isinstance(new, str) else new
            out.append((int(ts), float(v)))
        except (TypeError, ValueError):
            continue
    return out


def _at(temps: list[tuple[int, float]], t: int) -> float | None:
    """The last temperature reported at or before t."""
    v = None
    for ts, x in temps:
        if ts > t:
            break
        v = x
    return v


def _rise(temps: list[tuple[int, float]], start: int, end: int) -> float | None:
    """°C per hour from the stretch's start to the first time it reached its highest; None if it
    didn't rise, or rose in less than MIN_RISE_MIN."""
    t0 = _at(temps, start)
    inside = [(ts, x) for ts, x in temps if start < ts <= end]
    if t0 is None or not inside:
        return None
    top = max(x for _, x in inside)
    when = next(ts for ts, x in inside if x == top)
    hours = (when - start) / 3600_000
    if top <= t0 or hours * 60 < MIN_RISE_MIN:
        return None
    return (top - t0) / hours


def _device_type(signal: str) -> str:
    """"thermostat.heatingState" → "thermostat" (C2; a local copy of source.learn.device_type)."""
    return signal.split(".", 1)[0].split(":", 1)[0]


def _has(db: sqlite3.Connection, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def main() -> None:
    ap = argparse.ArgumentParser(description="How fast the heating warms the house.")
    ap.add_argument("--db", required=True, help="a home.sqlite3 with happenings and events")
    ap.add_argument("--at", required=True, help="the moment, e.g. 2026-11-19T18:00")
    ap.add_argument("--to", type=float, default=21.0, help="a target, for the lead time from 16 °C")
    args = ap.parse_args()
    db = sqlite3.connect(args.db)
    now = datetime.fromisoformat(args.at)
    print(json.dumps(warmup(db, now)))
    print(f"16 → {args.to} °C: {lead_minutes(db, now, 16.0, args.to)} min")


if __name__ == "__main__":
    main()
