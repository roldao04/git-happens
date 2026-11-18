import type { StateAt } from "../data/stateAt";

export function Scrubber({ state, onJump }: { state: StateAt; onJump(day: number): void }) {
  return (
    <div className="scrubber">
      <span className="al-muted al-tnum">day 1</span>
      <input
        type="range"
        aria-label="Simulated day"
        min={1}
        max={state.days}
        step={1}
        value={state.day}
        list="learn-days"
        onChange={(e) => onJump(Number(e.target.value))}
      />
      <datalist id="learn-days">
        {state.learnDays.map((d) => (
          <option key={d} value={d} />
        ))}
      </datalist>
      <span className="al-muted al-tnum">{`day ${state.days}`}</span>
    </div>
  );
}
