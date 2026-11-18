import { useCallback, useEffect, useRef, useState } from "react";
import { dayNumber } from "../data/stateAt";
import { advance, dayStart, type Speed } from "./clock";

export interface Playback {
  t: number;
  playing: boolean;
  speed: Speed;
  play(): void;
  pause(): void;
  toggle(): void;
  setSpeed(s: Speed): void;
  jumpToDay(day: number): void;
  jumpToEnd(): void;
}

function targetHandles(target: EventTarget | null, pressed: string): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || target.matches("select, textarea, input:not([type=range])")) return true;
  if (target.matches("input[type=range]")) return pressed !== " ";
  if (target.matches("summary")) return pressed === " ";
  return false;
}

export function usePlayback(startMs: number, endMs: number): Playback {
  const [t, setT] = useState(startMs);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);
  const tRef = useRef(t);
  tRef.current = t;

  const clamp = useCallback((v: number) => Math.min(Math.max(v, startMs), endMs), [startMs, endMs]);
  const play = useCallback(() => {
    if (tRef.current >= endMs) return;
    setPlaying(true);
  }, [endMs]);
  const pause = useCallback(() => setPlaying(false), []);
  const toggle = useCallback(() => (playing ? pause() : play()), [playing, pause, play]);
  const jumpToDay = useCallback((day: number) => setT(clamp(dayStart(startMs, day))), [clamp, startMs]);
  const jumpToEnd = useCallback(() => {
    setT(endMs);
    setPlaying(false);
  }, [endMs]);

  useEffect(() => {
    if (!playing) return;
    let last: number | null = null;
    let id = 0;
    const frame = (now: number) => {
      const dt = last === null ? 0 : now - last;
      last = now;
      const next = advance(tRef.current, dt, speed, endMs);
      tRef.current = next;
      setT(next);
      if (next >= endMs) {
        setPlaying(false);
        return;
      }
      id = requestAnimationFrame(frame);
    };
    id = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(id);
  }, [playing, speed, endMs]);

  useEffect(() => {
    const onPress = (e: KeyboardEvent) => {
      if (targetHandles(e.target, e.key)) return;
      if (e.key === " ") {
        e.preventDefault();
        toggle();
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const day = dayNumber(startMs, tRef.current) + (e.key === "ArrowRight" ? 1 : -1);
        setT(clamp(dayStart(startMs, day)));
      }
    };
    window.addEventListener("keydown", onPress);
    return () => window.removeEventListener("keydown", onPress);
  }, [toggle, clamp, startMs]);

  return { t, playing, speed, play, pause, toggle, setSpeed, jumpToDay, jumpToEnd };
}
