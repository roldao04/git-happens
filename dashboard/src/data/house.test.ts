import { expect, test } from "vitest";
import { sensor, sensorNow, state } from "../test/fixtures";
import type { Value } from "./recording";
import type { DeviceNow } from "./stateAt";
import { alfredNote, houseLabel, houseView } from "./house";

const dev = (type: string, values: Record<string, Value>): DeviceNow => ({ device: { id: type, type, name: type }, values, changedAt: null, since: null });
const at = (h: number, m = 0) => Date.UTC(2026, 8, 9, h, m);

test("the house follows the lamp, the door, the plug and the temperature", () => {
  const view = houseView([dev("bulb", { power: "on" }), dev("contactSensor", { sensorState: "alarm" }), dev("plug", { instantPower: 2100 }), dev("temperatureSensor", { temperatureValue: 19.94 })]);
  expect(view).toEqual({ lampOn: true, doorOpen: true, plugInUse: true, temperature: 19.94 });
});

test("a quiet house has the lamp off, the door closed and the plug at rest", () => {
  const view = houseView([dev("bulb", { power: "off" }), dev("contactSensor", { sensorState: "normal" }), dev("plug", { instantPower: 2 })]);
  expect(view).toEqual({ lampOn: false, doorOpen: false, plugInUse: false, temperature: null });
});

test("a device with no reading is unknown, not off", () => {
  expect(houseView([])).toEqual({ lampOn: null, doorOpen: null, plugInUse: null, temperature: null });
});

test("the picture's label says what it shows and leaves out what it doesn't know", () => {
  expect(houseLabel({ lampOn: true, doorOpen: false, plugInUse: false, temperature: 19.94 })).toBe(
    "The home: the lamp is on, the door is closed, the plug is at rest, 19.9 °C inside.",
  );
  expect(houseLabel({ lampOn: null, doorOpen: true, plugInUse: null, temperature: null })).toBe("The home: the door is open.");
  expect(houseLabel({ lampOn: null, doorOpen: null, plugInUse: null, temperature: null })).toBe("The home: no readings yet.");
});

test("before any habit, Alfred says it is still getting to know the home", () => {
  expect(alfredNote(state({ t: at(8, 20), day: 3, days: 21 }))).toBe(
    "Good morning. I'm still getting to know your home. It's day 3 of 21, and nothing repeats often enough yet to call a habit.",
  );
});

test("with habits but none seen today, Alfred counts them and keeps watching", () => {
  expect(alfredNote(state({ t: at(14), cards: [sensorNow(), sensorNow({ sensor: sensor({ id: "pattern-4" }) })] }))).toBe(
    "Good afternoon. I know 2 of your habits now. None so far today; I'll keep an eye out.",
  );
});

test("a habit recognized today is named, with the time it started", () => {
  const tea = sensor({ id: "pattern-6" });
  const note = alfredNote(state({ t: at(20), cards: [sensorNow({ sensor: tea })], today: [{ kind: "habit", at: at(7, 58), sensor: tea }] }));
  expect(note).toBe("Good evening. I know 1 of your habits now. Today I already recognized Pattern 6 at 07:58.");
});

test("several habits today are counted, and the latest is named", () => {
  const a = sensor({ id: "pattern-2" });
  const b = sensor({ id: "pattern-3", name: "Morning tea" });
  const today = [
    { kind: "habit" as const, at: at(7, 40), sensor: a },
    { kind: "habit" as const, at: at(8, 5), sensor: b },
  ];
  expect(alfredNote(state({ t: at(9), cards: [sensorNow({ sensor: a }), sensorNow({ sensor: b })], today }))).toBe(
    "Good morning. I know 2 of your habits now. Today I already recognized 2 of them, most recently Morning tea at 08:05.",
  );
});

test("night hours get a good evening too, and one habit is said in the singular", () => {
  expect(alfredNote(state({ t: at(2), cards: [sensorNow()] }))).toBe("Good evening. I know 1 of your habits now. None so far today; I'll keep an eye out.");
});

test("the latest habit is named whatever order today's feed is in, as stateAt lists it newest first", () => {
  const a = sensor({ id: "pattern-2" });
  const b = sensor({ id: "pattern-3" });
  const today = [
    { kind: "habit" as const, at: at(8, 5), sensor: b },
    { kind: "habit" as const, at: at(7, 40), sensor: a },
  ];
  expect(alfredNote(state({ t: at(9), cards: [sensorNow({ sensor: a }), sensorNow({ sensor: b })], today }))).toContain("most recently Pattern 3 at 08:05");
});
