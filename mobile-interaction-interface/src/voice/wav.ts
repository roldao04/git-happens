// Speech for the server's model (Parakeet TDT 0.6B v3): 16 kHz, mono, 16-bit PCM WAV, made in the browser
// so the server needs no ffmpeg.

export const RATE = 16000;

/** Averages (down) or interpolates (up) to 16 kHz. */
export function resampleTo16k(input: Float32Array, rate: number): Float32Array {
  if (rate === RATE) return input;
  const ratio = rate / RATE;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = i * ratio;
    const end = Math.min(input.length, (i + 1) * ratio);
    if (ratio > 1) {
      let sum = 0, n = 0;
      for (let j = Math.floor(start); j < end; j++) { sum += input[j] ?? 0; n++; }
      out[i] = n ? sum / n : 0;
    } else {
      const j = Math.floor(start), f = start - j;
      out[i] = (input[j] ?? 0) * (1 - f) + (input[j + 1] ?? input[j] ?? 0) * f;
    }
  }
  return out;
}

/** A WAV file: 44-byte header, then the samples as signed 16-bit little-endian. */
export function encodeWav(samples: Float32Array, rate = RATE): ArrayBuffer {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const text = (at: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i)); };
  text(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  v.setUint32(16, 16, true); // fmt chunk size
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); // bytes per second
  v.setUint16(32, 2, true); // block align
  v.setUint16(34, 16, true); // bits per sample
  text(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i] ?? 0));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

/** How loud a chunk is, 0 to 1, for the level bars. */
export function level(chunk: Float32Array): number {
  let sum = 0;
  for (const x of chunk) sum += x * x;
  return Math.min(1, Math.sqrt(sum / Math.max(1, chunk.length)) * 4);
}

export const SPEECH_LEVEL = 0.15;
export const SILENCE_LEVEL = 0.06;
export const END_SILENCE_MS = 1200;

/** Speech has been heard and has been followed by enough quiet: the person has finished talking. */
export function endOfSpeech(levels: number[], chunkMs: number, silenceMs = END_SILENCE_MS): boolean {
  const spoke = levels.findIndex((l) => l >= SPEECH_LEVEL);
  if (spoke < 0) return false;
  let quiet = 0;
  for (let i = levels.length - 1; i > spoke && (levels[i] ?? 1) < SILENCE_LEVEL; i--) quiet += chunkMs;
  return quiet >= silenceMs;
}
