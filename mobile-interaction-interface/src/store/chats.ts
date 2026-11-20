import type { ChatReply } from "../api/types";

// Conversations, kept on the phone (localStorage, one key per home). Pure functions, plus load and save.

export type Turn =
  | { who: "me"; at: number; text: string; voice?: boolean }
  | { who: "alfred"; at: number; reply: ChatReply; suggestionId?: string; alertId?: string }
  | { who: "event"; at: number; text: string };

export interface Chat {
  id: string;
  title: string;
  created: number;
  updated: number;
  archived: boolean;
  turns: Turn[];
}

export const DAY_MS = 24 * 60 * 60 * 1000;
export type AutoDelete = 0 | 7 | 30;

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Whole calendar days between a chat's last message and now. */
export function ageDays(updated: number, now: number): number {
  return Math.max(0, Math.round((startOfDay(now) - startOfDay(updated)) / DAY_MS));
}

export interface Group { name: "Today" | "This week" | "Older"; chats: Chat[] }

/** Today, This week (1–6 days), Older (7 days or more); newest first; archived chats left out. */
export function groupChats(chats: Chat[], now: number): Group[] {
  const live = chats.filter((c) => !c.archived).sort((a, b) => b.updated - a.updated);
  const groups: Group[] = [
    { name: "Today", chats: live.filter((c) => ageDays(c.updated, now) === 0) },
    { name: "This week", chats: live.filter((c) => { const a = ageDays(c.updated, now); return a >= 1 && a <= 6; }) },
    { name: "Older", chats: live.filter((c) => ageDays(c.updated, now) >= 7) },
  ];
  return groups.filter((g) => g.chats.length > 0);
}

/** Chats older than `days` go (0 keeps everything). Returns what stays and what went, for undo. */
export function autoDelete(chats: Chat[], days: AutoDelete, now: number): { kept: Chat[]; gone: Chat[] } {
  if (!days) return { kept: chats, gone: [] };
  const gone = chats.filter((c) => ageDays(c.updated, now) > days);
  return { kept: chats.filter((c) => !gone.includes(c)), gone };
}

export function newChat(now: number, id = `chat-${now}-${Math.random().toString(36).slice(2, 7)}`): Chat {
  return { id, title: "New chat", created: now, updated: now, archived: false, turns: [] };
}

/** The first thing a person says names the chat. */
export function titled(chat: Chat, text: string): Chat {
  return chat.title === "New chat" ? { ...chat, title: text.length > 40 ? `${text.slice(0, 39)}…` : text } : chat;
}

const KEY = (home: string) => `alfred-mobile-chats-${home}`;

export function loadChats(home: string, now: number): Chat[] {
  try {
    const raw = localStorage.getItem(KEY(home));
    if (raw) {
      const parsed = JSON.parse(raw) as unknown;
      if (Array.isArray(parsed)) return parsed as Chat[];
    }
  } catch {
    /* storage blocked or unreadable: start over */
  }
  return seedChats(now);
}

export function saveChats(home: string, chats: Chat[]): void {
  try {
    localStorage.setItem(KEY(home), JSON.stringify(chats));
  } catch {
    /* storage blocked: chats last until the page closes */
  }
}

const said = (text: string): ChatReply => ({ text, blocks: [], actions: [], lang: "en" });

/** A few earlier chats, so the list shows its groups the first time the app opens. */
export function seedChats(now: number): Chat[] {
  const ago = (days: number, hhmm: string) => {
    const d = new Date(startOfDay(now) - days * DAY_MS);
    const [h, m] = hhmm.split(":").map(Number) as [number, number];
    d.setHours(h, m);
    return d.getTime();
  };
  const chat = (id: string, title: string, at: number, turns: Turn[]): Chat => ({ id, title, created: at, updated: at, archived: false, turns });
  return [
    chat("seed-kettle", "Kettle plug off", ago(1, "22:40"), [
      { who: "me", at: ago(1, "22:40"), text: "Turn the kettle plug off" },
      { who: "alfred", at: ago(1, "22:40"), reply: said("Kettle off. It was drawing 0 W.") },
    ]),
    chat("seed-evening", "Skip evening tonight", ago(2, "17:05"), [
      { who: "me", at: ago(2, "17:05"), text: "Skip evening tonight", voice: true },
      { who: "alfred", at: ago(2, "17:05"), reply: said("Skipped Evening today. It runs again tomorrow, around 18:32.") },
    ]),
    chat("seed-door", "Is the front door closed?", ago(4, "22:15"), [
      { who: "me", at: ago(4, "22:15"), text: "Is the front door closed?" },
      { who: "alfred", at: ago(4, "22:15"), reply: said("Yes. It’s been closed since 18:44, when it opened for about 30 s.") },
    ]),
    chat("seed-morning", "Morning at 07:45", ago(9, "08:02"), [
      { who: "me", at: ago(9, "08:02"), text: "Move morning to 07:45", voice: true },
      { who: "alfred", at: ago(9, "08:02"), reply: said("Done. Morning now starts around 07:45.") },
    ]),
    chat("seed-smoke", "Smoke alarm signal", ago(16, "11:30"), [
      { who: "me", at: ago(16, "11:30"), text: "Why is the smoke alarm orange?" },
      { who: "alfred", at: ago(16, "11:30"), reply: said("Its signal to the security hub is weak. It still senses smoke, but an alert could reach your phone late. Moving the hub closer usually helps.") },
    ]),
    chat("seed-first", "What can you do?", ago(41, "20:14"), [
      { who: "me", at: ago(41, "20:14"), text: "What can you do?" },
      { who: "alfred", at: ago(41, "20:14"), reply: said("I watch what the devices report and learn the home’s habits. After a few days I plan ahead for them, and you can skip or undo anything I do.") },
    ]),
  ];
}
