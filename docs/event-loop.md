# The event loop: polling, triggers and when the agent wakes

How SYNAPSE notices what happens in the home and decides when the LLM should think about it.
This is the "event bridge + history" block from [`synapse.md`](synapse.md) §9, designed so it can grow into verification (§6), routine learning and self-authored scenarios without rework.
Status: **v1 built** in [`source/`](../source/) (branch `feat/house-mcp-polling-loop`): poll, normalize, diff, store, seed triggers, stub agent, plus the **LangGraph agent** (reads the home, can set the lamp and plug, verifies by reading back). Not yet: agent-editable triggers, guardrails/approval for writes, action ledger, attribution.

## 1. Why poll, and why not call the LLM on every poll
- The MCP has **no live events** ([`mcp-capabilities.md`](mcp-capabilities.md)), so we poll `get_home` (one call returns every device with its values and `lastCommunicationTime`).
- **The LLM does not run on every poll.** At 20–30 s that is ~3,000 calls a day, almost all on an unchanged home: expensive, slow, inconsistent, and a single snapshot can't show a change in behaviour anyway.
- **Safety never waits for an LLM.** Smoke → red lamp + plug off is code (and an Altice scenario), never a model call.

## 2. Three speeds
| Speed | Who | Input | Job |
|---|---|---|---|
| Every poll (~20 s) | Code | Raw snapshot | Diff into events, safety rules, check expectations |
| Every 10–15 min (heartbeat) **plus** important events | LLM | Recent events + current state + baselines | "Is right now normal? Act, ask or wait?" |
| Nightly | LLM | Day and week summaries vs baselines | Spot changes in behaviour, update baselines, propose or retire rules, rewrite its own triggers |

- **The heartbeat always runs**, even when nothing changed. That is how SYNAPSE notices *absence* ("no tea by 10:00", "no activity for 4 h"), which a change-only loop never sees.
- **Behaviour change is a slow signal.** "Rosa's tea moved from 08:10 to 09:40 over a week" only shows in summarised history, so learning happens in the nightly run, not in the 20 s loop.

## 3. The LLM decides what to watch; code does the watching
Wake rules (**triggers**) are **rows in the database, not code**. The agent can add, change and expire them, each with a reason and an expiry, e.g.:
> "Rosa's mornings are shifting. Until Sunday, wake me on any plug use between 07:00 and 11:00, check every 5 min after 10:00, and drop the 'no tea by 09:00' alert."

Code then runs those rules cheaply and reliably. So the system is **not a fixed rule set**: the LLM keeps re-programming its own attention. Two things it **can't** change: the **locked safety triggers** and a **minimum heartbeat**. Every trigger keeps its reason, so "Why are you watching the plug in the mornings?" has an answer ("Porquê?").

## 4. The loop (v1)
```python
while True:
    snap   = source.fetch()                      # get_home → raw JSON
    state  = normalize(snap)                     # {(device, capability): Reading(value, comm_ts)}
    events = diff(prev_state, state, now)        # value changes + device went stale / came back
    store.save(snap, events)

    fired = triggers.match(events, now, types)   # by device type, never a hard-coded id; includes the heartbeat
    for rule in fired with actions:              # hard rules: code runs them, no LLM
        run_actions(resolve_targets(rule.actions, fresh_by_type(state)))  # each write goes through the guard
    reasons = [r for r in fired if r.wake_agent]
    if reasons:
        ctx = build_context(reasons, state, store, since=last_agent_run)
        out = agent.run(ctx)                     # LangGraph agent, or the stub
        store.log_agent_run(ctx, out)
        triggers.apply_updates(out.trigger_updates)

    prev_state = state
    sleep_until_next_tick()                      # ~20 s
```
- Several reasons in one tick wake the agent **once**; unlocked triggers have a **cooldown** (~2 min) so a flickering value doesn't spam the LLM.
- A failed poll is recorded as a **gap**, never as "everything went to zero".
- **Triggers match a device type** (`contactSensor`, `smokeSensor`, `plug`…), so a re-paired sensor with a new id still fires. A hard rule targets a type too (`"target": "bulb"`) and is resolved to the first device of that type that is **fresh**; if none is, the action is logged as `skipped`, never sent to a stale id.
- If no fresh `smokeSensor` is reporting, the loop logs one WARNING (`safety trigger can't fire`) and one INFO when it comes back.
- Every live snapshot is also appended to a **JSONL recording**: it feeds replay, the simulator and the "Rosa" weeks.

