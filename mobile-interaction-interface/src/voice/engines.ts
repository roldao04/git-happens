import type { HomeApi } from "../api/client";
import { encodeWav, endOfSpeech, level, resampleTo16k } from "./wav";

// Speech to text, only on this home's own computer: the phone records 16 kHz WAV and the Python API turns
// it into text with Parakeet TDT 0.6B v3 (it recognises Portuguese and English by itself). No cloud
// speech: without the model (npm run setup said no) there is simply no microphone in the app.
// The demo house uses the same model, through the voice-only server npm run demo starts.

export interface Heard { text: string; lang: string | null }
export interface ListenOptions {
  onPartial(text: string): void;
  onLevel(level: number): void;
  /** The engine finished on its own: the person stopped talking. */
  onEnd(): void;
}
export interface VoiceEngine {
  readonly name: "server";
  start(opts: ListenOptions): Promise<void>;
  stop(): Promise<Heard>;
  cancel(): void;
}

export const MAX_TALK_MS = 15_000;

export class InsecurePage extends Error {
  constructor() {
    super("Voice needs the secure address (https://…). Open the link npm run demo or npm run home prints.");
    this.name = "InsecurePage";
  }
}

// ---- server (Parakeet through /api/voice) ----

export class ServerVoice implements VoiceEngine {
  readonly name = "server" as const;
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private node: ScriptProcessorNode | null = null;
  private chunks: Float32Array[] = [];

  constructor(private readonly transcribe: NonNullable<HomeApi["transcribe"]>) {}

  async start(opts: ListenOptions): Promise<void> {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new InsecurePage();
    this.chunks = [];
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    this.ctx = new AudioContext();
    const source = this.ctx.createMediaStreamSource(this.stream);
    // ScriptProcessorNode is old but runs everywhere without a separate worklet file.
    this.node = this.ctx.createScriptProcessor(4096, 1, 1);
    const chunkMs = (4096 / this.ctx.sampleRate) * 1000;
    const levels: number[] = [];
    let ended = false;
    this.node.onaudioprocess = (e) => {
      const data = new Float32Array(e.inputBuffer.getChannelData(0));
      this.chunks.push(data);
      const l = level(data);
      levels.push(l);
      opts.onLevel(l);
      if (!ended && (endOfSpeech(levels, chunkMs) || levels.length * chunkMs >= MAX_TALK_MS)) {
        ended = true; // the person stopped talking (or talked for 15 s): send what we have
        opts.onEnd();
      }
    };
    source.connect(this.node);
    this.node.connect(this.ctx.destination);
  }

  async stop(): Promise<Heard> {
    const rate = this.ctx?.sampleRate ?? 48000;
    this.release();
    const all = new Float32Array(this.chunks.reduce((n, c) => n + c.length, 0));
    let at = 0;
    for (const c of this.chunks) { all.set(c, at); at += c.length; }
    if (all.length < rate / 4) return { text: "", lang: null };
    const wav = new Blob([encodeWav(resampleTo16k(all, rate))], { type: "audio/wav" });
    const t = await this.transcribe(wav);
    return { text: t.text, lang: t.lang };
  }

  cancel(): void {
    this.release();
  }

  private release(): void {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.node = null; this.stream = null; this.ctx = null;
  }
}

/** This home's own model, when its computer has it; otherwise no microphone. */
export function pickVoice(api: HomeApi): VoiceEngine | null {
  return api.transcribe ? new ServerVoice(api.transcribe) : null;
}
