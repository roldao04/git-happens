import { ApiError } from "./client";
import type { ActionResult, Block, Change, ChatReply, Control, ControlBody, Device, HomeSnapshot, People, PlanItem, Status, Transcript, Value } from "./types";

// Checks what the Python API sends before the app trusts it, naming the field that is wrong
// ("devices[2].controls[0].kind ..."), the way dashboard/src/data/recording.ts checks its recording.

type Obj = Record<string, unknown>;

function fail(path: string, msg: string): never {
  throw new ApiError(`${path} ${msg}`);
}
function obj(v: unknown, path: string): Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) fail(path, "is not an object");
  return v as Obj;
}
function str(o: Obj, k: string, path: string): string {
  const v = o[k];
  if (typeof v !== "string") fail(`${path}.${k}`, "is not text");
  return v;
}
function optStr(o: Obj, k: string, path: string): string | null {
  const v = o[k];
  if (v === undefined || v === null) return null;
  if (typeof v !== "string") fail(`${path}.${k}`, "is not text");
  return v;
}
function num(o: Obj, k: string, path: string): number {
  const v = o[k];
  if (typeof v !== "number" || !Number.isFinite(v)) fail(`${path}.${k}`, "is not a number");
  return v;
}
function optNum(o: Obj, k: string, path: string): number | null {
  const v = o[k];
  if (v === undefined || v === null) return null;
  if (typeof v !== "number" || !Number.isFinite(v)) fail(`${path}.${k}`, "is not a number");
  return v;
}
function bool(o: Obj, k: string, path: string): boolean {
  const v = o[k];
  if (typeof v !== "boolean") fail(`${path}.${k}`, "is not true or false");
  return v;
}
function list<T>(o: Obj, k: string, path: string, each: (v: unknown, p: string) => T): T[] {
  const v = o[k];
  if (!Array.isArray(v)) fail(`${path}.${k}`, "is not a list");
  return v.map((x, i) => each(x, `${path}.${k}[${i}]`));
}
function oneOf<T extends string>(o: Obj, k: string, path: string, allowed: readonly T[]): T {
  const v = str(o, k, path);
  if (!allowed.includes(v as T)) fail(`${path}.${k}`, `"${v}" is not one of ${allowed.join(", ")}`);
  return v as T;
}
function value(v: unknown, path: string): Value {
  if (v === null || ["string", "number", "boolean"].includes(typeof v)) return v as Value;
  fail(path, "is not a plain value");
}

function control(v: unknown, path: string): Control {
  const o = obj(v, path);
  const kind = oneOf(o, "kind", path, ["switch", "level", "tone", "target", "choice"] as const);
  switch (kind) {
    case "switch": return { kind, capability: "power" };
    case "level": return { kind, capability: "brightness", min: num(o, "min", path), max: num(o, "max", path), step: num(o, "step", path) };
    case "target": return { kind, capability: "targetTemperature", min: num(o, "min", path), max: num(o, "max", path), step: num(o, "step", path) };
    case "tone": return { kind, capability: "temperature", options: list(o, "options", path, (x, p) => { const t = obj(x, p); return { label: str(t, "label", p), value: num(t, "value", p) }; }) };
    case "choice": return { kind, capability: "mode", options: list(o, "options", path, (x, p) => { if (x !== "heat" && x !== "eco" && x !== "off") fail(p, "is not heat, eco or off"); return x; }) };
  }
}

function device(v: unknown, path: string): Device {
  const o = obj(v, path);
  const values = obj(o.values, `${path}.values`);
  return {
    id: str(o, "id", path), type: str(o, "type", path), name: str(o, "name", path),
    values: Object.fromEntries(Object.entries(values).map(([k, x]) => [k, value(x, `${path}.values.${k}`)])),
    controls: list(o, "controls", path, control),
    reachable: bool(o, "reachable", path),
    lastSeen: optStr(o, "lastSeen", path),
  };
}

function people(v: unknown, path: string): People | null {
  if (v === null || v === undefined) return null;
  const o = obj(v, path);
  return { home: list(o, "home", path, (x, p) => { const q = obj(x, p); return { id: str(q, "id", p), name: str(q, "name", p) }; }), guests: num(o, "guests", path) };
}

