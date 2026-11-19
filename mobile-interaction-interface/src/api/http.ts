import { ApiError, type HomeApi } from "./client";
import { parseAction, parseActions, parseHome, parsePlanItem, parseReply, parseStatus, parseTranscript } from "./parse";
import type { ActionResult, ChatReply, ControlBody, HomeSnapshot, PlanItem, Status, Transcript } from "./types";

/** The Python API (python -m source --api), reached through the dev server's /api proxy. */
export class HttpHomeApi implements HomeApi {
  readonly kind = "http" as const;
  transcribe?: (wav: Blob) => Promise<Transcript>;

  constructor(private readonly base = "./api", private readonly fetcher: typeof fetch = (...a) => fetch(...a)) {}

  /** Asks the server whether it has the voice model. Only then does the app offer the microphone. */
  async checkVoice(): Promise<boolean> {
    let ok = false;
    try {
      ok = (await this.fetcher(`${this.base}/voice`, { method: "GET" })).ok;
    } catch {
      ok = false;
    }
    this.transcribe = ok ? (wav) => this.call("voice", { method: "POST", body: wav, headers: { "Content-Type": "audio/wav" } }, parseTranscript) : undefined;
    return ok;
  }

  getHome(): Promise<HomeSnapshot> { return this.call("home", {}, parseHome); }
  getStatus(): Promise<Status> { return this.call("status", {}, parseStatus); }
  setControl(deviceId: string, body: ControlBody): Promise<ActionResult> {
    return this.call(`devices/${encodeURIComponent(deviceId)}`, json({ body }), (r) => parseAction(r));
  }
  skip(id: string): Promise<PlanItem> { return this.call(`plan/${encodeURIComponent(id)}/skip`, json({}), (r) => parsePlanItem(r)); }
  unskip(id: string): Promise<PlanItem> { return this.call(`plan/${encodeURIComponent(id)}/unskip`, json({}), (r) => parsePlanItem(r)); }
  answer(id: string, yes: boolean): Promise<PlanItem> { return this.call(`plan/${encodeURIComponent(id)}/answer`, json({ yes }), (r) => parsePlanItem(r)); }
  undo(decisionId: string): Promise<ActionResult[]> { return this.call("undo", json({ decision: decisionId }), parseActions); }
  chat(text: string, opts: { lang?: string | null } = {}): Promise<ChatReply> { return this.call("chat", json({ text, lang: opts.lang ?? null }), parseReply); }

  private async call<T>(path: string, init: RequestInit, parse: (raw: unknown) => T): Promise<T> {
    let res: Response;
    try {
      res = await this.fetcher(`${this.base}/${path}`, { cache: "no-store", ...init });
    } catch (e) {
      throw new ApiError(`can't reach Alfred (${e instanceof Error ? e.message : String(e)})`);
    }
    let raw: unknown = null;
    try {
      raw = await res.json();
    } catch {
      if (res.ok) throw new ApiError(`${path}: the answer is not JSON`, res.status);
    }
    if (!res.ok) {
      const msg = raw && typeof raw === "object" && "error" in raw ? String((raw as { error: unknown }).error) : `HTTP ${res.status}`;
      throw new ApiError(msg, res.status);
    }
    return parse(raw);
  }
}

function json(body: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } };
}
