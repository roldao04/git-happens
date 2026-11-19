"""The demo, pre-computed (docs/demo-plan.md, part F): one command, then every moment opens at once.

  uv run python -m source.demo --household demo                  # 60 days, then Alfred on the moment days
  ALFRED_LLM_CACHE=only uv run python -m source.simulate --serve   # the visualizer, with no network

What it does:
1. **Learns**: the household's days through the pipeline (simulate → ingest → learn each night), as
   `source.pipeline` does, without Alfred. At the end of each moment's day it keeps the views the
   visualizer shows for that day (/learned, /knowledge), and the night before each moment it keeps a copy
   of the store: what Alfred knew when that day began.
2. **Lives the moment days**: each moment's day runs through the event loop (source/loop.py) on that copy,
   with Alfred awake: learned wakes, the LLM's proposals, decisions, the guard. Its thermostat writes go
   back into the simulator (`generate(..., thermostat_writes=...)`), so the room warms because Alfred
   heated it. The days come one after the other, so a write on the gathering's night is still there the
   next morning.
3. **Writes** `data/demo/<household>-s<seed>-<start>.json` (the moments, with the habits found for them as
   `focus`; Alfred's writes; the views of the moment days) and `.sqlite3` (each day's wakes and the decisions
   they led to), which source/simulate_web.py reads before anything else.

The LLM: with OPENROUTER_API_KEY (and AGENT_MODEL), each wake asks the model, through the reply cache
(source/llm_cache.py), so a second run, or ALFRED_LLM_CACHE=only, never calls it again. Without a key, or
with --scripted, the replies are **scripted**: plain code that answers each wake the way the prompt asks
(source/prompts/agent.md), from the same facts. The run says so (`"llm": "scripted"`), and so does the
visualizer. The confidence is code's either way: the wake's base × (1 − doubt).
"""
import argparse
import asyncio
import json
import logging
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import loop, simulate_web as web
from .agent import LangGraphAgent, make_model
from .guard import MAX_STEP_C, READ_TOOL, WRITE_TOOL
from .learn import to_local, to_ms
from .learn.forecast import hhmm
from .pipeline import _parse, run as pipeline_run
from .simulate import DEMO_MOMENTS, PRESETS, _at, generate, planted, start_for
from .store import Store
from .triggers import Triggers, seed_triggers

log = logging.getLogger("source.demo")

OUT = web.DEMO_RUNS  # data/demo/
SAFETY = ("smoke", "alarm-went-off")  # the only hard triggers on a demo day: the learned wakes do the rest
STEP_GAP_MIN = 11  # two thermostat steps (3 °C each at most) at least the guard's 10 min apart


# ---------- 1. learn, keeping the views and the night before each moment ----------

def learn(name: str, seed: int, days: int, work: Path, view_days: set[int], moment_days: set[int],
          names: bool = False) -> dict[int, dict]:
    """The pipeline over `days` days into work/home.sqlite3. Returns {day: views of that day}; leaves
    work/before-<day>.sqlite3, the store as the night before each moment day ended. `names`: name habits and
    occasions with scripted_names each night (no model)."""
    start = start_for(name)
    lines, _ = generate(name, days, start=start, seed=seed)
    views: dict[int, dict] = {}
    during: dict[int, dict] = {}

    def n_of(d: date) -> int:
        return (d - start).days + 1

    def before_learn(db: sqlite3.Connection, d: date) -> None:
        if n_of(d) in view_days:
            during[n_of(d)] = web.day_statuses(db, d)

    def after_learn(db: sqlite3.Connection, d: date) -> None:
        n = n_of(d)
        if names:
            scripted_names(db)
        if n in view_days:
            out = web.assemble(db, name, n, seed, start, during.pop(n, {}))
            views[n] = {"learned": out["learned"], "knowledge": out["knowledge"]}
            log.info("day %d (%s): views kept", n, d)
        if n + 1 in moment_days and n not in moment_days:  # after a moment day, the next starts from its night
            keep(db, work / f"before-{n + 1}.sqlite3")
            log.info("day %d (%s): the store kept for the moment of day %d", n, d, n + 1)

    pipeline_run(_parse(lines), work, replace=True, before_learn=before_learn, after_learn=after_learn)
    return views


def keep(db: sqlite3.Connection, path: Path) -> None:
    """A copy of the store, without the snapshots' contents (their times stay: they say when the home was
    watched). About a tenth of the size."""
    path.unlink(missing_ok=True)
    db.commit()
    out = sqlite3.connect(path)
    try:
        db.backup(out)
        out.execute("UPDATE snapshots SET home = '{}'")
        out.commit()
        out.execute("VACUUM")
    finally:
        out.close()


