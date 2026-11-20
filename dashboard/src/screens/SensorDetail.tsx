import { DaysChart } from "../components/DaysChart";
import { FoundSteps } from "../components/FoundSteps";
import { FadingPill } from "../components/FadingPill";
import { Later } from "../components/Later";
import { Shell } from "../components/Shell";
import { RecentDays } from "../components/RecentDays";
import { recentDays } from "../data/days";
import type { Recording } from "../data/recording";
import type { SensorNow, StateAt } from "../data/stateAt";
import { evidence, foundSteps, sensorTitle } from "../data/words";
import type { Playback } from "../playback/usePlayback";

function Back() {
  return <a className="more back" href="#/learned">← Learned</a>;
}

function Note({ id, title, text }: { id: string; title: string; text: string }) {
  return (
    <section className="detail-note" aria-labelledby={id}>
      <h3 id={id}>{title}</h3>
      <p>{text}</p>
    </section>
  );
}

function Detail({ item, state }: { item: SensorNow; state: StateAt }) {
  const { sensor, readings } = item;
  const title = sensorTitle(sensor);
  const { time_of_day: usual, spread_min: spread, days_seen: seen, days_total: total } = sensor.pattern;
  const pct = Math.round(sensor.confidence * 100);
  const rows = recentDays(readings, state.t, state.startMs);
  return (
    <article className="detail">
      <header>
        <h1 className={`detail-title${title.unnamed ? " unnamed" : ""}`}>{title.text}</h1>
        <FadingPill sensor={sensor} />
        {title.unnamed ? (
          <p className="detail-evidence">{evidence(sensor.pattern.parts)}</p>
        ) : (
          sensor.explanation && <p className="al-looks">{sensor.explanation}</p>
        )}
      </header>
      <div className="detail-grid">
        <div className="detail-main">
          <section className="al-panel" aria-labelledby="when">
            <h3 id="when">When it happens</h3>
            <DaysChart rows={rows} usual={usual} spreadMin={spread} />
          </section>
          <section className="al-panel" aria-labelledby="recent">
            <h3 id="recent">Recent days</h3>
            <RecentDays rows={rows} />
          </section>
        </div>
        <aside className="detail-side">
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
          </section>
          {sensor.means && <Note id="means" title="What it means" text={sensor.means} />}
          {sensor.if_missing && <Note id="missing" title="If it doesn't happen" text={sensor.if_missing} />}
          <FoundSteps steps={foundSteps(sensor.pattern.parts, usual, spread)} />
          <p className="never">Alfred never turns anything on or off because of this sensor.</p>
          {title.unnamed ? (
            <Later title="Naming comes later">Alfred will ask the household what this is and use their word for it.</Later>
          ) : (
            <Later title="Asking the household comes later">Alfred guessed this name; it will ask the household and use their word.</Later>
          )}
        </aside>
      </div>
    </article>
  );
}

interface Props { rec: Recording; state: StateAt; playback: Playback; id: string }

export function SensorDetail({ rec, state, playback, id }: Props) {
  const item = [...state.cards, ...state.alsoNoticed].find((s) => s.sensor.id === id);
  const known = rec.sensors.some((s) => s.id === id);
  return (
    <Shell state={state} playback={playback} tab="learned" simulated={rec.meta.simulated}>
      <Back />
      {item ? (
        <Detail item={item} state={state} />
      ) : (
        <p className="al-muted">{known ? "Alfred hasn't learned this one yet at this point of the run" : "This sensor isn't in the recording"}</p>
      )}
    </Shell>
  );
}
