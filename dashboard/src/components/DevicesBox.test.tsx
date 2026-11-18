import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { DeviceNow } from "../data/stateAt";
import { DAY9_0812 } from "../test/fixtures";
import { DevicesBox } from "./DevicesBox";

function dev(type: string, name: string, values: DeviceNow["values"], since: number | null = null, changedAt: number | null = null): DeviceNow {
  return { device: { id: `${type}-1`, type, name }, values, changedAt, since };
}
const at = (h: number, m: number) => Date.UTC(2026, 8, 9, h, m);

test("lists each device with its value and the line under its name", () => {
  render(
    <DevicesBox
      devices={[
        dev("contactSensor", "Front door", { sensorState: "normal" }, at(8, 41)),
        dev("temperatureSensor", "Climate", { temperatureValue: 23.1, humidityValue: 62.9 }),
      ]}
    />,
  );
  expect(screen.getByRole("heading", { name: "Devices" })).toBeInTheDocument();
  expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual([
    "Front doorclosed since 08:41Closed",
    "Climate62.9% humidity23.1 °C",
  ]);
});

test("devices with nothing to show are left out", () => {
  render(<DevicesBox devices={[dev("gateway", "Security hub", { tamperState: "normal" })]} />);
  expect(screen.queryByText("Security hub")).not.toBeInTheDocument();
});

test("a device that just changed is marked, and says when in text", () => {
  render(<DevicesBox devices={[dev("plug", "Plug", { instantPower: 2105 }, DAY9_0812, DAY9_0812)]} />);
  const row = screen.getByText("Plug").closest("li");
  expect(row).toHaveClass("just-changed");
  expect(row?.textContent).toContain("in use since 08:12");
});

test("the latest thing that happened is held in a line of its own", () => {
  render(<DevicesBox devices={[]} latest={{ device: { id: "p", type: "plug", name: "Plug" }, capability: "instantPower", value: 2105, at: DAY9_0812 }} />);
  expect(screen.getByText("Latest · 08:12")).toBeInTheDocument();
  expect(screen.getByText("Plug: 2,105 W, in use")).toBeInTheDocument();
});
