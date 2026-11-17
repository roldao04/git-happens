"""Replays a synthetic sequence built from a real get_home snapshot through the whole loop.

  uv run python -m unittest discover -s tests -t . -v
"""
import asyncio
import copy
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from source.loop import run
from source.sources import ReplaySource
from source.store import Store
from source.triggers import Triggers, seed_triggers

# ids in the 14 Nov fixture (get_home.json); triggers find devices by type, not by these
SMOKE = "e4566929-1477-4023-8095-af904a0befe3"
CONTACT = "d4d883d1-c83b-4411-b790-f63289402c1d"
PLUG = "a291277c-8ba5-480a-b3a3-35b5f49bb459"
LIGHT = "90654832-7bca-47dd-89f3-d6e0b5f0a9ee"
CAMERA = "4f4f2408-ffc2-4543-a42b-3757133a6df4"
DOOR = "13dc0bee-f750-4cb1-9cac-6897fe0a4bd4"  # a second contact sensor added by some tests
LIVE = json.loads((Path(__file__).parent / "fixtures" / "get_home_live.json").read_text())
LIVE_TS = 1794913759185  # 17 Nov 11:09:19; the smoke sensor last reported 14 Nov
LIVE_CONTACT = "276b9ad8-89ce-491a-a177-fe42358bee1c"

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "get_home.json").read_text())
T0 = 1794625400000  # just after the fixture's newest lastCommunicationTime
STEP = 20_000


def _set(home, dev_id, cap, value, comm=None):
    for d in home["devices"]:
        if d["id"] == dev_id:
            for c in d["capabilities"]:
                if c["type"] == cap:
                    c["value"] = value
            if comm:
                d["lastCommunicationTime"] = comm


def _fresh(home, ts):
    for d in home["devices"]:
        d["lastCommunicationTime"] = ts - 1000


class RecordingAgent:
    def __init__(self):
        self.runs = []

    async def run(self, ctx):
        self.runs.append(ctx)
        return {"notes": "", "trigger_updates": [], "actions": []}


