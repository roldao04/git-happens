import { SensorTable } from "../components/SensorTable";
import { Shell } from "../components/Shell";
import { StillLearning } from "../components/StillLearning";
import type { Recording } from "../data/recording";
import type { StateAt } from "../data/stateAt";
import { learnedHeadline, learnedSummary } from "../data/words";
import type { Playback } from "../playback/usePlayback";

export function Learned({ rec, state, playback }: { rec: Recording; state: StateAt; playback: Playback }) {
  const summary = learnedSummary(state.cards.filter((c) => c.justLearned).length, state.alsoNoticed.length);
  const home = rec.meta.household ? `The ${rec.meta.household} home` : "The home";
  return (
    <Shell state={state} playback={playback} tab="learned" simulated={rec.meta.simulated}>
      <section className="pg-head" aria-label="Summary">
        <h1>{learnedHeadline(state.cards.length)}</h1>
        {summary && <p>{summary}</p>}
        {rec.meta.simulated && <p className="honesty">{`${home} is simulated; the learning is real.`}</p>}
      </section>
      {state.cards.length === 0 ? (
        <StillLearning day={state.day} days={state.days} />
      ) : (
        <section className="sec">
          <SensorTable items={state.cards} label="Learned sensors" />
          <p className="note">Each bar is one day. The shaded part is the usual time, and each dot is a time it was seen.</p>
        </section>
      )}
      {state.alsoNoticed.length > 0 && (
        <details className="fold">
          <summary>{`${state.alsoNoticed.length} more still being watched`}</summary>
          <SensorTable items={state.alsoNoticed} label="Still being watched" />
          <p className="note">These are under 60% confidence, so Alfred doesn't treat them as sensors yet.</p>
        </details>
      )}
    </Shell>
  );
}
