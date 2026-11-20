import { clockOf, toMin } from "../data/days";

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

export function DayBar({ usual, spreadMin, hits }: { usual: string; spreadMin?: number; hits: number[] }) {
  const centre = toMin(usual);
  const label =
    spreadMin === undefined ? `usually around ${usual}` : `usually between ${clockOf(centre - spreadMin)} and ${clockOf(centre + spreadMin)}`;
  return (
    <div className="ts" role="img" aria-label={label}>
      {[25, 50, 75].map((left) => (
        <span key={left} className="h" style={{ left: `${left}%` }} />
      ))}
      {spreadMin !== undefined &&
        windowPieces(centre, spreadMin).map((p) => <span key={p.left} className="w" style={{ left: `${p.left}%`, width: `${p.width}%` }} />)}
      {hits.map((m, i) => (
        <span key={i} className="p" style={{ left: `${pct(m)}%` }} />
      ))}
    </div>
  );
}
