import { expect, test } from "vitest";
import type { Value } from "./recording";
import type { DeviceNow } from "./stateAt";
import { devicesHeadline, needsLook, statusRow } from "./devices";

function dev(type: string, values: Record<string, Value>, name = type): DeviceNow {
  return { device: { id: `${type}-1`, type, name }, values, changedAt: null, since: null };
}

test("a healthy home has nothing that needs a look", () => {
  const home = [
    dev("temperatureSensor", { batteryLevel: 100 }),
    dev("contactSensor", { batteryStatus: "high", signalStrength: "high", tamperState: "normal" }),
  ];
  expect(needsLook(home)).toEqual([]);
});

test("the hub's low battery and lost mobile signal are one concern with two issues", () => {
  const hub = dev("gateway", { batteryLevel: 20, cellularConnectionState: "abnormal", tamperState: "normal" }, "Security hub");
  expect(needsLook([hub])).toEqual([{ device: hub.device, issues: ["Battery 20%", "No mobile signal"] }]);
});

test("a battery above 20% is fine", () => {
  expect(needsLook([dev("gateway", { batteryLevel: 21 })])).toEqual([]);
});

test("a weak or bad signal, a low battery status and tampering each need a look", () => {
  expect(needsLook([dev("smokeSensor", { signalStrength: "low" })])[0]?.issues).toEqual(["Weak signal"]);
  expect(needsLook([dev("smokeSensor", { signalStrength: "bad" })])[0]?.issues).toEqual(["Weak signal"]);
  expect(needsLook([dev("contactSensor", { batteryStatus: "low" })])[0]?.issues).toEqual(["Battery low"]);
  expect(needsLook([dev("contactSensor", { tamperState: "alarm" })])[0]?.issues).toEqual(["Tamper alert"]);
});

test("the alarm is described as disarmed or armed, and whether it is sounding", () => {
  expect(statusRow(dev("security", { status: "DISARMED", isAlarmed: false }))).toEqual({ value: "Disarmed", sub: "not sounding" });
  expect(statusRow(dev("security", { status: "ARMED_AWAY", isAlarmed: true }))).toEqual({ value: "Armed", sub: "sounding" });
});

test("the hub shows its network and battery", () => {
  expect(statusRow(dev("gateway", { networkMode: "ethernet", batteryLevel: 20, chargeState: true }))).toEqual({
    value: "On ethernet",
    sub: "battery 20%, charging",
  });
  expect(statusRow(dev("gateway", { networkMode: "wifi", batteryLevel: 90, chargeState: false }))).toEqual({
    value: "On wifi",
    sub: "battery 90%",
  });
});

test("the smoke sensor says whether it senses smoke", () => {
  expect(statusRow(dev("smokeSensor", { sensorState: "normal", batteryStatus: "high" }))).toEqual({ value: "No smoke", sub: "battery high" });
  expect(statusRow(dev("smokeSensor", { sensorState: "alarm", batteryStatus: "high" }))).toEqual({ value: "Smoke", sub: "battery high" });
});

test("the camera only says whether it records to its card", () => {
  expect(statusRow(dev("camera", { sdRecording: true, streamingSwitch: false }))).toEqual({ value: "Recording", sub: "to its SD card" });
  expect(statusRow(dev("camera", { sdRecording: false }))).toEqual({ value: "Not recording", sub: "" });
});

test("the home devices reuse their home rows", () => {
  expect(statusRow(dev("temperatureSensor", { temperatureValue: 21.9, humidityValue: 48 }))).toEqual({ value: "21.9 °C", sub: "48.0% humidity" });
});

test("an unknown device type still gets a row", () => {
  expect(statusRow(dev("vacuum", {}))).toEqual({ value: "No reading", sub: "" });
});

test("the headline counts the devices that need a look", () => {
  expect(devicesHeadline(8, 0)).toBe("All 8 devices look fine.");
  expect(devicesHeadline(8, 1)).toBe("8 devices, 1 needs a look.");
  expect(devicesHeadline(8, 2)).toBe("8 devices, 2 need a look.");
});

test("a device that went silent has no signal, says when it was last heard and needs a look", () => {
  const lamp = dev("bulb", { reachable: false, lastHeard: "2026-11-18T17:57:58" }, "Lamp");
  expect(statusRow(lamp)).toEqual({ value: "No signal", sub: "last heard 17:57, may be unplugged" });
  expect(statusRow(dev("plug", { reachable: false }))).toEqual({ value: "No signal", sub: "may be unplugged" });
  expect(needsLook([lamp])).toEqual([{ device: lamp.device, issues: ["No signal"] }]);
});