class LoopTest(unittest.TestCase):
    def test_morning_sequence(self):
        frames = []
        home = copy.deepcopy(FIXTURE)
        _set(home, CONTACT, "sensorState", "normal")
        for i in range(7):
            ts = T0 + i * STEP
            _fresh(home, ts)
            if i == 2:
                _set(home, CONTACT, "sensorState", "alarm")  # door opens
            if i == 3:
                _set(home, PLUG, "instantPower", 2140)  # kettle on
            if i == 4:
                _set(home, PLUG, "instantPower", 2150)  # small change: no wake
            if i == 6:
                _set(home, SMOKE, "sensorState", "alarm")
            frames.append({"ts": ts, "home": copy.deepcopy(home)})

        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "rec.jsonl"
            rec.write_text("\n".join(json.dumps(f) for f in frames))
            store = Store(Path(tmp) / "data", record=False)
            agent = RecordingAgent()
            asyncio.run(run(ReplaySource(rec), store, Triggers(seed_triggers(15)), agent,
                            poll_seconds=20, stale_minutes=60))

            wakes = [[r["trigger_id"] for r in ctx["reasons"]] for ctx in agent.runs]
            self.assertEqual(wakes, [["heartbeat"], ["contact"], ["plug-power"], ["smoke"]])

            events = store.events_since(0)
            described = [e.describe() for e in events]
            self.assertIn("Contact sensor.sensorState: 'normal' → 'alarm'", described)
            self.assertIn("Plug.instantPower: 2140 → 2150", described)  # stored even without a wake
            # the plug change below the threshold still reaches the agent on its next run
            self.assertIn("Plug.instantPower: 2140 → 2150",
                          " ".join(agent.runs[-1]["events_since_last_run"]))
            store.close()

    def test_camera_sound_wakes_agent(self):
        frames = []
        home = copy.deepcopy(FIXTURE)
        for i, sound in enumerate(["", "", "1", ""]):  # quiet, quiet, talking, quiet again
            ts = T0 + i * STEP
            _fresh(home, ts)
            _set(home, CAMERA, "soundDetected", sound)
            frames.append({"ts": ts, "home": copy.deepcopy(home)})
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "rec.jsonl"
            rec.write_text("\n".join(json.dumps(f) for f in frames))
            store = Store(Path(tmp) / "data", record=False)
            agent = RecordingAgent()
            asyncio.run(run(ReplaySource(rec), store, Triggers(seed_triggers(15)), agent,
                            poll_seconds=20, stale_minutes=60))
            wakes = [[r["trigger_id"] for r in ctx["reasons"]] for ctx in agent.runs]
            self.assertEqual(wakes, [["heartbeat"], ["camera-sound"]])  # back to "" doesn't wake
            store.close()

    def test_door_hard_rules_run_without_llm(self):
        class RecordingActuator:
            def __init__(self):
                self.calls = []

            async def call(self, tool, args):
                self.calls.append((tool, args["deviceId"], args["body"]))
                return "{}"

        frames = []
        home = copy.deepcopy(FIXTURE)
        home["devices"].append({"id": DOOR, "name": "Door", "type": "contactSensor",
                                "capabilities": [{"type": "sensorState", "value": "normal"}]})
        for i, state in enumerate(["normal", "alarm", "normal", "alarm"]):  # open, close, open again
            ts = T0 + i * STEP
            _fresh(home, ts)
            _set(home, DOOR, "sensorState", state)
            frames.append({"ts": ts, "home": copy.deepcopy(home)})
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "rec.jsonl"
            rec.write_text("\n".join(json.dumps(f) for f in frames))
            store = Store(Path(tmp) / "data", record=False)
            agent, actuator = RecordingAgent(), RecordingActuator()
            asyncio.run(run(ReplaySource(rec), store, Triggers(seed_triggers(15)), agent,
                            poll_seconds=20, stale_minutes=60, actuator=actuator))

            wakes = [[r["trigger_id"] for r in ctx["reasons"]] for ctx in agent.runs]
            door_rules = {"door-open", "door-closed"}
            self.assertFalse(any(door_rules & set(w) for w in wakes))  # door rules never wake the LLM
            self.assertEqual(wakes, [["heartbeat"], ["contact"]])  # the type-based contact trigger does
            colours = [(b["redValue"], b["greenValue"], b["blueValue"]) for _, dev, b in actuator.calls]
            self.assertEqual(colours, [(0, 0, 255), (255, 0, 0), (0, 0, 255)])  # no cooldown
            self.assertTrue(all(dev == LIGHT for _, dev, _ in actuator.calls))
            self.assertEqual([tid for _, tid, _ in store.rule_runs()], ["door-open", "door-closed", "door-open"])
            store.close()

    def test_rule_actions_limited_to_allowed_tools(self):
        from source.actions import DryRunActuator, run_actions
        out = asyncio.run(run_actions(DryRunActuator(), "x", [{"tool": "patch_home_security_status", "args": {}}]))
        self.assertTrue(out[0]["error"])

    def test_camera_motion_wakes_agent(self):
        frames = []
        home = copy.deepcopy(FIXTURE)
        for i, motion in enumerate(["", "1", ""]):
            ts = T0 + i * STEP
            _fresh(home, ts)
            _set(home, CAMERA, "motionDetected", motion)
            frames.append({"ts": ts, "home": copy.deepcopy(home)})
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "rec.jsonl"
            rec.write_text("\n".join(json.dumps(f) for f in frames))
            store = Store(Path(tmp) / "data", record=False)
            agent = RecordingAgent()
            asyncio.run(run(ReplaySource(rec), store, Triggers(seed_triggers(15)), agent,
                            poll_seconds=20, stale_minutes=60))
            wakes = [[r["trigger_id"] for r in ctx["reasons"]] for ctx in agent.runs]
            self.assertEqual(wakes, [["heartbeat"], ["camera-motion"]])
            store.close()

    def _replay(self, frames, agent=None, actuator=None, stale_minutes=60):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rec = Path(tmp.name) / "rec.jsonl"
        rec.write_text("\n".join(json.dumps(f) for f in frames))
        store = Store(Path(tmp.name) / "data", record=False)
        self.addCleanup(store.close)
        asyncio.run(run(ReplaySource(rec), store, Triggers(seed_triggers(15)), agent or RecordingAgent(),
                        poll_seconds=20, stale_minutes=stale_minutes, actuator=actuator))
        return store

    def test_contact_trigger_survives_repair(self):
        home = copy.deepcopy(LIVE)
        _set(home, LIVE_CONTACT, "sensorState", "normal")
        frames = []
        for i in range(3):
            ts = LIVE_TS + i * STEP
            for d in home["devices"]:
                if d["type"] != "smokeSensor":
                    d["lastCommunicationTime"] = ts - 1000
            if i == 2:  # re-paired: same sensor, new id, and the door opens
                contact = next(d for d in home["devices"] if d["type"] == "contactSensor")
                contact["id"] = "repaired-contact"
                _set(home, "repaired-contact", "sensorState", "alarm")
            frames.append({"ts": ts, "home": copy.deepcopy(home)})
        agent = RecordingAgent()
        self._replay(frames, agent)
        wakes = [r["trigger_id"] for ctx in agent.runs for r in ctx["reasons"]]
        self.assertIn("contact", wakes)

    def test_smoke_blind_warned_once(self):
        frames = []
        for i in range(3):
            home = copy.deepcopy(LIVE)
            for d in home["devices"]:
                if d["type"] != "smokeSensor":
                    d["lastCommunicationTime"] = LIVE_TS + i * STEP - 1000
            frames.append({"ts": LIVE_TS + i * STEP, "home": home})
        with self.assertLogs("source.loop", level="WARNING") as logs:
            self._replay(frames)
        self.assertEqual(sum("smokeSensor" in line for line in logs.output), 1)

    def test_agent_told_which_rules_ran(self):
        home = copy.deepcopy(FIXTURE)
        home["devices"].append({"id": DOOR, "name": "Door", "type": "contactSensor",
                                "capabilities": [{"type": "sensorState", "value": "normal"}]})
        frames = []
        for i, state in enumerate(["normal", "alarm"]):
            ts = T0 + i * STEP
            _fresh(home, ts)
            _set(home, DOOR, "sensorState", state)
            frames.append({"ts": ts, "home": copy.deepcopy(home)})
        agent = RecordingAgent()
        self._replay(frames, agent=agent)
        ctx = next(c for c in agent.runs if any(r["trigger_id"] == "contact" for r in c["reasons"]))
        ran = ctx["rules_just_ran"]
        self.assertEqual([r["trigger_id"] for r in ran], ["door-open"])
        a = ran[0]["actions"][0]
        self.assertEqual((a["deviceId"], a["body"]["blueValue"]), (LIGHT, 255))
        self.assertIn("status", a)

    def test_rule_action_crash_does_not_stop_loop(self):
        home = copy.deepcopy(FIXTURE)
        home["devices"].append({"id": DOOR, "name": "Door", "type": "contactSensor",
                                "capabilities": [{"type": "sensorState", "value": "normal"}]})
        frames = []
        for i, state in enumerate(["normal", "alarm", "normal"]):
            ts = T0 + i * STEP
            _fresh(home, ts)
            _set(home, DOOR, "sensorState", state)
            frames.append({"ts": ts, "home": copy.deepcopy(home)})
        with mock.patch("source.loop.run_actions", side_effect=RuntimeError("boom")):
            with self.assertLogs("source.loop", "INFO") as logs:
                self._replay(frames)
        self.assertTrue(any(m.startswith("INFO:source.loop:tick 3 ") for m in logs.output))
        self.assertTrue(any(m.startswith("ERROR:") and "door-open" in m for m in logs.output))

    def test_rule_skipped_when_target_stale(self):
        class RecordingActuator:
            def __init__(self):
                self.writes = []

            async def call(self, tool, args):
                if tool == "update_device_capabilities":
                    self.writes.append(args)
                return "{}"

        home = copy.deepcopy(FIXTURE)
        home["devices"].append({"id": DOOR, "name": "Door", "type": "contactSensor",
                                "capabilities": [{"type": "sensorState", "value": "normal"}]})
        frames = []
        for i, state in enumerate(["normal", "alarm"]):
            ts = T0 + i * STEP
            _fresh(home, ts)
            _set(home, LIGHT, "power", "on", comm=ts - 2 * 3600_000)  # lamp silent for 2 h
            _set(home, DOOR, "sensorState", state)
            frames.append({"ts": ts, "home": copy.deepcopy(home)})
        actuator = RecordingActuator()
        store = self._replay(frames, actuator=actuator)
        self.assertEqual(actuator.writes, [])
        runs = store.rule_runs()
        self.assertEqual([tid for _, tid, _ in runs], ["door-open"])
        self.assertEqual(runs[0][2][0]["status"], "skipped")

    def test_stale_device_reported_once(self):
        home = copy.deepcopy(FIXTURE)  # the plug is ~7 h behind the others
        frames = [{"ts": T0 + i * STEP, "home": home} for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "rec.jsonl"
            rec.write_text("\n".join(json.dumps(f) for f in frames))
            store = Store(Path(tmp) / "data", record=False)
            asyncio.run(run(ReplaySource(rec), store, Triggers(seed_triggers(15)), RecordingAgent(),
                            poll_seconds=20, stale_minutes=60))
            stale = [e.device for e in store.events_since(0) if e.kind == "stale"]
            self.assertIn("Plug", stale)
            self.assertEqual(len(stale), len(set(stale)))
            store.close()

    def test_morning_replay_and_logs(self):
        """The demo recording: no stale flapping, camera goes silent once, logs are written."""
        import logging
        from source.logs import setup_logging

        rec = Path(__file__).parent / "fixtures" / "morning.jsonl"
        with tempfile.TemporaryDirectory() as tmp:
            setup_logging(Path(tmp) / "logs", console=False)
            try:
                store = Store(Path(tmp) / "data", record=False)
                agent = RecordingAgent()
                asyncio.run(run(ReplaySource(rec), store, Triggers(seed_triggers(15)), agent,
                                poll_seconds=60, stale_minutes=1))
            finally:
                for h in logging.getLogger().handlers:
                    h.close()
                logging.getLogger().handlers.clear()

            silent = [(e.device, e.kind) for e in store.events_since(0) if e.kind in ("stale", "returned")]
            self.assertEqual(silent, [("Camera", "stale")])
            wakes = [r["trigger_id"] for ctx in agent.runs for r in ctx["reasons"]]
            self.assertEqual(wakes, ["heartbeat", "contact", "heartbeat", "plug-power",
                                     "plug-power", "device-silent", "heartbeat"])
            store.close()

            records = [json.loads(line) for line in (Path(tmp) / "logs" / "loop.jsonl").read_text().splitlines()]
            ticks = [r for r in records if r.get("data", {}).get("tick")]
            self.assertEqual(len(ticks), 31)
            self.assertTrue((Path(tmp) / "logs" / "loop.log").read_text())


if __name__ == "__main__":
    unittest.main()
