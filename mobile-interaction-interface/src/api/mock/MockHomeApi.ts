import type { HomeApi } from "../client";
import type { ActionResult, Change, ChatReply, ControlBody, Device, HomeSnapshot, PlanItem, Status, Value } from "../types";
import { respond } from "./brain";
import { checkWrite } from "./guard";
import { alertsOf } from "./alerts";
import { DEMO_PHONES, makeHouse, type HouseName, type MockHouse, type Moment } from "./houses";

/** A home in memory: the app's whole API, with the guard's rules and no network. */
export class MockHomeApi implements HomeApi {
  readonly kind = "mock" as const;
  readonly house: MockHouse;
  /** This laptop's voice model, through the voice-only server npm run demo starts: the demo house hears you too. */
  transcribe?: HomeApi["transcribe"];
  private overridden = false;

  constructor(readonly name: HouseName = "demo", private readonly delayMs = 250, moment?: Moment) {
    this.house = makeHouse(name, moment);
  }

  async getHome(): Promise<HomeSnapshot> {
    await this.wait();
    const h = this.house;
    const thermo = h.devices.find((d) => d.type === "thermostat");
    return clone({
      now: h.now,
      household: h.household,
      live: false,
      dryRun: true,
      lookOnly: false,
      devices: h.devices,
      people: h.people,
      climate: thermo
        ? { roomC: num(thermo.values.temperatureValue), targetC: num(thermo.values.targetTemperature), mode: str(thermo.values.mode), heating: str(thermo.values.heatingState) }
        : null,
    });
  }

  async getStatus(): Promise<Status> {
    await this.wait();
    const s = this.house.status;
    return clone({
      title: s.title,
      since: s.since,
      why: s.why,
      decisionId: s.decisionId,
      byAlfred: s.byAlfred && !this.overridden,
      changes: this.changes(),
      planned: this.house.plan.filter((p) => p.mode === "do" && (p.status === "planned" || p.answer === "skip")),
      suggestions: this.house.plan.filter((p) => p.mode === "suggest" && p.status === "planned" && p.answer === null),
      notSent: [],
      learning: null, // the demo house has been watched for weeks
      alerts: alertsOf(this.house),
    });
  }

  async setControl(deviceId: string, body: ControlBody): Promise<ActionResult> {
    await this.wait();
    return this.write(deviceId, body);
  }

  /** One write, checked like source/guard.py checks a person's write. */
  write(deviceId: string, body: ControlBody): ActionResult {
    const device = this.device(deviceId);
    const reason = checkWrite(device, body, this.heatNow());
    if (reason || !device) return { status: "blocked", verified: null, reason: reason ?? "blocked", deviceId, body };
    const reversed = this.reverses(device, body);
    device.values[body.type] = body.value;
    if (body.type === "power" && device.type === "plug" && body.value === "off") device.values.instantPower = 0;
    if (body.type === "brightness" && device.values.power === "off") device.values.power = "on";
    if (body.type === "targetTemperature") this.heating();
    const out: ActionResult = { status: "sent", verified: "confirmed", deviceId, body };
    if (reversed) out.note = this.countOverride();
    return out;
  }

  async skip(id: string): Promise<PlanItem> {
    return this.answerWith(id, (p) => { p.status = "skipped"; p.answer = "skip"; });
  }

  async unskip(id: string): Promise<PlanItem> {
    return this.answerWith(id, (p) => { p.status = "planned"; p.answer = null; });
  }

  async answer(id: string, yes: boolean): Promise<PlanItem> {
    return this.answerWith(id, (p) => {
      p.answer = yes ? "yes" : "no";
      if (yes) p.mode = "do";
      else p.status = "skipped";
    });
  }

  async undo(decisionId: string): Promise<ActionResult[]> {
    await this.wait();
    const s = this.house.status;
    if (s.decisionId !== decisionId || !s.byAlfred) return [];
    const now = this.snapshot(s.before);
    const results: ActionResult[] = [];
    for (const [id, caps] of Object.entries(s.before)) {
      const d = this.device(id);
      if (!d) continue;
      for (const [cap, value] of Object.entries(caps)) {
        d.values[cap] = value;
        results.push({ status: "sent", verified: "confirmed", deviceId: id, body: { type: cap, value } as ControlBody });
      }
    }
    this.heating();
    const counted = !this.overridden;
    const was = s.title;
    this.house.status = { title: this.house.previousTitle, since: this.house.now, why: `You undid ${was}.`, decisionId: null, byAlfred: false, before: now };
    this.house.previousTitle = was;
    this.overridden = false;
    if (counted) this.house.overrides += 1;
    return results;
  }

