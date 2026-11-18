import type { ReactNode } from "react";
import type { StateAt } from "../data/stateAt";
import type { Playback } from "../playback/usePlayback";
import { Scrubber } from "./Scrubber";
import { TopBar, type Tab } from "./TopBar";

interface Props { state: StateAt; playback: Playback; tab: Tab; children: ReactNode }

export function Shell({ state, playback, tab, children }: Props) {
  return (
    <div className="scr">
      <TopBar state={state} playback={playback} tab={tab} />
      <main className="pg">{children}</main>
      <footer className="scrub-bar">
        <Scrubber state={state} onJump={playback.jumpToDay} />
      </footer>
    </div>
  );
}
