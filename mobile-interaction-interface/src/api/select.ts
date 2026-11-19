import type { HomeApi } from "./client";
import { HttpHomeApi } from "./http";
import { MockHomeApi } from "./mock/MockHomeApi";

/** How the app was started (scripts/start.mjs sets VITE_ALFRED_MODE): the demo house, or your home through the
 *  loop's API. Look-only or live is the API's to say (HomeSnapshot.lookOnly). */
export type Mode = "demo" | "home";

export function modeFrom(env: { VITE_ALFRED_MODE?: string }): Mode {
  return env.VITE_ALFRED_MODE === "home" ? "home" : "demo";
}

export function makeApi(mode: Mode): HomeApi {
  return mode === "home" ? new HttpHomeApi("./api") : new MockHomeApi("demo");
}

/** One key per home, so each keeps its own chats. */
export function homeKey(mode: Mode): string {
  return mode === "home" ? "http" : "mock-demo";
}
