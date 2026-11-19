"""Learned wakes (source/wake.py, docs/demo-plan.md C8): habit soon, arrival, departure, a missing
habit, an occasion; each once per key.

Hand-written habits (C3 shape: `person`, `windows`), forecast entries, occasion matches and a
warm-up stand in for the learners (parts B and C are built separately). No network.

  uv run python -m unittest tests.test_wake -v
"""
import json
import sqlite3
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from source import wake
from source.learn import forecast as forecast_module
from source.learn import to_ms
from source.model import Reading
from source.store import SCHEMA as STORE_SCHEMA
from tests.test_anticipate import HABIT_TABLES, habit, part

NOW = datetime(2026, 11, 19, 18, 10)  # Thursday, day 60 of the demo house
ANA, JOAO, GUEST = "c-8f3a", "c-2b71", "c-91d0"
TH, RT = "th-1", "rt-1"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]


def ana_home_habit(**card) -> dict:
    h = habit("habit-4", "Ana gets home", [("pattern-9", 0, 0.95)], "18:40", earliest="18:25", latest="18:55",
              confidence=0.9, occurrences=40, opportunities=42, kind="weekly", text="every weekday", **card)
    h["person"] = {"phone": ANA, "name": "Ana's phone"}
    h["windows"] = [{"days": WEEKDAYS, "usual": "18:40", "earliest": "18:25", "latest": "18:55"}]
    return h


def waking_habit(**card) -> dict:
    h = habit("habit-1", "Waking up", [("pattern-8", 0, 0.9)], "07:10", earliest="06:55", latest="09:10",
              confidence=0.85, occurrences=55, opportunities=60, **card)
    h["windows"] = [{"days": WEEKDAYS, "usual": "07:10", "earliest": "06:55", "latest": "07:30"},
                    {"days": ["sat", "sun"], "usual": "08:40", "earliest": "08:20", "latest": "09:10"}]
    return h


def db_with(*habits) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(STORE_SCHEMA)  # events, as the loop stores them
    db.executescript(HABIT_TABLES)
    for h in habits:
        db.execute("INSERT INTO habits VALUES (?, ?, ?)", (h["id"], h["status"], json.dumps(h)))
    db.commit()
    return db


def clients(*phones) -> list[dict]:
    names = {ANA: "Ana's phone", JOAO: "João's phone"}
    return [{"id": p, "name": names.get(p, ""), "kind": "phone", "known": p in names} for p in phones]


def state(*, phones=(), target=16.0, room=19.0, thermostat=True) -> dict:
    s = {(RT, "connectedClients"): Reading(RT, "Router", "connectedClients", json.dumps(clients(*phones)),
                                           1, "router")}
    if thermostat:
        for cap, v in (("temperatureValue", room), ("targetTemperature", target), ("mode", "heat"),
                       ("heatingState", "off")):
            s[(TH, cap)] = Reading(TH, "Thermostat", cap, v, 1, "thermostat")
    return s


def phone_change(db, at: datetime, old: tuple, new: tuple) -> None:
    db.execute("INSERT INTO events (ts, device_id, device, capability, kind, old, new) VALUES (?, ?, ?, ?, ?, ?, ?)",
               (to_ms(at), RT, "Router", "connectedClients", "change",
                json.dumps(json.dumps(clients(*old))), json.dumps(json.dumps(clients(*new)))))
    db.commit()


def ana_forecast(probability=0.9, expected="18:40"):
    return lambda db, now, hours=24: [{"habit_id": "habit-4", "date": "2026-11-19", "probability": probability,
                                       "expected": expected, "window": ["18:25", "18:55"], "confidence": 0.81,
                                       "basis": "Thursdays: 8 of 9 since 21 Sept"}]


WARMUP = {"rate_c_per_h": 4.8, "loss_c_per_h": 0.6, "samples": 23, "confidence": 0.82, "as_of": "2026-11-19"}


class Isolated(unittest.TestCase):
    """The occasions and habits-today parts are missing unless a test stands one in; the warm-up
    says 25 min."""

    def setUp(self):
        self.enterContext(part("source.learn.occasions"))
        self.enterContext(part("source.learn.grouping"))
        self.enterContext(part("source.learn.warmup", warmup=lambda db, now: WARMUP,
                               lead_minutes=lambda db, now, a, b: 25.0))
        self.enterContext(mock.patch.object(forecast_module, "forecast", lambda db, now, hours=24: []))


