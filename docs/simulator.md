# The simulator

Part 1 of [`starting-plan.md`](starting-plan.md): it writes a household's days as `get_home` snapshots, so the learners have weeks of history that the kit can't give us.
Code: [`source/simulate.py`](../source/simulate.py) · form: [`source/simulate_web.py`](../source/simulate_web.py) + [`console/simulator.html`](../console/simulator.html) · tests: [`tests/test_simulate.py`](../tests/test_simulate.py).

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
uv run python -m source.simulate --days 30 --household home-a --out data/sim/home-a.jsonl
uv run python -m source.simulate --list            # the named households and their settings
```
- `--truth file.json` also writes an answer key for a named household: what was planted and when. Off unless asked for; never feed it to the learners.
- `--config file.json` overrides settings of `--household` (the keys are the fields of `Household`).
- `--start`, `--seed`, `--interval`: first day (default 2026-09-01), random seed (another seed gives other houses), seconds between snapshots when nothing changes (default 300).

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
A `Household` is about 30 settings: wake time, bedtime, what is on the plug and when it runs, when the door opens, the room's climate. Each time has a spread, each habit a chance, so days vary and some are skipped.

| Preset | Plug | Day | Door |
|---|---|---|---|
| `rosa` | Kettle, 2,100 W for 3 min, about 20 min after waking, 85 % of days | Up 07:45, bed 23:10 | Opens after waking; out on Thursdays 10:00–12:00 |
| `couple` | Coffee machine, 1,250 W for 6 min, almost every morning | Up 06:50, bed 22:40, much later at weekends | Out on weekdays 08:05, back about 17:35 |
| `students` | Games console, 160 W for about 2.5 h in the evening; idles at 8 W | Up 10:15, bed 01:30 | Many openings; out on weekday afternoons |

Made-up houses (`--houses`, the form) draw these settings at random: one of eight appliances (kettle, coffee machine, toaster, microwave, hair dryer, television, games console, electric blanket), a wake time between 06:00 and 10:00, and so on.

A day: lamp on at waking → door → appliance → lamp off; outings and other door openings; lamp on in the evening → off at bedtime → a quiet night (only temperature and humidity move).

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
- Planted events for the event learner (dinners with friends) and the kettle slowly drifting for the drift learner.
- Outside temperature, for Insulation.
- Noise levels tuned against a recording of our own kit.
- The licence of the Smart\* data is not checked: confirm we may redistribute the extract before the repo goes to Altice.
