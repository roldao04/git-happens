import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { sensor, sensorNow } from "../test/fixtures";
import { SensorTable } from "./SensorTable";

const r = (start: string) => ({ sensor_id: "pattern-2", start, end: null, value: null });

test("one row per sensor with its name, when it usually happens and the day bar, linking to its page", () => {
  render(<SensorTable items={[sensorNow({ readings: [r("2026-09-07T07:51:00")] })]} label="Learned sensors" />);
  const row = screen.getByRole("link", { name: /Pattern 2/ });
  expect(row).toHaveAttribute("href", "#/sensor/pattern-2");
  expect(row).toHaveTextContent("around 07:59");
  expect(screen.getByRole("img", { name: "usually between 07:39 and 08:19" }).querySelectorAll(".p")).toHaveLength(1);
});

test("the detail stays on the sensor's page: no description, days seen or confidence in the row", () => {
  render(<SensorTable items={[sensorNow()]} label="Learned sensors" />);
  const row = screen.getByRole("link");
  expect(row.textContent).not.toMatch(/%|days|give or take|lamp on/);
  expect(document.querySelector(".lt-head")).toBeNull();
});

test("a sensor learned today has a dot and says so", () => {
  render(<SensorTable items={[sensorNow({ justLearned: true })]} label="Learned sensors" />);
  expect(screen.getByText("Learned today")).toBeInTheDocument();
  expect(document.querySelectorAll(".new-dot")).toHaveLength(1);
});

test("a fading sensor says so", () => {
  render(<SensorTable items={[sensorNow({ sensor: sensor({ status: "fading" }) })]} label="Learned sensors" />);
  expect(screen.getByText("fading")).toHaveClass("al-pill");
});

test("a named sensor shows its name", () => {
  render(<SensorTable items={[sensorNow({ sensor: sensor({ name: "Morning tea" }) })]} label="Learned sensors" />);
  expect(screen.getByText("Morning tea")).not.toHaveClass("unnamed");
});
