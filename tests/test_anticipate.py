"""Anticipation (source/anticipate.py): decisions ahead of habits and because of occasions, the
0.75 rule, running them through the guard, the loop and the agent's context.

Hand-made rows in the shapes of docs/habits-plan.md; the habits and occasions learners are
built separately, so their modules are stood in for (or made missing) here. No network.

  uv run python -m unittest tests.test_anticipate -v
"""
import asyncio
import copy
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest import mock

from source import anticipate
from source.anticipate import DO_AT, plan_decisions, run_due
from source.context import build_context
from source.learn import storage, to_ms
from source.learn.forecast import upcoming
from source.learn.today import baselines as pattern_baselines
from source.loop import run
from source.normalize import normalize
from source.sources import ReplaySource
from source.store import Store
from source.triggers import Triggers, seed_triggers

HABIT_TABLES = """
CREATE TABLE IF NOT EXISTS habits (id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS habit_readings (habit_id TEXT NOT NULL, day TEXT NOT NULL, start TEXT NOT NULL,
                                           end TEXT, steps_seen TEXT);
"""
DECISION_SHAPE = {"id", "at", "kind", "habit_id", "occasion_id", "action", "expectation", "confidence", "mode",
                  "status", "reason"}
FIRST = date(2026, 9, 1)  # a Tuesday
MON_EVENING = datetime(2026, 9, 21, 22)
BULB, PLUG = "bulb-1", "plug-1"
TYPES = {BULB: "bulb", PLUG: "plug"}
FRESH = {"bulb": [BULB], "plug": [PLUG]}

# The patterns (`sensors`) the habits are made of, as the habit learner writes them.
PATTERNS = {
    "pattern-1": ("kind-end:home-A", ["end of home-A (quiet after bulb until bulb, about 8.5 h)"], "07:59", {}),
    "pattern-2": ("kind:plug-A", ["plug-A (about 2,100 W, about 3 min)"], "08:05", {}),
    "pattern-3": ("kind:home-A", ["home-A (quiet after bulb until bulb, about 8.5 h)"], "23:20", {}),
    "pattern-4": ("link:contactSensor.sensorState>bulb-A",
                  ["contactSensor sensorState alarm (any)", "bulb-A (power on, about 20 min)"], "18:00", {"lag_min": 5}),
}


def habit(hid, name, steps, usual, *, earliest=None, latest=None, confidence=0.9, occurrences=20,
          opportunities=21, status="active", kind="daily", text="every day", **card) -> dict:
    h = {"id": hid, "name": name, "named_by": "llm" if name else None, "status": status,
         "patterns": [s[0] for s in steps],
         "steps": [{"pattern": p, "offset_min": off, "share": share} for p, off, share in steps],
         "recurrence": {"kind": kind, "weekdays": None, "every_n_days": None, "anchor": None, "day_of_month": None,
                        "every_n_hours": None, "text": text},
         "window": {"usual": usual, "earliest": earliest or usual, "latest": latest or usual},
         "variation": {"spread_min": 12, "by_weekday": {}},
         "occurrences": occurrences, "opportunities": opportunities, "first_seen": "2026-09-01",
         "last_seen": "2026-09-21", "learned_at": "2026-09-06", "confidence": confidence,
         "trend": {"minutes_per_week": 0}}
    h.update(card)
    return h


HABITS = [
    habit("habit-1", "Waking up", [("pattern-1", 0, 0.95), ("pattern-2", 6, 0.9)], "07:59",
          earliest="07:33", latest="08:27"),
    habit("habit-2", "Morning kettle", [("pattern-2", 0, 1.0)], "08:05"),  # no light: never a lamp action
    habit("habit-3", "Going to sleep", [("pattern-3", 0, 1.0)], "23:20", earliest="22:50", latest="23:50"),
    # door → lamp: a link step's offset is its second part, the lamp, 5 min after the door (the grouper's convention)
    habit("habit-4", "Coming home", [("pattern-4", 5, 0.9)], "18:00", confidence=0.7),
]


