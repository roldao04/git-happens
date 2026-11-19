# Habits: patterns that go together

Part A of [`habits-plan.md`](habits-plan.md). Plain code and statistics, no LLM, like the [learners](learners.md).

A **pattern** is one thing at a consistent time ("light → kettle about 07:59", "the end of the night's quiet about 07:59", "light → door about 07:50"). A person calls all three one **habit**: Waking up. A habit is a group of patterns that recur together, in some rhythm, with variation.

| Step | File | How |
|---|---|---|
| Together | `source/learn/grouping.py` | Two groups of patterns are one habit when, on most of the rarer one's days, they start within a short window, more often than chance in this home allows. Chance: the other one landing that close if it followed the home's activity across the day (the links' "rhythm" null). The window is measured: 10, 20, 30, 60 or 90 min (the links' lag windows, then 2 and 3 × `MAX_LAG_MIN`), whichever beats chance most. Groups merge strongest first until nothing beats chance. A pattern with nothing to join is a habit of one step |
| Every n hours | `recurrence.evenly_spaced` | Three or more patterns of the same thing at evenly spaced times are one habit. Chance: the slots between the first and last falling that close to an even grid anywhere between them |
| Steps | `grouping.py` | In order of when they happen. `offset_min`: minutes after the habit starts (for a link, when its second part happens: the kettle in light → kettle). `share`: how often the step is there when the habit happens |
| Rhythm | `source/learn/recurrence.py` | The simplest rhythm that explains its days, below. A finer rhythm must beat its parent on the home's own counted days |
| Window, variation | `recurrence.weekday_shift` | Days that start later or earlier (single days, else work days / weekend) when their times differ more than chance allows (Mann-Whitney). A day the shifted ones hid (Sunday beside a later Saturday) is tested again against the days left. `usual` is the median start on the other days, `by_weekday` the shift of the days that differ, `spread_min` what is left once the shift is taken out |
| Windows by weekday | `recurrence.weekday_windows` | One window per group of days whose times differ: the shifted days, those alike among themselves sharing one (Saturday and Sunday: the weekend), and the rhythm's other days. One window with all its days when nothing is shifted |
| Person | `grouping._person` | The one known phone that arrives or leaves within the habit on at least 60 % of its days |
| Trend | `recurrence.drift` | Minutes per week it moves (later > 0) when the slope beats chance, else 0 |
| Confidence | `grouping.confidence` | Below |
| Stops | `grouping.py` | `fading` once its absence on the days its rhythm expects it beats chance (rule of succession, as for patterns) |

**Rhythms**, simplest first. Each finer one is kept only if the habit's days crowd onto its days more than chance allows (an exact test: a habit happens at most once a day, so its days are drawn without replacement from the parent's days), corrected for the candidates tried, with at least `MIN_OCCURRENCES` days of its own:
```
daily ─┬─ weekly (single weekdays, else work days / weekend) ─┬─ every 2, 3 or 4 weeks ("every other weekend")
       │                                                      └─ the nth weekday of the month
       ├─ every n days (2 to 13)
       └─ monthly: a day of the month, or the nth weekday of the month
```
Holidays and outages aren't counted days (`run.counted_days`), so they are never misses. `text` is the sentence the UI shows: "every day", "every weekday", "every weekend", "every Thursday", "every other Saturday", "every 3 days", "on the 1st of the month", "on the first Sunday of the month", "every 3 hours from 08:00 to 20:00". `recurrence.is_on(rec, day)` says whether a rhythm expects the habit on a day (for the forecast).

**Confidence**, in [0, 1], computed by code:
```
confidence = hit rate × evidence × timing
  hit rate = occurrences / opportunities      how often it happens when its rhythm says it should
  evidence = n / (n + 1), n = occurrences     one example gives at most 0.5
  timing   = max(0, 1 − spread / home spread) 1 when it always starts at the same minute, 0 when its start
                                              (weekday shifts taken out) is as scattered as the home's activity
```
`home spread` is the circular spread of the clock times of all the home's activity (people's devices). Rosa's Waking up: 18/19 × 18/19 × (1 − 11/308) = 0.86.

- **Knobs** (`source/learn/__init__.py`): `MIN_OCCURRENCES`, `ALPHA`, `MAX_LAG_MIN`. Nothing else is fixed: chance, windows, shifts and spreads come from the home's data. "Most" means more than half, as in the rest of the learners.
- **Stable ids:** a habit found today is an existing one when most of its occurrences start at the same moments (within `MAX_LAG_MIN`), or it shares most of its patterns (a habit that moved). Ties go to the one sharing more, then the oldest. `habit-N`, `learned_at` and a name are kept; when most of its patterns changed, the LLM's name and card are cleared so the namer names it again (a person's name is never touched). A habit is never deleted: one whose patterns now belong to another is `merged` (two habits learned apart turned out to be one, as sensors merge), otherwise it is `fading`.
- **Readings** (`habit_readings`): one per day it happened. `start` is its first step, `end` its last step (a habit of one step keeps its own end: asleep until 07:59), `steps_seen` the pattern ids in step order. Days before a habit's current first day are kept as history.
- **Now** (`habit_today`): built on each pattern's status from `today.py`. The usual steps (share > 1/2) decide: `done` all happened, `partly` some did and the rest are late, `now` some did and the rest may still come (or a stretch is going on: asleep), `expected`, `late`, `not_today` (its rhythm skips today), `unknown` (not watched). Each has `steps_done`.

