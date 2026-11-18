import type { ReactNode } from "react";
import type { StateAt } from "../data/stateAt";
import { formatClock } from "../data/words";
import { Mark } from "./Mark";

export function Header({ state, children }: { state: StateAt; children?: ReactNode }) {
  const { weekday, time } = formatClock(state.t);
  const learned = state.cards.length;
  return (
    <header className="al-header">
      <h1 className="al-brand">
        <Mark />
        <span className="al-word">Alfred</span>
      </h1>
      <span className="al-pill learning">{learned > 0 ? `${learned} learned` : "Still learning"}</span>
      <span className="al-spacer" />
      <div className="al-clock compact" aria-label="Simulated time">
        <span className="al-label">Simulated</span>
        <span className="al-time">{time}</span>
        <span className="al-sub">{`${weekday} · day ${state.day} of ${state.days}`}</span>
      </div>
      {children}
    </header>
  );
}
