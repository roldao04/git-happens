import { concernText, lookCount, type Concern } from "../data/devices";
import { Chevron } from "./Chevron";

export function NeedsLook({ concerns }: { concerns: Concern[] }) {
  if (concerns.length === 0) return null;
  return (
    <a className="attn" href="#/devices">
      <div className="t">
        <b>{lookCount(concerns.length)}</b>
        <span>{concerns.map(concernText).join(" ")}</span>
      </div>
      <Chevron />
    </a>
  );
}
