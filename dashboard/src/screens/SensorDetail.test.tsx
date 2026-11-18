import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { Reading } from "../data/recording";
import { playback, recording, sensor, sensorNow, state } from "../test/fixtures";
import { SensorDetail } from "./SensorDetail";

const r = (start: string): Reading => ({ sensor_id: "pattern-2", start, end: "2026-09-09T08:30:00", value: null });
const item = sensorNow({ readings: [r("2026-09-07T07:51:00"), r("2026-09-08T08:05:00")] });
const rec = recording({ sensors: [item.sensor] });

test("shows the sensor in full", () => {
  render(<SensorDetail rec={rec} state={state({ cards: [item] })} playback={playback()} id="pattern-2" />);
  expect(screen.getByRole("heading", { level: 2, name: "Pattern 2" })).toBeInTheDocument();
  expect(screen.getByText("lamp on, about 43 min, then plug about 2,100 W, about 3 min")).toBeInTheDocument();
  expect(screen.getByText("84%").closest(".al-conf")).not.toBeNull();
  const chart = screen.getByRole("img", { name: "seen on 2 of the last 9 days, usually between 07:39 and 08:19" });
  expect(chart.querySelectorAll(".al-hit")).toHaveLength(2);
  expect(screen.getByRole("heading", { name: "How Alfred found it" })).toBeInTheDocument();
  expect(screen.getByText("Alfred never turns anything on or off because of this sensor.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Back/ })).toHaveAttribute("href", "#/");
  expect(screen.getByRole("heading", { name: "Naming comes later" }).closest(".al-later")).not.toBeNull();
});

test("lists the recent days, newest first, missed ones included", () => {
  render(<SensorDetail rec={rec} state={state({ cards: [item] })} playback={playback()} id="pattern-2" />);
  const rows = screen.getAllByRole("listitem").filter((li) => li.closest(".recent-days"));
  expect(rows.map((li) => li.textContent).slice(0, 3)).toEqual([
    "Todaynothing yetnot yet",
    "Tue 808:05 to 08:30seen",
    "Mon 707:51 to 08:30seen",
  ]);
});

test("an also-noticed sensor opens too", () => {
  const weak = sensorNow({ sensor: sensor({ id: "pattern-5", confidence: 0.44 }) });
  render(<SensorDetail rec={recording({ sensors: [weak.sensor] })} state={state({ alsoNoticed: [weak] })} playback={playback()} id="pattern-5" />);
  expect(screen.getByRole("heading", { level: 2, name: "Pattern 5" })).toBeInTheDocument();
});

test("an unknown id says so and links back", () => {
  render(<SensorDetail rec={rec} state={state()} playback={playback()} id="pattern-99" />);
  expect(screen.getByText("This sensor isn't in the recording")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Back/ })).toHaveAttribute("href", "#/");
});

test("a sensor not learned yet at this point says so", () => {
  render(<SensorDetail rec={rec} state={state()} playback={playback()} id="pattern-2" />);
  expect(screen.getByText("Alfred hasn't learned this one yet at this point of the run")).toBeInTheDocument();
});

test("at the end it shows the run's days seen", () => {
  render(<SensorDetail rec={rec} state={state({ cards: [item], atEnd: true })} playback={playback()} id="pattern-2" />);
  expect(screen.getByText("seen on 16 of 19 days")).toBeInTheDocument();
});

test("a named sensor shows its name and what it looks like", () => {
  const named = sensorNow({ sensor: sensor({ name: "Morning tea", explanation: "making tea" }), daysSoFar: 1 });
  render(<SensorDetail rec={recording({ sensors: [named.sensor] })} state={state({ cards: [named] })} playback={playback()} id="pattern-2" />);
  expect(screen.getByRole("heading", { level: 2, name: "Morning tea" })).not.toHaveClass("unnamed");
  expect(screen.getByText("Looks like making tea")).toBeInTheDocument();
  expect(screen.getByText("seen on 1 day so far")).toBeInTheDocument();
});

test("a fading sensor says so on its page", () => {
  const fading = sensorNow({ sensor: sensor({ status: "fading" }) });
  render(<SensorDetail rec={recording({ sensors: [fading.sensor] })} state={state({ cards: [fading] })} playback={playback()} id="pattern-2" />);
  expect(screen.getByText("fading")).toHaveClass("al-pill");
});
