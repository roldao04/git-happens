"""Habits (part A, source/learn/grouping.py): patterns that recur together become one habit, with a
rhythm, a window, variation, readings, stable ids and a status now.

  uv run python -m unittest tests.test_grouping -v      # about 20 s: two simulated homes

The integration step will call learn_habits after learn_day; here the tests call it themselves:
right after learn_day (the fixture), or as the pipeline's after_learn step (the simulator).
"""
import json
import logging
import random
import sqlite3
import tempfile
import unittest
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from source.learn import storage
from source.learn.grouping import export_habits, habit_today, learn_habits
from source.learn.run import learn_day
from source.pipeline import run
from source.simulate import generate

FIXTURE = Path(__file__).parent / "fixtures" / "happenings-14d.jsonl"
FIRST = date(2026, 9, 1)  # a Tuesday
HABIT_KEYS = ["id", "name", "named_by", "status", "person", "patterns", "steps", "recurrence", "window", "windows",
              "variation", "occurrences", "opportunities", "first_seen", "last_seen", "learned_at", "confidence", "trend"]
RECURRENCE_KEYS = {"kind", "weekdays", "every_n_days", "anchor", "day_of_month", "every_n_hours", "text"}
WORK_DAYS = ["mon", "tue", "wed", "thu", "fri"]


def minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def near(hhmm: str, target: str, within: int = 30) -> bool:
    d = abs(minutes(hhmm) - minutes(target)) % 1440
    return min(d, 1440 - d) <= within


def active(db) -> list[dict]:
    return [h for h in export_habits(db)["habits"] if h["status"] == "active"]


def holding(db, pattern_id: str) -> dict | None:
    """The active habit with this pattern among its steps."""
    return next((h for h in active(db) if pattern_id in h["patterns"]), None)


class Sensors:
    """The habit learner's sensors in a db, to find patterns by what they are."""

    def __init__(self, db):
        self.all = [s for s in storage.export(db)["sensors"] if s["status"] == "active"]

    def find(self, *parts: str, at: str | None = None, within: int = 30, steps: int | None = None) -> dict:
        for s in self.all:
            p = s["pattern"]
            if all(any(x in y for y in p["parts"]) for x in parts) and (at is None or near(p["time_of_day"], at, within)) \
                    and (steps is None or len(p["parts"]) == steps):
                return s
        raise AssertionError(f"no active sensor with {parts} at {at}: {[(s['pattern']['parts'], s['pattern']['time_of_day']) for s in self.all]}")


def simulated(household: str, days: int) -> sqlite3.Connection:
    """A simulated home through the pipeline (learn_day at each day end), with learn_habits right
    after it; returns an in-memory copy of its db."""
    lines, _ = generate(household, days)
    with tempfile.TemporaryDirectory() as tmp:
        logging.disable(logging.INFO)  # the pipeline logs one line per day
        try:
            run(((row["ts"], row["home"]) for row in map(json.loads, lines)), Path(tmp), after_learn=learn_habits)
        finally:
            logging.disable(logging.NOTSET)
        disk = sqlite3.connect(Path(tmp) / "home.sqlite3")
        db = sqlite3.connect(":memory:")
        disk.backup(db)
        disk.close()
    return db


# ---------- a hand-made home: every learner output written by hand ----------

