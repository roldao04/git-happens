# Occasions: rare evenings, recognised by resemblance

Part C of [`habits-plan.md`](habits-plan.md). An **occasion** is a rare departure from the usual day, such as friends to dinner: many door openings, the light on until 01:00, a late bedtime. The first one is only "an unusual evening". The next one that looks like it is the same occasion, and from then on Alfred can tell it is happening again while it is still under way. Plain code and statistics, no LLM. Every cut-off is measured against the home's own history; the only fixed numbers are the knobs in `source/learn/__init__.py` (`ALPHA = 0.01`, `MIN_OCCURRENCES = 5`).

| Step | What | How |
|---|---|---|
| 1. The usual day | What the home normally does in each stretch of the day | Minute-by-minute features of every counted day, cut at the home's quietest hour |
| 2. Episodes | Stretches that are unusual on several features at once | Each stretch of whole hours against the same stretch on the usual days |
| 3. Occasions | Episodes that look alike | Distance outside what the occasion has shown, in noise units; confidence `n/(n+1) × tightness` |
| 4. Live | "This looks like occasion-1, since 19:10" | Today's unusual stretch so far against each occasion's episodes, over the same clock hours |

Code: `source/learn/occasions.py`. Tests: `tests/test_occasions.py`.

## 1. The usual day
- **The day starts at the home's quietest hour**: the middle of the longest run of hours with the fewest starts and ends of activity (rosa: 03:00 or 04:00). A party that goes on past midnight stays one evening.
- **Features**, minute by minute, from the happenings:
  - door openings: `contactSensor` happenings;
  - light-on minutes: `bulb.power` on (several bulbs add up);
  - plug uses by kind: `plug.instantPower` happenings, one feature per kind from the grouper (`?` for none);
  - camera motion and sound events, when the camera reports any;
  - guests' phones: arrivals (`phone:<client id>.connected` happenings, C2) of phones that aren't the household's, a count like door openings. A phone is a guest's when the `devices` table gives it no name (C1); one the table doesn't list is a guest's if it came on fewer than `MIN_OCCURRENCES` days. Five guests arriving where none ever do is far beyond the home's history, so a gathering is matched live from its first arrivals;
  - awake minutes: from the end of last night's quiet to the start of tonight's. Tonight's quiet is the longest quiet stretch (people's devices all at rest, as in the habit learner) that starts in the second half of the day. This is "how late the night's quiet starts".
- **What counts as usual:** only counted days (`run.counted_days`: watched, someone in), so holidays and outages are left out. Minutes with no poll for 30 minutes are left out, and so are stretches that hold an earlier episode, so a party never becomes part of the usual day.
- **Weekdays and weekends** are pooled, unless for that stretch and feature the weekend days differ from the weekdays (Mann-Whitney U at `ALPHA`) and the day's own kind has at least `MIN_OCCURRENCES` days.
- **A baseline comes first:** a stretch is only judged once it has `MIN_OCCURRENCES` usual days.

## 2. Episodes
Every stretch of whole hours inside one day (300 of them) is compared with the same stretch on the usual days.
- **Counts** (door openings, plug uses, camera events): a Poisson rate with Jeffreys' prior, given the usual days' total `T` over `n` days. The chance of at least `x` is the negative binomial predictive `NB(T + ½, n/(n+1))`. Ten door openings where 0.3 are usual is about 1 in 10⁸; one door opening at an hour that never had one is about `0.5/(n+1)`.
- **Minutes** (light, awake) are ranked (a conformal p-value): `(1 + usual days with at least as much) / (n + 1)`. With 20 days, the light on later than ever is 1 in 21, never rarer.
- **Combined** with Fisher's method: `−2 Σ ln p` read as a chi-square with 2 degrees of freedom per feature.
- **An episode** is a stretch where:
  - at least two features are beyond the home's history (chance at most `1/(n+1)`): unusual on several features at once, so one bouncing sensor can't make an occasion;
  - and the combined chance, times the 300 stretches tried, is below `ALPHA`.
