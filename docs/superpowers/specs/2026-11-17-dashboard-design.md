# Alfred dashboard for the jury: design

Status: draft for review · 2026-11-17 · branch `feat/dashboard`

## Why
The jury has to see the plan's central claim happen: **nobody tells Alfred what to look for**. On a projector, simulated days go by, the home's devices change, and learned sensors appear on the day each clears the habit test, each with its evidence. The plan's honesty rule holds on every screen: "the data is simulated, the learning is real".

The demo is tomorrow (2026-11-18). Today the dashboard runs on a mocked recording; tomorrow the same file format comes from a real simulated run.

## Goals
- A React + TypeScript app in a new top-level `dashboard/` folder (not `console/`, which is a debugging page).
- It loads **one JSON file**, the recording (format below), and plays it back: play, pause, speed, jump to a day, jump to the end.
- Story on stage: start from zero, press play, watch the days go by and cards appear, then stop at the end and open one sensor's evidence.
- Readable from the back of a room: projector-sized type, few things on screen at once.
- Built from the Alfred design system (its `tokens.css`, `bundle.css` and fonts) and the v2 screens' layout.

## Non-goals (for tomorrow)
- No backend, no live pipeline, no MCP or real-kit connection. No network calls besides loading the recording.
- No Python exporter yet. The mock JSON is written by hand, with values taken from a real 21-day `rosa` run so the numbers are realistic. A tested exporter comes after the demo.
- No phone layout and no light theme toggle (dark theme only; the design system's light theme stays available for later).
- No namer: unnamed sensors show as "Pattern N", as the plan says.
- No device control: the dashboard never writes to anything.

## Anti-goals (a failure even if it works)
- Anything that can stall or error mid-pitch (spinners waiting on a server, a crash on a missing field).
- Presenting a guess as fact: cards say "around" and "looks like", and show their evidence.
- Hiding that the data is simulated: the clock always says "simulated".
- Showing camera motion, or Alfred acting on its own or arming the alarm.

## Constraints
- Stack: Vite + React 18 + TypeScript (strict), Vitest + React Testing Library. No `as any`, `@ts-ignore` or `@ts-expect-error`.
- Styling: the design system's CSS only (`al-` classes and tokens), plus one small `dashboard.css` for layout. **No Tailwind**: the design system already defines the look, and Tailwind would duplicate it.
- Design system files are vendored into `dashboard/public/ds/` (copied from the live design system, version 1794940672-0f30), not fetched at runtime.
- Accessibility per the design system: 4.5:1 text, 44 px targets, real buttons and links, status never by colour alone.
- Tests for every module; coverage 90%+ on playback and data logic, 80%+ on components.

## Sources the builder needs
- **Design system** (CSS and markup only; there is no React or JS in it): https://claude.ai/artifact/FGnVqzqNiocjo9Hy8o35nJ. Its `project/tokens.css`, `project/components/bundle.css`, `project/fonts/*.woff2`, and each `project/components/<Name>/README.md` + `preview.html`, which give the exact markup per component (`al-card`, `al-list`, `al-timeline`, `al-days`, `al-conf`, `al-pill`, `al-clock`, `al-header`, `al-counter`, `al-legend`, …). The dashboard writes its own small React components that render that markup; there is nothing to import.
- **v2 screens** (layout reference, not code to copy): https://claude.ai/artifact/W7DWfC3uNzzmuPZKvsdHvz, artboards "1 Home", "2 Learned", "3 Sensor detail", "5b Learning demo: after" for the stage feel.
- **Real data** for the mock: a 21-day `rosa` run, `uv run python -m source.pipeline --household rosa --days 21 --out <dir> --replace`, then `source.learn.storage.export(db, as_of, now)` and the `devices` and `events` tables in `<dir>/home.sqlite3`.

## Approaches considered
1. **Recorded run played back in the browser** (chosen). One JSON file holds the whole run; the browser decides what is visible at each simulated moment. Deterministic on stage, nothing to fail server-side.
2. Live Python server running the pipeline day by day. Truly live, but more moving parts on stage and roughly twice the work before tomorrow.
3. One HTML page with plain JavaScript. No tooling, but messy playback state, hard to test, and against the team's React/TypeScript preference.

Gabriel picked 1, in React, with a JSON file for now.

## The recording (the JSON contract)
One file, `dashboard/src/data/recording.json` for the mock. Times are local Lisbon time, ISO 8601 without offset, as `storage.export()` writes them.

```jsonc
{
  "meta": { "household": "rosa", "simulated": true,
            "start": "2026-09-01T00:00:00", "end": "2026-09-21T23:59:59", "days": 21 },
  "devices": [                       // one per device, from the devices table
    { "id": "d-door", "type": "contactSensor", "name": "Front door" }
  ],
  "initial": [                       // device states at meta.start
    { "device_id": "d-door", "capability": "sensorState", "value": "normal" }
  ],
  "events": [                        // every change after meta.start, in time order (events table)
    { "ts": "2026-09-01T07:59:39", "device_id": "d-plug", "capability": "instantPower", "value": 2105 }
  ],
  "sensors": [ /* storage.export() sensors, unchanged: id, name, named_by, focus, learner,
                  learned_at, explanation, pattern{parts, time_of_day, spread_min, days_seen,
                  days_total, earliest, latest, late_after, ...}, confidence, status */ ],
  "readings": [ /* storage.export() readings: sensor_id, start, end|null, value|null */ ]
}
```

A real sensor entry (rosa, 21 days). `learner` is `habit` for every sensor today; `focus` is `connected_life` or `connectivity`; `parts` is an array of strings. A quiet sensor has the same shape, its part reading `home-A (quiet after bulb until bulb, about 9.1 h)` or `end of home-A (…)`, and may add `until`, `duration_min`, `shortest_min`, `after_quiet_min` (docs/learners.md, Contracts).
```json
{"id": "pattern-2", "name": null, "named_by": null, "focus": "connected_life", "learner": "habit",
 "learned_at": "2026-09-06", "explanation": null, "confidence": 0.84, "status": "active",
 "pattern": {"parts": ["bulb-A (power on, about 43 min)", "plug-A (about 2,100 W, about 3 min)"],
   "time_of_day": "07:59", "spread_min": 20, "days_seen": 16, "days_total": 19, "lag_min": 18,
   "first_seen": "2026-09-01", "last_seen": "2026-09-20", "earliest": "07:33", "latest": "08:27",
   "late_after": "08:27", "late_chance": 0.059}}
```
**Required** (missing ⇒ the load error screen): `meta.start`, `meta.end`, `devices[].id/type/name`, `sensors[].id/learned_at/confidence/status/pattern.parts/pattern.time_of_day`, `readings[].sensor_id/start`. Everything else is optional and renders a dash or is left out.

Rules the app relies on:
- A sensor is **visible** once the playhead reaches the end of its `learned_at` day (that's when the pipeline learns it).
- Once visible, it shows **all** its readings up to the playhead, including those before `learned_at` (the evidence it was learned from).
- **Days seen during playback** are counted from the readings: "seen on N days so far" = distinct days with a reading up to the playhead. The learners' `days_seen` of `days_total` ("16 of 19 days") appears only once the playhead is at the end, because those are end-of-run numbers and would contradict "day 9 of 21".
- Sensors with `status` `merged` are hidden; `fading` shows with a neutral pill "fading".
- Device state at time t = `initial` with every event up to t applied.
- Unknown fields are ignored; a missing optional field renders a dash, never a crash. The app validates the file on load and shows a plain error screen if a required field is missing.

## Screens
**1. Stage (the main screen)**
```
 [mark] Alfred           SIMULATED · day 9 of 21 · Wed 08:12     [▶] [1×▾] [⏭]
 ─────────────────────────────────────────────────────────────────────────────
  Home now                         Learned so far                         (3)
  Front door   closed              ┌ Pattern 2 · learned today ───────────┐
  Lamp         on, 100%            │ around 07:59 · 16 of 19 days         │
  Plug         2,105 W  burst      │ lamp on, then plug about 2,100 W     │
  Temperature  21.4 °C             └──────────────────────────────────────┘
  Humidity     48%                 Pattern 1 · around 18:32 · 17 of 18 days
                                   Also noticed (4) ▸
  [day 1 ─────────●──────────────── day 21]   still learning, 12 days to go
```
- Header: lockup, the learning-state pill, the simulated clock (always "simulated", day N of M, weekday and time).
- Left: device rows (`ListRow`), value and state as words; a device whose value just changed is marked for a few simulated minutes (text, not colour alone).
- Right: learned sensor cards (`SensorCard`), newest first. A card learned in the current simulated day gets the design system's `just-learned` wash (`LearnedReveal`), only that day.
- **Ranking:** confidence ≥ 0.6 shows as a card; the rest are collapsed under "Also noticed (N)". Threshold is one constant.
- Before the first sensor: the still-learning state (`DayCounter`).
- Bottom: a scrubber over the days, with a tick on each day a sensor was learned.

**Card text, and where each line comes from** (same layout for every sensor):
- Title: `name`, else "Pattern N" from the id (`pattern-2` → "Pattern 2"), in the unnamed style.
- "Looks like" line: `explanation` when present; otherwise left out (never made up).
- Headline: "around `time_of_day`" with "± `spread_min` min" small beside it.
- Evidence: `pattern.parts` turned into plain words and joined with ", then ": `bulb-A (power on, about 43 min)` → "lamp on, about 43 min"; `plug-A (about 2,100 W, about 3 min)` → "plug about 2,100 W, about 3 min"; `contactSensor-A (sensorState alarm, about 40 s)` → "door open, about 40 s"; `home-A (quiet after bulb until bulb, about 9.1 h)` → "the home quiet, about 9.1 h"; `end of …` → "end of the home quiet". The `-A` kind letters are dropped. Anything that doesn't match a known form is shown as written.
- Foot: days seen (rule above) and confidence as a percentage.

**Device words:** door `sensorState` `normal` → "closed", `alarm` → "open"; lamp `power` → "on"/"off" plus brightness; plug `instantPower` in W, with "in use" when above its rest of 0 W (> 5 W); temperature in °C and humidity in %, one decimal. A capability not in this list shows its raw value. "Just changed" (Gabriel picked option a, 2026-11-17): changes to the door, lamp and plug are held for 3 real seconds at the current speed (0.3 simulated days at 1×). The row shows "changed hh:mm", and a "Latest · hh:mm" line above the rows names the last time one of them went active (door open, lamp on, plug above 5 W), so a 3-minute plug burst stays visible. Temperature and humidity are never marked.

**2. Sensor detail** (opens from a card, `<a href>`; back returns to the stage at the same playhead)
- Name (or "Pattern N"), "looks like" line, the parts as plain words, time of day ± spread, days seen, confidence (`ConfidenceBar`), the `DayTimeline` with its readings, `DaysSeen` for the last 14 days, and the readings list. A "coming later" note for the namer.

Navigation: React Router with two routes, `/` and `/sensor/:id`. The playhead lives above the router so it survives navigation.

## Playback
- A pure function `stateAt(recording, t)` returns everything a screen needs at simulated time t: devices, visible sensors, readings, day number. All the logic is here and unit-tested; components only render it.
- The clock: speeds of 1, 2 and 4 "days per 10 s", default 1 (so 21 days take about 3.5 min at 1×, under a minute at 4×). Play, pause, jump to a day, jump to the end. Space toggles play; left/right arrows step a day.
- Time advances in real seconds via `requestAnimationFrame`; it never reads the real date for simulated time.
- A sensor appears at 00:00 of the day after `learned_at` (the pipeline learns it at that day's end) and keeps the `just-learned` wash for that one simulated day.
- "Jump to a day" lands on 00:00 of that day. Scrubbing backwards hides sensors again (it's one pure function of t). Playback pauses at `meta.end`, never loops; "jump to the end" goes to `meta.end`, where the end-of-run numbers show.

## Running it on the day
- `npm run dev` during work; for the pitch, `npm run build` and `npm run preview` on the presenting laptop, no internet needed (fonts and design system are vendored).
- `base: "./"` and a hash router (`#/sensor/pattern-2`), so the build also opens from a plain folder.
- The recording is **fetched** from `dashboard/public/recording.json`, so tomorrow's real run replaces one file with no rebuild of the code.
- The mock is generated once from the 21-day rosa run by a throwaway script kept out of the repo, minus pattern-10 (see quirks); only the JSON is committed. Rosa's run gives 5 sensors with confidence ≥ 0.6 (patterns 1, 2, 3, 4, 6), enough for the stage.

## Parts of the how decided by Gabriel
- React. A JSON file as the data source for now. For the jury. Not in `console/`. Connected to simulated data tomorrow.

## My calls (Claude), open to change
- Folder `dashboard/`, Vite, Vitest, React Router, dark theme only.
- Vendoring the design system files instead of linking the artifact.
- The visibility rule (end of `learned_at` day) and showing earlier readings as evidence.
- Ranking threshold 0.6 and "Also noticed".
- Speeds, the Space and arrow shortcuts, the two screens and their content.
- Generating the mock once from the 21-day run with a throwaway script instead of building the tested exporter first.
- Days seen counted from readings during playback; end-of-run counts only at the end.
- Card line sources, the parts-to-words mapping, the device words and the 10-minute "changed" mark.
- Appearance at 00:00 after `learned_at`, wash for one day, pause at the end, backwards scrubbing re-hides.
- Static build with a hash router and a fetched `public/recording.json`.

## Left to the builder
Component breakdown, file names inside `dashboard/src/`, exact layout spacing within the design system's tokens, test file layout, how the scrubber is drawn.

## Known data quirks to keep in mind
- Several sensors are the same night quiet seen from different angles (rosa patterns 4–7); ranking keeps most of them under "Also noticed".
- rosa pattern-10 reports "done at 07:51" while its usual time is 11:56; leave it out of the mock and raise it with the learners' author.
- Parts read like `bulb-A (power on, about 43 min)`; the card turns them into plain words with a small, tested mapping (bulb → lamp, contactSensor → door, plug → plug), as `docs/learners.md` lists under "readable cards".

## Addendum (2026-11-18): v2 tabs
Gabriel asked for the v2 mockup's structure ("build it more like this", v2 artboards "1 Home" and "2 Learned") and chose:
- **Tabs, controls on all**: Home (`#/`), Learned (`#/learned`) and the sensor page (`#/sensor/:id`, under Learned). Every page has the top bar (logo, tabs, simulated clock, play controls) and the day slider stuck to the bottom. Timeline is left for later.
- **Suggestions and questions as "coming later"**: the pipeline makes neither and the dashboard never writes, so v2's "Turn the lamp off?" card and the naming and question rows become dashed "Not built yet" cards.

Home: a headline from the device states, a sentence (home words plus what was learned today), the "coming later" suggestion card, "Today so far" in v2's row style, "New from Alfred" (the newest learned sensors, linking to their pages), and a Devices box with sub-lines (door "closed since hh:mm", lamp brightness, humidity).
Learned: the headline, the summary and the honesty line; a "Connected life" table (sensor, 24 h bar, seen, confidence; each row opens the sensor page); "Still noticing" for sensors under the 0.6 threshold; "Connectivity" (its sensors, or a line saying there are none); "Coming later" cards for naming and questions, the event learner and the drift learner.
v2's 13–15 px text is raised to at least 16 px where the jury reads it. The stage's cards, "Also noticed" and the header pill are replaced by these pages.
