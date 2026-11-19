import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import App from "./App";
import type { HomeApi } from "./api/client";
import { HttpHomeApi } from "./api/http";
import { DEVICE_IDS } from "./api/mock/houses";
import { MockHomeApi } from "./api/mock/MockHomeApi";

const NOW = new Date(2026, 8, 21, 9, 12).getTime();
/** The demo house; `voice`: as when npm run demo found the laptop's voice model. */
const app = (house: "demo" | "kit" = "demo", { voice = false } = {}) => {
  const api = new MockHomeApi(house, 0);
  if (voice) api.transcribe = async () => ({ text: "liga a luz", lang: null, ms: 400 });
  return render(<App api={api} homeKey={`test-${house}`} clock={() => NOW} />);
};

const LOOK_ONLY = "blocked: look-only mode, nothing is sent (start npm run home:live to switch things)";
/** Your home started with npm run home: the API's look-only answers, over the demo house's data. */
function lookOnlyHome({ learning = null as { daysWatched: number } | null, evening = false } = {}): HomeApi {
  const m = new MockHomeApi("demo", 0);
  return {
    kind: "http",
    getHome: async () => {
      const h = await m.getHome();
      return { ...h, now: evening ? h.now.replace(/T\d\d/, "T21") : h.now, live: false, dryRun: true, lookOnly: true };
    },
    getStatus: async () => {
      const s = await m.getStatus();
      // Alfred's 08:45 change wasn't sent in look-only: nothing changed, it shows as what he would have done
      const wouldHave = { ...s.planned[0]!, id: "decision-20", at: s.since!, title: "Out for the day", does: "Lamp off", status: "failed" as const };
      return learning ? { ...s, byAlfred: false, changes: [], learning, notSent: [] } : { ...s, byAlfred: false, changes: [], notSent: [wouldHave] };
    },
    setControl: async (deviceId) => ({ status: "blocked", verified: null, deviceId, reason: LOOK_ONLY }),
    skip: (id) => m.skip(id), unskip: (id) => m.unskip(id), answer: (id, yes) => m.answer(id, yes), undo: (id) => m.undo(id),
    chat: async () => ({ text: "I tried to turn the lamp on.", blocks: [], lang: "en",
      actions: [{ status: "blocked", verified: null, deviceId: DEVICE_IDS.light, body: { type: "power", value: "on" }, reason: LOOK_ONLY }] }),
  };
}

test("Alfred opens with what it did and a suggestion to answer", async () => {
  app();
  expect(await screen.findByText(/so I switched to Out for the day at 08:45/)).toBeInTheDocument();
  const ask = await screen.findByRole("group", { name: "Warm up before Ana gets home" });
  await userEvent.click(within(ask).getByRole("button", { name: "Not now" }));
  expect(await screen.findByText("All right, I won’t.")).toBeInTheDocument();
});

test("typing a request gets Alfred's answer and changes the house", async () => {
  app();
  await screen.findByText(/so I switched/);
  await userEvent.type(screen.getByRole("textbox", { name: "Ask Alfred" }), "Turn the TV on");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("TV on.")).toBeInTheDocument();
  expect(screen.getByRole("switch", { name: "TV" })).toHaveAttribute("aria-checked", "true");
});

test("the mic shows until there is text, then send", async () => {
  app("demo", { voice: true });
  const box = await screen.findByRole("textbox", { name: "Ask Alfred" });
  expect(screen.getByRole("button", { name: "Speak to Alfred" })).toBeInTheDocument();
  await userEvent.type(box, "hi");
  expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Speak to Alfred" })).not.toBeInTheDocument();
});

