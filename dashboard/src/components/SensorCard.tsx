import type { SensorNow } from "../data/stateAt";
import { evidence, sensorTitle } from "../data/words";
import { sensorHref } from "./sensorHref";
import { FadingPill } from "./FadingPill";

function daysText({ sensor, daysSoFar }: SensorNow, atEnd: boolean): string {
  const { days_seen: seen, days_total: total } = sensor.pattern;
  if (atEnd && seen !== undefined && total !== undefined) return `${seen} of ${total} days`;
  return `seen on ${daysSoFar} ${daysSoFar === 1 ? "day" : "days"} so far`;
}

export function SensorCard({ item, atEnd }: { item: SensorNow; atEnd: boolean }) {
  const { sensor, justLearned } = item;
  const title = sensorTitle(sensor);
  const spread = sensor.pattern.spread_min;
  return (
    <a className={`al-card${justLearned ? " just-learned" : ""}`} href={sensorHref(sensor.id)}>
      <div className="al-top">
        <div>
          <h3 className={title.unnamed ? "unnamed" : undefined}>{title.text}</h3>
          {sensor.explanation && <p className="al-looks">{`Looks like ${sensor.explanation}`}</p>}
        </div>
        <div className="al-row">
          {justLearned && <span className="al-new">Learned today</span>}
          <FadingPill sensor={sensor} />
        </div>
      </div>
      <div className="al-when">
        <strong>{`around ${sensor.pattern.time_of_day}`}</strong>
        {spread !== undefined && <span>{`± ${spread} min`}</span>}
      </div>
      <p className="al-evidence">{evidence(sensor.pattern.parts)}</p>
      <div className="al-foot">
        <span className="al-muted al-tnum">{daysText(item, atEnd)}</span>
        <span className="al-pct">{`${Math.round(sensor.confidence * 100)}%`}</span>
      </div>
    </a>
  );
}
