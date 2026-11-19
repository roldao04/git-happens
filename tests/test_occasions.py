"""Part C, occasions (source/learn/occasions.py): parties planted in a simulated rosa are found,
grouped and recognised live; an ordinary month and a lookalike are not.

  uv run python -m unittest tests.test_occasions -v      # one 21-day simulation, about 10 s

The parties are planted in the stored happenings of one simulated history (the simulator's own parties
are part D's): many door openings 19:00-01:00, the light on until about 01:10, a kettle for the guests.
"""
import json
import logging
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from source.learn import Happening, storage
from source.learn.kinds import classify
from source.learn.occasions import export_occasions, learn_occasions, match_now
from source.pipeline import run
from source.simulate import generate

FIRST = date(2026, 9, 1)
DAYS = [FIRST + timedelta(days=i) for i in range(21)]
PARTIES = [date(2026, 9, 11), date(2026, 9, 15), date(2026, 9, 19)]  # Fri, Tue, Sat
FOURTH = datetime(2026, 9, 21, 22, 30)  # partway through a fourth one (Mon)
PLUMBER = date(2026, 9, 16)  # a burst of door openings mid-morning, nothing else
WORKMAN = date(2026, 9, 18)  # mid-morning too, with the light and a power tool: unusual, but no party
OCCASION_KEYS = {"id", "name", "named_by", "episodes", "fingerprint", "usual_hours", "weekdays", "confidence"}
FINGERPRINT_KEYS = {"door_openings", "light_hours_after_21", "quiet_start", "plug_uses"}
MATCH_KEYS = {"occasion_id", "similarity", "confidence", "since", "explains"}


def minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def devices(db) -> dict[str, str]:
    return dict(db.execute("SELECT signal, device_id FROM happenings GROUP BY signal"))


def door(dev: dict, at: datetime, seconds: int = 40) -> dict:
    return {"signal": "contactSensor.sensorState", "start": at.isoformat(), "end": (at + timedelta(seconds=seconds)).isoformat(),
            "value": "alarm", "device_id": dev["contactSensor.sensorState"], "capability": "sensorState"}


def plant_party(db, evening: date, shift: int, arrive: tuple, leave: tuple, lamp_off: int) -> None:
    """Guests arrive and leave (minutes after 19:00 + shift), the lamp stays on, a kettle at 21:30."""
    dev = devices(db)
    base = datetime.combine(evening, datetime.min.time()) + timedelta(hours=19, minutes=shift)
    db.execute("DELETE FROM happenings WHERE signal = 'bulb.power' AND start_ms >= ? AND start_ms < ?",
               (storage.to_ms(base - timedelta(hours=3)), storage.to_ms(base + timedelta(hours=9))))
    rows = [{"signal": "bulb.power", "start": (base - timedelta(minutes=30)).isoformat(),
             "end": (base + timedelta(minutes=lamp_off)).isoformat(), "value": "on",
             "device_id": dev["bulb.power"], "capability": "power"}]
    rows += [door(dev, base + timedelta(minutes=m)) for m in arrive + leave]
    kettle = base + timedelta(minutes=150)
    kind = classify(Happening("plug.instantPower", kettle, kettle + timedelta(minutes=3), 2100.0), storage.load_kinds(db))
    rows.append({"signal": "plug.instantPower", "start": kettle.isoformat(), "end": (kettle + timedelta(minutes=3)).isoformat(),
                 "size": 2100.0, "peak": 2110.0, "device_id": dev["plug.instantPower"], "capability": "instantPower",
                 "kind": kind})
    storage.add_happenings(db, rows)


def plant_visit(db, day: date, light: bool, tool: bool) -> None:
    """Someone comes and goes eight times between 10:00 and 11:30 (a plumber), maybe with the light and a tool."""
    dev = devices(db)
    base = datetime.combine(day, datetime.min.time()) + timedelta(hours=10)
    rows = [door(dev, base + timedelta(minutes=m), 50) for m in (2, 9, 25, 31, 50, 64, 80, 88)]
    if light:
        rows.append({"signal": "bulb.power", "start": (base + timedelta(minutes=5)).isoformat(),
                     "end": (base + timedelta(minutes=85)).isoformat(), "value": "on",
                     "device_id": dev["bulb.power"], "capability": "power"})
    if tool:
        rows += [{"signal": "plug.instantPower", "start": (base + timedelta(minutes=m)).isoformat(),
                  "end": (base + timedelta(minutes=m + 6)).isoformat(), "size": 800.0, "peak": 820.0,
                  "device_id": dev["plug.instantPower"], "capability": "instantPower"} for m in (15, 40, 60)]
    storage.add_happenings(db, rows)


