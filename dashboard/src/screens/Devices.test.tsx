import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { Value } from "../data/recording";
import type { DeviceNow } from "../data/stateAt";
import { playback, recording, state } from "../test/fixtures";
import { Devices } from "./Devices";

function dev(id: string, type: string, name: string, values: Record<string, Value>): DeviceNow {
  return { device: { id, type, name }, values, changedAt: null, since: null };
}

const home = [
  dev("l", "bulb", "Light", { power: "on", brightness: 100 }),
  dev("p", "plug", "Plug", { instantPower: 0 }),
  dev("d", "contactSensor", "Contact sensor", { sensorState: "normal", signalStrength: "high" }),
  dev("t", "temperatureSensor", "Temperature sensor", { temperatureValue: 21.9, humidityValue: 48, batteryLevel: 100 }),
  dev("c", "camera", "Camera", { sdRecording: true }),
  dev("a", "security", "Alarm", { status: "DISARMED", isAlarmed: false }),
  dev("g", "gateway", "Security hub", { networkMode: "ethernet", batteryLevel: 20, chargeState: true, cellularConnectionState: "abnormal" }),
  dev("s", "smokeSensor", "Smoke sensor", { sensorState: "normal", batteryStatus: "high", signalStrength: "low" }),
];

const page = (devices: DeviceNow[] = home) => render(<Devices rec={recording()} state={state({ devices })} playback={playback()} />);

test("is the Devices tab, with the count in the headline and the slider", () => {
  page();
  expect(screen.getByRole("link", { name: "Devices" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { level: 1, name: "8 devices, 2 need a look." })).toBeInTheDocument();
  expect(screen.getByText("Alfred only reads them. It never switches anything.")).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: "Simulated day" })).toBeInTheDocument();
});

test("needs a look lists each device with its issues", () => {
  page();
  const sec = screen.getByRole("region", { name: "Needs a look" });
  const rows = within(sec).getAllByRole("listitem");
  expect(rows.map((r) => r.textContent)).toEqual([
    "Security hubBattery 20%No mobile signal",
    "Smoke sensorWeak signal",
  ]);
});

test("the lamp and plug show their state only, with switching as coming later", () => {
  page();
  const sec = screen.getByRole("region", { name: "Lamp and plug" });
  expect(within(sec).getByText("Light")).toBeInTheDocument();
  expect(within(sec).getByText("On")).toBeInTheDocument();
  expect(within(sec).getByText("0 W")).toBeInTheDocument();
  expect(within(sec).queryByRole("switch")).toBeNull();
  expect(within(sec).queryByRole("button")).toBeNull();
  expect(within(sec).getByRole("heading", { name: "Switching comes later" }).closest(".later")).not.toBeNull();
});

test("reporting lists every other device that is fine, with its state", () => {
  page();
  const sec = screen.getByRole("region", { name: "Reporting" });
  const names = within(sec).getAllByRole("listitem").map((li) => li.querySelector(".n")?.textContent);
  expect(names).toEqual(["Contact sensor", "Temperature sensor", "Camera", "Alarm"]);
  expect(within(sec).getByText("Disarmed")).toBeInTheDocument();
  expect(within(sec).getByText("Recording")).toBeInTheDocument();
});

test("a home where nothing needs a look says so and has no such section", () => {
  page(home.slice(0, 6));
  expect(screen.getByRole("heading", { level: 1, name: "All 6 devices look fine." })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Needs a look" })).toBeNull();
});
