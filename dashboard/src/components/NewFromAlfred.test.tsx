import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { sensor, sensorNow } from "../test/fixtures";
import { NewFromAlfred } from "./NewFromAlfred";

const T = Date.UTC(2026, 8, 9, 8, 12);

test("lists the newest learned sensors, each linking to its page", () => {
  const items = [
    sensorNow({ sensor: sensor({ id: "pattern-3", learned_at: "2026-09-08" }), justLearned: true }),
    sensorNow({ sensor: sensor({ id: "pattern-2", learned_at: "2026-09-07" }) }),
    sensorNow({ sensor: sensor({ id: "pattern-1", learned_at: "2026-09-02" }) }),
  ];
  render(<NewFromAlfred items={items} t={T} />);
  expect(screen.getByRole("heading", { name: "New from Alfred" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "All learned" })).toHaveAttribute("href", "#/learned");
  const links = screen.getAllByRole("link").filter((a) => a.getAttribute("href")?.startsWith("#/sensor/"));
  expect(links.map((a) => a.getAttribute("href"))).toEqual(["#/sensor/pattern-3", "#/sensor/pattern-2", "#/sensor/pattern-1"]);
  expect(links[0]?.textContent).toContain("Learned today · around 07:59");
  expect(links[1]?.textContent).toContain("Learned yesterday · around 07:59");
  expect(links[2]?.textContent).toContain("Learned Thu 3 Sep · around 07:59");
});

test("marks the ones learned today with a dot and in words", () => {
  render(<NewFromAlfred items={[sensorNow({ justLearned: true })]} t={T} />);
  expect(document.querySelectorAll(".new-dot")).toHaveLength(1);
});

test("shows at most three", () => {
  const items = ["1", "2", "3", "4"].map((n) => sensorNow({ sensor: sensor({ id: `pattern-${n}` }) }));
  render(<NewFromAlfred items={items} t={T} />);
  expect(screen.getAllByRole("link").filter((a) => a.getAttribute("href")?.startsWith("#/sensor/"))).toHaveLength(3);
});

test("says so before anything is learned", () => {
  render(<NewFromAlfred items={[]} t={T} />);
  expect(screen.getByText("Nothing learned yet. Alfred is still watching.")).toBeInTheDocument();
});
