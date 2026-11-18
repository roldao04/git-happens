import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { sensor, sensorNow } from "../test/fixtures";
import { AlsoNoticed } from "./AlsoNoticed";

const items = [5, 7, 8, 9].map((n) => sensorNow({ sensor: sensor({ id: `pattern-${n}`, confidence: 0.44 }) }));

test("folds the weaker sensors under a count", () => {
  const { container } = render(<AlsoNoticed items={items} />);
  expect(container.querySelector("details")).not.toHaveAttribute("open");
  expect(screen.getByText("Also noticed (4)").tagName).toBe("SUMMARY");
  expect(screen.getByText("Pattern 7")).toBeInTheDocument();
  expect(screen.getAllByText("44%")).toHaveLength(4);
});

test("each one links to its page", () => {
  render(<AlsoNoticed items={items} />);
  expect(screen.getByRole("link", { name: /Pattern 5/ })).toHaveAttribute("href", "#/sensor/pattern-5");
});

test("shows nothing when there is nothing to fold", () => {
  const { container } = render(<AlsoNoticed items={[]} />);
  expect(container).toBeEmptyDOMElement();
});

test("a fading sensor says so", () => {
  render(<AlsoNoticed items={[sensorNow({ sensor: sensor({ status: "fading", confidence: 0.4 }) })]} />);
  expect(screen.getByText("fading")).toHaveClass("al-pill");
});
