import { expect, test } from "vitest";
import { chatStoreFor, makeApi } from "../select";
import { alertsOf, alertText } from "./alerts";
import { makeHouse } from "./houses";
import { MockHomeApi } from "./MockHomeApi";

test("the same sentence as the film, and as source/alerts.py", () => {
  expect(alertText("Living-room window", "contactSensor", "Night", "2026-09-21T21:10:00"))
    .toBe("The living-room window is still open. Night is starting, and it has been open since 21:10.");
  expect(alertText("Kettle", "plug", "Night", "2026-09-21T21:10:00"))
    .toBe("The kettle is still on. Night is starting, and it has been on since 21:10.");
});

test("bedtime: Night has started and the window is open, so one alert", async () => {
  const s = await new MockHomeApi("demo", 0, "bedtime").getStatus();
  expect(s.title).toBe("Night");
  expect(s.alerts).toEqual([{ id: "habit-night:window:2026-09-21", at: "2026-09-21T23:10:00",
    text: "The living-room window is still open. Night is starting, and it has been open since 21:10." }]);
});

test("the normal demo morning has no alert", async () => {
  expect((await new MockHomeApi("demo", 0).getStatus()).alerts).toEqual([]);
});

test("no alert once the window is closed, or two hours after Night started", () => {
  const closed = makeHouse("demo", "bedtime");
  const win = closed.devices.find((d) => d.name === "Living-room window")!;
  win.values.sensorState = "normal";
  expect(alertsOf(closed)).toEqual([]);
  const late = makeHouse("demo", "bedtime");
  late.now = "2026-09-22T01:11:00";
  expect(alertsOf(late)).toEqual([]);
});

test("?moment=bedtime starts the demo at 23:10, with its own chats", async () => {
  expect((await makeApi("demo", "?moment=bedtime").getHome()).now).toBe("2026-09-21T23:10:00");
  expect((await makeApi("demo", "").getHome()).now).toBe("2026-09-21T09:12:00");
  expect(chatStoreFor("demo", "?moment=bedtime")).not.toBe(chatStoreFor("demo", ""));
});

test("a device switched off, unlike the habit expects, isn't 'still on'", () => {
  const house = makeHouse("demo", "bedtime");
  const kettle = house.devices.find((d) => d.name === "Kettle")!;
  house.expects = [{ ...house.expects[0]!, tag: "kettle", deviceId: kettle.id, capability: "power", value: "on" }];
  house.changedAt[kettle.id] = "2026-09-21T22:40:00";
  kettle.values.power = "off";
  expect(alertsOf(house)).toEqual([]);
});
