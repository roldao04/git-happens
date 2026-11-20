import type { Device, Recording, Sensor, Value } from "./recording";
import { toMs } from "./recording";
import { activeCapability, wentActive } from "./shown";
import { DAY_MS, type StateAt } from "./stateAt";

const LANE_TYPES = new Set(["bulb", "contactSensor", "plug"]);

export interface Span { start: number; end: number | null; color?: string }
export interface Unwatched { start: number; end: number }
export interface Lane { device: Device; spans: Span[] }
export interface Signal { at: number; device: Device; capability: string; value: Value }
export interface Entry { at: number; end: number | null; sensor: Sensor; weak: boolean; signals: Signal[] }
export interface Point { at: number; value: number }
export interface Measure { label: string; unit: string; low: number; high: number; points: Point[] }
export interface DayView { day: number; dayStart: number; upTo: number; lanes: Lane[]; measures: Measure[]; entries: Entry[]; gaps: Unwatched[] }

const MEASURES = [
  { capability: "temperatureValue", label: "Temperature", unit: "°C" },
  { capability: "humidityValue", label: "Humidity", unit: "%" },
] as const;

function clampDay(day: number, last: number): number {
  if (!Number.isFinite(day)) return last;
  return Math.min(Math.max(Math.trunc(day), 1), last);
}

interface Switch extends Signal { active: boolean; signalOnly: boolean }

function switchesUpTo(rec: Recording, upTo: number): Switch[] {
  const byId = new Map(rec.devices.map((d) => [d.id, d]));
  const on = new Map<string, boolean>();
  const lost = new Set<string>();
  const active = new Map<string, boolean>();
  for (const v of rec.initial) {
    const device = byId.get(v.device_id);
    if (!device) continue;
    if (v.capability === activeCapability(device.type)) on.set(device.id, wentActive(device.type, v.capability, v.value));
    if (v.capability === "reachable" && v.value === false) lost.add(device.id);
  }
  for (const [id, isOn] of on) active.set(id, isOn && !lost.has(id));
  const switches: Switch[] = [];
  for (const e of rec.events) {
    const device = byId.get(e.device_id);
    const at = toMs(e.ts);
    if (at > upTo) break;
    if (!device) continue;
    const signal = e.capability === "reachable";
    if (signal) {
      if (e.value === false) lost.add(device.id);
      else lost.delete(device.id);
    } else if (e.capability === activeCapability(device.type)) on.set(device.id, wentActive(device.type, e.capability, e.value));
    else continue;
    const now = (on.get(device.id) ?? false) && !lost.has(device.id);
    if (now === (active.get(device.id) ?? false)) continue;
    active.set(device.id, now);
    switches.push({ at, device, capability: e.capability, value: e.value, active: now, signalOnly: signal });
  }
  return switches;
}

interface Tint { at: number; color: string | null }

function fullStrength(rgb: string): string | null {
  const parts = rgb.split(",").map((p) => Number(p.trim()));
  if (parts.length !== 3 || parts.some((p) => !Number.isFinite(p))) return null;
  const top = Math.max(...parts);
  return top <= 0 ? null : parts.map((p) => Math.round((p * 255) / top)).join(",");
}

function tintsOf(rec: Recording, device: Device, upTo: number): Tint[] {
  let mode: Value = null;
  let rgb: Value = null;
  const now = (): string | null => (mode === "colour" && typeof rgb === "string" ? fullStrength(rgb) : null);
  const take = (capability: string, value: Value) => {
    if (capability === "workMode") mode = value;
    else if (capability === "color_rgb") rgb = value;
  };
  for (const v of rec.initial) if (v.device_id === device.id) take(v.capability, v.value);
  const tints: Tint[] = [{ at: Number.NEGATIVE_INFINITY, color: now() }];
  for (const e of rec.events) {
    const at = toMs(e.ts);
    if (at > upTo) break;
    if (e.device_id !== device.id || (e.capability !== "workMode" && e.capability !== "color_rgb")) continue;
    take(e.capability, e.value);
    const color = now();
    if (color !== tints[tints.length - 1]?.color) tints.push({ at, color });
  }
  return tints;
}

function tinted(span: Span, tints: Tint[]): Span[] {
  const end = span.end ?? Number.POSITIVE_INFINITY;
  const cuts = tints.filter((t) => t.at > span.start && t.at < end).map((t) => t.at);
  const starts = [span.start, ...cuts];
  return starts.map((start, i) => {
    const color = [...tints].reverse().find((t) => t.at <= start)?.color ?? null;
    const pieceEnd = starts[i + 1] ?? span.end;
    return color === null ? { start, end: pieceEnd } : { start, end: pieceEnd, color };
  });
}

function spansOf(rec: Recording, device: Device, switches: Switch[], dayStart: number, upTo: number): Span[] {
  const spans: Span[] = [];
  const startsLost = rec.initial.some((v) => v.device_id === device.id && v.capability === "reachable" && v.value === false);
  const startsActive = !startsLost && rec.initial.some((v) => v.device_id === device.id && v.capability === activeCapability(device.type) && wentActive(device.type, v.capability, v.value));
  const firstReading = rec.gaps.find((g) => g.start === rec.meta.start)?.end ?? rec.meta.start;
  let since: number | null = startsActive ? toMs(firstReading) : null;
  for (const c of switches) {
    if (c.device.id !== device.id) continue;
    if (c.active) since = c.at;
    else if (since !== null) {
      spans.push({ start: since, end: c.at });
      since = null;
    }
  }
  if (since !== null) spans.push({ start: since, end: null });
  const tints = device.type === "bulb" ? tintsOf(rec, device, upTo) : [];
  return spans
    .flatMap((s) => (tints.length > 0 ? tinted(s, tints) : [s]))
    .filter((s) => (s.end === null || s.end > dayStart) && s.start <= upTo)
    .map((s) => ({ ...s, start: Math.max(s.start, dayStart) }));
}

