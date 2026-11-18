import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { House } from "./House";

const view = { lampOn: true, doorOpen: false, plugInUse: false, temperature: 19.94 };

test("draws the home as one described picture", () => {
  render(<House view={view} />);
  expect(screen.getByRole("img", { name: "The home: the lamp is on, the door is closed, the plug is at rest, 19.9 °C inside." })).toBeInTheDocument();
});

test("the lamp glows only while it is on", () => {
  const { container, rerender } = render(<House view={view} />);
  expect(container.querySelector(".house-lamp.on .house-glow")).not.toBeNull();
  rerender(<House view={{ ...view, lampOn: false }} />);
  expect(container.querySelector(".house-lamp.on")).toBeNull();
  expect(container.querySelector(".house-glow")).toBeNull();
});

test("the door is drawn open or closed", () => {
  const { container, rerender } = render(<House view={view} />);
  expect(container.querySelector(".house-door.closed")).not.toBeNull();
  rerender(<House view={{ ...view, doorOpen: true }} />);
  expect(container.querySelector(".house-door.open")).not.toBeNull();
});

test("the plug lights up while in use", () => {
  const { container, rerender } = render(<House view={view} />);
  expect(container.querySelector(".house-plug.on")).toBeNull();
  rerender(<House view={{ ...view, plugInUse: true }} />);
  expect(container.querySelector(".house-plug.on")).not.toBeNull();
});

test("shows the temperature, or nothing when it is unknown", () => {
  const { container, rerender } = render(<House view={view} />);
  expect(container.querySelector(".house-temp")?.textContent).toBe("19.9 °C");
  rerender(<House view={{ ...view, temperature: null }} />);
  expect(container.querySelector(".house-temp")).toBeNull();
});

test("a device without a reading is drawn faded", () => {
  const { container } = render(<House view={{ lampOn: null, doorOpen: null, plugInUse: null, temperature: null }} />);
  expect(container.querySelectorAll(".unknown")).toHaveLength(3);
});

test("the plug powers a plain appliance with a light that is on only while in use, not a guessed kettle", () => {
  const { container, rerender } = render(<House view={view} />);
  expect(container.querySelector(".house-appliance")).not.toBeNull();
  expect(container.querySelector(".house-kettle, .house-steam")).toBeNull();
  expect(container.querySelector(".house-led")).toBeNull();
  rerender(<House view={{ ...view, plugInUse: true }} />);
  expect(container.querySelector(".house-plug.on .house-led")).not.toBeNull();
});
