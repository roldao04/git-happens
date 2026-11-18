import { describe, expect, test } from "vitest";
import type { Sensor } from "./recording";
import type { DeviceNow } from "./stateAt";
import { deviceWords, evidence, formatClock, formatDate, foundSteps, homeHeadline, learnedHeadline, learnedSummary, partToWords, sensorTitle } from "./words";

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
  const dev = (type: string, values: DeviceNow["values"]): DeviceNow => ({ device: { id: type, type, name: type }, values, changedAt: null });

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
