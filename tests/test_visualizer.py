"""The 3D view's data: one day of a household, cut down to what the page draws, what Alfred learned and knows
by then (the panel and the knowledge tab), and the routes that serve them.

  uv run python -m unittest tests.test_visualizer -v
"""
import json
import sqlite3
import sys
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime
from http.server import ThreadingHTTPServer
from unittest import mock

import tempfile
from pathlib import Path

import source.simulate as simulate
import source.simulate_web as web
from source.learn import to_ms
from source.simulate import REAL_HOME, TZ, generate, houses
from source.simulate_web import (MAX_DAYS, MAX_LEARN_DAYS, REAL_MAX_DAYS, VIEW_PAGE, WATCHED, Handler,
                                 _day_request, _device_types, compact, day_view, knowledge_view, learned_view)

START = date(2026, 9, 1)
# The other parts of docs/habits-plan.md, which the knowledge tab reads when they are there.
PART_MODULES = ("source.learn.grouping", "source.learn.occasions", "source.learn.forecast", "source.anticipate",
                "source.learn.warmup")
PARTS = ("habits", "occasions", "forecast", "decisions", "warmup")
SECTIONS = ("kinds", "patterns", "habits", "occasions", "matches", "forecast", "decisions")

# The view's tests never call the LLM or write the real data/names/: no namer, and names kept in a temp folder.
web._namer_model = lambda: None
web.NAMES_DIR = Path(tempfile.mkdtemp(prefix="alfred-names-"))


class WithNamesTest(unittest.TestCase):
    """A cached view gets names saved later; a name it has is never replaced."""

    def test_fills_saved_names(self):
        view = {"named": False, "sensors": [{"id": "pattern-1", "key": "pattern-1|kind:a", "name": None},
                                            {"id": "pattern-2", "key": "pattern-2|kind:b", "name": "Kept"}]}
        path = web.NAMES_DIR / "test-names.json"
        path.write_text(json.dumps({"pattern-1|kind:a": {"name": "Waking up", "importance": "high"},
                                    "pattern-2|kind:b": {"name": "Other"}}))
        out = web.with_names(view, path)
        self.assertEqual([s["name"] for s in out["sensors"]], ["Waking up", "Kept"])
        self.assertEqual(out["sensors"][0]["importance"], "high")
        self.assertTrue(out["named"])
        self.assertEqual(web.with_names(view, web.NAMES_DIR / "missing.json")["sensors"], view["sensors"])

    def test_fills_the_knowledge_tabs_patterns_too(self):
        view = {"named": False, "patterns": [{"id": "pattern-1", "key": "pattern-1|kind:a", "name": None}], "habits": []}
        path = web.NAMES_DIR / "test-names-patterns.json"
        path.write_text(json.dumps({"pattern-1|kind:a": {"name": "Morning kettle"}}))
        out = web.with_names(view, path, "patterns")
        self.assertEqual((out["patterns"][0]["name"], out["named"]), ("Morning kettle", True))
        self.assertEqual(out["habits"], [])


def _replay(view: dict) -> list[tuple[int, list[dict]]]:
    """The frames put back together, as the page does: each snapshot's time and every device's state."""
    out, state = [], {}
    for ts, changes in view["frames"]:
        state = {**state, **{int(n): {**state.get(int(n), {}), **c} for n, c in changes.items()}}
        out.append((ts, [state[n] for n in sorted(state)]))
    return out


def _day_of(household, day: int) -> list[dict]:
    rows = [json.loads(line) for line in generate(household, day, start=START)[0]]
    return [r for r in rows if datetime.fromtimestamp(r["ts"] / 1000, TZ).date() == date(2026, 9, day)]


class DayViewTest(unittest.TestCase):
    def test_frames_replay_to_every_snapshot_of_that_day(self):
        for name, day in (("rosa", 1), ("students", 3), (REAL_HOME, 2)):
            view = json.loads(json.dumps(day_view(name, day)))  # as the page gets it: keys are strings
            rows = _day_of(name, day)
            self.assertEqual(_replay(view), [(r["ts"], compact(r["home"])) for r in rows], name)
            self.assertEqual((view["start"], view["end"] - view["start"]), (rows[0]["ts"], 86_400_000), name)
            self.assertEqual(view["date"], f"2026-09-{day:02d}")

    def test_first_frame_is_whole_and_the_rest_are_only_changes(self):
        view = day_view("rosa", 1)
        first, later = view["frames"][0][1], [changes for _, changes in view["frames"][1:]]
        self.assertEqual(len(first), len(view["devices"]) + 1)  # every device, then the home's security
        self.assertTrue(any(not changes for changes in later))  # a quiet snapshot is still a frame
        self.assertTrue(all(len(changes) < len(first) for changes in later))

    def test_keeps_only_what_the_view_draws(self):
        view = day_view("rosa", 1)
        self.assertEqual([d["type"] for d in view["devices"]],
                         ["temperatureSensor", "contactSensor", "gateway", "smokeSensor", "plug", "bulb", "camera"])
        self.assertEqual(set(view["devices"][0]), {"id", "name", "type", "room"})
        for n, dev in enumerate(view["devices"]):
            self.assertLessEqual(set(view["frames"][0][1][n]), set(WATCHED.get(dev["type"], ())))
        self.assertEqual(view["frames"][0][1][len(view["devices"])], {"status": "DISARMED", "isAlarmed": False})
        self.assertLess(len(json.dumps(view)), 40_000)  # the raw day is about 1.7 MB

    def test_the_day_shows_its_habits(self):
        view = day_view("rosa", 1)
        types = [d["type"] for d in view["devices"]]
        seen = {(types[int(n)], cap, value) for _, changes in view["frames"][1:] for n, c in changes.items()
                if int(n) < len(types) for cap, value in c.items()}
        self.assertIn(("contactSensor", "sensorState", "alarm"), seen)
        self.assertIn(("bulb", "power", "on"), seen)
        self.assertTrue(any(t == "plug" and cap == "instantPower" and value > 1500 for t, cap, value in seen))

    def test_a_made_up_house_is_the_one_in_the_forms_zip(self):
        rows = _day_of(houses(3)[2], 2)
        self.assertEqual(_replay(json.loads(json.dumps(day_view("house-3", 2)))),
                         [(r["ts"], compact(r["home"])) for r in rows])

    def test_answer_key_is_only_that_day(self):
        truth = day_view("rosa", 2)["truth"]
        self.assertIn("wake", [t["what"] for t in truth])
        self.assertTrue(all(t["start"].startswith("2026-09-02") for t in truth))

    def test_reads_the_query(self):
        self.assertEqual(_day_request({}), ("rosa", 1, 1, START))
        self.assertEqual(_day_request({"household": "couple", "day": "7", "seed": "3", "start": "2026-10-05"}),
                         ("couple", 7, 3, date(2026, 10, 5)))
        self.assertEqual(_day_request({"household": REAL_HOME, "day": str(REAL_MAX_DAYS)})[1], REAL_MAX_DAYS)

    def test_bad_input_is_a_plain_error(self):
        for bad in ({"household": "nobody"}, {"household": "house-0"}, {"household": "house-21"}, {"day": "0"},
                    {"day": "two"}, {"day": str(MAX_DAYS + 1)}, {"start": "yesterday"}, {"seed": "x"},
                    {"household": REAL_HOME, "day": str(REAL_MAX_DAYS + 1)}):
            with self.assertRaises(ValueError, msg=bad):
                _day_request(bad)


