import { chartPlace, chartSpan, clockOf, minuteOfDay, toMin, type DayRow } from "../data/days";

interface Props { rows: DayRow[]; usual: string; spreadMin?: number }

function firstTime(row: DayRow): string {
  const first = row.seen[0];
  if (first) return clockOf(minuteOfDay(first.start));
  return row.today ? "not yet" : "missed";
}

export function DaysChart({ rows, usual, spreadMin }: Props) {
  const centre = toMin(usual);
  const span = chartSpan(spreadMin);
  const from = spreadMin === undefined ? null : clockOf(centre - spreadMin);
  const to = spreadMin === undefined ? null : clockOf(centre + spreadMin);
  const left = spreadMin === undefined ? 0 : chartPlace(centre - spreadMin, usual, span);
  const right = spreadMin === undefined ? 0 : chartPlace(centre + spreadMin, usual, span);
  const count = rows.filter((r) => r.seen.length > 0).length;
  const usually = from && to ? `usually between ${from} and ${to}` : `usually around ${usual}`;
  return (
    <div className="days-chart">
      <div role="img" aria-label={`seen on ${count} of the last ${rows.length} days, ${usually}`}>
        {rows.map((row) => (
          <div key={row.dayMs} className={`chart-row${row.today ? " today" : ""}`}>
            <span className="chart-day">{row.today ? "Today" : row.label}</span>
            <div className="al-track">
              <span className="al-axis" />
              <span className="al-hour" style={{ left: "50%" }} />
              {spreadMin !== undefined && <span className="al-window" style={{ left: `${left}%`, width: `${right - left}%` }} />}
              {row.seen.map((s) => (
                <span key={s.start} className={`al-hit${row.today ? " today" : ""}`} style={{ left: `${chartPlace(minuteOfDay(s.start), usual, span)}%` }} />
              ))}
            </div>
            <span className={`chart-first${row.seen.length === 0 ? " al-muted" : ""}`}>{firstTime(row)}</span>
          </div>
        ))}
      </div>
      <div className="chart-axis" aria-hidden="true">
        {[-1, -0.5, 0, 0.5, 1].map((k) => (
          <span key={k} style={{ left: `${(k + 1) * 50}%` }}>{clockOf(centre + k * span)}</span>
        ))}
      </div>
      <div className="al-legend">
        <span><i className="swatch" />Seen</span>
        <span><i className="swatch ring" />Today</span>
        {from && to && <span><i className="swatch band" />{`Usual window, ${from} to ${to}`}</span>}
      </div>
    </div>
  );
}
