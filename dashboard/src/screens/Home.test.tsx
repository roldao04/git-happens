import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { playback, recording, sensorNow, state } from "../test/fixtures";
import { Home } from "./Home";

const door = (value: string) => ({ device: { id: "d", type: "contactSensor", name: "Front door" }, values: { sensorState: value }, changedAt: null, since: null });

test("is the Home tab with the headline, the summary and the playback pill", () => {
  render(<Home rec={recording()} state={state({ devices: [door("normal")] })} playback={playback()} />);
  expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { level: 1, name: "All quiet at home." })).toBeInTheDocument();
  expect(screen.getByText("The door is closed. Nothing new was learned today.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Simulated · day 9/ })).toBeInTheDocument();
});

test("the headline follows the home", () => {
  render(<Home rec={recording()} state={state({ devices: [door("alarm")] })} playback={playback()} />);
  expect(screen.getByRole("heading", { level: 1, name: "The door is open." })).toBeInTheDocument();
});

test("the summary counts what was learned today", () => {
  render(<Home rec={recording()} state={state({ cards: [sensorNow({ justLearned: true })] })} playback={playback()} />);
  expect(screen.getByText(/Alfred learned 1 new sensor today\./)).toBeInTheDocument();
});

test("links to the whole day on the timeline", () => {
  render(<Home rec={recording()} state={state()} playback={playback()} />);
  expect(screen.getByRole("link", { name: "See the whole day" })).toHaveAttribute("href", "#/timeline");
});

test("draws the home from the devices, with a note from Alfred", () => {
  render(<Home rec={recording()} state={state({ devices: [door("alarm")], cards: [sensorNow()] })} playback={playback()} />);
  expect(screen.getByRole("img", { name: "The home: the door is open." })).toBeInTheDocument();
  const note = screen.getByText(/I know 1 of your habits now\./);
  expect(note.closest("figure")).toHaveTextContent("Alfred");
});

test("keeps the page to the headline: no feed, no newest sensors, no device list, no suggestion card", () => {
  render(<Home rec={recording()} state={state({ cards: [sensorNow()], devices: [door("normal")] })} playback={playback()} />);
  expect(screen.getAllByRole("heading").map((h) => h.textContent)).toEqual(["All quiet at home."]);
  expect(screen.queryByRole("link", { name: /Pattern 2/ })).toBeNull();
  expect(document.querySelector(".later")).toBeNull();
});

const hub = { device: { id: "g", type: "gateway", name: "Security hub" }, values: { batteryLevel: 20, cellularConnectionState: "abnormal" }, changedAt: null, since: null };
const smoke = { device: { id: "s", type: "smokeSensor", name: "Smoke sensor" }, values: { signalStrength: "low" }, changedAt: null, since: null };

test("says which devices need a look and links to them", () => {
  render(<Home rec={recording()} state={state({ devices: [door("normal"), hub, smoke] })} playback={playback()} />);
  const link = screen.getByRole("link", { name: /2 devices need a look/ });
  expect(link).toHaveAttribute("href", "#/devices");
  expect(link).toHaveTextContent("Security hub: battery 20%, no mobile signal. Smoke sensor: weak signal.");
});

test("one device that needs a look is said in the singular", () => {
  render(<Home rec={recording()} state={state({ devices: [hub] })} playback={playback()} />);
  expect(screen.getByRole("link", { name: /1 device needs a look/ })).toBeInTheDocument();
});

test("there is no needs-a-look card when every device is fine", () => {
  render(<Home rec={recording()} state={state({ devices: [door("normal")] })} playback={playback()} />);
  expect(screen.queryByRole("link", { name: /need/ })).toBeNull();
});

test("a lamp with no signal is not called on, and needs a look", () => {
  const lamp = { device: { id: "l", type: "bulb", name: "Lamp" }, values: { reachable: false }, changedAt: null, since: null };
  render(<Home rec={recording()} state={state({ devices: [lamp] })} playback={playback()} />);
  expect(screen.getByRole("heading", { level: 1, name: "All quiet at home." })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /1 device needs a look/ })).toHaveTextContent("Lamp: no signal.");
});