def home(db: sqlite3.Connection | None = None, habits=HABITS, days=21) -> sqlite3.Connection:
    """The learners' tables with the patterns above, the habits and a reading a day from 1 Sept."""
    db = db or sqlite3.connect(":memory:")
    storage.init(db)
    db.executescript(HABIT_TABLES)
    for sid, (signature, parts, tod, extra) in PATTERNS.items():
        data = {"id": sid, "name": None, "named_by": None, "focus": "connected_life", "learner": "habit",
                "learned_at": "2026-09-05", "explanation": None, "status": "active", "confidence": 0.9,
                "pattern": {"parts": parts, "time_of_day": tod, "spread_min": 10, "days_seen": 18, "days_total": 20,
                            **extra}}
        h, m = map(int, tod.split(":"))
        db.execute("INSERT INTO sensors (id, learner, focus, signature, centre_min, width_min, learned_at, status,"
                   " data) VALUES (?, 'habit', 'connected_life', ?, ?, 20, '2026-09-05', 'active', ?)",
                   (sid, signature, h * 60 + m, json.dumps(data)))
    for h in habits:
        add_habit(db, h, days)
    db.commit()
    return db


def add_habit(db, h: dict, days: int = 21) -> None:
    db.execute("INSERT OR REPLACE INTO habits VALUES (?, ?, ?)", (h["id"], h["status"], json.dumps(h)))
    hh, mm = map(int, h["window"]["usual"].split(":"))
    db.executemany("INSERT INTO habit_readings VALUES (?, ?, ?, NULL, '1')",
                   [(h["id"], d.isoformat(), datetime.combine(d, time(hh, mm)).isoformat())
                    for d in (FIRST + timedelta(days=i) for i in range(days))])


def set_status(db, hid: str, status: str) -> None:
    (data,) = db.execute("SELECT data FROM habits WHERE id = ?", (hid,)).fetchone()
    h = json.loads(data)
    h["status"] = status
    db.execute("UPDATE habits SET status = ?, data = ? WHERE id = ?", (status, json.dumps(h), hid))


def stored(db) -> dict[str, dict]:
    return {i: json.loads(d) for i, d in db.execute("SELECT id, data FROM decisions")}


_MISSING = object()


@contextmanager
def part(name: str, **functions):
    """Stands in for another part's module (habits, occasions), or with no functions makes it missing."""
    old = sys.modules.get(name, _MISSING)
    if functions:
        module = types.ModuleType(name)
        for k, fn in functions.items():
            setattr(module, k, fn)
        sys.modules[name] = module
    else:
        sys.modules[name] = None  # importing it raises ModuleNotFoundError
    try:
        yield
    finally:
        if old is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old


class FakeLamp:
    """The two MCP tools the guard uses, over one lamp that reports after each write."""

    def __init__(self, power="off", brightness=100, fail=False):
        self.caps = {"power": power, "brightness": brightness}
        self.ts, self.fail = 1000, fail
        self.calls: list[tuple[str, dict]] = []

    @property
    def writes(self) -> list[dict]:
        return [args for tool, args in self.calls if tool == "update_device_capabilities"]

    async def call(self, tool: str, args: dict) -> str:
        self.calls.append((tool, args))
        if tool == "update_device_capabilities":
            if self.fail:
                raise RuntimeError("SH_10500: device busy")
            self.caps[args["body"]["type"]] = args["body"]["value"]
            self.ts += 1000
            return "{}"
        return json.dumps({"lastCommunicationTime": self.ts,
                           "capabilities": [{"type": k, "value": v} for k, v in self.caps.items()]})


class Isolated(unittest.TestCase):
    """The other parts' modules are missing unless a test stands one in."""

    def setUp(self):
        self.enterContext(part("source.learn.occasions"))
        self.enterContext(part("source.learn.grouping"))


