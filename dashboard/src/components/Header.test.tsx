import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { sensorNow, state } from "../test/fixtures";
import { Header } from "./Header";

test("shows the simulated clock with the day, weekday and time", () => {
  render(<Header state={state()} />);
  expect(screen.getByText("Simulated")).toBeInTheDocument();
  expect(screen.getByText("08:12")).toBeInTheDocument();
  expect(screen.getByText("Wed · day 9 of 21")).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 1, name: "Alfred" })).toBeInTheDocument();
});

test("the pill says still learning before the first card and counts cards after", () => {
  const { rerender } = render(<Header state={state()} />);
  expect(screen.getByText("Still learning")).toHaveClass("al-pill", "learning");
  rerender(<Header state={state({ cards: [sensorNow(), sensorNow({ sensor: { ...sensorNow().sensor, id: "pattern-1" } })] })} />);
  expect(screen.getByText("2 learned")).toHaveClass("al-pill", "learning");
});

test("renders what it is given beside the clock", () => {
  render(<Header state={state()}><button type="button">x</button></Header>);
  expect(screen.getByRole("button", { name: "x" })).toBeInTheDocument();
});
