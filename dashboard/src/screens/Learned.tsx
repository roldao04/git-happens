import { Later } from "../components/Later";
import { SensorTable } from "../components/SensorTable";
import { Shell } from "../components/Shell";
import { StillLearning } from "../components/StillLearning";
import type { Recording } from "../data/recording";
import type { SensorNow, StateAt } from "../data/stateAt";
import { learnedHeadline, learnedSummary } from "../data/words";
import type { Playback } from "../playback/usePlayback";

const isConnectivity = (s: SensorNow) => s.sensor.focus === "connectivity";

export function Learned({ rec, state, playback }: { rec: Recording; state: StateAt; playback: Playback }) {
  const summary = learnedSummary(state.cards.filter((c) => c.justLearned).length, state.alsoNoticed.length);
  const home = rec.meta.household ? `The ${rec.meta.household} home` : "The home";
  const life = state.cards.filter((s) => !isConnectivity(s));
  const connectivity = state.cards.filter(isConnectivity);
  return (
    <Shell state={state} playback={playback} tab="learned">
      <section className="pg-head" aria-label="Summary">
        <h1>{learnedHeadline(state.cards.length)}</h1>
        {summary && <p>{summary}</p>}
        <p className="honesty">{`${home} is simulated; the learning is real.`}</p>
      </section>
      {state.cards.length === 0 ? (
        <StillLearning day={state.day} days={state.days} />
      ) : (
        <>
          <section className="sec" aria-labelledby="h-life">
            <div className="sec-t"><h2 id="h-life">Connected life</h2></div>
            <SensorTable items={life} atEnd={state.atEnd} />
            <p className="note">
              The bar in each row is one day, midnight to midnight, with 06:00, 12:00 and 18:00 marked. A shaded block is the usual window. Each dot is a time it was seen.
            </p>
          </section>
          <section className="sec" aria-labelledby="h-con">
            <div className="sec-t"><h2 id="h-con">Connectivity</h2></div>
            {connectivity.length > 0 ? (
              <SensorTable items={connectivity} atEnd={state.atEnd} />
            ) : (
              <p className="note">No connectivity sensor yet. Alfred has not found a pattern in how the devices drop offline.</p>
            )}
          </section>
        </>
      )}
      {state.alsoNoticed.length > 0 && (
        <section className="sec still" aria-labelledby="h-still">
          <div className="sec-t"><h2 id="h-still">Still noticing</h2></div>
          <SensorTable items={state.alsoNoticed} atEnd={state.atEnd} />
          <p className="note">These are under 60% confidence, so Alfred doesn't treat them as sensors yet.</p>
        </section>
      )}
      <section className="sec" aria-labelledby="h-later">
        <div className="sec-t"><h2 id="h-later">Coming later</h2></div>
        <div className="pairs">
          <Later title="Names and questions">Alfred will ask what a sensor is, such as whether the plug's short bursts are a kettle, and use your word for it.</Later>
          <Later title="Dinner with friends">An event learner. It would spot a one-off evening that looks different from the usual ones, then ask what it was.</Later>
          <Later title="Kettle getting slower">A drift learner. It would notice when a familiar burst takes longer over weeks, and tell you.</Later>
        </div>
      </section>
    </Shell>
  );
}
