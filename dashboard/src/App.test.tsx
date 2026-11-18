import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";
import { RecordingError } from "./data/recording";
import { recording, sensor } from "./test/fixtures";

afterEach(() => {
  window.location.hash = "";
});

const rec = recording({ sensors: [sensor({ learned_at: "2026-09-01" })] });

test("says it is loading until the recording arrives", async () => {
  let resolve: (r: typeof rec) => void = () => undefined;
  render(<App load={() => new Promise((ok) => { resolve = ok; })} />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading the recording");
  await act(async () => resolve(rec));
  expect(screen.getByRole("heading", { level: 1, name: "Alfred" })).toBeInTheDocument();
});

test("a failing load shows the reason as an alert", async () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
  render(<App load={() => Promise.reject(new RecordingError("could not load recording.json (HTTP 404)"))} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("could not load recording.json (HTTP 404)");
  expect(log).toHaveBeenCalled();
  log.mockRestore();
});

test("going to a sensor and back keeps the playhead", async () => {
  render(<App load={async () => rec} />);
  await screen.findByRole("heading", { level: 1, name: "Alfred" });
  // the keyboard listener is attached by an effect that can land just after the page shows
  await waitFor(() => {
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("Wed · day 2 of 21")).toBeInTheDocument();
  });
  await userEvent.click(screen.getByRole("link", { name: /Pattern 2/ }));
  expect(await screen.findByRole("heading", { level: 2, name: "Pattern 2" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("link", { name: /Back/ }));
  expect(await screen.findByRole("heading", { name: "Home now" })).toBeInTheDocument();
  expect(screen.getByText("Wed · day 2 of 21")).toBeInTheDocument();
});
