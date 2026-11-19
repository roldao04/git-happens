import { useState } from "react";
import type { StateAt } from "../data/stateAt";
import { formatClock } from "../data/words";
import { SPEEDS } from "../playback/clock";
import type { Playback } from "../playback/usePlayback";
import { Scrubber } from "./Scrubber";

export function SimPill({ state, playback }: { state: StateAt; playback: Playback }) {
  const [open, setOpen] = useState(false);
  const { time } = formatClock(state.t);
  return (
    <div className="sim" role="region" aria-label="Simulation">
      {open && (
        <div className="sim-panel" id="sim-panel">
          <div className="al-row">
            <div className="al-row" role="group" aria-label="Speed">
              {SPEEDS.map((s) => (
                <button key={s} type="button" className="al-btn quiet" aria-pressed={playback.speed === s} onClick={() => playback.setSpeed(s)}>
                  {`${s}×`}
                </button>
              ))}
            </div>
            <button type="button" className="al-btn" aria-label="Jump to the end" onClick={playback.jumpToEnd}>
              ⏭
            </button>
          </div>
          <Scrubber state={state} onJump={playback.jumpToDay} />
        </div>
      )}
      <div className="sim-pill">
        <button type="button" className="al-btn primary" aria-label={playback.playing ? "Pause" : "Play"} onClick={playback.toggle}>
          {playback.playing ? "❚❚" : "▶"}
        </button>
        <button type="button" className="sim-toggle" aria-expanded={open} aria-controls="sim-panel" onClick={() => setOpen(!open)}>
          {`Simulated · day ${state.day} · ${time}`}
          <span aria-hidden="true">{open ? "▾" : "▴"}</span>
        </button>
      </div>
    </div>
  );
}
