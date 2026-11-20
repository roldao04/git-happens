import { TARGET } from "../controls";
import type { ControlBody, Device } from "../types";

// The mock's copy of source/guard.py, for a person's writes (allow_plug_on=True, check_heat by_person):
// only the lamp, the plugs and the thermostat; the alarm never; the thermostat within its limits.
// Keep the wording close to the guard's, so the app shows the same reasons either way.

const WRITE_TYPES: Record<string, string[]> = {
  bulb: ["power", "brightness", "temperature"],
  plug: ["power"],
  thermostat: ["targetTemperature", "mode"],
};

export interface HeatNow { target: number | null; someoneHome: boolean }

export function checkWrite(device: Device | undefined, body: ControlBody, heat: HeatNow): string | null {
  if (!device) return "blocked: no such device";
  const allowed = WRITE_TYPES[device.type];
  if (!allowed) return `blocked: ${device.name} can't be changed (only the lamp, the plugs and the thermostat)`;
  if (!device.reachable) return `blocked: ${device.name} isn't reporting`;
  if (!allowed.includes(body.type)) return `blocked: a ${device.type} only accepts ${allowed.join(", ")}`;
  switch (body.type) {
    case "power":
      return body.value === "on" || body.value === "off" ? null : "blocked: power is on or off";
    case "brightness":
      return isIn(body.value, 1, 100) ? null : "blocked: brightness is 1 to 100";
    case "temperature":
      return isIn(body.value, 0, 100) ? null : "blocked: white temperature is 0 to 100";
    case "mode":
      return ["heat", "eco", "off"].includes(body.value) ? null : "blocked: mode is heat, eco or off";
    case "targetTemperature": {
      const v = body.value;
      if (!isIn(v, TARGET.min, TARGET.max)) return `blocked: the heating goes from ${TARGET.min} to ${TARGET.max} °C`;
      if (heat.target === null) return "blocked: the thermostat's current target is unknown";
      if (Math.abs(v - heat.target) > TARGET.maxStep) return `blocked: at most ${TARGET.maxStep} °C change per write (now ${fmt(heat.target)} °C)`;
      if (v > TARGET.awayMax && !heat.someoneHome) return `blocked: never above ${TARGET.awayMax} °C while no one is home`;
      return null;
    }
  }
}

function isIn(v: unknown, lo: number, hi: number): v is number {
  return typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
}

const fmt = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(1));
