import { act, fireEvent, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { DAY_MS } from "../data/stateAt";
import { usePlayback } from "./usePlayback";

const start = Date.UTC(2026, 8, 1);
const end = Date.UTC(2026, 8, 21, 23, 59, 59);

let frames: FrameRequestCallback[] = [];
let now = 0;

function runFrames(totalMs: number, stepMs = 100) {
  for (let ran = 0; ran < totalMs; ran += stepMs) {
    now += stepMs;
    const pending = frames;
    frames = [];
    act(() => pending.forEach((f) => f(now)));
  }
}

beforeEach(() => {
  frames = [];
  now = 0;
  vi.stubGlobal("requestAnimationFrame", (f: FrameRequestCallback) => frames.push(f));
  vi.stubGlobal("cancelAnimationFrame", () => { frames = []; });
});

afterEach(() => vi.unstubAllGlobals());

test("starts paused at the start", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  expect(result.current.t).toBe(start);
  expect(result.current.playing).toBe(false);
  expect(result.current.speed).toBe(1);
});

test("plays one simulated day in 10 seconds at 1×", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  act(() => result.current.toggle());
  runFrames(10_100);
  expect(result.current.t).toBeGreaterThanOrEqual(start + DAY_MS - DAY_MS / 100);
  expect(result.current.t).toBeLessThanOrEqual(start + DAY_MS + DAY_MS / 50);
});

test("pauses by itself at the end", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  act(() => result.current.jumpToDay(21));
  act(() => result.current.setSpeed(4));
  act(() => result.current.play());
  runFrames(3_000);
  expect(result.current.t).toBe(end);
  expect(result.current.playing).toBe(false);
});

test("jumps to a day and to the end", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  act(() => result.current.jumpToDay(3));
  expect(result.current.t).toBe(start + 2 * DAY_MS);
  act(() => result.current.jumpToEnd());
  expect(result.current.t).toBe(end);
});

test("pause stops time", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  act(() => result.current.play());
  runFrames(1_000);
  act(() => result.current.pause());
  const held = result.current.t;
  runFrames(1_000);
  expect(result.current.t).toBe(held);
});

test("space toggles and arrows step a day", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  fireEvent.keyDown(window, { key: " " });
  expect(result.current.playing).toBe(true);
  fireEvent.keyDown(window, { key: " " });
  expect(result.current.playing).toBe(false);
  fireEvent.keyDown(window, { key: "ArrowRight" });
  expect(result.current.t).toBe(start + DAY_MS);
  fireEvent.keyDown(window, { key: "ArrowLeft" });
  expect(result.current.t).toBe(start);
});

test("typing into a text field is left alone", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  const input = document.createElement("input");
  document.body.append(input);
  fireEvent.keyDown(input, { key: " " });
  expect(result.current.playing).toBe(false);
  input.remove();
});

function focused(html: string): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = html;
  document.body.append(host);
  const el = host.firstElementChild;
  if (!(el instanceof HTMLElement)) throw new Error("no element");
  return el;
}

test("after clicking a button, space and the arrows still work", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  const button = focused("<button>2×</button>");
  const spaceKept = fireEvent.keyDown(button, { key: " " });
  expect(spaceKept).toBe(false);
  expect(result.current.playing).toBe(true);
  fireEvent.keyDown(button, { key: " " });
  fireEvent.keyDown(button, { key: "ArrowRight" });
  expect(result.current.t).toBe(start + DAY_MS);
  button.parentElement?.remove();
});

test("on a link the arrows still step a day", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  const link = focused('<a href="#/">x</a>');
  fireEvent.keyDown(link, { key: "ArrowRight" });
  expect(result.current.t).toBe(start + DAY_MS);
  link.parentElement?.remove();
});

test("the slider keeps its own arrows but space still plays", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  const range = focused('<input type="range">');
  fireEvent.keyDown(range, { key: "ArrowRight" });
  expect(result.current.t).toBe(start);
  fireEvent.keyDown(range, { key: " " });
  expect(result.current.playing).toBe(true);
  range.parentElement?.remove();
});

test("a summary keeps space for opening but the arrows still step", () => {
  const { result } = renderHook(() => usePlayback(start, end));
  const summary = focused("<summary>Also noticed</summary>");
  fireEvent.keyDown(summary, { key: " " });
  expect(result.current.playing).toBe(false);
  fireEvent.keyDown(summary, { key: "ArrowRight" });
  expect(result.current.t).toBe(start + DAY_MS);
  summary.parentElement?.remove();
});
