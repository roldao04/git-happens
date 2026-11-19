import { scaleFor, ticksFor, type DayView, type Measure, type Scale, type Zoom } from "../data/timeline";
import { amount, withUnit } from "./amount";

const MINUTE_MS = 60_000;
const tidy = (n: number) => Math.round(n * 100) / 100;

interface Side { measure: Measure; scale: Scale; side: "left" | "right" }

function heightOf(scale: Scale, value: number): number {
  return tidy(100 - ((value - scale.min) / (scale.max - scale.min)) * 100);
}

function range({ low, high, unit }: Measure): string {
  return withUnit(low === high ? amount(low) : `${amount(low)} to ${amount(high)}`, unit);
}

function Axis({ side }: { side: Side | undefined }) {
  if (side === undefined) return <div className="cl-right" />;
  const { measure, scale } = side;
  return (
    <div className={`cl-${side.side} ${measure.label.toLowerCase()}`}>
      {[...scale.ticks].reverse().map((t) => (
        <span key={t} style={{ top: `${heightOf(scale, t)}%` }}>{withUnit(amount(t), measure.unit)}</span>
      ))}
    </div>
  );
}

export function ClimateChart({ view, zoom }: { view: DayView; zoom: Zoom }) {
  const { dayStart, upTo, gaps } = view;
  const sides: Side[] = view.measures.slice(0, 2).map((measure, i) => ({
    measure, scale: scaleFor(measure.low, measure.high), side: i === 0 ? "left" : "right",
  }));
  const [left, right] = sides;
  if (left === undefined) return null;
  const minute = (ms: number) => tidy((ms - dayStart) / MINUTE_MS);
  const share = (ms: number) => (Math.min(Math.max(ms, zoom.start), zoom.end) - zoom.start) / (zoom.end - zoom.start);
  const pct = (ms: number) => `${share(ms) * 100}%`;
  const names = sides.map((s, i) => (i === 0 ? s.measure.label : s.measure.label.toLowerCase())).join(" and ");
  const label = [`${names}.`, ...sides.map((s) => `${s.measure.label}: ${range(s.measure)}, on the ${s.side}.`)].join(" ");
  return (
    <div className="climate" role="img" aria-label={label}>
      <Axis side={left} />
      <div className="cl-plot">
        {ticksFor(zoom).map((t) => <span key={`v-${t}`} className="gl" style={{ left: pct(t) }} />)}
        {left.scale.ticks.map((t) => <span key={`h-${t}`} className="hl" style={{ top: `${heightOf(left.scale, t)}%` }} />)}
        <svg viewBox={`${minute(zoom.start)} 0 ${minute(zoom.end) - minute(zoom.start)} 100`} preserveAspectRatio="none" aria-hidden="true">
          {sides.map(({ measure, scale }) => (
            <polyline key={measure.label} className={measure.label.toLowerCase()} points={measure.points.map((p) => `${minute(p.at)},${heightOf(scale, p.value)}`).join(" ")} vectorEffect="non-scaling-stroke" />
          ))}
        </svg>
        {gaps.filter((g) => g.end >= zoom.start && g.start <= zoom.end).map((g) => (
          <span key={`gap-${g.start}`} className="gap" style={{ left: pct(g.start), width: `${(share(g.end) - share(g.start)) * 100}%` }} />
        ))}
        {upTo < zoom.end && <span className="future" style={{ left: pct(upTo) }} />}
        {upTo < zoom.end && upTo >= zoom.start && <span className="nowl" style={{ left: pct(upTo) }} />}
      </div>
      <Axis side={right} />
    </div>
  );
}
