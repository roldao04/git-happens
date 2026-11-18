import type { Reading } from "./recording";
import { toMs } from "./recording";
import { DAY_MS } from "./stateAt";
import { formatDate } from "./words";

export const SHOWN_DAYS = 14;
const DAY_MIN = 24 * 60;

export interface Seen { start: number; end: number | null }
export interface DayRow { dayMs: number; label: string; today: boolean; seen: Seen[] }

export function recentDays(readings: Reading[], t: number, startMs: number, n: number = SHOWN_DAYS): DayRow[] {
  const today = t - (t % DAY_MS);
  const first = Math.max(today - (n - 1) * DAY_MS, startMs - (startMs % DAY_MS));
  const rows: DayRow[] = [];
  for (let dayMs = first; dayMs <= today; dayMs += DAY_MS) {
    const seen = readings
      .map((r) => ({ start: toMs(r.start), end: r.end ? toMs(r.end) : null }))
      .filter((s) => s.start >= dayMs && s.start < dayMs + DAY_MS && s.start <= t)
      .sort((a, b) => a.start - b.start);
    rows.push({ dayMs, label: formatDate(dayMs).replace(/ \w+$/, ""), today: dayMs === today, seen });
  }
  return rows;
}

export function chartSpan(spreadMin: number | undefined): number {
  return Math.min(DAY_MIN / 2, Math.max(60, 3 * (spreadMin ?? 0)));
}

export function minuteOfDay(ms: number): number {
  return (ms % DAY_MS) / 60_000;
}

export function clockOf(min: number): string {
  const m = ((Math.round(min) % DAY_MIN) + DAY_MIN) % DAY_MIN;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export function toMin(hhmm: string): number {
  const [h = 0, m = 0] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function chartPlace(minOfDay: number, usual: string, span: number): number {
  const off = ((((minOfDay - toMin(usual) + DAY_MIN / 2) % DAY_MIN) + DAY_MIN) % DAY_MIN) - DAY_MIN / 2;
  const clamped = Math.min(Math.max(off, -span), span);
  return ((clamped + span) / (2 * span)) * 100;
}
