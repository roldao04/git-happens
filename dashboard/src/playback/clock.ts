import { CHANGED_FOR_MS, DAY_MS } from "../data/stateAt";

export const SPEEDS = [1, 2, 4] as const;
export type Speed = (typeof SPEEDS)[number];
const REAL_MS_PER_DAY = 10_000;

export function advance(t: number, realDtMs: number, speed: Speed, endMs: number): number {
  return Math.min(t + (realDtMs * speed * DAY_MS) / REAL_MS_PER_DAY, endMs);
}

export function dayStart(startMs: number, day: number): number {
  return startMs - (startMs % DAY_MS) + (day - 1) * DAY_MS;
}

export const HOLD_REAL_MS = 3_000;

export function holdFor(speed: Speed, playing: boolean = true): number {
  if (!playing) return CHANGED_FOR_MS;
  return (HOLD_REAL_MS * speed * DAY_MS) / REAL_MS_PER_DAY;
}
