# The event loop: polling, triggers and when the agent wakes

How Alfred notices what happens in the home and decides when the LLM should think about it.
It is the history that the learners in [`starting-plan.md`](starting-plan.md) read from. It began as the "event bridge + history" block of the earlier concept ([`synapse.md`](synapse.md) §9), which is where the § references to verification and scenarios below point.
Status: **v1 built** in [`source/`](../source/): poll, normalize, diff, store, seed triggers, hard-rule actions, the stub agent and the **LangGraph agent** (reads the home, can set the lamp and plug, verifies by reading back), plus ingest and the first two learners (rest, happenings) for simulated days. Not yet: agent-editable triggers, approval for writes, the action ledger, attribution.

## 1. Why poll, and why not call the LLM on every poll
- The MCP has **no live events** ([`mcp-capabilities.md`](mcp-capabilities.md)), so we poll `get_home` (one call returns every device with its values and `lastCommunicationTime`).
- **The LLM does not run on every poll.** At 20 s that is ~4,000 calls a day, almost all on an unchanged home: expensive, slow, inconsistent, and a single snapshot can't show a change in behaviour anyway.
- **Safety never waits for an LLM.** Smoke → red lamp + plug off is code (and an Altice scenario), never a model call.

## 2. Three speeds
| Speed | Who | Input | Job |
|---|---|---|---|
| Every poll (20 s) | Code | Raw snapshot | Diff into events, run hard rules, match triggers |
| Every 15 min (heartbeat) **plus** important events | LLM | Recent events + current state + baselines | "Is right now normal? Act, ask or wait?" |
| Once a day | Code (the learners), then the LLM for names only | The stored history: happenings vs rest and the usual day | Find habits, events and drift, and write them as learned sensors ([`starting-plan.md`](starting-plan.md)) |

- **The heartbeat always runs**, even when nothing changed. That is how Alfred notices *absence* ("no tea by 10:00", "no activity for 4 h"), which a change-only loop never sees.
- **Behaviour change is a slow signal.** "Rosa's tea moved from 08:10 to 09:40 over a week" only shows in summarised history, so learning happens in the daily run, not in the 20 s loop.

## 3. The LLM decides what to watch; code does the watching
Wake rules (**triggers**) are **plain data, not code**: in v1 a seed list in [`source/triggers.py`](../source/triggers.py). Later they move into the database and the agent can add, change and expire them, each with a reason and an expiry, e.g.:
> "Rosa's mornings are shifting. Until Sunday, wake me on any plug use between 07:00 and 11:00, check every 5 min after 10:00, and drop the 'no tea by 09:00' alert."

Code then runs those rules cheaply and reliably, so the LLM keeps re-programming its own attention. Two things it **can't** change: the **locked safety triggers** and a **minimum heartbeat**. Every trigger keeps its reason, so "Why are you watching the plug in the mornings?" has an answer ("Porquê?").

