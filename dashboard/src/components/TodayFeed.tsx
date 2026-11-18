import type { FeedItem } from "../data/stateAt";
import { deviceWords, formatClock, sensorTitle } from "../data/words";

const SHOWN = 6;

function Row({ item }: { item: FeedItem }) {
  const time = <time>{formatClock(item.at).time}</time>;
  if (item.kind === "habit")
    return (
      <li>
        {time}
        <div className="al-what"><b>{`${sensorTitle(item.sensor).text} seen again`}</b></div>
        <span className="al-aside">learned habit</span>
      </li>
    );
  return (
    <li>
      {time}
      <div className="al-what"><b>{`${item.device.name} ${deviceWords(item.device.type, item.capability, item.value)}`}</b></div>
    </li>
  );
}

export function TodayFeed({ items }: { items: FeedItem[] }) {
  const shown = items.slice(0, SHOWN);
  return (
    <section className="today-feed" aria-labelledby="today-so-far">
      <h3 id="today-so-far">Today so far</h3>
      {shown.length === 0 ? (
        <p className="al-muted">Nothing yet today.</p>
      ) : (
        <ul className="al-list feed">
          {shown.map((item, i) => (
            <Row key={`${item.at}-${i}`} item={item} />
          ))}
        </ul>
      )}
      {items.length > SHOWN && <p className="al-muted">{`and ${items.length - SHOWN} earlier`}</p>}
    </section>
  );
}
