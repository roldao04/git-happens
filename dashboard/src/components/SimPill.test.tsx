import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { playback, state } from "../test/fixtures";
import { SimPill } from "./SimPill";

const pill = () => screen.getByRole("button", { name: "Simulated · day 9 · 08:12" });

test("closed, it shows the simulated day and time with a play button and nothing else", () => {
  render(<SimPill state={state()} playback={playback()} />);
  expect(pill()).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  expect(screen.queryByRole("slider")).toBeNull();
  expect(screen.queryByRole("group", { name: "Speed" })).toBeNull();
});

test("the play button toggles playback and says Pause while playing", async () => {
  const p = playback();
  const { rerender } = render(<SimPill state={state()} playback={p} />);
  await userEvent.click(screen.getByRole("button", { name: "Play" }));
  expect(p.toggle).toHaveBeenCalledOnce();
  rerender(<SimPill state={state()} playback={playback({ playing: true })} />);
  expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
});

test("opening it shows the speeds, jump to the end and the day slider, and it closes again", async () => {
  render(<SimPill state={state()} playback={playback()} />);
  await userEvent.click(pill());
  expect(pill()).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("slider", { name: "Simulated day" })).toHaveValue("9");
  expect(screen.getByRole("group", { name: "Speed" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Jump to the end" })).toBeInTheDocument();
  await userEvent.click(pill());
  expect(screen.queryByRole("slider")).toBeNull();
});

test("speed buttons show which one is on and set the speed", async () => {
  const p = playback({ speed: 2 });
  render(<SimPill state={state()} playback={p} />);
  await userEvent.click(pill());
  expect(screen.getByRole("button", { name: "1×" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("button", { name: "2×" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(screen.getByRole("button", { name: "4×" }));
  expect(p.setSpeed).toHaveBeenCalledWith(4);
});

test("jump to the end", async () => {
  const p = playback();
  render(<SimPill state={state()} playback={p} />);
  await userEvent.click(pill());
  await userEvent.click(screen.getByRole("button", { name: "Jump to the end" }));
  expect(p.jumpToEnd).toHaveBeenCalledOnce();
});