class LearnedViewTest(unittest.TestCase):
    """What Alfred learned by a day, for the panel: 14 days of rosa learn the morning tea and the night's quiet."""

    @classmethod
    def setUpClass(cls):
        cls.view = learned_view("rosa", 14)

    def test_knows_the_morning_tea_and_the_night(self):
        parts = [" → ".join(s["parts"]) for s in self.view["sensors"]]
        self.assertTrue(any(p.startswith("bulb-") and "plug-" in p for p in parts), parts)  # lamp → kettle
        self.assertTrue(any(p.startswith("home-") and "quiet" in p for p in parts), parts)
        tea = next(s for s in self.view["sensors"] if "plug-" in " ".join(s["parts"]))
        self.assertEqual(tea["devices"], ["bulb", "plug"])

    def test_statuses_follow_the_day_and_only_change_when_one_does(self):
        st = self.view["statuses"]
        known = {s["id"] for s in self.view["sensors"] if s["tonight"] != "learned" and s["status"] == "active"}
        self.assertEqual(set(st["ids"]), known)
        frames = st["frames"]
        self.assertTrue(frames)
        self.assertEqual([ts for ts, _ in frames], sorted(ts for ts, _ in frames))
        self.assertTrue(all(a[1] != b[1] for a, b in zip(frames, frames[1:])))
        statuses = {status for _, row in frames for status, _ in row}
        self.assertTrue(statuses & {"done", "now"}, statuses)
        self.assertLessEqual(statuses, {"done", "now", "expected", "late", "not_today", "unknown"})

    def test_readings_are_that_days_and_name_known_sensors(self):
        ids = {s["id"] for s in self.view["sensors"]}
        day_start = int(datetime(2026, 9, 14, tzinfo=TZ).timestamp() * 1000)
        for sid, a, b in self.view["readings"]:
            self.assertIn(sid, ids)
            self.assertTrue(a < day_start + 86_400_000 and (b or a) >= day_start)

    def test_nothing_learned_on_the_first_day(self):
        view = learned_view("rosa", 1)
        self.assertEqual((view["sensors"], view["statuses"]["frames"][0][1]), ([], []))

    def test_device_types_from_parts(self):
        self.assertEqual(_device_types(["bulb-A (power on)", "end of home-C (quiet after bulb until bulb)",
                                        "contactSensor-AB (sensorState alarm)", "bulb-B (power on)"]),
                         ["bulb", "contactSensor"])

    def test_too_many_days_is_a_plain_error(self):
        with self.assertRaises(ValueError):
            learned_view("rosa", MAX_LEARN_DAYS + 1)
        with self.assertRaises(ValueError):
            knowledge_view("rosa", MAX_LEARN_DAYS + 1)

    def test_keeps_its_shape(self):  # the knowledge tab added nothing to it
        self.assertEqual(set(self.view), {"household", "day", "date", "max_days", "named", "sensors", "statuses", "readings"})


class KnowledgeViewTest(unittest.TestCase):
    """What the knowledge tab shows after 14 days of rosa, from the same run as the learned view."""

    @classmethod
    def setUpClass(cls):
        cls.view = knowledge_view("rosa", 14)

    def test_has_every_section(self):
        k = self.view
        self.assertLessEqual({*SECTIONS, "named", "modules", "devices"}, set(k))
        self.assertEqual((k["household"], k["day"], k["date"], k["start"], k["tomorrow"], k["max_days"]),
                         ("rosa", 14, "2026-09-14", "2026-09-01", "2026-09-15", MAX_LEARN_DAYS))
        for key in SECTIONS:
            self.assertIsInstance(k[key], list, key)
        self.assertEqual(set(k["modules"]), set(PARTS))
        self.assertLessEqual(set(k["modules"].values()), {"ok", "missing", "failed"})
        self.assertEqual((k["devices"]["bulb"], k["devices"]["plug"]), ("Light", "Plug"))
        self.assertIsInstance(k["named"], bool)
        self.assertFalse(any(p["name"] for p in k["patterns"]))  # no namer here
        self.assertEqual(json.loads(json.dumps(k)), k)

    def test_says_each_event_type_in_words(self):
        by_signal = {k["signal"]: k for k in self.view["kinds"]}
        plug = by_signal["plug.instantPower"]
        self.assertEqual((plug["description"], plug["device"], plug["device_name"], plug["unit"]),
                         ("~2,100 W for ~3 min", "plug", "Plug", "W"))
        self.assertGreaterEqual(plug["count"], 5)
        self.assertRegex(by_signal["contactSensor.sensorState"]["description"], r"^open for ~\d+ s$")
        self.assertRegex(by_signal["bulb.power"]["description"], r"^on for ~")
        quiet = [k["description"] for k in self.view["kinds"] if k["signal"] == "home.quiet"]
        self.assertTrue(any(d.startswith("quiet for ~") and d.endswith("after the light until the light") for d in quiet), quiet)
        climate = [k["description"] for k in self.view["kinds"] if k["device"] == "temperatureSensor"]
        self.assertTrue(climate and all(d.startswith(("temperature ", "humidity ")) for d in climate), climate)

    def test_patterns_are_the_learned_sensors(self):
        self.assertEqual({p["id"] for p in self.view["patterns"]}, {s["id"] for s in learned_view("rosa", 14)["sensors"]})
        tea = next(p for p in self.view["patterns"] if p["devices"] == ["bulb", "plug"])  # lamp → kettle
        for key in ("name", "parts", "time_of_day", "earliest", "latest", "weekdays", "days_seen", "days_total",
                    "confidence", "status", "learned_at", "first_seen", "last_seen", "lag_min", "key"):
            self.assertIn(key, tea)
        self.assertTrue(tea["key"].startswith(f"{tea['id']}|"))


