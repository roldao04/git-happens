# Learners: how Alfred finds habits

Plain code, no LLM. Every decision asks: **does this beat chance in this home?** Chance is measured from the home's own data, so a different home gets different sensors from the same code.

| Step | File | How |
|---|---|---|
| Kinds | `source/learn/kinds.py` | Per signal, a Gaussian mixture on log size and log duration. BIC picks how many kinds; groups under 5 get no kind |
| Links | `source/learn/kinds.py` | A then B within 30 min, between two devices, more often than chance (event coincidence analysis) |
| Habits | `source/learn/habits.py` | A clock window (20/40/60 min) holding more occurrences than chance. Quiets first, against any moment of the day; the rest against the hours the home isn't in a recurring quiet. Weekday test: single days, work days, weekends |
| Quiet | `source/learn/habits.py` | Every stretch where people's devices all rest is a happening of the home, `home.quiet` ("after bulb until bulb"). It is grouped into kinds like any signal; a kind's start (`kind:`) and end (`kind-end:`) can be habits. Asleep, away and awake are whichever quiets recur when, e.g. "quiet after the lamp, 23:10", "after the door, weekdays 08:05". The namer names them |
| Stops / moves | `habits.py`, `storage.py` | `fading` once its absence beats chance (rule of succession). Back at a new time = the same card with `moved_from`; absorbed by a bigger card = `merged` |
| Counted days | `source/learn/run.py` | Only days the home was watched (no poll gap over 30 min) and someone was in. Outages and holidays aren't missed habits |
| Today | `source/learn/today.py` | Each habit now: `done`, `now`, `expected`, `late`, `not_today` or `unknown`. Late = later than ever seen (conformal bound) |

- **Knobs** (`source/learn/__init__.py`): `MIN_OCCURRENCES=5`, `ALPHA=0.01`, `MAX_LAG_MIN=30`. p-values are corrected for the number of tries.
- **Room facts are habits too:** temperature and humidity are grouped and can become habits like any signal (a recurring fact). Spotting deviations from habits is for a later model. Only people's devices decide "someone home" and what is quiet, because the room moves by itself.
- **Simulator, 21–28 days** (`tests/test_learn_sim.py`):
  - rosa: tea 07:59; night quiet from 23:07 and its end 07:59; unchanged by a holiday and an outage
  - couple: night quiet from 22:37 and its end 06:48; a weekday quiet from 08:03 to 17:08 (away); coffee 06:50

## Run
```
uv run python -m source.pipeline --household rosa --days 21 --out data/sim/rosa/ --replace   # parts 1–6
uv run python -m source.learn.run --store data/sim/rosa/        # learn again on that history
uv run python -m source.learn.today --db data/sim/rosa/home.sqlite3 --at 2026-09-21T08:30
uv run python -m source.learn.run --happenings tests/fixtures/happenings-14d.jsonl          # hand-made data
```
The pipeline calls `learn_day(store.db, day)` at each day end. `storage.export(db, as_of, now)` gives the sensors, readings and today.

**For the dashboard:** `source/recording.py` writes a whole run as the one JSON file the dashboard plays back (devices, their first values, every change, sensors and readings). Add `--recording <file>` to the pipeline, or run it on a history that exists:
```
uv run python -m source.pipeline --household rosa --days 21 --out data/sim/rosa/ --replace --recording dashboard/public/recording.json
uv run python -m source.recording data/sim/rosa/ --out dashboard/public/recording.json --household rosa
```
- Camera motion is left out, as the dashboard never shows it.
- When the clocks go back, the repeated hour's events are shown at the last time before it, so the file stays in clock order.
- `--real` marks a history from the kit (`meta.simulated` is false).

## Contracts
- **Happenings:** the table in `source/learn/happenings.py` (epoch ms; `signal` = `<device type>.<capability>`, or `<type>:<slug>.<capability>`, see below). The learners read it in Lisbon time (`source/clock.py`) and leave `cut` ones out of kinds and links.

