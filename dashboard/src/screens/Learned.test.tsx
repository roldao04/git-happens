import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { playback, recording, sensor, sensorNow, state } from "../test/fixtures";
import { Learned } from "./Learned";

const weak = sensorNow({ sensor: sensor({ id: "pattern-5", confidence: 0.44 }) });

test("has the top bar on the Learned tab, the headline, the summary and the honesty line", () => {
  render(<Learned rec={recording()} state={state({ cards: [sensorNow({ justLearned: true })], alsoNoticed: [weak] })} playback={playback()} />);
  expect(screen.getByRole("link", { name: "Learned" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { level: 1, name: "Alfred has learned 1 sensor." })).toBeInTheDocument();
  expect(screen.getByText("1 is new today. 1 more is too weak to show yet.")).toBeInTheDocument();
  expect(screen.getByText("The rosa home is simulated; the learning is real.")).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: "Simulated day" })).toBeInTheDocument();
});

test("connected life lists the strong sensors and still noticing the weak ones", () => {
  render(<Learned rec={recording()} state={state({ cards: [sensorNow()], alsoNoticed: [weak] })} playback={playback()} />);
  const life = screen.getByRole("heading", { name: "Connected life" }).closest("section");
  const still = screen.getByRole("heading", { name: "Still noticing" }).closest("section");
  expect(life?.querySelector('a[href="#/sensor/pattern-2"]')).not.toBeNull();
  expect(still?.querySelector('a[href="#/sensor/pattern-5"]')).not.toBeNull();
  expect(screen.getByText(/The bar in each row is one day/)).toBeInTheDocument();
});

test("connectivity sensors go in their own section, or a line says there are none", () => {
  const conn = sensorNow({ sensor: sensor({ id: "pattern-9", focus: "connectivity" }) });
  const { unmount } = render(<Learned rec={recording()} state={state({ cards: [sensorNow(), conn] })} playback={playback()} />);
  const section = screen.getByRole("heading", { name: "Connectivity" }).closest("section");
  expect(section?.querySelector('a[href="#/sensor/pattern-9"]')).not.toBeNull();
  expect(screen.getByRole("heading", { name: "Connected life" }).closest("section")?.querySelector('a[href="#/sensor/pattern-9"]')).toBeNull();
  unmount();
  render(<Learned rec={recording()} state={state({ cards: [sensorNow()] })} playback={playback()} />);
  expect(screen.getByText("No connectivity sensor yet. Alfred has not found a pattern in how the devices drop offline.")).toBeInTheDocument();
});

test("before the first sensor it says it is still learning", () => {
  render(<Learned rec={recording()} state={state({ day: 3 })} playback={playback()} />);
  expect(screen.getByRole("heading", { name: "Still learning" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Connected life" })).not.toBeInTheDocument();
});

test("no still-noticing section when there are no weak sensors", () => {
  render(<Learned rec={recording()} state={state({ cards: [sensorNow()] })} playback={playback()} />);
  expect(screen.queryByRole("heading", { name: "Still noticing" })).not.toBeInTheDocument();
});

test("coming later shows naming and questions and the two planned learners", () => {
  render(<Learned rec={recording()} state={state({ cards: [sensorNow()] })} playback={playback()} />);
  for (const name of ["Names and questions", "Dinner with friends", "Kettle getting slower"])
    expect(screen.getByRole("heading", { name }).closest(".later")).not.toBeNull();
});
