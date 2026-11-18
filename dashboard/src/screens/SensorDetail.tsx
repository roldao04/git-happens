import { Controls } from "../components/Controls";
import { DaysSeen } from "../components/DaysSeen";
import { DayTimeline } from "../components/DayTimeline";
import { Header } from "../components/Header";
import { toMs, type Recording } from "../data/recording";
import { DAY_MS, type SensorNow, type StateAt } from "../data/stateAt";
import { evidence, formatClock, formatDate, sensorTitle } from "../data/words";
import type { Playback } from "../playback/usePlayback";
import { FadingPill } from "../components/FadingPill";

const minuteOfDay = (ms: number) => (ms % DAY_MS) / 60_000;

function Back() {
  return <a className="al-btn quiet" href="#/">← Back</a>;
}

function Detail({ item, state }: { item: SensorNow; state: StateAt }) {
  const { sensor, readings } = item;
  const title = sensorTitle(sensor);
  const { time_of_day: usual, spread_min: spread, days_seen: seen, days_total: total } = sensor.pattern;
  const pct = Math.round(sensor.confidence * 100);
  const newest = [...readings].map((r) => toMs(r.start)).sort((a, b) => b - a);
  return (
    <article className="detail">
      <header>
        <h2 className={`detail-title${title.unnamed ? " unnamed" : ""}`}>{title.text}</h2>
        <FadingPill sensor={sensor} />
        {sensor.explanation && <p className="al-looks">{`Looks like ${sensor.explanation}`}</p>}
        <p className="detail-evidence">{evidence(sensor.pattern.parts)}</p>
      </header>
      <section className="al-panel detail-facts" aria-label="What Alfred learned">
        <p className="detail-when">
          <strong>{`around ${usual}`}</strong>
          {spread !== undefined && <span className="al-muted">{` ± ${spread} min`}</span>}
        </p>
        <p className="al-muted al-tnum">
          {state.atEnd && seen !== undefined && total !== undefined
            ? `seen on ${seen} of ${total} days`
            : `seen on ${item.daysSoFar} ${item.daysSoFar === 1 ? "day" : "days"} so far`}
        </p>
        <div className="al-conf">
          <span className="al-pct">{`${pct}%`}</span>
          <span className="al-bar" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span>
        </div>
        <span className="al-label">Time of day</span>
        <DayTimeline usual={usual} spreadMin={spread} hits={readings.map((r) => minuteOfDay(toMs(r.start)))} nowMin={minuteOfDay(state.t)} />
        <span className="al-label">Last 14 days</span>
        <DaysSeen readings={readings} t={state.t} />
      </section>
      <section className="al-panel" aria-labelledby="readings">
        <h2 id="readings">Seen so far</h2>
        <ul className="al-list readings">
          {newest.map((ms) => (
            <li key={ms}>
              <time>{formatDate(ms)}</time>
              <div className="al-what"><b>{formatClock(ms).time}</b></div>
            </li>
          ))}
        </ul>
      </section>
      <div className="al-later">
        <span className="al-pill">not built yet</span>
        <h3>Naming comes later</h3>
        <p>Alfred will ask the household what this is and use their word for it.</p>
      </div>
    </article>
  );
}

interface Props { rec: Recording; state: StateAt; playback: Playback; id: string }

export function SensorDetail({ rec, state, playback, id }: Props) {
  const item = [...state.cards, ...state.alsoNoticed].find((s) => s.sensor.id === id);
  const known = rec.sensors.some((s) => s.id === id);
  return (
    <>
      <Header state={state}>
        <Controls playback={playback} />
      </Header>
      <main className="stage">
        <Back />
        {item ? (
          <Detail item={item} state={state} />
        ) : (
          <p className="al-muted">{known ? "Alfred hasn't learned this one yet at this point of the run" : "This sensor isn't in the recording"}</p>
        )}
      </main>
    </>
  );
}