### Layout
```
source/
  sources.py    McpSource (live) · ReplaySource (recorded JSONL), same fetch()
  model.py      Reading, Event, WakeReason
  normalize.py  get_home JSON → flat readings
  diff.py       readings → events
  store.py      SQLite: snapshots, events, triggers, agent runs
  triggers.py   load, match (by device type), apply updates
  guard.py      write allow-list, plug-on rule, read-back verification
  actions.py    hard-rule actions: resolve the target type, then guarded_write
  context.py    builds what the agent reads
  agent.py      StubAgent · LangGraphAgent (LLM + MCP tools)
  loop.py       the loop above
```

### Running and testing it
```
cp .env.example .env            # add CGH_API_KEY (same key as the connected-home MCP config)

# 1. No kit needed: replay the demo morning (08:00–08:30, door, kettle, lamp, camera goes silent)
uv run python -m source --replay tests/fixtures/morning.jsonl
LOOP_STALE_MINUTES=1 uv run python -m source --replay tests/fixtures/morning.jsonl  # also see "stale"

# 2. Live
uv run python -m source --ticks 3 --poll 5     # quick check
uv run python -m source                        # run until Ctrl-C
uv run python -m source -v                     # DEBUG on the console too

# 3. Tests
uv run python -m unittest discover -s tests -t . -v
```
The first live run needs the `mcp-remote` sign-in that Claude Code already did (`~/.mcp-auth`); without it a browser window opens once. Regenerate the demo with `uv run python tests/fixtures/make_morning.py`.

### Logs and history
Live runs write to `data/`, replays to `data/replay/` (both gitignored):

| File | What | How to read it |
|---|---|---|
| `logs/loop.log` | Human-readable log, DEBUG and up, rotated daily, 14 days kept | `tail -f data/logs/loop.log` |
| `logs/loop.jsonl` | Same log as JSON, with structured `data` (events, wake reasons, agent context at DEBUG) | `jq 'select(.data.wake != null and (.data.wake \| length) > 0)' data/logs/loop.jsonl` |
| `logs/mcp-remote.log` | The MCP proxy's own output (auth, connection problems) | when polling fails |
| `home.sqlite3` | Snapshots, events, agent runs | `uv run python -m source.show events \| wakes \| state` (`--replay` for replay data, `--since 2h`, `-n 100`, `wakes --context`) |
| `recording.jsonl` | Every raw snapshot (live only) | replay it with `--replay` |

Every poll logs one `tick` line (`tick 22 @ 11-18 08:21:00: 52 readings, 2 events, wake: plug-power`), every change an `event` line, every wake-up a `wake` line. Console level: `LOOP_LOG_LEVEL` or `-v`; `-q` for files only. In code, attach data with `log.info("...", extra={"data": {...}})`.

### LangGraph
Not used in the loop: poll → diff → store → decide is simpler and easier to debug as plain asyncio. LangGraph lives inside **`agent.run(ctx)`** (`source/agent.py`), with the same contract: context dict in, `{notes, trigger_updates, actions}` out.

Graph: `START → agent ⇄ tools → report → END`. The `agent` node is the LLM; `tools` runs the MCP calls it asks for and loops back until it has nothing left to call (capped by `AGENT_MAX_STEPS`); `report` turns the result into the output dict.

It shares the loop's `mcp-remote` session (`McpSource.session()`), so there is one connection to the MCP. Tools are an allowlist:

| Tool | Kind | Why |
|---|---|---|
| `get_home`, `get_device_details`, `get_home_security_status`, `get_home_scenarios` | read | Look at the home |
| `update_device_capabilities` | write | Set the lamp and plug; the only write |
| Alarm arming (`patch_home_security_status`) | excluded | The alarm is never armed on the kit |
| Scenario create/activate/execute, timers | excluded | Scenarios and timers can't be deleted |

- **Every write goes through the guard (`source/guard.py`), in code, not in the prompt**:
  - only the bulb (`power`, `brightness` 1–100, `temperature` 0–100, `color_rgb` 0–255) and the plug (`power`), with exactly `deviceId` and `body` (no `deviceName`, which the MCP would look up by name);
  - switching the plug **on** is blocked in the loop and allowed only from `source.ask` (a person asked); a blocked write is told to the LLM and the run continues;
  - after the write, code reads the device back and calls it **confirmed** only if the value matches **and** `lastCommunicationTime` is newer than before the write (verification level 2, [`synapse.md`](synapse.md) §6). Otherwise it is **unconfirmed**; the LLM is told which and must not claim more.
- **Stale devices are marked** in the context (`fresh: false`, `staleMinutes`) so the LLM doesn't present last-known values as current.
- **`actions` are the write calls that actually ran, with their results.** The LLM doesn't write them.
- **Replay gives the LLM no tools**, so a replay never touches the real kit.
- **`--stub`**, or no `OPENROUTER_API_KEY`, runs the stub agent (logs the context, does nothing).

Config: `OPENROUTER_API_KEY`, `AGENT_MODEL` (any OpenRouter slug with tool calling, default `anthropic/claude-sonnet-4.5`), `AGENT_MAX_STEPS` (default 12).

