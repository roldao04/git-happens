"""source/demo.py: the scripted replies, the simulated home Alfred writes to, the scripted names.

The whole pre-computation (60 days, then the moment days) takes minutes; it is run by hand
(`uv run python -m source.demo`), and its pieces are tested here. No real LLM is called.
"""
import asyncio
import json
import sqlite3
import unittest
from datetime import datetime

from source import demo
from source.guard import READ_TOOL, WRITE_TOOL, guarded_write
from source.learn import to_local, to_ms
from source.learn.habits import QUIET

NOW = datetime(2026, 11, 19, 17, 58)


def soon(target=16.0, room=18.5, prepare=("heating",)) -> dict:
    return {"kind": "habit_soon", "key": "habit_soon:habit-5:2026-11-19", "data": {
        "habit": {"id": "habit-5", "name": "Ana gets home"}, "expected_at": "2026-11-19T18:38:00",
        "prepare_at": "2026-11-19T18:08:00", "lead_minutes": 30.5, "prepare": list(prepare),
        "warmup": {"rate_c_per_h": 4.9},
        "home": {"thermostat": True, "target_c": target, "room_c": room, "comfort_c": 21.0, "phones_home": []}}}


class ScriptedRepliesTest(unittest.TestCase):
    def test_warming_ahead_takes_two_steps(self):
        """16 → 21 °C: 3 °C at most a write and 10 min between writes (the guard), so two steps, the first now."""
        out = demo.scripted_proposals(soon(), {"now": NOW.isoformat()})
        self.assertEqual([(p["action"]["args"]["body"]["value"], p["at"]) for p in out],
                         [(19.0, "2026-11-19T17:58:00"), (21.0, "2026-11-19T18:09:00")])
        for p in out:
            self.assertEqual((p["wake"], p["doubt"], p["action"]["target"]), (soon()["key"], 0.0, "thermostat"))
        self.assertIn("Ana gets home usually starts around 18:38", out[0]["reason"])
        self.assertIn("about 30 min to go from 18.5 to 21 °C", out[0]["reason"])

    def test_nothing_to_heat(self):
        self.assertEqual(demo.scripted_proposals(soon(prepare=("lamp",)), {"now": NOW.isoformat()}), [])
        no_thermostat = soon()
        no_thermostat["data"]["home"]["thermostat"] = False
        self.assertEqual(demo.scripted_proposals(no_thermostat, {"now": NOW.isoformat()}), [])

    def test_a_gathering_lowers_the_heating_and_says_what_moves(self):
        reason = {"kind": "occasion", "key": "occasion:occasion-1:2026-11-13", "data": {
            "occasion": {"id": "occasion-1", "name": "Friday night gathering", "fingerprint": {"guest_phones": 6}},
            "forecast_shifted": [{"habit_id": "habit-2", "expected": "10:22",
                                  "shifted": {"minutes": 89, "before": {"expected": "08:53"}}}],
            "warmup": {"rate_c_per_h": 5.0},
            "home": {"thermostat": True, "target_c": 21.0, "room_c": 21.4, "comfort_c": 21.0, "guest_phones": 5}}}
        (p,) = demo.scripted_proposals(reason, {"now": "2026-11-13T20:25:00"})
        self.assertEqual(p["action"]["args"]["body"], {"type": "targetTemperature", "value": 19.0})
        self.assertIn("5 guests' phones", p["reason"])
        self.assertIn("tomorrow's warm-up moves from 08:08 to 09:37", p["reason"])  # 2.5 °C at 5 °C/h + 15 min

    def test_the_agent_answers_only_learned_wakes(self):
        ctx = {"now": NOW.isoformat(), "reasons": [{"trigger_id": "smoke"}, soon()]}
        out = asyncio.run(demo.ScriptedAgent().run(ctx))
        self.assertEqual(len(out["proposals"]), 2)
        self.assertTrue(out["notes"].startswith("(scripted)"))


class SimulatedHomeTest(unittest.TestCase):
    """Day 1 of the demo house, as the loop polls it, with Alfred at the thermostat."""

    def test_the_room_answers_alfreds_write(self):
        source = demo.SimSource("demo", 1, 1, [])
        actuator = demo.SimActuator(source)

        async def until(hhmm: str) -> None:
            await source.fetch()
            while to_local(source.now).strftime("%H:%M") < hhmm:
                await source.fetch()

        def target() -> float:
            dev = next(d for d in source.home["devices"] if d["type"] == "thermostat")
            return next(c["value"] for c in dev["capabilities"] if c["type"] == "targetTemperature")

        asyncio.run(until("03:00"))
        self.assertEqual(target(), 16.0)  # the people set 16 °C at bedtime
        dev_id = next(d["id"] for d in source.home["devices"] if d["type"] == "thermostat")
        devices = {dev_id: "thermostat"}

        async def call(tool, args):
            return await actuator.call(tool, args)

        from source.guard import Heat
        heat = Heat(source.now, target=16.0, someone_home=True)
        action = asyncio.run(guarded_write(call, {"deviceId": dev_id, "body": {"type": "targetTemperature", "value": 19.0}},
                                           devices, allow_plug_on=False, heat=heat))
        self.assertEqual((action["status"], action["verified"]), ("sent", "confirmed"))
        self.assertEqual(source.writes, [(source.now, "targetTemperature", 19.0)])
        asyncio.run(source.fetch())
        self.assertEqual(target(), 19.0)  # the simulation goes on from the write
        self.assertEqual(actuator.log[0]["body"], {"type": "targetTemperature", "value": 19.0})
        self.assertEqual(json.loads(asyncio.run(actuator.call(READ_TOOL, {"deviceId": "nope"}))), {})
        with self.assertRaises(RuntimeError):
            asyncio.run(actuator.call("patch_home_security_status", {}))
        self.assertEqual(asyncio.run(actuator.call(WRITE_TOOL, {"deviceId": "x", "body": {"type": "power", "value": "off"}})),
                         "ok")  # recorded, not played back
        self.assertEqual(len(source.writes), 1)


