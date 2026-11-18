import { clockOf, minuteOfDay, type DayRow } from "../data/days";

const SHOWN = 7;
const clock = (ms: number) => clockOf(minuteOfDay(ms));

function times(row: DayRow): string {
  const [only, ...rest] = row.seen;
  if (!only) return row.today ? "nothing yet" : "not seen";
  if (rest.length === 0 && only.end !== null) return `${clock(only.start)} to ${clock(only.end)}`;
  return row.seen.map((s) => clock(s.start)).join(", ");
}

function verdict(row: DayRow): string {
  if (row.seen.length > 0) return "seen";
  return row.today ? "not yet" : "not seen";
}

export function RecentDays({ rows }: { rows: DayRow[] }) {
  const newest = rows.slice(-SHOWN).reverse();
  return (
    <ul className="al-list recent-days">
      {newest.map((row) => (
        <li key={row.dayMs}>
          <time>{row.today ? "Today" : row.label}</time>
          <div className="al-what"><b>{times(row)}</b></div>
          <span className="al-aside">{verdict(row)}</span>
        </li>
      ))}
    </ul>
  );
}
