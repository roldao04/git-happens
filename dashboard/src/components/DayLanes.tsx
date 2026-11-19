import { useRef, useState, type PointerEvent } from "react";
import { DAY_MS } from "../data/stateAt";
import { ticksFor, timeAt, zoomBetween, type DayView, type Lane, type Span, type Zoom } from "../data/timeline";
import { formatClock } from "../data/words";

const times = (n: number) => (n === 1 ? "1 time" : `${n} times`);

function uses(spans: Span[]): number {
  return spans.filter((s, i) => i === 0 || spans[i - 1]?.end !== s.start).length;
}

function laneWords({ device, spans }: Lane): string {
  const n = uses(spans);
  if (device.type === "contactSensor") return `${device.name}: ${n ? `opened ${times(n)}` : "not opened"}.`;
  if (device.type === "plug") return `${device.name}: ${n ? `in use ${times(n)}` : "not in use"}.`;
  return `${device.name}: ${n ? `on ${times(n)}` : "not on"}.`;
}

function clock(ms: number, dayStart: number): string {
  return ms - dayStart >= DAY_MS ? "24:00" : formatClock(ms).time;
}

function lit(color: string | undefined) {
  return color === undefined ? {} : { backgroundColor: `rgba(${color}, 0.45)`, boxShadow: `inset 0 0 0 1.5px rgb(${color})` };
}

function habits(n: number): string {
  return n === 1 ? "1 habit" : `${n} habits`;
}

interface Props { view: DayView; zoom: Zoom; onZoom: (zoom: Zoom) => void }

export function DayLanes({ view, zoom, onZoom }: Props) {
  const { dayStart, upTo, lanes, entries, gaps } = view;
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  const area = useRef<HTMLDivElement>(null);
  const whole = zoom.start === dayStart && zoom.end === dayStart + DAY_MS;
  const share = (ms: number) => (Math.min(Math.max(ms, zoom.start), zoom.end) - zoom.start) / (zoom.end - zoom.start);
  const pct = (ms: number) => `${share(ms) * 100}%`;
  const width = (from: number, to: number) => `${(share(to) - share(from)) * 100}%`;
  const inView = (from: number, to: number | null) => (to ?? upTo) >= zoom.start && from <= zoom.end;

  const strong = entries.filter((e) => !e.weak).length;
  const unwatched = gaps.map((g) => `Not watched ${clock(g.start, dayStart)}–${clock(g.end, dayStart)}.`);
  const showing = whole ? [] : [`Showing ${clock(zoom.start, dayStart)}–${clock(zoom.end, dayStart)}.`];
  const label = [...lanes.map(laneWords), `Alfred recognized ${habits(strong)}.`, ...unwatched, ...showing].join(" ");

  const at = (e: PointerEvent<HTMLDivElement>) => {
    const box = area.current?.querySelector(".lane-r")?.getBoundingClientRect();
    return box === undefined ? zoom.start : timeAt(e.clientX, box.left, box.width, zoom);
  };
  const start = (e: PointerEvent<HTMLDivElement>) => {
    const t = at(e);
    if ("setPointerCapture" in e.currentTarget) e.currentTarget.setPointerCapture(e.pointerId); // jsdom has none
    setDrag({ from: t, to: t });
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (drag !== null) setDrag({ from: drag.from, to: at(e) });
  };
  const end = (e: PointerEvent<HTMLDivElement>) => {
    if (drag === null) return;
    const next = zoomBetween(drag.from, at(e));
    setDrag(null);
    if (next !== null) onZoom(next);
  };

  const shade = gaps.filter((g) => inView(g.start, g.end)).map((g) => <span key={`gap-${g.start}`} className="gap" style={{ left: pct(g.start), width: width(g.start, g.end) }} />);
  const later = upTo < zoom.end ? <span className="future" style={{ left: pct(upTo) }} /> : null;
  const now = upTo < zoom.end && upTo >= zoom.start ? <span className="nowl" style={{ left: pct(upTo) }} /> : null;
  const sel = drag !== null && drag.from !== drag.to ? <span className="sel" style={{ left: pct(Math.min(drag.from, drag.to)), width: width(Math.min(drag.from, drag.to), Math.max(drag.from, drag.to)) }} /> : null;
  const ticks = ticksFor(zoom);
  const grid = ticks.map((t) => <span key={t} className="gl" style={{ left: pct(t) }} />);
  return (
    <div ref={area} className="lanes" role="img" aria-label={label} onPointerDown={start} onPointerMove={move} onPointerUp={end} onPointerCancel={() => setDrag(null)}>
      {lanes.map(({ device, spans }) => (
        <div key={device.id} className="lane">
          <span className="lane-l">{device.name}</span>
          <div className="lane-r">
            {grid}
            {spans.filter((s) => inView(s.start, s.end)).map((s) => (
              <span key={s.start} className={device.type === "contactSensor" ? "bar door" : "bar"} style={{ left: pct(s.start), width: width(s.start, s.end ?? upTo), ...lit(s.color) }} />
            ))}
            {shade}
            {later}
            {now}
            {sel}
          </div>
        </div>
      ))}
      <div className="lane">
        <span className="lane-l">Alfred</span>
        <div className="lane-r">
          {grid}
          {entries.filter((e) => inView(e.at, e.at)).map((e) => <span key={`${e.sensor.id}-${e.at}`} className={`dia${e.weak ? " ring" : ""}`} style={{ left: pct(e.at) }} />)}
          {shade}
          {later}
          {now}
          {sel}
        </div>
      </div>
      <div className="lane">
        <span />
        <div className="ticks2">
          {ticks.map((t) => {
            const edge = t === zoom.start ? "none" : t === zoom.end ? "translateX(-100%)" : "translateX(-50%)";
            return <span key={t} style={{ left: pct(t), transform: edge }}>{clock(t, dayStart)}</span>;
          })}
        </div>
      </div>
    </div>
  );
}