class DeadlineTest(unittest.TestCase):
    def test_a_window_past_midnight_belongs_to_the_evening_before(self):
        bedtime = {"windows": [{"days": list(wake.WEEKDAYS), "usual": "23:29", "earliest": "22:55", "latest": "00:00"}]}
        self.assertEqual(wake._deadline(bedtime, datetime(2026, 11, 13, 0, 25)),
                         (datetime(2026, 11, 13, 0, 20), date(2026, 11, 12)))
        self.assertIsNone(wake._deadline(bedtime, datetime(2026, 11, 13, 0, 10)))
        morning = {"window": {"usual": "07:08", "earliest": "06:42", "latest": "07:26"}}
        self.assertEqual(wake._deadline(morning, datetime(2026, 11, 13, 8, 0)),
                         (datetime(2026, 11, 13, 7, 46), date(2026, 11, 13)))


class DeadTimeTest(unittest.TestCase):
    def test_a_first_step_that_heats_nothing(self):
        """16 → 21 °C is 19 then 21; with the room already at 19 °C the first write heats nothing for 10 min."""
        self.assertEqual(wake._dead_time({"target_c": 16.0, "room_c": 18.8, "comfort_c": 21.0}), 10.0)
        self.assertEqual(wake._dead_time({"target_c": 16.0, "room_c": 18.5, "comfort_c": 21.0}), 0.0)  # it heats
        self.assertEqual(wake._dead_time({"target_c": 18.0, "room_c": 19.0, "comfort_c": 21.0}), 0.0)  # one write
        self.assertEqual(wake._dead_time({"target_c": None, "room_c": 19.0}), 0.0)


class HabitSoonTest(Isolated):
    def test_fires_once_lead_time_plus_15_before(self):
        db = db_with(ana_home_habit())
        with mock.patch.object(forecast_module, "forecast", ana_forecast()):
            self.assertEqual(wake.learned_wakes(db, NOW - timedelta(minutes=11), state(room=18.5)), [])  # 41 min ahead
            (w,) = wake.learned_wakes(db, NOW - timedelta(minutes=10), state(room=18.5))  # 40 min: 25 min warm-up + 15
            self.assertEqual(wake.learned_wakes(db, NOW + timedelta(minutes=1), state(room=18.5)), [])  # once per key

        self.assertEqual((w.trigger_id, w.kind, w.key), ("habit_soon", "habit_soon", "habit_soon:habit-4:2026-11-19"))
        base = 0.9 * 0.9 * 40 / 41  # habit confidence × probability × n / (n + 1)
        self.assertAlmostEqual(w.data["confidence"], round(base, 3))
        d = w.data
        self.assertEqual((d["lead_minutes"], d["prepare_at"], d["expected_at"]),
                         (25.0, "2026-11-19T18:15:00", "2026-11-19T18:40:00"))
        self.assertEqual(d["prepare"], ["heating"])
        self.assertEqual(d["habit"]["person"], {"phone": ANA, "name": "Ana's phone"})
        self.assertEqual(d["habit"]["windows"][0]["days"], WEEKDAYS)
        self.assertEqual(d["forecast"]["basis"], "Thursdays: 8 of 9 since 21 Sept")
        self.assertEqual((d["home"]["room_c"], d["home"]["target_c"], d["home"]["phones_home"]), (18.5, 16.0, []))
        self.assertEqual(d["warmup"], WARMUP)
        self.assertEqual(d["planned"], [])
        self.assertIn("Ana gets home expected around 18:40", w.reason)
        self.assertEqual(w.to_dict()["key"], w.key)

        rec = wake.wake_record(db, w.key)  # what decide() reads the base from
        self.assertEqual((rec["kind"], rec["habit_id"], rec["base"], rec["arrival"]),
                         ("habit_soon", "habit-4", round(base, 3), "2026-11-19T18:40:00"))

    def test_unlikely_or_nothing_to_prepare(self):
        db = db_with(ana_home_habit())
        with mock.patch.object(forecast_module, "forecast", ana_forecast(probability=0.4)):
            self.assertEqual(wake.learned_wakes(db, NOW, state()), [])
        with mock.patch.object(forecast_module, "forecast", ana_forecast()):
            self.assertEqual(wake.learned_wakes(db, NOW, state(target=21, room=21.5)), [])  # already warm
            self.assertEqual(wake.learned_wakes(db, NOW, state(thermostat=False)), [])  # no heating, no lamp
            self.assertEqual(len(wake.learned_wakes(db, NOW, state())), 1)

    def test_without_the_warmup_the_lead_is_30_min(self):
        db = db_with(ana_home_habit())
        with part("source.learn.warmup"), mock.patch.object(forecast_module, "forecast", ana_forecast()):
            self.assertEqual(wake.learned_wakes(db, datetime(2026, 11, 19, 17, 54), state(room=18.5)), [])  # 46 min
            (w,) = wake.learned_wakes(db, datetime(2026, 11, 19, 17, 55), state(room=18.5))  # 45 = 30 + 15
        self.assertEqual((w.data["lead_minutes"], w.data["warmup"]), (30, None))

    def test_past_occurrence_never_wakes(self):
        db = db_with(ana_home_habit())
        with mock.patch.object(forecast_module, "forecast", ana_forecast(expected="18:00")):
            self.assertEqual(wake.learned_wakes(db, NOW, state()), [])


