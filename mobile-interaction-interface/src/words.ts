import { toneOf } from "./api/controls";
import type { Change, ControlBody, Device, HomeSnapshot, Status, Value } from "./api/types";

// Device values and Alfred's changes in plain words (from dashboard/src/data/words.ts, trimmed for the phone).

export const hhmm = (iso: string | null | undefined): string => (iso ? iso.slice(11, 16) : "");
export const fmtNum = (x: number): string => (Number.isInteger(x) ? String(x) : x.toFixed(1));
const num = (v: Value | undefined): number | null => (typeof v === "number" ? v : null);

/** Which of the three ways the app was started, in words: the composer's small print, Settings, Devices. */
export interface ModeWords { name: string; note: string; how: string; short: string }

export function modeWords(kind: "mock" | "http", home: HomeSnapshot | null): ModeWords | null {
  if (kind === "mock") {
    return { name: "Demo house", short: "simulated", note: "Demo house: simulated. Nothing reaches a real home.",
      how: "Started with npm run demo. For your real home: npm run home (look-only) or npm run home:live." };
  }
  if (!home) return null;
  if (home.lookOnly) {
    return { name: "Look-only", short: "look-only", note: "Look-only: Alfred sees your home and answers, but switches nothing.",
      how: "Started with npm run home. To let changes really happen: npm run home:live." };
  }
  if (home.dryRun) {
    return { name: "Dry run", short: "dry run", note: "Dry run: nothing is really sent.", how: "The loop runs a replay, or with --stub." };
  }
  return { name: "Live", short: "", note: "Live: changes really reach your home. Alfred never touches the alarm.",
    how: "Started with npm run home:live. To only look: npm run home." };
}

/** A write in words, as a person would ask for it: "turn the lamp on", "set the heating to 20 °C". */
export function whatOf(name: string, b: ControlBody): string {
  const n = name.toLowerCase();
  switch (b.type) {
    case "power": return `turn the ${n} ${b.value}`;
    case "brightness": return `set the ${n} to ${b.value}%`;
    case "temperature": return `make the ${n} ${toneOf(b.value).label.toLowerCase()}`;
    case "targetTemperature": return `set the heating to ${fmtNum(b.value)} °C`;
    case "mode": return `set the heating to ${b.value}`;
  }
}

/** "turn the lamp on" → "turned the lamp on", for the line that says it was done. */
export const pastTense = (what: string): string => what.replace(/^turn\b/, "turned").replace(/^make\b/, "made");

export type Tone = "ok" | "warn" | "bad" | "";
export interface Reading { sub: string; value?: string; pill?: { tone: Tone; text: string } }

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "17:57" today, "18 Nov, 17:57" before: a device silent since yesterday mustn't look recent. */
export function whenSeen(iso: string | null, today?: string): string {
  if (!iso) return "never";
  if (!today || iso.slice(0, 10) === today) return hhmm(iso);
  return `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1] ?? ""}, ${hhmm(iso)}`;
}

/** What a device is doing, for its row in the devices panel. `today` is the home's date (home.now). */
export function reading(d: Device, today?: string): Reading {
  const v = d.values;
  if (!d.reachable) return { sub: `last heard ${whenSeen(d.lastSeen, today)}, may be unplugged`, pill: { tone: "warn", text: "no signal" } };
  switch (d.type) {
    case "bulb":
      return { sub: v.power === "on" ? `on · ${v.brightness ?? "?"}% · ${toneOf(v.temperature).label.toLowerCase()}` : "off" };
    case "plug": {
      const w = num(v.instantPower);
      return { sub: v.power === "on" ? `on${w !== null ? ` · ${fmtNum(w)} W now` : ""}` : "off" };
    }
    case "thermostat":
      return { sub: `room ${fmtNum(num(v.temperatureValue) ?? 0)} °C · heating ${v.heatingState === "on" ? "on" : "off"}` };
    case "contactSensor":
      return { sub: `last changed ${hhmm(d.lastSeen)}`, pill: v.sensorState === "alarm" ? { tone: "", text: "open" } : { tone: "ok", text: "closed" } };
    case "temperatureSensor":
      return { sub: `humidity ${fmtNum(num(v.humidityValue) ?? 0)}%`, value: `${fmtNum(num(v.temperatureValue) ?? 0)} °C` };
    case "smokeSensor":
      if (v.sensorState === "alarm") return { sub: "smoke detected", pill: { tone: "bad", text: "smoke" } };
      if (v.signalStrength === "low") return { sub: "no smoke · battery high", pill: { tone: "warn", text: "weak signal" } };
      return { sub: "no smoke", pill: { tone: "ok", text: "normal" } };
    case "camera":
      return { sub: v.motionDetected ? "motion now" : "no motion", pill: v.sdRecording ? { tone: "ok", text: "recording" } : { tone: "", text: "not recording" } };
    case "gateway": {
      const b = num(v.batteryLevel);
      return b !== null && b <= 20
        ? { sub: `${v.chargeState ? "charging · " : ""}backup signal ${v.cellularSignalStrength ?? "?"}`, pill: { tone: "warn", text: `battery ${b}%` } }
        : { sub: "online", pill: { tone: "ok", text: "online" } };
    }
    case "security":
      return v.status === "DISARMED"
        ? { sub: "arm it at the security hub", pill: { tone: "", text: "disarmed" } }
        : { sub: "arm or disarm it at the security hub", pill: { tone: "ok", text: "armed" } };
    default:
      return { sub: "" };
  }
}

