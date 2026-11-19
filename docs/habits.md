# Habits: patterns that go together

Part A of [`habits-plan.md`](habits-plan.md). Plain code and statistics, no LLM, like the [learners](learners.md).

A **pattern** is one thing at a consistent time ("light → kettle about 07:59", "the end of the night's quiet about 07:59", "light → door about 07:50"). A person calls all three one **habit**: Waking up. A habit is a group of patterns that recur together, in some rhythm, with variation.

| Step | File | How |
|---|---|---|
| Together | `source/learn/grouping.py` | Two groups of patterns are one habit when, on most of the rarer one's days, they start within a short window, more often than chance in this home allows. Chance: the other one landing that close if it followed the home's activity across the day (the links' "rhythm" null). The window is measured: 10, 20, 30, 60 or 90 min (the links' lag windows, then 2 and 3 × `MAX_LAG_MIN`), whichever beats chance most. Groups merge strongest first until nothing beats chance. A pattern with nothing to join is a habit of one step |
| Every n hours | `recurrence.evenly_spaced` | Three or more patterns of the same thing at evenly spaced times are one habit. Chance: the slots between the first and last falling that close to an even grid anywhere between them |
| Steps | `grouping.py` | In order of when they happen. `offset_min`: minutes after the habit starts (for a link, when its second part happens: the kettle in light → kettle). `share`: how often the step is there when the habit happens |
| Rhythm | `source/learn/recurrence.py` | The simplest rhythm that explains its days, below. A finer rhythm must beat its parent on the home's own counted days |
| Window, variation | `recurrence.weekday_shift` | Days that start later or earlier (single days, else work days / weekend) when their times differ more than chance allows (Mann-Whitney). `usual` is the median start on the other days, `by_weekday` the shift of the days that differ, `spread_min` what is left once the shift is taken out |
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

## Simulator, 21 days
- **rosa:** light → kettle, the end of the night's quiet and light → door are one habit, Waking up: every day, usually 07:45 (07:19–08:27), 42 min later on Saturdays and 39 on Sundays; the quiet ends, the door 8 min later, the kettle at 19 min; 18 of 19 days; confidence 0.86. The evening lamp (and the quiets it ends) is another habit, the night's quiet another.
- **couple:** a weekday habit, every weekday, usually 06:46: up, coffee at 10 min, the house quiet at 51, out at 70; 14 of 14; confidence 0.92. Its weekend variant is a habit of its own, every weekend, usually 08:26: up, coffee at 8 min; 6 of 6; 0.84. Coming home is every weekday at 17:03.
- The hand-made 14 days (`tests/fixtures/happenings-14d.jsonl`): the morning (up 07:00, door → kettle about 07:56) is one daily habit.

## Use
```python
from source.learn.grouping import learn_habits, export_habits, habit_today
learn_habits(db, day)        # after learn_day(db, day), at each day end: the habits first learned that day
export_habits(db)            # {"habits": [...], "readings": [...]}
habit_today(db, now)         # [{"habit_id", "status", "at", "usual", "steps_done"}] for the active habits
```
Each works (and returns nothing) before the tables exist. The tables, `habits (id, status, data)` and `habit_readings (habit_id, day, start, end, steps_seen)`, are made by `learn_habits` on the store's db.
```
uv run python -m unittest tests.test_grouping tests.test_recurrence -v
```

## For the integration
- Call `learn_habits(db, day)` right after `learn_day(db, day)` (`run.learn_day` or the pipeline's day end). `grouping.py` imports `run.py` only inside a function, so `run.py` can import it at the top.
- Relearning from scratch (`run.py --store`, `storage.reset_learned`) must also clear `habits` and `habit_readings`, or the old ids and dates come back.
- `export_habits` belongs next to `storage.export` (dashboard, recording, knowledge tab), `habit_today` next to `sensor_today`.
- Statuses are `active`, `fading` and `merged`; show and forecast the active ones. The namer may add fields to a habit's data: they are kept until the habit changes.

## Known limits
- **Short histories:** "every other weekend" needs about 6 weekends (5 read as "every weekend"), a monthly rhythm about 5 months, single weekdays (Monday, Wednesday, Friday) about 10 weeks (before that they read as "every weekday" with a lower hit rate).
- **Busy hours:** near the home's busiest hour, joining needs more days. At 21 days rosa's quiet after the morning lamp (08:31) is still a habit of its own.
- **Near, not caused:** two routines within 90 min on the same days are one habit (couple's waking up and leaving for work are one weekday morning).
- **Alternatives aren't joined:** two patterns that are the same moment in different forms, never on the same day (the evening lamp ending one kind of quiet or another), stay two habits.
- **Borderline groups** can merge and split for a few days while the evidence builds; their ids survive.
- **Every n hours** needs the pattern learner to find three or more of the slots, and combines only with a daily or weekly rhythm. The nth weekday is the first to fourth (no "last Friday").
- Everything is worked out again from the whole history each day.
