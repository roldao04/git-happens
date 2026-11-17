import type { Sensor } from "../data/recording";

export function FadingPill({ sensor }: { sensor: Sensor }) {
  return sensor.status === "fading" ? <span className="al-pill">fading</span> : null;
}