def handmade(days: int = 28) -> sqlite3.Connection:
    """Sensors and readings as the habit learner would store them, plus the happenings under them:
    - on weekdays, the lamp at 07:00 then the kettle 10 min later (a link, lamp → kettle);
    - at weekends, the coffee machine at 07:50 (near the weekday morning, never on the same days);
    - the door at 18:00 every day;
    - a pump every 3 hours from 11:00 to 23:00;
    - a few door openings at random, so the home has a rhythm to measure chance against.
    """
    rng = random.Random(4)
    db = sqlite3.connect(":memory:")
    storage.init(db)
    rows, readings = [], {}

    def happen(signal, kind, at: datetime, seconds: float, size=None, value=None) -> datetime:
        end = at + timedelta(seconds=seconds)
        rows.append({"signal": signal, "start": at.isoformat(), "end": end.isoformat(), "size": size,
                     "value": value, "kind": kind})
        return end

    def read(pattern, start: datetime, end: datetime | None):
        readings.setdefault(pattern, []).append((start, end))

    for i in range(days):
        day = datetime.combine(FIRST + timedelta(days=i), time())
        at = lambda clock, spread: day + timedelta(minutes=clock + rng.gauss(0, spread))  # noqa: E731
        if day.weekday() < 5:
            lamp = at(7 * 60, 5)
            happen("bulb.power", "bulb-A", lamp, 1800, value="on")
            read("pattern-1", lamp, lamp + timedelta(seconds=1800))
            if rng.random() < 0.9:
                kettle = lamp + timedelta(minutes=rng.uniform(8, 12))
                read("pattern-2", lamp, happen("plug.instantPower", "plug-A", kettle, 180, size=2100))
        else:
            coffee = at(7 * 60 + 50, 5)
            read("pattern-3", coffee, happen("plug.instantPower", "plug-C", coffee, 300, size=1200))
        door = at(18 * 60, 10)
        read("pattern-4", door, happen("contactSensor.sensorState", "contactSensor-A", door, 60, value="alarm"))
        for n, clock in enumerate((11, 14, 17, 20, 23)):
            if rng.random() < 0.9:
                pump = at(clock * 60, 4)
                read(f"pattern-{5 + n}", pump, happen("plug.instantPower", "plug-B", pump, 120, size=400))
        for _ in range(4):
            happen("contactSensor.sensorState", None, at(rng.uniform(8 * 60, 22 * 60), 0), 30, value="alarm")
    storage.add_happenings(db, rows)
    signatures = {"pattern-1": ("kind:bulb-A", ["bulb-A (power on)"], 7 * 60, WORK_DAYS),
                  "pattern-2": ("link:bulb.power>plug-A", ["bulb-A (power on)", "plug-A (about 2,100 W)"], 7 * 60, WORK_DAYS),
                  "pattern-3": ("kind:plug-C", ["plug-C (about 1,200 W)"], 7 * 60 + 50, ["sat", "sun"]),
                  "pattern-4": ("kind:contactSensor-A", ["contactSensor-A (alarm)"], 18 * 60, None)}
    signatures |= {f"pattern-{5 + n}": ("kind:plug-B", ["plug-B (about 400 W)"], clock * 60, None)
                   for n, clock in enumerate((11, 14, 17, 20, 23))}
    for sid, (signature, parts, centre, weekdays) in signatures.items():
        spread = 15
        pattern = {"parts": parts, "time_of_day": f"{centre // 60:02d}:{centre % 60:02d}", "spread_min": 5,
                   "days_seen": len(readings[sid]), "days_total": days,
                   "earliest": f"{(centre - spread) // 60:02d}:{(centre - spread) % 60:02d}",
                   "latest": f"{(centre + spread) // 60:02d}:{(centre + spread) % 60:02d}",
                   "late_after": f"{(centre + spread) // 60:02d}:{(centre + spread) % 60:02d}"}
        if weekdays:
            pattern["weekdays"] = weekdays
        data = {"id": sid, "name": None, "named_by": None, "focus": "connected_life", "learner": "habit",
                "pattern": pattern, "learned_at": FIRST.isoformat(), "confidence": 0.9, "explanation": None,
                "status": "active"}
        db.execute("INSERT INTO sensors (id, learner, focus, signature, centre_min, width_min, learned_at, status, data)"
                   " VALUES (?, 'habit', 'connected_life', ?, ?, 20, ?, 'active', ?)",
                   (sid, signature, centre, FIRST.isoformat(), json.dumps(data)))
        db.executemany("INSERT INTO sensor_readings (sensor_id, start, end, value) VALUES (?, ?, ?, NULL)",
                       [(sid, s.isoformat(timespec="seconds"), e.isoformat(timespec="seconds") if e else None)
                        for s, e in readings[sid]])
    db.commit()
    return db


class EmptyTest(unittest.TestCase):
    """Every part works, returning nothing, when the tables aren't there yet."""

    def test_no_tables(self):
        db = sqlite3.connect(":memory:")
        self.assertEqual(learn_habits(db, FIRST), [])
        self.assertEqual(export_habits(db), {"habits": [], "readings": []})
        self.assertEqual(habit_today(db, datetime(2026, 9, 1, 8)), [])

    def test_no_patterns_yet(self):
        db = sqlite3.connect(":memory:")
        storage.init(db)
        self.assertEqual(learn_habits(db, FIRST), [])
        self.assertEqual(export_habits(db), {"habits": [], "readings": []})
        self.assertEqual(habit_today(db, datetime(2026, 9, 1, 8)), [])


class HandmadeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = handmade()
        cls.too_early = learn_habits(cls.db, FIRST + timedelta(days=2))  # three days: fewer than MIN_OCCURRENCES
        cls.new = learn_habits(cls.db, FIRST + timedelta(days=27))
        cls.habits = {tuple(h["patterns"]): h for h in export_habits(cls.db)["habits"]}
        cls.by_id = {h["id"]: h for h in cls.habits.values()}

    def test_first_days_make_no_habit(self):
        self.assertEqual(self.too_early, [])
        self.assertEqual({h["id"] for h in self.new}, {h["id"] for h in self.habits.values()})

    def test_lamp_then_kettle_is_one_weekday_habit(self):
        morning = self.habits[("pattern-1", "pattern-2")]
        self.assertEqual(morning["recurrence"]["text"], "every weekday")
        self.assertEqual(morning["window"]["usual"][:4], "07:0")
        lamp, kettle = morning["steps"]
        self.assertEqual(lamp["offset_min"], 0)
        self.assertTrue(8 <= kettle["offset_min"] <= 12, kettle)  # a link's step is its second part
        self.assertTrue(0.8 <= kettle["share"] < 1, kettle)
        self.assertEqual(morning["opportunities"], 20)  # the weekdays in four weeks

    def test_weekend_coffee_is_its_own_habit(self):
        """Near the weekday morning on the clock, but never on the same days: not together."""
        weekend = self.habits[("pattern-3",)]
        self.assertEqual((weekend["recurrence"]["weekdays"], weekend["recurrence"]["text"]), (["sat", "sun"], "every weekend"))

    def test_every_three_hours(self):
        pump = self.habits[tuple(f"pattern-{n}" for n in range(5, 10))]
        rec = pump["recurrence"]
        self.assertEqual((rec["kind"], rec["every_n_hours"], rec["text"]), ("hourly", 3, "every 3 hours from 11:00 to 23:00"))
        self.assertEqual([round(s["offset_min"] / 60) for s in pump["steps"]], [0, 3, 6, 9, 12])

    def test_a_pattern_with_nothing_to_join(self):
        door = self.habits[("pattern-4",)]
        self.assertEqual((door["recurrence"]["text"], door["steps"][0]["share"]), ("every day", 1.0))
        self.assertEqual(len(self.habits), 4)

    def test_shape(self):
        out = export_habits(self.db)
        for h in out["habits"]:
            self.assertEqual(list(h), HABIT_KEYS)  # the agreed shape, in its order
            self.assertEqual(set(h["recurrence"]), RECURRENCE_KEYS)
            self.assertEqual(set(h["window"]), {"usual", "earliest", "latest"})
            self.assertEqual(set(h["variation"]), {"spread_min", "by_weekday"})
            self.assertEqual(set(h["trend"]), {"minutes_per_week"})
            self.assertTrue(all(set(s) == {"pattern", "offset_min", "share"} for s in h["steps"]))
            self.assertEqual(h["patterns"], [s["pattern"] for s in h["steps"]])
            self.assertTrue(0 <= h["confidence"] <= 1)
            self.assertLessEqual(h["occurrences"], h["opportunities"])
        for r in out["readings"]:
            self.assertEqual(set(r), {"habit_id", "day", "start", "end", "steps_seen"})
        self.assertEqual(len({(r["habit_id"], r["day"]) for r in out["readings"]}), len(out["readings"]))

    def test_confidence_formula(self):
        """hit rate × n/(n+1) × timing; with timing at most 1."""
        for h in self.habits.values():
            n, opp = h["occurrences"], h["opportunities"]
            self.assertLessEqual(h["confidence"], round(n / opp * n / (n + 1), 2) + 0.005)
            self.assertGreater(h["confidence"], 0.5 * n / opp * n / (n + 1))

    def test_rerun_changes_nothing(self):
        before = export_habits(self.db)
        self.assertEqual(learn_habits(self.db, FIRST + timedelta(days=27)), [])
        self.assertEqual(export_habits(self.db), before)

    def test_today(self):
        def status(at: datetime) -> dict:
            return {tuple(self.by_id[t["habit_id"]]["patterns"]): t for t in habit_today(self.db, at)}
        monday = datetime(2026, 9, 28)  # the last day of the history, before the lamp
        early = status(monday + timedelta(hours=6))
        self.assertEqual(early[("pattern-1", "pattern-2")]["status"], "expected")
        self.assertEqual(early[("pattern-3",)]["status"], "not_today")  # a weekend habit on a Monday
        sunday = datetime(2026, 9, 27)
        evening = status(sunday + timedelta(hours=21, minutes=30))
        self.assertEqual(evening[("pattern-4",)]["status"], "done")
        pump = evening[tuple(f"pattern-{n}" for n in range(5, 10))]
        self.assertIn(pump["status"], ("now", "partly"))  # the 23:00 pump hasn't run yet
        self.assertTrue(pump["steps_done"])
        self.assertEqual(status(sunday + timedelta(hours=12))[("pattern-1", "pattern-2")]["status"], "not_today")


