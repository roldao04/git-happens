import { expect, test, vi } from "vitest";
import { ApiError } from "./client";
import { HttpHomeApi } from "./http";
import { MockHomeApi } from "./mock/MockHomeApi";

const respond = (body: unknown, status = 200) => vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

test("the home from the API is checked and kept", async () => {
  const home = await new MockHomeApi("demo", 0).getHome();
  const fetcher = respond(home);
  const api = new HttpHomeApi("/api", fetcher);
  expect((await api.getHome()).devices).toHaveLength(home.devices.length);
  expect(fetcher).toHaveBeenCalledWith("/api/home", expect.objectContaining({ cache: "no-store" }));
});

test("a wrong field is named in the error", async () => {
  const home = await new MockHomeApi("demo", 0).getHome();
  const broken = { ...home, devices: [{ ...home.devices[0], controls: [{ kind: "dial" }] }] };
  await expect(new HttpHomeApi("/api", respond(broken)).getHome()).rejects.toThrow(/home\.devices\[0\]\.controls\[0\]\.kind "dial" is not one of/);
});

test("a write posts exactly the guard's body, and a blocked answer comes back as it is", async () => {
  const fetcher = respond({ status: "blocked", verified: null, reason: "blocked: never above 18 °C while no one is home" });
  const r = await new HttpHomeApi("/api", fetcher).setControl("thermo 1", { type: "targetTemperature", value: 19 });
  expect(r).toEqual({ status: "blocked", verified: null, reason: "blocked: never above 18 °C while no one is home" });
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/devices/thermo%201");
  expect(JSON.parse(String(init.body))).toEqual({ body: { type: "targetTemperature", value: 19 } });
});

test("the server's error message is shown", async () => {
  const api = new HttpHomeApi("/api", respond({ error: "no such decision" }, 404));
  await expect(api.skip("decision-9")).rejects.toEqual(new ApiError("no such decision", 404));
});

test("transcribe exists only once the server says it has the voice model", async () => {
  const none = new HttpHomeApi("/api", respond({ error: "voice not installed" }, 501));
  expect(none.transcribe).toBeUndefined();
  expect(await none.checkVoice()).toBe(false);
  expect(none.transcribe).toBeUndefined();
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith("voice") ? { text: "liga a luz", lang: null, ms: 640 } : {}), { status: 200 }));
  const api = new HttpHomeApi("/api", fetcher as unknown as typeof fetch);
  expect(await api.checkVoice()).toBe(true);
  expect(await api.transcribe!(new Blob(["RIFF"]))).toEqual({ text: "liga a luz", lang: null, ms: 640 });
});

test("the status's alerts are checked, and an older API without them gives none", async () => {
  const s = await new MockHomeApi("demo", 0).getStatus();
  const alert = { id: "habit-1:contact-1:2026-09-21", at: "2026-09-21T23:10:00", text: "The window is still open." };
  expect((await new HttpHomeApi("/api", respond({ ...s, alerts: [alert] })).getStatus()).alerts).toEqual([alert]);
  const { alerts: _gone, ...older } = { ...s, alerts: [] };
  expect((await new HttpHomeApi("/api", respond(older)).getStatus()).alerts).toEqual([]);
  await expect(new HttpHomeApi("/api", respond({ ...s, alerts: [{ id: 1 }] })).getStatus()).rejects.toThrow(/status\.alerts\[0\]\.id/);
});
