import type { Sensor, Value } from "./recording";
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