class FixtureTest(unittest.TestCase):
    """The hand-made 14 days (tests/fixtures/happenings-14d.jsonl): learn_day then learn_habits, day by day."""

    @classmethod
    def setUpClass(cls):
        cls.db = sqlite3.connect(":memory:")
        storage.init(cls.db)
        storage.add_happenings(cls.db, [json.loads(l) for l in FIXTURE.read_text().splitlines()])
        cls.new = {}
        for i in range(14):
            day = FIRST + timedelta(days=i)
            learn_day(cls.db, day)
            cls.new[day] = learn_habits(cls.db, day)
        cls.out = export_habits(cls.db)
        cls.sensors = Sensors(cls.db)

    def test_habits_appear_gradually(self):
        learned = {h["learned_at"] for h in self.out["habits"]}
        self.assertGreater(len(learned), 1)
        self.assertNotIn(FIRST.isoformat(), learned)
        for day, new in self.new.items():
            self.assertTrue(all(h["learned_at"] == day.isoformat() for h in new))

    def test_each_pattern_in_one_habit(self):
        seen = [p for h in self.out["habits"] if h["status"] == "active" for p in h["patterns"]]
        self.assertEqual(len(seen), len(set(seen)))
        sensors = {s["id"] for s in self.sensors.all}
        self.assertTrue(set(seen) <= sensors)
        self.assertGreaterEqual(len(set(seen)), len(sensors) - 1)

    def test_the_morning(self):
        tea = holding(self.db, self.sensors.find("2,100 W", steps=2)["id"])
        self.assertEqual(tea["recurrence"]["text"], "every day")
        self.assertTrue(near(tea["window"]["usual"], "07:30", 60), tea["window"])
        self.assertGreaterEqual(tea["occurrences"], 11)

    def test_readings(self):
        """One per habit and day; from its first day on, made of its own steps (earlier ones are history)."""
        habits = {h["id"]: h for h in self.out["habits"]}
        self.assertEqual(len({(r["habit_id"], r["day"]) for r in self.out["readings"]}), len(self.out["readings"]))
        for r in self.out["readings"]:
            self.assertTrue(r["steps_seen"])
            h = habits[r["habit_id"]]
            if h["status"] == "active" and r["day"] >= h["first_seen"]:
                self.assertTrue(set(r["steps_seen"]) <= set(h["patterns"]), r)

    def test_rerun_keeps_ids_and_dates(self):
        before = {h["id"]: h["learned_at"] for h in self.out["habits"]}
        self.assertEqual(learn_habits(self.db, FIRST + timedelta(days=13)), [])
        self.assertEqual({h["id"]: h["learned_at"] for h in export_habits(self.db)["habits"]}, before)

    def test_merged_ones_live_on(self):
        """A habit absorbed by another keeps its id, as `merged`, and its patterns are in a live one."""
        live = [set(h["patterns"]) for h in self.out["habits"] if h["status"] == "active"]
        for h in self.out["habits"]:
            if h["status"] == "merged":
                self.assertTrue(any(2 * len(set(h["patterns"]) & l) > len(h["patterns"]) for l in live), h)

    def test_today(self):
        night = self.sensors.find("quiet after", at="23:16")["id"]
        tea = self.sensors.find("2,100 W", steps=2)["id"]
        at = lambda hhmm: {t["habit_id"]: t for t in habit_today(self.db, datetime.combine(  # noqa: E731
            date(2026, 9, 14), time(*map(int, hhmm.split(":")))))}
        self.assertEqual(at("02:00")[holding(self.db, night)["id"]]["status"], "now")  # asleep
        self.assertEqual(at("06:00")[holding(self.db, tea)["id"]]["status"], "expected")
        morning = at("08:30")[holding(self.db, tea)["id"]]
        self.assertIn(morning["status"], ("done", "partly", "now"))
        self.assertIn(tea, morning["steps_done"])


