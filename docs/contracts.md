# Code map, contracts and data shapes

The developer reference that the code comments cite: which part lives where (parts 1–8), the contracts shared between parts (C1–C12), and the shapes stored in the database.
The concepts behind them are in [`../ARCHITECTURE.md`](../ARCHITECTURE.md) and [`../TECHNICAL.md`](../TECHNICAL.md). How to work in the repo: [`../AGENT.md`](../AGENT.md).

## Code map
Parts 1–8 are the learning pipeline's numbering.

| # | Part | Code | What it does |
|---|---|---|---|
| 1 | Simulator | `source/simulate.py`, `source/sim_data/` | Households as `get_home` snapshots, with an answer key (`--truth`); the demo house is `--household demo` ([`simulator.md`](simulator.md)) |
| 2 | Ingest | `source/ingest.py` | Snapshots → events in the store, at full speed, times from the data |
| 3 | Rest finder | `source/learn/rest.py` | Each signal's resting state or level |
| 4 | Happening extractor | `source/learn/happenings.py` | Each departure from rest, with start, end and size |
| 5 | Grouper | `source/learn/kinds.py` | Kinds (similar happenings) and links (A then B) |
| 6 | Pattern learner | `source/learn/habits.py` | Kinds, links and quiets that recur at a consistent time → patterns (`sensors` table) |
| 7 | Namer | `source/learn/namer.py`, `source/prompts/namer*.md` | The LLM names patterns, habits and occasions, once |
| 8 | Views | `dashboard/`, `console/visualizer.html` | Watch the home and what Alfred learned |
| – | Habits, rhythms | `source/learn/grouping.py`, `recurrence.py` | Patterns that recur together → habits with a rhythm, weekday windows and a person ([`habits.md`](habits.md)) |
| – | Occasions | `source/learn/occasions.py` | Rare evenings recognised by resemblance; live match ([`occasions.md`](occasions.md)) |
| – | Forecast, warm-up, today | `source/learn/forecast.py`, `warmup.py`, `today.py` | Next 24 h per habit; how fast the house warms; each habit's status now |
| – | Nightly run | `source/learn/run.py`, `source/pipeline.py` | `learn_day` for one day; the pipeline simulates, ingests and learns day by day |
| – | Event loop | `source/loop.py`, `sources.py`, `normalize.py`, `diff.py`, `store.py`, `clock.py` | Poll the MCP (or a replay), keep the history, run everything below ([`event-loop.md`](event-loop.md)) |
| – | Triggers, hard rules | `source/triggers.py`, `actions.py` | Wake rules as data; rules that write without the LLM |
| – | Decisions | `source/anticipate.py` | Lamp changes ahead of habits; the LLM's proposals as decisions; `do` or `suggest` ([`anticipation.md`](anticipation.md)) |
| – | Learned wakes | `source/wake.py` | When to wake the LLM: a habit soon, an arrival or departure, a habit missing, an occasion |
| – | Agent | `source/agent.py`, `context.py`, `prompts/agent.md`, `llm_cache.py` | LangGraph agent with the MCP tools; its context; cached replies |
| – | Guard | `source/guard.py` | Which writes may run, with what limits; read-back verification |
| – | Phone API, voice | `source/phone_api.py`, `status.py`, `voice.py` | The app's HTTP API inside the loop; speech to text on this computer ([`phone-api.md`](phone-api.md)) |
| – | Phone app | `mobile-interaction-interface/` | Chat, voice, "Right now", plan with skip/undo, device controls |
| – | Demo | `source/demo.py`, `source/simulate_web.py` | Pre-computes 60 days and the moment days; serves the 3D visualizer |
| – | Jury dashboard | `dashboard/` | Plays back a recorded run (`source/recording.py`), or the real home live |

## Run modes
| Mode | Command | Reads | Writes |
|---|---|---|---|
| Replay | `python -m source --replay <file.jsonl>` | a recording | none, and the LLM gets no tools |
| Stub | `python -m source --stub` | the real kit | none, no LLM |
| Look-only | `python -m source --look-only --api` | the real kit, LLM on | none; each write says it was blocked |
| Live | `python -m source --api` | the real kit, LLM on | through the guard |
| Demo | `python -m source.demo`, then the visualizer | the simulated demo house | into the simulator only |

## The store
One SQLite file per home (`data/home.sqlite3`; `data/` is gitignored).

