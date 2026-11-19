import { expect, test } from "vitest";
import { recording, sensor } from "../test/fixtures";
import type { DeviceEvent, Recording } from "./recording";
import { DAY_MS, stateAt } from "./stateAt";
import { dayView, ticksFor, timeAt, zoomBetween } from "./timeline";

const D1 = Date.UTC(2026, 8, 1);
const at = (day: number, hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number) as [number, number];
  return D1 + (day - 1) * DAY_MS + (h * 60 + m) * 60_000;
};
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 19);
const ev = (device_id: string, capability: string, value: DeviceEvent["value"], ms: number): DeviceEvent => ({ device_id, capability, value, ts: iso(ms) });

const devices = [
  { id: "lamp", type: "bulb", name: "Light" },
  { id: "door", type: "contactSensor", name: "Contact sensor" },
  { id: "plug", type: "plug", name: "Plug" },
  { id: "temp", type: "temperatureSensor", name: "Temperature sensor" },
];

function rec(over: Partial<Recording> = {}): Recording {
  return recording({
    devices,
    initial: [{ device_id: "lamp", capability: "power", value: "off" }],
    events: [
      ev("lamp", "power", "on", at(1, "22:00")),
      ev("lamp", "power", "off", at(2, "01:00")),
      ev("door", "sensorState", "alarm", at(2, "08:05")),
      ev("door", "sensorState", "normal", at(2, "08:06")),
      ev("plug", "instantPower", 2140, at(2, "08:07")),
      ev("temp", "temperatureValue", 21, at(2, "08:08")),
      ev("plug", "instantPower", 0, at(2, "08:10")),
      ev("lamp", "power", "on", at(2, "18:00")),
    ],
    sensors: [
      sensor({ id: "pattern-2", learned_at: "2026-09-01", confidence: 0.84 }),
      sensor({ id: "pattern-7", learned_at: "2026-09-01", confidence: 0.5 }),
      sensor({ id: "pattern-9", learned_at: "2026-09-10", confidence: 0.9 }),
    ],
    readings: [
      { sensor_id: "pattern-2", start: iso(at(2, "08:05")), end: iso(at(2, "08:10")), value: null },
      { sensor_id: "pattern-7", start: iso(at(2, "18:00")), end: null, value: null },
      { sensor_id: "pattern-9", start: iso(at(2, "07:00")), end: iso(at(2, "07:30")), value: null },
      { sensor_id: "pattern-2", start: iso(at(3, "08:00")), end: iso(at(3, "08:05")), value: null },
    ],
    ...over,
  });
}

test("lanes are the lamp, door and plug, cut to the day", () => {
  const r = rec();
  const view = dayView(r, stateAt(r, at(3, "12:00")), 2);
  expect(view.dayStart).toBe(at(2, "00:00"));
  expect(view.upTo).toBe(at(3, "00:00"));
  expect(view.lanes.map((l) => l.device.id)).toEqual(["lamp", "door", "plug"]);
  const [lamp, door, plug] = view.lanes;
  expect(lamp?.spans).toEqual([
    { start: at(2, "00:00"), end: at(2, "01:00") },
    { start: at(2, "18:00"), end: null },
  ]);
  expect(door?.spans).toEqual([{ start: at(2, "08:05"), end: at(2, "08:06") }]);
  expect(plug?.spans).toEqual([{ start: at(2, "08:07"), end: at(2, "08:10") }]);
});

test("on the playhead's day nothing after now is shown", () => {
  const r = rec();
  const view = dayView(r, stateAt(r, at(2, "08:08")), 2);
  expect(view.upTo).toBe(at(2, "08:08"));
  expect(view.lanes[2]?.spans).toEqual([{ start: at(2, "08:07"), end: null }]);
  expect(view.entries.map((e) => e.sensor.id)).toEqual(["pattern-2"]);
  expect(view.entries[0]?.signals.map((s) => s.device.id)).toEqual(["door", "door", "plug"]);
});

