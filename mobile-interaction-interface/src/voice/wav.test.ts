import { expect, test } from "vitest";
import { encodeWav, level, resampleTo16k } from "./wav";

test("a WAV is a 44-byte PCM header for 16 kHz mono, then two bytes a sample", () => {
  const buf = encodeWav(new Float32Array([0, 1, -1, 0.5]));
  const v = new DataView(buf);
  const text = (at: number, n: number) => String.fromCharCode(...new Uint8Array(buf, at, n));
  expect(buf.byteLength).toBe(44 + 8);
  expect(text(0, 4)).toBe("RIFF");
  expect(text(8, 4)).toBe("WAVE");
  expect(v.getUint16(22, true)).toBe(1); // mono
  expect(v.getUint32(24, true)).toBe(16000);
  expect(v.getUint16(34, true)).toBe(16); // bits
  expect(v.getUint32(40, true)).toBe(8);
  expect(v.getInt16(46, true)).toBe(32767);
  expect(v.getInt16(48, true)).toBe(-32768);
});

test("48 kHz becomes a third as many samples at 16 kHz, averaged", () => {
  const out = resampleTo16k(new Float32Array([1, 1, 1, 0, 0, 0]), 48000);
  expect(Array.from(out)).toEqual([1, 0]);
});

test("16 kHz is left as it is", () => {
  const input = new Float32Array([0.1, 0.2]);
  expect(resampleTo16k(input, 16000)).toBe(input);
});

test("silence has no level and loud speech reaches the top", () => {
  expect(level(new Float32Array(100))).toBe(0);
  expect(level(new Float32Array(100).fill(0.5))).toBe(1);
});
