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
  ".al-card .al-evidence",
  ".al-card .al-when span",
  ".al-new",
  ".devices .al-what .changed",
  ".devices .al-what b",
  ".also summary",
])("%s is readable from the back of a room", (selector) => {
  expect(fontSize(selector)).toBeGreaterThanOrEqual(16);
});

test("the still-learning counter fills its row instead of collapsing to zero width", () => {
  expect(css).toMatch(/\.al-empty \.al-counter\s*\{[^}]*width:\s*100%/);
});
