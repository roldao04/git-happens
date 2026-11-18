import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { playback, recording, sensor, sensorNow, state } from "../test/fixtures";
import { Stage } from "./Stage";

test("before the first sensor it says it is still learning", () => {
  render(<Stage rec={recording()} state={state({ day: 3 })} playback={playback()} />);
  expect(screen.getByRole("heading", { name: "Still learning" })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "day 3 of 21" })).toBeInTheDocument();
});

test("with no cards but some weak sensors, both show", () => {
  const weak = sensorNow({ sensor: sensor({ id: "pattern-5", confidence: 0.44 }) });
  render(<Stage rec={recording()} state={state({ alsoNoticed: [weak] })} playback={playback()} />);
  expect(screen.getByRole("heading", { name: "Still learning" })).toBeInTheDocument();
  expect(screen.getByText("Also noticed (1)")).toBeInTheDocument();
});

test("with cards it shows them and not the still-learning state", () => {
  render(<Stage rec={recording()} state={state({ cards: [sensorNow()] })} playback={playback()} />);
  expect(screen.getByRole("heading", { name: "Pattern 2" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Still learning" })).not.toBeInTheDocument();
});

test("has the home, the learned sensors, the controls and the scrubber", () => {
  render(<Stage rec={recording()} state={state()} playback={playback()} />);
  expect(screen.getByRole("heading", { name: "Home now" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /Learned so far/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: "Simulated day" })).toBeInTheDocument();
});

test("the home column has a headline and today's happenings", () => {
  const devices = [{ device: { id: "d", type: "contactSensor", name: "Front door" }, values: { sensorState: "alarm" }, changedAt: null }];
  render(<Stage rec={recording()} state={state({ devices })} playback={playback()} />);
  expect(screen.getByText("The door is open.")).toHaveClass("headline");
  expect(screen.getByRole("heading", { name: "Today so far" })).toBeInTheDocument();
});

test("the learned column has a headline, a summary and the honesty line", () => {
  const weak = sensorNow({ sensor: sensor({ id: "pattern-5", confidence: 0.44 }) });
  render(<Stage rec={recording()} state={state({ cards: [sensorNow({ justLearned: true })], alsoNoticed: [weak] })} playback={playback()} />);
  expect(screen.getByText("Alfred has learned 1 sensor.")).toHaveClass("headline");
  expect(screen.getByText("1 is new today. 1 more is too weak to show yet.")).toBeInTheDocument();
  expect(screen.getByText("The rosa home is simulated; the learning is real.")).toBeInTheDocument();
});

test("the bars on the cards are explained once", () => {
  render(<Stage rec={recording()} state={state({ cards: [sensorNow()] })} playback={playback()} />);
  expect(screen.getByText(/Each bar is one day, midnight to midnight/)).toBeInTheDocument();
});

test("no bar caption while there are no cards", () => {
  render(<Stage rec={recording()} state={state()} playback={playback()} />);
  expect(screen.queryByText(/Each bar is one day/)).not.toBeInTheDocument();
});
