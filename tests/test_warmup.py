"""The warm-up (source/learn/warmup.py, C4 in docs/demo-plan.md), on a hand-made thermostat: the
heating's stretches as happenings (`thermostat.heatingState` on) and the room temperature it reported
as events, as the pipeline stores them.

  uv run python -m unittest tests.test_warmup -v
"""
import json
import random
import sqlite3
import unittest
from datetime import date, datetime, time, timedelta

from source.learn import storage
from source.learn.warmup import MIN_SAMPLES, lead_minutes, warmup
from source.store import SCHEMA as STORE_SCHEMA

FIRST = date(2026, 10, 15)
NOW = datetime(2026, 11, 19, 12)  # 35 days later; the last 30 start on 20 Oct at noon


def ms(t: datetime) -> int:
    return storage.to_ms(t)


def thermostat(days: int = 35, rate=lambda d: 5.0, cut_on: set[date] = frozenset(), first: date = FIRST):
    """Every evening the heating goes on at 18:15 with the room at 16 °C, warms it to 21 °C at `rate`
    °C/h, holds it until 19:45, then goes off; the room cools back to 16 °C by the next 18:15 (in
    0.1 °C steps, as the thermostat reports). Before 20 Oct the heating was twice as fast (a radiator
    since removed), to show only the last 30 days count."""
    rng = random.Random(2)
    db = sqlite3.connect(":memory:")
    storage.init(db)
    db.executescript(STORE_SCHEMA)
    happenings, events = [], []

    def report(t: datetime, c: float):
        events.append((ms(t), "th-1", "Thermostat", "temperatureValue", "change", None, json.dumps(round(c, 1))))

    for i in range(days):
        d = first + timedelta(days=i)
        on = datetime.combine(d, time(18, 15))
        r = rate(d) * (1 + rng.uniform(-0.05, 0.05))
        warm = on + timedelta(hours=5 / r)
        off = datetime.combine(d, time(19, 45))
        happenings.append({"signal": "thermostat.heatingState", "start": on.isoformat(), "end": off.isoformat(),
                           "value": "on", "device_id": "th-1", "capability": "heatingState", "cut": d in cut_on})
        report(on - timedelta(minutes=1), 16.0)
        for k in range(1, 51):  # 16.1 … 21.0
            report(on + (warm - on) * k / 50, 16 + k / 10)
        nxt = datetime.combine(d + timedelta(days=1), time(18, 15))
        for k in range(1, 50):  # 20.9 … 16.1 while off
            report(off + (nxt - off) * k / 50, 21 - k / 10)
    storage.add_happenings(db, happenings)
    db.executemany("INSERT INTO events (ts, device_id, device, capability, kind, old, new) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   events)
    db.commit()
    return db


class WarmupTest(unittest.TestCase):
    def test_the_rate_from_the_last_30_days(self):
        w = warmup(thermostat(rate=lambda d: 5.0 if d >= date(2026, 10, 20) else 10.0), NOW)
        self.assertEqual(set(w), {"rate_c_per_h", "loss_c_per_h", "samples", "confidence", "as_of"})
        self.assertEqual(w["samples"], 30)  # 20 Oct to 18 Nov
        self.assertAlmostEqual(w["rate_c_per_h"], 5.0, delta=0.15)
        self.assertAlmostEqual(w["loss_c_per_h"], 5 / 22.5, delta=0.02)  # 21 → 16 °C from 19:45 to 18:15
        self.assertEqual(w["as_of"], "2026-11-19")
        self.assertGreater(w["confidence"], 0.8)
        self.assertLessEqual(w["confidence"], 30 / 31)

    def test_lead_minutes(self):
        db = thermostat()
        self.assertAlmostEqual(lead_minutes(db, NOW, 16.0, 21.0), 60, delta=3)  # 5 °C at about 5 °C/h
        self.assertAlmostEqual(lead_minutes(db, NOW, 19.5, 21.0), 18, delta=1)
        self.assertEqual(lead_minutes(db, NOW, 21.5, 21.0), 0)  # warm enough already

    def test_none_until_five_samples(self):
        db = thermostat(days=MIN_SAMPLES - 1, first=NOW.date() - timedelta(days=MIN_SAMPLES))
        self.assertEqual(warmup(db, NOW)["samples"], MIN_SAMPLES - 1)
        self.assertIsNone(lead_minutes(db, NOW, 16.0, 21.0))
        db = thermostat(days=MIN_SAMPLES, first=NOW.date() - timedelta(days=MIN_SAMPLES))
        self.assertIsNotNone(lead_minutes(db, NOW, 16.0, 21.0))

    def test_a_cut_stretch_is_no_sample(self):
        """A stretch ended by a hole in the polls says nothing sure about how the room warmed."""
        cut = {NOW.date() - timedelta(days=k) for k in range(1, 4)}
        self.assertEqual(warmup(thermostat(cut_on=cut), NOW)["samples"], 27)

    def test_uneven_samples_lower_the_confidence(self):
        steady = warmup(thermostat(), NOW)["confidence"]
        odd = warmup(thermostat(rate=lambda d: 3.0 if d.day % 2 else 7.0), NOW)
        self.assertLess(odd["confidence"], steady)

    def test_nothing_to_read(self):
        empty = {"rate_c_per_h": None, "loss_c_per_h": None, "samples": 0, "confidence": 0.0, "as_of": "2026-11-19"}
        self.assertEqual(warmup(sqlite3.connect(":memory:"), NOW), empty)
        db = sqlite3.connect(":memory:")
        storage.init(db)
        db.executescript(STORE_SCHEMA)
        self.assertEqual(warmup(db, NOW), empty)
        self.assertIsNone(lead_minutes(db, NOW, 16.0, 21.0))


if __name__ == "__main__":
    unittest.main()
