// The shapes the phone app and the Python API (source/phone_api.py) share. docs/phone-api.md describes them.
// Times are the home's local time (Europe/Lisbon) as naive ISO strings, "2026-09-21T09:12:00", like the loop.

export type Value = string | number | boolean | null;

/** Exactly the write bodies source/guard.py accepts (BODIES). */
export type ControlBody =
  | { type: "power"; value: "on" | "off" }
  | { type: "brightness"; value: number }
  | { type: "temperature"; value: number }
  | { type: "targetTemperature"; value: number }
  | { type: "mode"; value: "heat" | "eco" | "off" };

export interface ToneOption { label: string; value: number }

/** One thing a person can change on a device, drawn by the app as a switch, slider, stepper or choice. */
export type Control =
  | { kind: "switch"; capability: "power" }
  | { kind: "level"; capability: "brightness"; min: number; max: number; step: number }
  | { kind: "tone"; capability: "temperature"; options: ToneOption[] }
  | { kind: "target"; capability: "targetTemperature"; min: number; max: number; step: number }
  | { kind: "choice"; capability: "mode"; options: ("heat" | "eco" | "off")[] };

export interface Device {
  id: string;
  type: string;
  name: string;
  values: Record<string, Value>;
  /** Empty for read-only devices (sensors, camera, hub, alarm). */
  controls: Control[];
  /** false when the device stopped reporting: its values are the last ones known. */
  reachable: boolean;
  lastSeen: string | null;
}

export interface Person { id: string; name: string }
export interface People { home: Person[]; guests: number }
export interface Climate { roomC: number | null; targetC: number | null; mode: string | null; heating: string | null }

export interface HomeSnapshot {
  now: string;
  household: string;
  /** false for a mock or a replay. */
  live: boolean;
  /** true when writes are not really sent (replay, --stub, mock). */
  dryRun: boolean;
  /** npm run home: your real home, but nothing is sent (npm run home:live sends). */
  lookOnly: boolean;
  devices: Device[];
  /** null when the home has no router. */
  people: People | null;
  /** null when the home has no thermostat. */
  climate: Climate | null;
}

/** One change Alfred made since the status began, as a before and after value. */
export interface Change { deviceId: string; device: string; capability: string; from: Value; to: Value }

export type PlanMode = "do" | "suggest";
export type PlanStatus = "planned" | "done" | "skipped" | "failed";

/** One of Alfred's decisions (source/anticipate.py): "do" runs at `at`, "suggest" waits for a person. */
export interface PlanItem {
  id: string;
  at: string;
  mode: PlanMode;
  status: PlanStatus;
  title: string;
  does: string;
  reason: string;
  confidence: number;
  /** What a person answered, if anything. */
  answer: "yes" | "no" | "skip" | "undo" | null;
}

/** The "Right now" card. */
export interface Status {
  title: string;
  since: string | null;
  changes: Change[];
  why: string | null;
  /** The decision behind the changes, which undo reverses; null when Alfred changed nothing. */
  decisionId: string | null;
  /** True while those changes are Alfred's and a person hasn't overruled them. */
  byAlfred: boolean;
  planned: PlanItem[];
  suggestions: PlanItem[];
  /** Look-only (npm run home): what Alfred would have done in the last hours, but didn't send. */
  notSent: PlanItem[];
  /** Before any habit is learned: how many days the loop has watched. null once it has habits. */
  learning: { daysWatched: number } | null;
}

/** The guard's record of one write (source/guard.py guarded_write). */
export interface ActionResult {
  status: "blocked" | "failed" | "sent";
  verified: "confirmed" | "unconfirmed" | null;
  reason?: string;
  deviceId?: string;
  body?: ControlBody;
  /** Set when this write overruled something Alfred did, which Alfred counts: Alfred's line about it. */
  note?: string;
}

export type Block =
  | { t: "p"; text: string }
  | { t: "control"; deviceId: string }
  | { t: "plan" }
  | { t: "devices" }
  | { t: "feed"; lines: [string, string, string][] };

export interface ChatReply {
  text: string;
  blocks: Block[];
  actions: ActionResult[];
  /** The language Alfred answered in ("en", "pt"), when known. */
  lang: string | null;
}

export interface Transcript { text: string; lang: string | null; ms: number }
