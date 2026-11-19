import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { HomeApi } from "./api/client";
import type { ActionResult, ChatReply, Device, PlanItem } from "./api/types";
import { Composer } from "./components/Composer";
import { DevicesPanel } from "./components/DevicesPanel";
import { Drawer } from "./components/Drawer";
import { Mark, MenuIcon, SlidersIcon } from "./components/icons";
import { StatusCard } from "./components/StatusCard";
import { Thread } from "./components/Thread";
import { VoiceSheet } from "./components/VoiceSheet";
import { HomeProvider, useHome } from "./state/HomeProvider";
import { ageDays, autoDelete, loadChats, newChat, saveChats, titled, type Chat, type Turn } from "./store/chats";
import { loadSettings, saveSettings, type Settings } from "./store/settings";
import { pickVoice, type Heard, type VoiceEngine } from "./voice/engines";
import { detectLang } from "./voice/lang";
import { speak } from "./voice/speak";
import { greeting, hhmm, modeWords, pastTense } from "./words";

interface Props { api: HomeApi; homeKey: string; clock?: () => number }

export default function App(props: Props) {
  return <HomeProvider api={props.api}><Phone {...props} /></HomeProvider>;
}

const said = (text: string): ChatReply => ({ text, blocks: [], actions: [], lang: "en" });
const clockText = (ms: number) => new Date(ms).toTimeString().slice(0, 5);

