export interface Meta { household: string; simulated: boolean; start: string; end: string; days: number; updated: string | null }
export interface Device { id: string; type: string; name: string }
export type Value = string | number | boolean | null;
export interface DeviceValue { device_id: string; capability: string; value: Value }
export interface DeviceEvent extends DeviceValue { ts: string }
export interface Pattern {
  parts: string[];
  time_of_day: string;
  spread_min?: number;
  days_seen?: number;
  days_total?: number;
  [extra: string]: unknown;
}
export type SensorStatus = "active" | "fading" | "merged";
export interface Sensor {
  id: string;
  name: string | null;
  named_by?: string | null;
  focus?: string;
  learner?: string;
  learned_at: string;
  explanation?: string | null;
  means?: string | null;
  if_missing?: string | null;
  confidence: number;
  status: SensorStatus;
  pattern: Pattern;
}
export interface Reading { sensor_id: string; start: string; end: string | null; value: number | null }
export interface Gap { start: string; end: string }
export interface Recording {
  meta: Meta;
  devices: Device[];
  initial: DeviceValue[];
  events: DeviceEvent[];
  sensors: Sensor[];
  readings: Reading[];
  gaps: Gap[];
}

export class RecordingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RecordingError";
  }
}

type Obj = Record<string, unknown>;
const STATUSES: readonly SensorStatus[] = ["active", "fading", "merged"];
const LOCAL_TIME = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2}))?$/;

export function toMs(text: string): number {
  const m = LOCAL_TIME.exec(text);
  if (!m) throw new RecordingError(`"${text}" is not a local time like 2026-09-01T07:59:39`);
  const [y, mo, d, h, mi, s] = m.slice(1).map((part) => Number(part ?? 0)) as [number, number, number, number, number, number];
  const ms = Date.UTC(y, mo - 1, d, h, mi, s);
  const back = new Date(ms);
  if (back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d || h > 23 || mi > 59 || s > 59) {
    throw new RecordingError(`"${text}" is not a real date and time`);
  }
  return ms;
}

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function field(o: Obj, name: string, path: string): unknown {
  const v = o[name];
  if (v === undefined) throw new RecordingError(`${path}.${name} is missing`);
  return v;
}

function str(o: Obj, name: string, path: string): string {
  const v = field(o, name, path);
  if (typeof v !== "string") throw new RecordingError(`${path}.${name} is not text`);
  return v;
}

function num(o: Obj, name: string, path: string): number {
  const v = field(o, name, path);
  if (typeof v !== "number" || !Number.isFinite(v)) throw new RecordingError(`${path}.${name} is not a number`);
  return v;
}

function time(o: Obj, name: string, path: string): string {
  const v = str(o, name, path);
  try {
    toMs(v);
  } catch (err) {
    if (err instanceof RecordingError) throw new RecordingError(`${path}.${name}: ${err.message}`);
    throw err;
  }
  return v;
}

function optStr(o: Obj, name: string): string | null {
  const v = o[name];
  return typeof v === "string" ? v : null;
}

function optNum(o: Obj, name: string): number | null {
  const v = o[name];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function value(v: unknown): Value {
  return typeof v === "string" || typeof v === "number" || typeof v === "boolean" ? v : null;
}

function list(o: Obj, name: string, required: boolean): unknown[] {
  const v = o[name];
  if (v === undefined && !required) return [];
  if (!Array.isArray(v)) throw new RecordingError(v === undefined ? `${name} is missing` : `${name} is not a list`);
  return v;
}

function each<T>(items: unknown[], name: string, read: (o: Obj, path: string) => T): T[] {
  return items.map((item, i) => {
    const path = `${name}[${i}]`;
    if (!isObj(item)) throw new RecordingError(`${path} is not an object`);
    return read(item, path);
  });
}

function readPattern(o: Obj, path: string): Pattern {
  const p = field(o, "pattern", path);
  const here = `${path}.pattern`;
  if (!isObj(p)) throw new RecordingError(`${here} is not an object`);
  const parts = field(p, "parts", here);
  if (!Array.isArray(parts) || !parts.every((x): x is string => typeof x === "string")) {
    throw new RecordingError(`${here}.parts is not a list of text`);
  }
  const out: Pattern = { ...p, parts, time_of_day: str(p, "time_of_day", here) };
  for (const k of ["spread_min", "days_seen", "days_total"] as const) {
    const n = optNum(p, k);
    if (n === null) delete out[k];
    else out[k] = n;
  }
  return out;
}

function readSensor(o: Obj, path: string): Sensor {
  const status = str(o, "status", path);
  const known = STATUSES.find((s) => s === status);
  if (!known) throw new RecordingError(`${path}.status "${status}" is not one of ${STATUSES.join(", ")}`);
  return {
    id: str(o, "id", path),
    name: optStr(o, "name"),
    named_by: optStr(o, "named_by"),
    focus: optStr(o, "focus") ?? undefined,
    learner: optStr(o, "learner") ?? undefined,
    learned_at: time(o, "learned_at", path),
    explanation: optStr(o, "explanation"),
    means: optStr(o, "means"),
    if_missing: optStr(o, "if_missing"),
    confidence: num(o, "confidence", path),
    status: known,
    pattern: readPattern(o, path),
  };
}

export function parseRecording(raw: unknown): Recording {
  if (!isObj(raw)) throw new RecordingError("recording is not an object");
  const m = field(raw, "meta", "recording");
  if (!isObj(m)) throw new RecordingError("meta is not an object");
  const meta: Meta = {
    household: optStr(m, "household") ?? "",
    simulated: m.simulated !== false,
    start: time(m, "start", "meta"),
    end: time(m, "end", "meta"),
    days: optNum(m, "days") ?? 0,
    updated: m.updated === undefined ? null : time(m, "updated", "meta"),
  };
  const events = each(list(raw, "events", false), "events", (o, p) => ({
    ts: time(o, "ts", p), device_id: str(o, "device_id", p), capability: str(o, "capability", p), value: value(o.value),
  }));
  events.forEach((e, i) => {
    const before = events[i - 1];
    if (before && toMs(e.ts) < toMs(before.ts)) throw new RecordingError(`events[${i}] is earlier than events[${i - 1}]`);
  });
  return {
    meta,
    events,
    devices: each(list(raw, "devices", true), "devices", (o, p) => ({
      id: str(o, "id", p), type: str(o, "type", p), name: str(o, "name", p),
    })),
    initial: each(list(raw, "initial", false), "initial", (o, p) => ({
      device_id: str(o, "device_id", p), capability: str(o, "capability", p), value: value(o.value),
    })),
    sensors: each(list(raw, "sensors", true), "sensors", readSensor),
    readings: each(list(raw, "readings", true), "readings", (o, p) => ({
      sensor_id: str(o, "sensor_id", p),
      start: time(o, "start", p),
      end: typeof o.end === "string" ? time(o, "end", p) : null,
      value: optNum(o, "value"),
    })),
    gaps: each(list(raw, "gaps", false), "gaps", (o, p) => ({ start: time(o, "start", p), end: time(o, "end", p) })),
  };
}
