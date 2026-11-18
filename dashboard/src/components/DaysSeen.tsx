import type { Reading } from "../data/recording";
import { DAY_MS } from "../data/stateAt";

const SHOWN_DAYS = 14;

export function DaysSeen({ readings, t }: { readings: Reading[]; t: number }) {
  const today = t - (t % DAY_MS);
  const seen = new Set(readings.map((r) => r.start.slice(0, 10)));
  const days = Array.from({ length: SHOWN_DAYS }, (_, i) => {
    const date = new Date(today - (SHOWN_DAYS - 1 - i) * DAY_MS).toISOString().slice(0, 10);
    return { date, seen: seen.has(date), today: i === SHOWN_DAYS - 1 };
  });
  const count = days.filter((d) => d.seen).length;
  return (
    <div>
      <div className="al-days" role="img" aria-label={`seen on ${count} of the last ${SHOWN_DAYS} days`}>
        {days.map((d) => (
          <span key={d.date} className={["al-day", d.seen ? "seen" : "", d.today ? "today" : ""].filter(Boolean).join(" ")} />
        ))}
      </div>
      <div className="al-days-caption" aria-hidden="true">
        <span>{`${SHOWN_DAYS} days ago`}</span>
        <span><b>{count}</b>{` of ${SHOWN_DAYS}`}</span>
        <span>today</span>
      </div>
    </div>
  );
}
