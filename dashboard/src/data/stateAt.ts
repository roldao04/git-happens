import type { Device, DeviceEvent, Reading, Recording, Sensor, Value } from "./recording";
import { toMs } from "./recording";
import { marksChange, wentActive } from "./shown";

export const CARD_MIN_CONFIDENCE = 0.6;
export const DAY_MS = 24 * 60 * 60 * 1000;
export const CHANGED_FOR_MS = 10 * 60 * 1000;

export interface Latest { device: Device; capability: string; value: Value; at: number }
export interface DeviceNow { device: Device; values: Record<string, Value>; changedAt: number | null; since: number | null }
export type FeedItem =
  | { kind: "device"; at: number; device: Device; capability: string; value: Value }
  | { kind: "habit"; at: number; sensor: Sensor };
export interface SensorNow { sensor: Sensor; readings: Reading[]; daysSoFar: number; justLearned: boolean }
export interface StateAt {
  t: number;
  startMs: number;
  endMs: number;
  atEnd: boolean;
  day: number;
  days: number;
  devices: DeviceNow[];
  cards: SensorNow[];
  alsoNoticed: SensorNow[];
  learnDays: number[];
  latest: Latest | null;
  today: FeedItem[];
}

export function appearsAt(sensor: Sensor): number {
  return toMs(sensor.learned_at.slice(0, 10)) + DAY_MS;
}

function midnight(ms: number): number {
  return ms - (ms % DAY_MS);
}

export function dayNumber(startMs: number, t: number): number {
  return Math.floor((midnight(t) - midnight(startMs)) / DAY_MS) + 1;
}

function heardOrNot(v: Record<string, Value>): Record<string, Value> {
  if (v.reachable !== false) return v;
  return v.lastHeard === undefined ? { reachable: false } : { reachable: false, lastHeard: v.lastHeard };
}

interface DevicesNow { devices: DeviceNow[]; latest: Latest | null; deviceFeed: FeedItem[] }

function devicesAt(rec: Recording, t: number, holdMs: number): DevicesNow {
  const values = new Map<string, Record<string, Value>>();
  const last = new Map<string, number>();
  const byId = new Map(rec.devices.map((d) => [d.id, d]));
  const isActive = new Set<string>();
  const since = midnight(t);
  const deviceFeed: FeedItem[] = [];
  let active: DeviceEvent | null = null;
  for (const v of rec.initial) values.set(v.device_id, { ...values.get(v.device_id), [v.capability]: v.value });
  for (const e of rec.events) {
    const ts = toMs(e.ts);
    if (ts > t) break;
    values.set(e.device_id, { ...values.get(e.device_id), [e.capability]: e.value });
    const type = byId.get(e.device_id)?.type ?? "";
    if (marksChange(type, e.capability)) {
      last.set(e.device_id, ts);
      const device = byId.get(e.device_id);
      if (!wentActive(type, e.capability, e.value)) isActive.delete(e.device_id);
      else {
        active = e;
        if (!isActive.has(e.device_id) && ts >= since && device)
          deviceFeed.push({ kind: "device", at: ts, device, capability: e.capability, value: e.value });
        isActive.add(e.device_id);
      }
    }
  }
  const devices = rec.devices.map((device) => {
    const changed = last.get(device.id);
    return {
      device,
      values: heardOrNot(values.get(device.id) ?? {}),
      changedAt: changed !== undefined && t - changed <= holdMs ? changed : null,
      since: changed ?? null,
    };
  });
  const device = active ? byId.get(active.device_id) : undefined;
  const at = active ? toMs(active.ts) : 0;
  const latest = active && device && t - at <= holdMs ? { device, capability: active.capability, value: active.value, at } : null;
  return { devices, latest, deviceFeed };
}

function habitFeed(cards: SensorNow[], t: number): FeedItem[] {
  const since = midnight(t);
  return cards.flatMap(({ sensor, readings }) =>
    readings.map((r) => toMs(r.start)).filter((ms) => ms >= since).map((at): FeedItem => ({ kind: "habit", at, sensor })),
  );
}

function sensorNow(sensor: Sensor, readings: Reading[], t: number): SensorNow {
  const upTo = readings.filter((r) => toMs(r.start) <= t);
  const shown = appearsAt(sensor);
  return {
    sensor,
    readings: upTo,
    daysSoFar: new Set(upTo.map((r) => r.start.slice(0, 10))).size,
    justLearned: t >= shown && t < shown + DAY_MS,
  };
}

export function stateAt(rec: Recording, tRaw: number, holdMs: number = CHANGED_FOR_MS): StateAt {
  const startMs = toMs(rec.meta.start);
  const endMs = toMs(rec.meta.end);
  const t = Math.min(Math.max(tRaw, startMs), endMs);
  const atEnd = t === endMs;
  const live = rec.sensors.filter((s) => s.status !== "merged");
  const bySensor = new Map<string, Reading[]>();
  for (const r of rec.readings) bySensor.set(r.sensor_id, [...(bySensor.get(r.sensor_id) ?? []), r]);

  const visible = live
    .filter((s) => atEnd || appearsAt(s) <= t)
    .sort((a, b) => appearsAt(b) - appearsAt(a) || a.id.localeCompare(b.id))
    .map((s) => sensorNow(s, bySensor.get(s.id) ?? [], t));

  const lastDay = dayNumber(startMs, endMs);
  const learnDays = [...new Set(live.map((s) => Math.min(dayNumber(startMs, appearsAt(s)), lastDay)))]
    .filter((d) => d >= 1)
    .sort((a, b) => a - b);

  const { devices, latest, deviceFeed } = devicesAt(rec, t, holdMs);
  const cards = visible.filter((s) => s.sensor.confidence >= CARD_MIN_CONFIDENCE);
  return {
    t,
    startMs,
    endMs,
    atEnd,
    day: dayNumber(startMs, t),
    days: rec.meta.days > 0 ? rec.meta.days : lastDay,
    devices,
    latest,
    today: [...deviceFeed, ...habitFeed(cards, t)].sort((a, b) => b.at - a.at),
    cards,
    alsoNoticed: visible.filter((s) => s.sensor.confidence < CARD_MIN_CONFIDENCE),
    learnDays,
  };
}
