import type { DayView, Lane } from "../data/timeline";
import { DAY_MS } from "../data/stateAt";

const pct = (ms: number, dayStart: number) => `${(Math.min(Math.max(ms - dayStart, 0), DAY_MS) / DAY_MS) * 100}%`;
const times = (n: number) => (n === 1 ? "1 time" : `${n} times`);
const HOURS = [0, 6, 12, 18, 24];

function laneWords({ device, spans }: Lane): string {
  const n = spans.length;
  if (device.type === "contactSensor") return `${device.name}: ${n ? `opened ${times(n)}` : "not opened"}.`;
  if (device.type === "plug") return `${device.name}: ${n ? `in use ${times(n)}` : "not in use"}.`;
  return `${device.name}: ${n ? `on ${times(n)}` : "not on"}.`;
}

function habits(n: number): string {
  return n === 1 ? "1 habit" : `${n} habits`;
}

export function DayLanes({ view }: { view: DayView }) {
  const { dayStart, upTo, dayEnd, lanes, entries } = view;
  const strong = entries.filter((e) => !e.weak).length;
  const label = [...lanes.map(laneWords), `Alfred recognized ${habits(strong)}.`].join(" ");
  const future = upTo < dayEnd ? <span className="future" style={{ left: pct(upTo, dayStart) }} /> : null;
  const now = upTo < dayEnd ? <span className="nowl" style={{ left: pct(upTo, dayStart) }} /> : null;
  const grid = HOURS.map((h) => <span key={h} className="gl" style={{ left: `${(h / 24) * 100}%` }} />);
  return (
    <div className="lanes" role="img" aria-label={label}>
      {lanes.map(({ device, spans }) => (
        <div key={device.id} className="lane">
          <span className="lane-l">{device.name}</span>
          <div className="lane-r">
            {grid}
            {spans.map((s) =>
              device.type === "contactSensor" ? (
                <span key={s.start} className="tick" style={{ left: pct(s.start, dayStart) }} />
              ) : (
                <span key={s.start} className="bar" style={{ left: pct(s.start, dayStart), width: `calc(${pct(s.end ?? upTo, dayStart)} - ${pct(s.start, dayStart)})` }} />
              ),
            )}
            {future}
            {now}
          </div>
        </div>
      ))}
      <div className="lane">
        <span className="lane-l">Alfred</span>
        <div className="lane-r">
          {grid}
          {entries.map((e) => <span key={`${e.sensor.id}-${e.at}`} className={`dia${e.weak ? " ring" : ""}`} style={{ left: pct(e.at, dayStart) }} />)}
          {future}
          {now}
        </div>
      </div>
      <div className="lane">
        <span />
        <div className="ticks2">
          {HOURS.map((h) => <span key={h} style={{ left: `${(h / 24) * 100}%` }}>{`${String(h).padStart(2, "0")}:00`}</span>)}
        </div>
      </div>
    </div>
  );
}
