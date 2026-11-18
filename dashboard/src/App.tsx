import { useEffect, useMemo, useState } from "react";
import { HashRouter, Route, Routes, useParams } from "react-router-dom";
import { loadRecording } from "./data/load";
import { toMs, type Recording } from "./data/recording";
import { stateAt, type StateAt } from "./data/stateAt";
import { holdFor } from "./playback/clock";
import { usePlayback, type Playback } from "./playback/usePlayback";
import { SensorDetail } from "./screens/SensorDetail";
import { Home } from "./screens/Home";
import { Learned } from "./screens/Learned";

type Loaded = { rec: Recording } | { error: string } | null;

function DetailRoute(props: { rec: Recording; state: StateAt; playback: Playback }) {
  const { id = "" } = useParams();
  return <SensorDetail {...props} id={id} />;
}

function Player({ rec }: { rec: Recording }) {
  const playback = usePlayback(toMs(rec.meta.start), toMs(rec.meta.end));
  const state = useMemo(() => stateAt(rec, playback.t, holdFor(playback.speed, playback.playing)), [rec, playback.t, playback.speed, playback.playing]);
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<Home rec={rec} state={state} playback={playback} />} />
        <Route path="/learned" element={<Learned rec={rec} state={state} playback={playback} />} />
        <Route path="/sensor/:id" element={<DetailRoute rec={rec} state={state} playback={playback} />} />
      </Routes>
    </HashRouter>
  );
}

export default function App({ load = loadRecording }: { load?: () => Promise<Recording> }) {
  const [loaded, setLoaded] = useState<Loaded>(null);
  useEffect(() => {
    let live = true;
    load().then(
      (rec) => live && setLoaded({ rec }),
      (e: unknown) => {
        console.error("loading recording.json failed", e);
        if (live) setLoaded({ error: e instanceof Error ? e.message : String(e) });
      },
    );
    return () => {
      live = false;
    };
  }, [load]);

  if (loaded === null) return <main className="stage"><p role="status">Loading the recording…</p></main>;
  if ("error" in loaded) return <main className="stage"><p role="alert">{`Could not show the recording: ${loaded.error}`}</p></main>;
  return <Player rec={loaded.rec} />;
}
