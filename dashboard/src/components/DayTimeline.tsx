const DAY_MIN = 24 * 60;

export interface Piece { left: number; width: number }

const pct = (min: number) => (min / DAY_MIN) * 100;

export function windowPieces(centreMin: number, spreadMin: number): Piece[] {
  const half = Math.min(spreadMin, DAY_MIN / 2);
  const from = centreMin - half;
  const to = centreMin + half;
  if (from < 0) return [{ left: pct(from + DAY_MIN), width: pct(-from) }, { left: 0, width: pct(to) }];
  if (to > DAY_MIN) return [{ left: pct(from), width: pct(DAY_MIN - from) }, { left: 0, width: pct(to - DAY_MIN) }];
  return [{ left: pct(from), width: pct(to - from) }];
}

function toMin(hhmm: string): number {
  const [h = 0, m = 0] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function clock(min: number): string {
  const m = ((Math.round(min) % DAY_MIN) + DAY_MIN) % DAY_MIN;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

interface Props { usual: string; spreadMin?: number; hits: number[]; nowMin: number | null }

export function DayTimeline({ usual, spreadMin, hits, nowMin }: Props) {
  const centre = toMin(usual);
  const label =
    spreadMin === undefined ? `usually around ${usual}` : `usually between ${clock(centre - spreadMin)} and ${clock(centre + spreadMin)}`;
  return (
    <div className="al-timeline" role="img" aria-label={label}>
      <div className="al-track">
        <span className="al-axis" />
        {[6, 12, 18].map((h) => (
          <span key={h} className="al-hour" style={{ left: `${pct(h * 60)}%` }} />
        ))}
        {spreadMin !== undefined &&
          windowPieces(centre, spreadMin).map((p) => <span key={p.left} className="al-window" style={{ left: `${p.left}%`, width: `${p.width}%` }} />)}
        {hits.map((m, i) => (
          <span key={i} className="al-hit" style={{ left: `${pct(m)}%` }} />
        ))}
        {nowMin !== null && <span className="al-now" style={{ left: `${pct(nowMin)}%` }} />}
      </div>
      <div className="al-ticks" aria-hidden="true">
        {[0, 6, 12, 18, 24].map((h) => (
          <span key={h} style={{ left: `${pct(h * 60)}%` }}>{String(h).padStart(2, "0")}</span>
        ))}
      </div>
    </div>
  );
}
