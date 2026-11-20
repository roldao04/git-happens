import { TARGET, TONES } from "../controls";
import type { ActionResult, Block, ChatReply, ControlBody, Device } from "../types";
import type { MockHomeApi } from "./MockHomeApi";

// The demo house's chat: scripted answers to what a person types or says, acting through the same writes the
// app's controls use. The real chat is the loop's agent (POST /api/chat); this only stands in for it.
// It understands Portuguese too (the voice model hears both), by putting the main phrases into English
// first; it answers in English.

type Routine = "evening" | "night" | "morning" | "away";
type Mode = "home" | Routine;

const TITLES: Record<Routine, string> = { evening: "Evening", night: "Night", morning: "Morning", away: "Out for the day" };
const MODE_TITLES: Record<Mode, string> = { home: "Home", ...TITLES };

function reply(text: string, blocks: Block[] = [], actions: ActionResult[] = []): ChatReply {
  return { text, blocks, actions, lang: "en" };
}
const p = (text: string): Block => ({ t: "p", text });
const fmt = (x: number) => (Number.isInteger(x) ? String(x) : x.toFixed(1));
const hhmm = (iso: string) => iso.slice(11, 16);

export function whichRoutine(t: string): Routine | null {
  if (/goodnight|good night|\bnight\b|\bbed\b|sleep/.test(t)) return "night";
  if (/evening|tonight/.test(t)) return "evening";
  if (/\bmorning\b|wake|getting up/.test(t)) return "morning";
  if (/leav|\baway\b|out for the day|when i go/.test(t)) return "away";
  return null;
}

function whichMode(t: string): Mode | null {
  if (/\bnight\b|\bbed\b|sleep/.test(t)) return "night";
  if (/evening/.test(t)) return "evening";
  if (/\bmorning\b/.test(t)) return "morning";
  if (/\baway\b|out for the day/.test(t)) return "away";
  if (/\bhome\b/.test(t)) return "home";
  return null;
}

export function parseTime(t: string): string | null {
  const m = t.match(/\b(\d{1,2})(?:[:h.](\d{2}))?\s*(am|pm)?\b(?!\s*(%|percent|degree|°))/);
  if (!m) return null;
  let h = Number(m[1]);
  const mi = m[2] ? Number(m[2]) : 0;
  if (m[3] === "pm" && h < 12) h += 12;
  if (m[3] === "am" && h === 12) h = 0;
  if (h > 23 || mi > 59) return null;
  return `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`;
}

function plugNamed(api: MockHomeApi, t: string): Device | undefined {
  const plugs = api.house.devices.filter((d) => d.type === "plug");
  return plugs.find((d) => t.includes(d.name.toLowerCase().split(" ")[0]!)) ?? plugs[0];
}

function lampOn(id: string, bri: number): [string, ControlBody][] {
  return [[id, { type: "power", value: "on" }], [id, { type: "brightness", value: bri }], [id, { type: "temperature", value: TONES[0]!.value }]];
}

function modeWrites(api: MockHomeApi, mode: Mode): [string, ControlBody][] {
  const light = api.find("bulb");
  const plugs = api.house.devices.filter((d) => d.type === "plug");
  const kettle = plugs.find((d) => /kettle/i.test(d.name)) ?? plugs[0];
  const tv = plugs.find((d) => /tv/i.test(d.name));
  const thermo = api.find("thermostat");
  const out: [string, ControlBody][] = [];
  const off = (d?: Device) => d && out.push([d.id, { type: "power", value: "off" }]);
  const on = (d?: Device) => d && out.push([d.id, { type: "power", value: "on" }]);
  const heat = (c: number) => thermo && out.push([thermo.id, { type: "targetTemperature", value: c }]);
  if (mode === "home" || mode === "evening") { if (light) out.push(...lampOn(light.id, 60)); if (mode === "home") on(kettle); heat(mode === "home" ? 20 : 21); }
  if (mode === "morning") { if (light) out.push(...lampOn(light.id, 30)); on(kettle); heat(20); }
  if (mode === "night" || mode === "away") { off(light); off(kettle); off(tv); heat(mode === "night" ? 18 : 17); }
  return out;
}

function blockedLine(results: ActionResult[], api: MockHomeApi): string | null {
  const b = results.find((r) => r.status === "blocked");
  if (!b) return null;
  const name = b.deviceId ? api.device(b.deviceId)?.name ?? "That" : "That";
  return `${name} stayed as it was: ${b.reason?.replace(/^blocked: /, "")}.`;
}

function notes(results: ActionResult[]): Block[] {
  return results.filter((r) => r.note).map((r) => p(r.note!));
}

