import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "vitest";

const css = readFileSync(resolve(__dirname, "dashboard.css"), "utf8");

function fontSize(selector: string): number | null {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rule = new RegExp(`(?:^|[},])\\s*${escaped}\\s*\\{([^}]*)\\}`, "m").exec(css);
  const size = rule?.[1] ? /font(?:-size)?:[^;]*?(\d+)px/.exec(rule[1]) : null;
  return size?.[1] ? Number(size[1]) : null;
}

test.each([
    ".al-new",
  ".al-list time",
  ".tab",
  ".more",
  ".pg-head p",
  ".devs .n",
  ".devs .s",
  ".devs .v",
  ".later p",
  ".note",
  ".chart-day",
  ".recent-days .al-what b",
  ".steps p",
  ".al-empty p",
  ".attn b",
  ".attn span",
  ".lane-l",
  ".ticks2",
  ".evt time",
  ".evt .body span",
  ".sg2 li",
  ".sim-toggle",
  ".lt-row .when",
  ".fold summary",
  ".see-day",
  ".house-temp",
  ".alfred-note p",
  ".alfred-note .sign",
])("%s is readable from the back of a room", (selector) => {
  expect(fontSize(selector)).toBeGreaterThanOrEqual(16);
});

test("the still-learning counter fills its row instead of collapsing to zero width", () => {
  expect(css).toMatch(/\.al-empty \.al-counter\s*\{[^}]*width:\s*100%/);
});

test("the playback pill floats in the bottom right corner", () => {
  expect(css).toMatch(/(^|\n)\.sim\{[^}]*position:fixed[^}]*right:[^}]*bottom:/);
});

test("the pill's buttons are at least 44px tall", () => {
  expect(css).toMatch(/(^|\n)\.sim \.al-btn,\.sim-toggle\{[^}]*min-height:44px/);
});

test("no rules are left for the old top-bar controls and bottom slider", () => {
  expect(css).not.toMatch(/\.scrub-bar|\.ph-play|\.controls|\.al-clock|\.lt-head|\.rows|\.latest|\.pairs/);
});

test("the folded weak sensors show an arrow that turns when opened", () => {
  expect(css).toMatch(/\.fold summary::after\{content:"▸"/);
  expect(css).toMatch(/\.fold\[open\] summary::after\{content:"▾"/);
});

test("home sets the picture beside the headline on wide screens and stacks it on a phone", () => {
  expect(css).toMatch(/(^|\n)\.home\{[^}]*grid-template-columns:/);
  expect(phone).toMatch(/\.home\{[^}]*grid-template-columns:minmax\(0,1fr\)/);
});

test("the lamp's glow uses the accent color", () => {
  expect(css).toMatch(/\.house-glow\{[^}]*var\(--accent\)/);
});

test("the devices page has a side column next to its list", () => {
  expect(css).toMatch(/\.two\s*\{[^}]*grid-template-columns:[^}]*340px/);
});

test("the active tab is marked by more than color", () => {
  expect(css).toMatch(/\.tab\.on\s*\{[^}]*border-bottom-color/);
});

test("no bare .today rule, which would also hit the chart's today row", () => {
  expect(css).not.toMatch(/(^|[},\s])\.today[\s{,]/m);
});

const phone = /@media \(max-width:640px\)\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";

test("on a phone the three tabs become a bar fixed to the bottom", () => {
  expect(phone).toMatch(/\.tabs\{[^}]*position:fixed[^}]*bottom:0[^}]*repeat\(3,/);
  expect(phone).toMatch(/\.tab-ic\{[^}]*display:block/);
  expect(css).toMatch(/(^|\n)\.tab-ic\{[^}]*display:none/);
});

test("on a phone the pill sits just above the tab bar", () => {
  expect(phone).toMatch(/\.sim\{[^}]*bottom:calc\(var\(--tabbar-h\)/);
});

test("on a phone the lanes chart is left out", () => {
  expect(phone).toMatch(/\.lanes-sec\{[^}]*display:none/);
});

test("tabs are at least 44px tall on a phone", () => {
  expect(phone).toMatch(/\.tab\{[^}]*min-height:(4[4-9]|[5-9]\d)px/);
});