def night(folder: Path, day: date, out: Path, names: bool) -> None:
    """The end of a day lived with Alfred (folder/home.sqlite3), as the pipeline ends a day: its happenings
    (Alfred's own writes left out: Event.source "agent"), then the learners. Kept at `out`."""
    from .clock import day_bounds
    from .learn.happenings import save_happenings
    from .learn.run import learn_day

    store = Store(folder, record=False)
    try:
        save_happenings(store, day_bounds(day)[1])
        learn_day(store.db, day)
        if names:
            scripted_names(store.db)
        keep(store.db, out)
    finally:
        store.close()


# ---------- 2. a moment's day, with Alfred awake ----------

class SimSource:
    """One day of the simulated home for the loop, simulated again from the moment Alfred writes to the
    thermostat (the room answers his writes). `writes` are kept across days: [(ms, capability, value)]."""

    realtime = False

    def __init__(self, name: str, seed: int, n: int, writes: list):
        self.name, self.seed, self.n, self.writes = name, seed, n, writes
        self.start = start_for(name)
        first = self.start + timedelta(days=n - 1)
        self.day_start, self.day_end = _at(first, 0), _at(first + timedelta(days=1), 0)
        self.now = self.day_start - 1
        self.home: dict = {}
        self._load()

    def _load(self) -> None:
        lines, _ = generate(self.name, self.n, start=self.start, seed=self.seed, thermostat_writes=list(self.writes))
        self._rows = []
        for line in lines:
            ts = int(line[6:line.index(",")])  # a line starts {"ts":<ms>,
            if self.now < ts < self.day_end and ts >= self.day_start:
                self._rows.append(line)
        self._i = 0

    async def fetch(self) -> tuple[int, dict]:
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        row = json.loads(self._rows[self._i])
        self._i += 1
        self.now, self.home = int(row["ts"]), row["home"]
        return self.now, self.home

    def write(self, capability: str, value) -> None:
        """Alfred set the thermostat now: the rest of the day is simulated again with it."""
        self.writes.append((self.now, capability, value))
        self._load()

    async def close(self) -> None:
        pass


class SimActuator:
    """The kit's MCP for the simulated home: a thermostat write goes into the simulation, a read answers from
    the last snapshot with what was written (so the guard can confirm it). Other writes (the lamp) are
    recorded and confirmed, but the simulator doesn't play them back."""

    def __init__(self, source: SimSource):
        self.source = source
        self.written: dict[tuple[str, str], object] = {}
        self.reads = 0
        self.log: list[dict] = []

    async def call(self, tool: str, args: dict) -> str:
        dev_id = args.get("deviceId")
        if tool == READ_TOOL:
            self.reads += 1
            dev = next((d for d in self.source.home.get("devices", []) if d.get("id") == dev_id), None)
            if dev is None:
                return "{}"
            caps = [{**c, "value": self.written.get((dev_id, c.get("type")), c.get("value"))}
                    for c in dev.get("capabilities", [])]
            return json.dumps({**dev, "capabilities": caps, "lastCommunicationTime": self.source.now + self.reads})
        if tool == WRITE_TOOL:
            body = args.get("body") or {}
            dev = next((d for d in self.source.home.get("devices", []) if d.get("id") == dev_id), {})
            self.written[(dev_id, body.get("type"))] = body.get("value")
            self.log.append({"at": to_local(self.source.now).isoformat(timespec="seconds"), "device": dev.get("type"),
                             "body": body})
            if dev.get("type") == "thermostat" and body.get("type") in ("targetTemperature", "mode"):
                self.source.write(body["type"], body.get("value"))
            return "ok"
        raise RuntimeError(f"{tool} is not offered in the demo")


async def live_day(name: str, seed: int, n: int, store_path: Path, writes: list, agent) -> list[dict]:
    """Day `n` through the loop on the store at `store_path` (the night before), Alfred awake. Appends his
    thermostat writes to `writes`; returns every write he sent."""
    folder = store_path.parent / f"day-{n}"
    folder.mkdir(exist_ok=True)
    shutil.copy(store_path, folder / "home.sqlite3")
    source = SimSource(name, seed, n, writes)
    actuator = SimActuator(source)
    store = Store(folder, record=False)
    try:
        triggers = Triggers([t for t in seed_triggers(15) if t.id in SAFETY])
        await loop.run(source, store, triggers, agent, poll_seconds=0, stale_minutes=60, actuator=actuator)
    finally:
        store.close()
    return actuator.log


