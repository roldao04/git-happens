import { Later } from "../components/Later";
import { Shell } from "../components/Shell";
import { devicesHeadline, needsLook, statusRow } from "../data/devices";
import type { Recording } from "../data/recording";
import type { DeviceNow, StateAt } from "../data/stateAt";
import type { Playback } from "../playback/usePlayback";

const SWITCHABLE = new Set(["bulb", "plug"]);

function Row({ d }: { d: DeviceNow }) {
  const row = statusRow(d);
  return (
    <li>
      <div>
        <span className="n">{d.device.name}</span>
        {row.sub && <span className="s">{row.sub}</span>}
      </div>
      <div className="v">{row.value}</div>
    </li>
  );
}

export function Devices({ state, playback }: { rec: Recording; state: StateAt; playback: Playback }) {
  const concerns = needsLook(state.devices);
  const flagged = new Set(concerns.map((c) => c.device.id));
  const switchable = state.devices.filter((d) => SWITCHABLE.has(d.device.type));
  const reporting = state.devices.filter((d) => !SWITCHABLE.has(d.device.type) && !flagged.has(d.device.id));
  return (
    <Shell state={state} playback={playback} tab="devices">
      <section className="pg-head" aria-label="Summary">
        <h1>{devicesHeadline(state.devices.length, concerns.length)}</h1>
        <p>Alfred only reads them. It never switches anything.</p>
      </section>
      <div className="two">
        <div className="stack">
          {concerns.length > 0 && (
            <section className="sec" aria-labelledby="h-look">
              <div className="sec-t"><h2 id="h-look">Needs a look</h2></div>
              <ul className="devs">
                {concerns.map(({ device, issues }) => (
                  <li key={device.id}>
                    <span className="n">{device.name}</span>
                    <span className="issues">
                      {issues.map((i) => <span key={i} className="al-pill warn">{i}</span>)}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section className="sec" aria-labelledby="h-rep">
            <div className="sec-t"><h2 id="h-rep">Reporting</h2></div>
            <ul className="devs">{reporting.map((d) => <Row key={d.device.id} d={d} />)}</ul>
          </section>
        </div>
        <aside className="aside">
          <section className="box" aria-labelledby="h-sw">
            <h2 id="h-sw">Lamp and plug</h2>
            <ul className="devs">{switchable.map((d) => <Row key={d.device.id} d={d} />)}</ul>
            <Later title="Switching comes later">Alfred will ask before it turns anything on or off, and only after you say yes.</Later>
          </section>
        </aside>
      </div>
    </Shell>
  );
}
