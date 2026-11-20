"""The phone app's API (source/phone_api.py) and its "Right now" card (source/status.py): a person's writes
through the guard, the status from Alfred's decisions, skip / yes / no / undo, overrides, and the HTTP rules.

A temporary store with hand-made decisions and events; the dry-run actuator, so nothing is ever sent.

  uv run python -m unittest tests.test_phone_api -v
"""
import asyncio
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from source import anticipate, phone_api, status, voice
from source.actions import DryRunActuator, LookOnlyActuator
from source.agent import StubAgent, guard_writes
from source.guard import LOOK_ONLY_REASON, READ_TOOL, WRITE_TOOL
from source.context import _overrides
from source.learn import to_ms
from source.model import Reading
from source.normalize import device_types, fresh_by_type
from source.store import Store

NOW = datetime(2026, 9, 21, 9, 12)
BULB, PLUG, THERMO, ROUTER, ALARM = "bulb-1", "plug-1", "thermo-1", "router-1", "home-security"


def home_state(*, ana_home: bool, target: float = 17.0, bulb_power: str = "off") -> dict:
    t = to_ms(NOW) - 20_000
    clients = [{"id": "c-8f3a", "name": "Ana's phone", "kind": "phone", "known": True}] if ana_home else []
    readings = [
        Reading(BULB, "Light", "power", bulb_power, t, "bulb"),
        Reading(BULB, "Light", "brightness", 30, t, "bulb"),
        Reading(PLUG, "Kettle", "power", "off", t, "plug"),
        Reading(THERMO, "Thermostat", "targetTemperature", target, t, "thermostat"),
        Reading(THERMO, "Thermostat", "temperatureValue", 19.7, t, "thermostat"),
        Reading(THERMO, "Thermostat", "mode", "heat", t, "thermostat"),
        Reading(ROUTER, "Router", "connectedClients", json.dumps(clients), t, "router"),
        Reading(ALARM, "Alarm", "status", "DISARMED", None, "security"),
    ]
    return {(r.device_id, r.capability): r for r in readings}


def decision(db, did: str, at: str, *, mode="do", status_="planned", action=None, reason="", ran=None, habit=None):
    d = {"id": did, "at": at, "kind": "llm", "habit_id": habit, "occasion_id": None, "action": action,
         "expectation": None, "confidence": 0.9 if mode == "do" else 0.6, "mode": mode, "status": status_, "reason": reason}
    meta = {"ran": ran} if ran else {}
    db.execute("INSERT INTO decisions (id, ts, data, key, meta) VALUES (?, ?, ?, ?, ?)",
               (did, to_ms(datetime.fromisoformat(at)), json.dumps(d), f"llm:{did}", json.dumps(meta)))
    db.commit()


LAMP_OFF = {"target": "bulb", "args": {"deviceId": BULB, "body": {"type": "power", "value": "off"}}}


class PhoneApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name), record=False)
        anticipate.init(self.store.db)
        db = self.store.db
        # Alfred switched the lamp off at 08:45: the decision ran, and its write made this change.
        decision(db, "decision-1", "2026-09-21T08:45:00", status_="done", action=LAMP_OFF, reason="The house went quiet.",
                 ran={"at": "2026-09-21T08:45:00", "why": "sent and confirmed",
                      "actions": [{"status": "sent", "verified": "confirmed", "args": LAMP_OFF["args"]}]})
        db.execute("INSERT INTO events (ts, device_id, device, capability, kind, old, new, source) VALUES (?,?,?,?,?,?,?,?)",
                   (to_ms(datetime(2026, 9, 21, 8, 45, 30)), BULB, "Light", "power", "change", '"on"', '"off"', "agent"))
        decision(db, "decision-2", "2026-09-21T18:32:00", action={"target": "bulb", "args": {"body": {"type": "brightness", "value": 60}}},
                 reason="Evenings at home.")
        decision(db, "decision-3", "2026-09-21T17:50:00", mode="suggest", reason="Ana usually gets home around 18:20.",
                 action={"target": "thermostat", "args": {"body": {"type": "targetTemperature", "value": 20}}})
        db.commit()
        self.api = self.make_api(home_state(ana_home=False))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def make_api(self, state):
        api = phone_api.PhoneApi(self.store, DryRunActuator(), StubAgent(), household="test", dry_run=True, stale_ms=3_600_000)
        api.update(to_ms(NOW), state, device_types(state), fresh_by_type(state, to_ms(NOW), 3_600_000))
        return api

    def run_(self, coro):
        return asyncio.run(coro)

    # ---- reading the home ----

    def test_home_lists_devices_with_their_controls_people_and_heating(self):
        home = self.run_(self.api.home())
        by_id = {d["id"]: d for d in home["devices"]}
        self.assertEqual([c["kind"] for c in by_id[BULB]["controls"]], ["switch", "level", "tone"])
        self.assertEqual([c["kind"] for c in by_id[THERMO]["controls"]], ["target", "choice"])
        self.assertEqual(by_id[THERMO]["controls"][0]["min"], 16.0)
        self.assertEqual(by_id[ALARM]["controls"], [])
        self.assertNotIn("connectedClients", by_id[ROUTER]["values"])
        self.assertEqual(home["people"], {"home": [], "guests": 0})
        self.assertEqual(home["climate"]["targetC"], 17.0)
        self.assertTrue(home["dryRun"])

    def test_a_voice_only_server_says_it_has_no_house(self):
        api = phone_api.PhoneApi(None, None, None, household="voice only", dry_run=True, stale_ms=1)
        for call in (api.home(), api.status(), api.undo("decision-1"), api.chat("hi", None)):
            with self.assertRaises(phone_api.ApiError) as e:
                self.run_(call)
            self.assertEqual(e.exception.status, 503)
            self.assertIn("npm run demo", str(e.exception))

    def test_before_the_first_poll_the_api_says_to_wait_or_why_it_cant_read_the_house(self):
        api = phone_api.PhoneApi(self.store, None, StubAgent(), household="t", dry_run=True, stale_ms=1)
        with self.assertRaises(phone_api.ApiError) as e:
            self.run_(api.home())
        self.assertEqual(e.exception.status, 503)
        api.poll_failed("not signed in to the kit")
        with self.assertRaises(phone_api.ApiError) as e:
            self.run_(api.status())
        self.assertEqual(str(e.exception), "Alfred can't read the house: not signed in to the kit")
        state = home_state(ana_home=False)
        api.update(to_ms(NOW), state, device_types(state), fresh_by_type(state, to_ms(NOW), 3_600_000))
        self.assertIsNone(api.last_error)

    # ---- a person's writes ----

    def test_a_person_may_switch_a_plug_on_and_the_change_is_labelled_theirs(self):
        r = self.run_(self.api.set_control(PLUG, {"type": "power", "value": "on"}))
        self.assertEqual(r["status"], "sent")
        self.assertEqual(r["verified"], "unconfirmed")  # a dry run never confirms
        self.assertEqual([w[1:] for w in self.api.take_writes()], [(PLUG, "power", "on", "app")])
        self.assertEqual(self.api.take_writes(), [])

    def test_look_only_sends_nothing_and_says_so(self):
        sent = []

        class Spy:
            async def call(self, tool, args):
                sent.append(tool)
                return "{}"
        api = phone_api.PhoneApi(self.store, Spy(), StubAgent(), household="t", dry_run=False, stale_ms=3_600_000,
                                 look_only=True)
        state = home_state(ana_home=False)
        api.update(to_ms(NOW), state, device_types(state), fresh_by_type(state, to_ms(NOW), 3_600_000))
        r = self.run_(api.set_control(BULB, {"type": "power", "value": "on"}))
        self.assertEqual((r["status"], r["reason"]), ("blocked", LOOK_ONLY_REASON))
        self.assertEqual((sent, api.take_writes()), ([], []))
        home = self.run_(api.home())
        self.assertEqual((home["lookOnly"], home["dryRun"], home["live"]), (True, True, False))

    def test_look_only_refuses_alfreds_own_writes_and_his_tool_never_sends(self):
        with self.assertRaises(RuntimeError):
            self.run_(LookOnlyActuator().call(WRITE_TOOL, {"deviceId": BULB, "body": {"type": "power", "value": "on"}}))
        self.assertEqual(self.run_(LookOnlyActuator().call(READ_TOOL, {"deviceId": BULB})), "{}")

        class Tool:  # stands in for the MCP's write tool: must never be called
            name, description, args_schema = WRITE_TOOL, "write", None

            async def ainvoke(self, *a, **k):
                raise AssertionError("look-only sent a write")
        actions = []
        wrapped = guard_writes([Tool()], {BULB: "bulb"}, actions, allow_plug_on=False, readback_tries=1,
                               readback_delay_s=0, send=False)
        said = self.run_(wrapped[0].coroutine(deviceId=BULB, body={"type": "power", "value": "on"}))
        self.assertEqual((said, actions[0]["status"]), (LOOK_ONLY_REASON, "blocked"))

    def test_the_alarm_is_never_written(self):
        r = self.run_(self.api.set_control(ALARM, {"type": "power", "value": "on"}))
        self.assertEqual(r["status"], "blocked")
        self.assertIn("can't be changed", r["reason"])

    def test_the_thermostat_keeps_its_limits_for_a_person_too(self):
        r = self.run_(self.api.set_control(THERMO, {"type": "targetTemperature", "value": 19}))
        self.assertEqual(r["status"], "blocked")
        self.assertIn("no one is home", r["reason"])
        home = self.make_api(home_state(ana_home=True))
        self.assertEqual(self.run_(home.set_control(THERMO, {"type": "targetTemperature", "value": 19}))["status"], "sent")

    def test_changing_back_what_alfred_did_is_counted_once(self):
        r = self.run_(self.api.set_control(BULB, {"type": "power", "value": "on"}))
        self.assertIn("note", r)
        self.assertIn("08:45", r["note"])
        rows = self.store.db.execute("SELECT kind, decision_id, alfred_value, person_value FROM overrides").fetchall()
        self.assertEqual(rows, [("manual", "decision-1", '"off"', '"on"')])
        self.assertNotIn("note", self.run_(self.api.set_control(BULB, {"type": "power", "value": "on"})))
        self.assertFalse(self.run_(self.api.status())["byAlfred"])

    # ---- the "Right now" card ----

    def test_status_shows_alfreds_change_what_is_planned_and_what_needs_a_yes(self):
        s = self.run_(self.api.status())
        self.assertEqual(s["title"], "Nobody home")
        self.assertEqual(s["changes"], [{"deviceId": BULB, "device": "Light", "capability": "power", "from": "on", "to": "off"}])
        self.assertEqual(s["decisionId"], "decision-1")
        self.assertEqual(s["why"], "The house went quiet.")
        self.assertTrue(s["byAlfred"])
        self.assertEqual([p["id"] for p in s["planned"]], ["decision-2"])
        self.assertEqual(s["planned"][0]["does"], "Lamp at 60%")
        self.assertEqual([p["id"] for p in s["suggestions"]], ["decision-3"])
        self.assertEqual(s["suggestions"][0]["does"], "Heating to 20 °C")

    def test_status_has_alerts(self):
        self.assertEqual(self.run_(self.api.status())["alerts"], [])

    def test_a_home_with_nothing_learned_still_has_a_status(self):
        self.store.db.execute("DELETE FROM decisions")
        state = {k: r for k, r in home_state(ana_home=False).items() if r.device_type != "router"}
        s = status.status_now(self.store.db, state, NOW)
        self.assertEqual((s["title"], s["since"], s["changes"], s["decisionId"]), ("Home", None, [], None))

    # ---- answering decisions ----

    def test_skip_then_put_it_back(self):
        item = self.run_(self.api.answer("decision-2", "skip"))
        self.assertEqual((item["status"], item["answer"]), ("skipped", "skip"))
        self.assertEqual(self.store.db.execute("SELECT kind FROM overrides").fetchall(), [("skip",)])
        self.assertEqual(self.run_(self.api.status())["planned"][0]["status"], "skipped")  # still shown, to put back
        item = self.run_(self.api.answer("decision-2", "unskip"))
        self.assertEqual((item["status"], item["answer"]), ("planned", None))

    def test_yes_makes_a_suggestion_a_do_and_replanning_keeps_it(self):
        item = self.run_(self.api.answer("decision-3", "yes"))
        self.assertEqual(item["mode"], "do")
        d, m = anticipate.decision_row(self.store.db, "decision-3")
        replanned = {**d, "mode": "suggest", "confidence": 0.6}
        anticipate._upsert(self.store.db, "llm:decision-3", replanned, {})
        d, m = anticipate.decision_row(self.store.db, "decision-3")
        self.assertEqual((d["mode"], m["person"]["answer"]), ("do", "yes"))

    def test_no_declines_and_a_wrong_answer_is_refused(self):
        self.assertEqual(self.run_(self.api.answer("decision-3", "no"))["status"], "skipped")
        with self.assertRaises(phone_api.ApiError) as e:
            self.run_(self.api.answer("decision-2", "yes"))  # a "do" decision isn't a suggestion
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(phone_api.ApiError) as e:
            self.run_(self.api.answer("decision-99", "skip"))
        self.assertEqual(e.exception.status, 404)

    def test_undo_writes_the_old_value_back_as_the_person(self):
        results = self.run_(self.api.undo("decision-1"))
        self.assertEqual([(r["status"], r["deviceId"]) for r in results], [("sent", BULB)])
        self.assertEqual([w[1:] for w in self.api.take_writes()], [(BULB, "power", "on", "app")])
        self.assertEqual(self.store.db.execute("SELECT kind FROM overrides").fetchall(), [("undo",)])
        s = self.run_(self.api.status())
        self.assertIsNone(s["decisionId"])  # undone: no longer Alfred's change
        with self.assertRaises(phone_api.ApiError):
            self.run_(self.api.undo("decision-1"))

    def test_look_only_has_nothing_to_undo_and_records_no_override(self):
        api = phone_api.PhoneApi(self.store, DryRunActuator(), StubAgent(), household="t", dry_run=False, stale_ms=3_600_000,
                                 look_only=True)
        state = home_state(ana_home=False)
        api.update(to_ms(NOW), state, device_types(state), fresh_by_type(state, to_ms(NOW), 3_600_000))
        with self.assertRaises(phone_api.ApiError) as e:
            self.run_(api.undo("decision-1"))
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM overrides").fetchone()[0], 0)

    def test_what_look_only_didnt_send_and_how_long_the_home_has_been_watched(self):
        decision(self.store.db, "decision-9", "2026-09-21T08:30:00", status_="failed", action=LAMP_OFF, reason="Quiet.",
                 ran={"at": "2026-09-21T08:30:00", "why": "failed: look-only mode, nothing is sent (start npm run home:live to switch things)",
                      "actions": []})
        decision(self.store.db, "decision-8", "2026-09-21T08:20:00", status_="failed", action=LAMP_OFF,
                 ran={"at": "2026-09-21T08:20:00", "why": "failed: the lamp didn't answer", "actions": []})
        s = self.run_(self.api.status())
        self.assertEqual([p["id"] for p in s["notSent"]], ["decision-9"])  # a real failure isn't a "would have"
        self.assertEqual(s["learning"], {"daysWatched": 0})  # no habits yet, and no snapshots in this store

    def test_the_agent_sees_how_often_it_was_overruled(self):
        self.run_(self.api.answer("decision-2", "skip"))
        self.assertEqual(_overrides(self.store.db, to_ms(NOW))[0]["kind"], "skip")

    def test_chat_asks_the_agent_as_a_person_and_stores_the_run(self):
        r = self.run_(self.api.chat("Is anyone home?", "en"))
        self.assertEqual(r["text"], "stub")
        reasons = json.loads(self.store.db.execute("SELECT reasons FROM agent_runs").fetchone()[0])
        self.assertEqual(reasons[0]["trigger_id"], "person")
        self.assertIn("(in en)", reasons[0]["reason"])


