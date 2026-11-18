import type { SensorNow } from "../data/stateAt";
import { evidence, sensorTitle } from "../data/words";
import { sensorHref } from "./sensorHref";
import { FadingPill } from "./FadingPill";

export function AlsoNoticed({ items }: { items: SensorNow[] }) {
  if (items.length === 0) return null;
  return (
    <details className="also">
      <summary>{`Also noticed (${items.length})`}</summary>
      <div className="al-list no-time">
        {items.map(({ sensor }) => (
          <a key={sensor.id} href={sensorHref(sensor.id)}>
            <div className="al-what">
              <b className="al-sensor">{sensorTitle(sensor).text}</b>
              <span>{evidence(sensor.pattern.parts)}</span>
            </div>
            <span className="al-aside">
              <FadingPill sensor={sensor} />
              {`${Math.round(sensor.confidence * 100)}%`}
            </span>
          </a>
        ))}
      </div>
    </details>
  );
}
