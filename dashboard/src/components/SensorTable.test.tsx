import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { sensor, sensorNow } from "../test/fixtures";
import { SensorTable } from "./SensorTable";

const r = (start: string) => ({ sensor_id: "pattern-2", start, end: null, value: null });

test("one row per sensor with its words, bar, days and confidence, linking to its page", () => {
  render(<SensorTable items={[sensorNow({ readings: [r("2026-09-07T07:51:00")] })]} atEnd={false} />);
  expect(document.querySelector(".lt-head")).toHaveAttribute("aria-hidden", "true");
  expect([...document.querySelectorAll(".lt-head span")].map((h) => h.textContent)).toEqual(["Sensor", "Usual time of day", "Seen", "Confidence"]);
  const row = screen.getByRole("link", { name: /Pattern 2/ });
  expect(row).toHaveAttribute("href", "#/sensor/pattern-2");
  expect(row.textContent).toContain("lamp on, about 43 min, then plug about 2,100 W, about 3 min. Around 07:59, give or take 20 min.");
  expect(row.textContent).toContain("7 daysseen so far");
  expect(row.textContent).toContain("84%");
  expect(screen.getByRole("img", { name: "usually between 07:39 and 08:19" }).querySelectorAll(".p")).toHaveLength(1);
});

test("at the end it shows the run's days seen", () => {
  render(<SensorTable items={[sensorNow()]} atEnd />);
  expect(screen.getByRole("link").textContent).toContain("16 of 19days seen");
});

test("a sensor learned today has a dot and says so", () => {
  render(<SensorTable items={[sensorNow({ justLearned: true })]} atEnd={false} />);
  expect(screen.getByText("Learned today")).toBeInTheDocument();
  expect(document.querySelectorAll(".new-dot")).toHaveLength(1);
});

test("a fading sensor says so", () => {
  render(<SensorTable items={[sensorNow({ sensor: sensor({ status: "fading" }) })]} atEnd={false} />);
  expect(screen.getByText("fading")).toHaveClass("al-pill");
});

test("a named sensor shows its name and what it looks like", () => {
  render(<SensorTable items={[sensorNow({ sensor: sensor({ name: "Morning tea", explanation: "making tea" }) })]} atEnd={false} />);
  expect(screen.getByText("Morning tea")).not.toHaveClass("unnamed");
  expect(screen.getByRole("link").textContent).toContain("Looks like making tea.");
});

test("one day reads in the singular", () => {
  render(<SensorTable items={[sensorNow({ daysSoFar: 1 })]} atEnd={false} />);
  expect(screen.getByRole("link").textContent).toContain("1 dayseen so far");
});