class PlanTest(Isolated):
    def test_prepare_ahead_of_habits_with_the_light(self):
        plan = plan_decisions(home(), MON_EVENING)
        by_habit = {d["habit_id"]: d for d in plan}
        self.assertEqual(set(by_habit), {"habit-1", "habit-3", "habit-4"})  # the kettle has no light
        self.assertTrue(all(set(d) == DECISION_SHAPE for d in plan))
        self.assertEqual([d["at"] for d in plan], sorted(d["at"] for d in plan))

        wake = by_habit["habit-1"]  # the night's quiet ends with the lamp: a soft lamp 10 min before
        self.assertEqual((wake["kind"], wake["at"], wake["status"]), ("prepare", "2026-09-22T07:49:00", "planned"))
        self.assertEqual(wake["action"], {"tool": "update_device_capabilities", "target": "bulb",
                                          "args": {"body": {"type": "brightness", "value": 30}}})
        self.assertEqual((wake["occasion_id"], wake["expectation"], wake["mode"]), (None, None, "do"))
        self.assertTrue(wake["reason"].startswith("Waking up expected around 07:59 (Tuesdays: 3 of 3 since 1 Sept)"))

        sleep = by_habit["habit-3"]  # the night's quiet starts after the lamp: dim it 30 min before
        self.assertEqual((sleep["at"], sleep["action"]["args"]["body"]), ("2026-09-21T22:50:00",
                                                                         {"type": "brightness", "value": 20}))
        door = by_habit["habit-4"]  # the lamp 5 min after the door (the link's lag)
        self.assertEqual(door["at"], "2026-09-22T17:55:00")

    def test_confidence_and_the_075_rule(self):
        db = home()
        plan = {d["habit_id"]: d for d in plan_decisions(db, MON_EVENING)}
        slot = next(s for s in upcoming(db, MON_EVENING) if s.habit["id"] == "habit-1")
        self.assertEqual(plan["habit-1"]["confidence"], round(slot.confidence * 0.95, 2))  # × the light step's share
        for d in plan.values():
            self.assertEqual(d["mode"], "do" if d["confidence"] >= DO_AT else "suggest")
        self.assertEqual(plan["habit-1"]["mode"], "do")
        self.assertEqual(plan["habit-4"]["mode"], "suggest")  # a less sure habit: shown, never sent
        self.assertLess(plan["habit-4"]["confidence"], DO_AT)

        def mode(c):
            return anticipate._decision(MON_EVENING, "prepare", "h", None, None, None, c, "")["mode"]
        self.assertEqual((mode(0.75), mode(0.74), mode(0.99)), ("do", "suggest", "do"))

    def test_one_example_only_suggests(self):
        """Evidence factor n / (n + 1): a habit seen once can't reach more than 0.5."""
        once = habit("habit-1", "Waking up", [("pattern-1", 0, 1.0)], "07:59", confidence=1.0,
                     occurrences=1, opportunities=1)
        (d,) = plan_decisions(home(habits=[once], days=0), MON_EVENING)
        self.assertEqual((d["confidence"], d["mode"]), (0.5, "suggest"))

    def test_weak_habits_are_not_even_suggested(self):
        weak = habit("habit-1", "Waking up", [("pattern-1", 0, 1.0)], "07:59", confidence=0.3)
        self.assertEqual(plan_decisions(home(habits=[weak]), MON_EVENING), [])

    def test_one_decision_per_occurrence(self):
        db = home()
        first = plan_decisions(db, MON_EVENING)
        again = plan_decisions(db, MON_EVENING + timedelta(minutes=5))
        self.assertEqual([d["id"] for d in first], [d["id"] for d in again])
        self.assertEqual(first, again)
        self.assertEqual(len(stored(db)), 3)
        tomorrow = plan_decisions(db, MON_EVENING + timedelta(days=1))  # the next occurrences: new decisions
        self.assertEqual(len(stored(db)), 6)
        self.assertFalse({d["id"] for d in first} & {d["id"] for d in tomorrow})

    def test_a_decision_that_ran_is_kept(self):
        db = home()
        sleep = next(d for d in plan_decisions(db, MON_EVENING) if d["habit_id"] == "habit-3")
        lamp = FakeLamp(power="on")
        asyncio.run(run_due(db, datetime(2026, 9, 21, 22, 50, 20), lamp, TYPES, FRESH))
        replanned = next(d for d in plan_decisions(db, datetime(2026, 9, 21, 22, 55)) if d["id"] == sleep["id"])
        self.assertEqual(replanned["status"], "done")
        self.assertEqual(len(lamp.writes), 1)

    def test_dropped_when_no_longer_expected(self):
        db = home()
        wake = next(d for d in plan_decisions(db, MON_EVENING) if d["habit_id"] == "habit-1")
        set_status(db, "habit-1", "fading")  # the learners say the habit stopped
        self.assertNotIn(wake["id"], [d["id"] for d in plan_decisions(db, MON_EVENING + timedelta(minutes=5))])
        self.assertEqual(stored(db)[wake["id"]]["status"], "skipped")
        lamp = FakeLamp()
        asyncio.run(run_due(db, datetime(2026, 9, 22, 7, 50), lamp, TYPES, FRESH))
        self.assertEqual(lamp.writes, [])

    def test_a_broken_forecast_drops_nothing(self):
        db = home()
        plan = plan_decisions(db, MON_EVENING)
        with mock.patch("source.anticipate.upcoming", side_effect=RuntimeError("boom")):
            with self.assertLogs("source.anticipate", "ERROR"):
                plan_decisions(db, MON_EVENING + timedelta(minutes=5))
        self.assertEqual({d["status"] for d in stored(db).values()}, {"planned"})
        self.assertEqual(len(stored(db)), len(plan))

    def test_adapt_from_an_occasion(self):
        db = home()
        db.execute("CREATE TABLE occasions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT INTO occasions VALUES ('occasion-1', ?)", (json.dumps(
            {"id": "occasion-1", "name": "Dinner with friends", "named_by": "llm", "episodes": 3,
             "fingerprint": {"door_openings": 9}, "usual_hours": ["19:00", "01:00"], "weekdays": ["fri", "sat"],
             "confidence": 0.75}),))
        match = {"occasion_id": "occasion-1", "similarity": 0.82, "confidence": 0.9, "since": "2026-09-19T19:40:00",
                 "explains": ["habit-3 late"]}
        saturday = datetime(2026, 9, 19, 22)
        with part("source.learn.occasions", match_now=lambda db, now: [match]):
            plan = plan_decisions(db, saturday)
            again = plan_decisions(db, saturday + timedelta(hours=3))  # still that evening: the same decision
        (adapt,) = [d for d in plan if d["kind"] == "adapt"]
        self.assertEqual(set(adapt), DECISION_SHAPE)
        self.assertEqual((adapt["occasion_id"], adapt["habit_id"], adapt["action"], adapt["at"]),
                         ("occasion-1", "habit-3", None, "2026-09-19T19:40:00"))
        self.assertEqual(adapt["expectation"], "Going to sleep late is normal tonight")
        self.assertEqual(adapt["reason"], "Looks like Dinner with friends: 82% like its 3 past times, since 19:40")
        self.assertEqual((adapt["confidence"], adapt["mode"]), (0.75, "do"))  # capped by 3 episodes: 3 / 4
        self.assertEqual([d["id"] for d in again if d["kind"] == "adapt"], [adapt["id"]])

        lamp = FakeLamp()
        (ran,) = [r for r in asyncio.run(run_due(db, saturday, lamp, TYPES, FRESH)) if r["decision"]["kind"] == "adapt"]
        self.assertEqual((ran["decision"]["status"], ran["why"], ran["actions"]), ("done", "expectation in force", []))
        self.assertEqual(lamp.calls, [])  # an expectation never touches a device

    def test_an_occasion_tempers_the_habit_it_explains(self):
        """Guests tonight: the usual bedtime dim is only as sure as the occasion isn't, so it
        becomes a suggestion (the same decision, re-planned); tomorrow's wake-up is untouched."""
        db = home()
        before = {d["habit_id"]: d for d in plan_decisions(db, MON_EVENING)}
        self.assertEqual(before["habit-3"]["mode"], "do")
        match = {"occasion_id": "occasion-1", "similarity": 0.8, "confidence": 0.75, "since": "2026-09-21T19:30:00",
                 "explains": ["habit-3 late"]}
        with part("source.learn.occasions", match_now=lambda db, now: [match]):
            after = {d["habit_id"]: d for d in plan_decisions(db, MON_EVENING + timedelta(minutes=5))
                     if d["kind"] == "prepare"}
        sleep = after["habit-3"]
        self.assertEqual((sleep["id"], sleep["mode"]), (before["habit-3"]["id"], "suggest"))
        self.assertAlmostEqual(sleep["confidence"], before["habit-3"]["confidence"] * 0.25, delta=0.01)
        self.assertTrue(sleep["reason"].endswith("less sure now: looks like Occasion 1"))
        self.assertEqual(after["habit-1"], before["habit-1"])

    def test_a_weak_match_makes_no_decision(self):
        match = {"occasion_id": "occasion-1", "similarity": 0.4, "confidence": 0.3, "since": None,
                 "explains": ["habit-3 late"]}
        with part("source.learn.occasions", match_now=lambda db, now: [match]):
            plan = plan_decisions(home(), MON_EVENING)
        self.assertEqual([d["kind"] for d in plan], ["prepare"] * 3)
        self.assertEqual({d["habit_id"]: d["mode"] for d in plan}["habit-3"], "do")

    def test_an_occasion_seen_once_is_only_a_suggestion(self):
        db = home()
        match = {"occasion_id": "occasion-2", "similarity": 0.9, "confidence": 0.95, "since": None, "explains": []}
        with part("source.learn.occasions", match_now=lambda db, now: [match]):
            with mock.patch("source.anticipate.occasions_by_id", return_value={"occasion-2": {"episodes": 1}}):
                (adapt,) = [d for d in plan_decisions(db, datetime(2026, 9, 19, 21)) if d["kind"] == "adapt"]
        self.assertEqual((adapt["confidence"], adapt["mode"]), (0.5, "suggest"))
        self.assertEqual(adapt["expectation"], "An evening unlike the usual one is normal tonight")

    def test_no_occasions_part_no_adapt(self):
        self.assertEqual([d for d in plan_decisions(home(), MON_EVENING) if d["kind"] == "adapt"], [])
        with part("source.learn.occasions", match_now=mock.Mock(side_effect=RuntimeError("broken"))):
            with self.assertLogs("source.anticipate", "ERROR"):
                plan = plan_decisions(home(), MON_EVENING)
        self.assertEqual(len(plan), 3)  # a broken occasions learner costs only its own decisions

    def test_nothing_learned(self):
        db = sqlite3.connect(":memory:")
        self.assertEqual(plan_decisions(db, MON_EVENING), [])
        self.assertEqual(asyncio.run(run_due(db, MON_EVENING, FakeLamp(), TYPES, FRESH)), [])