# ---------- two people with phones (C2 signal names; C3 person and windows) ----------

ANA, JOAO = "c-8f3a", "c-2b71"
MONDAY = date(2026, 9, 7)


def two_people(days: int = 35, ana_name: str = "Ana's phone") -> sqlite3.Connection:
    """Ana and João, each with a phone the router reports (`phone:<client id>.connected`):
    - Ana gets home on weekdays about 18:40 and leaves at 08:10;
    - João gets home about 19:30, on Tuesdays at 21:00, and leaves at 08:30, but works from home on
      Wednesdays (his phone stays all day);
    - the door opens a minute after each arrival and before each departure, and a few times at random.
    Patterns are written by hand, as the habit learner would: each phone's arrival (a kind of its
    signal) and the door after it; João's Tuesday arrival is a pattern of its own at 21:00.
    """
    rng = random.Random(7)
    db = sqlite3.connect(":memory:")
    storage.init(db)
    db.execute("CREATE TABLE devices (device_id TEXT NOT NULL UNIQUE, type TEXT NOT NULL, name TEXT NOT NULL,"
               " first_ts INTEGER NOT NULL)")
    db.executemany("INSERT INTO devices VALUES (?, 'phone', ?, 0)", [(ANA, ana_name), (JOAO, "João's phone")])
    db.executemany("INSERT INTO kinds (id, signal, data) VALUES (?, ?, '{}')",
                   [(f"phone:{ANA}-A", f"phone:{ANA}.connected"), (f"phone:{JOAO}-A", f"phone:{JOAO}.connected"),
                    ("contactSensor-A", "contactSensor.sensorState")])
    rows, readings = [], defaultdict(list)

    def door(at: datetime, pattern: str | None = None):
        rows.append({"signal": "contactSensor.sensorState", "start": at.isoformat(), "device_id": "door",
                     "end": (at + timedelta(seconds=40)).isoformat(), "value": "alarm", "kind": "contactSensor-A"})
        if pattern:
            readings[pattern].append(at)

    home: dict[str, datetime] = {}  # phone → when it connected
    for i in range(days):
        day = datetime.combine(MONDAY + timedelta(days=i), time())
        wd = day.weekday()
        at = lambda clock, spread: day + timedelta(minutes=clock + rng.gauss(0, spread))  # noqa: E731
        leaving = {ANA: at(8 * 60 + 10, 5) if wd < 5 else None,
                   JOAO: at(8 * 60 + 30, 5) if wd < 5 and wd != 2 else None}
        for phone, out in leaving.items():
            if out and phone in home:
                rows.append({"signal": f"phone:{phone}.connected", "start": home.pop(phone).isoformat(),
                             "end": out.isoformat(), "value": "on", "device_id": phone, "kind": f"phone:{phone}-A"})
                door(out - timedelta(minutes=1))
        if wd < 5:
            home[ANA] = at(18 * 60 + 40, 5)
            readings["pattern-1"].append(home[ANA])
            door(home[ANA] + timedelta(minutes=1), "pattern-2")
            if wd != 2:
                home[JOAO] = at(21 * 60 if wd == 1 else 19 * 60 + 30, 5)
                readings["pattern-5" if wd == 1 else "pattern-3"].append(home[JOAO])
                door(home[JOAO] + timedelta(minutes=1), "pattern-6" if wd == 1 else "pattern-4")
        for _ in range(3):
            door(at(rng.uniform(10 * 60, 17 * 60), 0))
    storage.add_happenings(db, rows)
    patterns = {"pattern-1": (f"kind:phone:{ANA}-A", 18 * 60 + 40, WORK_DAYS),
                "pattern-2": ("kind:contactSensor-A", 18 * 60 + 41, WORK_DAYS),
                "pattern-3": (f"kind:phone:{JOAO}-A", 19 * 60 + 30, None),
                "pattern-4": ("kind:contactSensor-A", 19 * 60 + 31, None),
                "pattern-5": (f"kind:phone:{JOAO}-A", 21 * 60, ["tue"]),
                "pattern-6": ("kind:contactSensor-A", 21 * 60 + 1, ["tue"])}
    for sid, (signature, centre, weekdays) in patterns.items():
        pattern = {"parts": [signature], "time_of_day": f"{centre // 60:02d}:{centre % 60:02d}", "spread_min": 5,
                   "days_seen": len(readings[sid]), "days_total": days}
        if weekdays:
            pattern["weekdays"] = weekdays
        data = {"id": sid, "name": None, "named_by": None, "focus": "connected_life", "learner": "habit",
                "pattern": pattern, "learned_at": MONDAY.isoformat(), "confidence": 0.9, "status": "active"}
        db.execute("INSERT INTO sensors (id, learner, focus, signature, centre_min, width_min, learned_at, status, data)"
                   " VALUES (?, 'habit', 'connected_life', ?, ?, 20, ?, 'active', ?)",
                   (sid, signature, centre, MONDAY.isoformat(), json.dumps(data)))
        db.executemany("INSERT INTO sensor_readings (sensor_id, start, end, value) VALUES (?, ?, ?, NULL)",
                       [(sid, s.isoformat(timespec="seconds"), (s + timedelta(seconds=40)).isoformat(timespec="seconds"))
                        for s in readings[sid]])
    db.commit()
    return db


