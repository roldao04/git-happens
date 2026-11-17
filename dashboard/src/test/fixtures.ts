import { vi } from "vitest";
import type { Recording, Sensor } from "../data/recording";
import type { SensorNow, StateAt } from "../data/stateAt";
import type { Playback } from "../playback/usePlayback";

export const START = Date.UTC(2026, 8, 1);
export const END = Date.UTC(2026, 8, 21, 23, 59, 59);
export const DAY9_0812 = Date.UTC(2026, 8, 9, 8, 12);

export function sensor(over: Partial<Sensor> = {}): Sensor {
  return {
    id: "pattern-2",
    name: null,
    learned_at: "2026-09-06",
    explanation: null,
    confidence: 0.84,
    status: "active",
    pattern: {
      parts: ["bulb-A (power on, about 43 min)", "plug-A (about 2,100 W, about 3 min)"],
      time_of_day: "07:59",
      spread_min: 20,
      days_seen: 16,
      days_total: 19,
    },
    ...over,
  };
}

export function sensorNow(over: Partial<SensorNow> = {}): SensorNow {
  return { sensor: sensor(), readings: [], daysSoFar: 7, justLearned: false, ...over };
}

export function state(over: Partial<StateAt> = {}): StateAt {
  return {
    t: DAY9_0812,
    startMs: START,
    endMs: END,
    atEnd: false,
    day: 9,
    days: 21,
    devices: [],
    cards: [],
    alsoNoticed: [],
    learnDays: [],
    latest: null,
    ...over,
  };
}

export function playback(over: Partial<Playback> = {}): Playback {
  return {
    t: DAY9_0812,
    playing: false,
    speed: 1,
    play: vi.fn(),
    pause: vi.fn(),
    toggle: vi.fn(),
    setSpeed: vi.fn(),
    jumpToDay: vi.fn(),
    jumpToEnd: vi.fn(),
    ...over,
  };
}

export function recording(over: Partial<Recording> = {}): Recording {
  return {
    meta: { household: "rosa", simulated: true, start: "2026-09-01T00:00:00", end: "2026-09-21T23:59:59", days: 21 },
    devices: [],
    initial: [],
    events: [],
    sensors: [],
    readings: [],
    ...over,
  };
}
