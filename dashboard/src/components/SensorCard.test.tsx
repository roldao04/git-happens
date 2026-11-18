import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { sensor, sensorNow } from "../test/fixtures";
import { SensorCard } from "./SensorCard";

test("shows an unnamed sensor's card from its pattern", () => {
  render(<SensorCard item={sensorNow()} atEnd={false} />);
  expect(screen.getByRole("heading", { name: "Pattern 2" })).toHaveClass("unnamed");
  expect(screen.getByText("around 07:59")).toBeInTheDocument();
  expect(screen.getByText("± 20 min")).toBeInTheDocument();
  expect(screen.getByText("lamp on, about 43 min, then plug about 2,100 W, about 3 min")).toBeInTheDocument();
  expect(screen.getByText("seen on 7 days so far")).toBeInTheDocument();
  expect(screen.getByText("84%")).toBeInTheDocument();
  expect(screen.queryByText(/looks like/i)).not.toBeInTheDocument();
});

test("at the end it shows the run's days seen", () => {
  render(<SensorCard item={sensorNow()} atEnd />);
  expect(screen.getByText("16 of 19 days")).toBeInTheDocument();
  expect(screen.queryByText(/so far/)).not.toBeInTheDocument();
});

test("one day reads in the singular", () => {
  render(<SensorCard item={sensorNow({ daysSoFar: 1 })} atEnd={false} />);
  expect(screen.getByText("seen on 1 day so far")).toBeInTheDocument();
});

test("a card learned today is marked, in text too", () => {
  render(<SensorCard item={sensorNow({ justLearned: true })} atEnd={false} />);
  expect(screen.getByRole("link")).toHaveClass("al-card", "just-learned");
  expect(screen.getByText("Learned today")).toBeInTheDocument();
});

test("the card links to the sensor's page", () => {
  render(<SensorCard item={sensorNow()} atEnd={false} />);
  expect(screen.getByRole("link")).toHaveAttribute("href", "#/sensor/pattern-2");
});

test("a named sensor with an explanation shows both", () => {
  render(<SensorCard item={sensorNow({ sensor: sensor({ name: "Morning tea", explanation: "making tea" }) })} atEnd={false} />);
  expect(screen.getByRole("heading", { name: "Morning tea" })).not.toHaveClass("unnamed");
  expect(screen.getByText("Looks like making tea")).toBeInTheDocument();
});

test("no spread means no ± line", () => {
  const s = sensor();
  render(<SensorCard item={sensorNow({ sensor: { ...s, pattern: { ...s.pattern, spread_min: undefined } } })} atEnd={false} />);
  expect(screen.queryByText(/±/)).not.toBeInTheDocument();
});

test("a fading sensor says so", () => {
  render(<SensorCard item={sensorNow({ sensor: sensor({ status: "fading" }) })} atEnd={false} />);
  expect(screen.getByText("fading")).toHaveClass("al-pill");
});

test("an active sensor has no fading pill", () => {
  render(<SensorCard item={sensorNow()} atEnd={false} />);
  expect(screen.queryByText("fading")).not.toBeInTheDocument();
});

test("draws its day as a bar with the times seen", () => {
  const r = (start: string) => ({ sensor_id: "pattern-2", start, end: null, value: null });
  render(<SensorCard item={sensorNow({ readings: [r("2026-09-07T07:51:00"), r("2026-09-08T08:05:00")] })} atEnd={false} />);
  const bar = screen.getByRole("img", { name: "usually between 07:39 and 08:19" });
  expect(bar.querySelectorAll(".al-hit")).toHaveLength(2);
  expect(bar.querySelectorAll(".al-now")).toHaveLength(0);
});
