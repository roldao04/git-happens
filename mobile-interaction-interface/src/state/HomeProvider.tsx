import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { HomeApi } from "../api/client";
import type { ActionResult, ControlBody, HomeSnapshot, PlanItem, Status, Value } from "../api/types";

// The home as the app sees it: the latest snapshot and status from the API, refreshed every few seconds
// for a real home. A control waits for the API's answer: only a write that was sent moves it (and it stays
// moved until the loop's next poll reads the device), so a write that wasn't sent never looks done.

export interface HomeCtx {
  api: HomeApi;
  home: HomeSnapshot | null;
  status: Status | null;
  error: string | null;
  /** When the last refresh worked (ms); the data is older than that when a refresh failed. */
  lastOk: number | null;
  /** Devices with a write on its way: their controls wait. */
  pending: ReadonlySet<string>;
  refresh(): Promise<void>;
  setControl(deviceId: string, body: ControlBody): Promise<ActionResult>;
  skip(id: string): Promise<PlanItem | null>;
  unskip(id: string): Promise<PlanItem | null>;
  answer(id: string, yes: boolean): Promise<PlanItem | null>;
  undo(decisionId: string): Promise<ActionResult[]>;
}

const Ctx = createContext<HomeCtx | null>(null);
const SENT_SHOWN_MS = 30_000; // a sent value is shown until a poll reads it, or this long at most

type Sent = Map<string, { deviceId: string; cap: string; value: Value; until: number }>;

/** The API's home with the values just sent on top, until the API reads them itself. */
function withSent(h: HomeSnapshot, sent: Sent, now: number): HomeSnapshot {
  if (!sent.size) return h;
  return {
    ...h,
    devices: h.devices.map((d) => {
      let values = d.values;
      for (const [k, w] of sent) {
        if (w.deviceId !== d.id) continue;
        if (now > w.until || values[w.cap] === w.value) { sent.delete(k); continue; } // too old, or caught up
        values = { ...values, [w.cap]: w.value };
      }
      return values === d.values ? d : { ...d, values };
    }),
  };
}

export function useHome(): HomeCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useHome needs a HomeProvider");
  return c;
}

export function HomeProvider({ api, pollMs, children }: { api: HomeApi; pollMs?: number; children: ReactNode }) {
  const [home, setHome] = useState<HomeSnapshot | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastOk, setLastOk] = useState<number | null>(null);
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());
  const live = useRef(true);
  const sent = useRef<Sent>(new Map());

  const refresh = useCallback(async () => {
    try {
      const [h, s] = await Promise.all([api.getHome(), api.getStatus()]);
      if (!live.current) return;
      setHome(withSent(h, sent.current, Date.now()));
      setStatus(s);
      setError(null);
      setLastOk(Date.now());
    } catch (e) {
      if (live.current) setError(e instanceof Error ? e.message : String(e)); // keep the last good data
    }
  }, [api]);

  useEffect(() => {
    live.current = true;
    void refresh();
    const every = pollMs ?? (api.kind === "http" ? 5000 : 0);
    const timer = every > 0 ? setInterval(() => void refresh(), every) : null;
    return () => {
      live.current = false;
      if (timer) clearInterval(timer);
    };
  }, [api, pollMs, refresh]);

  const setControl = useCallback(async (deviceId: string, body: ControlBody) => {
    setPending((p) => new Set(p).add(deviceId));
    let result: ActionResult;
    try {
      result = await api.setControl(deviceId, body);
    } catch (e) {
      result = { status: "failed", verified: null, reason: e instanceof Error ? e.message : String(e), deviceId, body };
    }
    if (result.status === "sent") {
      sent.current.set(`${deviceId}|${body.type}`, { deviceId, cap: body.type, value: body.value as Value, until: Date.now() + SENT_SHOWN_MS });
      setHome((h) => h && withSent(h, sent.current, Date.now()));
    }
    await refresh();
    setPending((p) => { const n = new Set(p); n.delete(deviceId); return n; });
    return result;
  }, [api, refresh]);

  const settle = useCallback(<T,>(p: Promise<T>, fallback: T) => p.then(async (x) => { await refresh(); return x; }, (e: unknown) => {
    setError(e instanceof Error ? e.message : String(e));
    return fallback;
  }), [refresh]);

  const value = useMemo<HomeCtx>(() => ({
    api, home, status, error, lastOk, pending, refresh, setControl,
    skip: (id) => settle(api.skip(id), null),
    unskip: (id) => settle(api.unskip(id), null),
    answer: (id, yes) => settle(api.answer(id, yes), null),
    undo: (id) => settle(api.undo(id), []),
  }), [api, home, status, error, lastOk, pending, refresh, setControl, settle]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
