import type { DeviceNow, Latest } from "../data/stateAt";
import { deviceRow, deviceWords, formatClock } from "../data/words";

export function DevicesBox({ devices, latest = null }: { devices: DeviceNow[]; latest?: Latest | null }) {
  const rows = devices.flatMap((d) => {
    const row = deviceRow(d);
    return row ? [{ d, row }] : [];
  });
  return (
    <section className="box" aria-labelledby="h-dev">
      <h2 id="h-dev">Devices</h2>
      {latest && (
        <p className="latest">
          <span className="al-label">{`Latest · ${formatClock(latest.at).time}`}</span>
          <b>{`${latest.device.name}: ${deviceWords(latest.device.type, latest.capability, latest.value)}`}</b>
        </p>
      )}
      <ul className="devs">
        {rows.map(({ d, row }) => (
          <li key={d.device.id} className={d.changedAt !== null ? "just-changed" : undefined}>
            <div>
              <span className="n">{d.device.name}</span>
              {row.sub && <span className="s">{row.sub}</span>}
            </div>
            <div className="v">{row.value}</div>
          </li>
        ))}
      </ul>
    </section>
  );
}