test("chats are grouped by age; deleting one from its ⋯ menu can be undone", async () => {
  app();
  await screen.findByText(/so I switched/);
  await userEvent.click(screen.getByRole("button", { name: "Conversations" }));
  const nav = screen.getByRole("navigation", { name: "Conversations" });
  expect(within(nav).getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Today", "This week", "Older"]);
  await userEvent.click(within(nav).getByRole("button", { name: "Options for Kettle plug off" }));
  await userEvent.click(within(nav).getByRole("menuitem", { name: "Delete" }));
  expect(within(nav).queryByText("Kettle plug off")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(await within(nav).findByText("Kettle plug off")).toBeInTheDocument();
});

test("Settings sit over the chats; auto-delete after 7 days removes the older chats", async () => {
  app();
  await screen.findByText(/so I switched/);
  await userEvent.click(screen.getByRole("button", { name: "Conversations" }));
  await userEvent.click(screen.getByRole("button", { name: "Settings" }));
  await userEvent.click(screen.getByRole("button", { name: "After 7 days" }));
  expect(screen.getByRole("status")).toHaveTextContent("3 older chats deleted");
  await userEvent.click(screen.getByRole("button", { name: "Back to chats" }));
  expect(within(screen.getByRole("navigation", { name: "Conversations" })).queryByRole("heading", { name: "Older" })).not.toBeInTheDocument();
});

test("the app starts light", async () => {
  app();
  await screen.findByText(/so I switched/);
  expect(document.documentElement.dataset.theme).toBe("light");
});

test("Language is Auto by default and can be fixed to one", async () => {
  app();
  await screen.findByText(/so I switched/);
  await userEvent.click(screen.getByRole("button", { name: "Conversations" }));
  await userEvent.click(screen.getByRole("button", { name: "Settings" }));
  const lang = screen.getByRole("group", { name: "Language" });
  expect(within(lang).getByRole("button", { name: "Auto" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(within(lang).getByRole("button", { name: "Português" }));
  expect(screen.getByText(/always answers in Portuguese/)).toBeInTheDocument();
});

test("the demo house without the laptop's voice model has no microphone: no pretend listening", async () => {
  app();
  await screen.findByText(/so I switched/);
  expect(screen.getByText("Demo house: simulated. Nothing reaches a real home.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Speak to Alfred" })).not.toBeInTheDocument();
});

test("look-only: a switch that isn't sent doesn't move, and the app says it didn't happen", async () => {
  render(<App api={lookOnlyHome()} homeKey="test-look" clock={() => NOW} />);
  expect(await screen.findByText(/^Look-only: Alfred sees your home/)).toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: "Devices" })[0]!); // the header's
  const tv = screen.getByRole("switch", { name: "TV" });
  expect(tv).toHaveAttribute("aria-checked", "false");
  await userEvent.click(tv);
  expect(await screen.findByText("Didn’t turn the tv on: look-only mode, nothing is sent (start npm run home:live to switch things)")).toBeInTheDocument();
  expect(screen.getByRole("switch", { name: "TV" })).toHaveAttribute("aria-checked", "false");
  expect(screen.queryByText(/^You turned/)).not.toBeInTheDocument();
});

test("look-only: the card shows what Alfred would have done, and a reply says what didn't happen", async () => {
  render(<App api={lookOnlyHome()} homeKey="test-look-2" clock={() => NOW} />);
  await userEvent.click(await screen.findByRole("button", { expanded: false, name: /Right now/ }));
  expect(screen.getByText("Not sent · look-only")).toBeInTheDocument();
  expect(screen.getByText("Would have: Lamp off")).toBeInTheDocument();
  await userEvent.type(screen.getByRole("textbox", { name: "Ask Alfred" }), "Turn the lamp on");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("Didn’t turn the light on: look-only mode, nothing is sent (start npm run home:live to switch things)")).toBeInTheDocument();
});

test("a home that is still learning says so, and the greeting follows the home's clock", async () => {
  render(<App api={lookOnlyHome({ learning: { daysWatched: 2 }, evening: true })} homeKey="test-learning" clock={() => NOW} />);
  expect(await screen.findByText(/^Good evening\. I’m still learning your home \(2 days watched\)/)).toBeInTheDocument();
  expect(screen.getByText("Still learning your home · 2 days watched")).toBeInTheDocument();
});

test("without a voice model there is no microphone, only typing", async () => {
  const api = new HttpHomeApi("/api", async () => new Response("{}", { status: 501 }));
  await api.checkVoice();
  render(<App api={api} homeKey="test-http" clock={() => NOW} />);
  await screen.findByRole("alert"); // this fake server has no home either: the page says so
  expect(screen.queryByRole("button", { name: "Speak to Alfred" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
});

test("the theme can be set in Settings", async () => {
  app();
  await screen.findByText(/so I switched/);
  await userEvent.click(screen.getByRole("button", { name: "Conversations" }));
  await userEvent.click(screen.getByRole("button", { name: "Settings" }));
  await userEvent.click(screen.getByRole("button", { name: "Light" }));
  expect(document.documentElement.dataset.theme).toBe("light");
  await userEvent.click(screen.getByRole("button", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
});

test("opening the app again carries on with today's chat instead of starting another", async () => {
  const first = app();
  await screen.findByText(/so I switched/);
  first.unmount();
  app();
  await screen.findByText(/so I switched/);
  await userEvent.click(screen.getByRole("button", { name: "Conversations" }));
  const today = within(screen.getByRole("navigation", { name: "Conversations" })).getByRole("heading", { name: "Today" }).parentElement!;
  expect(within(today).getAllByRole("listitem")).toHaveLength(1);
});

test("the kit home opens without the demo house's suggestion about Ana", async () => {
  app("kit");
  expect(await screen.findByText(/so I switched to Out for the day/)).toBeInTheDocument();
  expect(screen.queryByText(/Ana/)).not.toBeInTheDocument();
});
