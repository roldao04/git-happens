import { minuteOfDay } from "../data/days";
import { toMs } from "../data/recording";
import { sensorHref } from "../data/sensorHref";
import type { SensorNow } from "../data/stateAt";
import { sensorTitle } from "../data/words";
import { Chevron } from "./Chevron";
import { DayBar } from "./DayBar";
import { FadingPill } from "./FadingPill";

export function SensorTable({ items, label }: { items: SensorNow[]; label: string }) {
  return (
    <ul className="lt" aria-label={label}>
      {items.map(({ sensor, justLearned, readings }) => {
        const title = sensorTitle(sensor);
        return (
          <li key={sensor.id}>
            <a className={`lt-row${justLearned ? " just-learned" : ""}`} href={sensorHref(sensor.id)}>
              <span className={`nm${title.unnamed ? " unnamed" : ""}`}>
                {justLearned && <i className="new-dot" aria-hidden="true" />}
                {title.text}
                {justLearned && <span className="al-new">Learned today</span>}
                <FadingPill sensor={sensor} />
              </span>
              <span className="when">{`around ${sensor.pattern.time_of_day}`}</span>
              <DayBar usual={sensor.pattern.time_of_day} spreadMin={sensor.pattern.spread_min} hits={readings.map((r) => minuteOfDay(toMs(r.start)))} />
              <Chevron />
            </a>
          </li>
        );
      })}
    </ul>
  );
}
