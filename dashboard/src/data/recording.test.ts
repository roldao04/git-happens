import { describe, expect, test } from "vitest";
import { parseRecording, RecordingError, toMs } from "./recording";

function minimal(): Record<string, unknown> {
  return {
    meta: { household: "rosa", simulated: true, start: "2026-09-01T00:00:00", end: "2026-09-21T23:59:59", days: 21 },
    devices: [{ id: "d-plug", type: "plug", name: "Plug" }],
    initial: [{ device_id: "d-plug", capability: "instantPower", value: 0 }],
    events: [{ ts: "2026-09-01T07:59:39", device_id: "d-plug", capability: "instantPower", value: 2105 }],
    sensors: [{
      id: "pattern-2", name: null, learned_at: "2026-09-06", confidence: 0.84, status: "active",
      explanation: null, pattern: { parts: ["plug-A (about 2,100 W, about 3 min)"], time_of_day: "07:59", spread_min: 20 },
    }],
    readings: [{ sensor_id: "pattern-2", start: "2026-09-01T07:59:39", end: "2026-09-01T08:24:27", value: 2105 }],
  };
}

function sensor0(raw: Record<string, unknown>): Record<string, unknown> {
  const sensors = raw.sensors as Record<string, unknown>[];
  const first = sensors[0];
  if (!first) throw new Error("fixture has no sensor");
  return first;
}

describe("parseRecording", () => {
  test("parses a minimal valid recording", () => {
    const rec = parseRecording(minimal());
    expect(rec.meta.days).toBe(21);
    expect(rec.devices).toEqual([{ id: "d-plug", type: "plug", name: "Plug" }]);
    expect(rec.events[0]?.value).toBe(2105);
    expect(rec.sensors[0]?.pattern.time_of_day).toBe("07:59");
    expect(rec.readings[0]?.end).toBe("2026-09-01T08:24:27");
  });

  test("fills defaults for optional parts", () => {
    const raw = minimal();
    delete raw.initial;
    delete raw.events;
    delete (raw.meta as Record<string, unknown>).household;
    const rec = parseRecording(raw);
    expect(rec.initial).toEqual([]);
    expect(rec.events).toEqual([]);
    expect(rec.meta.household).toBe("");
  });

  test("names the missing field", () => {
    const raw = minimal();
    delete sensor0(raw).learned_at;
    expect(() => parseRecording(raw)).toThrow(new RecordingError("sensors[0].learned_at is missing"));
  });

  test.each([null, [], "x", 3])("rejects a non-object (%j)", (raw) => {
    expect(() => parseRecording(raw)).toThrow(new RecordingError("recording is not an object"));
  });

  test("keeps when a real home's last reading came in, and has none for a simulated one", () => {
    const raw = minimal();
    expect(parseRecording(raw).meta.updated).toBeNull();
    (raw.meta as Record<string, unknown>).updated = "2026-11-19T12:23:05";
    expect(parseRecording(raw).meta.updated).toBe("2026-11-19T12:23:05");
    (raw.meta as Record<string, unknown>).updated = "soon";
    expect(() => parseRecording(raw)).toThrow(/meta\.updated/);
  });

  test("keeps the hours nobody was watching, and has none when the file lists none", () => {
    const raw = minimal();
    expect(parseRecording(raw).gaps).toEqual([]);
    (raw as Record<string, unknown>).gaps = [{ start: "2026-11-17T12:05:34", end: "2026-11-19T01:50:31" }];
    expect(parseRecording(raw).gaps).toEqual([{ start: "2026-11-17T12:05:34", end: "2026-11-19T01:50:31" }]);
    (raw as Record<string, unknown>).gaps = [{ start: "2026-11-17T12:05:34" }];
    expect(() => parseRecording(raw)).toThrow(/gaps\[0\]\.end/);
  });

  test("rejects a bad time", () => {
    const raw = minimal();
    (raw.meta as Record<string, unknown>).start = "yesterday";
    expect(() => parseRecording(raw)).toThrow(/meta\.start/);
  });

  test("rejects an unknown status", () => {
    const raw = minimal();
    sensor0(raw).status = "maybe";
    expect(() => parseRecording(raw)).toThrow(/sensors\[0\]\.status/);
  });

  test("rejects parts that are not strings", () => {
    const raw = minimal();
    (sensor0(raw).pattern as Record<string, unknown>).parts = [3];
    expect(() => parseRecording(raw)).toThrow(/sensors\[0\]\.pattern\.parts/);
  });

  test("rejects a reading without a start", () => {
    const raw = minimal();
    delete (raw.readings as Record<string, unknown>[])[0]?.start;
    expect(() => parseRecording(raw)).toThrow(new RecordingError("readings[0].start is missing"));
  });

  test("rejects events out of time order", () => {
    const raw = minimal();
    raw.events = [
      { ts: "2026-09-01T08:00:00", device_id: "d-plug", capability: "instantPower", value: 1 },
      { ts: "2026-09-01T07:00:00", device_id: "d-plug", capability: "instantPower", value: 2 },
    ];
    expect(() => parseRecording(raw)).toThrow(new RecordingError("events[1] is earlier than events[0]"));
  });

  test("keeps unknown fields", () => {
    const raw = minimal();
    (sensor0(raw).pattern as Record<string, unknown>).lag_min = 18;
    raw.x = "ignored";
    expect(parseRecording(raw).sensors[0]?.pattern.lag_min).toBe(18);
  });
});

describe("toMs", () => {
  test("reads a local time as wall-clock ms", () => {
    expect(toMs("2026-12-31T23:59:59")).toBe(Date.UTC(2026, 11, 31, 23, 59, 59));
  });

  test("reads a date as its midnight", () => {
    expect(toMs("2026-09-06")).toBe(Date.UTC(2026, 8, 6));
  });

  test.each(["yesterday", "2026-13-01", "2026-09-01T25:00:00", "2026-09-01T08:00:00Z"])("rejects %s", (text) => {
    expect(() => toMs(text)).toThrow(RecordingError);
  });
});
