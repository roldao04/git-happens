import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { Reading } from "../data/recording";
import { DAY9_0812 } from "../test/fixtures";
import { DaysSeen } from "./DaysSeen";

const r = (start: string): Reading => ({ sensor_id: "pattern-2", start, end: null, value: null });

test("marks the last 14 days the sensor was seen on, ending today", () => {
  const { container } = render(
    <DaysSeen readings={[r("2026-09-09T07:51:00"), r("2026-09-08T08:02:00"), r("2026-09-08T09:00:00"), r("2026-08-26T08:00:00"), r("2026-08-27T08:00:00")]} t={DAY9_0812} />,
  );
  const days = container.querySelectorAll(".al-day");
  expect(days).toHaveLength(14);
  expect(container.querySelectorAll(".al-day.seen")).toHaveLength(3);
  expect(days[13]).toHaveClass("seen", "today");
  expect(days[0]).toHaveClass("seen");
  expect(screen.getByRole("img", { name: "seen on 3 of the last 14 days" })).toBeInTheDocument();
});
