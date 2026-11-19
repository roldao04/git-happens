import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "vitest";
import { parseRecording } from "./recording";
import { CARD_MIN_CONFIDENCE } from "./stateAt";

const rec = parseRecording(JSON.parse(readFileSync(resolve(__dirname, "../../public/recording.json"), "utf8")));

test("is a 21-day simulated run", () => {
  expect(rec.meta.days).toBe(21);
  expect(rec.meta.simulated).toBe(true);
});

test("holds all eleven sensors the run learned, each with readings", () => {
  expect(rec.sensors).toHaveLength(11);
  expect(rec.sensors.every((s) => rec.readings.some((r) => r.sensor_id === s.id))).toBe(true);
});

test("has five sensors strong enough for a card", () => {
  expect(rec.sensors.filter((s) => s.confidence >= CARD_MIN_CONFIDENCE)).toHaveLength(5);
});

test("every reading and event points at something real", () => {
  const sensors = new Set(rec.sensors.map((s) => s.id));
  const devices = new Set(rec.devices.map((d) => d.id));
  expect(rec.readings.every((r) => sensors.has(r.sensor_id))).toBe(true);
  expect(rec.events.every((e) => devices.has(e.device_id))).toBe(true);
  expect(rec.initial.every((v) => devices.has(v.device_id))).toBe(true);
});

test("has no camera motion in it", () => {
  expect(rec.initial.concat(rec.events).some((v) => /motion/i.test(v.capability))).toBe(false);
});
