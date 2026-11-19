# The simulator

Part 1 of [`starting-plan.md`](starting-plan.md): it writes a household's days as `get_home` snapshots, so the learners have weeks of history that the kit can't give us.
Code: [`source/simulate.py`](../source/simulate.py) · form and 3D view: [`source/simulate_web.py`](../source/simulate_web.py) + [`console/simulator.html`](../console/simulator.html), [`console/visualizer.html`](../console/visualizer.html) · tests: [`tests/test_simulate.py`](../tests/test_simulate.py).

## Run it
**The form** offers two kinds of home and downloads a zip with one `.jsonl` per house:
```
uv run python -m source.simulate --serve           # http://localhost:4781
```
| Choice | Asks for | Gives |
|---|---|---|
| **Simulated houses** (fully simulated) | Days, houses | Made-up homes `house-1`, `house-2`, …, each with its own wake time, bedtime, appliance on the plug and outings |
| **Real house** (partly real) | Days, up to 91 | `home-a`: one real kitchen, replayed (see [below](#a-real-house-home-a)) |

The page says, device by device, what is simulated and what is real. The same choices always give the same zip. About 0.02 MB zipped per house per day (1.7 MB unzipped).

**The command line** does the same, and also the named households:
```
uv run python -m source.simulate --days 14 --houses 5 --out data/sim/                    # as the form does
uv run python -m source.simulate --days 14 --household rosa --out data/sim/rosa.jsonl    # one named household
uv run python -m source.simulate --days 60 --household rosa-plus --out data/sim/rosa-plus.jsonl --truth data/sim/rosa-plus-truth.json
uv run python -m source.simulate --days 30 --household home-a --out data/sim/home-a.jsonl
uv run python -m source.simulate --list            # the named households and their settings
```
- `--truth file.json` also writes an answer key for a named household: what was planted and when. Off unless asked for; never feed it to the learners.
- `--config file.json` overrides settings of `--household` (the keys are the fields of `Household`).
- `--start`, `--seed`, `--interval`: first day (default 2026-09-01), random seed (another seed gives other houses), seconds between snapshots when nothing changes (default 300).

## Look at a day in 3D
The same server shows one day of a household in a small house you can turn, at `/visualizer`:
```
uv run python -m source.simulate --serve           # http://localhost:4781/visualizer?household=rosa&day=3
```
- **Pick** a household (`rosa`, `couple`, `students`, `home-a`, or `house-1` … `house-20` of the form's zip) and a day, counted from 2026-09-01. Or **open a `.jsonl` file** from a zip: it is read in the browser and cut into local days.
- **Time**: drag the slider or the lanes above it (light, door, plug, temperature), or play at 10 s to 1 h per second. `←` `→` go to the previous or next change, with `Shift` five minutes, `Space` plays. The house shows the latest snapshot at or before that time; a stretch with no snapshots (an outage) is blank in the lanes.
- **The house** is one room, as the kit has: the lamp glows with its colour and brightness, the door swings when the contact sensor says `alarm`, the plug's ring and bar grow with the watts, the thermometer is tinted by the temperature, the smoke sensor and the hub turn red on an alarm. Drag to turn, scroll to zoom, click a device or its row to mark it.
- The panel also lists **what was planted that day** (the answer key), for households the server generates.
- **Learned by Alfred** (right panel, or under the devices on a narrow window): for households the server generates, it runs simulate → ingest → learn from day 1 to the chosen day (the same as `source.pipeline`) and lists every learned sensor ([`learners.md`](learners.md)). Each one's chip follows the time bar: `done 07:51`, `now`, `expected`, `late`, `not today`, as Alfred saw it then, from the sensors it had when the day began. Sensors first learned that night say `new tonight`, and those that start fading, merge or move that night say so. The **Habits** lane shows when each one occurred that day. Click one to mark it and its devices and jump to it. About 5 s for day 21, and it stops at day 60; nothing for an opened file.
- `GET /learned?household=rosa&day=21` is what the learned panel reads: the sensors, each active habit's status every 5 minutes (only where one changes) and that day's readings.
- `GET /day?household=rosa&day=3` (also `seed`, `start`) is what the page reads: the day's snapshots with only the values it draws, each one as the changes since the one before. About 10 KB instead of 1.7 MB.
- three.js r128 comes from cdnjs, so the house needs a network connection; the panel and the lanes work without.

## What it writes
One line per snapshot, the format `ReplaySource` and ingest read:
```json
{"ts": 1788217274000, "home": { ...get_home... }}
```
- The snapshot starts as a real one from the kit ([`source/sim_data/home_template.json`](../source/sim_data/home_template.json)), so every field, id and value is the kit's own. The door says `alarm` for open and `normal` for closed.
- A snapshot is written **whenever something changes**, at the exact second, and **every 5 minutes**. Ingest sees no gap and no stale device.
- Times are whole local days in Europe/Lisbon, in order. Nothing reads the real clock.
- About 1.7 MB per simulated day (24 MB for 14 days).

| Device | What changes |
|---|---|
| Contact sensor | `sensorState`: `normal` ↔ `alarm` |
| Plug | `instantPower` (W), `instantCurrent` (mA, from the power), `todayConsumption` (kWh, back to 0 at local midnight), `yesterdayConsumption` |
| Light | `power`: `on` ↔ `off` (and `brightness` in the real house) |
| Temperature sensor | `temperatureValue`, `humidityValue`, reported when they move by 0.2 °C or 1 % |
| Camera, smoke sensor, hub | Nothing: they report, with constant values |

## Households from a config
A `Household` is about 30 settings: wake time, bedtime, what is on the plug and when it runs, when the door opens, the room's climate. Each time has a spread, each habit a chance, so days vary and some are skipped. About 20 more, off by default, add [rarer rhythms and parties](#rarer-rhythms-and-parties).

| Preset | Plug | Day | Door |
|---|---|---|---|
| `rosa` | Kettle, 2,100 W for 3 min, about 20 min after waking, 85 % of days | Up 07:45, bed 23:10 | Opens after waking; out on Thursdays 10:00–12:00 |
| `couple` | Coffee machine, 1,250 W for 6 min, almost every morning | Up 06:50, bed 22:40, much later at weekends | Out on weekdays 08:05, back about 17:35 |
| `students` | Games console, 160 W for about 2.5 h in the evening; idles at 8 W | Up 10:15, bed 01:30 | Many openings; out on weekday afternoons |
| `rosa-plus` | rosa's kettle, and tea every 3 h from 11:00 to 20:00 | rosa's; friends to dinner 3 or 4 times a month (mostly Fri or Sat): bed about 01:00, up about 90 min late | rosa's; visitors every other Saturday about 15:00–17:00; out for lunch on the first Sunday of the month, 12:30–16:30 |

Made-up houses (`--houses`, the form) draw these settings at random: one of eight appliances (kettle, coffee machine, toaster, microwave, hair dryer, television, games console, electric blanket), a wake time between 06:00 and 10:00, and so on.

A day: lamp on at waking → door → appliance → lamp off; outings and other door openings; lamp on in the evening → off at bedtime → a quiet night (only temperature and humidity move).

## Rarer rhythms and parties
What the habits plan ([`habits-plan.md`](habits-plan.md)) must find beyond daily and weekly habits, and its occasions. Off by default, so the other presets don't change by a byte; `rosa-plus` turns them all on. Set them in a `--config` JSON file like any setting.

| Settings | `rosa-plus` | What happens | Answer key |
|---|---|---|---|
| `visit_every_days`, `visit_anchor`, `visit_at`, `visit_minutes`, `visit_spread_min`, `visit_chance` | 14, `2026-09-05`, 15:00, 120 min, 20 min, 90 % | Visitors every 14 days from the anchor's day (so on its weekday): door, lamp on, the appliance 8–20 min later, door and lamp off as they go | `visit (every other Saturday)`, from arrival to departure; `visit use` |
| `monthly_on`, `monthly_at`, `monthly_minutes`, `monthly_spread_min`, `monthly_chance` | `first sun`, 12:30, 240 min, 20 min, 90 % | Out once a month: door, nothing, door. `first sun`, `2nd sat`, `last fri`, or a day of the month like `15` (none in a month without it) | `monthly outing (first Sunday)` |
| `hourly_every_h`, `hourly_from`, `hourly_until`, `hourly_spread_min`, `hourly_chance` | 3 h, 11:00 to 20:00, 10 min, 90 % each time | The appliance at about the same clock times, every so many hours | `every 3 h, 11:00 to 20:00`, one per use |
| `parties_per_30_days`, `party_days`, `party_at`, `party_minutes`, `party_spread_min`, `party_lie_in_min` | 3.5, `fri,sat`, 20:00, 270 min, 20 min, 90 min | Friends to dinner: 4–7 door openings as they arrive a few minutes apart, 0–2 during, 2–4 as they leave; the appliance up to 3 times; the lamp on until bed, 15–40 min after the last guest (about 01:00); the next morning about 90 min late | `party`, with `door_openings` and `plug_uses`; `party use`; that night's `bed` and the next `wake` say `"after": "party"` |

- **They vary.** Every time has a spread, and a visit, an outing or a time for the appliance is skipped now and then (its chance). Parties come about `parties_per_30_days` a month (3 or 4 in most 30-day stretches), a week or two apart, mostly on `party_days` (15 % fall on whatever day one is due).
- **One thing at a time.** None happens while nobody is home (an outing, away all day) or asleep, and the plug never runs two uses at once. A party never falls on a visit's or the monthly outing's day.
- **A run of N days is the start of a run of N+1**: everything is drawn day by day, and the next party is planned the day one happens. (Not with a plug that idles above 0 W, like `students`': its wobble and the room's drift share one random stream, so the room's values differ with the number of days.)
- **A holiday** cancels that day's visit, outing, appliance times and party. The other days come out as without it, unless it cancels a party: then the next morning has no lie-in, and the days after can differ.
- **Each rhythm's entries carry `recurrence`**, in the shape and words of a learned habit's ([`recurrence.py`](../source/learn/recurrence.py)): `{"kind": "every_n_days", "weekdays": ["sat"], "every_n_days": 14, "anchor": "2026-09-05", …, "text": "every other Saturday"}`, `on the first Sunday of the month` (its `anchor` is that day), `every 3 hours from 11:00 to 20:00`. A test can compare what was found with what was planted.

With the default seed, 60 days from 2026-09-01: visits on 5 and 19 Sep, 3 and 17 Oct; lunch out on 6 Sep and 4 Oct; parties on Sat 12 Sep, Fri 25 Sep, Fri 9 Oct, Fri 16 Oct and Sat 24 Oct; tea 199 of 240 times. The 3D view's routes take it like any preset (`/day?household=rosa-plus&day=12`).

## Options for the learners' tests
Off by default; set them in a `--config` JSON file (not in the form yet).

| Option | Example | Effect | Answer key |
|---|---|---|---|
| `holidays` | `["2026-09-20..2026-09-26"]` | Nobody home on those days; snapshots and the room's climate continue | `away (holiday)`, one per day |
| `outages` | `["2026-09-10T22:00..2026-09-11T08:00"]` (local) | No snapshots at all, so ingest records a `gap`; the home carries on underneath | `outage` |
| `changes` | `[{"from": "2026-10-01", "wake": "09:00"}]` | Habit settings change from that day (not the climate, `idle_watts` or `name`) | `change`, with the settings |

Other days come out exactly as without the option (unless a holiday cancels a [party](#rarer-rhythms-and-parties): the next morning has no lie-in). Answer key labels: `wake`, `bed`, `door after waking`, `morning use`, `evening use`, `outing`, `away all day`, `away (holiday)`, `outage`, `change`; with the [rarer rhythms](#rarer-rhythms-and-parties), also `visit (…)`, `visit use`, `monthly outing (…)`, `every n h, …`, `party` and `party use`.

## A real house: `home-a`
Door, light, temperature and humidity are **recordings of one real kitchen**, replayed on the same weekdays and at the same times of day. Source: UMass Smart\* data set, Home A, May–July 2012 ([traces.cs.umass.edu](https://traces.cs.umass.edu/docs/traces/smartstar); Barker et al., SustKDD 2012).

| Our device | Recorded sensor | Days |
|---|---|---|
| Contact sensor | The fridge door (the house's only door sensor), about 65 openings a day | 84 |
| Light | The kitchen lights (a dimmer: on/off and brightness) | 92 |
| Temperature sensor | Indoor temperature (°F → °C) and humidity, every 5 min | 92 |
| Plug | The coffee maker: about 1,000 W for 7 min, on a timer at 06:30, never on Sundays | **17** |

- **The plug is only real on 17 days.** On the others, one of its recorded uses is replayed at a recorded start time for that kind of day. The answer key says which is which.
- Up to 91 days (it depends on the first day's weekday). A request for more is refused.
- The house was empty for a week in July (15–21): no door or light activity then.
- A long "open" with no close message is closed after 10 minutes; meter readings above 3,000 W are dropped as glitches.
- The recordings are packed into [`source/sim_data/home_a.json.gz`](../source/sim_data/home_a.json.gz) (124 KB) by [`tools/extract_smart_home_a.py`](../tools/extract_smart_home_a.py), which says how to rebuild it.

## Not done yet
- The kettle slowly drifting, for the drift learner.
- Outside temperature, for Insulation.
- Noise levels tuned against a recording of our own kit.
- The licence of the Smart\* data is not checked: confirm we may redistribute the extract before the repo goes to Altice.
