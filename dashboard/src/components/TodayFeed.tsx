import type { FeedItem } from "../data/stateAt";
import { deviceWords, evidence, formatClock, sensorTitle } from "../data/words";
import { sensorHref } from "./sensorHref";

const SHOWN = 6;

function Row({ item }: { item: FeedItem }) {
  const time = <time>{formatClock(item.at).time}</time>;
  if (item.kind === "habit")
    return (
      <li>
        {time}
        <a className="t" href={sensorHref(item.sensor.id)}>
          <b>{sensorTitle(item.sensor).text}</b>
          <span>{evidence(item.sensor.pattern.parts)}</span>
        </a>
        <span className="a">learned habit</span>
      </li>
    );
  return (
    <li>
      {time}
      <div className="t"><b>{`${item.device.name} ${deviceWords(item.device.type, item.capability, item.value)}`}</b></div>
    </li>
  );
}

export function TodayFeed({ items }: { items: FeedItem[] }) {
  const shown = items.slice(0, SHOWN);
  return (
    <section className="sec" aria-labelledby="h-today">
      <div className="sec-t"><h2 id="h-today">Today so far</h2></div>
      {shown.length === 0 ? (
        <p className="note">Nothing yet today.</p>
      ) : (
        <ul className="rows">
          {shown.map((item, i) => (
            <Row key={`${item.at}-${i}`} item={item} />
          ))}
        </ul>
      )}
      {items.length > SHOWN && <p className="note">{`and ${items.length - SHOWN} earlier`}</p>}
    </section>
  );
}