def replay(db, live: dict[date, list[datetime]]) -> tuple[dict, dict, dict]:
    """learn_occasions at each day end, as the pipeline will; match_now at the given moments, each with
    what was known during that day (before that day's end)."""
    new, after, matched = {}, {}, {}
    for day in DAYS:
        for at in live.get(day, []):
            matched[at] = match_now(db, at)
        new[day] = learn_occasions(db, day)
        after[day] = export_occasions(db)
    return new, after, matched


class OccasionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        lines, _ = generate("rosa", len(DAYS))
        logging.disable(logging.INFO)  # the pipeline logs one line per day
        try:
            run(((row["ts"], row["home"]) for row in map(json.loads, lines)), root / "rosa")
        finally:
            logging.disable(logging.NOTSET)
        cls.plain_path, cls.party_path = root / "rosa" / "home.sqlite3", root / "party.sqlite3"
        shutil.copy(cls.plain_path, cls.party_path)

        plain = sqlite3.connect(cls.plain_path)
        cls.plain_new, cls.plain_after, _ = replay(plain, {})
        plain.close()

        db = sqlite3.connect(cls.party_path)
        plant_party(db, PARTIES[0], 0, (5, 20, 40, 65, 80), (280, 305, 325, 340, 355), 370)
        plant_party(db, PARTIES[1], 10, (5, 25, 45, 70), (275, 300, 330, 350, 360, 365), 360)
        plant_party(db, PARTIES[2], -5, (8, 22, 35, 60, 75, 90), (290, 310, 335, 345), 380)
        plant_party(db, FOURTH.date(), 5, (5, 20, 40, 65, 80), (280, 305, 325, 340, 355), 370)
        plant_visit(db, PLUMBER, light=False, tool=False)
        plant_visit(db, WORKMAN, light=True, tool=True)
        cls.ordinary_evening = datetime(2026, 9, 20, 22, 0)
        cls.new, cls.after, cls.live = replay(db, {
            PLUMBER: [datetime(2026, 9, 16, 10, 50)], WORKMAN: [datetime(2026, 9, 18, 10, 50)],
            cls.ordinary_evening.date(): [cls.ordinary_evening], FOURTH.date(): [FOURTH, FOURTH + timedelta(hours=1, minutes=20)]})
        quiets = [s for s in (json.loads(d) for (d,) in db.execute("SELECT data FROM sensors WHERE status = 'active'"))
                  if s["pattern"]["parts"][0].startswith("home-") and "until" in s["pattern"]]
        bedtime = min(quiets, key=lambda s: abs(minutes(s["pattern"]["time_of_day"]) - minutes("23:10")))["id"]
        cls.bedtime_pattern = bedtime
        # `explains` names the habit the night's quiet belongs to (learn_day learns habits too), else the pattern
        habits = [json.loads(d) for (d,) in db.execute("SELECT data FROM habits WHERE status = 'active'")]
        cls.bedtime = next((h["id"] for h in habits if bedtime in h["patterns"]), bedtime)
        db.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def occasion(self, day: date, oid: str = "occasion-1") -> dict:
        return next(o for o in self.after[day]["occasions"] if o["id"] == oid)

    def test_an_ordinary_month_has_no_occasions(self):
        self.assertTrue(all(new == [] for new in self.plain_new.values()))
        self.assertEqual(self.plain_after[DAYS[-1]], {"occasions": [], "episodes": []})

    def test_the_first_party_is_an_occasion_of_one(self):
        day = PARTIES[0] + timedelta(days=1)  # judged once its evening is over
        self.assertEqual([o["id"] for o in self.new[day]], ["occasion-1"])
        first = self.new[day][0]
        self.assertEqual(first["episodes"], 1)
        self.assertLessEqual(first["confidence"], 0.5)  # one example: a suggestion at most
        self.assertIsNone(first["usual_hours"])
        self.assertIsNone(first["weekdays"])
        self.assertTrue(all(new == [] for d, new in self.new.items() if d < day))

    def test_the_next_parties_join_it(self):
        two, three = (self.occasion(p + timedelta(days=1)) for p in PARTIES[1:])
        self.assertEqual((two["episodes"], three["episodes"]), (2, 3))
        self.assertEqual(self.new[PARTIES[1] + timedelta(days=1)], [])
        self.assertEqual(self.new[PARTIES[2] + timedelta(days=1)], [])
        self.assertEqual((two["confidence"], three["confidence"]), (0.67, 0.75))  # n/(n+1): the parties are alike
        self.assertEqual(three["weekdays"], ["tue", "fri", "sat"])
        self.assertEqual(three["usual_hours"][0][:2], "19")
        fp = three["fingerprint"]
        self.assertTrue(FINGERPRINT_KEYS <= set(fp), fp)
        self.assertGreaterEqual(fp["door_openings"], 8)
        self.assertGreaterEqual(fp["light_hours_after_21"], 3.5)
        self.assertLess(fp["quiet_start"], "03:00")  # after midnight: a late night
        self.assertEqual(fp["plug_uses"], 1)
        self.assertIn(f"{self.bedtime} late", three["explains"])  # bedtime (the night's quiet) is later

    def test_live_partway_through_the_fourth(self):
        for at in (FOURTH, FOURTH + timedelta(hours=1, minutes=20)):
            [m] = [m for m in self.live[at] if m["occasion_id"] == "occasion-1"]
            self.assertEqual(set(m), MATCH_KEYS)
            self.assertGreaterEqual(m["confidence"], 0.75, m)
            self.assertTrue("2026-09-21T19:00" <= m["since"] <= "2026-09-21T19:30", m)
            self.assertIn(f"{self.bedtime} late", m["explains"])

    def test_lookalikes_are_not_parties(self):
        self.assertEqual(self.live[datetime(2026, 9, 16, 10, 50)], [])  # the plumber, while he is there
        self.assertEqual(self.live[datetime(2026, 9, 18, 10, 50)], [])  # the workman, while he is there
        episodes = self.after[DAYS[-1]]["episodes"]
        self.assertFalse([e for e in episodes if e["start"].startswith(PLUMBER.isoformat())])  # doors alone: no episode
        [work] = [e for e in episodes if e["start"].startswith(WORKMAN.isoformat())]  # doors, light and a tool: one
        self.assertNotEqual(work["occasion_id"], "occasion-1")
        self.assertEqual([o["id"] for o in self.new[WORKMAN + timedelta(days=1)]], [work["occasion_id"]])
        self.assertEqual(self.occasion(DAYS[-1], work["occasion_id"])["episodes"], 1)

    def test_an_ordinary_evening_matches_nothing(self):
        self.assertEqual(self.live[self.ordinary_evening], [])

    def test_agreed_shapes(self):
        out = self.after[DAYS[-1]]
        self.assertEqual(set(out), {"occasions", "episodes"})
        for o in out["occasions"]:
            self.assertTrue(OCCASION_KEYS <= set(o), o)
            self.assertRegex(o["id"], r"^occasion-\d+$")
            self.assertTrue(0 <= o["confidence"] <= o["episodes"] / (o["episodes"] + 1))
        ids = {o["id"] for o in out["occasions"]}
        self.assertTrue(all(e["occasion_id"] in ids and e["start"] < e["end"] for e in out["episodes"]))
        self.assertEqual(sum(o["episodes"] for o in out["occasions"]), len(out["episodes"]))

    def test_running_days_again_keeps_ids(self):
        db = self.copy()
        before = export_occasions(db)
        self.assertEqual(learn_occasions(db, PARTIES[2] + timedelta(days=1)), [])
        for day in DAYS[DAYS.index(PARTIES[2]) + 2:]:
            learn_occasions(db, day)
        self.assertEqual(export_occasions(db), before)
        self.assertEqual([o["id"] for o in learn_occasions(db, WORKMAN + timedelta(days=1))], ["occasion-2"])

    def test_explains_names_habits_once_they_exist(self):
        db = self.copy()
        db.execute("DELETE FROM habits")  # one habit, made by hand, holding the night's quiet
        db.execute("INSERT INTO habits VALUES ('habit-99', 'active', ?)",
                   (json.dumps({"patterns": [self.bedtime_pattern]}),))
        [m] = [m for m in match_now(db, FOURTH) if m["occasion_id"] == "occasion-1"]
        self.assertEqual(m["explains"], ["habit-99 late"])

    def test_the_light_still_on_is_read_from_events(self):
        db = self.copy()  # a happening is stored once it ends: tonight's lamp is not there yet
        db.execute("DELETE FROM happenings WHERE signal = 'bulb.power' AND start_ms >= ? AND start_ms < ?",
                   (storage.to_ms(datetime(2026, 9, 21, 18)), storage.to_ms(datetime(2026, 9, 21, 19))))
        [m] = [m for m in match_now(db, FOURTH) if m["occasion_id"] == "occasion-1"]
        self.assertGreaterEqual(m["confidence"], 0.75, m)

    def test_missing_tables_give_empty_results(self):
        db = sqlite3.connect(":memory:")
        self.assertEqual(match_now(db, FOURTH), [])
        self.assertEqual(export_occasions(db), {"occasions": [], "episodes": []})
        self.assertEqual(learn_occasions(db, DAYS[-1]), [])
        storage.init(db)  # the learners' tables, no data
        self.assertEqual(learn_occasions(db, DAYS[-1]), [])
        self.assertEqual(match_now(db, FOURTH), [])

    def copy(self) -> sqlite3.Connection:
        path = Path(self.tmp.name) / f"{self.id().rsplit('.', 1)[-1]}.sqlite3"
        shutil.copy(self.party_path, path)
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        return db


if __name__ == "__main__":
    unittest.main()