class PresenceTest(Isolated):
    def test_arrival_of_a_known_phone(self):
        db = db_with(ana_home_habit())
        phone_change(db, NOW - timedelta(minutes=1), (), (ANA,))
        (w,) = wake.learned_wakes(db, NOW, state(phones=(ANA,)))
        self.assertEqual((w.kind, w.key, w.data["confidence"]), ("arrival", f"arrival:{ANA}:2026-11-19:18", 0.8))
        self.assertEqual(w.data["phone"], {"id": ANA, "name": "Ana's phone"})
        self.assertEqual([h["id"] for h in w.data["habits"]], ["habit-4"])  # her own habit
        self.assertEqual(wake.wake_record(db, w.key)["habit_id"], "habit-4")
        self.assertEqual(wake.learned_wakes(db, NOW + timedelta(minutes=1), state(phones=(ANA,))), [])

    def test_guests_and_old_changes_dont_wake(self):
        db = db_with()
        phone_change(db, NOW - timedelta(minutes=1), (ANA,), (ANA, GUEST))  # a guest's phone
        phone_change(db, NOW - timedelta(minutes=30), (), (ANA,))  # too long ago
        self.assertEqual(wake.learned_wakes(db, NOW, state(phones=(ANA, GUEST))), [])
        self.assertEqual(wake.home_now(state(phones=(ANA, GUEST)))["guest_phones"], 1)

    def test_departure_of_the_last_known_phone(self):
        db = db_with()
        phone_change(db, NOW - timedelta(minutes=2), (ANA, JOAO), (ANA,))  # João leaves, Ana stays: no wake
        self.assertEqual(wake.learned_wakes(db, NOW, state(phones=(ANA,))), [])
        db = db_with()
        phone_change(db, NOW - timedelta(minutes=2), (ANA,), ())
        (w,) = wake.learned_wakes(db, NOW, state(phones=()))
        self.assertEqual((w.kind, w.key), ("departure", f"departure:{ANA}:2026-11-19:18"))

    def test_per_phone_signals_too(self):
        """Once normalize gives one `connected` signal per phone (C2), they are read the same way."""
        db = db_with()
        db.execute("INSERT INTO events (ts, device_id, device, capability, kind, old, new) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (to_ms(NOW - timedelta(minutes=1)), f"phone:{ANA}", "Ana's phone", "connected", "change",
                    json.dumps("off"), json.dumps("on")))
        s = {(f"phone:{ANA}", "connected"): Reading(f"phone:{ANA}", "Ana's phone", "connected", "on", 1, "phone"),
             (f"phone:{GUEST}", "connected"): Reading(f"phone:{GUEST}", "phone", "connected", "on", 1, "phone")}
        self.assertEqual([p["id"] for p in wake.phones(s) if p["known"]], [ANA])
        (w,) = wake.learned_wakes(db, NOW, s)
        self.assertEqual(w.key, f"arrival:{ANA}:2026-11-19:18")


class HabitMissingTest(Isolated):
    MORNING = datetime(2026, 11, 19, 7, 55)  # Thursday: weekday window latest 07:30, + 20 min

    def test_missing_weekday_habit_with_someone_home(self):
        db = db_with(waking_habit())
        self.assertEqual(wake.learned_wakes(db, self.MORNING - timedelta(minutes=10), state(phones=(ANA,))), [])
        (w,) = wake.learned_wakes(db, self.MORNING, state(phones=(ANA,)))
        self.assertEqual((w.kind, w.key), ("habit_missing", "habit_missing:habit-1:2026-11-19"))
        self.assertAlmostEqual(w.data["confidence"], round(0.85 * 55 / 56, 3))
        self.assertEqual(w.data["deadline"], "2026-11-19T07:50:00")

    def test_not_when_done_nobody_home_or_unimportant(self):
        db = db_with(waking_habit())
        self.assertEqual(wake.learned_wakes(db, self.MORNING, state(phones=())), [])  # nobody known home
        db.execute("INSERT INTO habit_readings VALUES ('habit-1', '2026-11-19', '2026-11-19T07:05:00', NULL, '[]')")
        self.assertEqual(wake.learned_wakes(db, self.MORNING, state(phones=(ANA,))), [])  # it happened
        self.assertEqual(wake.learned_wakes(db_with(waking_habit(importance="low")), self.MORNING,
                                            state(phones=(ANA,))), [])
        self.assertEqual(wake.learned_wakes(db_with(waking_habit()), self.MORNING + timedelta(hours=4),
                                            state(phones=(ANA,))), [])  # old news

    def test_the_status_from_the_habits_part_decides_when_there(self):
        db = db_with(waking_habit())
        with part("source.learn.grouping", habit_today=lambda db, now: [{"habit_id": "habit-1", "status": "done"}]):
            self.assertEqual(wake.learned_wakes(db, self.MORNING, state(phones=(ANA,))), [])
        with part("source.learn.grouping", habit_today=lambda db, now: [{"habit_id": "habit-1", "status": "late"}]):
            self.assertEqual(len(wake.learned_wakes(db, self.MORNING, state(phones=(ANA,)))), 1)

    def test_weekend_window(self):
        sat = datetime(2026, 11, 21, 9, 35)  # weekend latest 09:10 + 20
        self.assertEqual(len(wake.learned_wakes(db_with(waking_habit()), sat, state(phones=(ANA,)))), 1)
        self.assertEqual(wake.learned_wakes(db_with(waking_habit()), datetime(2026, 11, 21, 7, 55),
                                            state(phones=(ANA,))), [])  # a weekday's deadline isn't Saturday's


