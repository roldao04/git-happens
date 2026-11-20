import { expect, test } from "vitest";
import { clockText } from "./words";

test("a time in ms reads as the local HH:MM", () => {
  expect(clockText(new Date(2026, 8, 21, 9, 5).getTime())).toBe("09:05");
  expect(clockText(new Date(2026, 8, 21, 23, 40).getTime())).toBe("23:40");
});