# ---------- the LLM, or the scripted replies ----------

class ScriptedAgent:
    """Answers each learned wake the way source/prompts/agent.md asks, in plain code: for a demo pre-computed
    without a model. Its sentences are labelled "scripted" wherever they are shown."""

    async def run(self, ctx: dict) -> dict:
        proposals = []
        for r in ctx.get("reasons", []):
            if r.get("key"):
                proposals += scripted_proposals(r, ctx)
        notes = "; ".join(p["reason"] for p in proposals) or "nothing to do"
        return {"notes": f"(scripted) {notes}", "trigger_updates": [], "actions": [], "proposals": proposals}


def scripted_proposals(reason: dict, ctx: dict) -> list[dict]:
    kind, key, data = reason.get("kind"), reason["key"], reason.get("data") or {}
    home = data.get("home") or ctx.get("home") or {}
    target, room = home.get("target_c"), home.get("room_c")
    comfort = home.get("comfort_c") or 21.0
    if not home.get("thermostat") or target is None:
        return []
    if kind == "habit_soon" and "heating" in (data.get("prepare") or []):
        habit = data.get("habit") or {}
        expected = _moment(data.get("expected_at"))
        # now, not at prepare_at: the guard's 3 °C a write means a second step 10 min later, so start at once
        at = _moment(ctx.get("now")) or _moment(data.get("prepare_at"))
        lead = data.get("lead_minutes")
        rate = (data.get("warmup") or {}).get("rate_c_per_h")
        why = (f"{habit.get('name') or 'This habit'} usually starts around {hhmm(expected)}"
               + (f"; at {rate:g} °C an hour" if rate else "")
               + (f" the house needs about {round(lead)} min to go from {room:g} to {comfort:g} °C" if lead and room else "")
               + ", so the heating starts now.")
        return _steps(target, comfort, at, why, key)
    if kind == "arrival" and not data.get("planned") and room is not None and room < comfort - 1 and target < comfort:
        who = (data.get("phone") or {}).get("name") or "Someone"
        return _steps(target, comfort, _moment(ctx.get("now")),
                      f"{who} is home and the room is {room:g} °C: warming it to {comfort:g} °C.", key, doubt=0.1)
    if kind == "departure" and target > 18:
        return _steps(target, 16.0, _moment(ctx.get("now")),
                      "No one's phone is home: the heating goes down to 16 °C until someone is due.", key)
    if kind == "occasion":
        o = data.get("occasion") or {}
        guests = home.get("guest_phones") or (o.get("fingerprint") or {}).get("guest_phones")
        lower = max(16.0, min(target, comfort) - 2)
        moved = _moved(data)
        why = (f"Looks like {o.get('name') or 'a gathering'}: {guests} guests' phones are here. A full room warms itself, "
               f"so the heating goes down to {lower:g} °C; a late night is expected"
               + (f", and tomorrow's warm-up moves from {moved[0]} to {moved[1]}" if moved else "") + ".")
        return _steps(target, lower, _moment(ctx.get("now")), why, key) if lower < target else []
    return []


def _steps(target: float, to: float, at: datetime, why: str, key: str, doubt: float = 0.0) -> list[dict]:
    """The writes from `target` to `to`, at most MAX_STEP_C each and STEP_GAP_MIN apart (the guard's limits)."""
    out, value, when = [], float(target), at
    while abs(to - value) > 1e-6:
        value = value + max(-MAX_STEP_C, min(MAX_STEP_C, to - value))
        out.append({"action": {"tool": WRITE_TOOL, "target": "thermostat",
                               "args": {"body": {"type": "targetTemperature", "value": value}}},
                    "at": when.isoformat(timespec="seconds"), "reason": why, "doubt": doubt, "wake": key})
        when += timedelta(minutes=STEP_GAP_MIN)
    return out


def _moved(data: dict) -> tuple[str, str] | None:
    """(usual, shifted) start of tomorrow's warm-up, from the forecast an occasion moves."""
    lead = (data.get("warmup") or {}).get("rate_c_per_h")
    for f in data.get("forecast_shifted") or []:
        s = f.get("shifted") or {}
        before, after = _clock(s.get("before", {}).get("expected")), _clock(f.get("expected"))
        if before is None or after is None:
            continue
        from .wake import EXTRA_LEAD_MIN
        # from a night-cooled room, about 2.5 °C to warm, and the wake comes EXTRA_LEAD_MIN before that
        minutes = (round(60 * 2.5 / lead) if lead else 30) + EXTRA_LEAD_MIN
        return hhmm(before - minutes), hhmm(after - minutes)
    return None


