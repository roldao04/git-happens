import { describe, expect, test } from "vitest";
import type { Reading, Recording, Sensor } from "./recording";
import { toMs } from "./recording";
import { appearsAt, stateAt } from "./stateAt";

function sensor(id: string, confidence: number, learned_at: string, status: Sensor["status"] = "active"): Sensor {
  return { id, name: null, learned_at, confidence, status, pattern: { parts: [], time_of_day: "08:00" } };
}

function rec(over: Partial<Recording> = {}): Recording {
  return {
    meta: { household: "rosa", simulated: true, start: "2026-09-01T00:00:00", end: "2026-09-21T23:59:59", days: 21, updated: null },
    devices: [{ id: "d-plug", type: "plug", name: "Plug" }],
    initial: [{ device_id: "d-plug", capability: "instantPower", value: 0 }],
    events: [{ ts: "2026-09-01T07:59:39", device_id: "d-plug", capability: "instantPower", value: 2105 }],
    sensors: [],
    readings: [],
    gaps: [],
    ...over,
  };
}

const at = (text: string) => toMs(text);

describe("devices", () => {
  test("apply events up to t", () => {
    expect(stateAt(rec(), at("2026-09-01T07:59:38")).devices[0]?.values.instantPower).toBe(0);
    expect(stateAt(rec(), at("2026-09-01T07:59:39")).devices[0]?.values.instantPower).toBe(2105);
  });

  test("mark a change for 10 simulated minutes", () => {
    expect(stateAt(rec(), at("2026-09-01T08:09:39")).devices[0]?.changedAt).toBe(at("2026-09-01T07:59:39"));
    expect(stateAt(rec(), at("2026-09-01T08:09:40")).devices[0]?.changedAt).toBeNull();
  });

  test("a device with no events keeps its initial value and no mark", () => {
    const r = rec({ events: [] });
    const d = stateAt(r, at("2026-09-02T00:00:00")).devices[0];
    expect(d?.values.instantPower).toBe(0);
    expect(d?.changedAt).toBeNull();
  });

  test("the mark lasts as long as the hold it is given", () => {
    const hold = 60 * 60 * 1000;
    expect(stateAt(rec(), at("2026-09-01T08:59:39"), hold).devices[0]?.changedAt).toBe(at("2026-09-01T07:59:39"));
    expect(stateAt(rec(), at("2026-09-01T08:59:40"), hold).devices[0]?.changedAt).toBeNull();
  });

  test("temperature and humidity changes leave no mark", () => {
    const r = rec({
      devices: [{ id: "d-t", type: "temperatureSensor", name: "Temperature" }],
      initial: [],
      events: [{ ts: "2026-09-01T07:59:39", device_id: "d-t", capability: "temperatureValue", value: 21.5 }],
    });
    expect(stateAt(r, at("2026-09-01T08:00:00")).devices[0]?.changedAt).toBeNull();
  });

  test("a change to a value the row does not show leaves no mark", () => {
    const r = rec({ events: [{ ts: "2026-09-01T07:59:39", device_id: "d-plug", capability: "todayConsumption", value: 12 }] });
    const d = stateAt(r, at("2026-09-01T08:00:00")).devices[0];
    expect(d?.values.todayConsumption).toBe(12);
    expect(d?.changedAt).toBeNull();
  });

  test("a device with no signal shows only that, until it is heard again", () => {
    const r = rec({
      devices: [{ id: "d-lamp", type: "bulb", name: "Lamp" }],
      initial: [{ device_id: "d-lamp", capability: "power", value: "on" }],
      events: [
        { ts: "2026-09-01T03:00:00", device_id: "d-lamp", capability: "reachable", value: false },
        { ts: "2026-09-01T03:00:00", device_id: "d-lamp", capability: "lastHeard", value: "2026-09-01T01:58:30" },
        { ts: "2026-09-01T04:00:00", device_id: "d-lamp", capability: "reachable", value: true },
      ],
    });
    expect(stateAt(r, at("2026-09-01T03:30:00")).devices[0]?.values).toEqual({ reachable: false, lastHeard: "2026-09-01T01:58:30" });
    expect(stateAt(r, at("2026-09-01T04:30:00")).devices[0]?.values.power).toBe("on");
  });
});

