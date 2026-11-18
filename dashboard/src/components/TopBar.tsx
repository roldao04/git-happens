import type { StateAt } from "../data/stateAt";
import { formatClock } from "../data/words";
import type { Playback } from "../playback/usePlayback";
import { Controls } from "./Controls";
import { Mark } from "./Mark";

export type Tab = "home" | "learned" | "timeline" | "devices";

const TABS: { id: Tab; label: string; href: string; icon: JSX.Element }[] = [
  { id: "home", label: "Home", href: "#/", icon: <path d="M3 10.5L11 4l8 6.5V19H14v-5H8v5H3z" /> },
  { id: "learned", label: "Learned", href: "#/learned", icon: <><path d="M3 16.5h16M5 16.5a6 6 0 0 1 12 0" /><circle cx="11" cy="8.6" r="1" /></> },
  { id: "timeline", label: "Timeline", href: "#/timeline", icon: <><circle cx="11" cy="11" r="8" /><path d="M11 7v4l3 2" /></> },
  {
    id: "devices",
    label: "Devices",
    href: "#/devices",
    icon: <><rect x="3.5" y="3.5" width="6" height="6" rx="1.5" /><rect x="12.5" y="3.5" width="6" height="6" rx="1.5" /><rect x="3.5" y="12.5" width="6" height="6" rx="1.5" /><rect x="12.5" y="12.5" width="6" height="6" rx="1.5" /></>,
  },
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
            <svg className="tab-ic" viewBox="0 0 22 22" aria-hidden="true">{t.icon}</svg>
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
