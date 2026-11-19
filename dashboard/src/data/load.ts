import { parseRecording, RecordingError, type Recording } from "./recording";

async function fetchRecording(fetcher: typeof fetch, file: string, init?: RequestInit): Promise<Recording> {
  const res = init === undefined ? await fetcher(`./${file}`) : await fetcher(`./${file}`, init);
  if (!res.ok) throw new RecordingError(`could not load ${file} (HTTP ${res.status})`);
  let raw: unknown;
  try {
    raw = await res.json();
  } catch (cause) {
    throw new RecordingError(`${file} is not valid JSON (${cause instanceof Error ? cause.message : String(cause)})`);
  }
  return parseRecording(raw);
}

export function loadRecording(fetcher: typeof fetch = fetch): Promise<Recording> {
  return fetchRecording(fetcher, "recording.json");
}

export function loadLive(fetcher: typeof fetch = fetch): Promise<Recording> {
  return fetchRecording(fetcher, "live.json", { cache: "no-store" });
}

export function isLive(search: string): boolean {
  return new URLSearchParams(search).has("live");
}
