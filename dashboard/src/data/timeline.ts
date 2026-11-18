import type { Device, Recording, Sensor, Value } from "./recording";
import { toMs } from "./recording";
import { activeCapability, wentActive } from "./shown";
import { DAY_MS, type StateAt } from "./stateAt";

const LANE_TYPES = new Set(["bulb", "contactSensor", "plug"]);

export interface Span { start: number; end: number | null }
export interface Lane { device: Device; spans: Span[] }
export interface Signal { at: number; device: Device; capability: string; value: Value }
export interface Entry { at: number; end: number | null; sensor: Sensor; weak: boolean; signals: Signal[] }
export interface DayView { day: number; dayStart: number; dayEnd: number; upTo: number; lanes: Lane[]; entries: Entry[] }

function clampDay(day: number, last: number): number {
  if (!Number.isFinite(day)) return last;
  return Math.min(Math.max(Math.trunc(day), 1), last);
}

interface Switch extends Signal { active: boolean }

function switchesUpTo(rec: Recording, upTo: number): Switch[] {
  const byId = new Map(rec.devices.map((d) => [d.id, d]));
  const active = new Map<string, boolean>();
  for (const v of rec.initial) {
    const device = byId.get(v.device_id);
    if (device && v.capability === activeCapability(device.type)) active.set(device.id, wentActive(device.type, v.capability, v.value));
  }
  const switches: Switch[] = [];
  for (const e of rec.events) {
    const device = byId.get(e.device_id);
    const at = toMs(e.ts);
    if (at > upTo) break;
    if (!device || e.capability !== activeCapability(device.type)) continue;
    const now = wentActive(device.type, e.capability, e.value);
    if (now === (active.get(device.id) ?? false)) continue;
    active.set(device.id, now);
    switches.push({ at, device, capability: e.capability, value: e.value, active: now });
  }
  return switches;
}

function spansOf(rec: Recording, device: Device, switches: Switch[], dayStart: number, upTo: number): Span[] {
  const spans: Span[] = [];
  const startsActive = rec.initial.some((v) => v.device_id === device.id && v.capability === activeCapability(device.type) && wentActive(device.type, v.capability, v.value));
  let since: number | null = startsActive ? toMs(rec.meta.start) : null;
  for (const c of switches) {
    if (c.device.id !== device.id) continue;
    if (c.active) since = c.at;
    else if (since !== null) {
      spans.push({ start: since, end: c.at });
      since = null;
    }
  }
  if (since !== null) spans.push({ start: since, end: null });
  return spans
    .filter((s) => (s.end === null || s.end > dayStart) && s.start <= upTo)
    .map((s) => ({ start: Math.max(s.start, dayStart), end: s.end }));
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
          return { at, end, sensor: s.sensor, weak, signals: switches.filter((c) => c.at >= at && c.at <= until).map(({ active: _active, ...signal }) => signal) };
        }),
    )
    .sort((a, b) => a.at - b.at || a.sensor.id.localeCompare(b.sensor.id));
  return { day, dayStart, dayEnd, upTo, lanes, entries };
}
