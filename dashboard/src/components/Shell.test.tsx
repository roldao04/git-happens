import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { playback, state } from "../test/fixtures";
import { Shell } from "./Shell";

test("a simulated recording gets the playback pill", () => {
  render(<Shell state={state()} playback={playback()} tab="home" simulated><p>page</p></Shell>);
  expect(screen.getByRole("button", { name: "Simulated · day 9 · 08:12" })).toBeInTheDocument();
});

test("a live recording has no playback controls at all", () => {
  render(<Shell state={state()} playback={playback()} tab="home" simulated={false}><p>page</p></Shell>);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByText(/Simulated/)).toBeNull();
});

test("the page content goes in main", () => {
  render(<Shell state={state()} playback={playback()} tab="home" simulated><p>page</p></Shell>);
  expect(within(screen.getByRole("main")).getByText("page")).toBeInTheDocument();
});
