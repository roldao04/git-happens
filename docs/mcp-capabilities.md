# Connected Home MCP: what we can use

What the `connected-home` MCP lets us **see**, **change**, and what it **can't** do.
"Now" values were read live on 17 Nov 2026 at 11:09 UTC.

> **Kit status:** live since 17 Nov ~10:00 UTC, except the **smoke sensor** (last report 14 Nov 03:03 UTC).

## Connecting
- Server: `https://cognitive-home.futurelabsinnovations.com` (HTTP), configured in `.mcp.json` at the repo root
- `.mcp.json` holds our API key, so it's **gitignored**. To set it up, copy `.mcp.example.json` to `.mcp.json`, then add the key and the absolute path to the helper
- Login once with `python3 tools/connected_home_auth.py login`. After that, tokens refresh automatically
- The poller (`source/sources.py`) uses the same HTTP connection and sign-in as Claude Code: it asks `tools/connected_home_auth.py` for a fresh token on every request. The console (`console/server.mjs`) instead starts `mcp-remote@0.16.6` over stdio, with its own sign-in in `~/.mcp-auth/`. The console reads `command`/`args` from `.mcp.json`, so for it `.mcp.json` must use that stdio form with the same args as `sources.py`. Only one sign-in can run at a time (callback port 5435)

## Tools (18)
| Tool | What it does | Type |
|---|---|---|
| `get_home` | Home, alarm state and all devices with values (best single call) | see |
| `get_home_detail_report` | Same output as `get_home` | see |
| `get_home_devices` | All devices with values | see |
| `get_home_status_brief` | Alarm state + device list with last-seen time (lightest call) | see |
| `get_home_rooms` | Rooms (none set up) | see |
| `get_device_details` | One device, plus when it was paired | see |
| `get_device_recommendations` | Raw device data for the AI to turn into advice | see |
| `get_device_timers` | A device's timers (⚠ errors in Claude Code) | see |
| `get_home_security_status` | Alarm mode + whether it has gone off | see |
| `get_home_security_alarm_history` | Past alarms (`size`, `page`, optional dates in ms) | see |
| `get_home_scenarios` | All automations (none exist) | see |
| `get_scenario` | One automation | see |
| `update_device_capabilities` | Light/plug only: `power`, `brightness`, `color_rgb`, `temperature` (by ID or name). Any other capability is rejected | change |
| `patch_home_security_status` | Arm Away / Stay / disarm; entry and exit delays | change |
| `post_timer_device` | Schedule an action on a device | change |
| `post_new_scenario` | Create an automation | change |
| `patch_scenario_activation` | Turn an automation on/off | change |
| `post_execute_scenario` | Run an automation now | change |

## Devices

**Light (RGB bulb)**
- Change: power, brightness (1–100), RGB colour, white temperature (a 0–100 scale, *not* degrees)
- See: those values, plus mode (colour / white / music / scene). The mode can't be changed through the MCP
- Now: on, 100%, white (255,255,255), white temperature 100, colour mode

**Plug**
- Change: power on/off (switches whatever is plugged in)
- See: power (W), voltage, current, energy today and yesterday (nothing older)
- Now: on, 225 V, 0 W (nothing drawing power), 0.004 kWh today

**Camera**
- Change: nothing through the MCP. Its settings are app-only: motion/sound alerts and sensitivity, night vision, SD recording and mode, LED, image flip, on-screen time, format SD, pan/tilt, streaming. Setting `motionAlarmSwitch` was rejected (17 Nov): `validating /properties/body: oneOf: did not validate against any of [4 anonymous schemas]`
- See: those settings, recording and SD card status, motion/sound detected. **No video, snapshots or audio through the MCP**; live video comes from the camera's local **RTSP stream** instead
- Now: recording on, SD storage reads `0|0|0`. On 17 Nov motion and sound detection were off (`motionAlarmSwitch: false`), so `motionDetected` stayed empty; whether it works through the MCP now is **unconfirmed**. The `camera-sound` / `camera-motion` triggers ([`event-loop.md`](event-loop.md) §5) fire only if it does

