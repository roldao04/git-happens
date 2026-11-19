import { useEffect, useRef, useState } from "react";
import { groupChats, type AutoDelete, type Chat } from "../store/chats";
import type { Lang, Settings, Theme } from "../store/settings";
import type { ModeWords } from "../words";
import { BoxIcon, CloseIcon, DotsIcon, GearIcon, NewChatIcon, TrashIcon } from "./icons";

interface Props {
  chats: Chat[];
  current: string;
  now: number;
  household: string;
  mode: ModeWords | null;
  settings: Settings;
  theme: Theme;
  onOpen: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onArchive: (id: string, archived: boolean) => void;
  onSettings: (s: Partial<Settings>) => void;
  onClose: () => void;
}

/** Earlier chats by age, a ⋯ menu to archive or delete each, swipe left-to-right to delete, and Settings. */
export function Drawer(p: Props) {
  const [archOpen, setArchOpen] = useState(false);
  const [menu, setMenu] = useState<{ id: string; top: number } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const newBtn = useRef<HTMLButtonElement>(null);
  useEffect(() => { newBtn.current?.focus(); }, []);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (menu) setMenu(null);
      else if (settingsOpen) setSettingsOpen(false);
      else p.onClose();
    };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [menu, settingsOpen, p]);

  const groups = groupChats(p.chats, p.now);
  const archived = p.chats.filter((c) => c.archived);
  const openMenu = (id: string, btn: HTMLElement) => {
    const d = panel.current?.getBoundingClientRect(), b = btn.getBoundingClientRect();
    setMenu(menu?.id === id ? null : { id, top: d ? b.bottom - d.top + (panel.current?.scrollTop ?? 0) + 4 : 0 });
  };
  const row = (c: Chat) => <Row key={c.id} chat={c} current={c.id === p.current} now={p.now} menuOpen={menu?.id === c.id}
    onOpen={() => p.onOpen(c.id)} onMenu={(btn) => openMenu(c.id, btn)} onSwipeDelete={() => p.onDelete(c.id)} />;
  const target = menu && p.chats.find((c) => c.id === menu.id);

  return (
    <div className="layer">
      <div className="al-scrim" onClick={p.onClose} />
      <nav ref={panel} className="drawer" aria-label="Conversations" onClick={(e) => { if (menu && !(e.target as HTMLElement).closest(".menu,.th-more")) setMenu(null); }}>
        <div className="drawer-head">
          <h2>Conversations</h2>
          <div className="head-acts">
            <button ref={newBtn} className="ib" type="button" aria-label="New chat" title="New chat" onClick={p.onNew}><NewChatIcon /></button>
            <button className="ib" type="button" aria-label="Close conversations" onClick={p.onClose}><CloseIcon /></button>
          </div>
        </div>
        <div className="groups">
          {groups.length === 0 && <p className="dim">No chats yet.</p>}
          {groups.map((g) => (
            <section className="grp" key={g.name}>
              <h3 className="al-label">{g.name}</h3>
              <ul className="al-list no-time th-list">{g.chats.map(row)}</ul>
            </section>
          ))}
          {archived.length > 0 && (
            <section className="grp">
              <button type="button" className="arch-toggle" aria-expanded={archOpen} onClick={() => setArchOpen((o) => !o)}>Archived · {archived.length}</button>
              {archOpen && <ul className="al-list no-time th-list">{archived.map(row)}</ul>}
            </section>
          )}
        </div>
        <button className="set-open" type="button" onClick={() => setSettingsOpen(true)}><GearIcon />Settings</button>
        <p className="drawer-foot">{p.household}</p>
        {menu && target && (
          <div className="menu" role="menu" aria-label="Chat options" style={{ top: menu.top, right: 8 }}>
            <button type="button" role="menuitem" autoFocus onClick={() => { setMenu(null); p.onArchive(target.id, !target.archived); }}><BoxIcon />{target.archived ? "Move back to chats" : "Archive"}</button>
            <button type="button" role="menuitem" className="danger" onClick={() => { setMenu(null); p.onDelete(target.id); }}><TrashIcon />Delete</button>
          </div>
        )}
      </nav>
      {settingsOpen && <SettingsPanel mode={p.mode} settings={p.settings} theme={p.theme} onChange={p.onSettings} onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}

function Row({ chat, current, now, menuOpen, onOpen, onMenu, onSwipeDelete }: {
  chat: Chat; current: boolean; now: number; menuOpen: boolean; onOpen: () => void; onMenu: (btn: HTMLElement) => void; onSwipeDelete: () => void;
}) {
  const slide = useRef<HTMLDivElement>(null);
  const li = useRef<HTMLLIElement>(null);
  const sw = useRef<{ x: number; y: number; dx: number; on: boolean } | null>(null);
  const [armed, setArmed] = useState(false);
  const suppress = useRef(false);
  const when = new Date(chat.updated);
  const label = now - chat.updated < 86_400_000 && when.getDate() === new Date(now).getDate()
    ? when.toTimeString().slice(0, 5)
    : when.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

  const end = (cancel: boolean) => {
    const s = sw.current;
    sw.current = null;
    if (!s?.on || !slide.current || !li.current) return;
    suppress.current = true;
    setTimeout(() => (suppress.current = false), 300);
    const del = !cancel && s.dx > li.current.offsetWidth * 0.4;
    slide.current.classList.add("snap");
    slide.current.style.transform = del ? "translateX(100%)" : "";
    setArmed(false);
    if (del) setTimeout(onSwipeDelete, 200);
  };

  return (
    <li ref={li} className={`th${armed ? " armed" : ""}`}>
      <div className="th-under" aria-hidden="true"><TrashIcon /><span>Delete</span></div>
      <div ref={slide} className="th-slide"
        onPointerDown={(e) => { if (e.button <= 0) sw.current = { x: e.clientX, y: e.clientY, dx: 0, on: false }; }}
        onPointerMove={(e) => {
          const s = sw.current;
          if (!s || !slide.current || !li.current) return;
          const dx = e.clientX - s.x, dy = e.clientY - s.y;
          if (!s.on) {
            if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { sw.current = null; return; }
            if (dx > 10 && dx > Math.abs(dy)) { s.on = true; slide.current.setPointerCapture?.(e.pointerId); slide.current.classList.remove("snap"); } else return;
          }
          s.dx = Math.max(0, dx);
          slide.current.style.transform = `translateX(${s.dx}px)`;
          setArmed(s.dx > li.current.offsetWidth * 0.4);
        }}
        onPointerUp={() => end(false)}
        onPointerCancel={() => end(true)}
        onClickCapture={(e) => { if (suppress.current) { e.preventDefault(); e.stopPropagation(); } }}>
        <button type="button" className="th-open" aria-current={current || undefined} onClick={onOpen}>
          <b>{chat.title}</b><span>{label}</span>
        </button>
        <button type="button" className="ib th-more" aria-haspopup="menu" aria-expanded={menuOpen} aria-label={`Options for ${chat.title}`}
          onClick={(e) => onMenu(e.currentTarget)}><DotsIcon /></button>
      </div>
    </li>
  );
}

const LANGS: { lang: Lang; label: string }[] = [{ lang: "auto", label: "Auto" }, { lang: "pt", label: "Português" }, { lang: "en", label: "English" }];
// The voice model recognises Portuguese and English by itself; this only sets the language Alfred answers in.
const LANG_HINT: Record<Lang, string> = {
  auto: "Alfred answers in the language you speak or type.",
  pt: "Alfred always answers in Portuguese. You can still speak either language.",
  en: "Alfred always answers in English. You can still speak either language.",
};
const AUTO: { days: AutoDelete; label: string }[] = [{ days: 0, label: "Never" }, { days: 30, label: "After 30 days" }, { days: 7, label: "After 7 days" }];
const HINT: Record<AutoDelete, string> = { 0: "Chats stay until you delete them.", 30: "Chats older than 30 days are deleted.", 7: "Chats older than 7 days are deleted." };

/** Over the chats list; its close button goes back to the chats. Further settings go here too. */
function SettingsPanel({ mode, settings, theme, onChange, onClose }: { mode: ModeWords | null; settings: Settings; theme: Theme; onChange: (s: Partial<Settings>) => void; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => { close.current?.focus(); }, []);
  return (
    <section className="drawer setpanel" aria-labelledby="set-title">
      <div className="drawer-head">
        <h2 id="set-title">Settings</h2>
        <button ref={close} className="ib" type="button" aria-label="Back to chats" onClick={onClose}><CloseIcon /></button>
      </div>
      {mode && (
        <div className="set">
          <span className="al-label">Mode</span>
          <p><b>{mode.name}</b></p>
          <p className="dim">{mode.how}</p>
        </div>
      )}
      <div className="set">
        <span className="al-label">Auto-delete chats</span>
        <div className="seg" role="group" aria-label="Auto-delete chats">
          {AUTO.map((a) => <button key={a.days} type="button" aria-pressed={settings.autoDelete === a.days} onClick={() => onChange({ autoDelete: a.days })}>{a.label}</button>)}
        </div>
        <p className="dim">{HINT[settings.autoDelete]}</p>
      </div>
      <div className="set">
        <span className="al-label">Appearance</span>
        <div className="seg" role="group" aria-label="Theme">
          {(["light", "dark"] as const).map((t) => <button key={t} type="button" aria-pressed={theme === t} onClick={() => onChange({ theme: t })}>{t === "light" ? "Light" : "Dark"}</button>)}
        </div>
      </div>
      <div className="set">
        <span className="al-label">Language</span>
        <div className="seg" role="group" aria-label="Language">
          {LANGS.map((l) => <button key={l.lang} type="button" aria-pressed={settings.lang === l.lang} onClick={() => onChange({ lang: l.lang })}>{l.label}</button>)}
        </div>
        <p className="dim">{LANG_HINT[settings.lang]}</p>
      </div>
      <div className="set">
        <span className="al-label">Voice</span>
        <div className="seg" role="group" aria-label="Read replies aloud">
          {[true, false].map((on) => <button key={String(on)} type="button" aria-pressed={settings.speakReplies === on} onClick={() => onChange({ speakReplies: on })}>{on ? "Read replies aloud" : "Text only"}</button>)}
        </div>
      </div>
    </section>
  );
}
