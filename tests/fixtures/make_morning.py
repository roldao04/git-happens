"""Generate morning.jsonl: a synthetic 08:00–08:30 morning, one poll a minute, built from the real
get_home snapshot. Replay it with:

  uv run python -m source --replay tests/fixtures/morning.jsonl

Story: 08:09 front door opens, 08:10 closes · 08:21–08:24 kettle (2140 W) · 08:25 lamp to
warm 60 % · temperature creeps up · 08:28 the camera stops reporting.
"""
import copy
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
CONTACT = "d4d883d1-c83b-4411-b790-f63289402c1d"
PLUG = "a291277c-8ba5-480a-b3a3-35b5f49bb459"
LIGHT = "90654832-7bca-47dd-89f3-d6e0b5f0a9ee"
TEMP = "eaffed37-e92f-45f5-867c-3852d229f8ad"
CAMERA = "4f4f2408-ffc2-4543-a42b-3757133a6df4"


def _set(home, dev_id, cap, value):
    for d in home["devices"]:
        if d["id"] == dev_id:
            for c in d["capabilities"]:
                if c["type"] == cap:
                    c["value"] = value


def main() -> None:
    home = json.loads((HERE / "get_home.json").read_text())
    home["security"]["isAlarmed"] = False
    _set(home, CONTACT, "sensorState", "normal")
    _set(home, LIGHT, "power", "off")
    _set(home, TEMP, "temperatureValue", 17.8)
    start = int(datetime(2026, 11, 18, 8, 0).timestamp() * 1000)
    frames = []
    for minute in range(31):
        ts = start + minute * 60_000
        for d in home["devices"]:
            if not (d["id"] == CAMERA and minute >= 28):
                d["lastCommunicationTime"] = ts - 5_000
        if minute == 9:
            _set(home, CONTACT, "sensorState", "alarm")
        if minute == 10:
            _set(home, CONTACT, "sensorState", "normal")
        if minute == 21:
            _set(home, PLUG, "instantPower", 2140)
            _set(home, PLUG, "instantCurrent", 9300)
        if minute == 22:
            _set(home, PLUG, "instantPower", 2135)  # small wobble: stored, no wake
        if minute == 24:
            _set(home, PLUG, "instantPower", 0)
            _set(home, PLUG, "instantCurrent", 0)
            _set(home, PLUG, "todayConsumption", 0.11)
        if minute == 25:
            _set(home, LIGHT, "power", "on")
            _set(home, LIGHT, "workMode", "white")
            _set(home, LIGHT, "brightness", 60)
        if minute in (15, 26):
            _set(home, TEMP, "temperatureValue", 18.1 if minute == 15 else 18.4)
        frames.append({"ts": ts, "home": copy.deepcopy(home)})
    # 28 min later the camera hasn't reported: use a short stale window when replaying to see it.
    (HERE / "morning.jsonl").write_text("".join(json.dumps(f) + "\n" for f in frames))
    print(f"wrote {len(frames)} frames to {HERE / 'morning.jsonl'}")


if __name__ == "__main__":
    main()
