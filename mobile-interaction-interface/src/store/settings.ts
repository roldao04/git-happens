import type { AutoDelete } from "./chats";

// The phone's own settings (localStorage). Light unless someone chooses dark.

export type Theme = "light" | "dark";
/** Which language Alfred answers in. Auto: the language the person spoke or typed in. */
export type Lang = "auto" | "pt" | "en";
export interface Settings { theme: Theme; autoDelete: AutoDelete; speakReplies: boolean; lang: Lang }

const KEY = "alfred-mobile-settings";
export const DEFAULTS: Settings = { theme: "light", autoDelete: 0, speakReplies: true, lang: "auto" };

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const s = JSON.parse(raw) as Partial<Settings>;
    return {
      theme: s.theme === "dark" ? "dark" : "light",
      autoDelete: s.autoDelete === 7 || s.autoDelete === 30 ? s.autoDelete : 0,
      speakReplies: s.speakReplies !== false,
      lang: s.lang === "pt" || s.lang === "en" ? s.lang : "auto",
    };
  } catch {
    return DEFAULTS;
  }
}

export function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage blocked */
  }
}