## 4. The loop (v1)
```python
while True:
    snap   = source.fetch()                      # get_home → raw JSON
    state  = normalize(snap)                     # {(device, capability): Reading(value, comm_ts)}
    events = diff(prev_state, state, now)        # value changes + device went stale / came back
    store.save(snap, events)

    anticipate.plan_decisions(...) every 5 min; anticipate.run_due(...)   # docs/anticipation.md
    fired = triggers.match(events, now, types)   # by device type, never a hard-coded id; includes the heartbeat
    for rule in fired with actions:              # hard rules: code runs them, no LLM
        run_actions(resolve_targets(rule.actions, fresh_by_type(state)))  # each write goes through the guard
    reasons = [r for r in fired if r.wake_agent]
    reasons += wake.learned_wakes(db, now, state)  # habit soon, arrival, departure, habit missing, occasion
    if reasons:
        ctx = build_context(reasons, state, store, since=last_agent_run)
        out = agent.run(ctx)                     # LangGraph agent, or the stub
        anticipate.decide(db, now, out.proposals); anticipate.run_due(...)  # code sets the confidence
        store.log_agent_run(ctx, out)
        triggers.apply_updates(out.trigger_updates)

    prev_state = state
    sleep_until_next_tick()                      # 20 s
```
- Several reasons in one tick wake the agent **once**; unlocked triggers have a **cooldown** (2 min) so a flickering value doesn't spam the LLM.
- **Learned wakes** ([`source/wake.py`](../source/wake.py), [`anticipation.md`](anticipation.md#waking-the-llm-and-acting-on-its-proposals-demo-part-d)) wake the agent from what was learned rather than from a raw change, each **once per key** (`habit_soon:<habit>:<date>`, `arrival:<phone>:<date>:<hh>`, ...), kept in the `wakes` table. A learned reason carries `kind`, `key` and `data` (the card, forecast, who is home, temperatures, warm-up, planned decisions). The agent answers with `proposals`; code gives each the wake's confidence × (1 − doubt), stores it as a decision and runs the due `do` ones in the same tick.
- **Today's happenings**: each re-plan (every 5 min) first saves the happenings that ended since the last (`learn.happenings.save_happenings`), so the live occasion match and today's statuses see today, not only past nights. A state still held (a phone connected since 19:34) counts too, read from the events (`learn.storage.ongoing_happenings`).
- **Alfred's own changes are his**: a change to the value Alfred (or a rule) just wrote, within 15 min, is stored with `source = "agent"` (or `scenario:<rule>`), so the happenings leave it out and his heating is never learned as the people's habit. New devices go into the `devices` table, as in ingest (a guest's phone has no name: that is how the learners tell it).
- **Learned wakes** are looked for at most once a minute of home time (each look reads the forecast); `normalize(home, prev)` so a phone that left reads `off`.
- A failed poll is recorded as a **gap**, never as "everything went to zero".
- **Triggers match a device type** (`contactSensor`, `smokeSensor`, `plug`…), so a re-paired sensor with a new id still fires. A hard rule targets a type too (`"target": "bulb"`) and is resolved to the first device of that type that is **fresh**; if none is, the action is logged as `skipped`, never sent to a stale id.
- A device is **stale** when its `lastCommunicationTime` is older than `LOOP_STALE_MINUTES` (60). If no fresh `smokeSensor` is reporting, the loop logs one WARNING (`safety trigger can't fire`) and one INFO when it comes back.
- Fields that change without meaning anything (camera `sdFormatState`, `ptzStop`, …) are skipped in `normalize.py`.
- Every live snapshot is also appended to a **JSONL recording**: it feeds replay, the simulator and the "Rosa" weeks.

### Layout
```
source/
  sources.py    McpSource (live) · ReplaySource (recorded JSONL), same fetch()
  model.py      Reading, Event, WakeReason
  normalize.py  get_home JSON → flat readings
  diff.py       readings → events
  store.py      SQLite: snapshots, events, devices, days, rule runs, agent runs
  clock.py      home time: local days in Europe/Lisbon, never the machine's zone
  ingest.py     JSONL snapshots → history at full speed, for the learners (no agent, no rules)
  triggers.py   seed triggers, match (by device type), apply updates
  guard.py      write allow-list, plug-on rule, read-back verification
  actions.py    hard-rule actions: resolve the target type, then guarded_write
  context.py    builds what the agent reads
  agent.py      StubAgent · LangGraphAgent (LLM + MCP tools)
  loop.py       the loop above
  ask.py        ask the agent once, on the live house
  show.py       read the stored history
  simulate.py   the simulator: households as get_home snapshots (docs/simulator.md)
  pipeline.py   simulate + ingest + learn, one day at a time
  learn/rest.py        each signal's rest, from observed time (starting-plan part 3)
  learn/happenings.py  each departure from rest, stored in `happenings` (part 4)
```

### Running and testing it
```
cp .env.example .env            # fill in the values it lists (see README, "Run")

# 1. No kit needed: replay the demo morning (08:00–08:30, door, kettle, lamp, camera goes silent)
uv run python -m source --replay tests/fixtures/morning.jsonl
LOOP_STALE_MINUTES=1 uv run python -m source --replay tests/fixtures/morning.jsonl  # also see "stale"

# 2. Live
uv run python -m source --ticks 3 --poll 5     # quick check
uv run python -m source                        # run until Ctrl-C (LangGraph agent: can change the lamp/plug)
uv run python -m source --stub                 # stub agent: no LLM, no writes
uv run python -m source -v                     # DEBUG on the console too
uv run python -m source.ask "make the lamp blue"   # ask the agent once

# 3. Simulated days: simulate, ingest, find rest and happenings (docs/simulator.md)
uv run python -m source.pipeline --household rosa --days 14 --out data/sim/rosa/ --replace

# 4. Tests (about a minute: some run simulated months)
uv run python -m unittest discover -s tests -t . -v
```
A live run reads the kit over HTTP with Claude Code's sign-in (`tools/connected_home_auth.py`, tokens in `~/.config/connected-home/`). Without it, the loop records a gap and says to run `python3 tools/connected_home_auth.py login` once. Regenerate the demo with `uv run python tests/fixtures/make_morning.py`. All settings are in [`.env.example`](../.env.example).

### Logs and history
Live runs write to `data/`, replays to `data/replay/` (both gitignored):

| File | What | How to read it |
|---|---|---|
| `logs/loop.log` | Human-readable log, DEBUG and up, rotated daily, 14 days kept | `tail -f data/logs/loop.log` |
| `logs/loop.jsonl` | Same log as JSON, with structured `data` (events, wake reasons, agent context at DEBUG) | `jq 'select(.data.wake != null and (.data.wake \| length) > 0)' data/logs/loop.jsonl` |
| `home.sqlite3` | Snapshots, events, agent runs | `uv run python -m source.show events \| wakes \| state` (`source.show --replay wakes` for replay data, before the subcommand; `--since 2h`, `-n 100`, `wakes --context`) |
| `recording.jsonl` | Every raw snapshot (live only) | replay it with `--replay` |

Every poll logs one `tick` line (`tick 22 @ 11-18 08:21:00: 52 readings, 2 events, wake: plug-power`), every change an `event` line, every wake-up a `wake` line. Console level: `LOOP_LOG_LEVEL` or `-v`; `-q` for files only. In code, attach data with `log.info("...", extra={"data": {...}})`.

### Ingest (what the learners read)
`source/ingest.py` builds the history from a JSONL file (the simulator's output or a live `recording.jsonl`) without the loop: no agent, no triggers, no rules, so it never calls the LLM or touches the kit. Every time comes from the data.
```
uv run python -m source.ingest data/sim/couple.jsonl --out data/sim/couple/   # --replace to start the run again
```
Checked on the simulator's output (starting-plan part 2): 90 days of a house (27,000 snapshots, 155 MB) take about 6 s and 37 MB of memory, with no gap, stale or gone event. `SimulatedMonthsTest` in `tests/test_ingest.py` runs two months, across the October clock change, with the real clock blocked.

In code: `ingest(read_jsonl(path), store, stale_ms=..., on_day_end=fn)`. `fn(day)` runs once per local date (empty days too), after that day is committed and before anything later is stored, so a learner run there never sees the future.

What it writes to `home.sqlite3`:

| Table | Holds |
|---|---|
| `events` | `initial` (each signal's value at the first snapshot), `change`, `gone` (a signal no longer in the snapshot; it comes back as a `change` from `None`), `stale`, `returned`, and `gap` (snapshots more than 30 min apart: `old` = last before, `new` = first after) |
| `devices` | `device_id`, `type`, `name`, `first_ts`: find devices by type with `store.devices_of_type("plug")`, even after re-pairing |
| `days` | Each local date that has data: `first_ts`, `last_ts`, `snapshots` |
| `snapshots` | Every snapshot, as before |

Times are epoch ms; days are Lisbon dates (`source/clock.py`: `local_day`, `day_bounds`; 25 Oct 2026 is 25 h long).

### Rest (starting-plan part 3)
`rest_from_store(store, until_ms)` in `source/learn/rest.py` gives one `Rest` per signal, `(device_id, capability)`. Call it from `on_day_end` with `day_bounds(day)[1]`. Rest is measured in observed time, never in event counts: time inside a `gap`, while a device is stale (from its last contact until it returns), after a signal is `gone`, or after the last snapshot does not count.

| Kind | For | Rest | Check |
|---|---|---|---|
| `state` | on/off and text signals, including `True`/`False` | the value held longest | `at_rest(v)` |
| `level` | numbers | the busiest group of close values: its median `value` ± `wobble`, the spread that covers 99.5% of the group's time (at least `step`, the reporting resolution) | `at_rest(v)` |
| `slow` | `temperatureValue`, `humidityValue` | its normal rate of change, `rate_per_h` + `rate_wobble` | `rate_at_rest(dv, dt_ms)` |

`share` is the part of observed time spent at rest; no threshold is applied. Counters and bookkeeping (`todayConsumption`, battery, signal strength, `multiPower`, `instantVoltage`, …) are skipped (`SKIP`). `observed_stretches(events, observed_until)` is public so the happening extractor can end happenings at the same gaps. Rest is not stored in a table yet.

To see the rests of an ingested history:
```
uv run python -m source.learn.rest data/sim/rosa/      # --all also lists signals that never left rest
```
On 90 simulated days the plug rests at 0 ± 1 W (`rosa`, `couple`, the real house `home-a`) or 8 ± 1 W (`students`, whose console idles), the door at `normal`, the light at `off`, and the temperature moves about 0.2 °C per hour. Running the finder at every day end reads the whole history again each time: 0.2 s per day at 90 days, 2 to 9 s over the 90.

### Happenings (starting-plan part 4)
`save_happenings(store, until_ms)` in `source/learn/happenings.py` appends each departure from rest that ended before `until_ms` to the `happenings` table, which the grouper and habit learner read. `source/pipeline.py` calls it from `on_day_end` with `day_bounds(day)[1]`, then `learn_day(db, day)` from `source/learn/run.py` once that exists.

| Column | Holds |
|---|---|
| `signal` | `<device type>.<capability>` (`plug.instantPower`), plus `device_id` and `capability` |
| `start_ms`, `end_ms`, `duration_s` | From the first stretch away from rest to the return to rest |
| `size` | `level`: time-weighted median of the values held (robust to ramps and wobble); `slow`: total change, signed; `state`: null |
| `peak` | `level`: the highest value |
| `value` | `state`: the non-rest state held longest (`alarm`, `on`) |
| `cut` | 1 if a gap, stale device or gone signal ended it, at the last observed moment |
| `kind` | Left null for the grouper |

- Consecutive stretches away from rest are one happening; a slow signal is away while it moves faster than `rate_at_rest` allows.
- One still away from rest at the end of observed time is not written; a later run writes it once it ends.
- Append-only: per signal, only happenings that start after its last stored one ends are added, so ids stay stable.
- None for `instantCurrent` (computed from power) or the bulb's settings (`brightness`, `color_rgb`, `temperature`, `workMode`): `NOT_HAPPENINGS`.

```
uv run python -m source.pipeline --household rosa --days 14 --out data/sim/rosa/ --replace   # simulate, ingest, learn
uv run python -m source.learn.happenings data/sim/rosa/       # --signal instantPower, --json
```
On 14 days of `rosa`: 12 kettle happenings (one per planted morning use, about 2,100 W for 3 min), door openings (`alarm`), lamp `on` stretches, nothing on those at night. Temperature and humidity give about 140 more: their normal rate (rest) is slower than the daily swing.

### LangGraph
Not used in the loop: poll → diff → store → decide is simpler and easier to debug as plain asyncio. LangGraph lives inside **`agent.run(ctx)`** (`source/agent.py`), with the same contract: context dict in, `{notes, trigger_updates, actions}` out.

Graph: `START → agent ⇄ tools → report → END`. The `agent` node is the LLM; `tools` runs the MCP calls it asks for and loops back until it has nothing left to call (capped by `AGENT_MAX_STEPS`); `report` turns the result into the output dict.

It shares the loop's MCP session (`McpSource.session()`), so there is one connection to the MCP. Tools are an allowlist:

| Tool | Kind | Why |
|---|---|---|
| `get_home`, `get_device_details`, `get_home_security_status`, `get_home_scenarios` | read | Look at the home |
| `update_device_capabilities` | write | Set the lamp and plugs; the only write (the thermostat only through proposals) |
| Alarm arming (`patch_home_security_status`) | excluded | The alarm is never armed on the kit |
| Scenario create/activate/execute, timers | excluded | Scenarios and timers can't be deleted |

- **Every write goes through the guard (`source/guard.py`), in code, not in the prompt**:
  - only the bulb (`power`, `brightness` 1–100, `temperature` 0–100, `color_rgb` 0–255), the plugs (`power`) and the thermostat (`targetTemperature` 16–23, `mode` heat/eco/off), with exactly `deviceId` and `body` (no `deviceName`, which the MCP would look up by name);
  - the thermostat also has code limits (at most 3 °C a write, one write per 10 min, never above 18 °C with no known phone home unless an arrival is forecast within 2 h, nothing for 2 h after a person changed it), checked against what code knows (`guard.Heat`). The LLM's own tool calls carry none, so it can't write the thermostat: it proposes, and code runs the proposal;
  - switching the plug **on** is blocked in the loop and allowed only from `source.ask` (a person asked); a blocked write is told to the LLM and the run continues;
  - after the write, code reads the device back and calls it **confirmed** only if the value matches **and** `lastCommunicationTime` is newer than before the write (verification level 2, [`synapse.md`](synapse.md) §6). Otherwise it is **unconfirmed**; the LLM is told which and must not claim more.
- **The agent is told which rules just ran** (`rules_just_ran`: trigger, device, body, status), because its `state` was read before those writes; the prompt says never to undo them.
- **Stale devices are marked** in the context (`fresh: false`, `staleMinutes`) so the LLM doesn't present last-known values as current.
- **`actions` are the write calls that actually ran, with their results.** The LLM doesn't write them.
- **Replay gives the LLM no tools**, so a replay never touches the real kit.
- **`--stub`**, or no `OPENROUTER_API_KEY`, runs the stub agent (logs the context, does nothing).

Config: `OPENROUTER_API_KEY`, `AGENT_MODEL` (any OpenRouter slug with tool calling, default `anthropic/claude-sonnet-4.5`), `AGENT_MAX_STEPS` (default 12).

**Cached replies** ([`source/llm_cache.py`](../source/llm_cache.py), demo-plan C10): every model call of the agent (kind `agent`) and the namer (kind `namer`) goes through `cached(kind, payload, call)`. The key is the sha256 of the kind and the payload as sorted JSON: the model's name, the tools offered and the messages without their ids (the graph gives each message a random one). Replies are appended to `data/llm-cache/<kind>.jsonl` (gitignored with `data/`). `ALFRED_LLM_CACHE` unset or `on`: a hit never calls the model; `only`: a miss raises `CacheMiss` instead of calling (the demo, no network); `off`: never reads, still stores. `ALFRED_LLM_CACHE_DIR` moves the folder. Only models with a name (`model_name`) are cached, so the tests' fake models never touch it.

## 5. Data shapes (fix these now, they're costly to change later)
**Event**
```
{id, ts, device, capability, old, new, kind: initial|change|gone|stale|returned|gap, source: "unknown"}
```
`source` stays `unknown` in v1; attribution (§6) later fills `alfred | scenario:<id> | human`.

**Trigger** (the `Trigger` dataclass in `source/triggers.py`)
```
{id, kind: interval|change|delta|stale, reason, condition: {...}, locked, cooldown_s,
 created_by: seed|agent|human, expires_at, enabled, actions: [{tool, target, args}], wake_agent}
```
**Hard rules.** A trigger's `actions` are tool calls the loop runs itself, with no LLM ([`source/actions.py`](../source/actions.py)). Only `update_device_capabilities` is allowed, replays and `--stub` never write, and each run is stored in `rule_runs`. `wake_agent: false` keeps the LLM out completely.

| Seed trigger | Fires on | Locked | Notes |
|---|---|---|---|
| `heartbeat` | Every 15 min | yes | |
| `smoke` | Smoke sensor `sensorState → alarm` | yes | |
| `alarm-went-off` | `security.isAlarmed → true` | yes | |
| `contact` | Contact sensor changes | no | |
| `plug-power` | Plug `instantPower` changes by ≥ 300 W | no | |
| `device-silent` | A device goes stale or comes back | no | |
| `door-open` · `door-closed` | Contact opens · closes | no | Hard rules: lamp blue · lamp red. No LLM, no cooldown |
| `camera-sound` · `camera-motion` | Camera `soundDetected` · `motionDetected` non-empty | no | Fire only if detection works through the MCP, which is unconfirmed ([`mcp-capabilities.md`](mcp-capabilities.md#devices)) |

**Agent context** ([`source/context.py`](../source/context.py))
```
{now, reasons[], rules_just_ran[], state (trimmed), events_since_last_run[], timeline_today[], baselines}
```
`baselines` holds what Alfred learned: habits with today's status and the namer's cards, the next 24 h forecast, occasions going on now, and today's decisions ([`anticipation.md`](anticipation.md)). Each tick, before the triggers, the loop also plans decisions every 5 minutes and runs the due `do` ones (lamp only, through the guard).
`home` (when the home has a router or a thermostat): known phones home, guests' phones, room and target temperature, mode. Learned reasons also carry `kind`, `key` and `data`.
**Agent output**: `{notes, trigger_updates[], actions[], proposals[]}`. `actions` are the executed write calls and their results (empty for the stub and for replays), logged with the run. `proposals` (C9) become decisions; the loop adds them as `decisions` to the logged output.

## 6. What plugs in next
| Later | Plugs into | Notes |
|---|---|---|
| **Learned sensors** (the current focus) | `on_day_end` in [`source/pipeline.py`](../source/pipeline.py), once per simulated or real day | Plain code. Built: rest finder → happening extractor (above). Next: grouper → habit learner (`source.learn.run`, which the pipeline calls once it exists), then the LLM names each pattern. Writes `sensors` and `sensor_readings`. See [`starting-plan.md`](starting-plan.md) |

**Parked** from the earlier concept until the sensors work (design in [`synapse.md`](synapse.md)):
- **Approval and per-reason routing** inside `agent.run(ctx)`: different prompts and tools per wake reason.
- **Closed-loop verification** (§6 there): an action ledger stores each command with its expected effects; a checker runs every tick and failures wake the agent.
- **Who made each change**: fill `event.source` by matching against the action ledger and our copy of active scenarios, otherwise human.
- **Override learning**: "a human reversed an action within N min", which needs full snapshots stored to rebuild the room at any moment.
- **Self-authoring scenarios**: scenarios can't be edited or deleted, so "rewrite" = deactivate v1 + create v2 (`SY · <name> · v<n>`); the local rule engine stays the main runtime.
- **Security readiness check**: all inputs exist in `get_home` (hub cellular signal, camera `sdRecording`, tamper flags); demo arming only in the simulator.