// Portuguese → the English the script matches. Accents are dropped first; the order matters ("desliga tudo"
// before "desliga", "desliga" before "liga").
const PT: [RegExp, string][] = [
  [/\b(desliga|desligar|apaga|apagar) tudo\b|\btudo (desligado|apagado)\b/g, "everything off"],
  [/\b(desarma|desarmar|arma|armar|ativa|ativar|liga|ligar)( o)? alarme\b/g, "arm the alarm"],
  [/\b((ja )?estou|cheguei|estou de volta)( a| em)? casa\b|\bcheguei\b|\bestou de volta\b/g, "i'm home"],
  [/\b(quem esta|esta alguem|ha alguem|alguem)( em casa)?\b/g, "anyone home"],
  [/\b(desfaz|desfazer|anula|anular)\b/g, "undo"],
  [/\b(porque e que|por que|porque)\b/g, "why"],
  [/\bhoje nao\b/g, "not today"], [/\besta noite nao\b/g, "not tonight"], [/\b(salta|saltar|cancela|cancelar)\b/g, "skip"],
  [/\besta noite\b/g, "tonight"], [/\b(fim de tarde|serao)\b/g, "evening"], [/\bnoite\b/g, "night"],
  [/\bmanha\b/g, "morning"], [/\b(dormir|deitar)\b/g, "bed"], [/\bvou sair\b|\bsaio\b/g, "leaving"],
  [/\b(muda|mudar|passa|passar) para\b/g, "switch to"], [/\b(em )?que modo\b/g, "what mode"], [/\bmodo\b/g, "mode"], [/\bestado\b/g, "status"],
  [/\bmais quente\b/g, "warmer"], [/\bmais fri[oa]\b/g, "colder"],
  [/\b(desliga|desligar|apaga|apagar)\b/g, "off"], [/\b(liga|ligar|acende|acender)\b/g, "on"],
  [/\b(sobe|aumenta|aumentar)\b/g, "up"], [/\b(baixa|baixar|desce|diminui|diminuir)\b/g, "down"],
  [/\b(luz|luzes|candeeiro|lampada)\b/g, "lamp"], [/\b(aquecimento|termostato|aquecedor)\b/g, "heating"], [/\bgraus\b/g, "degrees"],
  [/\bpor cento\b/g, "percent"], [/\b(tomada|ficha)\b/g, "plug"], [/\bchaleira\b/g, "kettle"], [/\btelevisao\b/g, "tv"],
  [/\bmaquina de lavar\b/g, "washing"], [/\b(quente|amarela)\b/g, "warm"], [/\b(fria|branca)\b/g, "cool"],
  [/\btemperatura\b/g, "temperature"], [/\bhumidade\b/g, "humid"], [/\bporta\b/g, "door"],
  [/\b(plano|planeado|agenda)\b/g, "plan"], [/\b(dispositivos|aparelhos)\b/g, "device"],
];

export function toEnglish(t: string): string {
  let s = t.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  for (const [pt, en] of PT) s = s.replace(pt, en);
  return s;
}