class RunDueTest(Isolated):
    def setUp(self):
        super().setUp()
        self.db = home()
        # just after midnight: Tuesday's soft lamp 07:49 (do), the door lamp 17:55 (suggest), the dim lamp 22:50 (do)
        self.plan = {d["habit_id"]: d for d in plan_decisions(self.db, datetime(2026, 9, 22, 0, 0))}

    def run_at(self, when: datetime, lamp: FakeLamp, fresh=FRESH, state=None) -> list[dict]:
        return asyncio.run(run_due(self.db, when, lamp, TYPES, fresh, state=state))

    def test_done_through_the_guard(self):
        lamp = FakeLamp(power="off")
        (r,) = self.run_at(datetime(2026, 9, 22, 7, 49, 10), lamp)
        self.assertEqual((r["decision"]["id"], r["decision"]["status"]), (self.plan["habit-1"]["id"], "done"))
        self.assertEqual(r["why"], "sent and confirmed")
        self.assertEqual(lamp.writes, [{"deviceId": BULB, "body": {"type": "brightness", "value": 30}}])
        # no poll given: it reads the lamp's state first; then the guard reads, writes and reads back
        self.assertEqual([t for t, _ in lamp.calls], ["get_device_details", "get_device_details",
                                                      "update_device_capabilities", "get_device_details"])
        self.assertEqual(stored(self.db)[r["decision"]["id"]]["status"], "done")
        self.assertEqual(self.run_at(datetime(2026, 9, 22, 7, 49, 30), lamp), [])  # once only

    def test_not_due_yet_and_suggestions_never_run(self):
        lamp = FakeLamp(power="off")
        self.assertEqual(self.run_at(datetime(2026, 9, 22, 7, 48), lamp), [])
        self.assertEqual(len(self.run_at(datetime(2026, 9, 22, 7, 49, 10), lamp)), 1)
        self.assertEqual(self.plan["habit-4"]["mode"], "suggest")
        lamp.caps["power"] = "off"
        self.assertEqual(self.run_at(datetime(2026, 9, 22, 17, 56), lamp), [])  # shown, never sent
        self.assertEqual(stored(self.db)[self.plan["habit-4"]["id"]]["status"], "planned")
        self.assertEqual(len(lamp.writes), 1)

    def test_skipped_when_the_lamp_is_already_on(self):
        lamp = FakeLamp(power="on")
        (r,) = self.run_at(datetime(2026, 9, 22, 7, 50), lamp)
        self.assertEqual((r["decision"]["status"], r["why"]), ("skipped", "skipped: the lamp is already on"))
        self.assertEqual(lamp.writes, [])

    def test_the_poll_says_what_the_lamp_is_doing(self):
        state = normalize({"devices": [{"id": BULB, "type": "bulb", "name": "Light", "lastCommunicationTime": 1,
                                        "capabilities": [{"type": "power", "value": "on"}]}]})
        lamp = FakeLamp(power="off")
        (r,) = self.run_at(datetime(2026, 9, 22, 7, 50), lamp, state=state)
        self.assertEqual(r["why"], "skipped: the lamp is already on")
        self.assertEqual(lamp.calls, [])  # no extra read: the poll just made is enough

    def test_dims_only_a_bright_lamp(self):
        results = self.run_at(datetime(2026, 9, 22, 22, 50), FakeLamp(power="on", brightness=15))
        (r,) = [r for r in results if r["decision"]["habit_id"] == "habit-3"]
        self.assertEqual(r["why"], "skipped: the lamp is already at 15 %")

    def test_too_late(self):
        lamp = FakeLamp(power="off")
        results = self.run_at(datetime(2026, 9, 22, 8, 10), lamp)  # the loop was down at 07:49
        self.assertEqual({r["why"] for r in results}, {"skipped: too late"})
        self.assertEqual(lamp.writes, [])

    def test_no_fresh_lamp(self):
        (r,) = self.run_at(datetime(2026, 9, 22, 7, 50), FakeLamp(), fresh={"plug": [PLUG]})
        self.assertEqual((r["decision"]["status"], r["why"]), ("skipped", "skipped: no fresh bulb"))

    def test_failed_write(self):
        (r,) = self.run_at(datetime(2026, 9, 22, 7, 50), FakeLamp(fail=True))
        self.assertEqual(r["decision"]["status"], "failed")
        self.assertIn("SH_10500", r["why"])

    def test_skipped_when_it_already_happened_today(self):
        lamp = FakeLamp(power="off")
        with part("source.learn.grouping",
                  habit_today=lambda db, now: [{"habit_id": "habit-1", "status": "done", "at": "07:40"}]):
            (r,) = self.run_at(datetime(2026, 9, 22, 7, 50), lamp)
        self.assertEqual(r["why"], "skipped: it already happened today")
        self.assertEqual(lamp.writes, [])

    def test_guard_blocks_the_plug(self):
        """A decision can only change the lamp: plug on (or off) never reaches the device."""
        anticipate.init(self.db)
        for i, body in enumerate(({"type": "power", "value": "on"}, {"type": "power", "value": "off"})):
            d = anticipate._decision(datetime(2026, 9, 21, 23), "prepare", "habit-2", None,
                                     {"tool": "update_device_capabilities", "target": "plug", "args": {"body": body}},
                                     None, 0.99, "test")
            d["id"] = f"decision-9{i}"
            self.db.execute("INSERT INTO decisions (id, ts, data, key, meta) VALUES (?, ?, ?, ?, '{}')",
                            (d["id"], to_ms(datetime(2026, 9, 21, 23)), json.dumps(d), f"test:{i}"))
        lamp = FakeLamp()
        results = self.run_at(datetime(2026, 9, 21, 23, 1), lamp)
        plug = [r for r in results if r["decision"]["id"].startswith("decision-9")]
        self.assertEqual([r["decision"]["status"] for r in plug], ["skipped", "skipped"])
        self.assertEqual([r["actions"][0]["status"] for r in plug], ["blocked", "blocked"])  # by the guard
        self.assertTrue(all(r["why"].startswith("blocked") for r in plug))
        self.assertEqual(lamp.writes, [])

    def test_guard_checks_lamp_values_too(self):
        anticipate.init(self.db)
        d = anticipate._decision(datetime(2026, 9, 21, 23), "prepare", "habit-3", None,
                                 {"tool": "update_device_capabilities", "target": "bulb",
                                  "args": {"body": {"type": "brightness", "value": 0}}}, None, 0.99, "test")
        d["id"] = "decision-90"
        self.db.execute("INSERT INTO decisions (id, ts, data, key, meta) VALUES (?, ?, ?, 'test', '{}')",
                        (d["id"], to_ms(datetime(2026, 9, 21, 23)), json.dumps(d)))
        lamp = FakeLamp(power="on")
        (r,) = [r for r in self.run_at(datetime(2026, 9, 21, 23, 1), lamp) if r["decision"]["id"] == "decision-90"]
        self.assertEqual((r["decision"]["status"], r["actions"][0]["status"]), ("skipped", "blocked"))
        self.assertEqual(lamp.writes, [])

    def test_never_raises(self):
        self.db.execute("INSERT INTO decisions (id, ts, data, key, meta) VALUES ('decision-80', ?, ?, 'x', '')",
                        (to_ms(datetime(2026, 9, 22, 7, 0)), '{"status": "planned", "mode": "do", broken'))
        lamp = FakeLamp(power="off")
        with self.assertLogs("source.anticipate", "ERROR"):
            results = self.run_at(datetime(2026, 9, 22, 7, 50), lamp)
        self.assertEqual([r["decision"]["habit_id"] for r in results], ["habit-1"])  # the others still run

    def test_a_decision_that_breaks_is_failed_once(self):
        d = anticipate._decision(datetime(2026, 9, 22, 7, 0), "prepare", "habit-1", None,
                                 {"target": "bulb", "args": {"body": {"type": "brightness", "value": 30}}},  # no tool
                                 None, 0.9, "test")
        d["id"] = "decision-81"
        self.db.execute("INSERT INTO decisions (id, ts, data, key, meta) VALUES (?, ?, ?, 'y', '{}')",
                        (d["id"], to_ms(datetime(2026, 9, 22, 7, 0)), json.dumps(d)))
        with self.assertLogs("source.anticipate", "ERROR"):
            self.run_at(datetime(2026, 9, 22, 7, 5), FakeLamp(power="off"))
        self.assertEqual(stored(self.db)["decision-81"]["status"], "failed")
        with self.assertNoLogs("source.anticipate", "ERROR"):  # settled: not retried on the next tick
            self.run_at(datetime(2026, 9, 22, 7, 6), FakeLamp(power="off"))