test("entries are the day's readings of the sensors visible now, morning first, with the changes behind them", () => {
  const r = rec();
  const view = dayView(r, stateAt(r, at(3, "12:00")), 2);
  expect(view.entries.map((e) => [e.sensor.id, e.weak])).toEqual([
    ["pattern-2", false],
    ["pattern-7", true],
  ]);
  const tea = view.entries[0];
  expect(tea?.at).toBe(at(2, "08:05"));
  expect(tea?.end).toBe(at(2, "08:10"));
  expect(tea?.signals.map((s) => [s.device.id, s.value])).toEqual([
    ["door", "alarm"],
    ["door", "normal"],
    ["plug", 2140],
    ["plug", 0],
  ]);
});

test("a reading still going on lists the changes up to now", () => {
  const r = rec();
  const view = dayView(r, stateAt(r, at(3, "12:00")), 2);
  expect(view.entries[1]?.end).toBeNull();
  expect(view.entries[1]?.signals.map((s) => s.device.id)).toEqual(["lamp"]);
});

test("the day is kept between day 1 and the playhead's day", () => {
  const r = rec();
  const s = stateAt(r, at(3, "12:00"));
  expect(dayView(r, s, 0).day).toBe(1);
  expect(dayView(r, s, 9).day).toBe(3);
  expect(dayView(r, s, Number.NaN).day).toBe(3);
});

test("a device active from the start of the recording opens a span at midnight of day 1", () => {
  const r = rec({ initial: [{ device_id: "lamp", capability: "power", value: "on" }], events: [ev("lamp", "power", "off", at(1, "06:00"))] });
  expect(dayView(r, stateAt(r, at(1, "12:00")), 1).lanes[0]?.spans).toEqual([{ start: at(1, "00:00"), end: at(1, "06:00") }]);
});

test("signals are only the moments a device switches, not every power report while it runs", () => {
  const r = rec({
    events: [
      ev("plug", "instantPower", 2140, at(2, "08:07")),
      ev("plug", "instantPower", 2150, at(2, "08:08")),
      ev("plug", "instantPower", 2135, at(2, "08:09")),
      ev("plug", "instantPower", 0, at(2, "08:10")),
    ],
  });
  const view = dayView(r, stateAt(r, at(3, "12:00")), 2);
  expect(view.entries[0]?.signals.map((s) => s.value)).toEqual([2140, 0]);
  expect(view.lanes[2]?.spans).toEqual([{ start: at(2, "08:07"), end: at(2, "08:10") }]);
});

test("a brightness change does not end the lamp's on span", () => {
  const r = rec({
    events: [
      ev("lamp", "power", "on", at(2, "08:00")),
      ev("lamp", "brightness", 40, at(2, "08:30")),
      ev("lamp", "power", "off", at(2, "09:00")),
    ],
  });
  expect(dayView(r, stateAt(r, at(3, "12:00")), 2).lanes[0]?.spans).toEqual([{ start: at(2, "08:00"), end: at(2, "09:00") }]);
});

test("losing the signal ends the lamp's bar, and hearing it again still on starts a new one", () => {
  const r = rec({
    events: [
      ev("lamp", "power", "on", at(2, "08:00")),
      ev("lamp", "reachable", false, at(2, "10:00")),
      ev("lamp", "lastHeard", "2026-09-02T08:58:00", at(2, "10:00")),
      ev("lamp", "reachable", true, at(2, "15:00")),
    ],
  });
  const view = dayView(r, stateAt(r, at(2, "16:00")), 2);
  expect(view.lanes[0]?.spans).toEqual([{ start: at(2, "08:00"), end: at(2, "10:00") }, { start: at(2, "15:00"), end: null }]);
});