Later:
- Route per wake reason (heartbeat, safety, stale device) with different prompts and tools.
- The action ledger (§7) records each write with its expected effects.

## 5. Data shapes (fix these now, they're costly to change later)
**Event**
```
{id, ts, device, capability, old, new, kind: change|stale|returned, source: "unknown"}
```
`source` stays `unknown` in v1; attribution (§7) later fills `synapse | scenario:<id> | human`.

**Trigger**
```
{id, kind: change|threshold|stale|interval, condition: {...}, reason,
 created_by: seed|agent|human, locked, expires_at, enabled,
 actions: [{tool, args}], wake_agent}
```
**Hard rules.** A trigger's `actions` are tool calls the loop runs itself, with no LLM ([`source/actions.py`](../source/actions.py)). Only `update_device_capabilities` is allowed, replays and `--stub` never write, and each run is stored in `rule_runs`. `wake_agent: false` keeps the LLM out completely. Example: door open → lamp blue, door closed → lamp red (`door-open` / `door-closed`, no cooldown).
| Seed trigger | Locked |
|---|---|
| `interval` 15 min (heartbeat) | yes (period editable, with a minimum) |
| Smoke `sensorState → alarm` | yes |
| `security.isAlarmed` false → true | yes |
| Contact sensor changes | no |
| Plug `instantPower` changes by > 300 W | no |
| A device silent for > 30 min | no |

**Agent context**
```
{now, reasons[], state (trimmed), events_since_last_run[], timeline_today[], baselines: {}}
```
**Agent output**: `{notes, trigger_updates[], actions[]}`. `actions` are the executed write calls and their results (empty for the stub and for replays), logged with the run.

## 6. What the real data tells us (`get_home`, 17 Nov 2026)
- **The kit is still offline**: the MCP answers, but every `lastCommunicationTime` is from 13–14 Nov. The loop's first live run (17 Nov) correctly reported all six devices as stale.
- **`lastCommunicationTime` isn't an event by itself**, but it decides whether a device is stale. The plug's was ~7 h older than the others', so "relay on, 0 A" only means something if the reading is **newer than our command**.
- **Camera `motionDetected` / `soundDetected` were empty strings.** They look short-lived, so a 20 s poll may miss them. Log them whenever they're non-empty to learn how long they stay set; don't build routines on them until then.
- **`security.isAlarmed: true` with `alarmType: FIRE_SMOKE`** is a stale "has gone off" flag, not "armed". Trigger on the **change**, not the level.
- **Skip noisy fields** in the diff (e.g. camera `sdFormatState`, `ptzStop`).
- **Client:** Cloudflare blocked a plain Python HTTP client before, so the poller starts the same `mcp-remote` stdio proxy as Claude Code (official `mcp` SDK). It reuses that OAuth sign-in from `~/.mcp-auth`; this works.

## 7. How the future pillars plug in
| Later | Plugs into | Notes |
|---|---|---|
| Guardrails, approval, per-reason routing | `agent.run(ctx)` | See §4 LangGraph |
| **Closed-loop verification** (act → sense → confirm) | A step after `diff`, before `triggers` | **Action ledger**: each command stored with its expected effects and deadlines. A deterministic **expectation checker** runs every tick; failures become events that wake the agent, which explains the cause. See [`synapse.md`](synapse.md) §6 |
| **Who made each change** | Inside `diff`, fills `event.source` | The MCP never says who changed something. Match against our action ledger (SYNAPSE), our copy of active scenario definitions (scenario runs on Altice's server), otherwise human |
| **Override learning** | Event pattern + `state_at(t)` | "Human reversed a SYNAPSE/scenario action within N min" is just an attributed event. Comparing overridden vs accepted cases needs **full snapshots stored**, so we can rebuild the room at any moment |
| **Self-authoring / self-pruning scenarios** | Reads `store`, writes triggers + a scenario registry | Scenarios can't be edited or deleted: "rewrite" = deactivate v1 + create v2 (`SY · <name> · v<n>`). The trigger format may not allow multi-condition rules, so the **local rule engine is the main runtime** and a rule is compiled to an MCP scenario only when it fits |
| Nightly review | A second `interval` trigger (24 h) | Different context builder: day/week summaries vs baselines |
| Security readiness check | Expectation checker | All inputs exist in `get_home` (hub cellular signal, camera `sdRecording`/`sdAvailable`, tamper flags). Arming is tier 4 and never done on the kit, so demo it in the simulator |
| Simulator | Another `source` | |

**Order:** the loop first, with attribution, snapshot storage, the action ledger and the expectation checker designed in from the start. Closed-loop verification next (works on live data, deterministic, good demo). Routine learning and self-authored scenarios last (they need days of history, so they lean on the simulator).
