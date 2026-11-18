import { expect, test, vi } from "vitest";
import { loadRecording } from "./load";
import { RecordingError } from "./recording";

function fake(status: number, body: unknown) {
  return vi.fn(async () => new Response(typeof body === "string" ? body : JSON.stringify(body), { status }));
}

test("fetches recording.json next to the page", async () => {
  const f = fake(404, "");
  await loadRecording(f).catch(() => undefined);
  expect(f).toHaveBeenCalledWith("./recording.json");
});

test("an HTTP error says it could not load the file", async () => {
  await expect(loadRecording(fake(404, "not here"))).rejects.toThrow(new RecordingError("could not load recording.json (HTTP 404)"));
});

test("a file that is not JSON says so", async () => {
  await expect(loadRecording(fake(200, "<html>"))).rejects.toThrow("recording.json is not valid JSON");
});

test("a wrong shape says which field", async () => {
  await expect(loadRecording(fake(200, { meta: {} }))).rejects.toThrow(/meta\.start/);
});

test("a good file comes back parsed", async () => {
  const body = { meta: { simulated: true, start: "2026-09-01T00:00:00", end: "2026-09-02T00:00:00", days: 1 }, devices: [], sensors: [], readings: [] };
  const rec = await loadRecording(fake(200, body));
  expect(rec.meta.days).toBe(1);
});
