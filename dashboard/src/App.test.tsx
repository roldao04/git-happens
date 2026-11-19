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
  expect(screen.getByRole("link", { name: "Alfred home" })).toBeInTheDocument();
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
  await screen.findByRole("link", { name: "Alfred home" });
  // the keyboard listener is attached by an effect that can land just after the page shows
  await waitFor(() => {
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByRole("button", { name: "Simulated · day 2 · 00:00" })).toBeInTheDocument();
  });
  await userEvent.click(screen.getByRole("link", { name: "Learned" }));
  await userEvent.click(await screen.findByRole("link", { name: /Pattern 2/ }));
  expect(await screen.findByRole("heading", { level: 1, name: "Pattern 2" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("link", { name: "← Learned" }));
  expect(await screen.findByRole("list", { name: "Learned sensors" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Simulated · day 2 · 00:00" })).toBeInTheDocument();
});

test("the tabs switch between Home and Learned", async () => {
  render(<App load={async () => rec} />);
  await screen.findByRole("link", { name: "Alfred home" });
  await waitFor(() => {
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByRole("button", { name: "Simulated · day 2 · 00:00" })).toBeInTheDocument();
  });
  await userEvent.click(screen.getByRole("link", { name: "Learned" }));
  expect(await screen.findByRole("list", { name: "Learned sensors" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("link", { name: "Home" }));
  expect(await screen.findByRole("link", { name: "See the whole day" })).toBeInTheDocument();
});

test("Home's link opens the Timeline, a day opens by its link, and the Devices tab opens its page", async () => {
  render(<App load={async () => rec} />);
  await screen.findByRole("link", { name: "Alfred home" });
  await waitFor(() => {
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByRole("button", { name: "Simulated · day 2 · 00:00" })).toBeInTheDocument();
  });
  await userEvent.click(screen.getByRole("link", { name: "See the whole day" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Today, Wed 2 Sep" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("link", { name: "Earlier" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Tue 1 Sep, day 1" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("link", { name: "Devices" }));
  expect(await screen.findByRole("heading", { level: 1, name: "All 0 devices look fine." })).toBeInTheDocument();
});

const real = (updated: string) => {
  const r = recording({ sensors: [] });
  return { ...r, meta: { ...r.meta, simulated: false, updated } };
};

test("live mode shows the newest reading, has no playback and keeps refreshing", async () => {
  let next: (r: ReturnType<typeof real>) => void = () => undefined;
  const later = new Promise<ReturnType<typeof real>>((ok) => { next = ok; });
  const load = vi.fn().mockResolvedValueOnce(real("2026-09-01T12:23:05")).mockReturnValue(later);
  render(<App load={load} live refreshMs={20} />);
  expect(await screen.findByText("Real home · last reading 12:23")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Simulated/ })).toBeNull();
  await act(async () => next(real("2026-09-01T12:24:10")));
  expect(await screen.findByText("Real home · last reading 12:24")).toBeInTheDocument();
});

test("a failed refresh keeps the last good page and logs why", async () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const load = vi.fn().mockResolvedValueOnce(real("2026-09-01T12:23:05")).mockRejectedValue(new RecordingError("could not load live.json (HTTP 404)"));
  render(<App load={load} live refreshMs={20} />);
  expect(await screen.findByText("Real home · last reading 12:23")).toBeInTheDocument();
  await waitFor(() => expect(log).toHaveBeenCalledWith("refreshing live.json failed", expect.any(RecordingError)));
  expect(screen.getByText("Real home · last reading 12:23")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
  log.mockRestore();
});
