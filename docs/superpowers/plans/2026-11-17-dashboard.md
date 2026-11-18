# Alfred jury dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A React + TypeScript dashboard in `dashboard/` that loads one recording JSON and plays a simulated run back for the jury: devices changing, learned sensors appearing with their evidence.

**Architecture:** All logic is pure TypeScript in `src/data/` (parse, words, `stateAt`) and `src/playback/` (clock math); components only render what `stateAt(recording, t)` returns. The recording is fetched from `public/recording.json`; the playhead lives above a hash router so it survives navigation.

**Tech Stack:** Node 20, Vite 5.4, React 18.3, react-router-dom 6.28 (HashRouter), TypeScript 5.6 strict, Vitest 2.1 + @testing-library/react 16 + jsdom 25, @vitest/coverage-v8 2.1. Styling: the Alfred design system's CSS, vendored.

**Spec:** `docs/superpowers/specs/2026-11-17-dashboard-design.md`

## Global Constraints
- Folder `dashboard/` at the repo root; never touch `console/`.
- No Tailwind. Styles: vendored `public/ds/tokens.css`, `public/ds/bundle.css`, `public/ds/fonts/*.woff2`, plus `src/dashboard.css` for layout only, using tokens (no hex literals).
- Dark theme only: `<html data-theme="dark">`.
- No `as any`, `@ts-ignore`, `@ts-expect-error`; `strict: true`.
- No network calls except `fetch("./recording.json")`. No real clock for simulated time (no `Date.now()`/`new Date()` in `src/data` or `src/playback`).
- Times in the recording are naive local ISO (`2026-09-01T07:59:39`); parse them as UTC wall-clock ms (`Date.UTC`) and format back the same way, so the browser's timezone never shifts them.
- Accessibility: real `<button>`/`<a href>`, 44 px targets, status never by colour alone, `aria-label` on icon-only controls.
- Copy: sentence case, 24-hour times, units always, "around"/"looks like", never claims Alfred acted.
- Coverage: 90%+ for `src/data` and `src/playback`, 80%+ for components.
- Commits: one-line conventional messages, on `feat/dashboard`, **only when Gabriel says to commit**. The commit steps below mark the points; ask before each.

## Review Focus
1. A recording whose times are in a different day or month than `2026-09` (e.g. starting 2026-12-30, crossing a year): day numbers and weekdays must still be right. → test in Task 3.
2. Scrubbing to exactly `meta.end` and past it: clamps, pauses, end-of-run counts show. → test in Tasks 3 and 4.
3. A sensor whose `learned_at` is the last day: it never appears during playback but does at `meta.end`. → test in Task 3.
4. A recording with zero sensors above 0.6: stage shows the still-learning state and "Also noticed", not an empty panel. → test in Task 6.
5. A malformed or missing `recording.json`: plain error screen naming the field, no white page. → test in Task 7.

---

### Task 1: Scaffold, design system and the first test

