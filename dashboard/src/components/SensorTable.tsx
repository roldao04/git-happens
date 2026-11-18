import { minuteOfDay } from "../data/days";
import { toMs } from "../data/recording";
import type { SensorNow } from "../data/stateAt";
import { evidence, sensorTitle } from "../data/words";
import { DayBar } from "./DayBar";
import { FadingPill } from "./FadingPill";
import { sensorHref } from "./sensorHref";

function days({ sensor, daysSoFar }: SensorNow, atEnd: boolean): { n: string; label: string } {
  const { days_seen: seen, days_total: total } = sensor.pattern;
  if (atEnd && seen !== undefined && total !== undefined) return { n: `${seen} of ${total}`, label: "days seen" };
  return { n: `${daysSoFar} ${daysSoFar === 1 ? "day" : "days"}`, label: "seen so far" };
}

function description(item: SensorNow): string {
  const { sensor } = item;
  const { time_of_day: usual, spread_min: spread } = sensor.pattern;
  const looks = sensor.explanation ? `Looks like ${sensor.explanation}. ` : "";
  const around = spread === undefined ? `Around ${usual}.` : `Around ${usual}, give or take ${spread} min.`;
  return `${looks}${evidence(sensor.pattern.parts)}. ${around}`;
}

export function SensorTable({ items, atEnd }: { items: SensorNow[]; atEnd: boolean }) {
  return (
    <div className="lt">
      <div className="lt-head" aria-hidden="true">
        <span>Sensor</span>
        <span>Usual time of day</span>
        <span>Seen</span>
        <span>Confidence</span>
      </div>
      {items.map((item) => {
        const { sensor, justLearned, readings } = item;
        const title = sensorTitle(sensor);
        const d = days(item, atEnd);
        return (
          <a key={sensor.id} className={`lt-row${justLearned ? " just-learned" : ""}`} href={sensorHref(sensor.id)}>
            <div>
              <span className={`nm${title.unnamed ? " unnamed" : ""}`}>
                {justLearned && <i className="new-dot" aria-hidden="true" />}
                {title.text}
                {justLearned && <span className="al-new">Learned today</span>}
                <FadingPill sensor={sensor} />
              </span>
              <span className="ds">{description(item)}</span>
            </div>
            <DayBar usual={sensor.pattern.time_of_day} spreadMin={sensor.pattern.spread_min} hits={readings.map((r) => minuteOfDay(toMs(r.start)))} />
            <span className="num">{d.n}<small>{d.label}</small></span>
            <span className="num">{`${Math.round(sensor.confidence * 100)}%`}<small>confidence</small></span>
          </a>
        );
      })}
    </div>
  );
}
