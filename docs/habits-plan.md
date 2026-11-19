# Habits, anticipation and occasions: the plan

From identifying patterns to acting on them. Five parts, built in parallel against the shapes below. **Status: all built and wired in** (`learn_day` learns habits and occasions each night; the loop plans and runs decisions; the namer names patterns, habits and occasions; the visualizer's "What Alfred knows" tab shows it all).

## Words
| Word | What it is | Example | Where |
|---|---|---|---|
| **Event type** | A kind of happening (the grouper's kinds) | plug ~2,100 W for 3 min; door open < 1 min | `kinds` (exists) |
| **Pattern** | One recurring event type or pair at a consistent time (today's "sensors") | light → plug around 07:59 | `sensors` (exists) |
| **Habit** | Patterns that recur together in some rhythm, with variation: daily, certain weekdays, every other weekend, monthly, every few hours | **Waking up**: quiet ends → light → door → kettle, 07:30–08:30, later at weekends | `habits` (new) |
| **Occasion** | A rare departure from the usual day, recognised by resemblance to past ones | **Dinner with friends**: many door openings, light until 01:00, late bedtime | `occasions` (new) |
| **Forecast** | What each habit will probably do in the next 24 h | Waking up tomorrow (Sat): 85 %, 08:40–09:30 | computed |
| **Decision** | Something Alfred does or suggests ahead of a habit, or because of an occasion | dim the lamp on at 08:35 | `decisions` (new) |

The namer names **habits and occasions** for people. Patterns keep their names as fallback detail.

## Confidence and acting
Every habit, forecast, occasion match and decision carries a `confidence` in [0, 1], computed by code, never by the LLM.
- **≥ 0.75: do it** (only the lamp: the guard allows nothing else, and never switches the plug on) and report it.
- **< 0.75: suggest it** (shown, logged, never sent).
- One past example lowers confidence: evidence factor `n / (n + 1)` (1 example → at most 0.5, so only a suggestion).

## Shapes (the contract)
**Habit** (`habits` table: `id`, `status`, `data` JSON; `habit_readings`: `habit_id`, `day`, `start`, `end`, `steps_seen`)
```json
{"id": "habit-1", "name": null, "named_by": null, "status": "active",
 "patterns": ["pattern-6", "pattern-2", "pattern-3"],
 "steps": [{"pattern": "pattern-6", "offset_min": 0, "share": 0.94},
           {"pattern": "pattern-2", "offset_min": 8, "share": 0.89}],
 "recurrence": {"kind": "weekly", "weekdays": ["mon", "tue", "wed", "thu", "fri"],
                "every_n_days": null, "anchor": null, "day_of_month": null, "every_n_hours": null,
                "text": "every weekday"},
 "window": {"usual": "07:59", "earliest": "07:33", "latest": "08:27"},
 "variation": {"spread_min": 14, "by_weekday": {"sat": 50, "sun": 55}},
 "occurrences": 16, "opportunities": 18, "first_seen": "2026-09-01", "last_seen": "2026-09-21",
 "learned_at": "2026-09-06", "confidence": 0.84, "trend": {"minutes_per_week": 0}}
```
`recurrence.kind`: `daily` | `weekly` (weekday set) | `every_n_days` (e.g. 14 with `anchor` date: every other weekend) | `monthly` (`day_of_month`, or nth weekday in `text`) | `hourly` (`every_n_hours`). `text` is the plain sentence the UI shows.

**Forecast** (`source/learn/forecast.py`, `forecast(db, now, hours=24) -> list[dict]`)
```json
{"habit_id": "habit-1", "date": "2026-09-22", "probability": 0.88, "expected": "07:59",
 "window": ["07:33", "08:27"], "confidence": 0.82, "basis": "Mondays: 9 of 10 since 1 Sept"}
```
**Occasion** (`occasions` table: `id`, `data`; `occasion_episodes`: `occasion_id`, `start`, `end`, `features` JSON)
```json
{"id": "occasion-1", "name": null, "named_by": null, "episodes": 3,
 "fingerprint": {"door_openings": 9, "light_hours_after_21": 3.5, "quiet_start": "01:10", "plug_uses": 2},
 "usual_hours": ["19:00", "01:00"], "weekdays": ["fri", "sat"], "confidence": 0.75}
```
Live match (`match_now(db, now) -> list[dict]`): `{"occasion_id", "similarity", "confidence", "since", "explains": ["habit-4 late"]}`.

**Decision** (`decisions` table: `id`, `ts`, `data`)
```json
{"id": "decision-12", "at": "2026-09-22T07:50:00", "kind": "prepare", "habit_id": "habit-1", "occasion_id": null,
 "action": {"tool": "update_device_capabilities", "target": "bulb", "args": {"body": {"type": "brightness", "value": 30}}},
 "expectation": null, "confidence": 0.81, "mode": "do", "status": "planned", "reason": "Waking up usually 07:59 on Mondays"}
```
`kind`: `prepare` (before a forecast habit) | `adapt` (because of an occasion, e.g. expect a late bedtime). `mode`: `do` | `suggest`. `status`: `planned` | `done` | `skipped` | `failed`.

## The parts and who builds them
Each part owns its files. Nobody edits another part's files; each exposes one function that the integration step wires in.

| Part | Builds | Owns | Exposes |
|---|---|---|---|
| **A. Habits** | Groups patterns into habits; finds rhythm (daily, weekdays, every n days, monthly, hourly); variation, trend, confidence; readings | `source/learn/grouping.py`, `source/learn/recurrence.py`, `tests/test_grouping.py`, `tests/test_recurrence.py`, `docs/habits.md` | `learn_habits(db, day) -> list[dict]` (new habits), `export_habits(db) -> dict` |
| **B. Anticipation** | Forecast per habit (weekday, n-day, monthly rhythm); decisions ahead of habits with the 0.75 rule; the agent reads habits, forecast, occasions and decisions | `source/learn/forecast.py`, `source/anticipate.py`, `source/context.py`, `source/prompts/agent.md`, `source/loop.py`, `tests/test_forecast.py`, `tests/test_anticipate.py`, `docs/anticipation.md` | `forecast(db, now)`, `plan_decisions(db, now) -> list[dict]` |
| **C. Occasions** | The usual day per hour and weekday; unusual episodes; grouping them into occasions by resemblance; live matching; adapt decisions (expectations) | `source/learn/occasions.py`, `tests/test_occasions.py`, `docs/occasions.md` | `learn_occasions(db, day)`, `match_now(db, now)`, `export_occasions(db)` |
| **D. Simulator** | Plants what the others must find: every-other-weekend and monthly habits, an hourly habit, parties (3–4 over 30 days), all in the answer key | `source/simulate.py`, `docs/simulator.md`, `tests/test_simulate.py` | new `Household` options and a preset that uses them |
| **E. Knowledge tab** | A second tab in the visualizer, no 3D: event types, patterns, habits (rhythm, window, steps, age, reliability, trend), occasions, tomorrow's forecast, decisions | `console/visualizer.html`, `source/simulate_web.py`, `tests/test_visualizer.py` | `GET /knowledge?household=&day=` |
| **Integration** (after) | Wire the functions into `source/learn/run.py` and `source/pipeline.py`; the namer names habits and occasions; README | `run.py`, `pipeline.py`, `namer.py`, `README.md` | — |

Missing pieces are read defensively: every part works (empty lists) when another part's table or module isn't there yet.
