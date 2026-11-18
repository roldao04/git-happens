import { describe, expect, test } from "vitest";
import type { Reading } from "./recording";
import { toMs } from "./recording";
import { chartPlace, chartSpan, recentDays } from "./days";

const r = (start: string, end: string | null = null): Reading => ({ sensor_id: "pattern-2", start, end, value: null });
const START = toMs("2026-09-01T00:00:00");

describe("recentDays", () => {
  test("gives the last 14 days, oldest first, with today last", () => {
    const rows = recentDays([], toMs("2026-09-20T08:00:00"), START);
    expect(rows).toHaveLength(14);
    expect(rows[0]?.label).toBe("Mon 7");
    expect(rows[13]).toMatchObject({ label: "Sun 20", today: true });
  });

  test("starts at the first day of the run when fewer days have gone by", () => {
    const rows = recentDays([], toMs("2026-09-03T08:00:00"), START);
    expect(rows.map((d) => d.label)).toEqual(["Tue 1", "Wed 2", "Thu 3"]);
  });

  test("puts each reading on its day, with its end when known", () => {
    const rows = recentDays([r("2026-09-02T07:59:00", "2026-09-02T08:24:00"), r("2026-09-03T08:01:00")], toMs("2026-09-03T09:00:00"), START);
    expect(rows[0]?.seen).toEqual([]);
    expect(rows[1]?.seen).toEqual([{ start: toMs("2026-09-02T07:59:00"), end: toMs("2026-09-02T08:24:00") }]);
    expect(rows[2]?.seen).toEqual([{ start: toMs("2026-09-03T08:01:00"), end: null }]);
  });

  test("leaves out readings after the playhead", () => {
    const rows = recentDays([r("2026-09-03T08:01:00")], toMs("2026-09-03T08:00:00"), START);
    expect(rows[2]?.seen).toEqual([]);
  });
});

describe("chart span", () => {
  test("is three spreads either side of the usual time, at least an hour", () => {
    expect(chartSpan(20)).toBe(60);
    expect(chartSpan(5)).toBe(60);
    expect(chartSpan(undefined)).toBe(60);
    expect(chartSpan(100)).toBe(300);
  });

  test("never more than half a day either side", () => {
    expect(chartSpan(400)).toBe(720);
  });
});

describe("chartPlace", () => {
  test("puts the usual time in the middle and the span at the edges", () => {
    expect(chartPlace(8 * 60, "08:00", 60)).toBe(50);
    expect(chartPlace(7 * 60, "08:00", 60)).toBe(0);
    expect(chartPlace(9 * 60, "08:00", 60)).toBe(100);
  });

  test("wraps around midnight", () => {
    expect(chartPlace(10, "23:50", 60)).toBeCloseTo(50 + (20 / 120) * 100);
  });

  test("keeps times outside the span on the edge", () => {
    expect(chartPlace(12 * 60, "08:00", 60)).toBe(100);
    expect(chartPlace(3 * 60, "08:00", 60)).toBe(0);
  });
});
