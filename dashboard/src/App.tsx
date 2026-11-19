import { useEffect, useMemo, useState } from "react";
import { HashRouter, Route, Routes, useParams } from "react-router-dom";
import { loadRecording } from "./data/load";
import { toMs, type Recording } from "./data/recording";
import { stateAt, type StateAt } from "./data/stateAt";
import { holdFor } from "./playback/clock";
import { usePlayback, type Playback } from "./playback/usePlayback";
import { SensorDetail } from "./screens/SensorDetail";
import { Home } from "./screens/Home";
import { Devices } from "./screens/Devices";
import { Learned } from "./screens/Learned";
import { Timeline } from "./screens/Timeline";

type Loaded = { rec: Recording } | { error: string } | null;

function DetailRoute(props: { rec: Recording; state: StateAt; playback: Playback }) {
  const { id = "" } = useParams();
  return <SensorDetail {...props} id={id} />;
}

function TimelineRoute(props: { rec: Recording; state: StateAt; playback: Playback }) {
  const { day } = useParams();
  return <Timeline {...props} day={day === undefined ? props.state.day : Number(day)} />;
}

const still = () => undefined;

function fixedAt(t: number): Playback {
  return { t, playing: false, speed: 1, play: still, pause: still, toggle: still, setSpeed: still, jumpToDay: still, jumpToEnd: still };
}

function LivePlayer({ rec }: { rec: Recording }) {
  const playback = useMemo(() => fixedAt(toMs(rec.meta.updated ?? rec.meta.end)), [rec]);
  const state = useMemo(() => stateAt(rec, playback.t, holdFor(1, false)), [rec, playback]);
  return <Pages rec={rec} state={state} playback={playback} />;
}

function Player({ rec }: { rec: Recording }) {
  const playback = usePlayback(toMs(rec.meta.start), toMs(rec.meta.end));
  const state = useMemo(() => stateAt(rec, playback.t, holdFor(playback.speed, playback.playing)), [rec, playback.t, playback.speed, playback.playing]);
  return <Pages rec={rec} state={state} playback={playback} />;
}

function Pages({ rec, state, playback }: { rec: Recording; state: StateAt; playback: Playback }) {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<Home rec={rec} state={state} playback={playback} />} />
        <Route path="/learned" element={<Learned rec={rec} state={state} playback={playback} />} />
        <Route path="/timeline" element={<TimelineRoute rec={rec} state={state} playback={playback} />} />
        <Route path="/timeline/:day" element={<TimelineRoute rec={rec} state={state} playback={playback} />} />
        <Route path="/devices" element={<Devices rec={rec} state={state} playback={playback} />} />
        <Route path="/sensor/:id" element={<DetailRoute rec={rec} state={state} playback={playback} />} />
      </Routes>
    </HashRouter>
  );
}

interface AppProps { load?: () => Promise<Recording>; live?: boolean; refreshMs?: number }

export default function App({ load = loadRecording, live = false, refreshMs = 5000 }: AppProps) {
  const [loaded, setLoaded] = useState<Loaded>(null);
  const file = live ? "live.json" : "recording.json";
  useEffect(() => {
    let mounted = true;
    let shown = false;
    const fetchOnce = () =>
      load().then(
        (rec) => {
          if (!mounted) return;
          shown = true;
          setLoaded({ rec });
        },
        (e: unknown) => {
          if (shown) {
            console.error(`refreshing ${file} failed`, e);
            return;
          }
          console.error(`loading ${file} failed`, e);
          if (mounted) setLoaded({ error: e instanceof Error ? e.message : String(e) });
        },
      );
    void fetchOnce();
    const timer = live ? window.setInterval(() => void fetchOnce(), refreshMs) : undefined;
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [load, live, refreshMs, file]);

  if (loaded === null) return <main className="stage"><p role="status">Loading the recording…</p></main>;
  if ("error" in loaded) return <main className="stage"><p role="alert">{`Could not show the recording: ${loaded.error}`}</p></main>;
  return live ? <LivePlayer rec={loaded.rec} /> : <Player rec={loaded.rec} />;
}
