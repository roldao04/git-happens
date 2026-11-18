import type { StateAt } from "../data/stateAt";
import { formatClock } from "../data/words";
import type { Playback } from "../playback/usePlayback";
import { Controls } from "./Controls";
import { Mark } from "./Mark";

export type Tab = "home" | "learned";

const TABS: { id: Tab; label: string; href: string }[] = [
  { id: "home", label: "Home", href: "#/" },
  { id: "learned", label: "Learned", href: "#/learned" },
];

export function TopBar({ state, playback, tab }: { state: StateAt; playback: Playback; tab: Tab }) {
  const { weekday, time } = formatClock(state.t);
  return (
    <header className="top">
      <a className="al-brand" href="#/" aria-label="Alfred home">
        <Mark />
        <span className="al-word">Alfred</span>
      </a>
      <nav className="tabs" aria-label="Main">
        {TABS.map((t) => (
          <a key={t.id} className={`tab${t.id === tab ? " on" : ""}`} href={t.href} aria-current={t.id === tab ? "page" : undefined}>
            {t.label}
          </a>
        ))}
      </nav>
      <div className="al-clock compact" aria-label="Simulated time">
        <span className="al-label">Simulated</span>
        <span className="al-time">{time}</span>
        <span className="al-sub">{`${weekday} · day ${state.day} of ${state.days}`}</span>
      </div>
      <Controls playback={playback} />
    </header>
  );
}
