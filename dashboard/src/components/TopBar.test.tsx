import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { playback, state } from "../test/fixtures";
import { TopBar } from "./TopBar";

test("shows the logo, the simulated clock and the controls", () => {
  render(<TopBar state={state()} playback={playback()} tab="home" />);
  expect(screen.getByRole("link", { name: "Alfred home" })).toHaveAttribute("href", "#/");
  expect(screen.getByText("Simulated")).toBeInTheDocument();
  expect(screen.getByText("08:12")).toBeInTheDocument();
  expect(screen.getByText("Wed · day 9 of 21")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
});

test("has Home and Learned tabs with the current one marked", () => {
  render(<TopBar state={state()} playback={playback()} tab="learned" />);
  const nav = screen.getByRole("navigation", { name: "Main" });
  expect(nav).toContainElement(screen.getByRole("link", { name: "Home" }));
  expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "#/");
  expect(screen.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "Learned" })).toHaveAttribute("href", "#/learned");
  expect(screen.getByRole("link", { name: "Learned" })).toHaveAttribute("aria-current", "page");
});

test("has Timeline and Devices tabs too", () => {
  render(<TopBar state={state()} playback={playback()} tab="devices" />);
  expect(screen.getAllByRole("link").filter((a) => a.closest("nav")).map((a) => a.textContent)).toEqual(["Home", "Learned", "Timeline", "Devices"]);
  expect(screen.getByRole("link", { name: "Timeline" })).toHaveAttribute("href", "#/timeline");
  expect(screen.getByRole("link", { name: "Devices" })).toHaveAttribute("href", "#/devices");
  expect(screen.getByRole("link", { name: "Devices" })).toHaveAttribute("aria-current", "page");
});

test("each tab carries a decorative icon for the phone's bottom bar", () => {
  render(<TopBar state={state()} playback={playback()} tab="home" />);
  for (const name of ["Home", "Learned", "Timeline", "Devices"]) {
    const icon = screen.getByRole("link", { name }).querySelector("svg.tab-ic");
    expect(icon).not.toBeNull();
    expect(icon).toHaveAttribute("aria-hidden", "true");
  }
});