class ContextTest(Isolated):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name), record=False)
        self.addCleanup(self.store.close)

    def baselines(self, now: datetime) -> dict:
        return build_context(to_ms(now), [], {}, self.store, None, 60 * 60_000)["baselines"]

    def test_empty_when_nothing_is_learned(self):
        self.assertEqual(self.baselines(MON_EVENING), {})
        plan_decisions(self.store.db, MON_EVENING)  # the decisions table exists, still nothing learned
        self.assertEqual(self.baselines(MON_EVENING), {})

    def test_habits_forecast_occasions_and_decisions(self):
        named = [habit("habit-1", "Waking up", [("pattern-1", 0, 0.95)], "07:59", earliest="07:33", latest="08:27",
                       means="Someone is up.", if_missing="Could be a late start; check the door.", importance="high"),
                 habit("habit-2", "Morning kettle", [("pattern-2", 0, 1.0)], "08:05", importance="hidden"),
                 HABITS[2]]
        home(self.store.db, habits=named)
        match = {"occasion_id": "occasion-1", "similarity": 0.8, "confidence": 0.6, "since": "2026-09-21T19:30:00",
                 "explains": ["habit-3 late"]}
        now = datetime(2026, 9, 21, 22, 51)
        plan_decisions(self.store.db, MON_EVENING)  # an ordinary evening so far
        asyncio.run(run_due(self.store.db, now, FakeLamp(power="on"), TYPES, FRESH))  # the 22:50 dim lamp
        with part("source.learn.occasions", match_now=lambda db, at: [match]), \
                part("source.learn.grouping", habit_today=lambda db, at: [
                    {"habit_id": "habit-1", "status": "done", "at": "07:58"},
                    {"habit_id": "habit-3", "status": "expected"}]):
            plan_decisions(self.store.db, now)  # then the evening looks like an occasion
            b = self.baselines(now)
        self.assertEqual(set(b), {"habits", "forecast", "occasion_now", "decisions"})

        wake = b["habits"][0]
        self.assertEqual(wake, {"id": "habit-1", "name": "Waking up", "rhythm": "every day",
                                "window": {"usual": "07:59", "earliest": "07:33", "latest": "08:27"},
                                "confidence": 0.9, "today": "done", "at": "07:58", "means": "Someone is up.",
                                "if_missing": "Could be a late start; check the door.", "importance": "high"})
        self.assertEqual([h["id"] for h in b["habits"]], ["habit-1", "habit-3"])  # the hidden duplicate is left out

        self.assertEqual([(f["habit"], f["date"], f["expected"]) for f in b["forecast"]],
                         [("Going to sleep", "2026-09-21", "23:20"), ("Waking up", "2026-09-22", "07:59")])
        self.assertTrue({"probability", "confidence", "basis", "window"} <= set(b["forecast"][0]))

        (occasion,) = b["occasion_now"]
        self.assertEqual((occasion["occasion_id"], occasion["name"], occasion["explains"]),
                         ("occasion-1", "Occasion 1", ["habit-3 late"]))

        decisions = {(d["kind"], d["habit_id"]): d for d in b["decisions"]}
        self.assertEqual(set(decisions), {("prepare", "habit-3"), ("prepare", "habit-1"), ("adapt", "habit-3")})
        dim = decisions[("prepare", "habit-3")]
        self.assertEqual((dim["status"], dim["outcome"]), ("done", "sent and confirmed"))
        self.assertEqual(decisions[("prepare", "habit-1")]["status"], "planned")
        self.assertNotIn("outcome", decisions[("prepare", "habit-1")])
        adapt = decisions[("adapt", "habit-3")]
        self.assertEqual((adapt["mode"], adapt["status"]), ("suggest", "planned"))

    def test_today_from_the_patterns_without_the_habits_part(self):
        home(self.store.db)
        now = datetime(2026, 9, 21, 22)
        patterns = {p["id"]: p["today"] for p in pattern_baselines(self.store.db, now)["habits"]}
        today = {h["id"]: h["today"] for h in self.baselines(now)["habits"]}
        self.assertEqual(today["habit-1"], patterns["pattern-1"])  # its first step's pattern
        self.assertEqual(today["habit-3"], patterns["pattern-3"])

    def test_a_broken_learner_leaves_its_piece_empty(self):
        home(self.store.db)
        with mock.patch("source.learn.forecast.forecast", side_effect=RuntimeError("boom")):
            with self.assertLogs("source.context", "ERROR"):
                b = self.baselines(MON_EVENING)
        self.assertEqual(b["forecast"], [])
        self.assertTrue(b["habits"])


