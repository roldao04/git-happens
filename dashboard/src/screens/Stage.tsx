import { AlsoNoticed } from "../components/AlsoNoticed";
import { Controls } from "../components/Controls";
import { DeviceList } from "../components/DeviceList";
import { Header } from "../components/Header";
import { Scrubber } from "../components/Scrubber";
import { SensorCard } from "../components/SensorCard";
import { StillLearning } from "../components/StillLearning";
import { TodayFeed } from "../components/TodayFeed";
import { homeHeadline, learnedHeadline, learnedSummary } from "../data/words";
import type { Recording } from "../data/recording";
import type { StateAt } from "../data/stateAt";
import type { Playback } from "../playback/usePlayback";

export function Stage({ rec, state, playback }: { rec: Recording; state: StateAt; playback: Playback }) {
  const summary = learnedSummary(state.cards.filter((c) => c.justLearned).length, state.alsoNoticed.length);
  const home = rec.meta.household ? `The ${rec.meta.household} home` : "The home";
  return (
    <>
      <Header state={state}>
        <Controls playback={playback} />
      </Header>
      <main className="stage">
        <div className="stage-grid">
          <section className="al-panel" aria-labelledby="home-now">
            <h2 id="home-now">Home now</h2>
            <p className="headline">{homeHeadline(state.devices)}</p>
            <DeviceList devices={state.devices} latest={state.latest} />
            <TodayFeed items={state.today} />
          </section>
          <section className="al-panel" aria-labelledby="learned">
            <h2 id="learned">Learned so far</h2>
            <p className="headline">{learnedHeadline(state.cards.length)}</p>
            {summary && <p className="summary">{summary}</p>}
            <p className="al-muted honesty">{`${home} is simulated; the learning is real.`}</p>
            {state.cards.length === 0 ? (
              <StillLearning day={state.day} days={state.days} />
            ) : (
              <>
                <div className="al-cards">
                  {state.cards.map((item) => (
                    <SensorCard key={item.sensor.id} item={item} atEnd={state.atEnd} />
                  ))}
                </div>
                <p className="al-muted bar-caption">
                  Each bar is one day, midnight to midnight, marked at 06:00, 12:00 and 18:00. The shaded part is the usual window; each dot is a time it was seen.
                </p>
              </>
            )}
            <AlsoNoticed items={state.alsoNoticed} />
          </section>
        </div>
        <Scrubber state={state} onJump={playback.jumpToDay} />
      </main>
    </>
  );
}
