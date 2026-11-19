import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { playback, recording, sensor, sensorNow, state } from "../test/fixtures";
import { Learned } from "./Learned";

const weak = sensorNow({ sensor: sensor({ id: "pattern-5", confidence: 0.44 }) });

test("is the Learned tab with the headline and the summary", () => {
  render(<Learned rec={recording()} state={state({ cards: [sensorNow({ justLearned: true })], alsoNoticed: [weak] })} playback={playback()} />);
  expect(screen.getByRole("link", { name: "Learned" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { level: 1, name: "Alfred has learned 1 sensor." })).toBeInTheDocument();
  expect(screen.getByText("1 is new today. 1 more is too weak to show yet.")).toBeInTheDocument();
});

test("leaves saying the home is simulated to the playback pill", () => {
  render(<Learned rec={recording()} state={state({ cards: [sensorNow()] })} playback={playback()} />);
  expect(screen.queryByText(/is simulated/)).toBeNull();
  expect(screen.getByRole("button", { name: /^Simulated · day/ })).toBeInTheDocument();
});

test("all learned sensors are one list, connectivity included", () => {
  const conn = sensorNow({ sensor: sensor({ id: "pattern-9", focus: "connectivity" }) });
  render(<Learned rec={recording()} state={state({ cards: [sensorNow(), conn] })} playback={playback()} />);
  const list = screen.getByRole("list", { name: "Learned sensors" });
  expect([...list.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual(["#/sensor/pattern-2", "#/sensor/pattern-9"]);
  expect(screen.queryByRole("heading", { name: "Connectivity" })).toBeNull();
});

test("the weak sensors are folded away under a count", () => {
  render(<Learned rec={recording()} state={state({ cards: [sensorNow()], alsoNoticed: [weak] })} playback={playback()} />);
  const fold = screen.getByText("1 more still being watched").closest("details");
  expect(fold).not.toBeNull();
  expect(fold).not.toHaveAttribute("open");
  expect(fold?.querySelector('a[href="#/sensor/pattern-5"]')).not.toBeNull();
});

test("no fold when there are no weak sensors", () => {
  render(<Learned rec={recording()} state={state({ cards: [sensorNow()] })} playback={playback()} />);
  expect(document.querySelector("details")).toBeNull();
});

test("before the first sensor it says it is still learning", () => {
  render(<Learned rec={recording()} state={state({ day: 3 })} playback={playback()} />);
  expect(screen.getByRole("heading", { name: "Still learning" })).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Learned sensors" })).toBeNull();
});

test("the coming-later cards are gone from the list page", () => {
  render(<Learned rec={recording()} state={state({ cards: [sensorNow()] })} playback={playback()} />);
  expect(document.querySelector(".later")).toBeNull();
});
