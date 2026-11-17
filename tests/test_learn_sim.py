"""Parts 1–6 end to end on master's simulator: simulate → ingest → happenings → learners.

  uv run python -m unittest tests.test_learn_sim -v      # about 15 s
"""
import json
import logging
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from source.pipeline import run
from source.simulate import PRESETS, generate, household_from_dict


def learned(household, days: int) -> tuple[list[dict], set[str]]:
    """Every sensor learned from a simulated household, day by day, as the pipeline does it,
    and the signals that have kinds."""
    lines, _ = generate(household, days)
    with tempfile.TemporaryDirectory() as tmp:
        logging.disable(logging.INFO)  # the pipeline logs one line per day
        try:
            run(((row["ts"], row["home"]) for row in map(json.loads, lines)), Path(tmp))
        finally:
            logging.disable(logging.NOTSET)
        db = sqlite3.connect(Path(tmp) / "home.sqlite3")
        sensors = [json.loads(d) for (d,) in db.execute("SELECT data FROM sensors")]
        grouped = {sig for (sig,) in db.execute("SELECT DISTINCT signal FROM kinds")}
        db.close()
    return sensors, grouped


def minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def find(sensors, part: str, near: str | None = None, within: int = 30) -> list[dict]:
    out = [s for s in sensors if s["status"] == "active" and any(part in p for p in s["pattern"]["parts"])]
    if near:
        out = [s for s in out if abs(minutes(s["pattern"]["time_of_day"]) - minutes(near)) <= within]
    return out


class RosaTest(unittest.TestCase):
    """rosa (kettle about 20 min after waking at 07:45, bed 23:10), 28 days, with a holiday and an outage."""

    @classmethod
    def setUpClass(cls):
        rosa = household_from_dict(asdict(PRESETS["rosa"]) | {
            "holidays": ["2026-09-15..2026-09-18"], "outages": ["2026-09-08T22:00..2026-09-09T09:00"]})
        cls.sensors, cls.grouped = learned(rosa, 28)

    def test_morning_tea(self):
        tea = find(self.sensors, "2,100 W", near="07:59")
        self.assertTrue(tea, [s["pattern"] for s in self.sensors])

    def test_asleep_and_awake_are_kinds_of_quiet(self):
        self.assertTrue(find(self.sensors, "quiet after", near="23:10"))  # the night's quiet
        self.assertTrue(find(self.sensors, "end of home-", near="08:00"))  # its end

    def test_holiday_and_outage_change_nothing(self):
        night = find(self.sensors, "quiet after", near="23:10")[0]["pattern"]
        self.assertLess(night["duration_min"], 20 * 60)  # no "sleep" across the outage or the holiday
        self.assertFalse([s for s in self.sensors if s["status"] == "fading" and "2,100 W" in " ".join(s["pattern"]["parts"])])

    def test_room_facts_are_grouped_too(self):  # temperature and humidity are signals like any other
        self.assertTrue({"temperatureSensor.temperatureValue", "temperatureSensor.humidityValue"} <= self.grouped)


class CoupleTest(unittest.TestCase):
    """couple: up 06:50 (later at weekends), out on weekdays 08:05 to about 17:35, bed 22:40."""

    @classmethod
    def setUpClass(cls):
        cls.sensors, _ = learned("couple", 21)

    def test_night_and_workday_are_different_quiets(self):
        self.assertTrue(find(self.sensors, "quiet after", near="22:40"))  # asleep
        away = find(self.sensors, "quiet after", near="08:05")  # out at work
        self.assertTrue(away, [s["pattern"]["parts"] for s in self.sensors])
        self.assertNotEqual(away[0]["pattern"]["time_of_day"], find(self.sensors, "quiet after", near="22:40")[0]["pattern"]["time_of_day"])

    def test_routines(self):
        self.assertTrue(find(self.sensors, "end of home-", near="06:50"))  # up on work days
        self.assertTrue(find(self.sensors, "1,300 W", near="06:50"))  # coffee


if __name__ == "__main__":
    unittest.main()
