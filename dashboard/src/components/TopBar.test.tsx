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
