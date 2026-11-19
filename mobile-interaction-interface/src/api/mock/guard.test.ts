import { expect, test } from "vitest";
import { controlsFor } from "../controls";
import type { Device } from "../types";
import { checkWrite } from "./guard";

const dev = (type: string, reachable = true): Device => ({ id: type, type, name: type, values: {}, controls: controlsFor(type), reachable, lastSeen: null });
const home = { target: 17, someoneHome: true };

test("the alarm and the sensors can never be written", () => {
  expect(checkWrite(dev("security"), { type: "power", value: "on" }, home)).toMatch(/can't be changed/);
  expect(checkWrite(dev("smokeSensor"), { type: "power", value: "off" }, home)).toMatch(/can't be changed/);
});

test("a person may switch a plug on, and the lamp takes power, brightness and white temperature", () => {
  expect(checkWrite(dev("plug"), { type: "power", value: "on" }, home)).toBeNull();
  expect(checkWrite(dev("bulb"), { type: "brightness", value: 30 }, home)).toBeNull();
  expect(checkWrite(dev("bulb"), { type: "brightness", value: 0 }, home)).toMatch(/1 to 100/);
  expect(checkWrite(dev("plug"), { type: "brightness", value: 30 }, home)).toMatch(/only accepts power/);
});

test("the thermostat keeps the guard's limits: 16–23 °C, 3 °C a write, 18 °C while nobody is home", () => {
  const t = dev("thermostat");
  expect(checkWrite(t, { type: "targetTemperature", value: 19 }, home)).toBeNull();
  expect(checkWrite(t, { type: "targetTemperature", value: 24 }, home)).toMatch(/16 to 23/);
  expect(checkWrite(t, { type: "targetTemperature", value: 21 }, home)).toMatch(/at most 3 °C/);
  expect(checkWrite(t, { type: "targetTemperature", value: 18.5 }, { target: 17, someoneHome: false })).toMatch(/no one is home/);
  expect(checkWrite(t, { type: "targetTemperature", value: 18 }, { target: 17, someoneHome: false })).toBeNull();
});

test("a device that stopped reporting can't be changed", () => {
  expect(checkWrite(dev("bulb", false), { type: "power", value: "on" }, home)).toMatch(/isn't reporting/);
});