- **Edges:** the most unusual stretch tends to reach out to ordinary activity next to it, such as a door at 15:32 before a party. End hours with no feature below `ALPHA` on their own are dropped, as long as the stretch's chance stays within a factor `1/ALPHA` of the best one's. The episode then runs from the first happening that starts in it to when what was on in it went back to rest (the lamp at 01:10).
- **When:** a day is judged once it is over. `learn_occasions(db, day)` at the end of `day` judges `day − 1`, whose day ended at the quietest hour of `day`. Several episodes a day are found one by one, most unusual first.

**Fingerprint** (per episode; for an occasion, the median, and the circular mean of `quiet_start`):
`door_openings`, `light_hours_after_21` (light-hours between 21:00 and the episode's end), `quiet_start` (when tonight's quiet began), `plug_uses`, `camera_events` when the camera reports, and `guest_phones` when the home has a router (C5): the most guests' phones connected at once during the episode, from `router.guestPhones`'s peak (its happenings' `peak`), or else from the guests' own phones that overlap. For an occasion it is the median over its episodes, like the others. Guests aren't one of the numbers resemblance compares: the doors and the hours already tell a gathering apart, and a gathering of 4 is like one of 6.

## 3. Occasions
- **Resemblance** compares when the episode started and ended and when the night's quiet began (hours into the day; one hour is one unit, the usual day's resolution), and its door openings, light-hours and plug uses (Poisson noise: `|x − y| / √(x + y + 1)`). Each number is measured outside the range the occasion's episodes have shown, minus one unit, so differences within the noise count as none. The squares are added and read as a chi-square with one degree of freedom per number. The result, `similarity`, is 1 for "could be one of them" and near 0 for "clearly not".
- A new episode **joins** the most similar occasion if `similarity ≥ ALPHA` (it can't be told apart beyond chance). Otherwise it is a new occasion, `occasion-N`; ids are never reused, except when a replay forgets the day an occasion was first seen.
- **confidence** = `n/(n+1) × tightness`. `n` is the number of episodes, and `tightness` is the mean leave-one-out similarity (1 for a single episode). One example gives at most 0.5, so only a suggestion; three alike parties give 0.75.
- With 2 episodes or more: **`usual_hours`** (circular mean of starts and ends) and **`weekdays`** (the weekdays its episodes fell on; too few episodes for a test of preference).
- **`explains`** lists the learned patterns an occasion moves, on most of its episodes:
  - the night's quiet (bedtime) and its end (waking up): the quiet patterns whose range holds the home's usual bedtime or wake-up;
  - any other pattern due during the episode that happened outside its range.

  `late` means after its `late_after` (later than ever seen), and `early` means before its `earliest`. An absence isn't reported. Each entry is written `habit-N late` once part A's `habits` table maps the pattern to a habit, otherwise `pattern-N late`. `changes` has the detail: `{"pattern", "effect", "usual", "seen"}`.

## 4. Live
`match_now(db, now)`, cheap enough for every poll (about 25 ms on 21 days, mostly reading the polls):
- **Something is under way** when a stretch of today so far is unusual on one feature (its smallest chance × the number of features) or on several (Fisher), × 2 for asking both, × the start hours tried, below `ALPHA`. Learning asks for two features; live, one is enough, because looking like a known occasion is the second line of evidence. Guests arriving at 20:00 are matched long before the light is on later than ever.
- **Where it began:** the start hours of unusual stretches that are themselves unusual on their own (an ordinary 11:50 door is no beginning). Each candidate is compared from its first happening up to now with every episode of the occasion, over the same clock window: start, door openings, light-hours and plug uses by then. The best alignment wins. `since` is that first happening.
- `confidence = similarity × the occasion's confidence`. Occasions it is beyond chance unlike (`similarity < ALPHA`) are left out.
- **`shifts`** (C5): how the occasion moved habits the next day in its past episodes, dated the day after today's (`[{"habit_id", "date", "minutes"}]`). For each past episode whose next day is over, and each active habit (not hourly) due on the next calendar day: its start that day is the median, over its usual steps (share > ½), of the happening nearest its usual moment (its weekday's usual time, from `windows` or the weekday shift, plus the step's offset), looked for from 3 h before to 6 h after it and after the episode ended, less the offset. A step that is the end of the night's quiet is read from the night after the episode; a quiet's start (bedtime) is left out (`explains` covers it). A habit is listed when, on most of the episodes, it moved the same way by more than twice its `spread_min`; `minutes` is the median shift. So the morning after a Friday gathering: Waking up, +90.
- **`shifts_now(db, now)`** gives `[{"occasion_id", "confidence", "habit_id", "date", "minutes"}]` for the occasion under way **and** the one matched at the last minute of the day before. The morning after, the stretch under way is ordinary and matches nothing, yet the party of the night before still moves this morning's waking up. The forecast (`forecast.py`) applies a shift once its match's confidence is 0.5 or more (the occasion wake's bar, C8): `expected` and `window` move, and the entry gains `"shifted": {"occasion_id", "minutes", "before": {"expected", "window"}}` (C6).
- It reads the happenings stored up to `now`. An on/off signal that is away from rest right now (the lamp on since 18:30) is read from `events`, because a happening is only stored once it ends.

## Shapes
Tables on the same SQLite file, created by `occasions.py`: `occasions (id, data)` and `occasion_episodes (occasion_id, start, end, features)`.
```json
{"id": "occasion-1", "name": null, "named_by": null, "episodes": 3,
 "fingerprint": {"door_openings": 10, "light_hours_after_21": 4.2, "quiet_start": "01:14", "plug_uses": 1, "guest_phones": 5},
 "usual_hours": ["19:08", "01:14"], "weekdays": ["tue", "fri", "sat"], "confidence": 0.75,
 "explains": ["pattern-4 late"],
 "changes": [{"pattern": "pattern-4", "effect": "late", "usual": "23:07", "seen": "01:14"}],
 "first_seen": "2026-09-11", "last_seen": "2026-09-19", "learned_at": "2026-09-12"}
```
Live match: `{"occasion_id": "occasion-1", "similarity": 1.0, "confidence": 0.75, "since": "2026-09-21T19:10:00", "explains": ["pattern-4 late"], "shifts": [{"habit_id": "habit-1", "date": "2026-09-22", "minutes": 90}]}`.

An episode's `features` holds `day`, `fingerprint`, `door`, `light` (hours), `plug`, `quiet_at`, `unusual` (the features beyond history), `p` (combined chance), `stretch` (its whole hours) and `changes`.

| Function | Does | Returns |
|---|---|---|
| `learn_occasions(db, day)` | At each day end, after the learners. Re-running a day first forgets what that day and later ones found, so a replay gives the same ids | the occasions first seen that day |
| `match_now(db, now)` | Live; never raises | matches, most confident first |
| `shifts_now(db, now)` | Live: what the occasion under way, or the night before's, moves; never raises | `[{"occasion_id", "confidence", "habit_id", "date", "minutes"}]` |
| `export_occasions(db)` | For the dashboard | `{"occasions": [...], "episodes": [...]}` |

Each returns empty when the tables or data are missing.

## On the simulator
- **Ordinary homes:** no occasions on 21 days of rosa. On each day, the most unusual stretch's chance × 300 is between 60 and 160, where an episode needs less than 0.01. None either on 30 days of rosa (two seeds), couple, students and two made-up houses, or on rosa with a holiday and two outages.
- **Three planted parties** (`tests/test_occasions.py`): the first is `occasion-1` with 1 episode and confidence 0.5. The 2nd and 3rd join it (0.67, then 0.75), with bedtime late in `explains`. A 4th is matched live from 20:30 to 02:30 with confidence 0.75, since 19:10.
- **Lookalikes:**
  - a plumber (eight door openings mid-morning) makes no episode (one feature) and no live match;
  - a workman (doors, the light and a power tool) is an occasion of its own, never the party.
- **Part D's `rosa-plus`** parties are found as whole evenings (20:00 to 01:25), with bedtime and the next morning's wake-up late. With two parties in 21 days they are one occasion (confidence 0.67), and the third is matched live from 21:15.

## Limits
- **Lookalikes exist.** Resemblance is by numbers, not meaning: a plumber who comes in the evening, keeps the light on late and leaves after midnight looks like a dinner. Cards and the namer should say "looks like", never state it.
- **A baseline comes first.** Nothing is judged before `MIN_OCCURRENCES` usual days. With 21 days, a late light can't be rarer than 1 in 21, so a party stands out by its counts (door openings, plug uses). A small dinner with two or three openings isn't found in the first weeks.
- **Only "more than usual".** An evening out or an early night is not an occasion here; the habits' `not_today` and `late` cover absences.
- **The day is cut at the quietest hour**, so a party that goes on past it is cut there.
- **Night blips:** a light at 03:00 splits the night's quiet, and tonight's quiet is the longest one starting in the second half of the day, so `quiet_start` can then be off.
- **Recurring gatherings** (every Sunday lunch) become one occasion with many episodes, not part of the usual day, because episodes are kept out of it.
- **Bursty sensors:** the counts assume a Poisson rate, so a door that bounces open and shut in seconds counts several times.
- Occasions never merge, and an episode's occasion isn't re-decided later.
- **Shifts need the next day over**: a shift is only measured once the day after an episode has ended (6 h past the habit's usual time), and only habits due on the day being forecast are listed. A habit the occasion stopped altogether (no waking up at all) is not a shift.
- **Phones as activity**: a known phone's `connected` happening lasts as long as the person is home, so if phones count as activity (C2), the night's quiet never starts while someone is in, and the awake minutes and `quiet_start` lose their meaning. Presence signals should be kept out of the quiets (`source.learn.is_activity`, part B), or read only at their edges.

## Run
```
uv run python -m unittest tests.test_occasions -v                                        # one 21-day simulation, ~10 s
uv run python -m source.learn.occasions --db data/sim/rosa-plus/home.sqlite3 --learn      # forget and replay every day
uv run python -m source.learn.occasions --db data/sim/rosa-plus/home.sqlite3 --at 2026-09-21T23:00
```

## For the integration step
1. Call `learn_occasions(db, day)` at each day end, right after `learn_day` (in `run.learn_day` or the pipeline's `on_day_end`). `occasions.py` imports `run.counted_days` inside a function, so `run.py` may import `occasions` at the top. Replays need nothing: re-running a day forgets that day and later ones. For a clean start, add `DELETE FROM occasions; DELETE FROM occasion_episodes;` to `storage.reset_learned`.
2. Live (part B): `match_now(db, now)` at each poll. Today's happenings must be stored as they end, for example `save_happenings(store, now)` at each poll; the loop now does this at each re-plan (every 5 min), and a state still held (a guest's phone) is read from the events (`storage.ongoing_happenings`). `_match` is remembered per store, moment and learned state: one tick asks it several times (the forecast's shifts, the wakes, the plan).
3. The namer names occasions from `fingerprint`, `usual_hours`, `weekdays`, `episodes` and `changes`. Names should say "looks like". `name` and `named_by` are kept when an occasion gains episodes.
4. The knowledge tab and dashboard read `export_occasions(db)`.

Background: [Fisher's method](https://en.wikipedia.org/wiki/Fisher%27s_method) · [conformal p-values](https://www.stat.berkeley.edu/~ryantibs/statlearn-s24/lectures/conformal.pdf) · [negative binomial as a Poisson-gamma predictive](https://en.wikipedia.org/wiki/Negative_binomial_distribution#Gamma%E2%80%93Poisson_mixture) · [Mann-Whitney U](https://en.wikipedia.org/wiki/Mann%E2%80%93Whitney_U_test)