class SharedRunTest(unittest.TestCase):
    """/learned and /knowledge come from one run per household and day, also when both are asked at once."""

    def test_one_run_for_both_views(self):
        key, calls, out = ("rosa", 4, 77, START), [], {}

        def run(*args):
            calls.append(args)
            time.sleep(0.2)  # long enough for the second request to arrive while it runs
            return {"learned": {"run": args}, "knowledge": {"run": args}}
        try:
            with mock.patch.object(web, "_learn_once", run):
                threads = [threading.Thread(target=lambda: out.update(learned=learned_view("rosa", 4, 77))),
                           threading.Thread(target=lambda: out.update(knowledge=knowledge_view("rosa", 4, 77)))]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                out["again"] = knowledge_view("rosa", 4, 77)
        finally:
            web._runs.pop(key, None)
        self.assertEqual(calls, [key])
        self.assertEqual(out, {"learned": {"run": key}, "knowledge": {"run": key}, "again": {"run": key}})
        self.assertNotIn(key, web._running)

    def test_a_failed_run_is_not_kept(self):
        key, calls = ("rosa", 4, 78, START), []

        def run(*args):
            calls.append(args)
            if len(calls) == 1:
                raise RuntimeError("the first try breaks")
            return {"learned": {}, "knowledge": {"ok": True}}
        try:
            with mock.patch.object(web, "_learn_once", run):
                with self.assertRaises(RuntimeError):
                    knowledge_view("rosa", 4, 78)
                self.assertEqual(knowledge_view("rosa", 4, 78), {"ok": True})
        finally:
            web._runs.pop(key, None)
        self.assertEqual(len(calls), 2)


def _module(**functions) -> types.ModuleType:
    module = types.ModuleType("fake")
    module.__dict__.update(functions)
    return module


OCCASION = {"id": "occasion-1", "name": None, "named_by": None, "episodes": 1,
            "fingerprint": {"door_openings": 9, "quiet_start": "01:10"}, "usual_hours": ["19:00", "01:00"],
            "weekdays": ["fri"], "confidence": 0.5}
FORECAST = {"habit_id": "habit-1", "date": "2026-09-04", "probability": 0.88, "expected": "07:59",
            "window": ["07:33", "08:27"], "confidence": 0.82, "basis": "Fridays: 1 of 1 since 1 Sept"}
DECISION = {"id": "decision-1", "at": "2026-09-04T07:50:00", "kind": "prepare", "habit_id": "habit-1", "occasion_id": None,
            "action": {"tool": "update_device_capabilities", "target": "bulb", "args": {"body": {"type": "brightness", "value": 30}}},
            "expectation": None, "confidence": 0.81, "mode": "do", "status": "planned", "reason": "Waking up usually 07:59"}
WARMUP = {"rate_c_per_h": 4.8, "loss_c_per_h": 0.6, "samples": 23, "confidence": 0.82, "as_of": "2026-09-03"}


