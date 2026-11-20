"""The phone app's API (mobile-interaction-interface/), served from inside the loop: python -m source --api.

The loop owns the MCP session, the SQLite store and the latest poll, so the API runs in its process: a
small HTTP server in a thread that hands every request to the loop's own asyncio thread. Only that thread
touches SQLite and the MCP. Speech to text runs in the server's thread (CPU, no store).

A person's writes go through the same guard as Alfred's (source/guard.py), as a person: a plug may be
switched on, and the thermostat's hold and rate limit (which protect people from Alfred) don't apply.
The changes they cause are labelled "app" in `events`, so the learners count them as the person's.

GET  /api/home                  devices with values and controls, who is home, the heating, now
GET  /api/status                the "Right now" card: status, Alfred's changes, planned, suggestions
GET  /api/voice                 200 when this machine can turn speech into text, 501 when it can't
POST /api/devices/<id>          {"body": {...}}: a person's write
POST /api/plan/<id>/skip        a planned decision: not today      (…/unskip puts it back)
POST /api/plan/<id>/answer      {"yes": true|false}: a suggestion
POST /api/undo                  {"decision": "<id>"}: put back what a decision changed
POST /api/chat                  {"text", "lang"}: ask Alfred (the agent, as a person's request)
POST /api/voice                 audio/wav, 16 kHz mono -> {"text", "lang", "ms"}

Localhost only, checking Host and Origin like source/simulate_web.py: the app's dev server proxies /api to
it, over HTTPS for the phone. With --look-only every write answers "not sent" (npm run home).
Shapes: docs/phone-api.md and mobile-interaction-interface/src/api/types.ts.
"""
import asyncio
import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from . import anticipate, status, voice
from .actions import Actuator, DryRunActuator
from .anticipate import heat_now
from .context import build_context
from .guard import LOOK_ONLY_REASON, TARGET_MAX_C, TARGET_MIN_C, WRITE_TYPES, guarded_write
from .learn import to_local
from .model import State, WakeReason
from .wake import home_now

log = logging.getLogger("source.phone_api")

DEFAULT_PORT = 4790
MAX_JSON = 20_000
TIMEOUT_S, CHAT_TIMEOUT_S = 30, 180
TONES = [{"label": "Warm", "value": 0}, {"label": "Neutral", "value": 50}, {"label": "Cool", "value": 100}]
HIDDEN_TYPES = {"phone"}
RAW_CAPABILITIES = {"connectedClients"}  # the router's raw list: `people` says the same, readably


class ApiError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status = status_code


def controls_for(dev_type: str) -> list[dict]:
    """What a person can change on a device, as the app draws it (Control in the app's types.ts):
    guard.WRITE_TYPES, minus color_rgb (the app has no colour picker)."""
    caps = WRITE_TYPES.get(dev_type, set())
    out = []
    if "power" in caps:
        out.append({"kind": "switch", "capability": "power"})
    if "brightness" in caps:
        out.append({"kind": "level", "capability": "brightness", "min": 1, "max": 100, "step": 1})
    if "temperature" in caps:
        out.append({"kind": "tone", "capability": "temperature", "options": TONES})
    if "targetTemperature" in caps:
        out.append({"kind": "target", "capability": "targetTemperature", "min": TARGET_MIN_C, "max": TARGET_MAX_C, "step": 0.5})
    if "mode" in caps:
        out.append({"kind": "choice", "capability": "mode", "options": ["heat", "eco", "off"]})
    return out


def _iso(ms: int) -> str:
    return to_local(ms).replace(microsecond=0).isoformat(timespec="seconds")


def _plain(v):
    return v if v is None or isinstance(v, (str, int, float, bool)) else json.dumps(v, default=str)