export async function respond(raw: string, api: MockHomeApi): Promise<ChatReply> {
  const t = toEnglish(raw.toLowerCase().replace(/’/g, "'").trim());
  const status = api.house.status;
  const light = api.find("bulb");
  const thermo = api.find("thermostat");

  if (/\b(arm|disarm)\b/.test(t)) {
    return reply("I don’t arm or disarm the alarm. That stays with you, at the security hub.", [p("It’s disarmed now.")]);
  }

  if (/\bwhy\b/.test(t)) {
    return reply(
      "The front door closed at 08:41, then nothing moved for 4 minutes. On 8 of the last 18 days a quiet like that lasted until around 17:53, so it looked like you’d gone out for the day.",
      [
        { t: "feed", lines: [["08:41:07", "door.contact", "open"], ["08:41:38", "door.contact", "closed"], ["08:45:38", "lamp.power", "off"], ...(thermo ? [["08:45:40", "heating.target", "17 °C"] as [string, string, string]] : [])] },
        p("If that was wrong, say undo. I count every undo, and stop doing things you keep undoing."),
      ],
    );
  }

  if (/\bundo\b|back on\b|turn it back|put it back|change it back/.test(t) && !/plug|kettle|\btv\b/.test(t) && !whichRoutine(t)) {
    if (!status.byAlfred || !status.decisionId) return reply("There’s nothing of mine to undo right now.");
    const was = status.title;
    const actions = await api.undo(status.decisionId);
    const s = api.house.status;
    return reply(`Undone. Back to ${s.title}: everything as it was before ${hhmm(status.since)}.`,
      [p(`That’s the ${ordinal(api.house.overrides)} time this week you’ve undone ${was}.${api.house.overrides >= 2 ? " One more and I’ll stop switching to it on my own." : ""}`)], actions);
  }

  if (/\bstop\b|don'?t do (this|that)|never do/.test(t)) {
    const r = whichRoutine(t) ?? "away";
    api.house.plan = api.house.plan.filter((x) => x.title !== TITLES[r]);
    return reply(`Done. I won’t switch to ${TITLES[r]} on my own anymore.`, [p("Ask me to start it again any time.")]);
  }

  if (/\bskip\b|not tonight|not today|cancel/.test(t)) {
    const r = whichRoutine(t) ?? "evening";
    if (r === "away") return reply("Skipped Out for the day for today. I won’t change anything when you go out.");
    const item = api.house.plan.find((x) => x.title === TITLES[r] && x.mode === "do");
    if (!item) return reply(`${TITLES[r]} isn’t planned, so there’s nothing to skip.`);
    if (item.status === "skipped") return reply(`${TITLES[r]} is already skipped.`);
    await api.skip(item.id);
    return reply(`Skipped ${TITLES[r]} ${r === "morning" ? "tomorrow" : "today"}. It runs again the day after, around ${hhmm(item.at)}.`, [{ t: "plan" }]);
  }

  const rid = whichRoutine(t);
  if (rid && rid !== "away" && /\b(move|change|shift|push|later|earlier|make)\b/.test(t) && !/heat|thermostat|degree/.test(t)) {
    const item = api.house.plan.find((x) => x.title === TITLES[rid]);
    let to = parseTime(t);
    if (!to && item && /later|earlier/.test(t)) {
      const [h, m] = hhmm(item.at).split(":").map(Number) as [number, number];
      const mins = h * 60 + m + (/later/.test(t) ? 30 : -30);
      to = `${String(Math.floor(((mins + 1440) % 1440) / 60)).padStart(2, "0")}:${String(((mins % 60) + 60) % 60).padStart(2, "0")}`;
    }
    if (item && to) {
      item.at = `${item.at.slice(0, 11)}${to}:00`;
      return reply(`Done. ${TITLES[rid]} now starts around ${to}.`, [p("If you keep doing it yourself at another time, I’ll suggest moving it again."), { t: "plan" }]);
    }
  }

  if (/i'?m (back )?home|i am (back )?home|back home|home mode|switch to home|i'?m back/.test(t)) {
    api.arrive("Ana");
    return switchTo(api, "home", "Welcome back. ");
  }
  const mid = whichMode(t);
  if (mid && /\bmode\b|switch to|change to|go to/.test(t)) return switchTo(api, mid, "");

  if (/what mode|which mode|current mode|right now|status/.test(t) && !/device/.test(t)) {
    return reply(`Right now: ${status.title}, since ${hhmm(status.since)}${status.byAlfred ? ", set by me" : ""}.`, [{ t: "plan" }]);
  }

  if (/everything off|all off|goodnight|good night|going to bed|turn (it )?all off/.test(t)) {
    const writes: [string, ControlBody][] = [];
    if (light) writes.push([light.id, { type: "power", value: "off" }]);
    for (const d of api.house.devices) if (d.type === "plug" && !/washing/i.test(d.name)) writes.push([d.id, { type: "power", value: "off" }]);
    const actions = writes.map(([id, b]) => api.write(id, b));
    return reply("Lamp and plugs off. The camera and the smoke alarm stay on.", notes(actions), actions);
  }

  if (/plan|schedule|today|coming up|planned/.test(t)) return reply("Here’s what I’ll do. Skip anything you don’t want today.", [{ t: "plan" }]);

  if (light && /\b(lamp|light)\b|bright|\bdim\b|\d+\s*(%|percent)/.test(t)) {
    if (/^(is|was|are)\b/.test(t)) return reply(light.values.power === "on" ? `The lamp is on, ${light.values.brightness}%.` : "The lamp is off.", [{ t: "control", deviceId: light.id }]);
    const n = t.match(/(\d{1,3})\s*(%|percent)/);
    const writes: ControlBody[] = [];
    if (/\boff\b/.test(t) && !n) writes.push({ type: "power", value: "off" });
    else {
      const cur = typeof light.values.brightness === "number" ? light.values.brightness : 60;
      writes.push({ type: "power", value: "on" });
      if (n) writes.push({ type: "brightness", value: Math.max(1, Math.min(100, Number(n[1]))) });
      else if (/\bdim|darker|lower|down\b/.test(t)) writes.push({ type: "brightness", value: 30 });
      else if (/bright|\bup\b|full|max/.test(t)) writes.push({ type: "brightness", value: Math.min(100, cur + 30) });
      const tone = /warm/.test(t) ? TONES[0] : /cool|cold|daylight|white/.test(t) ? TONES[2] : /neutral/.test(t) ? TONES[1] : null;
      if (tone) writes.push({ type: "temperature", value: tone.value });
    }
    const actions = writes.map((b) => api.write(light.id, b));
    const on = light.values.power === "on";
    return reply(on ? `Lamp on, ${light.values.brightness}%.` : "Lamp off.", [{ t: "control", deviceId: light.id }, ...notes(actions)], actions);
  }

  if (/heat|thermostat|warmer|colder|cooler|degree|°/.test(t) && !/how (warm|cold)|what'?s the temp/.test(t)) {
    if (!thermo) return reply("This home has no thermostat I can reach, so I can’t change the heating.");
    const cur = typeof thermo.values.targetTemperature === "number" ? thermo.values.targetTemperature : 17;
    const n = t.match(/(\d{1,2}(?:[.,]\d)?)/);
    let v: number | null = null;
    if (n) v = Number(n[1]!.replace(",", "."));
    else if (/warmer|\bup\b|raise|higher/.test(t)) v = cur + 1;
    else if (/colder|cooler|\bdown\b|lower/.test(t)) v = cur - 1;
    if (v === null) return reply(`The heating is set to ${fmt(cur)} °C. The room is at ${thermo.values.temperatureValue} °C.`, [{ t: "control", deviceId: thermo.id }]);
    v = Math.round(v * 2) / 2;
    const a = api.write(thermo.id, { type: "targetTemperature", value: v });
    if (a.status === "blocked") return reply(`The heating stayed at ${fmt(cur)} °C: ${a.reason?.replace(/^blocked: /, "")}.`, [{ t: "control", deviceId: thermo.id }], [a]);
    return reply(`Heating set to ${fmt(v)} °C. The room is at ${thermo.values.temperatureValue} °C now.`, [{ t: "control", deviceId: thermo.id }, ...notes([a])], [a]);
  }

  if (/plug|kettle|\btv\b|washing|socket/.test(t)) {
    const plug = plugNamed(api, t);
    if (!plug) return reply("This home has no plug I can reach.");
    if (/^(is|was|are)\b/.test(t)) return reply(`The ${plug.name.toLowerCase()} plug is ${plug.values.power}.`, [{ t: "control", deviceId: plug.id }]);
    const a = api.write(plug.id, { type: "power", value: /\boff\b/.test(t) ? "off" : "on" });
    return reply(`${plug.name} ${plug.values.power}.`, [{ t: "control", deviceId: plug.id }, ...notes([a])], [a]);
  }

  if (/device|everything ok|\bok\b|okay|battery|signal|smoke|camera|\bhub\b|working/.test(t)) {
    return reply("Two things need a look: the security hub’s battery is at 20%, and the smoke alarm’s signal is weak.", [{ t: "devices" }]);
  }

  if (/anyone|anybody|nobody|someone|\bhome\b|empty/.test(t)) {
    const home = api.house.people?.home ?? [];
    const who = home.length ? `${home.map((x) => x.name).join(" and ")} ${home.length > 1 ? "are" : "is"} home.` : "Looks like nobody is home. The front door closed at 08:41 and nothing has moved since.";
    return reply(who, status.byAlfred ? [p(`That’s why I switched to ${status.title} at ${hhmm(status.since)}.`)] : []);
  }

  if (/temperature|how warm|how cold|humid|climate/.test(t)) {
    const room = api.find("temperatureSensor");
    return reply(`${room?.values.temperatureValue ?? "?"} °C and ${room?.values.humidityValue ?? "?"}% humidity.${thermo ? ` The heating is set to ${fmt(Number(thermo.values.targetTemperature))} °C.` : ""}`);
  }

  if (/door/.test(t)) return reply("The front door is closed. It last opened at 08:41, for about 30 s.");
  if (/^(hi|hello|hey|good (morning|afternoon|evening)|ol[aá]|bom dia)\b/.test(t)) return reply("Good morning. Tell me what to switch, or ask what I’ve planned.");
  if (/thank|obrigad/.test(t)) return reply("Of course.");
  return reply(`I can switch the lamp${thermo ? ", the plugs and the heating" : " and the plug"}, change the home’s mode, and skip, move or stop what I’ve planned. Try “I’m home” or “dim the lamp to 30%”.`);
}

async function switchTo(api: MockHomeApi, mode: Mode, lead: string): Promise<ChatReply> {
  if (api.house.status.title === MODE_TITLES[mode]) return reply(`You’re already in ${MODE_TITLES[mode]}.`);
  const actions = await api.applyMode(MODE_TITLES[mode], modeWrites(api, mode));
  const blocked = blockedLine(actions, api);
  return reply(`${lead}Switched to ${MODE_TITLES[mode]}.`, [...(blocked ? [p(blocked)] : []), ...notes(actions)], actions);
}

function ordinal(n: number): string {
  return ["zeroth", "first", "second", "third", "fourth", "fifth"][n] ?? `${n}th`;
}

/** For tests: the target range in words. */
export const HEAT_RANGE = `${TARGET.min}–${TARGET.max} °C`;
