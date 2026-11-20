import type { Alert } from "../types";
import type { MockHouse } from "./houses";

const ALERT_FOR_MS = 2 * 60 * 60 * 1000;

/** The pitch film's sentence, word for word as source/alerts.py says it. */
export function alertText(device: string, type: string, habit: string, since: string): string {
  const word = type === "contactSensor" ? "open" : "on";
  return `The ${device.toLowerCase()} is still ${word}. ${habit} is starting, and it has been ${word} since ${since.slice(11, 16)}.`;
}

/** As source/alerts.py: only a door or window left open, or something left on. */
function leftOn(type: string, capability: string, value: unknown): boolean {
  if (capability === "sensorState") return type === "contactSensor" && value === "alarm";
  return capability === "power" && value === "on";
}

/** What source/alerts.py would say about the mock house now: a habit started in the last two hours and a
 *  device not as that habit usually finds it. */
export function alertsOf(house: MockHouse): Alert[] {
  const now = Date.parse(house.now);
  const out: Alert[] = [];
  for (const e of house.expects) {
    const start = house.plan.find((p) => p.id === e.decisionId)?.at;
    const device = house.devices.find((d) => d.id === e.deviceId);
    const since = house.changedAt[e.deviceId];
    if (!start || !device || !since) continue;
    const started = now - Date.parse(start);
    if (started < 0 || started > ALERT_FOR_MS || !leftOn(device.type, e.capability, device.values[e.capability])) continue;
    out.push({ id: `${e.habitId}:${e.tag}:${start.slice(0, 10)}`, at: start, text: alertText(device.name, device.type, e.habit, since) });
  }
  return out;
}
