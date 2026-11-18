import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { FoundSteps } from "./FoundSteps";

test("numbers each step with its title and text", () => {
  render(<FoundSteps steps={[{ title: "First", text: "lamp on" }, { title: "Habit", text: "This repeats around 07:59." }]} />);
  expect(screen.getByRole("heading", { name: "How Alfred found it" })).toBeInTheDocument();
  expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["1Firstlamp on", "2HabitThis repeats around 07:59."]);
});