class OtherPartsTest(unittest.TestCase):
    """Parts A, B and C (docs/habits-plan.md) are read defensively and called when they should be. Fake modules
    stand in for them, so these tests hold whether or not the real ones are written yet. Not cached: _learn_once."""

    def test_missing_parts_leave_their_sections_empty(self):
        with mock.patch.dict(sys.modules, dict.fromkeys(PART_MODULES)):  # None: as if they weren't written
            out = web._learn_once("rosa", 2, 1, START)
        k = out["knowledge"]
        self.assertEqual(k["modules"], dict.fromkeys(PARTS, "missing"))
        self.assertEqual([k[s] for s in ("habits", "occasions", "matches", "forecast", "decisions")], [[]] * 5)
        self.assertIsNone(k["warmup"])
        self.assertEqual((k["date"], out["learned"]["date"]), ("2026-09-02", "2026-09-02"))
        self.assertIsNone(out["thoughts"])  # no wakes table: part D isn't wired in

    def test_calls_each_part_when_it_should(self):
        calls = []
        habit = {"id": "habit-1", "name": None, "status": "active", "patterns": {"pattern-2"}, "first_seen": date(2026, 9, 1),
                 "recurrence": {"kind": "daily", "text": "every day"}, "confidence": 0.84}
        parts = {
            "source.learn.grouping": _module(learn_habits=lambda db, day: calls.append(("habits", day)),
                                             export_habits=lambda db: {"habits": [habit]}),
            "source.learn.occasions": _module(learn_occasions=lambda db, day: calls.append(("occasions", day)),
                                              export_occasions=lambda db: {"occasions": [OCCASION]},
                                              match_now=lambda db, now: calls.append(("match", now)) or [{"occasion_id": "occasion-1"}]),
            "source.learn.forecast": _module(forecast=lambda db, now: calls.append(("forecast", now)) or [FORECAST]),
            "source.anticipate": _module(plan_decisions=lambda db, now: calls.append(("decisions", now)) or [DECISION]),
            "source.learn.warmup": _module(warmup=lambda db, now: calls.append(("warmup", now)) or WARMUP),
        }
        with mock.patch.dict(sys.modules, parts):
            k = web._learn_once("rosa", 3, 1, START)["knowledge"]
        nights = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
        self.assertEqual([d for what, d in calls if what == "habits"], nights)  # each night, after the patterns
        self.assertEqual([d for what, d in calls if what == "occasions"], nights)
        tomorrow = datetime(2026, 9, 4)  # once the last day is over
        self.assertEqual(calls[-4:], [("match", tomorrow), ("forecast", tomorrow), ("decisions", tomorrow), ("warmup", tomorrow)])
        self.assertEqual(k["warmup"], WARMUP)
        self.assertEqual(k["modules"], dict.fromkeys(PARTS, "ok"))
        self.assertEqual(k["tomorrow"], "2026-09-04")
        self.assertEqual((k["habits"][0]["first_seen"], k["habits"][0]["patterns"]), ("2026-09-01", ["pattern-2"]))  # as JSON
        self.assertEqual((k["forecast"], k["decisions"], k["matches"]), ([FORECAST], [DECISION], [{"occasion_id": "occasion-1"}]))
        self.assertEqual(k["occasions"], [{**OCCASION, "episode_list": [], "key": "occasion:occasion-1"}])

    def test_a_part_that_fails_shows_nothing(self):
        def broken(db, day):
            raise RuntimeError("not finished")

        def unread(db):  # its table was never made
            return db.execute("SELECT data FROM habits").fetchall()
        parts = {
            "source.learn.grouping": _module(learn_habits=lambda db, day: None, export_habits=unread),
            "source.learn.occasions": _module(learn_occasions=broken, export_occasions=lambda db: [OCCASION]),
            "source.learn.forecast": _module(),  # no forecast() yet
            "source.anticipate": _module(plan_decisions=lambda db, now: [DECISION]),
            "source.learn.warmup": _module(),
        }
        with mock.patch.dict(sys.modules, parts), self.assertLogs("source.simulate_web", "WARNING") as logs, \
                self.assertLogs("source.learn.run", "ERROR") as nightly:
            out = web._learn_once("rosa", 2, 1, START)
        k = out["knowledge"]
        # A nightly learner that fails is logged by learn_day each night and never stops the run; what it
        # stored before still shows. A part whose reading fails shows nothing.
        self.assertEqual(len(nightly.records), 2)
        self.assertEqual(k["modules"], {"habits": "failed", "occasions": "ok", "forecast": "missing", "decisions": "ok",
                                        "warmup": "missing"})
        self.assertEqual((k["habits"], k["forecast"]), ([], []))
        self.assertEqual(k["decisions"], [DECISION])
        self.assertEqual(len(logs.records), 1)  # once per part and run, not every night
        self.assertEqual(out["learned"]["date"], "2026-09-02")

    def test_occasions_get_their_episodes_times(self):
        def learn_occasions(db, day):
            db.execute("CREATE TABLE IF NOT EXISTS occasion_episodes (occasion_id TEXT, start, end, features TEXT)")
            if day == date(2026, 9, 2):
                db.execute("INSERT INTO occasion_episodes VALUES ('occasion-1', ?, '2026-09-03T01:10:00', ?)",
                           (to_ms(datetime(2026, 9, 2, 19, 40)), json.dumps({"door_openings": 9})))
            db.commit()
        parts = {**dict.fromkeys(PART_MODULES),
                 "source.learn.occasions": _module(learn_occasions=learn_occasions, export_occasions=lambda db: [OCCASION])}
        with mock.patch.dict(sys.modules, parts):
            k = web._learn_once("rosa", 2, 1, START)["knowledge"]
        self.assertEqual(k["occasions"][0]["episodes"], 1)
        self.assertEqual(k["occasions"][0]["episode_list"],  # epoch ms as the home's clock time
                         [{"start": "2026-09-02T19:40:00", "end": "2026-09-03T01:10:00", "features": {"door_openings": 9}}])


DEMO_START = date(2026, 9, 21)  # the demo house's first day (docs/demo-plan.md, C12)


