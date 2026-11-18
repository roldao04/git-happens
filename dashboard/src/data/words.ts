import type { Sensor, Value } from "./recording";
import type { DeviceNow } from "./stateAt";
import { IN_USE_W } from "./shown";

const NOUNS: Record<string, string> = {
  bulb: "lamp",
  plug: "plug",
  contactSensor: "door",
  temperatureSensor: "temperature",
  home: "the home",
};
const DOOR: Record<string, string> = { normal: "closed", alarm: "open" };
const PART = /^(end of )?([A-Za-z]+)-[A-Z]+ \((.*)\)$/;
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function inner(kind: string, text: string): string {
  if (kind === "bulb") return text.replace(/^power /, "");
  if (kind === "contactSensor") return text.replace(/^sensorState (\w+)/, (_, state: string) => DOOR[state] ?? state);
  if (kind === "home") return text.replace(/^quiet after .+? until [^,]+/, "quiet");
  return text;
}

export function partToWords(part: string): string {
  const m = PART.exec(part);
  if (!m) return part;
  const [, endOf, kind = "", text = ""] = m;
  const noun = NOUNS[kind];
  if (!noun) return part;
  if (endOf) return kind === "home" ? "end of the home quiet" : `end of ${noun} ${inner(kind, text).split(",")[0]}`;
  return `${noun} ${inner(kind, text)}`;
}

export function evidence(parts: string[]): string {
  return parts.map(partToWords).join(", then ");
}

function whole(n: number): string {
  return Math.round(n).toLocaleString("en-US");
}

export function deviceWords(type: string, capability: string, value: Value): string {
  if (value === null) return "—";
  if (capability === "sensorState" && typeof value === "string") return DOOR[value] ?? value;
  if (typeof value === "number") {
    if (capability === "instantPower") return `${whole(value)} W${value > IN_USE_W ? ", in use" : ""}`;
    if (capability === "temperatureValue") return `${value.toFixed(1)} °C`;
    if (capability === "humidityValue") return `${value.toFixed(1)}%`;
    if (capability === "brightness") return `${whole(value)}%`;
  }
  if (capability === "power" && type === "bulb") {
    if (value === true || value === "on") return "on";
    if (value === false || value === "off") return "off";
  }
  return String(value);
}

export function sensorTitle(s: Sensor): { text: string; unnamed: boolean } {
  if (s.name) return { text: s.name, unnamed: false };
  const n = /^pattern-(\d+)$/.exec(s.id);
  return { text: n ? `Pattern ${n[1]}` : s.id, unnamed: true };
}

export function formatClock(t: number): { weekday: string; time: string } {
  const d = new Date(t);
  const two = (n: number) => String(n).padStart(2, "0");
  return { weekday: WEEKDAYS[d.getUTCDay()] ?? "", time: `${two(d.getUTCHours())}:${two(d.getUTCMinutes())}` };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatDate(t: number): string {
  const d = new Date(t);
  return `${WEEKDAYS[d.getUTCDay()] ?? ""} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()] ?? ""}`;
}

function listed(items: string[]): string {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1] ?? ""}`;
}

function clause({ device, values }: DeviceNow): string | null {
  const v = (cap: string) => values[cap];
  if (device.type === "contactSensor" && v("sensorState") !== undefined) return `the door is ${deviceWords("contactSensor", "sensorState", v("sensorState") ?? null)}`;
  if (device.type === "bulb" && v("power") !== undefined) return `the lamp is ${deviceWords("bulb", "power", v("power") ?? null)}`;
  const watts = v("instantPower");
  if (device.type === "plug" && typeof watts === "number") return `the plug is ${watts > IN_USE_W ? "in use" : "at rest"}`;
  return null;
}

export function homeHeadline(devices: DeviceNow[]): string {
  const parts = devices.map(clause).filter((c): c is string => c !== null);
  const temp = devices.find((d) => d.device.type === "temperatureSensor")?.values.temperatureValue;
  const sentences = [];
  if (parts.length > 0) {
    const text = listed(parts);
    sentences.push(`${text.charAt(0).toUpperCase()}${text.slice(1)}.`);
  }
  if (typeof temp === "number") sentences.push(`It is ${temp.toFixed(1)} °C inside.`);
  return sentences.length > 0 ? sentences.join(" ") : "Nothing to show yet.";
}

export function learnedHeadline(cards: number): string {
  if (cards === 0) return "Alfred hasn't learned anything yet.";
  return `Alfred has learned ${cards} ${cards === 1 ? "sensor" : "sensors"}.`;
}

export function learnedSummary(newToday: number, weak: number): string {
  const sentences = [];
  if (newToday > 0) sentences.push(`${newToday} ${newToday === 1 ? "is" : "are"} new today.`);
  if (weak > 0) sentences.push(`${weak} more ${weak === 1 ? "is" : "are"} too weak to show yet.`);
  return sentences.join(" ");
}

export interface Step { title: string; text: string }

export function foundSteps(parts: string[], usual: string, spreadMin: number | undefined): Step[] {
  const steps = parts.map((p, i) => ({ title: i === 0 ? "First" : "Then", text: partToWords(p) }));
  const spread = spreadMin === undefined ? "" : `, give or take ${spreadMin} min`;
  return [...steps, { title: "Habit", text: `This repeats around ${usual}${spread}.` }];
}

export function homeTitle(devices: DeviceNow[]): string {
  const value = (type: string, cap: string) => devices.find((d) => d.device.type === type)?.values[cap];
  if (value("contactSensor", "sensorState") === "alarm") return "The door is open.";
  const watts = value("plug", "instantPower");
  if (typeof watts === "number" && watts > IN_USE_W) return "The plug is in use.";
  const lamp = value("bulb", "power");
  if (lamp === true || lamp === "on") return "The lamp is on.";
  return "All quiet at home.";
}

export function homeSummary(devices: DeviceNow[], newToday: number): string {
  const learned =
    newToday === 0 ? "Nothing new was learned today." : `Alfred learned ${newToday} new ${newToday === 1 ? "sensor" : "sensors"} today.`;
  return `${homeHeadline(devices)} ${learned}`;
}

const capital = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;

export interface DeviceRow { value: string; sub: string }

export function deviceRow({ device, values, since }: DeviceNow): DeviceRow | null {
  const at = since === null ? null : formatClock(since).time;
  const sinceText = (state: string) => (at ? `${state} since ${at}` : "");
  const v = values;
  if (device.type === "contactSensor" && v.sensorState !== undefined) {
    const state = deviceWords("contactSensor", "sensorState", v.sensorState);
    return { value: capital(state), sub: sinceText(state) };
  }
  if (device.type === "bulb" && v.power !== undefined) {
    const state = deviceWords("bulb", "power", v.power);
    const bright = state === "on" && typeof v.brightness === "number" ? `, ${deviceWords("bulb", "brightness", v.brightness)}` : "";
    const sub = sinceText(state);
    return { value: capital(state), sub: sub ? `${sub}${bright}` : "" };
  }
  if (device.type === "plug" && typeof v.instantPower === "number") {
    return { value: `${whole(v.instantPower)} W`, sub: sinceText(v.instantPower > IN_USE_W ? "in use" : "at rest") };
  }
  if (device.type === "temperatureSensor" && typeof v.temperatureValue === "number") {
    const hum = typeof v.humidityValue === "number" ? `${v.humidityValue.toFixed(1)}% humidity` : "";
    return { value: `${v.temperatureValue.toFixed(1)} °C`, sub: hum };
  }
  return null;
}
