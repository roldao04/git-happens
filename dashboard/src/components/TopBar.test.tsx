import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { TopBar } from "./TopBar";

test("shows the logo and no clock or controls", () => {
  render(<TopBar tab="home" />);
  expect(screen.getByRole("link", { name: "Alfred home" })).toHaveAttribute("href", "#/");
  expect(screen.queryByText("Simulated")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});

test("has only the Home, Learned and Devices tabs, with the current one marked", () => {
  render(<TopBar tab="learned" />);
  const nav = screen.getByRole("navigation", { name: "Main" });
  expect([...nav.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
    ["Home", "#/"],
    ["Learned", "#/learned"],
    ["Devices", "#/devices"],
  ]);
  expect(screen.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "Learned" })).toHaveAttribute("aria-current", "page");
});

test("each tab carries a decorative icon for the phone's bottom bar", () => {
  render(<TopBar tab="home" />);
  for (const name of ["Home", "Learned", "Devices"]) {
    const icon = screen.getByRole("link", { name }).querySelector("svg.tab-ic");
    expect(icon).not.toBeNull();
    expect(icon).toHaveAttribute("aria-hidden", "true");
  }
});
