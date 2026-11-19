# The demo: learned rhythms that run the house

**Goal:** in 3 minutes (and 5 for the second presentation) show that Alfred learns a home's rhythms by itself and runs the house ahead of them. The LLM is woken by what Alfred learned and decides what to do from the habits and the current context.

This plan is written to be built **in parallel by several agents, on another machine**. Every part owns its files and builds against the contracts below, with its own small fixtures. Nobody waits for another part. Integration (part F) comes last.

Read first: `docs/habits-plan.md` (habits, occasions, forecast, decisions, the 0.75 rule), `docs/learners.md`, `docs/anticipation.md`, `docs/occasions.md`, `docs/simulator.md`, `docs/event-loop.md`.

## The three examples (what the audience sees)

| # | Example | What Alfred learns, unprompted | How it changes the house |
|---|---|---|---|
| 1 | **Waking up** (daily, varies by weekday) | "Waking up: weekdays 06:55–07:30 · weekends 08:20–09:10. Kettle, then the front door" | Heating starts one warm-up time before: 06:40 on weekdays, 08:05 on Saturday |
| 2 | **Coming home** (one rhythm per person) | "Ana gets home weekdays 18:25–18:55" · "João gets home 19:15–19:45, Tuesdays 20:45–21:15" · "The house warms 1 °C in about 12 min" | Heating starts so the house is warm when the first person arrives; eco when the last phone leaves |
| 3 | **A gathering** (a departure from the rhythm, 5-min version only) | "Friday night gathering, seen 3 times: guests' phones, door often, late night, next morning ~1.5 h later" | At 20:35 it recognises it: heating lowered, the late bedtime is expected, **tomorrow's warm-up moves from 08:05 to 09:30** |

**Presentation 1 (3 min):** #1 and #2, plus "planted vs found". **Presentation 2 (5 min):** adds #3.

## The demo kit (what we assume, nothing more)
Today's kit (temperature/humidity sensor, front door contact, lamp, camera, smoke sensor, alarm), plus:
- **a thermostat**;
- **the WiFi router's list of connected devices** (phones);
- **3 plugs**: kettle, TV, washing machine.

## Contracts (everyone builds against these)

### C1. New devices in the `get_home` snapshot
Same style as the existing devices (`id`, `name`, `type`, `capabilities[{type, value, ...}]`).

```json
{"id": "th-1", "name": "Thermostat", "type": "thermostat", "capabilities": [
  {"type": "temperatureValue", "value": 19.4},
  {"type": "targetTemperature", "value": 16.0, "min": 5, "max": 30, "step": 0.5},
  {"type": "heatingState", "availableValues": ["on", "off"], "value": "off"},
  {"type": "mode", "availableValues": ["heat", "eco", "off"], "value": "heat"}]}
```
```json
{"id": "rt-1", "name": "Router", "type": "router", "capabilities": [
  {"type": "connectedClients", "value": [
    {"id": "c-8f3a", "name": "Ana's phone", "kind": "phone", "known": true},
    {"id": "c-91d0", "name": "", "kind": "phone", "known": false}]}]}
```
- `id` is a stable hash of the device's MAC address. The same phone always has the same id. A guest phone that comes back has the same id too.
- `known`: a person marked it as the household's. Unknown phones are guests.
- Plugs: three devices of type `plug`, named `Kettle`, `TV`, `Washing machine` (the names are what tells them apart; ids change on re-pairing).

### C2. Signal names (how happenings name a device)
- Today: `"<device type>.<capability>"`, e.g. `plug.instantPower`.
- **New rule:** when a home has more than one device of a type, the device's name goes in, slugged: `"<type>:<slug>.<capability>"`.
  - `plug:kettle.instantPower`, `plug:tv.instantPower`, `plug:washing-machine.instantPower`.
  - A single-device type keeps the old name (`bulb.power`), so existing homes and tests don't change.
- The router becomes **one signal per phone** plus one count:
  - `phone:<client id>.connected`: an on/off signal, value `"on"` while connected. Rest is `"off"`.
  - `router.guestPhones`: the number of connected phones with `known: false`.
  - Known phones also carry the name: `phone:<client id>` keeps the client's `name` as the device name.
- Helper, used everywhere instead of `signal.split(".")[0]`: `source.learn.device_type(signal) -> "plug"` (strips `:<slug>`).
- Phones are **activity** (they say someone is home). `router.guestPhones` is activity. `thermostat.temperatureValue` is **slow** (like the room's temperature). `thermostat.targetTemperature` and `heatingState` are activity only when a person changed them (the simulator's person, not Alfred: see C6).

