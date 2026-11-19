import { DAY_MS } from "../data/stateAt";
import type { DayView } from "../data/timeline";

export function LanesLegend({ view }: { view: DayView }) {
  const has = (type: string) => view.lanes.some((l) => l.device.type === type);
  const items: [className: string, text: string][] = [];
  if (has("bulb")) items.push(["k-bar k-lamp", "Lamp on, in its color"]);
  if (has("plug")) items.push(["k-bar", "Plug in use"]);
  if (has("contactSensor")) items.push(["k-bar k-door", "Door open"]);
  for (const m of view.measures) items.push([`k-line ${m.label.toLowerCase()}`, m.label]);
  if (view.entries.some((e) => !e.weak)) items.push(["k-dia", "Habit Alfred recognized"]);
  if (view.entries.some((e) => e.weak)) items.push(["k-dia k-ring", "Not a sensor yet"]);
  if (view.upTo < view.dayStart + DAY_MS) items.push(["k-future", "Not happened yet"]);
  if (view.gaps.length > 0) items.push(["k-gap", "Not watched"]);
  return (
    <ul className="lanes-legend" aria-label="Chart legend">
      {items.map(([className, text]) => (
        <li key={text}><i className={className} aria-hidden="true" />{text}</li>
      ))}
    </ul>
  );
}