describe("sensors", () => {
  test("appear at midnight after learned_at, freshly learned for one day", () => {
    const r = rec({ sensors: [sensor("a", 0.8, "2026-09-06")] });
    expect(appearsAt(sensor("a", 0.8, "2026-09-06"))).toBe(at("2026-09-07T00:00:00"));
    expect(stateAt(r, at("2026-09-06T23:59:59")).cards).toEqual([]);
    expect(stateAt(r, at("2026-09-07T00:00:00")).cards[0]?.justLearned).toBe(true);
    expect(stateAt(r, at("2026-09-08T00:00:00")).cards[0]?.justLearned).toBe(false);
  });

  test("carry their earlier readings up to t", () => {
    const readings: Reading[] = [
      { sensor_id: "a", start: "2026-09-01T08:00:00", end: null, value: null },
      { sensor_id: "a", start: "2026-09-08T08:00:00", end: null, value: null },
    ];
    const card = stateAt(rec({ sensors: [sensor("a", 0.8, "2026-09-05")], readings }), at("2026-09-07T12:00:00")).cards[0];
    expect(card?.readings.map((x) => x.start)).toEqual(["2026-09-01T08:00:00"]);
    expect(card?.daysSoFar).toBe(1);
  });

  test("count days, not readings", () => {
    const readings: Reading[] = [
      { sensor_id: "a", start: "2026-09-01T08:00:00", end: null, value: null },
      { sensor_id: "a", start: "2026-09-01T19:00:00", end: null, value: null },
      { sensor_id: "a", start: "2026-09-02T08:00:00", end: null, value: null },
    ];
    const card = stateAt(rec({ sensors: [sensor("a", 0.8, "2026-09-02")], readings }), at("2026-09-04T00:00:00")).cards[0];
    expect(card?.daysSoFar).toBe(2);
  });

  test("rank cards newest first, and the weak ones as also noticed", () => {
    const r = rec({ sensors: [sensor("a", 0.84, "2026-09-06"), sensor("b", 0.59, "2026-09-07"), sensor("c", 0.6, "2026-09-08")] });
    const s = stateAt(r, at("2026-09-10T00:00:00"));
    expect(s.cards.map((x) => x.sensor.id)).toEqual(["c", "a"]);
    expect(s.alsoNoticed.map((x) => x.sensor.id)).toEqual(["b"]);
  });

  test("hide merged ones and keep fading ones", () => {
    const r = rec({ sensors: [sensor("m", 0.9, "2026-09-02", "merged"), sensor("f", 0.9, "2026-09-02", "fading")] });
    expect(stateAt(r, at("2026-09-05T00:00:00")).cards.map((x) => x.sensor.id)).toEqual(["f"]);
  });

  test("one learned on the last day shows only at the end", () => {
    const r = rec({ sensors: [sensor("z", 0.9, "2026-09-21")] });
    expect(stateAt(r, at("2026-09-21T23:59:58")).cards).toEqual([]);
    const end = stateAt(r, at("2026-09-21T23:59:59"));
    expect(end.atEnd).toBe(true);
    expect(end.cards.map((x) => x.sensor.id)).toEqual(["z"]);
  });
});

describe("time", () => {
  test("clamps t and flags the end", () => {
    const s = stateAt(rec(), at("2026-10-05T00:00:00"));
    expect(s.t).toBe(at("2026-09-21T23:59:59"));
    expect(s.atEnd).toBe(true);
    expect(stateAt(rec(), at("2026-08-01T00:00:00")).t).toBe(at("2026-09-01T00:00:00"));
  });

  test("numbers days across a month and a year", () => {
    const r = rec({ meta: { household: "x", simulated: true, start: "2026-12-30T00:00:00", end: "2027-01-05T23:59:59", days: 7, updated: null } });
    const s = stateAt(r, at("2027-01-01T10:00:00"));
    expect(s.day).toBe(3);
    expect(s.days).toBe(7);
  });

  test("counts days from the dates when meta.days is missing", () => {
    const r = rec({ meta: { household: "x", simulated: true, start: "2026-09-01T00:00:00", end: "2026-09-03T23:59:59", days: 0, updated: null } });
    expect(stateAt(r, at("2026-09-01T00:00:00")).days).toBe(3);
  });

  test("lists the days on which sensors appear", () => {
    const r = rec({ sensors: [sensor("a", 0.9, "2026-09-06"), sensor("b", 0.3, "2026-09-06"), sensor("c", 0.9, "2026-09-02"), sensor("z", 0.9, "2026-09-21")] });
    expect(stateAt(r, at("2026-09-01T00:00:00")).learnDays).toEqual([3, 7, 21]);
  });
});

