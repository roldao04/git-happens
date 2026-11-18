import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { playback, state } from "../test/fixtures";
import { Shell } from "./Shell";

test("the bottom bar has its own play button next to the slider, for the phone", async () => {
  const pb = playback();
  render(<Shell state={state()} playback={pb} tab="home"><p>page</p></Shell>);
  const bar = screen.getByRole("contentinfo");
  expect(within(bar).getByRole("slider", { name: "Simulated day" })).toBeInTheDocument();
  await userEvent.click(within(bar).getByRole("button", { name: "Play" }));
  expect(pb.toggle).toHaveBeenCalledTimes(1);
});

test("the bottom play button says Pause while playing", () => {
  render(<Shell state={state()} playback={playback({ playing: true })} tab="home"><p>page</p></Shell>);
  expect(within(screen.getByRole("contentinfo")).getByRole("button", { name: "Pause" })).toBeInTheDocument();
});

test("the page content goes in main", () => {
  render(<Shell state={state()} playback={playback()} tab="home"><p>page</p></Shell>);
  expect(within(screen.getByRole("main")).getByText("page")).toBeInTheDocument();
});
