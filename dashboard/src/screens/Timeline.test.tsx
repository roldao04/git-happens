import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { DeviceEvent, Recording } from "../data/recording";
import { DAY_MS, stateAt } from "../data/stateAt";
import { playback, recording, sensor } from "../test/fixtures";
import { Timeline } from "./Timeline";

const D1 = Date.UTC(2026, 8, 1);
const at = (day: number, hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number) as [number, number];
  return D1 + (day - 1) * DAY_MS + (h * 60 + m) * 60_000;
};
const iso = (ms: number) => new Date(ms).toISOString().slice(0, 19);
const ev = (device_id: string, capability: string, value: DeviceEvent["value"], ms: number): DeviceEvent => ({ device_id, capability, value, ts: iso(ms) });

const rec: Recording = recording({
  devices: [
    { id: "lamp", type: "bulb", name: "Light" },
    { id: "door", type: "contactSensor", name: "Contact sensor" },
    { id: "plug", type: "plug", name: "Plug" },
  ],
  events: [
    ev("door", "sensorState", "alarm", at(2, "08:05")),
    ev("door", "sensorState", "normal", at(2, "08:06")),
    ev("plug", "instantPower", 2140, at(2, "08:07")),
    ev("plug", "instantPower", 0, at(2, "08:10")),
  ],
  sensors: [
    sensor({ id: "pattern-2", learned_at: "2026-09-01", confidence: 0.84 }),
    sensor({ id: "pattern-7", learned_at: "2026-09-01", confidence: 0.5 }),
  ],
  readings: [
    { sensor_id: "pattern-2", start: iso(at(2, "08:05")), end: iso(at(2, "08:10")), value: null },
    { sensor_id: "pattern-7", start: iso(at(2, "18:00")), end: null, value: null },
  ],
});

const page = (t: number, day: number) => render(<Timeline rec={rec} state={stateAt(rec, t)} playback={playback({ t })} day={day} />);

test("sits under the Home tab and names the day, with the playback pill", () => {
  page(at(2, "20:00"), 2);
  expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { level: 1, name: "Today, Wed 2 Sep" })).toBeInTheDocument();
  expect(screen.getByText("Alfred recognized 1 habit and noticed 1 more.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Simulated · day 2/ })).toBeInTheDocument();
});

test("an earlier day is named by its date and can step to the day after", () => {
  page(at(3, "12:00"), 2);
  expect(screen.getByRole("heading", { level: 1, name: "Wed 2 Sep, day 2" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Earlier" })).toHaveAttribute("href", "#/timeline/1");
  expect(screen.getByRole("link", { name: "Later" })).toHaveAttribute("href", "#/timeline");
});

test("today has no later day and day 1 has no earlier day", () => {
  page(at(1, "12:00"), 1);
  expect(screen.queryByRole("link", { name: "Later" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Earlier" })).toBeNull();
});

test("the lanes chart describes the day in words", () => {
  page(at(2, "20:00"), 2);
  expect(screen.getByRole("img", { name: "Light: not on. Contact sensor: opened 1 time. Plug: in use 1 time. Alfred recognized 1 habit." })).toBeInTheDocument();
});

test("each habit is listed with its time, a link to the sensor and the changes behind it", () => {
  page(at(2, "20:00"), 2);
  const list = screen.getByRole("list", { name: "What Alfred read" });
  const items = within(list).getAllByRole("listitem").filter((li) => li.parentElement === list);
  expect(items).toHaveLength(2);
  const tea = items[0] as HTMLElement;
  expect(tea.querySelector(":scope > time")?.textContent).toBe("08:05");
  expect(within(tea).getByRole("link", { name: "Pattern 2" })).toHaveAttribute("href", "#/sensor/pattern-2");
  expect(within(tea).getByText("lamp on, about 43 min, then plug about 2,100 W, about 3 min")).toBeInTheDocument();
  expect(within(tea).getByRole("list", { name: "Signals" }).textContent).toBe("08:05Contact sensoropen08:06Contact sensorclosed08:07Plug2,140 W, in use08:10Plug0 W");
  const weak = items[1] as HTMLElement;
  expect(within(weak).getByText("Noticed, not a sensor yet.")).toBeInTheDocument();
});

test("a day with nothing recognized says so", () => {
  page(at(2, "08:00"), 2);
  expect(screen.getByText("Nothing recognized yet on this day.")).toBeInTheDocument();
  expect(screen.getByText("Alfred hasn't recognized anything yet.")).toBeInTheDocument();
});
