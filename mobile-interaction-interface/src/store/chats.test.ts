import { expect, test } from "vitest";
import { DAY_MS, ageDays, autoDelete, groupChats, loadChats, newChat, saveChats, seedChats, titled, type Chat } from "./chats";

const NOW = new Date(2026, 8, 21, 9, 12).getTime();
const chat = (id: string, daysAgo: number, archived = false): Chat => ({ ...newChat(NOW - daysAgo * DAY_MS, id), archived, turns: [{ who: "event", at: NOW, text: "x" }] });

test("chats are grouped as Today, This week and Older, newest first, archived ones left out", () => {
  const groups = groupChats([chat("old", 9), chat("today", 0), chat("week", 3), chat("arch", 1, true), chat("six", 6), chat("seven", 7)], NOW);
  expect(groups.map((g) => [g.name, g.chats.map((c) => c.id)])).toEqual([
    ["Today", ["today"]],
    ["This week", ["week", "six"]],
    ["Older", ["seven", "old"]],
  ]);
});

test("auto-delete keeps chats younger than the limit and returns the rest for undo", () => {
  const all = [chat("a", 2), chat("b", 8), chat("c", 41)];
  expect(autoDelete(all, 0, NOW).gone).toEqual([]);
  expect(autoDelete(all, 30, NOW).gone.map((c) => c.id)).toEqual(["c"]);
  expect(autoDelete(all, 7, NOW).kept.map((c) => c.id)).toEqual(["a"]);
});

test("the first thing a person says names a new chat", () => {
  expect(titled(newChat(NOW), "Is the door closed?").title).toBe("Is the door closed?");
  expect(titled({ ...newChat(NOW), title: "Kept" }, "x").title).toBe("Kept");
  expect(titled(newChat(NOW), "x".repeat(60)).title).toHaveLength(40);
});

test("chats are kept per home, and a home with none starts with the seeded ones", () => {
  expect(loadChats("demo", NOW).map((c) => c.id)).toEqual(seedChats(NOW).map((c) => c.id));
  saveChats("demo", [chat("mine", 0)]);
  expect(loadChats("demo", NOW).map((c) => c.id)).toEqual(["mine"]);
  expect(loadChats("kit", NOW)).toHaveLength(seedChats(NOW).length);
});

test("age counts calendar days, not 24-hour blocks", () => {
  const lateYesterday = new Date(2026, 8, 20, 23, 50).getTime();
  expect(ageDays(lateYesterday, NOW)).toBe(1);
  expect(ageDays(NOW - 60_000, NOW)).toBe(0);
});