def _clock(v) -> int | None:
    try:
        h, m = str(v).split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _moment(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


WEEKDAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
                 "sat": "Saturday", "sun": "Sunday"}


def scripted_names(db: sqlite3.Connection) -> int:
    """Names for habits and occasions without a model, from their own facts (never the answer key): a person's
    phone arriving is "<person> gets home", the end of the night's quiet in the morning is "Waking up", a plug
    is its name. `named_by: "scripted"`; the LLM namer (source/learn/namer.py) replaces them when it runs.
    Returns how many were named."""
    from .learn import signal_devices
    from .learn.habits import QUIET

    tables = {t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not {"habits", "kinds", "sensors", "devices"} <= tables:
        return 0
    kinds = dict(db.execute("SELECT id, signal FROM kinds"))
    signatures = dict(db.execute("SELECT id, signature FROM sensors"))
    rows = db.execute("SELECT device_id, type, name FROM devices").fetchall()
    devices = {part: name for (dev_id, _, name), part in zip(rows, signal_devices(rows).values()) if name}
    n = 0
    for hid, data in db.execute("SELECT id, data FROM habits").fetchall():
        h = json.loads(data)
        if h.get("named_by") not in (None, "scripted"):
            continue
        name, importance = _habit_words(h, [signatures.get(s.get("pattern"), "") for s in h.get("steps") or []], kinds,
                                        devices, QUIET)
        if name and (name, importance) != (h.get("name"), h.get("importance")):
            h.update(name=name, named_by="scripted", importance=importance)
            db.execute("UPDATE habits SET data = ? WHERE id = ?", (json.dumps(h), hid))
            n += 1
    if "occasions" in tables:
        for oid, data in db.execute("SELECT id, data FROM occasions").fetchall():
            o = json.loads(data)
            if o.get("named_by") not in (None, "scripted"):
                continue
            day = WEEKDAY_NAMES.get((o.get("weekdays") or [""])[0])
            late = _clock((o.get("usual_hours") or [""])[0]) or 0
            name = (f"{day or 'An'} {'night' if late >= 17 * 60 else 'day'} "
                    f"{'gathering' if (o.get('fingerprint') or {}).get('guest_phones') else 'out of the ordinary'}")
            if name != o.get("name"):
                o.update(name=name, named_by="scripted")
                db.execute("UPDATE occasions SET data = ? WHERE id = ?", (json.dumps(o), oid))
                n += 1
    db.commit()
    return n


def _habit_words(h: dict, signatures: list[str], kinds: dict, devices: dict, quiet: str) -> tuple[str | None, str]:
    """(name, importance): an appliance's use is "low" (nothing to worry about when it doesn't happen), the
    rest "normal", as the namer's cards have it."""
    name = _habit_name(h, signatures, kinds, devices, quiet)
    return name, "low" if name and any(sig.startswith("kind:plug") for sig in signatures[:1]) else "normal"


def _habit_name(h: dict, signatures: list[str], kinds: dict, devices: dict, quiet: str) -> str | None:
    usual = _clock((h.get("window") or {}).get("usual")) or 0
    parts = []  # (how, signal): "kind" (it happened), "end" (a stretch ended), "link" (a part of a link)
    for sig in signatures:
        how, _, rest = sig.partition(":")
        for p in rest.split(">", 1) if how == "link" else [rest]:
            parts.append((how, kinds.get(p, p)))
    person = (h.get("person") or {}).get("name") if isinstance(h.get("person"), dict) else None
    if person:
        who = person.split("'")[0]
        arrives = any(s.startswith("phone:") for _, s in parts[:2]) or any(s.startswith("phone:") for _, s in parts)
        return f"{who} gets home" if arrives and usual >= 12 * 60 else f"{who} leaves"
    if any(how == "kind-end" and s == quiet for how, s in parts) and usual < 12 * 60:
        return "Waking up"
    if parts and all(s == quiet for _, s in parts) and (usual >= 20 * 60 or usual < 3 * 60):
        return "Bedtime"
    if any(how == "kind-end" and s == quiet for how, s in parts):
        return "Back home"  # a quiet afternoon ends: someone is back
    if any(s.endswith(".targetTemperature") for _, s in parts):
        return f"Heating on in the {'morning' if usual < 12 * 60 else 'evening'}"
    plugs = [devices.get(s.split(".", 1)[0]) for _, s in parts if s.startswith("plug")]
    if plugs and plugs[0]:
        days = (h.get("recurrence") or {}).get("weekdays") or []
        when = (f"on {WEEKDAY_NAMES[days[0]]}s" if len(days) == 1 else
                "in the evening" if usual >= 17 * 60 else "in the morning" if usual < 12 * 60 else "")
        return f"{plugs[0]} {when}".strip()
    return None


def make_agent(scripted: bool, model_name: str | None):
    """(agent, label): the LLM through the reply cache, or the scripted replies when there is no model."""
    from .config import load_settings

    settings = load_settings()
    if scripted or not settings.openrouter_api_key:
        return ScriptedAgent(), "scripted"
    name = model_name or settings.agent_model
    return LangGraphAgent(make_model(name, settings.openrouter_api_key), None, max_steps=6), name


# ---------- 3. what the visualizer reads ----------

def found_for(knowledge: dict, keys: list[str], name: str, seed: int, start: date) -> list[str]:
    """The habits and occasions Alfred found for these rows of the answer key (planted vs found)."""
    rows = {r["key"]: r for r in planted(name, start)}
    out = []
    for k in keys:
        if k in rows:
            got = web._planted_row(rows[k], knowledge)
            if got.get("found") and got["found"] != "warmup" and got["found"] not in out:
                out.append(got["found"])
    return out


def copy_thoughts(src: Path, dst: sqlite3.Connection, day: date) -> None:
    """That day's wakes and the decisions they led to, from a moment's store into the demo's."""
    db = sqlite3.connect(src)
    try:
        lo, hi = to_ms(datetime.combine(day, datetime.min.time())), to_ms(datetime.combine(day + timedelta(days=1),
                                                                                         datetime.min.time()))
        tables = {t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "wakes" in tables:
            dst.executemany("INSERT OR REPLACE INTO wakes (key, ts, data) VALUES (?, ?, ?)",
                            db.execute("SELECT key, ts, data FROM wakes WHERE ts >= ? AND ts < ?", (lo, hi)))
        if "decisions" in tables:
            for _id, ts, data in db.execute("SELECT id, ts, data FROM decisions").fetchall():
                d = json.loads(data)
                if d.get("wake") and (d["wake"].endswith(day.isoformat()) or lo <= ts < hi
                                      or (d.get("at") or "")[:10] == day.isoformat()):
                    dst.execute("INSERT INTO decisions (id, ts, data) VALUES (?, ?, ?)", (f"{day}:{_id}", ts, data))
        if "agent_runs" in tables:
            dst.executemany("INSERT INTO agent_runs (ts, reasons, output) VALUES (?, ?, ?)",
                            db.execute("SELECT ts, reasons, output FROM agent_runs WHERE ts >= ? AND ts < ?", (lo, hi)))
        dst.commit()
    finally:
        db.close()


THOUGHTS_SCHEMA = """
CREATE TABLE wakes (key TEXT PRIMARY KEY, ts INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE decisions (id TEXT NOT NULL, ts INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE agent_runs (ts INTEGER NOT NULL, reasons TEXT NOT NULL, output TEXT);
"""


@dataclass
class Result:
    path: Path
    moments: list[dict] = field(default_factory=list)
    writes: list = field(default_factory=list)
    llm: str = ""


def precompute(name: str = "demo", seed: int = 1, days: int = 60, out: Path = OUT, *, scripted: bool = False,
               model: str | None = None, work: Path | None = None, reuse: bool = False) -> Result:
    """Everything above, written to `out`. `work` keeps the stores (default: a temporary folder); `reuse` takes
    the learning (step 1) from an earlier run in `work` and lives the moment days again."""
    if name not in PRESETS or not PRESETS[name].people:
        raise ValueError(f"{name} has no demo: only a household of people (demo) has moments")
    start = start_for(name)
    moments = [dict(m) for m in DEMO_MOMENTS if m["day"] <= days]
    moment_days = {m["day"] for m in moments}
    agent, llm = make_agent(scripted, model)
    stem = web._names_file(name, seed, start).stem
    out.mkdir(parents=True, exist_ok=True)
    tmp = None
    if work is None:
        tmp = tempfile.TemporaryDirectory(prefix="alfred-demo-")
        work = Path(tmp.name)
    work.mkdir(parents=True, exist_ok=True)
    try:
        kept = work / "views.json"
        if reuse and kept.exists():
            log.info("reusing the learning in %s", work)
            views = {int(n): v for n, v in json.loads(kept.read_text()).items()}
        else:
            log.info("learning %d days of %s (seed %d, from %s): a few minutes", days, name, seed, start)
            views = learn(name, seed, days, work, moment_days | {days}, moment_days, names=llm == "scripted")
            kept.write_text(json.dumps(views, default=str))
        writes: list = []
        sent: dict[int, list[dict]] = {}
        for n in sorted(moment_days):
            before = work / f"before-{n}.sqlite3"
            if n - 1 in moment_days:  # the day before was lived with Alfred: its night, as the pipeline would
                night(work / f"day-{n - 1}", start + timedelta(days=n - 2), before, names=llm == "scripted")
            log.info("day %d: the loop, with Alfred awake (%s)", n, llm)
            sent[n] = asyncio.run(live_day(name, seed, n, before, writes, agent))
        thoughts = out / f"{stem}.sqlite3"
        thoughts.unlink(missing_ok=True)
        db = sqlite3.connect(thoughts)
        db.executescript(THOUGHTS_SCHEMA)
        for n in sorted(moment_days):
            copy_thoughts(work / f"day-{n}" / "home.sqlite3", db, start + timedelta(days=n - 1))
        db.close()
        for m in moments:
            k = views[m["day"]]["knowledge"]
            m["focus"] = found_for(k, m.get("planted", []), name, seed, start)
        data = {"household": name, "seed": seed, "start": start.isoformat(), "days": days, "llm": llm,
                "made": datetime.now().isoformat(timespec="seconds"), "moments": moments,
                "writes": [[ms, cap, value] for ms, cap, value in writes],
                "sent": {str(n): s for n, s in sent.items()},
                "views": {str(n): v for n, v in views.items()},
                "day_views": {str(n): web.day_view(name, n, seed, start, writes=writes) for n in sorted(moment_days)}}
        path = out / f"{stem}.json"
        path.write_text(json.dumps(data, separators=(",", ":"), default=str), encoding="utf-8")
        return Result(path, moments, writes, llm)
    finally:
        if tmp is not None:
            tmp.cleanup()


def report(result: Result, name: str, seed: int) -> str:
    """What each moment shows, in words: the wakes, what Alfred did, and the room."""
    start = start_for(name)
    lines = [f"wrote {result.path} (LLM: {result.llm})"]
    db = sqlite3.connect(result.path.with_suffix(".sqlite3"))
    for m in sorted(result.moments, key=lambda m: m["day"]):
        day = start + timedelta(days=m["day"] - 1)
        lines.append(f"\nday {m['day']} ({day:%a %d %b}) {m['time']}  {m['title']}  focus={m['focus']}")
        for t in web.read_thoughts(db, day) or []:
            act = (t.get("action") or {}).get("args", {}).get("body")
            lines.append(f"  {(t.get('at') or '')[11:16] or '--:--'} {t['kind']:<13} {t['key']}"
                         + (f"\n      {t['mode']} {t['status']} {t['confidence']}  {act} at {(t.get('due') or '')[11:16]}"
                            if t.get("mode") else "")
                         + (f"\n      “{t['sentence']}”" if t.get("sentence") else ""))
    db.close()
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="source.demo", description="Pre-compute the demo: runs, views and LLM replies")
    p.add_argument("--household", default="demo")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--out", type=Path, default=OUT, help="where the visualizer looks (data/demo/)")
    p.add_argument("--scripted", action="store_true", help="scripted replies even when a model is configured")
    p.add_argument("--model", help="an OpenRouter model slug (default: AGENT_MODEL)")
    p.add_argument("--work", type=Path, help="keep the stores here (default: a temporary folder)")
    p.add_argument("--reuse", action="store_true", help="with --work: keep its learning, live the moment days again")
    args = p.parse_args(argv)
    if args.reuse and not args.work:
        p.error("--reuse needs --work")
    try:
        result = precompute(args.household, args.seed, args.days, args.out, scripted=args.scripted,
                            model=args.model, work=args.work, reuse=args.reuse)
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    print(report(result, args.household, args.seed))
    if result.llm == "scripted":
        print("\nNo LLM: the replies are scripted (source/demo.py, ScriptedAgent). With OPENROUTER_API_KEY set, run "
              "again to ask the model; its replies are cached for ALFRED_LLM_CACHE=only.")
    return 0


if __name__ == "__main__":
    from .logs import setup_logging

    setup_logging(None)
    raise SystemExit(main())