test("the hours nobody was watching are cut to the day", () => {
  const r = rec({ gaps: [{ start: iso(at(1, "12:05")), end: iso(at(3, "01:50")) }] });
  expect(dayView(r, stateAt(r, at(3, "12:00")), 2).gaps).toEqual([{ start: at(2, "00:00"), end: at(3, "00:00") }]);
  expect(dayView(r, stateAt(r, at(3, "12:00")), 3).gaps).toEqual([{ start: at(3, "00:00"), end: at(3, "01:50") }]);
  expect(dayView(r, stateAt(r, at(1, "18:00")), 1).gaps).toEqual([{ start: at(1, "12:05"), end: at(1, "18:00") }]);
});

test("a device on at the first reading starts its bar then, not at a midnight nobody watched", () => {
  const r = rec({
    initial: [{ device_id: "lamp", capability: "power", value: "on" }],
    events: [ev("lamp", "power", "off", at(1, "12:23"))],
    gaps: [{ start: iso(at(1, "00:00")), end: iso(at(1, "11:44")) }],
  });
  expect(dayView(r, stateAt(r, at(1, "18:00")), 1).lanes[0]?.spans).toEqual([{ start: at(1, "11:44"), end: at(1, "12:23") }]);
});

test("the lamp's bar takes the color it was, at full strength, and white light keeps the plain bar", () => {
  const r = rec({
    initial: [
      { device_id: "lamp", capability: "power", value: "off" },
      { device_id: "lamp", capability: "workMode", value: "colour" },
      { device_id: "lamp", capability: "color_rgb", value: "255,0,0" },
    ],
    events: [
      ev("lamp", "power", "on", at(2, "08:00")),
      ev("lamp", "color_rgb", "0,0,255", at(2, "08:30")),
      ev("lamp", "color_rgb", "46,46,46", at(2, "09:00")),
      ev("lamp", "workMode", "white", at(2, "09:30")),
      ev("lamp", "power", "off", at(2, "10:00")),
    ],
  });
  const view = dayView(r, stateAt(r, at(3, "12:00")), 2);
  expect(view.lanes[0]?.spans).toEqual([
    { start: at(2, "08:00"), end: at(2, "08:30"), color: "255,0,0" },
    { start: at(2, "08:30"), end: at(2, "09:00"), color: "0,0,255" },
    { start: at(2, "09:00"), end: at(2, "09:30"), color: "255,255,255" },
    { start: at(2, "09:30"), end: at(2, "10:00") },
  ]);
});

test("hour labels get finer as the window gets narrower", () => {
  const hm = (ms: number) => iso(ms).slice(11, 16);
  expect(ticksFor({ start: at(1, "00:00"), end: at(2, "00:00") }).map(hm)).toEqual(["00:00", "06:00", "12:00", "18:00", "00:00"]);
  expect(ticksFor({ start: at(1, "11:30"), end: at(1, "12:30") }).map(hm)).toEqual(["11:30", "11:45", "12:00", "12:15", "12:30"]);
  expect(ticksFor({ start: at(1, "11:44"), end: at(1, "12:24") }).map(hm)).toEqual(["11:50", "12:00", "12:10", "12:20"]);
});

test("a point on the chart is a time in the window, to the minute", () => {
  const zoom = { start: at(1, "12:00"), end: at(1, "14:00") };
  expect(timeAt(150, 100, 200, zoom)).toBe(at(1, "12:30"));
  expect(timeAt(151, 100, 200, zoom)).toBe(at(1, "12:31"));
  expect(timeAt(20, 100, 200, zoom)).toBe(at(1, "12:00"));
  expect(timeAt(900, 100, 200, zoom)).toBe(at(1, "14:00"));
});

test("a drag zooms to the stretch it covers, either way round, unless it is under five minutes", () => {
  expect(zoomBetween(at(1, "12:30"), at(1, "11:30"))).toEqual({ start: at(1, "11:30"), end: at(1, "12:30") });
  expect(zoomBetween(at(1, "12:00"), at(1, "12:04"))).toBeNull();
});
