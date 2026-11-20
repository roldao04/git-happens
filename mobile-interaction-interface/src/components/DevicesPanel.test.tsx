import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { DEVICE_IDS } from "../api/mock/houses";
import { MockHomeApi } from "../api/mock/MockHomeApi";
import { renderWithHome } from "../test/render";
import { DevicesPanel } from "./DevicesPanel";

test("the lamp's switch sends exactly a power body, and the result is reported", async () => {
  const api = new MockHomeApi("demo", 0);
  const set = vi.spyOn(api, "setControl");
  const onResult = vi.fn();
  renderWithHome(<DevicesPanel onClose={() => {}} onResult={onResult} />, api);
  await userEvent.click(await screen.findByRole("switch", { name: "Light" }));
  expect(set).toHaveBeenCalledWith(DEVICE_IDS.light, { type: "power", value: "on" });
  expect(onResult).toHaveBeenCalledWith(expect.objectContaining({ status: "sent" }), expect.objectContaining({ name: "Light" }), "turn the light on");
  expect(await screen.findByRole("switch", { name: "Light" })).toHaveAttribute("aria-checked", "true");
});

test("raising the heating while nobody is home is blocked, with the guard's reason", async () => {
  const onResult = vi.fn();
  renderWithHome(<DevicesPanel onClose={() => {}} onResult={onResult} />);
  const up = await screen.findByRole("button", { name: "Raise the heating" });
  await userEvent.click(up); // 17.5
  await userEvent.click(up); // 18
  await userEvent.click(up); // 18.5: blocked
  expect(onResult).toHaveBeenLastCalledWith(expect.objectContaining({ status: "blocked", reason: expect.stringMatching(/no one is home/) }), expect.anything(), expect.anything());
  expect(screen.getByText(/^18\.0/)).toBeInTheDocument();
});

test("the demo house shows its plugs, the thermostat and who is home; the sensors are only read", async () => {
  renderWithHome(<DevicesPanel onClose={() => {}} onResult={() => {}} />);
  for (const name of ["Kettle", "TV", "Washing machine"]) expect(await screen.findByRole("switch", { name })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Heating mode" })).toBeInTheDocument();
  expect(screen.getByText("Nobody")).toBeInTheDocument();
  expect(screen.getByText("battery 20%")).toBeInTheDocument();
  expect(screen.queryByRole("switch", { name: "Alarm" })).not.toBeInTheDocument();
});

test("the kit has one plug and no thermostat", async () => {
  renderWithHome(<DevicesPanel onClose={() => {}} onResult={() => {}} />, new MockHomeApi("kit", 0));
  expect(await screen.findByRole("switch", { name: "Plug" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Raise the heating" })).not.toBeInTheDocument();
  expect(screen.queryByText("Nobody")).not.toBeInTheDocument();
});
