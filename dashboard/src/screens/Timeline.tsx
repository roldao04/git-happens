import { DayLanes } from "../components/DayLanes";
import { Shell } from "../components/Shell";
import { sensorHref } from "../components/sensorHref";
import type { Recording } from "../data/recording";
import type { StateAt } from "../data/stateAt";
import { dayView, type Entry } from "../data/timeline";
import { deviceWords, evidence, formatClock, formatDate, sensorTitle } from "../data/words";
import type { Playback } from "../playback/usePlayback";

function summary(entries: Entry[]): string {
  const strong = entries.filter((e) => !e.weak).length;
  const weak = entries.length - strong;
  if (entries.length === 0) return "Alfred hasn't recognized anything yet.";
  const more = weak > 0 ? ` and noticed ${weak} more` : "";
  return `Alfred recognized ${strong === 1 ? "1 habit" : `${strong} habits`}${more}.`;
}

function EntryItem({ entry }: { entry: Entry }) {
  const title = sensorTitle(entry.sensor);
  return (
    <li>
      <time>{formatClock(entry.at).time}</time>
      <span className={`dot${entry.weak ? "" : " l"}`} aria-hidden="true" />
      <div className="body">
        <a className={entry.weak ? "notice" : undefined} href={sensorHref(entry.sensor.id)}>{title.text}</a>
        <span>{entry.weak ? "Noticed, not a sensor yet." : evidence(entry.sensor.pattern.parts)}</span>
        {entry.signals.length > 0 && (
          <ul className="sg2" aria-label="Signals">
            {entry.signals.map((s) => (
              <li key={`${s.device.id}-${s.at}-${s.capability}`}>
                <span>{formatClock(s.at).time}</span>
                <span>{s.device.name}</span>
                <span>{deviceWords(s.device.type, s.capability, s.value)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

interface Props { rec: Recording; state: StateAt; playback: Playback; day: number }

export function Timeline({ rec, state, playback, day }: Props) {
  const view = dayView(rec, state, day);
  const isToday = view.day === state.day;
  const date = formatDate(view.dayStart);
  const laterHref = view.day + 1 === state.day ? "#/timeline" : `#/timeline/${view.day + 1}`;
  return (
    <Shell state={state} playback={playback} tab="home" simulated={rec.meta.simulated}>
      <section className="pg-head" aria-label="Summary">
        <h1>{isToday ? `Today, ${date}` : `${date}, day ${view.day}`}</h1>
        <p>{summary(view.entries)}</p>
      </section>
      <nav className="daynav" aria-label="Days">
        {view.day > 1 && <a className="more" href={`#/timeline/${view.day - 1}`}>Earlier</a>}
        {!isToday && <a className="more" href={laterHref}>Later</a>}
      </nav>
      <section className="sec lanes-sec" aria-labelledby="h-lanes">
        <div className="sec-t"><h2 id="h-lanes">The day, device by device</h2></div>
        <DayLanes view={view} />
        <p className="note">A bar is the lamp on or the plug in use. A tick is the door opening. A diamond is a habit Alfred recognized; a hollow one is not a sensor yet. The hatched part has not happened yet.</p>
      </section>
      <section className="sec" aria-labelledby="h-read">
        <div className="sec-t"><h2 id="h-read">What Alfred read</h2></div>
        {view.entries.length === 0 ? (
          <p className="note">{isToday ? "Nothing recognized yet on this day." : "Nothing recognized on this day."}</p>
        ) : (
          <ol className="evt" aria-label="What Alfred read">
            {view.entries.map((e) => <EntryItem key={`${e.sensor.id}-${e.at}`} entry={e} />)}
          </ol>
        )}
      </section>
    </Shell>
  );
}
