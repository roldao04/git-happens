import { controlsFor } from "../controls";
import type { Device, People, PlanItem, Value } from "../types";

// Two houses for the mock: the real Altice kit (7 devices) and the simulated demo house from master
// (source/simulate.py PRESETS["demo"]: three named plugs, a thermostat, a router with phones).
// Ids and capabilities are the real ones (source/sim_data/home_template.json). The moment is the same in
// both: 09:12, the house quiet since 08:41, and Alfred switched it to "Out for the day" at 08:45.

export type HouseName = "demo" | "kit";
/** A staged moment for the stage (?moment=bedtime): Night has just started and the window is open. */
export type Moment = "bedtime";

/** What a habit learned to expect of a device when it starts (source/learn/expectations.py). */
export interface Expectation {
  decisionId: string;
  habitId: string;
  habit: string;
  /** Short name in the alert's id. */
  tag: string;
  deviceId: string;
  capability: string;
  value: Value;
}

export interface MockHouse {
  household: string;
  now: string;
  devices: Device[];
  people: People | null;
  status: {
    title: string;
    since: string;
    why: string;
    decisionId: string | null;
    byAlfred: boolean;
    /** The values before the status began; changes are worked out against these. */
    before: Record<string, Record<string, Value>>;
  };
  /** The mode the home was in before, for undo. */
  previousTitle: string;
  plan: PlanItem[];
  /** Times this week a person overruled "Out for the day". */
  overrides: number;
  expects: Expectation[];
  /** When a device last changed to its current value, for "open since". */
  changedAt: Record<string, string>;
}

const ID = {
  light: "90654832-7bca-47dd-89f3-d6e0b5f0a9ee",
  plug: "a291277c-8ba5-480a-b3a3-35b5f49bb459",
  tv: "3e0c1f52-7d4b-4c8e-9a61-2f5b8d0e7c14",
  washer: "8b7d2a90-15c3-4f6e-b2d8-6a4e9c1f0d37",
  thermostat: "5d2e8f1a-9c47-4b3d-8e65-0f7a1c2b9d48",
  contact: "276b9ad8-89ce-491a-a177-fe42358bee1c",
  window: "7c1e4b2a-3d5f-4a6b-9e8c-1f2a3b4c5d6e",
  smoke: "e4566929-1477-4023-8095-af904a0befe3",
  climate: "bb553b28-fdb1-4f77-b7e9-b0972d50ab15",
  hub: "c5c8e959-2a0b-4c1b-87f4-c285afa50e00",
  camera: "4f4f2408-ffc2-4543-a42b-3757133a6df4",
  alarm: "home-security",
} as const;
export const DEVICE_IDS = ID;

const NOW = "2026-09-21T09:12:00";

function device(id: string, type: string, name: string, values: Record<string, Value>, lastSeen = "2026-09-21T09:11:40"): Device {
  return { id, type, name, values, controls: controlsFor(type), reachable: true, lastSeen };
}

function shared(): Device[] {
  return [
    device(ID.light, "bulb", "Light", { power: "off", brightness: 30, temperature: 0, workMode: "white" }),
    device(ID.contact, "contactSensor", "Front door", { sensorState: "normal", batteryStatus: "high", signalStrength: "high", tamperState: "normal" }, "2026-09-21T08:41:38"),
    device(ID.climate, "temperatureSensor", "Climate", { temperatureValue: 19.7, humidityValue: 57.4, batteryLevel: 100 }),
    device(ID.smoke, "smokeSensor", "Smoke alarm", { sensorState: "normal", batteryStatus: "high", signalStrength: "low", tamperState: "normal" }),
    device(ID.camera, "camera", "Camera", { sdRecording: true, motionDetected: false }),
    device(ID.hub, "gateway", "Security hub", { batteryLevel: 20, chargeState: true, cellularSignalStrength: "bad", wifiSignalStrength: "good", alarmState: "normal" }),
    device(ID.alarm, "security", "Alarm", { status: "DISARMED", isAlarmed: false }),
  ];
}

const WHY = "The front door closed at 08:41, then nothing moved for 4 min. You go out around now on 8 of 18 days.";