class ScriptedNamesTest(unittest.TestCase):
    def db(self) -> sqlite3.Connection:
        db = sqlite3.connect(":memory:")
        db.executescript("""
            CREATE TABLE habits (id TEXT PRIMARY KEY, status TEXT, data TEXT);
            CREATE TABLE occasions (id TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE kinds (id TEXT PRIMARY KEY, signal TEXT, data TEXT);
            CREATE TABLE sensors (id TEXT PRIMARY KEY, signature TEXT);
            CREATE TABLE devices (device_id TEXT, type TEXT, name TEXT, first_ts INTEGER);""")
        db.executemany("INSERT INTO kinds VALUES (?, ?, '{}')", [("home-B", QUIET), ("plug-C", "plug:tv.instantPower"),
                                                                  ("contactSensor-B", "contactSensor.sensorState")])
        db.executemany("INSERT INTO sensors VALUES (?, ?)", [
            ("pattern-8", "kind-end:home-B"), ("pattern-3", "kind:plug-C"), ("pattern-10", "kind:home-B"),
            ("pattern-9", "link:phone:c-8f3a.connected>contactSensor-B")])
        db.executemany("INSERT INTO devices VALUES (?, ?, ?, 0)", [("p1", "plug", "Kettle"), ("p2", "plug", "TV"),
                                                                   ("c-8f3a", "phone", "Ana's phone")])

        def habit(hid, steps, usual, **extra):
            h = {"id": hid, "name": None, "named_by": None, "steps": [{"pattern": s} for s in steps],
                 "window": {"usual": usual}, "recurrence": {"weekdays": None}, **extra}
            db.execute("INSERT INTO habits VALUES (?, 'active', ?)", (hid, json.dumps(h)))
        habit("habit-1", ["pattern-8"], "07:08")
        habit("habit-2", ["pattern-3"], "20:56")
        habit("habit-3", ["pattern-10"], "23:29")
        habit("habit-4", ["pattern-9"], "18:38", person={"phone": "c-8f3a", "name": "Ana's phone"})
        habit("habit-5", ["pattern-9"], "18:38", name="Ana is back", named_by="llm")
        db.execute("INSERT INTO occasions VALUES ('occasion-1', ?)", (json.dumps(
            {"id": "occasion-1", "weekdays": ["fri"], "usual_hours": ["20:05", "01:38"],
             "fingerprint": {"guest_phones": 6}}),))
        return db

    def test_from_the_habits_own_facts(self):
        db = self.db()
        self.assertEqual(demo.scripted_names(db), 5)
        names = {hid: (json.loads(d)["name"], json.loads(d)["named_by"]) for hid, d in db.execute("SELECT id, data FROM habits")}
        self.assertEqual(names, {"habit-1": ("Waking up", "scripted"), "habit-2": ("TV in the evening", "scripted"),
                                 "habit-3": ("Bedtime", "scripted"), "habit-4": ("Ana gets home", "scripted"),
                                 "habit-5": ("Ana is back", "llm")})  # the LLM's name is never replaced
        (o,) = [json.loads(d) for (d,) in db.execute("SELECT data FROM occasions")]
        self.assertEqual((o["name"], o["named_by"]), ("Friday night gathering", "scripted"))
        importance = {hid: json.loads(d).get("importance") for hid, d in db.execute("SELECT id, data FROM habits")}
        self.assertEqual((importance["habit-2"], importance["habit-1"]), ("low", "normal"))  # an appliance: low
        self.assertEqual(demo.scripted_names(db), 0)  # the same names again: nothing changes


class KeepTest(unittest.TestCase):
    def test_a_copy_without_the_snapshots_contents(self):
        db = sqlite3.connect(":memory:")
        db.executescript("CREATE TABLE snapshots (ts INTEGER PRIMARY KEY, home TEXT NOT NULL);"
                         "CREATE TABLE events (id INTEGER PRIMARY KEY, ts INTEGER)")
        db.execute("INSERT INTO snapshots VALUES (?, ?)", (to_ms(NOW), json.dumps({"devices": ["..."] * 100})))
        db.execute("INSERT INTO events (ts) VALUES (1)")
        import tempfile
        from pathlib import Path
        path = Path(tempfile.mkdtemp()) / "kept.sqlite3"
        demo.keep(db, path)
        kept = sqlite3.connect(path)
        self.assertEqual(kept.execute("SELECT ts, home FROM snapshots").fetchall(), [(to_ms(NOW), "{}")])
        self.assertEqual(kept.execute("SELECT count(*) FROM events").fetchone(), (1,))


if __name__ == "__main__":
    unittest.main()