export interface Scale { min: number; max: number; ticks: number[] }

const SCALE_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100];
const EPSILON = 1e-9;
const tidy = (n: number) => Math.round(n * 1000) / 1000;

export function scaleFor(low: number, high: number): Scale {
  const [from, to] = low === high ? [low - 0.5, high + 0.5] : [low, high];
  for (const step of SCALE_STEPS) {
    const first = Math.floor(from / step + EPSILON);
    const last = Math.ceil(to / step - EPSILON);
    if (last - first > 4) continue;
    const ticks = Array.from({ length: last - first + 1 }, (_, i) => tidy((first + i) * step));
    return { min: tidy(first * step), max: tidy(last * step), ticks };
  }
  return { min: from, max: to, ticks: [from, to] };
}

function measuresOf(rec: Recording, dayStart: number, upTo: number): Measure[] {
  const sensors = new Set(rec.devices.filter((d) => d.type === "temperatureSensor").map((d) => d.id));
  return MEASURES.flatMap(({ capability, label, unit }) => {
    let before: number | null = null;
    for (const v of rec.initial) if (sensors.has(v.device_id) && v.capability === capability && typeof v.value === "number") before = v.value;
    const points: Point[] = [];
    for (const e of rec.events) {
      if (!sensors.has(e.device_id) || e.capability !== capability || typeof e.value !== "number") continue;
      const at = toMs(e.ts);
      if (at > upTo) break;
      if (at < dayStart) before = e.value;
      else points.push({ at, value: e.value });
    }
    if (before !== null && points[0]?.at !== dayStart) points.unshift({ at: dayStart, value: before });
    const last = points[points.length - 1];
    if (last === undefined) return [];
    if (last.at < upTo) points.push({ at: upTo, value: last.value });
    const values = points.map((p) => p.value);
    return [{ label, unit, low: Math.min(...values), high: Math.max(...values), points }];
  });
}

export function dayView(rec: Recording, state: StateAt, dayWanted: number): DayView {
  const day = clampDay(dayWanted, state.day);
  const dayStart = state.startMs - (state.startMs % DAY_MS) + (day - 1) * DAY_MS;
  const dayEnd = dayStart + DAY_MS;
  const upTo = Math.min(dayEnd, state.t);
  const switches = switchesUpTo(rec, upTo);
  const lanes = rec.devices
    .filter((d) => LANE_TYPES.has(d.type))
    .map((device) => ({ device, spans: spansOf(rec, device, switches, dayStart, upTo) }));
  const visible = [
    ...state.cards.map((s) => ({ s, weak: false })),
    ...state.alsoNoticed.map((s) => ({ s, weak: true })),
  ];
  const entries = visible
    .flatMap(({ s, weak }) =>
      rec.readings
        .filter((r) => r.sensor_id === s.sensor.id)
        .map((r) => ({ r, at: toMs(r.start) }))
        .filter(({ at }) => at >= dayStart && at <= upTo)
        .map(({ r, at }): Entry => {
          const endMs = r.end === null ? null : toMs(r.end);
          const end = endMs !== null && endMs <= upTo ? endMs : null;
          const until = end ?? upTo;
          return { at, end, sensor: s.sensor, weak, signals: switches.filter((c) => !c.signalOnly && c.at >= at && c.at <= until).map(({ active: _active, signalOnly: _signalOnly, ...signal }) => signal) };
        }),
    )
    .sort((a, b) => a.at - b.at || a.sensor.id.localeCompare(b.sensor.id));
  const gaps = rec.gaps
    .map((g) => ({ start: toMs(g.start), end: toMs(g.end) }))
    .filter((g) => g.end > dayStart && g.start < upTo)
    .map((g) => ({ start: Math.max(g.start, dayStart), end: Math.min(g.end, upTo) }));
  return { day, dayStart, upTo, lanes, measures: measuresOf(rec, dayStart, upTo), entries, gaps };
}

export interface Zoom { start: number; end: number }

const MINUTE_MS = 60_000;
const TICK_STEPS_MIN = [1, 2, 5, 10, 15, 30, 60, 120, 180, 360];
export const MIN_ZOOM_MS = 5 * MINUTE_MS;

export function ticksFor({ start, end }: Zoom): number[] {
  const minutes = (end - start) / MINUTE_MS;
  const step = (TICK_STEPS_MIN.find((s) => minutes / s <= 4) ?? 360) * MINUTE_MS;
  const ticks: number[] = [];
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) ticks.push(t);
  return ticks;
}

export function timeAt(x: number, left: number, width: number, { start, end }: Zoom): number {
  const share = width > 0 ? Math.min(Math.max((x - left) / width, 0), 1) : 0;
  return Math.round((start + share * (end - start)) / MINUTE_MS) * MINUTE_MS;
}

export function zoomBetween(a: number, b: number): Zoom | null {
  return Math.abs(b - a) < MIN_ZOOM_MS ? null : { start: Math.min(a, b), end: Math.max(a, b) };
}