function plan(withHeat: boolean): PlanItem[] {
  const item = (id: string, at: string, title: string, does: string, reason: string, confidence: number, mode: "do" | "suggest" = "do"): PlanItem =>
    ({ id, at, mode, status: "planned", title, does, reason, confidence, answer: null });
  return [
    item("decision-21", "2026-09-21T18:32:00", "Evening", withHeat ? "Lamp warm 60%, heating 21 °C" : "Lamp warm 60%",
      "You're usually home by 18:30 on weekdays (17 of 18 days).", 0.94),
    item("decision-22", "2026-09-21T23:10:00", "Night", withHeat ? "Lamp and kettle plug off, heating 18 °C" : "Lamp and plug off",
      "Asleep from around 23:07 (17 of 19 nights).", 0.89),
    item("decision-23", "2026-09-22T07:45:00", "Morning", withHeat ? "Lamp warm 30%, heating 20 °C" : "Lamp warm 30%",
      "Up around 07:59 on weekdays (15 of 18 days).", 0.83),
    withHeat
      ? item("decision-24", "2026-09-21T17:50:00", "Warm up before Ana gets home", "Heating to 21 °C at 17:50",
        "Ana usually gets home around 18:20, and the house takes about 30 min to warm up.", 0.62, "suggest")
      : item("decision-24", "2026-09-21T18:20:00", "Light for coming home", "Lamp warm 60% at 18:20",
        "Someone usually comes home around 18:20 (9 of 18 days).", 0.58, "suggest"),
  ];
}

export function makeHouse(name: HouseName, moment?: Moment): MockHouse {
  if (name === "kit") {
    return {
      household: "Hack 1 (the Altice kit)",
      now: NOW,
      devices: [
        ...shared().slice(0, 1),
        device(ID.plug, "plug", "Plug", { power: "off", instantPower: 0 }),
        ...shared().slice(1),
      ],
      people: null,
      status: {
        title: "Out for the day", since: "2026-09-21T08:45:00", why: WHY, decisionId: "decision-20", byAlfred: true,
        before: { [ID.light]: { power: "on", brightness: 30 }, [ID.plug]: { power: "on" } },
      },
      previousTitle: "Morning",
      plan: plan(false),
      overrides: 1,
      expects: [],
      changedAt: {},
    };
  }
  const house: MockHouse = {
    household: "Demo house (simulated)",
    now: NOW,
    devices: [
      ...shared().slice(0, 1),
      device(ID.plug, "plug", "Kettle", { power: "off", instantPower: 0 }),
      device(ID.tv, "plug", "TV", { power: "off", instantPower: 0 }),
      device(ID.washer, "plug", "Washing machine", { power: "on", instantPower: 2 }),
      device(ID.thermostat, "thermostat", "Thermostat", { temperatureValue: 19.7, targetTemperature: 17, mode: "heat", heatingState: "off" }),
      ...shared().slice(1),
      device(ID.window, "contactSensor", "Living-room window", { sensorState: "normal", batteryStatus: "high", signalStrength: "high", tamperState: "normal" }),
    ],
    people: { home: [], guests: 0 },
    status: {
      title: "Out for the day", since: "2026-09-21T08:45:00", why: WHY, decisionId: "decision-20", byAlfred: true,
      before: { [ID.light]: { power: "on", brightness: 30 }, [ID.plug]: { power: "on" }, [ID.thermostat]: { targetTemperature: 20 } },
    },
    previousTitle: "Morning",
    plan: plan(true),
    overrides: 1,
    expects: [{ decisionId: "decision-22", habitId: "habit-night", habit: "Night", tag: "window", deviceId: ID.window, capability: "sensorState", value: "normal" }],
    changedAt: { [ID.window]: "2026-09-20T22:31:00" },
  };
  return moment === "bedtime" ? bedtime(house) : house;
}

/** 23:10: Alfred has just started Night, and the living-room window has been open since 21:10. */
function bedtime(house: MockHouse): MockHouse {
  const now = "2026-09-21T23:10:00";
  const set = (id: string, values: Record<string, Value>) => {
    const d = house.devices.find((x) => x.id === id);
    if (d) d.values = { ...d.values, ...values };
  };
  set(ID.window, { sensorState: "alarm" });
  set(ID.light, { power: "off" });
  set(ID.thermostat, { targetTemperature: 18 });
  for (const p of house.plan) if (p.at < now && p.mode === "do") p.status = "done";
  for (const p of house.plan) if (p.at < now && p.mode === "suggest") p.status = "skipped";
  return {
    ...house,
    now,
    people: { home: DEMO_PHONES.map((p) => ({ ...p })), guests: 0 },
    status: {
      title: "Night", since: now, why: "Asleep from around 23:07 (17 of 19 nights).", decisionId: "decision-22", byAlfred: true,
      before: { [ID.light]: { power: "on", brightness: 60 }, [ID.thermostat]: { targetTemperature: 21 } },
    },
    previousTitle: "Evening",
    changedAt: { ...house.changedAt, [ID.window]: "2026-09-21T21:10:00" },
  };
}

/** Who lives in the demo house: their phones, for "I'm home". */
export const DEMO_PHONES = [{ id: "c-8f3a", name: "Ana" }, { id: "c-2b71", name: "João" }];
