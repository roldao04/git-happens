import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { DayRow } from "../data/days";
import { DaysChart } from "./DaysChart";

const day = (d: number) => Date.UTC(2026, 8, d);
const rows: DayRow[] = [
  { dayMs: day(7), label: "Mon 7", today: false, seen: [{ start: day(7) + (7 * 60 + 51) * 60_000, end: null }] },
  { dayMs: day(8), label: "Tue 8", today: false, seen: [] },
  { dayMs: day(9), label: "Wed 9", today: true, seen: [{ start: day(9) + (8 * 60 + 5) * 60_000, end: null }] },
];

test("one row per day with a dot per time seen and its first time", () => {
  render(<DaysChart rows={rows} usual="07:59" spreadMin={20} />);
  const chart = screen.getByRole("img", { name: "seen on 2 of the last 3 days, usually between 07:39 and 08:19" });
  expect(chart.querySelectorAll(".chart-row")).toHaveLength(3);
  expect(chart.querySelectorAll(".al-hit")).toHaveLength(2);
  expect(chart.querySelectorAll(".al-window")).toHaveLength(3);
  expect(screen.getByText("07:51")).toBeInTheDocument();
});

test("a missed day says so in text, and today is marked", () => {
  render(<DaysChart rows={rows} usual="07:59" spreadMin={20} />);
  expect(screen.getByText("missed")).toBeInTheDocument();
  const chart = screen.getByRole("img");
  expect(within(chart).getByText("Today").closest(".chart-row")).toHaveClass("today");
  expect(within(chart).queryByText("Wed 9")).not.toBeInTheDocument();
});

test("today with nothing yet says not yet instead of missed", () => {
  render(<DaysChart rows={[{ dayMs: day(9), label: "Wed 9", today: true, seen: [] }]} usual="07:59" spreadMin={20} />);
  expect(screen.getByText("not yet")).toBeInTheDocument();
  expect(screen.queryByText("missed")).not.toBeInTheDocument();
});

test("has a legend for the dots, today and the window", () => {
  render(<DaysChart rows={rows} usual="07:59" spreadMin={20} />);
  const legend = screen.getByText("Usual window, 07:39 to 08:19").closest(".al-legend");
  expect(legend).not.toBeNull();
  expect(legend?.textContent).toContain("Seen");
  expect(legend?.textContent).toContain("Today");
});

test("the axis is labelled around the usual time", () => {
  render(<DaysChart rows={rows} usual="07:59" spreadMin={20} />);
  const ticks = [...document.querySelectorAll(".chart-axis span")].map((s) => s.textContent);
  expect(ticks).toEqual(["06:59", "07:29", "07:59", "08:29", "08:59"]);
});

test("without a spread there is no window and the label says around", () => {
  render(<DaysChart rows={rows} usual="07:59" spreadMin={undefined} />);
  expect(screen.getByRole("img", { name: "seen on 2 of the last 3 days, usually around 07:59" }).querySelectorAll(".al-window")).toHaveLength(0);
});

test("each legend entry has its swatch", () => {
  render(<DaysChart rows={rows} usual="07:59" spreadMin={20} />);
  expect(document.querySelectorAll(".al-legend .swatch")).toHaveLength(3);
  expect(document.querySelector(".al-legend .swatch.ring")).not.toBeNull();
  expect(document.querySelector(".al-legend .swatch.band")).not.toBeNull();
});
