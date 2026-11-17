import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { playback, state } from "../test/fixtures";
import { Scrubber } from "./Scrubber";

test("a slider over the days with a tick on each learning day", () => {
  const { container } = render(<Scrubber state={state({ learnDays: [2, 7, 21] })} onJump={playback().jumpToDay} />);
  const slider = screen.getByRole("slider", { name: "Simulated day" });
  expect(slider).toHaveAttribute("min", "1");
  expect(slider).toHaveAttribute("max", "21");
  expect(slider).toHaveValue("9");
  expect(container.querySelectorAll("datalist option")).toHaveLength(3);
});

test("moving it jumps to that day", () => {
  const p = playback();
  render(<Scrubber state={state()} onJump={p.jumpToDay} />);
  fireEvent.change(screen.getByRole("slider", { name: "Simulated day" }), { target: { value: "14" } });
  expect(p.jumpToDay).toHaveBeenCalledWith(14);
});
