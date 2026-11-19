# Anticipation: forecasts and decisions

Part B of [`habits-plan.md`](habits-plan.md). Alfred stops only identifying habits and starts acting ahead of them: it forecasts what each habit will probably do in the next 24 hours, plans small lamp changes before them, expects the unusual when the day looks like a past occasion, and tells the agent all of it. Plain code: every number comes from counts, never from the LLM.

| What | File | Call |
|---|---|---|
| Forecast | [`source/learn/forecast.py`](../source/learn/forecast.py) | `forecast(db, now, hours=24) -> list[dict]` (Forecast shape) |
| Decisions | [`source/anticipate.py`](../source/anticipate.py) | `plan_decisions(db, now) -> list[dict]` (Decision shape), `await run_due(db, now, actuator, types, fresh, state=None)` |
| The agent's view | [`source/context.py`](../source/context.py) | `baselines`: `habits`, `forecast`, `occasion_now`, `decisions` |
| The loop | [`source/loop.py`](../source/loop.py) | plans every 5 min, runs what is due every tick, before the triggers |

`now` is the home's clock time (a naive datetime in Lisbon time, as the learners use).

## The forecast
For each **active** habit (`habits` table, written by the grouper, [`grouping.py`](../source/learn/grouping.py)), every occurrence its rhythm makes due with an expected time in `[now, now + 24 h)`:

- **Due days** follow the grouper's own rule (`recurrence.is_on`): daily, the weekdays of a weekly habit, every n days from its `anchor` (with weekdays: every n/7 weeks, "every other weekend"), a day of the month, or the nth weekday of the month. A test checks the two agree day by day.
- **Probability** comes from the habit's readings (`habit_readings`), counted per **kind of day**: the same weekday for daily and weekly habits ("Mondays: 9 of 10"), the due days for every-n-days and monthly habits, and each time slot for hourly habits. Per kind of day:

  `probability = (hits + 2·p0) / (due days + 2)`, with `p0 = (occurrences + 1) / (opportunities + 2)`

  This is a beta estimate: the weekday's own count, pulled towards the habit's overall rate by two imaginary days, so "3 Mondays of 3" isn't read as certain. Hourly slots use plain Laplace instead, `(hits + 1) / (days + 2)`, because day and night slots differ too much to share a prior. Occurrences under 10 % are left out.
- **What counts**: days run from the habit's `first_seen` to the last day the learners looked at (the latest day any habit was seen, before today). Days after that are neither hits nor misses. A holiday or an outage isn't a miss either. When the calendar has more days than the grouper's `opportunities` (its counted days), the days on which no habit happened at all go first, and anything still over is shared out in proportion. A habit with no readings has only its overall rate. Readings after `now` are never used, and an occurrence that already has a reading (today's has happened) isn't forecast again.
- **Expected time** is `window.usual`, plus the weekday shift (`variation.by_weekday`, which the grouper only reports when it beats chance, e.g. "50 min later on Saturdays"), plus the drift (`trend.minutes_per_week`, followed from the middle of its history, at most 60 min). A reading's `day` is the habit's own day, so a bedtime just after midnight still counts for its evening.
- **Window**: the habit's own `earliest`–`latest` when no weekday is shifted. When some are, those span every day (weekends included), so the window is ± twice the spread (`variation.spread_min`) instead.
- **Basis**: the counts in plain words, e.g. `Saturdays: 3 of 3 since 1 Sept, about 50 min later than usual`.

```json
{"habit_id": "habit-1", "date": "2026-09-22", "probability": 0.97, "expected": "07:59",
 "window": ["07:33", "08:27"], "confidence": 0.81, "basis": "Tuesdays: 3 of 3 since 1 Sept"}
```
`date` is the calendar date of the expected time.

## Confidence
Computed by code at every step, always in [0, 1]:

| Of | Formula |
|---|---|
| A forecast | `probability × the habit's confidence` (the grouper's: hit rate × evidence × timing). Without one: `p0 × n / (n + 1)` |
| A `prepare` decision | `forecast confidence × the light step's share × (1 − occasion confidence)`, capped at `n / (n + 1)` (n = the habit's occurrences). The occasion factor applies only when an occasion going on now explains this habit, for its occurrences in the next 12 h |
| An `adapt` decision | the match's confidence, capped at `e / (e + 1)` (e = the occasion's past episodes) |

The share is how often the light is part of the habit: the highest `share` among its light steps. The `n / (n + 1)` cap is the evidence factor: one past example allows at most 0.5, so it can only be a suggestion. A capped value is the same whether or not the other part already applied it.

## Do or suggest
- **0.75 or more: `do`.** Code carries it out at its time, through the guard, and reports it.
- **Below 0.75: `suggest`.** Shown to the agent and logged, never sent. The agent may mention it but doesn't carry it out unless a person asks.
- **Below 0.4**, no new decision is made at all (it would be noise). A decision already planned keeps being updated, so it can drop to `suggest`.

## The action list
A decision does at most one guarded write, and only to the lamp. Lamp actions come only from habits whose steps include the light, read from each step's pattern (the `sensors` table):

| The habit's light step | Example | Decision | When | Only if |
|---|---|---|---|---|
| The light comes on: a lamp kind, a lamp part of a link, or the end of a quiet "until bulb" | Waking up; the evening light | **Soft lamp**: `brightness 30` | 10 min before the light usually comes on | the lamp reports and is **off** |
| The light goes off and a quiet of 4 h or more starts ("after bulb") | Going to sleep | **Dim lamp**: `brightness 20` | 30 min before | the lamp reports, is **on**, and is brighter than 20 % |
| An occasion explains a departure (`adapt`) | Dinner with friends | **Expectation**, e.g. "Going to sleep late is normal tonight" | while it matches | no device action |

- A link step's `offset_min` is when its second part happens (the grouper's convention), so a lamp in a link's first part comes on `lag_min` earlier.
- If two habits ask for the same lamp action within 20 min, only the surer one is planned. Hourly habits get no lamp actions (several a day on a guess is a nuisance). Neither do habits the namer marked `hidden` (a duplicate card).
- "Warm" would need a second write (`temperature`), and which end of this bulb's 0–100 scale is warm hasn't been checked. So v1 only dims. Whether a `brightness` write switches this bulb on hasn't been checked either. Either way the soft lamp is harmless: the lamp comes on soft, or it is soft when someone switches it on.