**Files:**
- Create: `dashboard/package.json`, `dashboard/vite.config.ts`, `dashboard/tsconfig.json`, `dashboard/index.html`, `dashboard/.gitignore` (`node_modules/`, `dist/`, `coverage/`), `dashboard/src/main.tsx`, `dashboard/src/App.tsx`, `dashboard/src/dashboard.css`, `dashboard/src/test/setup.ts`
- Create (vendored): `dashboard/public/ds/tokens.css`, `dashboard/public/ds/bundle.css`, `dashboard/public/ds/fonts/{SourceSerif4,Archivo,JetBrainsMono}-Variable-latin.woff2`, `dashboard/public/ds/alfred-mark-on-dark.svg`, `dashboard/public/ds/SOURCE.md` (one line: copied from https://claude.ai/artifact/FGnVqzqNiocjo9Hy8o35nJ version 1794940672-0f30)
- Test: `dashboard/src/App.test.tsx`

- [ ] **Step 1:** Write `package.json` with scripts `dev`, `build` (`tsc --noEmit && vite build`), `preview`, `test` (`vitest run`), `test:coverage` (`vitest run --coverage`), `typecheck` (`tsc --noEmit`), and the pinned versions from Tech Stack. `npm install`.
- [ ] **Step 2:** `vite.config.ts`: `base: "./"`, react plugin, `test: {environment: "jsdom", setupFiles: "src/test/setup.ts", coverage: {provider: "v8", include: ["src/**"], exclude: ["src/main.tsx", "src/test/**"]}}`. `setup.ts` imports `@testing-library/jest-dom/vitest`.
- [ ] **Step 3:** Vendor the design system files: `Artifact read` the paths from the design system (tokens.css is served at `project/tokens.css`), copy the woff2 files from the v2 folder `…/scratchpad/alfred-v2/project/ds/alfred/fonts/` (identical, sha256-checked), and fetch the mark asset `alfred-mark-on-dark.svg`. In the copied `tokens.css`, font URLs must resolve to `fonts/…` relative to `public/ds/`.
- [ ] **Step 4:** `index.html` has `<html lang="en" data-theme="dark">` and links `./ds/tokens.css`, `./ds/bundle.css` before the module script.
- [ ] **Step 5: Write the failing test** `App.test.tsx`: `render(<App />)` and `expect(screen.getByRole("heading", {name: "Alfred"})).toBeInTheDocument()`.
- [ ] **Step 6:** Run `npm test` → FAIL (App missing). Write the minimal `App` with an `<h1>Alfred</h1>`. Run → PASS. Run `npm run build` → succeeds.
- [ ] **Step 7: Commit** (ask first): `chore(dashboard): scaffold the React dashboard with the Alfred design system`

### Task 2: The recording: types and validation

**Files:** Create `dashboard/src/data/recording.ts`; Test `dashboard/src/data/recording.test.ts`

**Interfaces — Produces:**
```ts
export interface Meta { household: string; simulated: boolean; start: string; end: string; days: number }
export interface Device { id: string; type: string; name: string }
export interface DeviceValue { device_id: string; capability: string; value: string | number | boolean | null }
export interface DeviceEvent extends DeviceValue { ts: string }
export interface Pattern { parts: string[]; time_of_day: string; spread_min?: number; days_seen?: number; days_total?: number; [extra: string]: unknown }
export interface Sensor { id: string; name: string | null; named_by?: string | null; focus?: string; learner?: string; learned_at: string; explanation?: string | null; confidence: number; status: "active" | "fading" | "merged"; pattern: Pattern }
export interface Reading { sensor_id: string; start: string; end: string | null; value: number | null }
export interface Recording { meta: Meta; devices: Device[]; initial: DeviceValue[]; events: DeviceEvent[]; sensors: Sensor[]; readings: Reading[] }
export class RecordingError extends Error {}          // message names the path, e.g. "sensors[2].learned_at is missing"
export function parseRecording(raw: unknown): Recording
export function toMs(isoLocal: string): number         // "2026-09-01T07:59:39" -> Date.UTC(2026, 8, 1, 7, 59, 39); "2026-09-06" -> midnight; throws RecordingError on anything else
```

- [ ] **Step 1: Failing tests:**
  - `parses a minimal valid recording` (one device, one sensor, one reading) → deep-equals input with defaults: missing `initial`/`events` become `[]`, missing `meta.household` → `""`.
  - `names the missing field`: sensor without `learned_at` → throws `RecordingError` with message `"sensors[0].learned_at is missing"`.
  - `rejects a non-object` (`null`, `[]`, `"x"`) → `RecordingError("recording is not an object")`.
  - `rejects a bad time`: `meta.start: "yesterday"` → message contains `meta.start`.
  - `ignores unknown fields` (extra `pattern.lag_min`, top-level `x`) → no throw, `pattern.lag_min` kept.
  - `toMs` on `"2026-12-31T23:59:59"` → `Date.UTC(2026, 11, 31, 23, 59, 59)`; on `"2026-09-06"` → `Date.UTC(2026, 8, 6)`.
  - `status` other than the three values → `RecordingError` naming `sensors[0].status`.
- [ ] **Step 2:** Run `npx vitest run src/data/recording.test.ts` → FAIL (module missing).
- [ ] **Step 3:** Implement with hand-written guards (no `as` casts on unknown data: narrow with `typeof` checks). Required fields as in the spec's "Required" list.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** (ask first): `feat(dashboard): parse and validate the recording`

### Task 3: `stateAt`: what is true at simulated time t

**Files:** Create `dashboard/src/data/stateAt.ts`; Test `dashboard/src/data/stateAt.test.ts`

**Interfaces — Consumes:** Task 2's types and `toMs`. **Produces:**
```ts
export const CARD_MIN_CONFIDENCE = 0.6
export interface DeviceNow { device: Device; values: Record<string, DeviceValue["value"]>; changedAt: number | null }  // changedAt: ms of the last change if within 10 simulated min of t
export interface SensorNow { sensor: Sensor; readings: Reading[]; daysSoFar: number; justLearned: boolean }
export interface StateAt {
  t: number; startMs: number; endMs: number; atEnd: boolean;
  day: number; days: number;                 // day 1..days, from meta.start's date
  devices: DeviceNow[];
  cards: SensorNow[];                         // visible, confidence >= 0.6, newest learned first
  alsoNoticed: SensorNow[];                   // visible, below 0.6
  learnDays: number[];                        // day numbers on which a sensor appears (for scrubber ticks)
}
export function appearsAt(sensor: Sensor): number   // toMs(learned_at) + 24 h
export function stateAt(rec: Recording, t: number): StateAt   // t is clamped to [startMs, endMs]
```

- [ ] **Step 1: Failing tests** (build a small recording in the test file with a `rec()` helper):
  - `device state applies events up to t`: plug 0 W at start, event 2105 at 07:59:39 → at 07:59:38 value 0, at 07:59:39 value 2105.
  - `marks a change for 10 simulated minutes`: `changedAt` set at 08:09:39, `null` at 08:09:40.
  - `sensor appears at midnight after learned_at`: learned_at `2026-09-06` → hidden at `2026-09-06T23:59:59`, visible at `2026-09-07T00:00:00` with `justLearned: true`; `justLearned: false` at `2026-09-08T00:00:00`.
  - `visible sensor carries earlier readings up to t`: readings 09-01 and 09-08, t = 09-07 → only the 09-01 reading; `daysSoFar` 1.
  - `daysSoFar counts distinct days`: two readings on one day → 1.
  - `ranks cards and also-noticed`: `a` (0.84, learned 09-06), `b` (0.59, 09-07), `c` (0.6, 09-08), t = 09-10 → `cards` ids `["c", "a"]` (newest learned first, 0.6 counts as a card), `alsoNoticed` ids `["b"]`.
  - `hides merged, keeps fading`.
  - `clamps t and flags the end`: t past `meta.end` → `t === endMs`, `atEnd: true`; a sensor learned on the last day is visible only at `atEnd`.
  - `day numbers across a month and a year`: start `2026-12-30T00:00:00`, t `2027-01-01T10:00:00` → `day: 3`.
  - `learnDays` lists the day numbers on which sensors appear, sorted and unique.
- [ ] **Step 2:** Run `npx vitest run src/data/stateAt.test.ts` → FAIL.
- [ ] **Step 3:** Implement. Rule for the last day: a sensor whose `appearsAt > endMs` is visible when `atEnd`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** (ask first): `feat(dashboard): compute the home and sensors at any simulated moment`

### Task 4: Plain words and the playback clock

**Files:** Create `dashboard/src/data/words.ts`, `dashboard/src/playback/clock.ts`, `dashboard/src/playback/usePlayback.ts`; Test `words.test.ts`, `clock.test.ts`, `usePlayback.test.tsx`

**Interfaces — Produces:**
```ts
// words.ts
export function sensorTitle(s: Sensor): { text: string; unnamed: boolean }     // "Pattern 2" from "pattern-2"
export function partToWords(part: string): string
export function evidence(parts: string[]): string                               // joined with ", then "
export function deviceWords(type: string, capability: string, value: DeviceValue["value"]): string
export function formatClock(t: number): { weekday: string; time: string }      // "Wed", "08:12" (UTC wall clock)
// clock.ts
export const SPEEDS = [1, 2, 4] as const          // simulated days per 10 real seconds
export type Speed = typeof SPEEDS[number]
export function advance(t: number, realDtMs: number, speed: Speed, endMs: number): number   // clamped
export function dayStart(startMs: number, day: number): number
// usePlayback.ts
export function usePlayback(startMs: number, endMs: number): { t: number; playing: boolean; speed: Speed; play(): void; pause(): void; toggle(): void; setSpeed(s: Speed): void; jumpToDay(d: number): void; jumpToEnd(): void }
```

- [ ] **Step 1: Failing tests** — exact strings from the spec:
  - `partToWords`: `"bulb-A (power on, about 43 min)"` → `"lamp on, about 43 min"`; `"plug-A (about 2,100 W, about 3 min)"` → `"plug about 2,100 W, about 3 min"`; `"contactSensor-A (sensorState alarm, about 40 s)"` → `"door open, about 40 s"`; `"home-A (quiet after bulb until bulb, about 9.1 h)"` → `"the home quiet, about 9.1 h"`; `"end of home-A (quiet after bulb until bulb, about 9.1 h)"` → `"end of the home quiet"`; `"weird thing"` → `"weird thing"`.
  - `evidence(["bulb-A (power on, about 43 min)","plug-A (about 2,100 W, about 3 min)"])` → `"lamp on, about 43 min, then plug about 2,100 W, about 3 min"`.
  - `deviceWords`: contactSensor/sensorState `normal` → `"closed"`, `alarm` → `"open"`; plug/instantPower 2105 → `"2,105 W, in use"`, 3 → `"3 W"`; temperatureSensor/temperatureValue 21.43 → `"21.4 °C"`; humidityValue 48 → `"48.0%"`; unknown → `String(value)`; `null` → `"—"`.
  - `sensorTitle` with `name: "Morning tea"` → `{text: "Morning tea", unnamed: false}`.
  - `formatClock(Date.UTC(2026, 8, 9, 8, 12))` → `{weekday: "Wed", time: "08:12"}`.
  - `advance`: speed 1, 10 000 ms real → +1 day; speed 4, 2 500 ms → +1 day; never past `endMs`.
  - `usePlayback` (fake timers, `vi.stubGlobal("requestAnimationFrame", …)`): `toggle` then 10 s of frames at speed 1 → `t` advanced one day; reaching `endMs` sets `playing` false; `jumpToDay(3)` → `t === dayStart(start, 3)`; Space keydown toggles; ArrowRight moves one day.
- [ ] **Step 2:** Run the three test files → FAIL.
- [ ] **Step 3:** Implement. Number formatting with `toLocaleString("en-US")`; no `Date.now()` for simulated time (the hook may use the rAF timestamp for real elapsed time).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** (ask first): `feat(dashboard): plain words for sensors and devices, and the playback clock`

### Task 5: The mock recording

**Files:** Create `dashboard/public/recording.json` (generated); Test `dashboard/src/data/mock.test.ts`. The generator lives in the scratchpad, not the repo.

- [ ] **Step 1: Failing test** `mock.test.ts`: read `public/recording.json` with `fs`, `parseRecording` it, assert: `meta.days === 21`, `meta.simulated === true`, no sensor with id `pattern-10`, exactly 5 sensors with confidence ≥ 0.6, every reading's `sensor_id` is a known sensor, `events` sorted by `ts`.
- [ ] **Step 2:** Run → FAIL (file missing).
- [ ] **Step 3:** Run the 21-day rosa pipeline into the scratchpad and a throwaway script (`python3 -I`) that writes the JSON: `meta` from the run, `devices` from the `devices` table, `initial` from the first snapshot (normalized), `events` from the `events` table (kind `change`/`initial`, `new` as `value`, ts formatted as naive Lisbon ISO via `source.clock`), `sensors`/`readings` from `export(db, date(2026,9,21))` minus `pattern-10` and its readings.
- [ ] **Step 4:** Run → PASS. Check the file size (expect well under 1 MB).
- [ ] **Step 5: Commit** (ask first): `feat(dashboard): a mock recording from a 21-day simulated run`

### Task 6: The stage screen

**Files:** Create `dashboard/src/screens/Stage.tsx`, `dashboard/src/components/{Header,Controls,DeviceList,SensorCard,AlsoNoticed,StillLearning,Scrubber}.tsx`; Tests beside each (`*.test.tsx`)

**Interfaces — Consumes:** `StateAt`, `SensorNow`, `DeviceNow` (Task 3), words and `usePlayback` return type (Task 4). **Produces:** `Stage({rec, state, playback})`.

Markup follows the design system's previews: `al-header` + `al-brand`, `al-pill learning`, `al-clock compact` (label "Simulated"); `al-list no-time` for devices; `al-card` (+ `just-learned`, `al-new` "Learned today") for cards; `al-counter` inside `al-empty` for still learning; buttons `al-btn`.

- [ ] **Step 1: Failing tests:**
  - Header shows "Simulated", "day 9 of 21", weekday and time.
  - Controls: play button `aria-label="Play"` becomes "Pause" when playing; speed buttons 1×/2×/4× with `aria-pressed`; "Jump to the end" calls `jumpToEnd`.
  - DeviceList renders each device's name and `deviceWords`; a changed device shows "changed 08:12".
  - SensorCard: title "Pattern 2" with unnamed style, "around 07:59", "± 20 min", evidence sentence, "seen on 7 days so far", "84%"; at `atEnd` shows "16 of 19 days" instead; `justLearned` adds class `just-learned` and the text "Learned today"; the card is a link to `#/sensor/pattern-2`; no "looks like" line when `explanation` is null.
  - AlsoNoticed: a `<details>` with summary "Also noticed (4)".
  - Stage with zero cards shows "Still learning" and the counter with `aria-label="day 3 of 21"`; with zero cards but some also-noticed, both show.
  - Scrubber: an `<input type="range">` with `aria-label="Simulated day"`, min 1, max 21, and one tick per `learnDays` entry; changing it calls `jumpToDay`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement; layout in `dashboard.css`: two columns at ≥ 1024 px, one below. For projector size, use the design system's larger styles (`display` 36 for the page title, `heading` 24 for section titles, `figure` 20 for device values) rather than overriding token sizes.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** (ask first): `feat(dashboard): the stage screen with devices, cards and playback controls`

### Task 7: Sensor detail, loading and wiring

**Files:** Create `dashboard/src/screens/SensorDetail.tsx`, `dashboard/src/components/{DayTimeline,DaysSeen}.tsx`, `dashboard/src/data/load.ts`; Modify `dashboard/src/App.tsx`; Tests beside each.

**Interfaces — Produces:** `loadRecording(fetcher = fetch): Promise<Recording>` (fetches `./recording.json`, throws `RecordingError` with "could not load recording.json" on HTTP errors). `App` holds `usePlayback` above `<HashRouter>` with routes `/` → Stage, `/sensor/:id` → SensorDetail.

- [ ] **Step 1: Failing tests:**
  - `loadRecording` with a fake fetcher returning 404 → rejects with "could not load recording.json"; returning invalid JSON shape → rejects with the field message.
  - App with a failing loader shows `role="alert"` with the message; while loading shows "Loading the recording"; on success shows the header.
  - SensorDetail for `pattern-2`: title, evidence, `ConfidenceBar` markup (`al-conf`, "84%"), a `DayTimeline` with `aria-label` "usually between 07:39 and 08:19" (time ± spread) and one `al-hit` per reading so far, `DaysSeen` with the last 14 days, a "Back" link to `#/`, and an `al-later` note "Naming comes later".
  - Unknown id → "This sensor isn't in the recording" and a back link.
  - Navigating to detail and back keeps the playhead (render App with MemoryRouter-equivalent hash, assert `t` unchanged).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** (ask first): `feat(dashboard): sensor detail and loading the recording`

### Task 8: Finish: coverage, build, docs

**Files:** Modify `README.md` (repo structure + run commands), Create `dashboard/README.md` (run, build, swap the recording).

- [ ] **Step 1:** `npm run typecheck` → no errors. `npm run test:coverage` → `src/data` and `src/playback` ≥ 90%, components ≥ 80%; add tests for any gap.
- [ ] **Step 2:** `npm run build` → `dist/` created; `npm run preview` serves it; `dist/recording.json` present.
- [ ] **Step 3:** `dashboard/README.md`: `npm install`, `npm run dev`, `npm run build && npm run preview` for the pitch, and "to show a new run, replace `public/recording.json` (or `dist/recording.json`) with a file in the same shape".
- [ ] **Step 4:** Repo `README.md`: add `dashboard/` to the structure and the Run section.
- [ ] **Step 5: Commit** (ask first): `docs(dashboard): how to run, build and swap the recording`
