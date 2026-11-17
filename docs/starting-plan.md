# Alfred: starting plan

What we build first, how each part works and who builds it.
This replaces the approach in [`learned-sensors-plan.md`](learned-sensors-plan.md): sensors are no longer queries we write or SQL the LLM writes. Alfred finds patterns with plain code, and the LLM only names them.

## The idea
Smart homes report devices and values. **Alfred learns what they mean**, and shows it as **learned sensors**: new devices made purely of software, built from the signals of the 7 real ones. Nobody tells Alfred which patterns to look for. A home with different habits gets different sensors from the same code.

## Goal for today
- ✅ Simulate any number of days and have Alfred learn **one well-defined sensor** from it, with no LLM in the finding (`source/pipeline.py`).
- ⬜ A small dashboard where we can watch that happen.
- ⬜ The visual identity defined: colour, typeface, logo.

The target: from 14 simulated days, Alfred finds the morning pattern and the nightly quiet on its own. ✅ **Met:** on 14 days of `rosa`, the morning tea (lamp → kettle, 07:48, 9 of 12 days) is learned on day 6, the night's quiet (from 23:05, 10 of 12 nights) on day 11, and its end (08:01, waking up) on day 14.

## How Alfred learns

### Two baselines
| Baseline | What it is | What stands out against it |
|---|---|---|
| **Rest** | Per signal: the value it sits at most of the time (plug at 0 W, door closed) | Any activity at all |
| **The usual day** | What the house normally does at each time of day | Anything unusual, like a party |

"Something happened" is always measured against **rest**. The morning tea is part of the usual day, and it is still a clear departure from rest. Rest is learned from the data: a plug that idles at 80 W has a rest of 80 W.

### From signals to sensors
1. **Happenings.** A happening starts when a signal leaves rest and ends when it returns: *plug burst, about 2,100 W, 3 minutes*.
2. **Kinds.** Happenings with similar size and duration are grouped into a kind. Nothing says "2,000 W means kettle"; groups form wherever the numbers cluster.
3. **Combinations.** Kinds on different devices that usually occur within a few minutes of each other are linked: *door opened, then plug kind A*.
4. **Habits.** A kind or combination that recurs in a consistent way becomes a sensor. The test is not "does it happen daily?" but "when it happens, is it consistent?" Twelve days out of 14 around 08:05 passes. So does one day in 7, always Thursday at 10:00.
5. **Names.** The learner knows *that* a pattern exists, never *what* it is. The LLM reads the pattern's facts and proposes a name: *Morning tea*.

### Three learners
A learner is ordinary code that reads the stored history and writes sensors. It runs once per simulated day. It is not an LLM call.

| Learner | Looks for | Example | When |
|---|---|---|---|
| **Habit learner** | Activity that recurs at a consistent time, and quiet stretches that recur | *Morning tea*, *Asleep*, *Awake* | ✅ Done |
| **Event learner** | Hours that differ from the usual day in several ways, grouped by resemblance. The calendar is a hint, never a requirement | *Dinner with friends* | Next |
| **Drift learner** | A learned habit whose shape slowly changes | *Kettle getting slower* | Next |

The habit learner comes first because it produces the usual day, which the other two need.

## What we store
| Layer | What it holds | Example | Status |
|---|---|---|---|
| Snapshots | The whole home at one moment | Every device and value at 08:21 | Exists |
| Events | Each change between snapshots | Plug: 0 W → 2,140 W | Exists |
| Rest | Each signal's resting value and normal wobble | Plug: 0 W, ± 3 W | ✅ Done, computed each day (not stored) |
| Happenings | Departures from rest | Plug burst, 2,140 W, 3 min 10 s | ✅ Done (`happenings`) |
| Sensors and readings | What Alfred learned, and each time it occurred | Morning tea · around 08:05 · 12 of 14 days | ✅ Done (`kinds`, `sensors`, `sensor_readings`) |
| Hourly profiles, the usual day, calendar | Inputs for the event learner | Saturday 21:00: 0 to 1 door openings | Later |

