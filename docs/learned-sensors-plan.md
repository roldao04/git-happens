# Learned sensors: build plan

The plan for the new direction: **a home that builds its own sensors from its history.**
This replaces the rules, verification and memories focus in [`synapse.md`](synapse.md), which still has to be rewritten (step 4.3). The event loop in [`event-loop.md`](event-loop.md) stays as it is and feeds everything here.

## Goal
Smart homes report devices and values. This one learns what they mean, and shows it as **learned sensors**: new devices made purely of software, built from the signals of the 7 real ones.

- Some sensors are **defined**: we write them, every home gets them, and they calibrate to the home.
- Others are **inferred**: the LLM explores the home's history, finds a pattern and writes the sensor itself.
- Both use the same format and the same pipeline.
- The demo shows a few months of simulated learning and the sensors that came out of it.

## Focus areas
| Focus | Defined (we write, every home gets) | Inferred (LLM finds, this home only) |
|---|---|---|
| **Connected Life** | Awake, Asleep, Someone home | Morning tea, Dinner with friends |
| **Connectivity** | Appliance on the plug, Insulation | Kettle getting slower |

Sensor categories: **people** (habits), **events** (dinner with friends), **appliances** (what is connected), **measurements** (insulation, degradation).

## 1. Foundation

### 1.1 Simulator
- Generate a few months of snapshots as JSONL in the `get_home` shape, so `ReplaySource` in `source/sources.py` reads them unchanged. Extend the approach in `tests/fixtures/make_morning.py`.
- Emit a snapshot when something changes, plus one every five minutes for temperature and humidity.
- Plant the patterns: daily habits, the kettle signature, the kettle slowly using more energy, nightly cooling, dinners with friends.
- Add an outside-temperature series, which Insulation needs.
- Add day-to-day noise and a few irregular days, or every pattern will be trivially perfect.

### 1.2 Sensor format
One record per sensor, agreed by the whole team before anyone builds on it:
```
name         "Morning tea"
focus        connected_life | connectivity
category     people | events | appliances | measurements
origin       defined | inferred
query        SQL returning rows of (ts, ts_end, value)
explanation  one sentence in plain words
confidence   0–1
learned_at   the date it first had enough evidence
```

### 1.3 Storage and runner
- Add `sensors` and `sensor_readings` tables in `source/store.py`.
- The runner executes a sensor's query over the history and writes its readings. Filling in past history is the same operation as updating it.

### 1.4 Generic summaries
The values in the `events` table are stored as JSON text, which makes queries awkward. Build three clean tables that every sensor query reads from:
- **Episodes:** for each numeric signal, every period it left its baseline, with start, end, peak and total.
- **Intervals:** for each state signal, how long it stayed in each state.
- **Daily digest:** one row per day per signal (count, min, max, first and last time).

These must stay pattern-agnostic. They describe signals, never "kettle" or "dinner".

## 2. Defined sensors

### 2.1 Connected Life
- **Awake:** first activity after the longest quiet gap of the night.
- **Asleep:** last activity before that gap.
- **Someone home:** activity since the last door event, or none.

### 2.2 Connectivity
- **Appliance on the plug:** the plug's episodes grouped by peak power and duration, matched against a small table of known signatures.
- **Insulation:** how fast the room cools each night relative to the outside temperature.

### 2.3 Calibration
No fixed clock times or thresholds. Each defined sensor derives its parameters from this home's data, and sets `learned_at` to the first date it had enough evidence (for example, 5 days out of 7).

## 3. Inferred sensors

### 3.1 Tools for the LLM
- `run_sql`: read-only connection, row limit and timeout.
- `propose_sensor`: submits a record in the format from 1.2.
- Reuse the LangGraph agent and tool allowlist in `source/agent.py`.

The LLM can't read months of raw snapshots, and it is unreliable at arithmetic over long series. So it gets tools to explore the data, not the data itself.

### 3.2 Validator
Reject a proposal that errors, returns no rows, appears on too few days, or overlaps heavily with an existing sensor. Send the reason back so the LLM can revise.

### 3.3 Learning loop
- Step through the simulated history one week at a time.
- Each week the LLM sees the new digest and the current sensors, then proposes, refines or retires.
- Limit every table to data before that week's cutoff. Otherwise the LLM sees the future, and `learned_at` means nothing.

### 3.4 Calendar
Load a calendar file as one more table, so the LLM can match an unexplained pattern to an event title and name it.

### 3.5 Check against the planted patterns
The targets are Morning tea and Dinner with friends for Connected Life, and Kettle getting slower for Connectivity. If the LLM misses one, improve the summaries in 1.4, not the prompt: hints in the prompt would mean it is no longer learning.

## 4. Demo and deliverables

### 4.1 Console view
- A timeline to scrub through the months, in `console/index.html`.
- Cards appear on their `learned_at` date, grouped by Connected Life and Connectivity, with a badge for defined or inferred.
- Each card opens to its explanation and its history.

### 4.2 Saved run
Run the learning before the pitch and save the sensors it produced. The console plays back that saved run. The LLM won't find the same sensors every run, so it never runs live on stage.

### 4.3 Documents
- Rewrite [`synapse.md`](synapse.md) around learned sensors, with the new focus names and without the old product name.
- Write the 2-page approach report, due Wednesday 18 Nov at 19:00.

## Demo
"We simulated X months of this home. These are the sensors it learned." Then two of them in detail:
- **Connected Life: Dinner with friends.** The house finds an unexplained recurring evening (door traffic, the room warming as it fills), then finds its name in the calendar.
- **Connectivity: Kettle getting slower.** The house first learns what is on the plug, then notices the same morning tea using more energy month after month.

Say "simulated data, real learning" on stage. The patterns are planted in the dataset, but the code has to find them.

## Open decisions
- The third focus (the notes say three; only Connected Life and Connectivity are defined).
- The product name.
- How many months to simulate. Three is enough for the kettle drift to show; more adds generation time and LLM cost.

## Risks
| Risk | Mitigation |
|---|---|
| The LLM finds nothing useful | The defined sensors still give a complete demo |
| The LLM finds different sensors each run | The saved run (4.2) |
| A judge asks if the sensors are hard-coded | The defined ones are, and the badge says so. For the inferred ones, show the query the LLM wrote and the week it proposed it |
| A subtle pattern (Insulation, kettle drift) is missed | Test the learning loop early; improve the generic summaries, not the prompt |
