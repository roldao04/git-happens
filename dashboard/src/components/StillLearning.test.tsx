import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { StillLearning } from "./StillLearning";

test("counts the days so far against the run", () => {
  render(<StillLearning day={3} days={21} />);
  expect(screen.getByRole("heading", { name: "Still learning" })).toBeInTheDocument();
  const counter = screen.getByRole("img", { name: "day 3 of 21" });
  expect(counter.querySelectorAll("i")).toHaveLength(21);
  expect(counter.querySelectorAll("i.done")).toHaveLength(3);
  expect(screen.getByText("day 3 of 21")).toBeInTheDocument();
});
