import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { DeviceNow } from "../data/stateAt";
import { DAY9_0812 } from "../test/fixtures";
import { DeviceList } from "./DeviceList";

function dev(type: string, name: string, values: DeviceNow["values"], changedAt: number | null = null): DeviceNow {
  return { device: { id: `${type}-1`, type, name }, values, changedAt };
}

test("shows each device's name and its value in words", () => {
  render(
    <DeviceList
      devices={[
        dev("contactSensor", "Front door", { sensorState: "normal" }),
        dev("bulb", "Lamp", { power: true, brightness: 100 }),
        dev("plug", "Plug", { instantPower: 2105 }),
        dev("temperatureSensor", "Temperature", { temperatureValue: 21.43, humidityValue: 48 }),
      ]}
    />,
  );
  expect(screen.getByText("Front door")).toBeInTheDocument();
  expect(screen.getByText("closed")).toBeInTheDocument();
  expect(screen.getByText("on, 100%")).toBeInTheDocument();
  expect(screen.getByText("2,105 W, in use")).toBeInTheDocument();
  expect(screen.getByText("21.4 °C, 48.0%")).toBeInTheDocument();
});

test("a lamp that is off does not show its brightness", () => {
  render(<DeviceList devices={[dev("bulb", "Lamp", { power: false, brightness: 100 })]} />);
  expect(screen.getByText("off")).toBeInTheDocument();
});

test("a device that just changed says when, in text", () => {
  render(<DeviceList devices={[dev("plug", "Plug", { instantPower: 0 }, DAY9_0812)]} />);
  expect(screen.getByText("changed 08:12")).toBeInTheDocument();
});

test("devices with nothing to show in words are left out", () => {
  render(<DeviceList devices={[dev("gateway", "Security hub", { tamperState: "normal" }), dev("plug", "Plug", {})]} />);
  expect(screen.queryByText("Security hub")).not.toBeInTheDocument();
  expect(screen.queryByText("Plug")).not.toBeInTheDocument();
});

test("the latest thing that happened is held in a line of its own", () => {
  render(
    <DeviceList
      devices={[]}
      latest={{ device: { id: "p", type: "plug", name: "Plug" }, capability: "instantPower", value: 2105, at: DAY9_0812 }}
    />,
  );
  expect(screen.getByText("Latest · 08:12")).toBeInTheDocument();
  expect(screen.getByText("Plug: 2,105 W, in use")).toBeInTheDocument();
});

test("no latest line when nothing happened lately", () => {
  render(<DeviceList devices={[]} latest={null} />);
  expect(screen.queryByText(/Latest/)).not.toBeInTheDocument();
});
