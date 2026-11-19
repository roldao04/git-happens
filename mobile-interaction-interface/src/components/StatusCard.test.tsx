import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { MockHomeApi } from "../api/mock/MockHomeApi";
import { renderWithHome } from "../test/render";
import { StatusCard } from "./StatusCard";

test("closed, it says what the home is doing right now and what changed", async () => {
  renderWithHome(<StatusCard />);
  const card = await screen.findByRole("button", { name: /Right now · since 08:45/ });
  expect(card).toHaveTextContent("Out for the day");
  expect(card).toHaveTextContent("Light −30% · Kettle off · Heating −3 °C");
  expect(card).toHaveTextContent("Next 18:32");
  expect(card).toHaveAttribute("aria-expanded", "false");
});

test("opened, it shows why and what's planned, and Skip skips it", async () => {
  const api = new MockHomeApi("demo", 0);
  const skip = vi.spyOn(api, "skip");
  const onEvent = vi.fn();
  renderWithHome(<StatusCard onEvent={onEvent} />, api);
  await userEvent.click(await screen.findByRole("button", { name: /Right now/ }));
  expect(screen.getByText(/The front door closed at 08:41/)).toBeInTheDocument();
  const evening = screen.getByText("Evening").closest("li")!;
  await userEvent.click(within(evening).getByRole("button", { name: "Skip Evening" }));
  expect(skip).toHaveBeenCalledWith("decision-21");
  expect(await within(screen.getByText("Evening").closest("li")!).findByText("skipped")).toBeInTheDocument();
  expect(onEvent).toHaveBeenCalledWith("Evening skipped");
});

test("it has no switches: the card is for looking", async () => {
  renderWithHome(<StatusCard />);
  await userEvent.click(await screen.findByRole("button", { name: /Right now/ }));
  expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /undo/i })).not.toBeInTheDocument();
});

test("a device silent since an earlier day says which day, not just the time", async () => {
  const { whenSeen } = await import("../words");
  expect(whenSeen("2026-11-18T17:57:58", "2026-11-19")).toBe("18 Nov, 17:57");
  expect(whenSeen("2026-11-19T08:13:03", "2026-11-19")).toBe("08:13");
  expect(whenSeen(null, "2026-11-19")).toBe("never");
});
