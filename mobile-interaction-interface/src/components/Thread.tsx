import { useEffect, useRef } from "react";
import type { ActionResult, Block, PlanItem } from "../api/types";
import { useHome } from "../state/HomeProvider";
import type { Turn } from "../store/chats";
import { DeviceControl, SensorList, type OnResult } from "./DeviceControl";
import { MicIcon, Mark } from "./icons";
import { PlanList } from "./PlanList";
import { clockText, whatOf } from "../words";

interface Props {
  turns: Turn[];
  thinking: string | null;
  onResult: OnResult;
  onAnswer: (item: PlanItem, yes: boolean) => void;
  onEvent: (text: string) => void;
}

/** The conversation: the person's bubbles, Alfred's replies with what they show, and short lines for what happened. */
export function Thread({ turns, thinking, onResult, onAnswer, onEvent }: Props) {
  const main = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = main.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns.length, thinking]);
  return (
    <main ref={main} className="thread" aria-live="polite" aria-label="Conversation">
      {turns.map((t, i) => <TurnView key={i} turn={t} onResult={onResult} onAnswer={onAnswer} onEvent={onEvent} />)}
      {thinking && <div className="thinking" role="status"><Mark size={18} /><span>{thinking}</span></div>}
    </main>
  );
}

function TurnView({ turn, onResult, onAnswer, onEvent }: { turn: Turn } & Omit<Props, "turns" | "thinking">) {
  if (turn.who === "me") {
    return (
      <div className="msg me">
        <p className="bubble">{turn.text}</p>
        {turn.voice && <span className="via"><MicIcon />spoken</span>}
      </div>
    );
  }
  if (turn.who === "event") return <div className="ev">{turn.text}</div>;
  return (
    <div className="msg alf">
      <div className="by"><Mark size={16} /><span>Alfred</span><time>{clockText(turn.at)}</time></div>
      {turn.reply.text && <p>{turn.reply.text}</p>}
      {turn.reply.blocks.map((b, i) => <BlockView key={i} block={b} onResult={onResult} onEvent={onEvent} />)}
      {turn.suggestionId && <Suggestion id={turn.suggestionId} onAnswer={onAnswer} />}
      <NotDone actions={turn.reply.actions} />
    </div>
  );
}

/** The real Alfred's writes that didn't happen, said plainly under his reply (the scripted demo words its own). */
function NotDone({ actions }: { actions: ActionResult[] }) {
  const { api, home } = useHome();
  if (api.kind !== "http") return null;
  const missed = actions.filter((a) => a.status !== "sent");
  if (!missed.length) return null;
  const name = (id?: string) => home?.devices.find((d) => d.id === id)?.name ?? "device";
  return (
    <>
      {missed.map((a, i) => (
        <p key={i} className="ev nd">
          {a.body ? `Didn’t ${whatOf(name(a.deviceId), a.body)}` : `${name(a.deviceId)}: not changed`}: {(a.reason ?? "it didn’t work").replace(/^blocked: /, "")}
        </p>
      ))}
    </>
  );
}

function BlockView({ block, onResult, onEvent }: { block: Block; onResult: OnResult; onEvent: (t: string) => void }) {
  const { home, status } = useHome();
  switch (block.t) {
    case "p":
      return <p>{block.text}</p>;
    case "control": {
      const d = home?.devices.find((x) => x.id === block.deviceId);
      return d ? <div className="blk"><DeviceControl device={d} onResult={onResult} /></div> : null;
    }
    case "plan":
      return status && home ? <div className="blk plan-blk"><PlanList items={status.planned} now={home.now} onEvent={onEvent} /></div> : null;
    case "devices": {
      if (!home) return null;
      const all = home.devices.filter((d) => d.type !== "router" && d.type !== "phone");
      return (
        <>
          {all.filter((d) => d.controls.length).map((d) => <div className="blk" key={d.id}><DeviceControl device={d} onResult={onResult} /></div>)}
          <div className="blk"><SensorList devices={all.filter((d) => !d.controls.length)} /></div>
        </>
      );
    }
    case "feed":
      return (
        <div className="blk feedblk">
          <div className="al-feed">{block.lines.map(([t, k, v], i) => <div key={i}><time>{t}</time><span>{k}</span><span className="v">{v}</span></div>)}</div>
        </div>
      );
  }
}

/** A "suggest" decision: Alfred asks before doing it. */
function Suggestion({ id, onAnswer }: { id: string; onAnswer: (item: PlanItem, yes: boolean) => void }) {
  const { status } = useHome();
  const item = status?.suggestions.find((s) => s.id === id);
  if (!item) return null;
  return (
    <div className="ask" role="group" aria-label={item.title}>
      <p className="ask-what"><b>{item.title}?</b> {item.does}.</p>
      <p className="why">{item.reason}</p>
      <div className="acts">
        <button className="al-btn primary" type="button" onClick={() => onAnswer(item, true)}>Yes</button>
        <button className="al-btn" type="button" onClick={() => onAnswer(item, false)}>Not now</button>
      </div>
    </div>
  );
}
