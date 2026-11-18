import { SPEEDS } from "../playback/clock";
import type { Playback } from "../playback/usePlayback";

export function Controls({ playback }: { playback: Playback }) {
  return (
    <div className="al-row controls">
      <button type="button" className="al-btn primary" aria-label={playback.playing ? "Pause" : "Play"} onClick={playback.toggle}>
        {playback.playing ? "❚❚" : "▶"}
      </button>
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
  );
}
