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
  ".al-clock .al-sub",
  ".al-new",
  ".al-list time",
  ".tab",
  ".more",
  ".pg-head p",
  ".rows time",
  ".rows .t b",
  ".rows .t span",
  ".rows .a",
  ".devs .n",
  ".devs .s",
  ".devs .v",
  ".latest .al-label",
  ".lt-head",
  ".lt-row .ds",
  ".lt-row .num",
  ".lt-row .num small",
  ".later p",
  ".note",
  ".chart-day",
  ".recent-days .al-what b",
  ".steps p",
  ".al-empty p",
])("%s is readable from the back of a room", (selector) => {
  expect(fontSize(selector)).toBeGreaterThanOrEqual(16);
});

test("the still-learning counter fills its row instead of collapsing to zero width", () => {
  expect(css).toMatch(/\.al-empty \.al-counter\s*\{[^}]*width:\s*100%/);
});

test("the day slider stays on screen at the bottom", () => {
  expect(css).toMatch(/\.scrub-bar\s*\{[^}]*position:\s*sticky[^}]*bottom:\s*0/);
});

test("home puts the devices in a side column next to the feed", () => {
  expect(css).toMatch(/\.two\s*\{[^}]*grid-template-columns:[^}]*340px/);
});

test("the active tab is marked by more than color", () => {
  expect(css).toMatch(/\.tab\.on\s*\{[^}]*border-bottom-color/);
});

test("no bare .today rule, which would also hit the chart's today row", () => {
  expect(css).not.toMatch(/(^|[},\s])\.today[\s{,]/m);
});