## Signal names: several plugs, phones, the thermostat
Contracts C1 and C2 ([`contracts.md`](contracts.md#contracts)). Read the device type with `source.learn.device_type(signal)`, never `signal.split(".")[0]`.
- **Names:** `signal_devices(rows)` (in `source/learn/__init__.py`) names each device from the `devices` table. A type whose devices have more than one name gets `plug:kettle`, `plug:tv`, `plug:washing-machine`; otherwise the old `plug`. Devices with the same name are one device (a re-paired plug). Phones are always `phone:<client id>`.
- **The router** (`source/normalize.py`): `connectedClients` becomes one reading per phone (`kind: "phone"` only; a laptop is left out), device id = the client id, type `phone`, name = the client's name (`Guest phone` when empty), value `"on"`; plus `router.guestPhones`, the count of connected phones with `known: false`. The raw list is no reading.
- **A phone that leaves** disappears from the list. `normalize(home, prev)` keeps it as `"off"` (ingest passes `prev`). A history stored without `prev` has a `gone` event instead, which `rest.py` also reads as `"off"`. A phone's rest is always `"off"`, never measured (Ana is home most of the day).
- **Activity and slow:** phones, `router.guestPhones`, `targetTemperature` and `mode` are activity. `temperatureValue` is slow. `heatingState` is never activity: the thermostat switches it by itself, at night too.
- **A person, not Alfred:** a happening that begins with an event whose `source` is `agent` or `scenario:…` is not written. Events are all `unknown` today, so nothing changes until the loop marks Alfred's own writes.
- **Edges:** a phone connected all evening is one long happening. `activity_edges(activity)` turns those (phones, guest count, setpoint, mode) into their start and end moments, so they break a quiet only when someone arrives, leaves or touches the thermostat. Without it there is no night quiet while a phone is home. Wired in `run.learn`, `grouping` (the counted days), `today` (live quiets) and `occasions` (nights, counted days).
- **Kind ids** use the type (`plug-A`, `plug-B`), whichever plug; the kind's `signal` says which.
- **Limits:** the names come from the whole `devices` table. A second plug added later renames the first from then on (`plug.instantPower` before, `plug:kettle.instantPower` after): two signals for one plug. A phone is heard through the router: when the router goes silent, every phone goes stale with it.
- **Sensor:** the pattern shape in [`contracts.md`](contracts.md#data-shapes), plus:
  - `status` (`active`, `fading`, `merged` with `merged_into`)
  - `pattern.weekdays`, `lag_min`, `first_seen`, `last_seen`, `earliest`, `latest`, `late_after`, `moved_from`
  - for stretches: `until`, `duration_min`, `shortest_min`; for the end of a quiet: `after_quiet_min`

## Above the patterns
`learn_day` also learns habits ([`habits.md`](habits.md)) and occasions ([`occasions.md`](occasions.md)) each night; `reset_learned` clears them too. The agent's `baselines` carry the habits, forecast, occasions and decisions ([`anticipation.md`](anticipation.md)). Links use the `rhythm` chance model by default; `other_day` is the stricter option (both agree on the fixtures).

## Not built yet
- **What is on the plug:** appliance cards from plug kinds.
- **Drift learner** ("kettle getting slower").
- **3-part chains:** rosa's lamp → door → kettle is two cards today.
- **Readable cards:** plain parts ("door opened → smart plug about 2,100 W") and "12 days, where chance gives about 1".

## Known limits
- **Clutter:** short and middle quiets (minutes to ~2 h) become cards too ("the end of a 1.7 h quiet at 19:30" is the evening light coming on). The namer ranks them (`importance`, `same_as`).
- **Slow fading:** a habit polluted by random noise in its window fades slowly (a rate-change test would fix it).
- **Per-device watching** isn't done: a stale plug hides plug habits.

Background: [NILM review](https://arxiv.org/pdf/1610.01191) · [event coincidence](https://arxiv.org/pdf/1508.03534) · [rule of succession](https://en.wikipedia.org/wiki/Rule_of_succession) · [conformal bounds](https://www.stat.berkeley.edu/~ryantibs/statlearn-s24/lectures/conformal.pdf) · [Rashidi & Cook 2011](https://pmc.ncbi.nlm.nih.gov/articles/PMC3100559)
