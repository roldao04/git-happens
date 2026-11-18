import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { playback, recording, sensorNow, state } from "../test/fixtures";
import { Home } from "./Home";

const door = (value: string) => ({ device: { id: "d", type: "contactSensor", name: "Front door" }, values: { sensorState: value }, changedAt: null, since: null });

test("has the top bar on the Home tab, the headline and the day slider", () => {
  render(<Home rec={recording()} state={state({ devices: [door("normal")] })} playback={playback()} />);
  expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { level: 1, name: "All quiet at home." })).toBeInTheDocument();
  expect(screen.getByText("The door is closed. Nothing new was learned today.")).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: "Simulated day" })).toBeInTheDocument();
});

test("the headline follows the home", () => {
  render(<Home rec={recording()} state={state({ devices: [door("alarm")] })} playback={playback()} />);
  expect(screen.getByRole("heading", { level: 1, name: "The door is open." })).toBeInTheDocument();
});

test("the summary counts what was learned today", () => {
  render(<Home rec={recording()} state={state({ cards: [sensorNow({ justLearned: true })] })} playback={playback()} />);
  expect(screen.getByText(/Alfred learned 1 new sensor today\./)).toBeInTheDocument();
});

test("has the suggestion as coming later, today so far, new from Alfred and the devices", () => {
  render(<Home rec={recording()} state={state({ cards: [sensorNow()] })} playback={playback()} />);
  expect(screen.getByRole("heading", { name: "Alfred will suggest, then ask" }).closest(".later")).not.toBeNull();
  expect(screen.getByRole("heading", { name: "Today so far" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "New from Alfred" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Pattern 2/ })).toHaveAttribute("href", "#/sensor/pattern-2");
  expect(screen.getByRole("heading", { name: "Devices" })).toBeInTheDocument();
});

test("weak sensors are not listed as new", () => {
  render(<Home rec={recording()} state={state({ alsoNoticed: [sensorNow()] })} playback={playback()} />);
  expect(screen.getByText("Nothing learned yet. Alfred is still watching.")).toBeInTheDocument();
});