class HttpTest(unittest.TestCase):
    """The HTTP side: localhost only, JSON errors, voice answering 501 without the model."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

        async def start():  # as in the loop's process: the store is opened on the loop's own thread
            self.store = Store(Path(self.tmp.name), record=False)
            anticipate.init(self.store.db)
            self.api = phone_api.PhoneApi(self.store, None, StubAgent(), household="t", dry_run=True, stale_ms=3_600_000)
            state = home_state(ana_home=True)
            self.api.update(to_ms(NOW), state, device_types(state), fresh_by_type(state, to_ms(NOW), 3_600_000))
            return self.api.serve(0)
        self.port = asyncio.run_coroutine_threadsafe(start(), self.loop).result(5).server_address[1]

    def tearDown(self):
        self.api.close()

        async def stop():
            self.store.close()
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()
        self.tmp.cleanup()

    def request(self, path, body=None, *, origin=None, method=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method or ("POST" if data is not None else "GET"))
        if origin:
            req.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_home_and_status_answer_on_localhost(self):
        code, home = self.request("/api/home")
        self.assertEqual(code, 200)
        self.assertEqual(home["people"]["home"][0]["name"], "Ana's phone")
        self.assertEqual(self.request("/api/status")[0], 200)

    def test_other_sites_are_refused(self):
        self.assertEqual(self.request("/api/home", origin="https://example.com")[0], 403)
        self.assertEqual(self.request("/api/home", origin=f"http://127.0.0.1:{self.port}")[0], 200)

    def test_bad_requests_get_a_plain_reason(self):
        code, body = self.request(f"/api/devices/{PLUG}", {"nobody": 1})
        self.assertEqual((code, body["error"]), (400, 'send {"body": {"type": ..., "value": ...}}'))
        self.assertEqual(self.request("/api/nothing")[0], 404)
        self.assertEqual(self.request("/api/plan/decision-1/answer", {"yes": "maybe"})[0], 400)
        self.assertEqual(self.request(f"/api/devices/{PLUG}", b"not json")[0], 400)

    def test_a_write_over_http(self):
        code, r = self.request(f"/api/devices/{PLUG}", {"body": {"type": "power", "value": "on"}})
        self.assertEqual((code, r["status"]), (200, "sent"))

    @unittest.skipIf(voice.available(), "the voice model is installed here")
    def test_voice_says_it_is_not_installed(self):
        self.assertEqual(self.request("/api/voice")[0], 501)
        self.assertEqual(self.request("/api/voice", b"RIFF")[0], 501)