## The parts
| # | Part | File | Takes in | Produces | Status |
|---|---|---|---|---|---|
| 1 | Simulator | `source/simulate.py` | Number of days, a household config | Snapshots | ✅ Done (#17, #18) |
| 2 | Ingest | `source/ingest.py` | Snapshots | Events | ✅ Done (#14) |
| 3 | Rest finder | `source/learn/rest.py` | Events | Each signal's rest | ✅ Done (#15) |
| 4 | Happening extractor | `source/learn/happenings.py` | Events + rest | Happenings | ✅ Done (#18) |
| 5 | Grouper | `source/learn/kinds.py` | Happenings | Kinds and combinations | ✅ Done (`feat/grouper-habits`, not merged yet) |
| 6 | Habit learner | `source/learn/habits.py` | Kinds over many days | Sensors and readings | ✅ Done (`feat/grouper-habits`, not merged yet) |
| 7 | Namer | `source/learn/namer.py` | A sensor's facts | A name and explanation | ⬜ To do |
| 8 | Dashboard | `console/` | Sensors, readings, device states | What we see | ⬜ To do |

**1. Simulator** ✅ ([`simulator.md`](simulator.md))
- Writes a household's days as snapshots in the `get_home` shape: a wake time, a kettle, a bedtime, each with random variation and some days skipped.
- The household is a small config, so we can simulate a different home and check that Alfred finds different things.
- Uses the real kit's values (the door says `alarm` for open and `normal` for closed), or code written against it breaks on the real kit.

**2. Ingest** ✅
- Reads snapshots and stores each change as an event. It already works.
- To check: it runs through months at full speed and takes every time from the data, never the real clock.

**3. Rest finder** ✅
- On/off signals such as the door: the most common state.
- Numeric signals such as plug power: the most common value, plus how much it normally wobbles.
- Slow signals such as temperature: how fast it normally changes.

**4. Happening extractor** ✅
- Walks through each signal. A happening starts when the signal leaves rest by more than its normal wobble, and ends when it returns.
- Records the signal, start, end, duration and size.

**5. Grouper** ✅ (with a Gaussian mixture instead of the 20% rule: [`learners.md`](learners.md))
- Within each signal, puts happenings with similar size and duration into the same kind. Start simple: the same kind if both numbers are within about 20%.
- Links kinds across signals that usually occur within a few minutes of each other.

**6. Habit learner** ✅ ([`learners.md`](learners.md))
- For each kind and combination, measures how many days it occurred on, how tightly its time of day clusters, and whether it favours certain weekdays.
- Does the same for the long quiet stretch each night. ✅ Done as quiet stretches grouped as happenings of the home: asleep, away and awake are whichever quiets recur when. The room's temperature and humidity are habits too ([`learners.md`](learners.md)).
- Anything frequent and consistent enough becomes a sensor, with the date it first passed the test and one reading per occurrence.

**7. Namer**
- Sends the sensor's facts to the LLM and gets back a name and a one-line explanation.
- Until it runs, or if it fails, the sensor is shown as "Pattern 1". A wrong name never breaks the pattern underneath, and a person can rename it.

**8. Dashboard**
- Shows simulated time advancing, device states changing, and sensor cards appearing on the day they were learned.
- Reads one JSON response, so it can be built on fake data from the start.

**What ties them together:** one command, for example `simulate 14 days`, which generates the data, feeds it through ingest, and at the end of each simulated day runs parts 3 to 6. That is why sensors appear gradually. ✅ `uv run python -m source.pipeline --household rosa --days 14 --out data/sim/rosa/`

## Agree together first
These are the hand-offs between people. Fix them in the first hour. ✅ **Agreed.** The happening is stored as in `source/learn/happenings.py` (`signal` = `plug.instantPower`, times in epoch ms), and the sensor as below plus a few fields ([`learners.md`](learners.md)).

**A happening**
```json
{"signal": "Plug.instantPower",
 "start": "2026-09-01T08:21:00", "end": "2026-09-01T08:24:10",
 "duration_s": 190, "size": 2140, "kind": "plug-A"}
```

**A sensor**
```json
{"id": "pattern-1",
 "name": "Morning tea", "named_by": "llm",
 "focus": "connected_life",
 "learner": "habit",
 "pattern": {"parts": ["door opened", "plug-A (about 2,100 W, about 3 min)"],
             "time_of_day": "08:05", "spread_min": 12,
             "days_seen": 12, "days_total": 14},
 "learned_at": "2026-09-05", "confidence": 0.86,
 "explanation": "Most mornings the front door opens and the kettle runs shortly after."}
```

**A reading:** a sensor id, a start, an optional end and a value. That covers a moment (Awake), a stretch (Asleep) and a number (a drift).

**Also to agree**
- **Simulated time:** no code reads the real clock; time comes from the data.
- **The dashboard's JSON:** current simulated time, device states, list of sensors.
- **When a pattern counts as a habit:** a starting point is at least 5 occurrences with a time-of-day spread under 30 minutes. ✅ Now: at least 5 occurrences that beat chance in that home; the spread is reported, not used as a threshold.
- **Exact names:** `connected_life` and `connectivity` for focus; `habit`, `event` and `drift` for learner.

## Visual identity
- **Name:** Alfred. Decide how it relates to Altice's MyButler: a jury may read a butler's name as a rival, and adoption by Altice is a quarter of the score.
- **Colour:** one main colour, one accent, and neutrals, checked in the console's light and dark themes. Not red, amber or green, which the dashboard needs for status.
- **Logo:** a wordmark plus a simple mark that reads at small size and in one colour, as an SVG.
- **Done when:** one page shows the palette, typeface and logo, and all three are applied to the dashboard.

## Who builds what
| Person | Today |
|---|---|
| 1 | ✅ Simulator (part 1). A rough three-day dataset in the first hour matters more than a polished one tonight |
| 2 | ✅ Ingest check, rest finder, happening extractor (parts 2 to 4) |
| 3 | ✅ Grouper and habit learner (parts 5 and 6), starting from a hand-made list of happenings |
| 4 | Dashboard (part 8) on fake data, then the namer (part 7) once sensors exist |
| 5 | Visual identity, then the approach report, due Wednesday 18 Nov at 19:00 |

## Focus areas
| Focus | Sensors |
|---|---|
| **Connected Life** | Awake, Asleep, Morning tea, Dinner with friends |
| **Connectivity** | What is on the plug, Kettle getting slower, Insulation |

## Limits to keep in mind
- **A baseline comes first.** Alfred can't call anything unusual until it has a couple of weeks of normal.
- **Events need a few examples.** One party is only "an unusual evening". Plant four or five in the simulation.
- **Lookalikes exist.** A plumber's visit also means many door openings. Cards for events should say "looks like", not state it as fact.
- **Not everything is a repeated pattern.** Insulation is a slow physical property and needs its own method.
- **The data is simulated, the learning is real.** The patterns are planted in the dataset, but the code has to find them. Say so on stage.

## Open decisions
- The third focus (if there is one).
- How Alfred relates to MyButler.
- How many months to simulate for the demo. Three is enough for the kettle drift to show.
