import { useState } from "react";
import { MicIcon, SendIcon } from "./icons";

const QUICK = ["Today’s plan", "Devices", "Everything off"];

/** Three quick asks, the text box, and the mic (send once there is text). Without `onMic` (no voice model
 *  on this home's computer) there is no microphone, only typing. */
export function Composer({ busy, onSend, onMic, note }: { busy: boolean; onSend: (text: string) => void; onMic?: () => void; note: string }) {
  const [text, setText] = useState("");
  const has = text.trim().length > 0;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!has || busy) return;
    onSend(text.trim());
    setText("");
  };
  return (
    <form className="composer" onSubmit={submit} autoComplete="off">
      <div className="quick" aria-label="Quick asks">
        {QUICK.map((q) => <button key={q} type="button" disabled={busy} onClick={() => onSend(q)}>{q}</button>)}
      </div>
      <div className="field">
        <label className="sr" htmlFor="ask">Ask Alfred</label>
        <input id="ask" name="ask" type="text" placeholder="Ask or tell Alfred" maxLength={200} enterKeyHint="send" value={text} onChange={(e) => setText(e.target.value)} />
        {has || !onMic ? (
          <button className="send" type="submit" aria-label="Send" disabled={busy || !has}><SendIcon /></button>
        ) : (
          <button className="ib" type="button" aria-label="Speak to Alfred" disabled={busy} onClick={onMic}><MicIcon /></button>
        )}
      </div>
      <p className="fine">{note}</p>
    </form>
  );
}
