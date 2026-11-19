import type { Control, ToneOption } from "./types";

// What a person can change on each device type: source/guard.py WRITE_TYPES and BODIES, drawn as controls.
// The Python API sends these with each device; the mock houses use them directly.

/** The bulb's white temperature is a 0–100 scale, not degrees (docs/mcp-capabilities.md).
 *  Assumed: 0 is the warmest white. Check on the kit before relying on it. */
export const TONES: ToneOption[] = [
  { label: "Warm", value: 0 },
  { label: "Neutral", value: 50 },
  { label: "Cool", value: 100 },
];

/** The thermostat's limits in the guard (TARGET_MIN_C, TARGET_MAX_C) and the device's 0.5 °C step. */
export const TARGET = { min: 16, max: 23, step: 0.5, maxStep: 3, awayMax: 18 } as const;

export function controlsFor(type: string): Control[] {
  switch (type) {
    case "bulb":
      return [
        { kind: "switch", capability: "power" },
        { kind: "level", capability: "brightness", min: 1, max: 100, step: 1 },
        { kind: "tone", capability: "temperature", options: TONES },
      ];
    case "plug":
      return [{ kind: "switch", capability: "power" }];
    case "thermostat":
      return [
        { kind: "target", capability: "targetTemperature", min: TARGET.min, max: TARGET.max, step: TARGET.step },
        { kind: "choice", capability: "mode", options: ["heat", "eco", "off"] },
      ];
    default:
      return [];
  }
}

/** The tone option nearest a bulb's white temperature. */
export function toneOf(value: unknown): ToneOption {
  const v = typeof value === "number" ? value : 0;
  return TONES.reduce((best, t) => (Math.abs(t.value - v) < Math.abs(best.value - v) ? t : best), TONES[0]!);
}
