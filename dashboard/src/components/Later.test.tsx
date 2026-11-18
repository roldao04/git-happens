import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { Later } from "./Later";

test("a dashed not-built-yet card with a title and a line", () => {
  render(<Later title="Alfred will suggest, then ask">It never acts on its own.</Later>);
  const card = screen.getByRole("heading", { name: "Alfred will suggest, then ask" }).closest(".later");
  expect(card).not.toBeNull();
  expect(screen.getByText("Not built yet")).toHaveClass("al-pill");
  expect(screen.getByText("It never acts on its own.")).toBeInTheDocument();
});