**Security hub**
- Change: nothing through the MCP (restart, factory reset, cellular backup and network mode are app-only)
- See: battery and charging, connection type, Wi-Fi and cellular signal, siren and tamper state. The three sensors below connect through it
- Now: on Ethernet, Wi-Fi good, battery 20% (charging), cellular backup not working

**Smoke sensor**
- See: smoke yes/no, tamper, battery (low/medium/high), signal
- Now: ⚠ **not reporting**: last report 14 Nov 03:03 UTC (no smoke, battery high, signal low). Not re-paired yet, so smoke can't be detected through the MCP

**Contact sensor (door/window)**
- See: open/closed (`sensorState`: `normal` = closed, `alarm` = open), tamper, battery (low/medium/high), signal
- Now: live, battery high, signal high. Opening the door changed `sensorState` to `alarm` within ~10 s

**Temperature sensor**
- See: temperature, humidity, battery (current values only, no history)
- Now: 22.9 °C, 73.2 % humidity, battery 100%

## Alarm
- Change: Disarm, Stay (people at home) or Away, plus entry/exit delays (now Away 30 s, Stay 0 s). Confirm with the user first
- See: current mode, whether the alarm has gone off, alarm history
- Now: disarmed. One smoke alarm from 5 Nov (almost certainly a test) is still flagged (`isAlarmed: true`, `alarmType: FIRE_SMOKE`), and no tool can clear it. So trigger on the **change** of `isAlarmed`, never on its value

## Scenarios (automations)
- Started by a device state (e.g. door opens) or a schedule (e.g. daily at 12:00)
- Run actions when they start, plus optional actions when they end
- Run on the server, so they keep working when our app is closed
- Can be created, switched on/off, and run now. **Can't be edited or deleted** through the MCP
- Now: none exist

## Timers
- Schedule an action on a device. The format is undocumented, reading timers errors in Claude Code, and timers can't be deleted

## Not available / gotchas
- No live events: we have to **poll** (ask repeatedly) to notice changes (design: [`event-loop.md`](event-loop.md))
- Camera `motionDetected` / `soundDetected` read as empty strings; they look short-lived, so polling may miss them
- `lastCommunicationTime` differs per device, and quiet sensors (e.g. the contact) can go 15+ min without reporting: check it before trusting a value, in code rather than in the LLM prompt
- Device IDs change when a device is re-paired: find devices by `type`, never by a hard-coded ID
- No camera video, snapshots or audio through the MCP (video: the local RTSP stream, see Camera), and no speaker
- No history apart from alarm history and the plug's today/yesterday energy
- No rooms (and no way to create them); no energy tariff set
- In `get_home_status_brief`, `alarmArmed` actually means "the alarm went off", not "armed"
- Errors come back as `{"message":"SH_10500: <id>","error":"tool_error"}`
- Cloudflare blocked a plain Python client (403); Claude Code's client works. Test your own client early

## Device IDs
As of 17 Nov 11:09 UTC. They change on re-pairing, so don't hard-code them (old contact `d4d883d1-…` and temperature `eaffed37-…` are gone).
```
Home                dcbc3f70-f895-4698-8e68-2a3ee0383904
Security hub        c5c8e959-2a0b-4c1b-87f4-c285afa50e00
Smoke sensor        e4566929-1477-4023-8095-af904a0befe3   (stale since 14 Nov)
Contact sensor      276b9ad8-89ce-491a-a177-fe42358bee1c
Temperature sensor  bb553b28-fdb1-4f77-b7e9-b0972d50ab15
Plug                a291277c-8ba5-480a-b3a3-35b5f49bb459
Light               90654832-7bca-47dd-89f3-d6e0b5f0a9ee
Camera              4f4f2408-ffc2-4543-a42b-3757133a6df4
```
