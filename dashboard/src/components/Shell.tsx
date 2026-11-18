import type { ReactNode } from "react";
import type { StateAt } from "../data/stateAt";
import type { Playback } from "../playback/usePlayback";
import { SimPill } from "./SimPill";
import { TopBar, type Tab } from "./TopBar";

interface Props { state: StateAt; playback: Playback; tab: Tab; simulated: boolean; children: ReactNode }

export function Shell({ state, playback, tab, simulated, children }: Props) {
  return (
    <div className="scr">
      <TopBar tab={tab} />
      <main className="pg">{children}</main>
      {simulated && <SimPill state={state} playback={playback} />}
    </div>
  );
}