def _demo_db(wakes: list[tuple], decisions: list[dict]) -> sqlite3.Connection:
    """Part D's tables, as C8 and C9 describe them: wakes (key, ts, data) and decisions (id, ts, data)."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE wakes (key TEXT PRIMARY KEY, ts INTEGER, data TEXT)")
    db.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, ts INTEGER, data TEXT)")
    db.executemany("INSERT INTO wakes VALUES (?, ?, ?)", wakes)
    db.executemany("INSERT INTO decisions VALUES (?, 0, ?)", [(d["id"], json.dumps(d)) for d in decisions])
    return db


def _ms(*args) -> int:
    return to_ms(datetime(*args))


class DemoRequestTest(unittest.TestCase):
    def test_reads_the_query(self):
        self.assertEqual(web._demo_request({}), ("demo", 1, 1, DEMO_START, "demo" in web.PRESETS))
        self.assertEqual(web._demo_request({"household": "rosa", "day": "3"}), ("rosa", 3, 1, START, True))
        self.assertEqual(web._demo_request({"day": "60", "start": "2026-10-01"})[1:4], (60, 1, date(2026, 10, 1)))

    def test_bad_input_is_a_plain_error(self):
        for bad in ({"household": "nobody"}, {"household": "../demo"}, {"day": "0"}, {"day": "x"}, {"start": "soon"}):
            with self.assertRaises(ValueError, msg=bad):
                web._demo_request(bad)

    def test_a_preset_can_have_its_own_first_day(self):
        self.assertEqual(web._default_start("rosa"), START)
        self.assertEqual(web._default_start("demo"), DEMO_START)  # from its fixture, until the preset says
        with mock.patch.dict(web.PRESETS, {"later": types.SimpleNamespace(start="2026-10-05")}):
            self.assertEqual(web._default_start("later"), date(2026, 10, 5))


class MomentsTest(unittest.TestCase):
    def test_the_demo_fixture(self):
        out = web.moments_view("demo", 1, DEMO_START, simulated=False)
        self.assertEqual((out["source"], out["start"]), ("fixture", "2026-09-21"))
        self.assertEqual([(m["id"], m["day"], m["time"]) for m in out["moments"]],
                         [("gathering", 54, "20:30"), ("saturday-morning", 55, "07:45"),
                          ("weekday-morning", 59, "06:30"), ("ana-arrives", 60, "18:00")])
        for m in out["moments"]:
            self.assertEqual(set(m), {"id", "title", "day", "time", "focus", "say"})
            self.assertTrue(m["title"] and m["say"] and m["focus"])

    def test_the_simulators_own_come_first(self):
        with mock.patch.object(web, "DEMO_RUNS", Path(tempfile.mkdtemp())):  # nothing pre-computed
            out = web.moments_view("demo", 1, DEMO_START)
        self.assertEqual(out["source"], "simulator")
        self.assertEqual([m["id"] for m in out["moments"]], [m["id"] for m in sorted(
            simulate.DEMO_MOMENTS, key=lambda m: (m["day"], m["time"]))])
        self.assertEqual(out["moments"][0]["focus"], [])

    def test_a_precomputed_run_comes_first(self):
        """source/demo.py's moments carry the habits it found for each one (`focus`)."""
        runs = Path(tempfile.mkdtemp())
        mine = [{"id": "late", "title": "Late", "day": 3, "time": "22:00", "focus": ["habit-2"], "say": "x"},
                {"id": "early", "title": "Early", "day": 3, "time": "07:00"}]
        (runs / f"{web._names_file('demo', 1, DEMO_START).stem}.json").write_text(json.dumps({"moments": mine}))
        with mock.patch.object(web, "DEMO_RUNS", runs):
            out = web.moments_view("demo", 1, DEMO_START)
        self.assertEqual(out["source"], "precomputed")
        self.assertEqual([(m["id"], m["focus"]) for m in out["moments"]], [("early", []), ("late", ["habit-2"])])

    def test_none_for_a_house_without_any(self):
        self.assertEqual(web.moments_view("rosa", 1, START)["moments"], [])
        self.assertEqual(web.moments_view("rosa", 1, START)["source"], "none")

    def test_only_moments_it_can_jump_to(self):
        rows = [{"day": 0, "time": "07:00"}, {"day": 2, "time": "7am"}, {"day": "x"}, "nope",
                {"day": "4", "time": "08:15", "focus": "habit-1"}]
        self.assertEqual(web._clean_moments(rows), [{"id": "day-4-08:15", "title": "Day 4, 08:15", "day": 4, "time": "08:15",
                                                     "focus": [], "say": ""}])
        self.assertEqual(web._clean_moments(None), [])