class PhoneApi:
    """What the HTTP handler calls. The loop feeds it each poll (update) and takes the app's writes
    (take_writes) to label the changes they cause."""

    def __init__(self, store, actuator: Actuator | None, agent, *, household: str, dry_run: bool,
                 stale_ms: int, readback_delay_s: float = 0.0, look_only: bool = False):
        self.store = store
        self.look_only = look_only  # --look-only: the real home, but a person's writes are never sent
        self.actuator = actuator or DryRunActuator()
        self.agent = agent
        self.household = household
        self.dry_run = dry_run
        self.stale_ms = stale_ms
        self.readback_delay_s = readback_delay_s
        self.state: State = {}
        self.now_ms: int | None = None
        self.types: dict[str, str] = {}
        self.fresh: dict[str, list[str]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._writes: list[tuple[int, str, str, object, str]] = []
        self._server: ThreadingHTTPServer | None = None
        self.last_error: str | None = None  # why the loop couldn't read the house, until it can

    # ---- the loop's side ----

    def update(self, now_ms: int, state: State, types: dict[str, str], fresh: dict[str, list[str]]) -> None:
        self.now_ms, self.state, self.types, self.fresh = now_ms, state, types, fresh
        self.last_error = None

    def poll_failed(self, why: str) -> None:
        """A poll failed (no sign-in, the kit not answering): the app shows why instead of just waiting."""
        self.last_error = why

    def take_writes(self) -> list[tuple[int, str, str, object, str]]:
        out, self._writes = self._writes, []
        return out

    # ---- requests (run on the loop's thread) ----

    def _ready(self) -> int:
        if self.store is None:  # python -m source.voice --serve: the mock home's microphone, no house here
            raise ApiError(503, "this server only turns speech into text (for npm run demo): there's no house here")
        if self.now_ms is None:
            if self.last_error:
                raise ApiError(503, f"Alfred can't read the house: {self.last_error}")
            raise ApiError(503, "Alfred hasn't read the house yet: try again in a few seconds")
        return self.now_ms

    def _now(self) -> datetime:
        return to_local(self._ready())

    async def home(self) -> dict:
        now_ms = self._ready()
        devices: dict[str, dict] = {}
        for r in self.state.values():
            if r.device_type in HIDDEN_TYPES:
                continue
            d = devices.setdefault(r.device_id, {"id": r.device_id, "type": r.device_type, "name": r.device,
                                                 "values": {}, "controls": controls_for(r.device_type),
                                                 "reachable": True, "lastSeen": None})
            if r.capability not in RAW_CAPABILITIES:
                d["values"][r.capability] = _plain(r.value)
            if r.comm_ts is not None:
                d["lastSeen"] = _iso(r.comm_ts)
                d["reachable"] = now_ms - r.comm_ts <= self.stale_ms
        home = home_now(self.state)
        has_router = any(r.capability == "connectedClients" or r.device_type == "phone" for r in self.state.values())
        return {
            "now": _iso(now_ms),
            "household": self.household,
            "live": not self.dry_run and not self.look_only,
            "dryRun": self.dry_run or self.look_only,
            "lookOnly": self.look_only,
            "devices": list(devices.values()),
            "people": {"home": home.get("phones_home", []), "guests": int(home.get("guest_phones") or 0)} if has_router else None,
            "climate": {"roomC": home.get("room_c"), "targetC": home.get("target_c"), "mode": home.get("mode"),
                        "heating": home.get("heating")} if home.get("thermostat") else None,
        }

    async def status(self) -> dict:
        now = self._now()
        return status.status_now(self.store.db, self.state, now, self.stale_ms)

    async def set_control(self, device_id: str, body, *, count_override: bool = True) -> dict:
        now_ms = self._ready()
        if not isinstance(body, dict) or not isinstance(body.get("type"), str):
            raise ApiError(400, 'send {"body": {"type": ..., "value": ...}}')
        if self.look_only:  # said plainly, never pretended: the app shows that it didn't happen
            return {"status": "blocked", "verified": None, "deviceId": device_id, "reason": LOOK_ONLY_REASON}
        fresh_ids = {i for ids in self.fresh.values() for i in ids}
        devices = {dev: t for dev, t in self.types.items() if dev in fresh_ids}
        heat = heat_now(self.store.db, self.state, self._now()) if self.types.get(device_id) == "thermostat" else None
        rec = await guarded_write(self.actuator.call, {"deviceId": device_id, "body": body}, devices,
                                  allow_plug_on=True, heat=heat, readback_delay_s=self.readback_delay_s)
        out = {"status": rec["status"], "verified": rec.get("verified"), "deviceId": device_id}
        if rec["status"] != "sent":
            out["reason"] = rec.get("reason") or str(rec.get("result") or "")[:300]
            return out
        self._writes.append((now_ms, device_id, body["type"], body.get("value"), "app"))
        log.info("app    %s %s -> %s (%s)", device_id, body["type"], body.get("value"), rec.get("verified"),
                 extra={"data": {"device": device_id, "body": body, "verified": rec.get("verified")}})
        if count_override:
            note = self._manual_override(device_id, body, now_ms)
            if note:
                out["note"] = note
        return out

    def _manual_override(self, device_id: str, body: dict, now_ms: int) -> str | None:
        """A person changing back by hand what Alfred's last decision set: logged once per decision."""
        st = status.status_now(self.store.db, self.state, self._now(), self.stale_ms)
        if not st["byAlfred"] or not st["decisionId"]:
            return None
        change = next((c for c in st["changes"] if c["deviceId"] == device_id and c["capability"] == body["type"]), None)
        if change is None or change["to"] == body.get("value"):
            return None
        found = anticipate.decision_row(self.store.db, st["decisionId"])
        habit = found[0].get("habit_id") if found else None
        self.store.log_override(now_ms, "manual", decision_id=st["decisionId"], habit_id=habit, device_id=device_id,
                                capability=body["type"], alfred_value=change["to"], person_value=body.get("value"))
        return f"Noted: you changed back what I did at {str(found[0].get('at', ''))[11:16] if found else 'earlier'}. " \
               f"I’ll count that ({self._count(habit)} this week)."

    def _count(self, habit_id) -> int:
        row = self.store.db.execute("SELECT COUNT(*) FROM overrides WHERE ts >= ? AND habit_id IS ?",
                                    ((self.now_ms or 0) - 7 * 86_400_000, habit_id)).fetchone()
        return int(row[0]) if row else 0

    async def answer(self, decision_id: str, answer: str) -> dict:
        now = self._now()
        d = anticipate.set_by_person(self.store.db, decision_id, answer, now)
        if d is None:
            if anticipate.decision_row(self.store.db, decision_id) is None:
                raise ApiError(404, "no such decision")
            raise ApiError(409, f"that decision can't take “{answer}” now")
        kind = {"skip": "skip", "no": "decline"}.get(answer)
        if kind:
            self.store.log_override(self._ready(), kind, decision_id=decision_id, habit_id=d.get("habit_id"))
        found = anticipate.decision_row(self.store.db, decision_id)
        return status.plan_item(found[0], found[1], status.habit_names(self.store.db), anticipate.occasions_by_id(self.store.db))

    async def undo(self, decision_id: str) -> list[dict]:
        self._ready()
        found = anticipate.decision_row(self.store.db, decision_id)
        if found is None:
            raise ApiError(404, "no such decision")
        d, m = found
        if self.look_only:
            raise ApiError(409, "look-only mode: nothing is sent, so there's nothing to put back")
        if d.get("status") != "done" or (m.get("person") or {}).get("answer") == "undo":
            raise ApiError(409, "that decision has nothing to undo")
        since = (status.ran_ms(m) or self._ready()) - 60_000
        results = []
        for a in status.sent_actions(m):
            args = a.get("args") or {}
            dev, body = args.get("deviceId"), args.get("body") or {}
            if not dev or not body.get("type") or body["type"] == "color_rgb":
                continue
            seen = status.agent_change(self.store.db, dev, body["type"], since)
            if seen is None or seen[0] is None:
                results.append({"status": "blocked", "verified": None, "deviceId": dev,
                                "reason": "blocked: I never saw that change happen, so there is nothing to put back"})
                continue
            results.append(await self.set_control(dev, {"type": body["type"], "value": seen[0]}, count_override=False))
        if any(r["status"] == "sent" for r in results):  # only an undo that put something back counts as one
            anticipate.set_by_person(self.store.db, decision_id, "undo", self._now())
            self.store.log_override(self._ready(), "undo", decision_id=decision_id, habit_id=d.get("habit_id"))
        return results

    async def chat(self, text: str, lang: str | None) -> dict:
        now_ms = self._ready()
        if not isinstance(text, str) or not text.strip():
            raise ApiError(400, 'send {"text": "..."}')
        said = f"A person asked{f' (in {lang})' if lang else ''}: {text.strip()[:1000]}"
        ctx = build_context(now_ms, [WakeReason("person", said)], self.state, self.store, None, self.stale_ms)
        out = await self.agent.run(ctx)
        self.store.log_agent_run(now_ms, ctx["reasons"], ctx, out)
        actions = []
        for a in out.get("actions") or []:
            args = a.get("args") or {}
            body = args.get("body") or {}
            if a.get("status") == "sent" and args.get("deviceId") and body.get("type"):
                self._writes.append((now_ms, args["deviceId"], body["type"], body.get("value"), "app"))
            actions.append({"status": a.get("status", "failed"), "verified": a.get("verified"), "deviceId": args.get("deviceId"),
                            **({"body": body} if body.get("type") else {}),
                            **({"reason": a.get("reason") or str(a.get("result") or "")[:300]} if a.get("status") != "sent" else {})})
        return {"text": str(out.get("notes") or ""), "blocks": [], "actions": actions, "lang": lang}

    # ---- serving ----

    def run(self, coro, timeout: float = TIMEOUT_S):
        """Run a request on the loop's thread and wait for it (called from the HTTP server's threads)."""
        if self.loop is None:
            coro.close()
            raise ApiError(503, "the loop isn't running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def serve(self, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
        """On localhost: the app's dev server (npm run demo / home) passes the phone's /api requests here."""
        self.loop = asyncio.get_running_loop()
        server = _Server(("127.0.0.1", port), _Handler)
        server.api = self
        threading.Thread(target=server.serve_forever, name="phone-api", daemon=True).start()
        self._server = server
        log.info("phone API on http://127.0.0.1:%d/api%s", server.server_address[1], " (look-only)" if self.look_only else "")
        return server

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    api: PhoneApi


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "AlfredPhoneAPI"

    def log_message(self, fmt, *args) -> None:
        log.debug("http   " + fmt, *args)

    def _allowed(self) -> bool:
        port = self.server.server_address[1]
        ok = {f"localhost:{port}", f"127.0.0.1:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in ok and (origin is None or origin.split("://")[-1] in ok)

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data) -> None:
        self._send(code, json.dumps(data, default=str).encode())

    def _body(self, limit: int = MAX_JSON) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ApiError(413, "that's too much to send")
        return self.rfile.read(n) if n else b""

    def _json_body(self) -> dict:
        raw = self._body()
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, "the body is not JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "the body must be a JSON object")
        return data

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def _handle(self, method: str) -> None:
        if not self._allowed():
            return self._json(403, {"error": "not allowed from here"})
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/"):
                return self._api(method, [unquote(p) for p in path[len("/api/"):].strip("/").split("/")])
            raise ApiError(404, "nothing here")
        except ApiError as e:
            self._json(e.status, {"error": str(e)})
        except TimeoutError:
            self._json(504, {"error": "Alfred took too long to answer"})
        except Exception as e:  # a broken request must never take the loop down
            log.exception("phone API request failed: %s %s", method, path)
            self._json(500, {"error": f"something went wrong: {e}"[:300]})

    def _api(self, method: str, parts: list[str]) -> None:
        api = self.server.api
        match method, parts:
            case "GET", ["home"]:
                return self._json(200, api.run(api.home()))
            case "GET", ["status"]:
                return self._json(200, api.run(api.status()))
            case "GET", ["voice"]:
                ok = voice.available()
                return self._json(200 if ok else 501, {"ok": True, "model": voice.MODEL} if ok else {"error": "voice not installed"})
            case "POST", ["devices", device_id]:
                return self._json(200, api.run(api.set_control(device_id, self._json_body().get("body"))))
            case "POST", ["plan", decision_id, ("skip" | "unskip") as what]:
                self._json_body()
                return self._json(200, api.run(api.answer(decision_id, what)))
            case "POST", ["plan", decision_id, "answer"]:
                yes = self._json_body().get("yes")
                if not isinstance(yes, bool):
                    raise ApiError(400, 'send {"yes": true} or {"yes": false}')
                return self._json(200, api.run(api.answer(decision_id, "yes" if yes else "no")))
            case "POST", ["undo"]:
                decision = self._json_body().get("decision")
                if not isinstance(decision, str):
                    raise ApiError(400, 'send {"decision": "<id>"}')
                return self._json(200, api.run(api.undo(decision)))
            case "POST", ["chat"]:
                data = self._json_body()
                lang = data.get("lang") if isinstance(data.get("lang"), str) else None
                return self._json(200, api.run(api.chat(data.get("text"), lang), CHAT_TIMEOUT_S))
            case "POST", ["voice"]:
                if not voice.available():
                    raise ApiError(501, "voice not installed")
                try:
                    return self._json(200, voice.transcribe(self._body(voice.MAX_BYTES)))
                except ValueError as e:
                    raise ApiError(400, str(e)) from None
        raise ApiError(404 if method == "GET" else 405, f"no {method} /api/{'/'.join(parts)}")
