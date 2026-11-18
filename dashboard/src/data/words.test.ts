import { describe, expect, test } from "vitest";
import type { Sensor } from "./recording";
import type { DeviceNow } from "./stateAt";
import { deviceRow, deviceWords, evidence, formatClock, formatDate, foundSteps, homeHeadline, homeSummary, homeTitle, learnedHeadline, learnedSummary, partToWords, sensorTitle } from "./words";

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

describe("homeHeadline", () => {
  const dev = (type: string, values: DeviceNow["values"]): DeviceNow => ({ device: { id: type, type, name: type }, values, changedAt: null, since: null });

  test("says what the door, lamp, plug and temperature are doing", () => {
    expect(
      homeHeadline([
        dev("contactSensor", { sensorState: "normal" }),
        dev("bulb", { power: true }),
        dev("plug", { instantPower: 0 }),
        dev("temperatureSensor", { temperatureValue: 21.94 }),
      ]),
    ).toBe("The door is closed, the lamp is on and the plug is at rest. It is 21.9 °C inside.");
  });

  test("an open door and a busy plug", () => {
    expect(homeHeadline([dev("contactSensor", { sensorState: "alarm" }), dev("plug", { instantPower: 2105 })])).toBe(
      "The door is open and the plug is in use.",
    );
  });

  test("leaves out devices with no value yet", () => {
    expect(homeHeadline([dev("bulb", {}), dev("temperatureSensor", { temperatureValue: 20 })])).toBe("It is 20.0 °C inside.");
    expect(homeHeadline([])).toBe("Nothing to show yet.");
  });
});

describe("learnedHeadline", () => {
  test("counts the sensors shown as cards", () => {
    expect(learnedHeadline(0)).toBe("Alfred hasn't learned anything yet.");
    expect(learnedHeadline(1)).toBe("Alfred has learned 1 sensor.");
    expect(learnedHeadline(5)).toBe("Alfred has learned 5 sensors.");
  });
});

describe("learnedSummary", () => {
  test("says how many are new today and how many are still weak", () => {
    expect(learnedSummary(1, 2)).toBe("1 is new today. 2 more are too weak to show yet.");
    expect(learnedSummary(2, 1)).toBe("2 are new today. 1 more is too weak to show yet.");
    expect(learnedSummary(0, 0)).toBe("");
  });
});

describe("foundSteps", () => {
  test("one step per part, then the habit", () => {
    expect(foundSteps(["bulb-A (power on, about 43 min)", "plug-A (about 2,100 W, about 3 min)"], "07:59", 20)).toEqual([
      { title: "First", text: "lamp on, about 43 min" },
      { title: "Then", text: "plug about 2,100 W, about 3 min" },
      { title: "Habit", text: "This repeats around 07:59, give or take 20 min." },
    ]);
  });

  test("without a spread the habit step says only the time", () => {
    expect(foundSteps(["home-A (quiet after bulb until bulb, about 9.1 h)"], "23:07", undefined)).toEqual([
      { title: "First", text: "the home quiet, about 9.1 h" },
      { title: "Habit", text: "This repeats around 23:07." },
    ]);
  });
});

describe("Home page words", () => {
  const t = (h: number, m: number) => Date.UTC(2026, 8, 9, h, m);
  const dev = (type: string, values: DeviceNow["values"], since: number | null = null): DeviceNow => ({
    device: { id: type, type, name: type },
    values,
    changedAt: null,
    since,
  });

  test("the title says the most notable thing, else all quiet", () => {
    expect(homeTitle([dev("contactSensor", { sensorState: "alarm" }), dev("bulb", { power: true })])).toBe("The door is open.");
    expect(homeTitle([dev("plug", { instantPower: 2105 }), dev("bulb", { power: true })])).toBe("The plug is in use.");
    expect(homeTitle([dev("bulb", { power: true })])).toBe("The lamp is on.");
    expect(homeTitle([dev("contactSensor", { sensorState: "normal" }), dev("plug", { instantPower: 0 })])).toBe("All quiet at home.");
  });

  test("the summary adds what was learned today", () => {
    expect(homeSummary([], 0)).toBe("Nothing to show yet. Nothing new was learned today.");
    expect(homeSummary([dev("temperatureSensor", { temperatureValue: 20 })], 1)).toBe("It is 20.0 °C inside. Alfred learned 1 new sensor today.");
    expect(homeSummary([], 2)).toBe("Nothing to show yet. Alfred learned 2 new sensors today.");
  });

  test("each device has a value and a line under its name", () => {
    expect(deviceRow(dev("contactSensor", { sensorState: "normal" }, t(8, 41)))).toEqual({ value: "Closed", sub: "closed since 08:41" });
    expect(deviceRow(dev("bulb", { power: true, brightness: 100 }, t(7, 33)))).toEqual({ value: "On", sub: "on since 07:33, 100%" });
    expect(deviceRow(dev("bulb", { power: false, brightness: 100 }))).toEqual({ value: "Off", sub: "" });
    expect(deviceRow(dev("plug", { instantPower: 2105 }, t(7, 59)))).toEqual({ value: "2,105 W", sub: "in use since 07:59" });
    expect(deviceRow(dev("plug", { instantPower: 0 }, t(8, 2)))).toEqual({ value: "0 W", sub: "at rest since 08:02" });
    expect(deviceRow(dev("temperatureSensor", { temperatureValue: 21.94, humidityValue: 48 }))).toEqual({ value: "21.9 °C", sub: "48.0% humidity" });
  });

  test("a device with nothing to show gives no row", () => {
    expect(deviceRow(dev("gateway", { tamperState: "normal" }))).toBeNull();
    expect(deviceRow(dev("plug", {}))).toBeNull();
  });
});
