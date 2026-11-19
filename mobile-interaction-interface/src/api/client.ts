import type { ActionResult, ChatReply, ControlBody, HomeSnapshot, PlanItem, Status, Transcript } from "./types";

/** Everything the app asks of the home. MockHomeApi answers from memory; HttpHomeApi asks the Python API. */
export interface HomeApi {
  readonly kind: "mock" | "http";
  getHome(): Promise<HomeSnapshot>;
  getStatus(): Promise<Status>;
  setControl(deviceId: string, body: ControlBody): Promise<ActionResult>;
  /** A planned "do" decision: not today. */
  skip(id: string): Promise<PlanItem>;
  /** Undo `skip`. */
  unskip(id: string): Promise<PlanItem>;
  /** A "suggest" decision: yes (Alfred does it) or no. */
  answer(id: string, yes: boolean): Promise<PlanItem>;
  /** Put back what one of Alfred's decisions changed. */
  undo(decisionId: string): Promise<ActionResult[]>;
  chat(text: string, opts?: { lang?: string | null }): Promise<ChatReply>;
  /** Speech to text on the server (Parakeet). Absent when the server has no voice model. */
  transcribe?: (wav: Blob) => Promise<Transcript>;
}

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}
