import { toMs, type Device, type Value } from "./recording";
import type { DeviceNow } from "./stateAt";
import { deviceRow, formatClock, type DeviceRow } from "./words";

export const LOW_BATTERY_PCT = 20;

export interface Concern { device: Device; issues: string[] }

function issuesOf(values: Record<string, Value>): string[] {
  const issues: string[] = [];
  if (values.reachable === false) issues.push("No signal");
  const level = values.batteryLevel;
  if (typeof level === "number" && level <= LOW_BATTERY_PCT) issues.push(`Battery ${level}%`);
  if (values.batteryStatus === "low") issues.push("Battery low");
  if (values.cellularConnectionState === "abnormal") issues.push("No mobile signal");
  if (values.signalStrength === "low" || values.signalStrength === "bad") issues.push("Weak signal");
  if (values.tamperState !== undefined && values.tamperState !== "normal") issues.push("Tamper alert");
  return issues;
}

export function needsLook(devices: DeviceNow[]): Concern[] {
  return devices.flatMap(({ device, values }) => {
    const issues = issuesOf(values);
    return issues.length > 0 ? [{ device, issues }] : [];
  });
}

function otherRow(type: string, v: Record<string, Value>): DeviceRow {
  switch (type) {
    case "security":
      return { value: v.status === "DISARMED" ? "Disarmed" : "Armed", sub: v.isAlarmed === true ? "sounding" : "not sounding" };
    case "gateway": {
      const battery = typeof v.batteryLevel === "number" ? `battery ${v.batteryLevel}%` : "";
      return { value: `On ${String(v.networkMode ?? "an unknown network")}`, sub: v.chargeState === true ? `${battery}, charging` : battery };
    }
    case "smokeSensor":
      return { value: v.sensorState === "alarm" ? "Smoke" : "No smoke", sub: v.batteryStatus ? `battery ${String(v.batteryStatus)}` : "" };
    case "camera":
      return v.sdRecording === true ? { value: "Recording", sub: "to its SD card" } : { value: "Not recording", sub: "" };
    default:
      return { value: "No reading", sub: "" };
  }
}

function silentRow(lastHeard: Value | undefined): DeviceRow {
  if (typeof lastHeard !== "string") return { value: "No signal", sub: "may be unplugged" };
  return { value: "No signal", sub: `last heard ${formatClock(toMs(lastHeard)).time}, may be unplugged` };
}

export function statusRow(d: DeviceNow): DeviceRow {
  if (d.values.reachable === false) return silentRow(d.values.lastHeard);
  return deviceRow(d) ?? otherRow(d.device.type, d.values);
}

export function lookCount(n: number): string {
  return n === 1 ? "1 device needs a look" : `${n} devices need a look`;
}

export function concernText({ device, issues }: Concern): string {
  return `${device.name}: ${issues.join(", ").toLowerCase()}.`;
}

export function devicesHeadline(total: number, concerns: number): string {
  if (concerns === 0) return `All ${total} devices look fine.`;
  return `${total} devices, ${concerns === 1 ? "1 needs" : `${concerns} need`} a look.`;
}
