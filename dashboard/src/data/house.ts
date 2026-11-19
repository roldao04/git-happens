import { activeCapability, wentActive } from "./shown";
import type { DeviceNow, StateAt } from "./stateAt";
import { formatClock, sensorTitle } from "./words";

export interface HouseView { lampOn: boolean | null; doorOpen: boolean | null; plugInUse: boolean | null; temperature: number | null }

function active(devices: DeviceNow[], type: string): boolean | null {
  const cap = activeCapability(type);
  const value = cap === undefined ? undefined : devices.find((d) => d.device.type === type)?.values[cap];
  return value === undefined || cap === undefined ? null : wentActive(type, cap, value);
}

export function houseView(devices: DeviceNow[]): HouseView {
  const temp = devices.find((d) => d.device.type === "temperatureSensor")?.values.temperatureValue;
  return {
    lampOn: active(devices, "bulb"),
    doorOpen: active(devices, "contactSensor"),
    plugInUse: active(devices, "plug"),
    temperature: typeof temp === "number" ? temp : null,
  };
}

export function houseLabel(v: HouseView): string {
  const parts = [
    v.lampOn === null ? null : `the lamp is ${v.lampOn ? "on" : "off"}`,
    v.doorOpen === null ? null : `the door is ${v.doorOpen ? "open" : "closed"}`,
    v.plugInUse === null ? null : `the plug is ${v.plugInUse ? "in use" : "at rest"}`,
    v.temperature === null ? null : `${v.temperature.toFixed(1)} °C inside`,
  ].filter((p): p is string => p !== null);
  return parts.length === 0 ? "The home: no readings yet." : `The home: ${parts.join(", ")}.`;
}

function greeting(t: number): string {
  const hour = new Date(t).getUTCHours();
  if (hour >= 5 && hour < 12) return "Good morning.";
  if (hour >= 12 && hour < 18) return "Good afternoon.";
  return "Good evening.";
}

type NoteState = Pick<StateAt, "t" | "day" | "days" | "cards" | "today">;

export function alfredNote(state: NoteState): string {
  const hello = greeting(state.t);
  if (state.cards.length === 0)
    return `${hello} I'm still getting to know your home. It's day ${state.day} of ${state.days}, and nothing repeats often enough yet to call a habit.`;
  const known = `I know ${state.cards.length} of your habits now.`;
  const seen = state.today.flatMap((f) => (f.kind === "habit" ? [f] : []));
  const last = seen.reduce<(typeof seen)[number] | undefined>((m, f) => (m === undefined || f.at > m.at ? f : m), undefined);
  if (last === undefined) return `${hello} ${known} None so far today; I'll keep an eye out.`;
  const named = `${sensorTitle(last.sensor).text} at ${formatClock(last.at).time}`;
  if (seen.length === 1) return `${hello} ${known} Today I already recognized ${named}.`;
  return `${hello} ${known} Today I already recognized ${seen.length} of them, most recently ${named}.`;
}