/** How many devices need a look (as the dashboard's devices.ts counts them). */
export function needsLook(devices: Device[]): number {
  return devices.filter((d) => {
    const r = reading(d);
    return r.pill?.tone === "warn" || r.pill?.tone === "bad";
  }).length;
}

export interface Shown { deviceId: string; label: string; short: string; delta: string; to: string }

/** Alfred's changes, one per device: "Light −30% → off", "Kettle off", "Heating −3 °C → 17 °C". */
export function shownChanges(changes: Change[], devices: Device[]): Shown[] {
  const byDevice = new Map<string, Change[]>();
  for (const c of changes) byDevice.set(c.deviceId, [...(byDevice.get(c.deviceId) ?? []), c]);
  const out: Shown[] = [];
  for (const [id, cs] of byDevice) {
    const d = devices.find((x) => x.id === id);
    const type = d?.type ?? "";
    const get = (cap: string) => cs.find((c) => c.capability === cap);
    if (type === "bulb") {
      const power = get("power"), bri = get("brightness");
      const was = num(bri?.from ?? d?.values.brightness ?? null) ?? 0;
      if (power?.to === "off") out.push({ deviceId: id, label: "Light", short: "Light", delta: `−${was}%`, to: "off" });
      else if (power?.to === "on") out.push({ deviceId: id, label: "Light", short: "Light", delta: `+${num(bri?.to ?? d?.values.brightness ?? null) ?? 0}%`, to: "on" });
      else if (bri && typeof bri.to === "number" && typeof bri.from === "number") out.push({ deviceId: id, label: "Light", short: "Light", delta: signed(bri.to - bri.from, "%"), to: `${bri.to}%` });
      else if (get("temperature")) out.push({ deviceId: id, label: "Light", short: "Light", delta: toneOf(get("temperature")!.to).label.toLowerCase(), to: "" });
    } else if (type === "thermostat") {
      const t = get("targetTemperature"), m = get("mode");
      if (t && typeof t.to === "number" && typeof t.from === "number") out.push({ deviceId: id, label: "Heating", short: "Heating", delta: signed(t.to - t.from, " °C"), to: `${fmtNum(t.to)} °C` });
      if (m) out.push({ deviceId: id, label: "Heating mode", short: "Heating", delta: String(m.to), to: "" });
    } else {
      const power = get("power");
      if (power) out.push({ deviceId: id, label: `${d?.name ?? "Plug"} plug`, short: d?.name ?? "Plug", delta: String(power.to), to: `was ${power.from}` });
    }
  }
  return out;
}

function signed(x: number, unit: string): string {
  return `${x > 0 ? "+" : "−"}${fmtNum(Math.abs(x))}${unit}`;
}

/** "Good morning" before noon, "Good afternoon" until 18:00, then "Good evening", from the home's clock. */
export function hello(now: string | null | undefined): string {
  const h = Number(now?.slice(11, 13));
  if (!Number.isFinite(h) || !now) return "Hello";
  return h >= 5 && h < 12 ? "Good morning" : h >= 12 && h < 18 ? "Good afternoon" : "Good evening";
}

/** While nothing is learned yet: "Still learning your home · 2 days watched". */
export function learningLine(l: Status["learning"]): string | null {
  if (!l) return null;
  return `Still learning your home · ${l.daysWatched === 1 ? "1 day" : `${l.daysWatched} days`} watched`;
}

/** Alfred's first line in a new chat, from the status and the home's clock. */
export function greeting(s: Status, now?: string | null): string {
  const hi = hello(now);
  if (s.byAlfred && s.changes.length) return `${hi}. ${s.why ? `${s.why.split(". ")[0]}, so` : "So"} I switched to ${s.title} at ${hhmm(s.since)}. Open the card at the top to see what’s planned.`;
  if (s.learning) return `${hi}. I’m still learning your home (${s.learning.daysWatched === 1 ? "1 day" : `${s.learning.daysWatched} days`} watched): my plans appear after a few days. Meanwhile, tell me what to switch.`;
  return `${hi}. Right now: ${s.title}${s.since ? `, since ${hhmm(s.since)}` : ""}. Tell me what to switch, or ask what I’ve planned.`;
}