  async chat(text: string): Promise<ChatReply> {
    await this.wait(this.delayMs * 2);
    return respond(text, this);
  }

  // ---- for the mock's chat brain (brain.ts) ----

  device(id: string): Device | undefined {
    return this.house.devices.find((d) => d.id === id);
  }

  find(type: string, name?: string): Device | undefined {
    const all = this.house.devices.filter((d) => d.type === type);
    return (name ? all.find((d) => d.name.toLowerCase().includes(name)) : undefined) ?? all[0];
  }

  /** Switch the home to a mode a person asked for: each write goes through the guard. */
  async applyMode(title: string, writes: [string, ControlBody][]): Promise<ActionResult[]> {
    await this.wait();
    const shape: Record<string, Record<string, Value>> = {};
    for (const [id, b] of writes) (shape[id] ??= {})[b.type] = null;
    const before = this.snapshot(shape);
    const wasAlfreds = this.house.status.byAlfred && !this.overridden;
    const results = writes.map(([id, body]) => this.write(id, body));
    this.house.previousTitle = this.house.status.title;
    this.house.status = { title, since: this.house.now, why: "You switched to it.", decisionId: null, byAlfred: false, before };
    if (wasAlfreds && !this.overridden) this.house.overrides += 1;
    this.overridden = false;
    return results;
  }

  arrive(name = "Ana"): void {
    const phone = DEMO_PHONES.find((p) => p.name === name);
    if (this.house.people && phone && !this.house.people.home.some((p) => p.id === phone.id)) this.house.people.home.push(phone);
  }

  someoneHome(): boolean {
    return !!this.house.people?.home.length;
  }

  // ---- inside ----

  private changes(): Change[] {
    const out: Change[] = [];
    for (const [id, caps] of Object.entries(this.house.status.before)) {
      const d = this.device(id);
      if (!d) continue;
      for (const [cap, from] of Object.entries(caps)) {
        const to = d.values[cap] ?? null;
        if (from !== null && from !== to) out.push({ deviceId: id, device: d.name, capability: cap, from, to });
      }
    }
    return out;
  }

  /** A write that moves a value back toward what it was before Alfred changed it. */
  private reverses(d: Device, body: ControlBody): boolean {
    const s = this.house.status;
    if (!s.byAlfred || this.overridden) return false;
    const prev = s.before[d.id]?.[body.type];
    const cur = d.values[body.type];
    if (prev === undefined || prev === null || prev === cur) return false;
    if (typeof prev === "number" && typeof cur === "number" && typeof body.value === "number") return Math.sign(body.value - cur) === Math.sign(prev - cur);
    return body.value === prev;
  }

  private countOverride(): string {
    this.overridden = true;
    this.house.overrides += 1;
    const s = this.house.status;
    return `Noted. I switched to ${s.title} at ${s.since.slice(11, 16)} and you changed it, so I’ll count that as a no.` +
      (this.house.overrides >= 2 ? " One more this week and I’ll stop doing it on my own." : "");
  }

  private heatNow() {
    const t = this.house.devices.find((d) => d.type === "thermostat");
    return { target: t ? num(t.values.targetTemperature) : null, someoneHome: this.someoneHome() };
  }

  private heating(): void {
    const t = this.house.devices.find((d) => d.type === "thermostat");
    if (!t) return;
    const room = num(t.values.temperatureValue) ?? 0, target = num(t.values.targetTemperature) ?? 0;
    t.values.heatingState = t.values.mode !== "off" && target > room ? "on" : "off";
  }

  private snapshot(shape: Record<string, Record<string, Value>>): Record<string, Record<string, Value>> {
    const out: Record<string, Record<string, Value>> = {};
    for (const [id, caps] of Object.entries(shape)) {
      const d = this.device(id);
      if (!d) continue;
      out[id] = {};
      for (const cap of Object.keys(caps)) out[id]![cap] = d.values[cap] ?? null;
    }
    return out;
  }

  private async answerWith(id: string, change: (p: PlanItem) => void): Promise<PlanItem> {
    await this.wait();
    const p = this.house.plan.find((x) => x.id === id);
    if (!p) throw new Error(`no decision ${id}`);
    change(p);
    return clone(p);
  }

  private wait(ms = this.delayMs): Promise<void> {
    return ms > 0 ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve();
  }
}

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;
const num = (v: Value | undefined): number | null => (typeof v === "number" ? v : null);
const str = (v: Value | undefined): string | null => (typeof v === "string" ? v : null);
