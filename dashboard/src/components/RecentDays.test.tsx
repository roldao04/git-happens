import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { DayRow } from "../data/days";
import { RecentDays } from "./RecentDays";

const day = (d: number) => Date.UTC(2026, 8, d);
const min = (d: number, h: number, m: number) => day(d) + (h * 60 + m) * 60_000;

test("lists days newest first with their times, missed ones in words", () => {
  const rows: DayRow[] = [
    { dayMs: day(7), label: "Mon 7", today: false, seen: [{ start: min(7, 7, 51), end: min(7, 8, 30) }] },
    { dayMs: day(8), label: "Tue 8", today: false, seen: [] },
    { dayMs: day(9), label: "Wed 9", today: true, seen: [] },
  ];
  render(<RecentDays rows={rows} />);
  expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
    "Todaynothing yetnot yet",
    "Tue 8not seennot seen",
    "Mon 707:51 to 08:30seen",
  ]);
});

test("shows at most seven days", () => {
  const rows: DayRow[] = Array.from({ length: 10 }, (_, i) => ({ dayMs: day(i + 1), label: `d${i + 1}`, today: false, seen: [] }));
  render(<RecentDays rows={rows} />);
  expect(screen.getAllByRole("listitem")).toHaveLength(7);
});

test("several times in one day are all listed", () => {
  const rows: DayRow[] = [{ dayMs: day(7), label: "Mon 7", today: false, seen: [{ start: min(7, 7, 0), end: null }, { start: min(7, 9, 5), end: null }] }];
  render(<RecentDays rows={rows} />);
  expect(screen.getByText("07:00, 09:05")).toBeInTheDocument();
});