class ThoughtsTest(unittest.TestCase):
    SOON = {"kind": "habit_soon", "habit": {"id": "habit-4", "name": "Ana gets home"}}
    HEAT = {"id": "decision-3", "at": "2026-11-19T18:15:00", "kind": "llm", "habit_id": "habit-4", "wake": "habit_soon:habit-4:2026-11-19",
            "action": {"tool": "update_device_capabilities", "target": "thermostat",
                       "args": {"body": {"type": "targetTemperature", "value": 21}}},
            "confidence": 0.84, "mode": "do", "status": "done", "reason": "Ana usually gets home at 18:40."}

    def test_wakes_and_what_they_led_to(self):
        db = _demo_db([("habit_soon:habit-4:2026-11-19", _ms(2026, 11, 19, 18, 10), json.dumps(self.SOON)),
                       ("arrival:c-8f3a:2026-11-19:18", _ms(2026, 11, 19, 18, 41), "not json"),
                       ("occasion:occasion-1:2026-11-18", _ms(2026, 11, 18, 20, 35), "{}")],  # another day
                      [self.HEAT, {**self.HEAT, "id": "decision-4", "wake": "habit_soon:habit-9:2026-11-19"},
                       {"id": "decision-5", "kind": "prepare"}])
        out = web.read_thoughts(db, date(2026, 11, 19))
        self.assertEqual(out, [
            {"at": "2026-11-19T18:10:00", "kind": "habit_soon", "key": "habit_soon:habit-4:2026-11-19", "habit_id": "habit-4",
             "occasion_id": None, "subject": "Ana gets home", "sentence": "Ana usually gets home at 18:40.", "confidence": 0.84,
             "mode": "do", "status": "done", "action": self.HEAT["action"], "due": "2026-11-19T18:15:00"},
            {"at": "2026-11-19T18:41:00", "kind": "arrival", "key": "arrival:c-8f3a:2026-11-19:18", "habit_id": None,
             "occasion_id": None, "subject": None, "sentence": None, "confidence": None, "mode": None, "status": None,
             "action": None, "due": None}])

    def test_one_entry_per_decision(self):
        db = _demo_db([("occasion:occasion-1:2026-11-13", "2026-11-13T20:35:00", json.dumps({"confidence": 0.8}))],
                      [{"id": "d-1", "wake": "occasion:occasion-1:2026-11-13", "mode": "do", "reason": "Lower it."},
                       {"id": "d-2", "wake": "occasion:occasion-1:2026-11-13", "mode": "suggest", "confidence": 0.5}])
        out = web.read_thoughts(db, date(2026, 11, 13))
        self.assertEqual([(t["kind"], t["occasion_id"], t["mode"], t["confidence"]) for t in out],
                         [("occasion", "occasion-1", "do", 0.8), ("occasion", "occasion-1", "suggest", 0.5)])

    def test_none_without_a_wakes_table(self):
        self.assertIsNone(web.read_thoughts(sqlite3.connect(":memory:"), date(2026, 11, 19)))
        db = _demo_db([], [])
        db.execute("DROP TABLE decisions")  # wakes without decisions still read
        self.assertEqual(web.read_thoughts(db, date(2026, 11, 19)), [])

    def test_the_demo_fixture(self):
        out = web.thoughts_view("demo", 60, 1, DEMO_START, simulated=False)
        self.assertEqual((out["source"], out["date"]), ("fixture", "2026-11-19"))
        soon = out["wakes"][0]
        self.assertEqual((soon["kind"], soon["habit_id"], soon["mode"], soon["status"]), ("habit_soon", "habit-4", "do", "done"))
        self.assertGreaterEqual(soon["confidence"], 0.75)
        self.assertEqual(soon["action"]["args"]["body"], {"type": "targetTemperature", "value": 21})
        for w in out["wakes"]:
            self.assertLessEqual({"at", "kind", "key", "habit_id", "occasion_id", "sentence", "confidence", "mode", "status",
                                  "action"}, set(w))
            self.assertTrue(w["at"].startswith("2026-11-19"))
        gathering = web.thoughts_view("demo", 54, 1, DEMO_START, simulated=False)["wakes"]
        self.assertIn("occasion", [w["kind"] for w in gathering])
        self.assertEqual(web.thoughts_view("demo", 3, 1, DEMO_START, simulated=False)["source"], "none")

    def test_the_run_first_then_the_fixture(self):
        """The learning runs don't run the loop, so only source/demo.py's run has wakes; then the fixture."""
        runs, fixtures = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        stem = web._names_file("rosa", 1, START).stem
        db = sqlite3.connect(runs / f"{stem}.sqlite3")
        db.execute("CREATE TABLE wakes (key TEXT, ts INTEGER, data TEXT)")
        db.commit()
        db.close()
        (runs / f"{stem}.json").write_text(json.dumps({"llm": "scripted"}))
        with mock.patch.object(web, "DEMO_RUNS", runs):
            self.assertEqual(web.thoughts_view("rosa", 3, 1, START), {"household": "rosa", "day": 3, "date": "2026-09-03",
                                                                       "wakes": [], "source": "precomputed",
                                                                       "llm": "scripted"})  # a quiet day is still the run's
        (fixtures / "rosa.json").write_text(json.dumps({"thoughts": {"3": [{"at": "2026-09-03T08:00:00", "key": "habit_soon:habit-1:x"}]}}))
        with mock.patch.object(web, "DEMO_RUNS", Path(tempfile.mkdtemp())), mock.patch.object(web, "FIXTURES", fixtures):
            out = web.thoughts_view("rosa", 3, 1, START)
        self.assertEqual((out["source"], out["wakes"][0]["kind"], out["wakes"][0]["habit_id"]), ("fixture", "habit_soon", "habit-1"))

    def test_a_precomputed_run(self):
        runs = Path(tempfile.mkdtemp())
        path = runs / f"{web._names_file('rosa', 1, START).stem}.sqlite3"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE wakes (key TEXT, ts INTEGER, data TEXT)")
        db.execute("INSERT INTO wakes VALUES ('habit_soon:habit-1:2026-09-03', ?, '{}')", (_ms(2026, 9, 3, 6, 30),))
        db.commit()
        db.close()
        with mock.patch.object(web, "_learned", return_value={"thoughts": None}), mock.patch.object(web, "DEMO_RUNS", runs):
            out = web.thoughts_view("rosa", 3, 1, START)
        self.assertEqual((out["source"], [w["at"] for w in out["wakes"]]), ("precomputed", ["2026-09-03T06:30:00"]))

    def test_a_run_with_wakes(self):
        def learn_occasions(db, day):  # stands in for part D writing its wakes during the day
            db.execute("CREATE TABLE IF NOT EXISTS wakes (key TEXT PRIMARY KEY, ts INTEGER, data TEXT)")
            db.execute("INSERT OR IGNORE INTO wakes VALUES (?, ?, '{}')",
                       (f"habit_soon:habit-1:{day}", to_ms(datetime.combine(day, datetime.min.time()).replace(hour=7))))
            db.commit()
        parts = {**dict.fromkeys(PART_MODULES), "source.learn.occasions": _module(learn_occasions=learn_occasions)}
        with mock.patch.dict(sys.modules, parts), self.assertLogs("source.learn.run", "ERROR"):  # the missing habit learner
            out = web._learn_once("rosa", 2, 1, START)
        self.assertEqual([(t["at"], t["habit_id"]) for t in out["thoughts"]], [("2026-09-02T07:00:00", "habit-1")])


KNOWS = {"habits": [
    {"id": "habit-1", "name": "Waking up", "status": "active", "recurrence": {"text": "every day"},
     "windows": [{"days": ["mon", "tue", "wed", "thu", "fri"], "usual": "07:10", "earliest": "06:55", "latest": "07:30"},
                 {"days": ["sat", "sun"], "usual": "08:40", "earliest": "08:20", "latest": "09:10"}]},
    {"id": "habit-4", "name": "Ana gets home", "status": "active", "person": {"phone": "c-8f3a", "name": "Ana's phone"},
     "window": {"usual": "18:40", "earliest": "18:25", "latest": "18:55"},
     "recurrence": {"weekdays": ["mon", "tue", "wed", "thu", "fri"], "text": "every weekday"}},
    {"id": "habit-5", "name": "João gets home", "status": "active", "person": {"phone": "c-2b71", "name": "João's phone"},
     "windows": [{"days": ["mon", "wed", "thu", "fri"], "usual": "19:30"}, {"days": ["tue"], "usual": "21:00"}]},
    {"id": "habit-9", "status": "fading", "window": {"usual": "18:40"}}],
    "occasions": [{"id": "occasion-1", "name": "Friday night gathering", "weekdays": ["fri"], "episodes": 3}],
    "warmup": {"rate_c_per_h": 4.8}}