class OccasionTest(Isolated):
    FRIDAY = datetime(2026, 11, 13, 20, 35)

    def occasion_db(self):
        db = db_with()
        db.execute("CREATE TABLE occasions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT INTO occasions VALUES ('occasion-1', ?)", (json.dumps(
            {"id": "occasion-1", "name": "Friday night gathering", "episodes": 3,
             "fingerprint": {"door_openings": 9, "guest_phones": 5}, "usual_hours": ["20:00", "01:30"],
             "weekdays": ["fri"], "confidence": 0.75, "means": "friends over"}),))
        return db

    def match(self, confidence):
        return lambda db, now: [{"occasion_id": "occasion-1", "similarity": 0.8, "confidence": confidence,
                                 "since": "2026-11-13T20:00:00", "explains": ["habit-1 late"],
                                 "shifts": [{"habit_id": "habit-1", "date": "2026-11-14", "minutes": 90}]}]

    def test_fires_when_the_match_reaches_half(self):
        db = self.occasion_db()
        shifted = {"habit_id": "habit-1", "date": "2026-11-14", "probability": 0.8, "expected": "09:30",
                   "window": ["09:10", "10:00"], "confidence": 0.7, "basis": "Saturdays",
                   "shifted": {"occasion_id": "occasion-1", "minutes": 90,
                               "before": {"expected": "08:05", "window": ["07:50", "08:30"]}}}
        with part("source.learn.occasions", match_now=self.match(0.4)):
            self.assertEqual(wake.learned_wakes(db, self.FRIDAY, state(phones=(ANA, GUEST))), [])
        with part("source.learn.occasions", match_now=self.match(0.9)), \
                mock.patch.object(forecast_module, "forecast", lambda db, now, hours=24: [shifted]):
            (w,) = wake.learned_wakes(db, self.FRIDAY, state(phones=(ANA, GUEST)))
        self.assertEqual((w.kind, w.key), ("occasion", "occasion:occasion-1:2026-11-13"))
        self.assertEqual(w.data["confidence"], 0.75)  # the match's, capped by 3 episodes: 3 / 4
        self.assertEqual(w.data["occasion"]["name"], "Friday night gathering")
        self.assertEqual(w.data["match"]["shifts"][0]["minutes"], 90)
        self.assertEqual(w.data["forecast_shifted"], [shifted])
        self.assertEqual(w.data["home"]["guest_phones"], 1)
        self.assertEqual(wake.wake_record(db, w.key)["occasion_id"], "occasion-1")


class RobustTest(Isolated):
    def test_nothing_learned(self):
        self.assertEqual(wake.learned_wakes(sqlite3.connect(":memory:"), NOW, {}), [])

    def test_a_broken_part_leaves_the_others(self):
        db = db_with(ana_home_habit())
        phone_change(db, NOW - timedelta(minutes=1), (), (ANA,))
        with mock.patch.object(forecast_module, "forecast", side_effect=RuntimeError("boom")):
            with self.assertLogs("source.wake", "ERROR"):
                got = wake.learned_wakes(db, NOW, state(phones=(ANA,)))
        self.assertEqual([w.kind for w in got], ["arrival"])

    def test_never_raises(self):
        db = sqlite3.connect(":memory:")
        db.close()
        with self.assertLogs("source.wake", "ERROR"):
            self.assertEqual(wake.learned_wakes(db, NOW, state()), [])


if __name__ == "__main__":
    unittest.main()