describe("latest", () => {
  const door = { id: "d-door", type: "contactSensor", name: "Front door" };
  const busy = () =>
    rec({
      devices: [{ id: "d-plug", type: "plug", name: "Plug" }, door],
      events: [
        { ts: "2026-09-01T07:59:39", device_id: "d-plug", capability: "instantPower", value: 2105 },
        { ts: "2026-09-01T08:02:39", device_id: "d-plug", capability: "instantPower", value: 0 },
        { ts: "2026-09-01T08:10:00", device_id: "d-door", capability: "sensorState", value: "alarm" },
        { ts: "2026-09-01T08:10:40", device_id: "d-door", capability: "sensorState", value: "normal" },
      ],
    });
  const hold = 2 * 60 * 60 * 1000;

  test("is the last time a door, lamp or plug went active, even after it went back", () => {
    expect(stateAt(busy(), at("2026-09-01T08:05:00"), hold).latest).toEqual({
      device: { id: "d-plug", type: "plug", name: "Plug" },
      capability: "instantPower",
      value: 2105,
      at: at("2026-09-01T07:59:39"),
    });
    expect(stateAt(busy(), at("2026-09-01T08:11:00"), hold).latest?.device).toEqual(door);
  });

  test("goes away once the hold is over", () => {
    expect(stateAt(busy(), at("2026-09-01T10:10:00"), hold).latest?.device).toEqual(door);
    expect(stateAt(busy(), at("2026-09-01T10:10:01"), hold).latest).toBeNull();
  });

  test("is null before anything has happened", () => {
    expect(stateAt(busy(), at("2026-09-01T07:00:00"), hold).latest).toBeNull();
  });

  test("a lamp turning on counts and turning off does not", () => {
    const lamp = { id: "d-lamp", type: "bulb", name: "Lamp" };
    const r = rec({
      devices: [lamp],
      initial: [],
      events: [
        { ts: "2026-09-01T07:00:00", device_id: "d-lamp", capability: "power", value: true },
        { ts: "2026-09-01T07:30:00", device_id: "d-lamp", capability: "power", value: false },
      ],
    });
    expect(stateAt(r, at("2026-09-01T07:45:00"), hold).latest?.at).toBe(at("2026-09-01T07:00:00"));
  });
});

describe("today", () => {
  const door = { id: "d-door", type: "contactSensor", name: "Front door" };
  const plug = { id: "d-plug", type: "plug", name: "Plug" };
  const day = () =>
    rec({
      devices: [plug, door],
      events: [
        { ts: "2026-09-01T23:00:00", device_id: "d-door", capability: "sensorState", value: "alarm" },
        { ts: "2026-09-01T23:00:30", device_id: "d-door", capability: "sensorState", value: "normal" },
        { ts: "2026-09-02T07:59:00", device_id: "d-plug", capability: "instantPower", value: 2105 },
        { ts: "2026-09-02T08:00:00", device_id: "d-plug", capability: "instantPower", value: 2110 },
        { ts: "2026-09-02T08:02:00", device_id: "d-plug", capability: "instantPower", value: 0 },
        { ts: "2026-09-02T08:10:00", device_id: "d-door", capability: "sensorState", value: "alarm" },
      ],
    });

  test("lists what went active since midnight, newest first", () => {
    const items = stateAt(day(), at("2026-09-02T09:00:00")).today;
    expect(items.map((i) => [i.kind, i.at])).toEqual([
      ["device", at("2026-09-02T08:10:00")],
      ["device", at("2026-09-02T07:59:00")],
    ]);
  });

  test("a plug that stays above its rest counts once", () => {
    const items = stateAt(day(), at("2026-09-02T08:01:00")).today;
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "device", device: plug, value: 2105 });
  });

  test("leaves out what happens after the playhead", () => {
    expect(stateAt(day(), at("2026-09-02T07:58:59")).today).toEqual([]);
  });

  test("a habit seen today shows once its sensor is a card", () => {
    const s = sensor("pattern-2", 0.84, "2026-09-01");
    const readings: Reading[] = [
      { sensor_id: "pattern-2", start: "2026-09-01T07:59:00", end: null, value: null },
      { sensor_id: "pattern-2", start: "2026-09-02T07:59:00", end: null, value: null },
    ];
    const items = stateAt(rec({ events: [], sensors: [s], readings }), at("2026-09-02T09:00:00")).today;
    expect(items).toEqual([{ kind: "habit", at: at("2026-09-02T07:59:00"), sensor: s }]);
  });

  test("a weak or not yet learned sensor adds nothing", () => {
    const weak = sensor("pattern-5", 0.4, "2026-09-01");
    const later = sensor("pattern-3", 0.9, "2026-09-05");
    const readings: Reading[] = [
      { sensor_id: "pattern-5", start: "2026-09-02T07:00:00", end: null, value: null },
      { sensor_id: "pattern-3", start: "2026-09-02T07:30:00", end: null, value: null },
    ];
    expect(stateAt(rec({ events: [], sensors: [weak, later], readings }), at("2026-09-02T09:00:00")).today).toEqual([]);
  });
});

describe("since", () => {
  test("is when a door, lamp or plug last changed, and stays after the hold", () => {
    const d = stateAt(rec(), at("2026-09-01T12:00:00")).devices[0];
    expect(d?.since).toBe(at("2026-09-01T07:59:39"));
    expect(d?.changedAt).toBeNull();
  });

  test("is null for a device that has not changed yet", () => {
    expect(stateAt(rec(), at("2026-09-01T07:00:00")).devices[0]?.since).toBeNull();
  });
});
