import { NeedsLook } from "../components/NeedsLook";
import { Shell } from "../components/Shell";
import { needsLook } from "../data/devices";
import type { Recording } from "../data/recording";
import type { StateAt } from "../data/stateAt";
import { homeSummary, homeTitle } from "../data/words";
import type { Playback } from "../playback/usePlayback";

export function Home({ rec, state, playback }: { rec: Recording; state: StateAt; playback: Playback }) {
  const newToday = state.cards.filter((c) => c.justLearned).length;
  return (
    <Shell state={state} playback={playback} tab="home" simulated={rec.meta.simulated}>
      <section className="pg-head" aria-label="Summary">
        <h1>{homeTitle(state.devices)}</h1>
        <p>{homeSummary(state.devices, newToday)}</p>
        <a className="see-day" href="#/timeline">See the whole day</a>
      </section>
      <NeedsLook concerns={needsLook(state.devices)} />
    </Shell>
  );
}