class PeopleTest(unittest.TestCase):
    """Whose a habit is (`person`) and its windows by weekday (`windows`): C3 in docs/demo-plan.md."""

    @classmethod
    def setUpClass(cls):
        cls.db = two_people()
        learn_habits(cls.db, MONDAY + timedelta(days=34))

    def by_person(self, phone: str, db=None) -> dict:
        return next(h for h in active(db or self.db) if (h["person"] or {}).get("phone") == phone)

    def test_one_habit_per_person(self):
        ana, joao = self.by_person(ANA), self.by_person(JOAO)
        self.assertEqual(ana["person"], {"phone": ANA, "name": "Ana's phone"})
        self.assertEqual(joao["person"], {"phone": JOAO, "name": "João's phone"})
        self.assertEqual(set(ana["patterns"]), {"pattern-1", "pattern-2"})  # never joined with João's, 50 min later
        # his 19:30 and his Tuesday 21:00 are never on the same day: one habit, his coming home
        self.assertEqual(set(joao["patterns"]), {"pattern-3", "pattern-4", "pattern-5", "pattern-6"})

    def test_a_tuesday_that_stands_out(self):
        joao = self.by_person(JOAO)
        tue = next(w for w in joao["windows"] if "tue" in w["days"])
        other = next(w for w in joao["windows"] if "mon" in w["days"])
        self.assertEqual(tue["days"], ["tue"])
        self.assertTrue({"mon", "thu", "fri"} <= set(other["days"]) and "tue" not in other["days"], joao["windows"])
        self.assertTrue(near(tue["usual"], "21:00", 5) and near(other["usual"], "19:30", 5), joao["windows"])
        self.assertTrue(tue["earliest"] <= tue["usual"] <= tue["latest"])
        self.assertEqual(set(joao["variation"]["by_weekday"]), {"tue"})

    def test_no_difference_is_one_window(self):
        ana = self.by_person(ANA)
        [w] = ana["windows"]
        self.assertEqual(w["days"], ana["recurrence"]["weekdays"] or ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
        self.assertTrue(near(w["usual"], "18:40", 5), w)
        self.assertEqual((w["earliest"], w["latest"]), (ana["window"]["earliest"], ana["window"]["latest"]))

    def test_a_guests_phone_is_nobody(self):
        db = two_people(ana_name="")  # the router doesn't know it: a guest's
        learn_habits(db, MONDAY + timedelta(days=34))
        ana = next(h for h in active(db) if "pattern-1" in h["patterns"])
        self.assertIsNone(ana["person"])

    def test_habits_without_phones_are_nobodys(self):
        db = handmade()
        learn_habits(db, FIRST + timedelta(days=27))
        self.assertTrue(all(h["person"] is None for h in export_habits(db)["habits"]))


class RosaTest(unittest.TestCase):
    """rosa, 21 days: wakes about 07:45 (45 min later at weekends), lamp on, door, kettle."""

    @classmethod
    def setUpClass(cls):
        cls.db = simulated("rosa", 21)
        cls.sensors = Sensors(cls.db)

    def test_waking_up_is_one_habit(self):
        """light → kettle, the end of the night's quiet and light → door: one habit, Waking up."""
        kettle = self.sensors.find("2,100 W", steps=2)["id"]
        door = self.sensors.find("bulb-", "contactSensor-", steps=2)["id"]
        awake = self.sensors.find("end of home-", at="07:59")["id"]
        waking = holding(self.db, kettle)
        self.assertIsNotNone(waking)
        self.assertTrue({kettle, door, awake} <= set(waking["patterns"]), waking["patterns"])
        steps = {s["pattern"]: s for s in waking["steps"]}
        self.assertEqual(waking["patterns"][0], awake)  # the quiet ends first, then the door, then the kettle
        self.assertLess(steps[door]["offset_min"], steps[kettle]["offset_min"])

    def test_every_day_later_at_weekends(self):
        waking = holding(self.db, self.sensors.find("2,100 W", steps=2)["id"])
        self.assertEqual((waking["recurrence"]["kind"], waking["recurrence"]["text"]), ("daily", "every day"))
        self.assertTrue(near(waking["window"]["usual"], "07:45", 15), waking["window"])
        shift = waking["variation"]["by_weekday"]
        self.assertEqual(set(shift), {"sat", "sun"})
        self.assertTrue(all(25 <= v <= 65 for v in shift.values()), shift)
        self.assertGreaterEqual(waking["confidence"], 0.75)
        self.assertEqual(waking["trend"]["minutes_per_week"], 0)

    def test_night_and_evening(self):
        night = holding(self.db, self.sensors.find("quiet after", at="23:07")["id"])
        self.assertEqual(night["recurrence"]["text"], "every day")
        self.assertTrue(near(night["window"]["usual"], "23:07", 20))


class CoupleTest(unittest.TestCase):
    """couple, 21 days: up 06:50 on weekdays and 90 min later at weekends, coffee soon after, out at work."""

    @classmethod
    def setUpClass(cls):
        cls.db = simulated("couple", 21)
        cls.sensors = Sensors(cls.db)

    def test_a_weekday_habit(self):
        """Up at 06:50 on weekdays: the weekday window of the habit (docs/demo-plan.md, C3)."""
        waking = holding(self.db, self.sensors.find("1,300 W", at="06:50", steps=2)["id"])
        weekday = next(w for w in waking["windows"] if "mon" in w["days"])
        self.assertEqual(weekday["days"], WORK_DAYS)
        self.assertTrue(near(weekday["usual"], "06:50", 15), weekday)
        self.assertGreaterEqual(waking["confidence"], 0.75)

    def test_and_a_weekend_variant(self):
        """The same steps 90 minutes later at weekends: the same habit, with a weekend window (C3), so the
        forecast and the wakes know Saturday's time as well as Monday's."""
        waking = holding(self.db, self.sensors.find("1,300 W", at="06:50", steps=2)["id"])
        later = [s for s in self.sensors.all if "1,300 W" in s["pattern"]["parts"][-1]
                 and near(s["pattern"]["time_of_day"], "08:20", 30)]
        if later:
            self.assertEqual(holding(self.db, later[0]["id"])["id"], waking["id"])
        weekend = next(w for w in waking["windows"] if "sat" in w["days"])
        self.assertEqual(weekend["days"], ["sat", "sun"])
        self.assertTrue(near(weekend["usual"], "08:20", 30), weekend)
        self.assertEqual(waking["recurrence"]["text"], "every day")

    def test_ids_stay(self):
        """Habits learned apart that turn out to be one keep their ids: the absorbed ones are `merged`."""
        habits = export_habits(self.db)["habits"]
        self.assertEqual(len({h["id"] for h in habits}), len(habits))
        live = [set(h["patterns"]) for h in habits if h["status"] == "active"]
        for h in habits:
            if h["status"] == "merged":
                self.assertTrue(any(2 * len(set(h["patterns"]) & l) > len(h["patterns"]) for l in live), h)


if __name__ == "__main__":
    unittest.main()