## People and weekday windows (C3 of [`demo-plan.md`](demo-plan.md))
```json
{"id": "habit-4", "person": {"phone": "c-8f3a", "name": "Ana's phone"},
 "windows": [{"days": ["mon","tue","wed","thu","fri"], "usual": "18:40", "earliest": "18:25", "latest": "18:55"}]}
```
- **Phones** are read from their happenings, `phone:<client id>.connected` (C2): a happening's start is an arrival, its end a departure (unless it was cut by a hole in the polls).
- **`person`**: a known phone's arrival or departure falls within the habit (its first step to its last, give or take 10 min, the links' shortest lag window) on at least 60 % of its occurrences (the contract's share). If two known phones qualify (a couple who always come home together) it is `null`: the habit is nobody's alone. **Known** means the `devices` table names it (C1: a guest's phone has no name); a phone the table doesn't list counts as known (hand-made data), since only a phone seen on most of a habit's days can qualify anyway.
- **One rhythm per person.** Groups whose patterns are about different phones (a kind of a phone's signal, or a link with a phone in it) are never joined, so "Ana gets home" at 18:40 and "João gets home" at 19:30 stay two habits. And two groups about the same one phone that are alternatives (on most of the rarer one's days the other doesn't happen) are one habit: João home at 19:30, and on Tuesdays at 21:00, which the pattern learner finds as two patterns.
- **`windows`**: from `weekday_windows`, over the same unwrapped start times as `window` (so a habit around midnight works). `usual` is each group's median, `earliest` and `latest` its extremes. The days of the main window are the rhythm's days (`recurrence.weekdays`, all seven for a daily habit) minus the shifted ones. `window` (overall) and `variation.by_weekday` are kept as before.
- The forecast (`forecast.py`) uses a habit's `windows` when it has them: the day's own `usual` and `earliest`–`latest`, instead of the usual time plus the weekday shift and ± twice the spread.

## What the demo house taught (part F of [`demo-plan.md`](demo-plan.md))
On 60 days of the demo house the first version found "Waking up" anchored on the room warming (05:38–07:16), no weekend window, Ana's arrival with no person and João's Tuesdays inside the TV habit. Four changes, each plain code:
- **No steps the house does by itself.** A pattern with a slow part (the room's temperature drifting, the thermostat's own `heatingState`: `SLOW_CAPABILITIES`) stays a pattern but is no habit's step (`grouping._slow`). A warming that began before anyone woke only blurred when the habit begins; its cause, the person at the thermostat, is a step, and `warmup.py` reads the heating itself.
- **A link reaches as far as its second part's start.** Door → lamp ends when the lamp goes on, not at bedtime when it goes off. Before, the evening lamp stretched "Ana gets home" over João's arrival, two phones qualified and the habit had no person.
- **One person's arrival joins what the house does then only on most days of both** (`_together(..., mutual=True)` when exactly one side is about a phone). João home at 21:00 on Tuesdays is not a step of the TV at 21:00 every day; "Ana gets home" still takes the door and the thermostat, there on most of her days.
- **Alternatives are one habit** (`grouping._alternatives`): two groups that are mostly the same kinds of pattern (the end of the night's quiet, light → kettle), on mostly other days, within 3 h of each other: waking up at 07:08 on weekdays and 08:53 at weekends. `windows` then say when, which day. The couple's weekend morning is now a window of its weekday habit, not a habit of its own.
- **A weekday it never happens on leaves the rhythm** (`recurrence._without_absent`): of a chosen weekday group, a weekday whose hits are far below the others' rate (binomial lower tail with the rule of succession's rate, corrected for the weekdays tried). João: "every Monday, Tuesday, Thursday and Friday".

Day 60 of `demo` (seed 1): Waking up every day, weekdays 07:08 (06:42–07:26), weekends 08:53 (08:25–09:15), confidence 0.90 · Ana gets home every weekday 18:38 (18:17–18:53), Ana's phone, 0.94 · João gets home Mon, Tue, Thu, Fri 19:28 (19:08–19:44), Tuesdays 20:54 (20:45–21:01), João's phone, 0.93 · TV every day 20:56 · washing machine Mondays 19:35 · bedtime 23:29 · warm-up 4.9 °C/h (1 °C in about 12 min), 100 samples.

## Warm-up (C4 of [`demo-plan.md`](demo-plan.md))
`source/learn/warmup.py`, plain code like the rest:
```python
warmup(db, now) -> {"rate_c_per_h": 4.8, "loss_c_per_h": 0.6, "samples": 23, "confidence": 0.82, "as_of": "2026-11-19"}
lead_minutes(db, now, from_c, to_c) -> float | None   # None until there are 5 samples
```
- **Samples**: each heating stretch of the last 30 days (the thermostat's `heatingState` happenings with value `on`, not cut by a hole in the polls) gives one: the room temperature the thermostat reported (its `temperatureValue` changes, read from the `events` table, since a slow signal's happenings don't keep its values) from the stretch's start to the first time it reached its highest, if it rose over at least 5 min. Time spent holding a set temperature afterwards doesn't count.
- **`rate_c_per_h`**: the samples' median, so how fast the house really warms, its losses included. **`loss_c_per_h`**: the median fall per hour between two stretches at least an hour apart, when the temperature fell.
- **`confidence`** = n/(n+1) × (1 − spread / rate), the spread being the samples' MAD × 1.4826.
- **`lead_minutes`** = max(0, to − from) / rate × 60. It assumes a steady rate: the real rise slows near the target, so a long warm-up is a little under-estimated.
- `as_of` is `now`'s date. Both never raise; with nothing to read, `samples` is 0 and the rates `null`.

## Simulator, 21 days
- **rosa:** light → kettle, the end of the night's quiet and light → door are one habit, Waking up: every day, usually 07:45 (07:19–08:27), 42 min later on Saturdays and 39 on Sundays; the quiet ends, the door 8 min later, the kettle at 19 min; 18 of 19 days; confidence 0.86. The evening lamp (and the quiets it ends) is another habit, the night's quiet another.
- **couple:** a weekday habit, every weekday, usually 06:46: up, coffee at 10 min, the house quiet at 51, out at 70; 14 of 14; confidence 0.92. Its weekend variant (08:26: up, coffee at 8 min) is now its weekend window: one habit, every day. Coming home is every weekday at 17:03.
- The hand-made 14 days (`tests/fixtures/happenings-14d.jsonl`): the morning (up 07:00, door → kettle about 07:56) is one daily habit.

## Use
```python
from source.learn.grouping import learn_habits, export_habits, habit_today
learn_habits(db, day)        # after learn_day(db, day), at each day end: the habits first learned that day
export_habits(db)            # {"habits": [...], "readings": [...]}
habit_today(db, now)         # [{"habit_id", "status", "at", "usual", "steps_done"}] for the active habits
```
Each works (and returns nothing) before the tables exist. `warmup(db, now)` and `lead_minutes(db, now, from_c, to_c)` (`source/learn/warmup.py`) read the stored happenings and events directly, at any moment. The tables, `habits (id, status, data)` and `habit_readings (habit_id, day, start, end, steps_seen)`, are made by `learn_habits` on the store's db.
```
uv run python -m unittest tests.test_grouping tests.test_recurrence -v
```

## For the integration
- Call `learn_habits(db, day)` right after `learn_day(db, day)` (`run.learn_day` or the pipeline's day end). `grouping.py` imports `run.py` only inside a function, so `run.py` can import it at the top.
- Relearning from scratch (`run.py --store`, `storage.reset_learned`) must also clear `habits` and `habit_readings`, or the old ids and dates come back.
- `export_habits` belongs next to `storage.export` (dashboard, recording, knowledge tab), `habit_today` next to `sensor_today`.
- Statuses are `active`, `fading` and `merged`; show and forecast the active ones. `person` and `windows` appear on habits learned from now on; an older stored habit lacks them until the next `learn_habits`. The namer may add fields to a habit's data: they are kept until the habit changes.

## Known limits
- **Short histories:** "every other weekend" needs about 6 weekends (5 read as "every weekend"), a monthly rhythm about 5 months, single weekdays (Monday, Wednesday, Friday) about 10 weeks (before that they read as "every weekday" with a lower hit rate).
- **Busy hours:** near the home's busiest hour, joining needs more days. At 21 days rosa's quiet after the morning lamp (08:31) is still a habit of its own.
- **Near, not caused:** two routines within 90 min on the same days are one habit (couple's waking up and leaving for work are one weekday morning).
- **Alternatives** are joined only when they share most of their kinds of pattern and are within 3 h: the same moment in different forms (the evening lamp ending one kind of quiet or another) stays two habits.
- **Borderline groups** can merge and split for a few days while the evidence builds; their ids survive.
- **Every n hours** needs the pattern learner to find three or more of the slots, and combines only with a daily or weekly rhythm. The nth weekday is the first to fourth (no "last Friday").
- Everything is worked out again from the whole history each day.
