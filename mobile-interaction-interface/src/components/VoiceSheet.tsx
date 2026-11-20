import { useEffect, useRef, useState } from "react";
import type { Heard, VoiceEngine } from "../voice/engines";

const NOTE: Record<VoiceEngine["name"], string> = {
  server: "Speech is turned into text on this home’s own computer.",
};

/** Listening: level bars, what was heard so far, Cancel and Send. Ends by itself when the engine does. */
export function VoiceSheet({ engine, onDone, onCancel }: { engine: VoiceEngine; onDone: (h: Heard) => void; onCancel: () => void }) {
  const [said, setSaid] = useState("");
  const [state, setState] = useState<"listening" | "thinking" | "error">("listening");
  const [levelNow, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const finishing = useRef(false);
  const send = useRef<HTMLButtonElement>(null);

  const finish = async () => {
    if (finishing.current) return;
    finishing.current = true;
    setState("thinking");
    try {
      const heard = await engine.stop();
      if (heard.text.trim()) onDone(heard);
      else { setError("I didn’t catch anything. Try again, or type it."); setState("error"); finishing.current = false; }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState("error");
      finishing.current = false;
    }
  };

  useEffect(() => {
    send.current?.focus();
    engine.start({ onPartial: setSaid, onLevel: setLevel, onEnd: () => void finish() }).catch((e: unknown) => {
      setError(e instanceof Error && e.name === "NotAllowedError" ? "The microphone isn’t allowed. Type instead, or allow it in the browser."
        : e instanceof Error ? e.message : String(e));
      setState("error");
    });
    return () => engine.cancel();
  }, [engine]);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onCancel]);

  return (
    <div className="layer">
      <div className="al-scrim" onClick={onCancel} />
      <section className={`sheet voice${state === "listening" ? " listening" : ""}`} role="dialog" aria-modal="true" aria-labelledby="voice-state">
        <span className="grab" aria-hidden="true" />
        <div className="al-label" id="voice-state">{state === "listening" ? "Listening" : state === "thinking" ? "Understanding" : "Voice"}</div>
        <div className="bars" aria-hidden="true" style={{ "--lvl": String(0.4 + levelNow) } as React.CSSProperties}><i /><i /><i /><i /><i /></div>
        <p className="said" aria-live="polite">{error ?? said}</p>
        <div className="vbtns">
          <button className="al-btn quiet" type="button" onClick={onCancel}>Cancel</button>
          <button ref={send} className="al-btn primary" type="button" disabled={state === "thinking"} onClick={() => void finish()}>Send</button>
        </div>
        <p className="vnote">{NOTE[engine.name]}</p>
      </section>
    </div>
  );
}
