import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { fireEvent } from "@testing-library/react";
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

test("the hours nobody was watching are shaded and said in words", () => {
  const gapped = { ...rec, gaps: [{ start: iso(at(2, "12:05")), end: iso(at(2, "14:30")) }] };
  render(<Timeline rec={gapped} state={stateAt(gapped, at(2, "20:00"))} playback={playback({ t: at(2, "20:00") })} day={2} />);
  const chart = screen.getByRole("img", { name: /Alfred recognized 1 habit\. Not watched 12:05–14:30\.$/ });
  expect(chart.querySelectorAll(".gap")).toHaveLength(4);
});

test("a colored lamp's bar is drawn in its color", () => {
  const red = recording({
    devices: [{ id: "lamp", type: "bulb", name: "Light" }],
    initial: [{ device_id: "lamp", capability: "workMode", value: "colour" }, { device_id: "lamp", capability: "color_rgb", value: "255,0,0" }],
    events: [ev("lamp", "power", "on", at(2, "08:00")), ev("lamp", "power", "off", at(2, "09:00"))],
  });
  render(<Timeline rec={red} state={stateAt(red, at(2, "20:00"))} playback={playback({ t: at(2, "20:00") })} day={2} />);
  const bar = document.querySelector<HTMLElement>(".lane-r .bar");
  expect(bar?.style.backgroundColor).toBe("rgba(255, 0, 0, 0.45)");
});

afterEach(() => vi.restoreAllMocks());

// the chart is 240px wide from x=100, so 10px is one hour
const sizeChart = () => vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(100, 0, 240, 48));
const chart = () => screen.getByRole("img", { name: /Alfred recognized/ });
const tickTexts = () => Array.from(document.querySelectorAll(".ticks2 span"), (el) => el.textContent);

test("dragging across the chart zooms to those hours, and Whole day goes back", async () => {
  sizeChart();
  page(at(2, "20:00"), 2);
  expect(screen.queryByRole("button", { name: "Whole day" })).toBeNull();
  fireEvent.pointerDown(chart(), { clientX: 215, pointerId: 1 });
  fireEvent.pointerMove(chart(), { clientX: 225, pointerId: 1 });
  expect(document.querySelector(".lanes .sel")).not.toBeNull();
  fireEvent.pointerUp(chart(), { clientX: 225, pointerId: 1 });
  expect(document.querySelector(".lanes .sel")).toBeNull();
  expect(chart()).toHaveAccessibleName(/Showing 11:30–12:30\.$/);
  expect(tickTexts()).toEqual(["11:30", "11:45", "12:00", "12:15", "12:30"]);
  await userEvent.click(screen.getByRole("button", { name: "Whole day" }));
  expect(chart()).not.toHaveAccessibleName(/Showing/);
  expect(tickTexts()).toEqual(["00:00", "06:00", "12:00", "18:00", "24:00"]);
});

test("a tiny drag is a click, not a zoom", () => {
  sizeChart();
  page(at(2, "20:00"), 2);
  fireEvent.pointerDown(chart(), { clientX: 215, pointerId: 1 });
  fireEvent.pointerUp(chart(), { clientX: 215, pointerId: 1 });
  expect(chart()).not.toHaveAccessibleName(/Showing/);
});

test("the From and To boxes zoom too, without a mouse", async () => {
  page(at(2, "20:00"), 2);
  fireEvent.change(screen.getByLabelText("From"), { target: { value: "07:00" } });
  fireEvent.change(screen.getByLabelText("To"), { target: { value: "09:30" } });
  await userEvent.click(screen.getByRole("button", { name: "Zoom" }));
  expect(chart()).toHaveAccessibleName(/Showing 07:00–09:30\.$/);
});

test("another day starts with the whole day again", () => {
  sizeChart();
  const { rerender } = page(at(2, "20:00"), 2);
  fireEvent.pointerDown(chart(), { clientX: 215, pointerId: 1 });
  fireEvent.pointerUp(chart(), { clientX: 225, pointerId: 1 });
  rerender(<Timeline rec={rec} state={stateAt(rec, at(2, "20:00"))} playback={playback({ t: at(2, "20:00") })} day={1} />);
  expect(chart()).not.toHaveAccessibleName(/Showing/);
});

