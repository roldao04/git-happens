import { describe, expect, test } from "vitest";
import type { Sensor } from "./recording";
import { deviceWords, evidence, formatClock, formatDate, partToWords, sensorTitle } from "./words";

describe("partToWords", () => {
  test.each([
    ["bulb-A (power on, about 43 min)", "lamp on, about 43 min"],
    ["plug-A (about 2,100 W, about 3 min)", "plug about 2,100 W, about 3 min"],
    ["contactSensor-A (sensorState alarm, about 40 s)", "door open, about 40 s"],
    ["contactSensor-B (sensorState normal, about 2 min)", "door closed, about 2 min"],
    ["home-A (quiet after bulb until bulb, about 9.1 h)", "the home quiet, about 9.1 h"],
    ["end of home-A (quiet after bulb until bulb, about 9.1 h)", "end of the home quiet"],
    ["temperatureSensor-A (about 1.5 °C, about 2 h)", "temperature about 1.5 °C, about 2 h"],
    ["weird thing", "weird thing"],
  ])("%s", (part, words) => {
    expect(partToWords(part)).toBe(words);
  });
});

test("evidence joins parts with then", () => {
  expect(evidence(["bulb-A (power on, about 43 min)", "plug-A (about 2,100 W, about 3 min)"]))
    .toBe("lamp on, about 43 min, then plug about 2,100 W, about 3 min");
  expect(evidence([])).toBe("");
});

describe("deviceWords", () => {
  test.each([
    ["contactSensor", "sensorState", "normal", "closed"],
    ["contactSensor", "sensorState", "alarm", "open"],
    ["plug", "instantPower", 2105, "2,105 W, in use"],
    ["plug", "instantPower", 3, "3 W"],
    ["temperatureSensor", "temperatureValue", 21.43, "21.4 °C"],
    ["temperatureSensor", "humidityValue", 48, "48.0%"],
    ["bulb", "power", true, "on"],
    ["bulb", "power", "off", "off"],
    ["bulb", "brightness", 100, "100%"],
    ["camera", "streamingSwitch", "x", "x"],
    ["plug", "instantPower", null, "—"],
  ])("%s.%s %j", (type, capability, value, words) => {
    expect(deviceWords(type, capability, value)).toBe(words);
  });
});

describe("sensorTitle", () => {
  const base: Sensor = { id: "pattern-2", name: null, learned_at: "2026-09-06", confidence: 0.8, status: "active", pattern: { parts: [], time_of_day: "07:59" } };

  test("numbers an unnamed sensor", () => {
    expect(sensorTitle(base)).toEqual({ text: "Pattern 2", unnamed: true });
  });

  test("uses the name when there is one", () => {
    expect(sensorTitle({ ...base, name: "Morning tea" })).toEqual({ text: "Morning tea", unnamed: false });
  });

  test("falls back to the id", () => {
    expect(sensorTitle({ ...base, id: "odd" })).toEqual({ text: "odd", unnamed: true });
  });
});

test("formatClock reads the wall clock", () => {
  expect(formatClock(Date.UTC(2026, 8, 9, 8, 12))).toEqual({ weekday: "Wed", time: "08:12" });
});

test("formatDate gives weekday, day and month", () => {
  expect(formatDate(Date.UTC(2026, 8, 8, 8, 5))).toBe("Tue 8 Sep");
});
