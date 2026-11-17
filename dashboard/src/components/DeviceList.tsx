import type { DeviceNow, Latest } from "../data/stateAt";
import { SHOWN } from "../data/shown";
import { deviceWords, formatClock } from "../data/words";


function words({ device, values }: DeviceNow): string {
  const lampOff = device.type === "bulb" && deviceWords("bulb", "power", values.power ?? null) === "off";
  return (SHOWN[device.type] ?? [])
    .filter((cap) => cap in values && !(lampOff && cap === "brightness"))
    .map((cap) => deviceWords(device.type, cap, values[cap] ?? null))
    .join(", ");
}

export function DeviceList({ devices, latest = null }: { devices: DeviceNow[]; latest?: Latest | null }) {
  const rows = devices.map((d) => ({ d, text: words(d) })).filter((r) => r.text !== "");
  return (
    <>
      {latest && (
        <p className="latest">
          <span className="al-label">{`Latest · ${formatClock(latest.at).time}`}</span>
          <b>{`${latest.device.name}: ${deviceWords(latest.device.type, latest.capability, latest.value)}`}</b>
        </p>
      )}
      <ul className="al-list no-time devices">
        {rows.map(({ d, text }) => (
          <li key={d.device.id}>
            <div className="al-what">
              <b>{d.device.name}</b>
              {d.changedAt !== null && <span className="changed">{`changed ${formatClock(d.changedAt).time}`}</span>}
            </div>
            <span className="al-aside">{text}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