test("a lamp that changes color while on is still on once", () => {
  const colors = recording({
    devices: [{ id: "lamp", type: "bulb", name: "Light" }],
    initial: [{ device_id: "lamp", capability: "workMode", value: "colour" }, { device_id: "lamp", capability: "color_rgb", value: "255,0,0" }],
    events: [ev("lamp", "power", "on", at(2, "08:00")), ev("lamp", "color_rgb", "0,0,255", at(2, "08:20")), ev("lamp", "power", "off", at(2, "09:00"))],
  });
  render(<Timeline rec={colors} state={stateAt(colors, at(2, "20:00"))} playback={playback({ t: at(2, "20:00") })} day={2} />);
  expect(screen.getByRole("img", { name: /^Light: on 1 time\./ })).toBeInTheDocument();
  expect(document.querySelectorAll(".lane-r .bar")).toHaveLength(2);
});

test("a drag keeps following the pointer when it leaves the chart", () => {
  sizeChart();
  const capture = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", { value: capture, configurable: true, writable: true });
  page(at(2, "20:00"), 2);
  fireEvent.pointerDown(chart(), { clientX: 215, pointerId: 7 });
  expect(capture).toHaveBeenCalledWith(7);
  Reflect.deleteProperty(HTMLElement.prototype, "setPointerCapture");
});

const legendItems = () => within(screen.getByRole("list", { name: "Chart legend" })).getAllByRole("listitem");

test("the chart has a legend with a sample of each mark it draws, and a short hint to drag", () => {
  page(at(2, "20:00"), 2);
  const items = legendItems();
  expect(items.map((li) => li.textContent)).toEqual([
    "Lamp on, in its color", "Plug in use", "Door open", "Habit Alfred recognized", "Not a sensor yet", "Not happened yet",
  ]);
  expect(items.every((li) => li.querySelector("[aria-hidden='true']") !== null)).toBe(true);
  expect(screen.queryByText(/^A bar is/)).toBeNull();
  expect(screen.getByText("Drag across the chart to see some hours closer.")).toBeInTheDocument();
});

test("a past day's legend leaves out what has not happened yet", () => {
  page(at(3, "12:00"), 2);
  expect(legendItems().map((li) => li.textContent)).not.toContain("Not happened yet");
});

test("a day with unwatched hours adds them to the legend", () => {
  const gapped = { ...rec, gaps: [{ start: iso(at(2, "12:05")), end: iso(at(2, "14:30")) }] };
  render(<Timeline rec={gapped} state={stateAt(gapped, at(3, "12:00"))} playback={playback()} day={2} />);
  expect(legendItems().map((li) => li.textContent).at(-1)).toBe("Not watched");
});

test("the legend lists only the marks this home's chart can have", () => {
  const lampOnly = recording({ devices: [{ id: "lamp", type: "bulb", name: "Light" }], events: [], sensors: [], readings: [] });
  render(<Timeline rec={lampOnly} state={stateAt(lampOnly, at(2, "20:00"))} playback={playback()} day={2} />);
  expect(legendItems().map((li) => li.textContent)).toEqual(["Lamp on, in its color", "Not happened yet"]);
});

test("the door is a bar from when it opened to when it closed", () => {
  page(at(2, "20:00"), 2);
  const lane = screen.getByText("Contact sensor", { selector: ".lane-l" }).closest(".lane") as HTMLElement;
  const bars = lane.querySelectorAll<HTMLElement>(".bar.door");
  expect(bars).toHaveLength(1);
  expect(lane.querySelector(".tick")).toBeNull();
  const bar = bars[0] as HTMLElement;
  expect(parseFloat(bar.style.left)).toBeCloseTo(((8 * 60 + 5) / 1440) * 100, 3);
  expect(parseFloat(bar.style.width)).toBeCloseTo((1 / 1440) * 100, 3);
});