LIGHT = "90654832-7bca-47dd-89f3-d6e0b5f0a9ee"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "get_home.json").read_text())
T0 = 1794625400000  # 14 Nov 03:03:20 in Lisbon
STEP = 20_000


class RecordingAgent:
    def __init__(self):
        self.runs = []

    async def run(self, ctx):
        self.runs.append(ctx)
        return {"notes": "", "trigger_updates": [], "actions": []}


class LoopTest(Isolated):
    """The loop plans and runs decisions on its own, before the triggers, and tells the agent."""

    def replay(self, actuator, ticks=3):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "data", record=False)
        self.addCleanup(store.close)
        wake = habit("habit-1", "Waking up", [("pattern-1", 0, 1.0)], "03:10")
        wake.update(first_seen="2026-10-24", last_seen="2026-11-13")
        home(store.db, habits=[wake], days=0)
        frames = []
        home_ = copy.deepcopy(FIXTURE)
        for i in range(ticks):
            ts = T0 + i * STEP
            for d in home_["devices"]:
                d["lastCommunicationTime"] = ts - 1000
                if d["id"] == LIGHT:
                    for c in d["capabilities"]:
                        if c["type"] == "power":
                            c["value"] = "off"
            frames.append({"ts": ts, "home": copy.deepcopy(home_)})
        rec = Path(tmp.name) / "rec.jsonl"
        rec.write_text("\n".join(json.dumps(f) for f in frames))
        agent = RecordingAgent()
        asyncio.run(run(ReplaySource(rec), store, Triggers(seed_triggers(15)), agent,
                        poll_seconds=20, stale_minutes=60, actuator=actuator))
        return store, agent

    def test_soft_lamp_before_waking_up(self):
        lamp = FakeLamp(power="off")
        store, agent = self.replay(lamp)
        self.assertEqual(lamp.writes, [{"deviceId": LIGHT, "body": {"type": "brightness", "value": 30}}])  # once
        self.assertEqual([tid for _, tid, _ in store.rule_runs()], ["decision:decision-1"])
        ctx = agent.runs[0]  # the heartbeat in the same tick: told what just ran
        self.assertEqual(ctx["rules_just_ran"][0]["trigger_id"], "decision:decision-1")
        self.assertEqual(ctx["rules_just_ran"][0]["actions"][0]["verified"], "confirmed")
        (d,) = ctx["baselines"]["decisions"]
        self.assertEqual((d["status"], d["outcome"]), ("done", "sent and confirmed"))

    def test_a_broken_plan_never_stops_the_loop(self):
        with mock.patch("source.anticipate.plan_decisions", side_effect=RuntimeError("boom")):
            with self.assertLogs("source.loop", "INFO") as logs:
                self.replay(FakeLamp())
        self.assertTrue(any(m.startswith("INFO:source.loop:tick 3 ") for m in logs.output))
        self.assertTrue(any("anticipation failed" in m for m in logs.output))


if __name__ == "__main__":
    unittest.main()
