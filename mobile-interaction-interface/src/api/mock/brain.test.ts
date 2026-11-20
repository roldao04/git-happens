import { expect, test } from "vitest";
import { parseTime, toEnglish, whichRoutine } from "./brain";
import { DEVICE_IDS } from "./houses";
import { MockHomeApi } from "./MockHomeApi";

const demo = () => new MockHomeApi("demo", 0);
const value = (api: MockHomeApi, id: string, cap: string) => api.device(id)?.values[cap];

test("the opening status lists what Alfred changed at 08:45 and what's planned", async () => {
  const s = await demo().getStatus();
  expect(s.title).toBe("Out for the day");
  expect(s.byAlfred).toBe(true);
  expect(s.changes.map((c) => `${c.device}.${c.capability}`).sort()).toEqual(["Kettle.power", "Light.power", "Thermostat.targetTemperature"]);
  expect(s.planned.map((p) => p.title)).toEqual(["Evening", "Night", "Morning"]);
  expect(s.suggestions).toHaveLength(1);
});

test("dimming the lamp switches it on at that level, and counts as overruling Alfred", async () => {
  const api = demo();
  const r = await api.chat("Dim the lamp to 30 percent");
  expect(value(api, DEVICE_IDS.light, "power")).toBe("on");
  expect(value(api, DEVICE_IDS.light, "brightness")).toBe(30);
  expect(r.blocks.some((b) => b.t === "p" && /count that as a no/.test(b.text))).toBe(true);
  expect((await api.getStatus()).byAlfred).toBe(false);
});

test("the heating can't go above 18 °C while nobody is home, but can once someone is", async () => {
  const api = demo();
  const blocked = await api.chat("Set the heating to 19 degrees");
  expect(blocked.text).toMatch(/stayed at 17 °C: never above 18 °C while no one is home/);
  expect(value(api, DEVICE_IDS.thermostat, "targetTemperature")).toBe(17);
  await api.chat("I'm home");
  expect(value(api, DEVICE_IDS.thermostat, "targetTemperature")).toBe(20);
  expect((await api.getStatus()).title).toBe("Home");
});

test("undo puts back what Alfred changed and says how often this week", async () => {
  const api = demo();
  const r = await api.chat("undo");
  expect(r.text).toMatch(/^Undone/);
  expect(value(api, DEVICE_IDS.light, "power")).toBe("on");
  expect(value(api, DEVICE_IDS.plug, "power")).toBe("on");
  expect(value(api, DEVICE_IDS.thermostat, "targetTemperature")).toBe(20);
  expect((await api.getStatus()).byAlfred).toBe(false);
  expect((await api.chat("undo")).text).toMatch(/nothing of mine to undo/);
});

test("skip, move and stop change the plan", async () => {
  const api = demo();
  await api.chat("Skip evening tonight");
  expect((await api.getStatus()).planned.find((p) => p.title === "Evening")?.status).toBe("skipped");
  await api.chat("Move night to 23:30");
  expect(api.house.plan.find((p) => p.title === "Night")?.at).toBe("2026-09-21T23:30:00");
  await api.chat("Stop doing the morning");
  expect(api.house.plan.some((p) => p.title === "Morning")).toBe(false);
});

test("the alarm is never armed, whoever asks", async () => {
  expect((await demo().chat("Arm the alarm")).text).toMatch(/don’t arm or disarm/);
});

test("the kit has no thermostat, so heating requests say so", async () => {
  const kit = new MockHomeApi("kit", 0);
  expect((await kit.getHome()).climate).toBeNull();
  expect((await kit.chat("make it warmer")).text).toMatch(/no thermostat/);
});

test("the demo's usual requests get a real answer, not the fallback, in English or Portuguese", async () => {
  const phrases = ["I'm home", "Set the heating to 19 degrees", "Why did you switch to away?", "Dim the lamp to 30 percent",
    "Skip evening tonight", "Move night to 23:30", "Estou em casa", "Liga a luz", "Desliga a luz", "Porquê?",
    "Põe o aquecimento a 19 graus", "Está alguém em casa?", "Desliga tudo", "Hoje não"];
  for (const phrase of phrases) {
    const r = await demo().chat(phrase);
    expect(r.text, phrase).not.toMatch(/^I can switch/);
  }
});

test("Portuguese requests act as the English ones do", async () => {
  expect(toEnglish("desliga a luz")).toBe("off a lamp");
  expect(toEnglish("liga o alarme")).toBe("arm the alarm");
  const api = demo();
  const light = (await api.getHome()).devices.find((d) => d.type === "bulb")!;
  expect((await api.chat("Liga a luz")).text).toMatch(/^Lamp on/);
  expect(value(api, light.id, "power")).toBe("on");
  expect((await api.chat("Desliga a luz")).text).toBe("Lamp off.");
  expect((await api.chat("Estou em casa")).text).toMatch(/^Welcome back/);
  expect((await api.chat("Arma o alarme")).text).toMatch(/don’t arm/);
});

test("routines and times are read from plain speech", () => {
  expect(whichRoutine("skip goodnight")).toBe("night");
  expect(whichRoutine("not tonight")).toBe("evening");
  expect(whichRoutine("when i go out")).toBe("away");
  expect(parseTime("move it to 7pm")).toBe("19:00");
  expect(parseTime("at 23:30")).toBe("23:30");
  expect(parseTime("30 percent")).toBeNull();
});
