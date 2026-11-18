import { expect, test } from "vitest";
import { DAY_MS } from "../data/stateAt";
import { advance, dayStart, holdFor, HOLD_REAL_MS } from "./clock";

const start = Date.UTC(2026, 8, 1);
const end = Date.UTC(2026, 8, 21, 23, 59, 59);

test("one simulated day per 10 real seconds at 1×", () => {
  expect(advance(start, 10_000, 1, end)).toBe(start + DAY_MS);
});

test("four times as fast at 4×", () => {
  expect(advance(start, 2_500, 4, end)).toBe(start + DAY_MS);
});

test("never past the end", () => {
  expect(advance(end - 1000, 10_000, 4, end)).toBe(end);
});

test("day starts at its midnight", () => {
  expect(dayStart(start, 1)).toBe(start);
  expect(dayStart(start + 5 * 3600_000, 3)).toBe(start + 2 * DAY_MS);
});

test("a hold of 3 real seconds spans more simulated time at higher speed", () => {
  expect(HOLD_REAL_MS).toBe(3_000);
  expect(holdFor(1)).toBe(0.3 * DAY_MS);
  expect(holdFor(4)).toBe(1.2 * DAY_MS);
});
