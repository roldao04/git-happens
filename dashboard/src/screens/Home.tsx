import { DevicesBox } from "../components/DevicesBox";
import { NeedsLook } from "../components/NeedsLook";
import { Later } from "../components/Later";
import { NewFromAlfred } from "../components/NewFromAlfred";
import { Shell } from "../components/Shell";
import { TodayFeed } from "../components/TodayFeed";
import { needsLook } from "../data/devices";
import type { Recording } from "../data/recording";
import type { StateAt } from "../data/stateAt";
import { homeSummary, homeTitle } from "../data/words";
import type { Playback } from "../playback/usePlayback";

export function Home({ state, playback }: { rec: Recording; state: StateAt; playback: Playback }) {
  const newToday = state.cards.filter((c) => c.justLearned).length;
  return (
    <Shell state={state} playback={playback} tab="home">
      <section className="pg-head" aria-label="Summary">
        <h1>{homeTitle(state.devices)}</h1>
        <p>{homeSummary(state.devices, newToday)}</p>
      </section>
      <div className="two">
        <div className="stack">
          <Later title="Alfred will suggest, then ask">
            When it looks like nobody is home, Alfred will offer to turn the lamp off and wait for your answer. It never acts on its own.
          </Later>
          <TodayFeed items={state.today} />
          <NewFromAlfred items={state.cards} t={state.t} />
        </div>
        <aside className="aside" aria-label="Devices">
          <NeedsLook concerns={needsLook(state.devices)} />
          <DevicesBox devices={state.devices} latest={state.latest} />
        </aside>
      </div>
    </Shell>
  );
}
