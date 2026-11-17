"""Replays a synthetic sequence built from a real get_home snapshot through the whole loop.

  uv run python -m unittest discover -s tests -t . -v
"""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from source.loop import run
from source.sources import ReplaySource
from source.store import Store
from source.triggers import CAMERA, CONTACT, DOOR, LIGHT, PLUG, SMOKE, Triggers, seed_triggers

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
            self.assertEqual(wakes, [["heartbeat"]])  # the door never wakes the LLM
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
