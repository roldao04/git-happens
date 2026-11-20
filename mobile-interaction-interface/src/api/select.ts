import type { HomeApi } from "./client";
import { HttpHomeApi } from "./http";
import type { Moment } from "./mock/houses";
import { MockHomeApi } from "./mock/MockHomeApi";

/** How the app was started (scripts/start.mjs sets VITE_ALFRED_MODE): the demo house, or your home through the
 *  loop's API. Look-only or live is the API's to say (HomeSnapshot.lookOnly). */
export type Mode = "demo" | "home";

export function modeFrom(env: { VITE_ALFRED_MODE?: string }): Mode {
  return env.VITE_ALFRED_MODE === "home" ? "home" : "demo";
}

/** ?moment=bedtime stages the demo house at Night's start with the window open (the pitch film's 01:40 scene). */
export function momentFrom(search: string): Moment | undefined {
  return new URLSearchParams(search).get("moment") === "bedtime" ? "bedtime" : undefined;
}

export function makeApi(mode: Mode, search = ""): HomeApi {
  return mode === "home" ? new HttpHomeApi("./api") : new MockHomeApi("demo", undefined, momentFrom(search));
}

/** Where a home's chats are kept, so each has its own; a staged moment has its own too. */
export function chatStoreFor(mode: Mode, search = ""): string {
  if (mode === "home") return "http";
  const moment = momentFrom(search);
  return moment ? `mock-demo-${moment}` : "mock-demo";
}