function Phone({ api, homeKey, clock = Date.now }: Props) {
  const { home, status, error, refresh, answer } = useHome();

  // ---- settings and theme ----
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const theme = settings.theme;
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#111a2b" : "#ffffff");
    saveSettings(settings);
  }, [theme, settings]);

  // ---- chats ----
  const [chats, setChats] = useState<Chat[]>(() => {
    const all = autoDelete(loadChats(homeKey, clock()), settings.autoDelete, clock()).kept.filter((c) => c.turns.length > 0);
    const today = all.find((c) => !c.archived && ageDays(c.updated, clock()) === 0); // carry on with today's chat
    return today ? [today, ...all.filter((c) => c !== today)] : [newChat(clock()), ...all];
  });
  const [currentId, setCurrentId] = useState(() => chats[0]!.id);
  const current = chats.find((c) => c.id === currentId) ?? chats[0]!;
  useEffect(() => { saveChats(homeKey, chats.filter((c) => c.turns.length > 0)); }, [homeKey, chats]);

  const push = useCallback((turn: Turn, title?: string) => {
    setChats((all) => all.map((c) => (c.id === currentId ? { ...(title ? titled(c, title) : c), updated: turn.at, turns: [...c.turns, turn] } : c)));
  }, [currentId]);
  const event = useCallback((text: string) => push({ who: "event", at: clock(), text: `${text} · ${clockText(clock())}` }), [push, clock]);

  // Alfred speaks first in a new chat: what the home is doing, then anything it would like a yes for.
  useEffect(() => {
    if (!status || current.turns.length > 0) return;
    const turns: Turn[] = [{ who: "alfred", at: clock(), reply: said(greeting(status, home?.now)) }];
    const asked = new Set(chats.flatMap((c) => c.turns.map((t) => (t.who === "alfred" ? t.suggestionId : undefined))));
    for (const s of status.suggestions) if (!asked.has(s.id)) turns.push({ who: "alfred", at: clock(), reply: said(""), suggestionId: s.id });
    setChats((all) => all.map((c) => (c.id === current.id ? { ...c, title: c.title === "New chat" ? "This morning" : c.title, turns } : c)));
  }, [status, home?.now, current.id, current.turns.length, chats, clock]);

  // ---- talking to Alfred ----
  const [thinking, setThinking] = useState<string | null>(null);
  const send = useCallback(async (text: string, voice = false, heard: string | null = null) => {
    if (thinking) return;
    // Auto: answer in the language the person used. A fixed language: always that one.
    const lang = settings.lang === "auto" ? heard ?? detectLang(text) : settings.lang;
    push({ who: "me", at: clock(), text, voice }, text);
    setThinking("Thinking…");
    let reply: ChatReply;
    try {
      reply = await api.chat(text, { lang });
    } catch (e) {
      reply = said(`I can’t answer right now: ${e instanceof Error ? e.message : String(e)}.`);
    }
    setThinking(null);
    push({ who: "alfred", at: clock(), reply });
    if (voice && settings.speakReplies) speak([reply.text, ...reply.blocks.map((b) => (b.t === "p" ? b.text : ""))].join(" "), reply.lang ?? lang);
    void refresh();
  }, [api, clock, push, refresh, settings.lang, settings.speakReplies, thinking]);

  // ---- toasts ----
  const [toast, setToast] = useState<{ text: string; undo?: () => void } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();
  const showToast = useCallback((text: string, undo?: () => void) => {
    setToast({ text, undo });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 6000);
  }, []);

  // A control was used: say it happened, or plainly that it didn't (and why). It only moved if it was sent.
  const onResult = useCallback((r: ActionResult, _d: Device, what: string) => {
    if (r.status !== "sent") { showToast(`Didn’t ${what}: ${(r.reason ?? "it didn’t work").replace(/^blocked: /, "")}`); return; }
    event(`You ${pastTense(what)}${r.verified === "unconfirmed" ? " (not confirmed yet)" : ""}`);
    if (r.note) push({ who: "alfred", at: clock(), reply: said(r.note) });
  }, [clock, event, push, showToast]);

  const onAnswer = useCallback(async (item: PlanItem, yes: boolean) => {
    const done = await answer(item.id, yes);
    if (!done) return;
    event(`${item.title}: ${yes ? "yes" : "not now"}`);
    push({ who: "alfred", at: clock(), reply: said(yes ? `Done. I’ll do it at ${hhmm(item.at)}.` : "All right, I won’t.") });
  }, [answer, clock, event, push]);

  // ---- panels ----
  const [panel, setPanel] = useState<"none" | "drawer" | "devices">("none");
  const [voice, setVoice] = useState<VoiceEngine | null>(null);
  const canTalk = useMemo(() => pickVoice(api) !== null, [api]);
  const onHeard = (h: Heard) => { setVoice(null); void send(h.text, true, h.lang); };

  const openChat = (id: string) => { setCurrentId(id); setPanel("none"); };
  const startChat = () => { const c = newChat(clock()); setChats((all) => [c, ...all]); setCurrentId(c.id); setPanel("none"); };
  const removeChats = (ids: string[], text: string, after?: () => void) => {
    const gone = chats.filter((c) => ids.includes(c.id));
    if (!gone.length) return;
    const left = chats.filter((c) => !ids.includes(c.id));
    const next = left.length ? left : [newChat(clock())];
    setChats(next);
    if (ids.includes(currentId)) setCurrentId(next[0]!.id);
    showToast(text, () => { setChats((all) => [...all, ...gone]); after?.(); });
  };
  const changeSettings = (s: Partial<Settings>) => {
    const prev = settings;
    const next = { ...settings, ...s };
    setSettings(next);
    if (s.autoDelete !== undefined && s.autoDelete !== prev.autoDelete) {
      const { gone } = autoDelete(chats, s.autoDelete, clock());
      if (gone.length) removeChats(gone.map((c) => c.id), `${gone.length} older chat${gone.length > 1 ? "s" : ""} deleted`, () => setSettings((x) => ({ ...x, autoDelete: prev.autoDelete })));
    }
  };

  const mode = modeWords(api.kind, home);

  return (
    <div className="stage">
      <div className="phone">
        <header className="ph-head">
          <button className="ib" type="button" aria-label="Conversations" aria-expanded={panel === "drawer"} onClick={() => setPanel("drawer")}><MenuIcon /></button>
          <div className="who"><span className="al-brand"><Mark size={36} /><span className="al-word">Alfred</span></span></div>
          <div className="head-acts">
            <button className="ib" type="button" aria-label="Devices" onClick={() => setPanel("devices")}><SlidersIcon /></button>
          </div>
        </header>
        <StatusCard onEvent={event} />
        {error && !home ? (
          <main className="thread"><p role="alert" className="alert">Can’t reach Alfred: {error}</p></main>
        ) : (
          <Thread turns={current.turns} thinking={thinking} onResult={onResult} onAnswer={onAnswer} onEvent={event} />
        )}
        <Composer busy={!!thinking} onSend={(t) => void send(t)} onMic={canTalk ? () => setVoice(pickVoice(api)) : undefined} note={mode?.note ?? ""} />

        {panel === "devices" && <DevicesPanel onClose={() => setPanel("none")} onResult={onResult} />}
        {panel === "drawer" && (
          <Drawer chats={chats.filter((c) => c.turns.length > 0 || c.id === currentId)} current={currentId} now={clock()} household={home?.household ?? ""} mode={mode}
            settings={settings} theme={theme} onOpen={openChat} onNew={startChat} onClose={() => setPanel("none")}
            onDelete={(id) => removeChats([id], "Chat deleted")}
            onArchive={(id, archived) => {
              setChats((all) => all.map((c) => (c.id === id ? { ...c, archived } : c)));
              showToast(archived ? "Chat archived" : "Chat moved back", () => setChats((all) => all.map((c) => (c.id === id ? { ...c, archived: !archived } : c))));
            }}
            onSettings={changeSettings} />
        )}
        {voice && <VoiceSheet engine={voice} onDone={onHeard} onCancel={() => setVoice(null)} />}
        {toast && (
          <div className="toast" role="status">
            <span>{toast.text}</span>
            {toast.undo && <button className="al-btn quiet sm" type="button" onClick={() => { toast.undo?.(); setToast(null); }}>Undo</button>}
          </div>
        )}
      </div>
    </div>
  );
}