| Tables | Hold |
|---|---|
| `snapshots`, `events`, `devices`, `days` | The raw history |
| `happenings`, `kinds`, `sensors`, `sensor_readings` | What was learned: patterns and when they occurred |
| `habits`, `habit_readings`, `occasions`, `occasion_episodes` | Habits and occasions |
| `decisions`, `wakes`, `overrides` | What Alfred planned or did, why it woke, and when people overruled it |
| `rule_runs`, `agent_runs`, `learned_days` | Logs of rules, LLM runs and nightly learning |

## Contracts
Shapes shared between parts; the code cites them by number.

| # | Contract | Where |
|---|---|---|
| C1 | New devices in `get_home`: thermostat, router `connectedClients` (`id`, `name`, `kind`, `known`), named plugs | `source/sim_data/home_template.json`, `normalize.py` |
| C2 | Signal names: `<type>.<capability>`, or `<type>:<slug>.<capability>` when a type has several devices; `phone:<client id>.connected`, `router.guestPhones`; read the type with `device_type()` | `source/learn/__init__.py`, `normalize.py` |
| C3 | Habit `person` and weekday `windows` | `learn/grouping.py`, `recurrence.py` |
| C4 | `warmup(db, now)`, `lead_minutes(db, now, from_c, to_c)` | `learn/warmup.py` |
| C5 | Occasion `guest_phones` and the next-day `shifts` | `learn/occasions.py` |
| C6 | Forecast entries an occasion moves gain `shifted` | `learn/forecast.py` |
| C7 | Thermostat writes and their limits ([`../TECHNICAL.md`](../TECHNICAL.md#4-safety)); the simulator's people set the thermostat too | `guard.py`, `simulate.py` |
| C8 | `learned_wakes(db, now, state)`, once per key, kept in `wakes` | `wake.py` |
| C9 | The agent's `proposals`; confidence set by code | `agent.py`, `anticipate.py` |
| C10 | `cached(kind, payload, call)` LLM reply cache | `llm_cache.py` |
| C11 | Demo endpoints `/moments`, `/thoughts`, `/planted` | `simulate_web.py` |
| C12 | The `demo` household | `simulate.py`, [`simulator.md`](simulator.md#the-demo-house-demo) |

## Data shapes
**Happening** (`happenings` table): `signal`, `device_id`, `capability`, `start_ms`, `end_ms`, `duration_s`, `size`, `peak`, `value`, `cut`, `kind`.

**Pattern** (`sensors` table, "learned sensor"):
```json
{"id": "pattern-1", "name": "Morning kettle", "named_by": "llm", "learner": "habit", "status": "active",
 "pattern": {"parts": ["door opened", "plug-A (about 2,100 W, about 3 min)"], "time_of_day": "08:05",
             "spread_min": 12, "days_seen": 12, "days_total": 14, "late_after": "08:40"},
 "learned_at": "2026-09-05", "confidence": 0.86}
```

**Habit** (`habits` table):
```json
{"id": "habit-1", "name": null, "named_by": null, "status": "active",
 "patterns": ["pattern-6", "pattern-2"],
 "steps": [{"pattern": "pattern-6", "offset_min": 0, "share": 0.94},
           {"pattern": "pattern-2", "offset_min": 8, "share": 0.89}],
 "recurrence": {"kind": "weekly", "weekdays": ["mon","tue","wed","thu","fri"], "text": "every weekday"},
 "window": {"usual": "07:59", "earliest": "07:33", "latest": "08:27"},
 "windows": [{"days": ["sat","sun"], "usual": "08:53", "earliest": "08:25", "latest": "09:15"}],
 "person": {"phone": "c-8f3a", "name": "Ana's phone"},
 "variation": {"spread_min": 14, "by_weekday": {"sat": 50}}, "trend": {"minutes_per_week": 0},
 "occurrences": 16, "opportunities": 18, "learned_at": "2026-09-06", "confidence": 0.84}
```
`recurrence.kind` is `daily`, `weekly`, `every_n_days` (with `anchor`), `monthly` or `hourly`.

**Decision** (`decisions` table):
```json
{"id": "decision-12", "at": "2026-09-22T07:50:00", "kind": "prepare", "habit_id": "habit-1", "occasion_id": null,
 "action": {"tool": "update_device_capabilities", "target": "bulb", "args": {"body": {"type": "brightness", "value": 30}}},
 "confidence": 0.81, "mode": "do", "status": "planned", "reason": "Waking up usually 07:59 on Mondays"}
```
- `kind`: `prepare` (before a habit), `adapt` (an occasion's expectation) or `llm` (a proposal, with `wake`).
- `status`: `planned`, `done`, `skipped` or `failed`.

The forecast and occasion shapes are in [`anticipation.md`](anticipation.md#the-forecast) and [`occasions.md`](occasions.md#shapes).
