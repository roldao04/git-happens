import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { DayBar, windowPieces } from "./DayBar";

test("names the usual window in words and marks each time seen", () => {
  render(<DayBar usual="07:59" spreadMin={20} hits={[471, 480]} />);
  const bar = screen.getByRole("img", { name: "usually between 07:39 and 08:19" });
  expect(bar).toHaveClass("ts");
  expect(bar.querySelectorAll(".p")).toHaveLength(2);
  expect(bar.querySelectorAll(".w")).toHaveLength(1);
  expect(bar.querySelectorAll(".h")).toHaveLength(3);
});

test("without a spread it says around and draws no window", () => {
  render(<DayBar usual="07:59" hits={[]} />);
  expect(screen.getByRole("img", { name: "usually around 07:59" }).querySelectorAll(".w")).toHaveLength(0);
});

test("a window across midnight is drawn in two pieces", () => {
  expect(windowPieces(1430, 30)).toEqual([
    { left: (1400 / 1440) * 100, width: (40 / 1440) * 100 },
    { left: 0, width: (20 / 1440) * 100 },
  ]);
  expect(windowPieces(600, 30)).toEqual([{ left: (570 / 1440) * 100, width: (60 / 1440) * 100 }]);
});

test("a midnight-crossing window wraps its words too", () => {
  render(<DayBar usual="23:50" spreadMin={30} hits={[]} />);
  expect(screen.getByRole("img", { name: "usually between 23:20 and 00:20" }).querySelectorAll(".w")).toHaveLength(2);
});