class PlantedTest(unittest.TestCase):
    def test_the_demo_fixture(self):
        out = web.planted_view("demo", 60, 1, DEMO_START, simulated=False)
        self.assertEqual(out["source"], "fixture")
        self.assertGreaterEqual(len(out["rows"]), 8)
        for r in out["rows"]:
            self.assertEqual(set(r), {"planted", "found", "found_text", "match"})
            self.assertIn(r["match"], ("yes", "partly", "no"))
        self.assertIn(("Ana home weekdays 18:40", "habit-4", "yes"), [(r["planted"], r["found"], r["match"]) for r in out["rows"]])
        self.assertEqual(web.planted_view("demo", 61, 1, DEMO_START, simulated=False)["rows"], out["rows"])  # the latest up to that day
        self.assertEqual(web.planted_view("demo", 59, 1, DEMO_START, simulated=False)["source"], "none")

    def test_matches_the_answer_key_with_what_alfred_knows(self):
        key = [{"planted": "Ana home weekdays 18:40", "usual": "18:40", "days": ["mon", "thu"], "person": "c-8f3a"},
               {"planted": "João home Tuesdays 21:00", "usual": "21:00", "days": ["tue"], "person": "c-2b71"},
               {"planted": "João home 19:30", "usual": "18:55", "person": "c-2b71"},  # 35 min off: partly
               {"planted": "Waking up at weekends 08:30", "usual": "08:30", "days": ["sat"]},
               {"planted": "Someone home at 13:00", "usual": "13:00"},
               {"planted": "Friday gatherings", "kind": "occasion", "days": ["fri"]},
               {"planted": "Warms 5 °C an hour", "kind": "warmup"},
               {"planted": "Already matched", "found": "habit-7", "found_text": "Laundry", "match": "partly"},
               {"no": "planted text"}]
        with mock.patch.object(web, "_answer_key", return_value=[r for r in key if r.get("planted")]), \
                mock.patch.object(web, "knowledge_view", return_value=KNOWS):
            out = web.planted_view("rosa", 20, 1, START)
        self.assertEqual(out["source"], "simulator")
        self.assertEqual([(r["found"], r["match"]) for r in out["rows"]],
                         [("habit-4", "yes"), ("habit-5", "yes"), ("habit-5", "partly"), ("habit-1", "yes"), (None, "no"),
                          ("occasion-1", "yes"), ("warmup", "yes"), ("habit-7", "partly")])
        self.assertEqual(out["rows"][3]["found_text"], "Waking up · every day · weekdays 06:55–07:30, weekends 08:20–09:10")
        self.assertEqual(out["rows"][1]["found_text"], "João gets home · João's phone")

    def test_the_demo_houses_answer_key(self):
        """simulate.planted's rows, with their weekday windows and the person's phone."""
        with mock.patch.object(web, "knowledge_view", return_value=KNOWS):
            rows = {r["planted"]: r for r in web.planted_view("demo", 60, 1, DEMO_START)["rows"]}
        self.assertEqual(len(rows), len(simulate.planted("demo", DEMO_START)))
        self.assertEqual((rows["Ana home weekdays 18:40"]["found"], rows["Ana home weekdays 18:40"]["match"]), ("habit-4", "yes"))
        waking = next(r for p, r in rows.items() if p.startswith("Waking up"))
        self.assertEqual((waking["found"], waking["match"]), ("habit-1", "yes"))
        joao = next(r for p, r in rows.items() if p.startswith("João home"))
        self.assertEqual(joao["found"], "habit-5")
        self.assertEqual(rows["The house warms 1 °C in about 12 min"]["match"], "yes")

    def test_an_appliance_by_its_own_plug(self):
        """João home on Mondays at 19:30 and the washing machine on Mondays at 19:30: the plug's row is the plug's."""
        k = {"habits": [
            {"id": "habit-9", "status": "active", "person": {"phone": "c-2b71"}, "steps": [{"pattern": "pattern-18"}],
             "windows": [{"days": ["mon"], "usual": "19:28"}]},
            {"id": "habit-11", "status": "active", "steps": [{"pattern": "pattern-50"}],
             "windows": [{"days": ["mon"], "usual": "19:35"}]}],
            "patterns": [{"id": "pattern-18", "key": "pattern-18|link:phone:c-2b71.connected>contactSensor-B"},
                         {"id": "pattern-50", "key": "pattern-50|kind:plug-D"}],
            "kinds": [{"id": "plug-D", "signal": "plug:washing-machine.instantPower"},
                      {"id": "contactSensor-B", "signal": "contactSensor.sensorState"}]}
        row = {"planted": "Washing machine Mondays 19:30", "windows": [{"days": ["mon"], "usual": "19:30"}],
               "signals": ["plug:washing-machine.instantPower"]}
        self.assertEqual(web._match(row, k)["found"], "habit-11")

    def test_none_without_an_answer_key(self):
        out = web.planted_view("rosa", 3, 1, START)
        self.assertEqual((out["rows"], out["source"]), ([], "none"))


class DemoHouseTest(unittest.TestCase):
    """What the demo house adds to the 3D view and the knowledge tab, read without the demo preset."""

    def test_draws_the_thermostat_and_the_phones(self):
        home = {"devices": [
            {"id": "th-1", "type": "thermostat", "capabilities": [
                {"type": "temperatureValue", "value": 19.4}, {"type": "targetTemperature", "value": 16.0},
                {"type": "heatingState", "value": "off"}, {"type": "mode", "value": "heat"}, {"type": "firmware", "value": "x"}]},
            {"id": "rt-1", "type": "router", "capabilities": [
                {"type": "connectedClients", "value": [{"id": "c-8f3a", "name": "Ana's phone", "kind": "phone", "known": True}]}]}],
            "security": {}}
        self.assertEqual(compact(home)[:2], [
            {"temperatureValue": 19.4, "targetTemperature": 16.0, "heatingState": "off", "mode": "heat"},
            {"connectedClients": [{"id": "c-8f3a", "name": "Ana's phone", "kind": "phone", "known": True}]}])

    def test_event_types_of_one_device_among_several(self):
        kettle = types.SimpleNamespace(id="kind-3", signal="plug:kettle.instantPower", count=40, duration_s=180, size=2100,
                                       value=None)
        phone = types.SimpleNamespace(id="kind-8", signal="phone:c-8f3a.connected", count=30, duration_s=36000, size=None,
                                      value="on")
        devices = {"plug": "Kettle", "plug:kettle": "Kettle", "plug:tv": "TV"}
        card = web._kind_card(kettle, devices)
        self.assertEqual((card["device"], card["device_name"], card["unit"], card["description"]),
                         ("plug:kettle", "Kettle", "W", "~2,100 W for ~3 min"))
        self.assertEqual(web._kind_words(phone, devices), "connected for ~10 h")
        self.assertEqual((web._slug("Washing machine"), web._device_type("plug:washing-machine")), ("washing-machine", "plug"))


