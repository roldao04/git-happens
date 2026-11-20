import { useEffect, useState } from "react";
import { toneOf } from "../api/controls";
import type { ActionResult, ControlBody, Device } from "../api/types";
import { useHome } from "../state/HomeProvider";
import { reading, whatOf } from "../words";

/** `what` is what the person asked for, as in "turn the lamp on": the app says it was done, or wasn't. */
export type OnResult = (r: ActionResult, d: Device, what: string) => void;

/** One device a person can change: its switch, brightness, tone, heating target and mode, as the API lists them. */
export function DeviceControl({ device, onResult }: { device: Device; onResult?: OnResult }) {
  const { setControl, pending, home } = useHome();
  const send = async (body: ControlBody) => {
    const r = await setControl(device.id, body);
    onResult?.(r, device, whatOf(device.name, body));
    return r;
  };
  const busy = pending.has(device.id); // waits for the answer: it only moves if the write was sent
  const off = !device.reachable || busy;
  const v = device.values;
  const sw = device.controls.find((c) => c.kind === "switch");
  const on = v.power === "on";
  return (
    <div className="ctl" data-device={device.id} aria-busy={busy}>
      <div className="ctl-head">
        <div className="al-what"><b>{device.name}</b><span>{reading(device, home?.now.slice(0, 10)).sub}</span></div>
        {sw && (
          <button className="al-toggle" type="button" role="switch" aria-checked={on} aria-label={device.name} disabled={off}
            onClick={() => void send({ type: "power", value: on ? "off" : "on" })} />
        )}
      </div>
      {device.controls.map((c) => {
        switch (c.kind) {
          case "level":
            return <Level key={c.kind} name={device.name} value={typeof v.brightness === "number" ? v.brightness : c.min} min={c.min} max={c.max} step={c.step} off={!on} disabled={off}
              onCommit={(n) => send({ type: "brightness", value: n })} />;
          case "tone": {
            const cur = toneOf(v.temperature);
            return (
              <div key={c.kind} className="seg" role="group" aria-label={`${device.name} colour`}>
                {c.options.map((o) => (
                  <button key={o.label} type="button" aria-pressed={cur.label === o.label} disabled={off}
                    onClick={() => void send({ type: "temperature", value: o.value })}>{o.label}</button>
                ))}
              </div>
            );
          }
          case "target": {
            const t = typeof v.targetTemperature === "number" ? v.targetTemperature : c.min;
            const to = (x: number) => void send({ type: "targetTemperature", value: Math.round(x / c.step) * c.step });
            return (
              <div key={c.kind} className="stepper">
                <button type="button" aria-label="Lower the heating" disabled={off || t <= c.min} onClick={() => to(t - c.step)}>−</button>
                <output aria-live="polite">{t.toFixed(1)}<small>°C</small></output>
                <button type="button" aria-label="Raise the heating" disabled={off || t >= c.max} onClick={() => to(t + c.step)}>+</button>
              </div>
            );
          }
          case "choice":
            return (
              <div key={c.kind} className="seg" role="group" aria-label="Heating mode">
                {c.options.map((m) => (
                  <button key={m} type="button" aria-pressed={v.mode === m} disabled={off} onClick={() => void send({ type: "mode", value: m })}>{m[0]!.toUpperCase() + m.slice(1)}</button>
                ))}
              </div>
            );
          default:
            return null;
        }
      })}
    </div>
  );
}

/** Brightness: moves freely, sends once when let go, and goes back if that wasn't sent. */
function Level({ name, value, min, max, step, off, disabled, onCommit }: {
  name: string; value: number; min: number; max: number; step: number; off: boolean; disabled: boolean; onCommit: (n: number) => Promise<ActionResult>;
}) {
  const [v, setV] = useState(value);
  useEffect(() => { setV(value); }, [value]);
  const commit = async () => {
    if (v === value) return;
    if ((await onCommit(v)).status !== "sent") setV(value);
  };
  return (
    <label className={`slider${off ? " is-off" : ""}`}>
      <span>Brightness</span>
      <input type="range" min={min} max={max} step={step} value={v} aria-label={`${name} brightness`} disabled={disabled}
        onChange={(e) => setV(Number(e.target.value))}
        onPointerUp={() => void commit()}
        onKeyUp={() => void commit()} />
      <b>{v}%</b>
    </label>
  );
}

/** The devices nobody changes from the phone: sensors, camera, hub, alarm. */
export function SensorList({ devices }: { devices: Device[] }) {
  const { home } = useHome();
  const today = home?.now.slice(0, 10);
  return (
    <ul className="al-list no-time sensors">
      {devices.map((d) => {
        const r = reading(d, today);
        return (
          <li key={d.id}>
            <div className="al-what"><b>{d.name}</b><span>{r.sub}</span></div>
            <span className="al-aside">
              {r.value}
              {r.pill && <span className={`al-pill ${r.pill.tone}`.trim()}>{r.pill.text}</span>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
