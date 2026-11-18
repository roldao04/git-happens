import { AlsoNoticed } from "../components/AlsoNoticed";
import { Controls } from "../components/Controls";
import { DeviceList } from "../components/DeviceList";
import { Header } from "../components/Header";
import { Scrubber } from "../components/Scrubber";
import { SensorCard } from "../components/SensorCard";
import { StillLearning } from "../components/StillLearning";
import type { Recording } from "../data/recording";
import type { StateAt } from "../data/stateAt";
import type { Playback } from "../playback/usePlayback";

export function Stage({ rec, state, playback }: { rec: Recording; state: StateAt; playback: Playback }) {
  return (
    <>
      <Header state={state}>
        <Controls playback={playback} />
      </Header>
      <main className="stage">
        <div className="stage-grid">
          <section className="al-panel" aria-labelledby="home-now">
            <h2 id="home-now">Home now</h2>
            <DeviceList devices={state.devices} latest={state.latest} />
          </section>
          <section className="al-panel" aria-labelledby="learned">
            <h2 id="learned">
              Learned so far
              <small>{rec.meta.household ? `the ${rec.meta.household} home, simulated` : "simulated"}</small>
            </h2>
            {state.cards.length === 0 ? (
              <StillLearning day={state.day} days={state.days} />
            ) : (
              <div className="al-cards">
                {state.cards.map((item) => (
                  <SensorCard key={item.sensor.id} item={item} atEnd={state.atEnd} />
                ))}
              </div>
            )}
            <AlsoNoticed items={state.alsoNoticed} />
          </section>
        </div>
        <Scrubber state={state} onJump={playback.jumpToDay} />
      </main>
    </>
  );
}
