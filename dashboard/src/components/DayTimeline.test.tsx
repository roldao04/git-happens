import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { DayTimeline, windowPieces } from "./DayTimeline";

test("names the usual window in words", () => {
  render(<DayTimeline usual="07:59" spreadMin={20} hits={[471, 480]} nowMin={492} />);
  const tl = screen.getByRole("img", { name: "usually between 07:39 and 08:19" });
  expect(tl.querySelectorAll(".al-hit")).toHaveLength(2);
  expect(tl.querySelectorAll(".al-window")).toHaveLength(1);
  expect(tl.querySelector(".al-now")).toHaveStyle({ left: `${(492 / 1440) * 100}%` });
});

test("without a spread it says around", () => {
  render(<DayTimeline usual="07:59" hits={[]} nowMin={null} />);
  const tl = screen.getByRole("img", { name: "usually around 07:59" });
  expect(tl.querySelector(".al-now")).toBeNull();
});

test("a window across midnight is drawn in two pieces", () => {
  expect(windowPieces(1430, 30)).toEqual([
    { left: (1400 / 1440) * 100, width: (40 / 1440) * 100 },
    { left: 0, width: (20 / 1440) * 100 },
  ]);
  expect(windowPieces(600, 30)).toEqual([{ left: (570 / 1440) * 100, width: (60 / 1440) * 100 }]);
});

test("a midnight-crossing window wraps its words too", () => {
  render(<DayTimeline usual="23:50" spreadMin={30} hits={[]} nowMin={null} />);
  expect(screen.getByRole("img", { name: "usually between 23:20 and 00:20" }).querySelectorAll(".al-window")).toHaveLength(2);
});