### C3. Habits: person and weekday windows (adds to the habit shape in habits-plan.md)
```json
{"id": "habit-4", "person": {"phone": "c-8f3a", "name": "Ana's phone"},
 "windows": [{"days": ["mon","tue","wed","thu","fri"], "usual": "18:40", "earliest": "18:25", "latest": "18:55"}],
 "...": "everything else as before"}
```
- `person`: set when one known phone's arrival or departure is a step of the habit (share ≥ 0.6). Else `null`.
- `windows`: one entry per group of days whose times differ (weekdays vs weekend, or one weekday that stands out, like João's Tuesday). One entry with all days when there is no difference. The old `window` stays (the overall one).

### C4. Warm-up (new, `source/learn/warmup.py`)
```python
warmup(db, now) -> {"rate_c_per_h": 4.8, "loss_c_per_h": 0.6, "samples": 23, "confidence": 0.82, "as_of": "2026-11-19"}
lead_minutes(db, now, from_c: float, to_c: float) -> float | None   # None until there are 5 samples
```
Measured from past heating stretches (`heatingState` on, room temperature rising), most recent 30 days.

### C5. Occasions: guests and the next-day shift (adds to occasions.md)
- `fingerprint` gains `"guest_phones": 5` (the most connected at once).
- `match_now` entries gain `"shifts": [{"habit_id": "habit-1", "date": "2026-11-14", "minutes": 90}]`: how the occasion moved habits the next day in past episodes (median).

### C6. Forecast before and after an occasion (adds to the forecast shape)
A forecast entry that an occasion moves gains:
```json
"shifted": {"occasion_id": "occasion-1", "minutes": 90, "before": {"expected": "08:40", "window": ["08:20", "09:10"]}}
```
`expected` and `window` are then the shifted ones.

### C7. Thermostat writes (the guard)
- Writable: `thermostat.targetTemperature` (number), `thermostat.mode` (`heat` | `eco` | `off`).
- Limits, checked in code: target 16–23 °C; at most 3 °C change per write; never above 18 °C when no known phone is home; at most one write per 10 min (except when a person changed it, which always wins: Alfred never fights a person for 2 h after they touched it).
- Plugs: `power: off` allowed (e.g. TV), `power: on` still needs a person.
- The simulator's **people** also set the thermostat (21 °C when they get home or wake up, 16 °C at night and when the last one leaves). That is the history Alfred learns from, and the "before Alfred" baseline: the house is cold for ~25 min after each arrival.

### C8. Waking the LLM from what was learned (new, `source/wake.py`)
```python
learned_wakes(db, now: datetime, state: State) -> list[WakeReason]
```
Each reason carries a stable `key` so it fires **once per habit occurrence**; fired keys are kept in a `wakes` table (`key`, `ts`, `data`).

| `kind` | Fires when | `key` |
|---|---|---|
| `habit_soon` | a forecast habit (probability ≥ 0.5) starts within `lead_minutes` + 5 min and has something to prepare (heating, lamp) | `habit_soon:<habit>:<date>` |
| `arrival` / `departure` | a known phone connects / the last known phone leaves | `arrival:<phone>:<date>:<hh>` |
| `habit_missing` | a habit with `importance` ≥ normal is past its weekday window's `latest` + 20 min, not done, and a known phone is home | `habit_missing:<habit>:<date>` |
| `occasion` | `match_now` confidence crosses 0.5 | `occasion:<occasion>:<date>` |

WakeReason `data` (what the LLM needs): the habit or occasion card (name, explanation, means, if_missing), its forecast entry, who is home, room and target temperature, `warmup`, and the decisions already planned for it.

### C9. What the LLM answers, and who sets the confidence
The agent's report (`source/agent.py`, `parse_report`) gains:
```json
"proposals": [{"action": {"tool": "update_device_capabilities", "target": "thermostat",
                          "args": {"body": {"type": "targetTemperature", "value": 21}}},
               "at": "2026-11-19T18:15:00", "reason": "Ana usually gets home at 18:40 on Thursdays; the house needs 25 min to reach 21 °C.",
               "doubt": 0.0, "wake": "habit_soon:habit-4:2026-11-19"}]
```
- **Confidence is set by code:** `base` comes from the wake (habit confidence × forecast probability × evidence n/(n+1); for an occasion, the match's confidence). Final = `base × (1 − doubt)`. The LLM can only lower it, with `doubt` in [0, 1] and a reason.
- ≥ 0.75: the guard runs it (C7) and it's stored as a decision with `mode: "do"`. Below: `mode: "suggest"`, shown, never sent.
- `at` in the future: stored as a planned decision, run by `run_due` (source/anticipate.py).
- Each proposal becomes a decision (habits-plan.md shape) with `"kind": "llm"`, `"wake": <key>`, `"reason": <the LLM's sentence>`.

### C10. Cached LLM replies (for an instant, repeatable demo)
`source/llm_cache.py`: `cached(kind: str, payload: dict, call: Callable[[], str]) -> str`.
- Key: sha256 of `kind` + the payload as sorted JSON, without timestamps of the request itself.
- Stored in `data/llm-cache/<kind>.jsonl` (gitignored). A hit never calls the model. `ALFRED_LLM_CACHE=only` fails instead of calling (for the demo); `=off` never reads it.
- Used by the agent (C9) and by the namer (source/learn/namer.py).

### C11. Demo endpoints for the visualizer (source/simulate_web.py)
```
GET /moments?household=demo  -> {"moments": [{"id": "weekday-morning", "title": "Waking up, a weekday",
                                 "day": 59, "time": "06:30", "focus": ["habit-1"], "say": "one line for the presenter"}], ...}
GET /thoughts?household=demo&day=59 -> {"wakes": [{"at", "kind", "key", "habit_id", "occasion_id",
                                 "sentence", "confidence", "mode", "status", "action"}]}
GET /planted?household=demo&day=60  -> {"rows": [{"planted": "Ana home weekdays 18:40", "found": "habit-4", "found_text": "...", "match": "yes|partly|no"}]}
```

### C12. The demo household (source/simulate.py preset `demo`)
- **Start** 2026-09-21 (a Monday), **60 days**, so day 60 is Thursday 19 Nov. Outside temperature falls from ~18 °C to ~10 °C over the run (heating matters by the end).
- **People** (new `people` option, a list):
  - Ana: phone `c-8f3a`. Leaves 08:10, home 18:40 on weekdays (spread 10 min). Weekends at home with outings.
  - João: phone `c-2b71`. Leaves 08:30, home 19:30, **Tuesdays 21:00**. Works from home on Wednesdays (phone stays all day).
- **Waking up:** first person 06:55–07:30 on weekdays, 08:20–09:10 at weekends: kettle, then the door 15–25 min later on weekdays.
- **TV** evenings 21:00–23:00 most days; **washing machine** Mondays ~19:30 (the shape: 2 kW for 15 min, then 200–500 W pulses for ~60 min).
- **Gatherings:** Fridays 2026-10-02, 10-16 and 10-30 (the three past ones), then 11-13, the one shown live (day 54). 5–6 guest phones (new ids each time) from 20:00, the door often, TV off, bed ~01:30, the next morning ~90 min later.
- **The heating** as set by the people (C7), with simple physics: `dT/dt = heat × rate − (T − T_out) × loss`, rate ≈ 5 °C/h, loss tuned so the house cools 2–3 °C overnight.
- **The answer key** lists each planted rhythm in the words of the "planted" column of C11.
- **Scripted moments** (the demo days, kept stable by the seed and the prefix property in docs/simulator.md):
  - day 59 (Wed 18 Nov) 06:30: a weekday morning;
  - day 55 (Sat 14 Nov) 07:45: the morning after a gathering;
  - day 60 (Thu 19 Nov) 18:00: Ana arrives at ~18:40;
  - day 54 (Fri 13 Nov) 20:30: the gathering, live.

## The parts

| Part | Builds | Owns (only these files) | Contracts it implements | Fixtures it uses until the others land |
|---|---|---|---|---|
| **A. Demo house** | The `demo` preset: people and phones, guest phones, thermostat with physics and people setting it, 3 named plugs, gatherings, answer key, scripted days | `source/simulate.py`, `source/sim_data/home_template.json` (add the new devices, as copies of real shapes), `docs/simulator.md`, `tests/test_simulate.py` | C1, C7 (people's side), C12 | none needed |
| **B. Signals** | Per-device signal names, the router → phone signals and `router.guestPhones`, `device_type()`, activity/slow lists, every call site of `split(".")[0]` | `source/normalize.py`, `source/learn/happenings.py`, `source/learn/rest.py`, `source/learn/__init__.py`, `source/ingest.py`, call sites in `source/learn/*.py` (only the helper swap), `tests/test_normalize.py`, `tests/test_happenings.py`, new `tests/fixtures/demo-snapshots.jsonl` (hand-made, a few hours: 3 plugs, 2 phones, guests, thermostat) | C1 (reading), C2 | its own hand-made snapshots |
| **C. Learners** | Habit `person` and weekday `windows`; `warmup.py`; occasions' `guest_phones` and `shifts`; forecast `shifted` | `source/learn/grouping.py`, `source/learn/recurrence.py`, `source/learn/warmup.py` (new), `source/learn/occasions.py`, `source/learn/forecast.py`, their tests, `docs/habits.md`, `docs/occasions.md` | C3, C4, C5, C6 | happenings written directly into a test DB with C2 names |
| **D. Waking and acting** | `wake.py` (C8), context for the LLM, the agent's proposals (C9), the confidence rule, thermostat in the guard (C7), decisions from proposals, the LLM cache (C10), the prompt | `source/wake.py` (new), `source/llm_cache.py` (new), `source/loop.py`, `source/context.py`, `source/agent.py`, `source/guard.py`, `source/actions.py`, `source/anticipate.py`, `source/prompts/agent.md`, `source/learn/namer.py` (cache only), their tests, `docs/anticipation.md`, `docs/event-loop.md` | C7, C8, C9, C10 | a test DB with hand-written habits, forecast and occasion rows in the C3–C6 shapes; a fake model returning canned JSON |
| **E. Demo screen** | Moments bar (one click jumps to day and time), "Alfred thinks" panel, "planted vs found" list, the habit card's weekday windows and person, the forecast's before/after, the heating on the 3D view (target and room temperature) | `console/visualizer.html`, `source/simulate_web.py`, `tests/test_visualizer.py` | C11 | `/moments`, `/thoughts`, `/planted` served from a fixture JSON first, then from real data |
| **F. Integration** (last, one agent) | Wire everything; run the demo house end to end; `source/demo.py` that pre-computes the runs, names and LLM replies for the moments; check each moment shows what the table at the top says; fix what doesn't; README | `source/pipeline.py`, `source/learn/run.py`, `source/demo.py` (new), `README.md`, anything needed to make it work | all | — |

Shared files nobody else touches without saying so in their final note: `source/model.py` (D may add fields to `WakeReason`), `pyproject.toml` (no new dependencies without need).

## Running it in parallel

1. **Start from the current work.** The habits, occasions, forecast, anticipation and namer work is on branch `feat/namer` (commit and push it first). Create `feat/demo` from it.
2. **One worktree per part** so agents don't touch each other's files:
   ```
   git worktree add ../gh-demo-A -b feat/demo-A feat/demo
   git worktree add ../gh-demo-B -b feat/demo-B feat/demo
   ... (C, D, E)
   ```
3. **Start A to E at the same time**, one agent each, with the brief below. Each works only in its own files and tests against fixtures in the contract shapes.
4. **Merge** into `feat/demo` in this order: B → A → C → D → E (B first: the signal names are what the others read). After each merge run the full suite.
5. **Then F**, one agent, on `feat/demo`.
6. **Rehearse:** `uv run python -m source.demo --household demo` pre-computes everything; then `ALFRED_LLM_CACHE=only` and open the visualizer.

Tests: `uv run python -m unittest discover -s tests -t . -v` (the full suite must pass before a part says it's done).

### The brief for each agent (copy it, filling in the part)
> You are building **part X** of `docs/demo-plan.md` in this worktree. Read the plan, then `docs/habits-plan.md` and the docs listed at its top.
> - Edit only the files your part owns (the table). If you truly need a change elsewhere, don't make it: write it in your final note.
> - Build against the contracts exactly (names, fields, shapes). Where a contract is unclear, choose, and say what you chose in your final note and in a short section of your part's doc.
> - Until the other parts land, use your own small fixtures in the contract shapes.
> - Write tests for what you build. The full suite must pass. Keep the house style: short plain comments, no new dependency unless needed.
> - Don't call the real LLM in tests.
> - Final note: what you built, what you chose where the contract was open, what the integration must wire, anything left.

## What "done" looks like (checked in part F)
- Day 60 of the demo house, in the knowledge tab:
  - **Waking up** with weekday and weekend windows;
  - **Ana gets home** and **João gets home** (with his Tuesdays) as habits of a person;
  - **Friday night gathering** with guest phones;
  - the warm-up rate.
- Moment "Ana arrives": a `habit_soon` wake about 25–30 min before 18:40; the LLM's sentence; heating to 21 °C **done** (≥ 0.75); the room is warm when her phone connects. In the "before Alfred" days, the room was cold for ~25 min.
- Moment "gathering": an `occasion` wake at ~20:35; heating lowered; tomorrow's warm-up shown moving from 08:05 to ~09:30.
- Moment "Saturday morning": heating at the shifted time, not the usual one.
- Planted vs found: every planted rhythm has a row, most found.
- With `ALFRED_LLM_CACHE=only` every moment opens instantly, with no network.

## Not in this demo
The every-few-hours habit, monthly rhythms, slow drift, laundry advice, inferring an open window, the missed morning (a stretch goal for the 5-min version if time allows: a scripted day with no Waking up and a phone at home, giving a `habit_missing` wake).