class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str, **headers) -> tuple[int, str, bytes]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.status, res.headers["Content-Type"], res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers["Content-Type"], err.read()

    def test_serves_the_page(self):
        status, kind, body = self._get("/visualizer")
        self.assertEqual((status, kind), (200, "text/html; charset=utf-8"))
        self.assertEqual(body, VIEW_PAGE.read_bytes())
        self.assertIn(b"/ajax/libs/three.js/r128/three.min.js", body)  # an exact version, with its hash
        self.assertIn(b'integrity="sha384-', body)
        self.assertIn(b'role="tablist"', body)  # House | What Alfred knows
        self.assertIn(b"/knowledge?household=", body)

    def test_the_form_links_to_the_page(self):
        self.assertIn(b'href="/visualizer"', self._get("/")[2])

    def test_serves_a_day(self):
        status, kind, body = self._get("/day?household=couple&day=2")
        self.assertEqual((status, kind), (200, "application/json"))
        self.assertEqual(json.loads(body), json.loads(json.dumps(day_view("couple", 2))))

    def test_bad_day_is_a_plain_error(self):
        status, _, body = self._get("/day?household=nobody")
        self.assertEqual(status, 400)
        self.assertIn("unknown household", json.loads(body)["error"])
        self.assertEqual(self._get("/day/more")[0], 404)

    def test_serves_what_was_learned(self):
        status, kind, body = self._get("/learned?household=rosa&day=2")
        self.assertEqual((status, kind), (200, "application/json"))
        self.assertEqual(json.loads(body)["date"], "2026-09-02")
        self.assertEqual(self._get("/learned?household=nobody")[0], 400)

    def test_serves_what_alfred_knows(self):
        status, kind, body = self._get("/knowledge?household=rosa&day=2")
        self.assertEqual((status, kind), (200, "application/json"))
        k = json.loads(body)
        self.assertEqual((k["date"], k["tomorrow"]), ("2026-09-02", "2026-09-03"))
        self.assertLessEqual({*SECTIONS, "named"}, set(k))
        self.assertEqual(self._get("/knowledge?household=nobody")[0], 400)
        status, _, body = self._get(f"/knowledge?household=rosa&day={MAX_LEARN_DAYS + 1}")
        self.assertEqual(status, 400)
        self.assertIn(f"stops at day {MAX_LEARN_DAYS}", json.loads(body)["error"])

    def test_a_broken_learner_is_a_plain_error(self):
        with mock.patch.object(web, "_learn_once", side_effect=RuntimeError("boom")), \
                self.assertLogs("source.simulate_web", "ERROR"):
            status, kind, body = self._get("/knowledge?household=rosa&day=5&seed=88")
        self.assertEqual((status, kind), (500, "application/json"))
        self.assertIn("Learning failed", json.loads(body)["error"])

    def test_serves_the_demo_screen(self):
        with mock.patch.object(web, "DEMO_RUNS", Path(tempfile.mkdtemp())), \
                mock.patch.object(web, "knowledge_view", return_value=KNOWS):  # no 60-day run in a test
            self._demo_screen()

    def _demo_screen(self):
        for path, key in (("/moments?household=demo", "moments"), ("/thoughts?household=demo&day=60", "wakes"),
                          ("/planted?household=demo&day=60", "rows"), ("/moments", "moments")):
            status, kind, body = self._get(path)
            self.assertEqual((status, kind), (200, "application/json"), path)
            out = json.loads(body)
            self.assertTrue(out[key], path)
            self.assertEqual(out["household"], "demo")
        self.assertEqual(json.loads(self._get("/thoughts?household=demo&day=60")[2])["date"], "2026-11-19")
        with mock.patch.object(web, "_learned", return_value={"thoughts": None}):
            out = json.loads(self._get("/thoughts?household=rosa&day=2")[2])
        self.assertEqual((out["wakes"], out["source"]), ([], "none"))
        self.assertEqual(json.loads(self._get("/moments?household=rosa")[2])["moments"], [])

    def test_bad_demo_requests_are_plain_errors(self):
        for path in ("/moments?household=nobody", "/thoughts?household=demo&day=0", "/planted?household=demo&day=x"):
            status, _, body = self._get(path)
            self.assertEqual(status, 400, path)
            self.assertIn("error", json.loads(body))
        with mock.patch.object(web, "planted_view", side_effect=RuntimeError("boom")), \
                self.assertLogs("source.simulate_web", "ERROR"):
            self.assertEqual(self._get("/planted?household=demo&day=60")[0], 500)

    def test_the_page_asks_for_the_demo_screen(self):
        body = VIEW_PAGE.read_text(encoding="utf-8")
        for needle in ("/moments?household=", "/thoughts?household=", "/planted?household=", 'id="moments"', 'id="thinks"',
                       'id="k-planted"', "targetTemperature", "heatingState", "shifted", "windows", '<option value="demo">'):
            self.assertIn(needle, body)
        self.assertEqual(body.count("<script src="), 1)  # still three.js only

    def test_refuses_other_hosts_and_sites(self):
        self.assertEqual(self._get("/day", Host="evil.example")[0], 403)
        self.assertEqual(self._get("/visualizer", Host="evil.example")[0], 403)
        self.assertEqual(self._get("/day", Origin="http://evil.example")[0], 403)


if __name__ == "__main__":
    unittest.main()
