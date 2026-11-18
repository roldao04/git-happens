import { appearsAt, DAY_MS, type SensorNow } from "../data/stateAt";
import { formatDate, sensorTitle } from "../data/words";
import { Chevron } from "./Chevron";
import { sensorHref } from "./sensorHref";

const SHOWN = 3;

function when(appears: number, t: number): string {
  const today = t - (t % DAY_MS);
  if (appears >= today) return "today";
  if (appears >= today - DAY_MS) return "yesterday";
  return formatDate(appears);
}

export function NewFromAlfred({ items, t }: { items: SensorNow[]; t: number }) {
  const shown = items.slice(0, SHOWN);
  return (
    <section className="sec" aria-labelledby="h-new">
      <div className="sec-t">
        <h2 id="h-new">New from Alfred</h2>
        <a className="more" href="#/learned">All learned</a>
      </div>
      {shown.length === 0 ? (
        <p className="note">Nothing learned yet. Alfred is still watching.</p>
      ) : (
        <div className="rows no-time">
          {shown.map(({ sensor, justLearned }) => (
            <a key={sensor.id} href={sensorHref(sensor.id)}>
              <div className="t">
                <b className="serif">
                  {justLearned && <i className="new-dot" aria-hidden="true" />}
                  {sensorTitle(sensor).text}
                </b>
                <span>{`Learned ${when(appearsAt(sensor), t)} · around ${sensor.pattern.time_of_day}`}</span>
              </div>
              <span className="a">Open<Chevron /></span>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}