export function parseHome(raw: unknown): HomeSnapshot {
  const o = obj(raw, "home");
  const c = o.climate === null || o.climate === undefined ? null : obj(o.climate, "home.climate");
  return {
    now: str(o, "now", "home"), household: str(o, "household", "home"),
    live: bool(o, "live", "home"), dryRun: bool(o, "dryRun", "home"), lookOnly: o.lookOnly === true,
    devices: list(o, "devices", "home", device),
    people: people(o.people, "home.people"),
    climate: c && { roomC: optNum(c, "roomC", "home.climate"), targetC: optNum(c, "targetC", "home.climate"), mode: optStr(c, "mode", "home.climate"), heating: optStr(c, "heating", "home.climate") },
  };
}

export function parsePlanItem(v: unknown, path = "item"): PlanItem {
  const o = obj(v, path);
  const answer = o.answer === null || o.answer === undefined ? null : oneOf(o, "answer", path, ["yes", "no", "skip", "undo"] as const);
  return {
    id: str(o, "id", path), at: str(o, "at", path),
    mode: oneOf(o, "mode", path, ["do", "suggest"] as const),
    status: oneOf(o, "status", path, ["planned", "done", "skipped", "failed"] as const),
    title: str(o, "title", path), does: str(o, "does", path), reason: str(o, "reason", path),
    confidence: num(o, "confidence", path), answer,
  };
}

function change(v: unknown, path: string): Change {
  const o = obj(v, path);
  return { deviceId: str(o, "deviceId", path), device: str(o, "device", path), capability: str(o, "capability", path), from: value(o.from, `${path}.from`), to: value(o.to, `${path}.to`) };
}

export function parseStatus(raw: unknown): Status {
  const o = obj(raw, "status");
  return {
    title: str(o, "title", "status"), since: optStr(o, "since", "status"),
    changes: list(o, "changes", "status", change),
    why: optStr(o, "why", "status"), decisionId: optStr(o, "decisionId", "status"),
    byAlfred: bool(o, "byAlfred", "status"),
    planned: list(o, "planned", "status", parsePlanItem),
    suggestions: list(o, "suggestions", "status", parsePlanItem),
    notSent: o.notSent === undefined ? [] : list(o, "notSent", "status", parsePlanItem),
    learning: o.learning && typeof o.learning === "object" ? { daysWatched: num(o.learning as Obj, "daysWatched", "status.learning") } : null,
  };
}

const BODY_TYPES = ["power", "brightness", "temperature", "targetTemperature", "mode"] as const;

export function parseAction(v: unknown, path = "action"): ActionResult {
  const o = obj(v, path);
  const out: ActionResult = {
    status: oneOf(o, "status", path, ["blocked", "failed", "sent"] as const),
    verified: o.verified === null || o.verified === undefined ? null : oneOf(o, "verified", path, ["confirmed", "unconfirmed"] as const),
  };
  const reason = optStr(o, "reason", path), id = optStr(o, "deviceId", path), note = optStr(o, "note", path);
  if (reason) out.reason = reason;
  if (id) out.deviceId = id;
  const b = o.body;
  if (b && typeof b === "object" && BODY_TYPES.includes((b as Obj).type as (typeof BODY_TYPES)[number])) out.body = b as ControlBody;
  if (note) out.note = note;
  return out;
}

export function parseActions(raw: unknown): ActionResult[] {
  if (!Array.isArray(raw)) fail("actions", "is not a list");
  return raw.map((x, i) => parseAction(x, `actions[${i}]`));
}

function block(v: unknown, path: string): Block {
  const o = obj(v, path);
  const t = oneOf(o, "t", path, ["p", "control", "plan", "devices", "feed"] as const);
  if (t === "p") return { t, text: str(o, "text", path) };
  if (t === "control") return { t, deviceId: str(o, "deviceId", path) };
  if (t === "feed") return { t, lines: list(o, "lines", path, (x, p) => { if (!Array.isArray(x) || x.length !== 3 || !x.every((s) => typeof s === "string")) fail(p, "is not three texts"); return x as [string, string, string]; }) };
  return { t };
}

export function parseReply(raw: unknown): ChatReply {
  const o = obj(raw, "reply");
  return {
    text: str(o, "text", "reply"),
    blocks: o.blocks === undefined ? [] : list(o, "blocks", "reply", block),
    actions: o.actions === undefined ? [] : list(o, "actions", "reply", parseAction),
    lang: optStr(o, "lang", "reply"),
  };
}

export function parseTranscript(raw: unknown): Transcript {
  const o = obj(raw, "transcript");
  return { text: str(o, "text", "transcript"), lang: optStr(o, "lang", "transcript"), ms: num(o, "ms", "transcript") };
}