## Decisions: stored, deduplicated, re-planned
`decisions` table: `id`, `ts` (when it is due, epoch ms), `data` (the Decision exactly as in the plan), plus `key` and `meta`, which only this module reads.

- **One per habit or occasion per occurrence.** `key` is `prepare:<habit>:<the habit's day>` or `adapt:<occasion>:<the evening it started>`, and it is unique. Planning again finds the same decision and keeps its id.
- **Re-planned until it runs.** A `planned` decision follows the latest forecast (its time, confidence and mode). One that ran (`done`, `skipped`, `failed`) is never changed.
- **Dropped when no longer expected.** A planned `prepare` decision whose occurrence left the forecast (the habit faded or already happened) becomes `skipped`, "no longer expected". This happens only after a forecast that worked: a broken one drops nothing.
- `meta` keeps the recipe, the lamp state it needs (`only_if`), the moment after which it is pointless (`until`), and what happened (`ran`: when, why, and the guard's records).

## How the loop runs them
Each tick, after the poll is stored and before the triggers ([`loop.py`](../source/loop.py)):

1. **Every 5 minutes**, `plan_decisions(store.db, now)`.
2. **Every tick**, `run_due(...)` on the `do` decisions whose time has come:
   - an expectation (no action) is marked `done`;
   - otherwise it is `skipped` if it is more than 15 min late or past its moment, if the habit already happened today (`grouping.habit_today`: `done`, `partly` or `now`), if no lamp is reporting, or if the lamp isn't in the state the recipe needs (read from the poll just made);
   - otherwise it goes through `actions.run_actions` and `guard.guarded_write`. `sent` becomes `done` ("sent and confirmed", or "sent, but the lamp didn't confirm it"), `blocked` becomes `skipped`, and an error becomes `failed`.
3. Writes are stored like hard rules (`rule_runs`, trigger `decision:<id>`) and added to `rules_just_ran`, so an agent waking in the same tick knows its `state` was read before them.

Everything is wrapped: a failure in planning or running is logged (`anticipation failed this tick`) and the tick goes on to the triggers, including smoke. Replays and `--stub` use the `DryRunActuator`, so a replay never touches the kit.

## Safety
- **Only the lamp.** `run_due` offers the guard only the bulbs as writable devices, so a decision can never change the plug, on or off. The guard's own rules still apply to every write: allowed fields and values, plug-on blocked, read-back. Nothing bypasses it.
- **Never on a guess below 0.75**, never late, never on a stale lamp, never against what the lamp is doing now (it doesn't dim a lamp someone just switched on, or switch one on that is already on).
- **Confirmed means confirmed**: only a read-back with the new value and a newer report counts.
- **Occasions calm things down.** They make no device action, and they make the habits they explain less sure, so the usual bedtime dim doesn't fire during a dinner party.

## What the agent reads
`baselines` in the context ([`context.py`](../source/context.py)), `{}` until something is learned:

| Key | Holds |
|---|---|
| `habits` | each active habit: `name` (or "Habit 3"), `rhythm` (`recurrence.text`), `window`, `confidence`, `today` (from `grouping.habit_today`, or else the status of its first step's pattern from `today.baselines`), `at`, and the namer's `means`, `if_missing` and `importance` when present. Before any habit exists: the learned patterns from `today.baselines`. Cards the namer hid are left out |
| `forecast` | the next 24 h, each with its `habit` name |
| `occasion_now` | `match_now`'s matches, with the occasion's `name` |
| `decisions` | from midnight to 24 h ahead, with `outcome` once they ran |

The prompt ([`prompts/agent.md`](../source/prompts/agent.md)) teaches the agent to weigh departures by importance, confidence and the forecast, to never alarm when an occasion explains one, to say "looks like", to leave `do` decisions to code and only mention `suggest` ones, and to act only on the lamp itself.

## Reading the other parts
Every other part is read defensively: a missing table or module only leaves its piece empty.

- Habits: the `habits` and `habit_readings` tables, and `source.learn.grouping.habit_today`.
- Occasions: the `occasions` table and `source.learn.occasions.match_now`.
- Patterns: the `sensors` table and `source.learn.today.baselines`.

## Run it
```
uv run python -m source.learn.forecast --db data/sim/rosa/home.sqlite3 --at 2026-09-21T22:00
uv run python -m unittest tests.test_forecast tests.test_anticipate -v
```
On 28 simulated days of `rosa`, with the grouper's habits, Monday's plan at 06:00 is: the evening lamp (`do`, 0.81), the bedtime dim (`do`, 0.79), and the waking-up soft lamp (`suggest`, 0.61; the lamp is part of only 69 % of the mornings).
