import { parseRecording, RecordingError, type Recording } from "./recording";

export async function loadRecording(fetcher: typeof fetch = fetch): Promise<Recording> {
  const res = await fetcher("./recording.json");
  if (!res.ok) throw new RecordingError(`could not load recording.json (HTTP ${res.status})`);
  let raw: unknown;
  try {
    raw = await res.json();
  } catch (cause) {
    throw new RecordingError(`recording.json is not valid JSON (${cause instanceof Error ? cause.message : String(cause)})`);
  }
  return parseRecording(raw);
}
