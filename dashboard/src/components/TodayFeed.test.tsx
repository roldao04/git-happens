import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { FeedItem } from "../data/stateAt";
import { sensor } from "../test/fixtures";
import { TodayFeed } from "./TodayFeed";

const at = (h: number, m: number) => Date.UTC(2026, 8, 9, h, m);
const door = { id: "d", type: "contactSensor", name: "Front door" };
const plug = { id: "p", type: "plug", name: "Plug" };

test("lists today's happenings in words, newest first as given", () => {
  const items: FeedItem[] = [
    { kind: "device", at: at(8, 10), device: door, capability: "sensorState", value: "alarm" },
    { kind: "habit", at: at(7, 59), sensor: sensor() },
    { kind: "device", at: at(7, 59), device: plug, capability: "instantPower", value: 2105 },
  ];
  render(<TodayFeed items={items} />);
  expect(screen.getByRole("heading", { name: "Today so far" })).toBeInTheDocument();
  const rows = screen.getAllByRole("listitem").map((li) => li.textContent);
  expect(rows).toEqual(["08:10Front door open", "07:59Pattern 2 seen againlearned habit", "07:59Plug 2,105 W, in use"]);
});

test("shows only the newest six", () => {
  const items: FeedItem[] = Array.from({ length: 8 }, (_, i) => ({ kind: "device", at: at(9, 8 - i), device: plug, capability: "instantPower", value: 100 }));
  render(<TodayFeed items={items} />);
  expect(screen.getAllByRole("listitem")).toHaveLength(6);
  expect(screen.getByText("and 2 earlier")).toBeInTheDocument();
});

test("says so when nothing has happened yet today", () => {
  render(<TodayFeed items={[]} />);
  expect(screen.getByText("Nothing yet today.")).toBeInTheDocument();
});
