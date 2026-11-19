import { expect, test } from "vitest";
import { HttpHomeApi } from "../api/http";
import { MockHomeApi } from "../api/mock/MockHomeApi";
import { pickVoice, ServerVoice } from "./engines";
import { detectLang } from "./lang";
import { endOfSpeech } from "./wav";

test("talking ends after 1.2 s of quiet that follows speech, not before anything was said", () => {
  const chunk = 100;
  const quiet = (n: number) => Array(n).fill(0.02);
  const speech = (n: number) => Array(n).fill(0.4);
  expect(endOfSpeech(quiet(30), chunk)).toBe(false); // nobody has spoken yet
  expect(endOfSpeech([...quiet(5), ...speech(10), ...quiet(11)], chunk)).toBe(false);
  expect(endOfSpeech([...quiet(5), ...speech(10), ...quiet(12)], chunk)).toBe(true);
  expect(endOfSpeech([...speech(10), ...quiet(6), ...speech(2), ...quiet(6)], chunk)).toBe(false); // a pause, then more words
});

test("the language of what was said decides the answer's on Auto", () => {
  expect(detectLang("Liga a luz da sala, por favor")).toBe("pt");
  expect(detectLang("Está alguém em casa?")).toBe("pt");
  expect(detectLang("Turn the lamp off please")).toBe("en");
  expect(detectLang("Is anyone home?")).toBe("en");
  expect(detectLang("ok")).toBeNull();
});

test("only this home's own model listens: no model, no microphone, for your home and the demo house alike", async () => {
  const noModel = new HttpHomeApi("/api", async () => new Response("{}", { status: 501 }));
  await noModel.checkVoice();
  expect(pickVoice(noModel)).toBeNull();
  const withModel = new HttpHomeApi("/api", async () => new Response("{}", { status: 200 }));
  await withModel.checkVoice();
  expect(pickVoice(withModel)).toBeInstanceOf(ServerVoice);
  const demo = new MockHomeApi("demo", 0);
  expect(pickVoice(demo)).toBeNull(); // npm run dev, or setup said no
  demo.transcribe = withModel.transcribe; // npm run demo with the model: its voice-only server
  expect(pickVoice(demo)).toBeInstanceOf(ServerVoice);
});

test("on a page that isn't HTTPS, the microphone says it needs the secure address", async () => {
  const voice = new ServerVoice(async () => ({ text: "", lang: null, ms: 0 }));
  await expect(voice.start({ onPartial() {}, onLevel() {}, onEnd() {} })).rejects.toThrow(/secure address/);
});
