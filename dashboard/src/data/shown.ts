export const SHOWN: Record<string, string[]> = {
  contactSensor: ["sensorState"],
  bulb: ["power", "brightness"],
  plug: ["instantPower"],
  temperatureSensor: ["temperatureValue", "humidityValue"],
};

export function isShown(type: string, capability: string): boolean {
  return SHOWN[type]?.includes(capability) ?? false;
}

export const IN_USE_W = 5;
const NOTABLE: Record<string, string> = { contactSensor: "sensorState", bulb: "power", plug: "instantPower" };

export function activeCapability(type: string): string | undefined {
  return NOTABLE[type];
}

export function marksChange(type: string, capability: string): boolean {
  return type in NOTABLE && isShown(type, capability);
}

export function wentActive(type: string, capability: string, value: unknown): boolean {
  if (NOTABLE[type] !== capability) return false;
  if (type === "contactSensor") return value === "alarm";
  if (type === "bulb") return value === true || value === "on";
  return typeof value === "number" && value > IN_USE_W;
}
