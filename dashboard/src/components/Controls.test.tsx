import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { playback } from "../test/fixtures";
import { Controls } from "./Controls";

test("the play button names what it will do and toggles", async () => {
  const p = playback();
  const { rerender } = render(<Controls playback={p} />);
  await userEvent.click(screen.getByRole("button", { name: "Play" }));
  expect(p.toggle).toHaveBeenCalledOnce();
  rerender(<Controls playback={playback({ playing: true })} />);
  expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
});

test("speed buttons show which one is on and set the speed", async () => {
  const p = playback({ speed: 2 });
  render(<Controls playback={p} />);
  expect(screen.getByRole("button", { name: "1×" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("button", { name: "2×" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(screen.getByRole("button", { name: "4×" }));
  expect(p.setSpeed).toHaveBeenCalledWith(4);
});

test("jump to the end", async () => {
  const p = playback();
  render(<Controls playback={p} />);
  await userEvent.click(screen.getByRole("button", { name: "Jump to the end" }));
  expect(p.jumpToEnd).toHaveBeenCalledOnce();
});
