"""The simulator's form: simulated houses or the real one, how many days, download. Local use only.

  uv run python -m source.simulate --serve      # then open http://localhost:4781

GET  /           the page (console/simulator.html)
POST /generate   kind (simulated | real), days, houses -> a zip with one JSONL file per house
GET  /visualizer the 3D view of one day (console/visualizer.html)
GET  /day        household, day[, seed, start] -> that day's snapshots, cut down to what the view draws
GET  /learned    household, day[, seed, start] -> what Alfred learned by that day, and each habit's status through it
GET  /knowledge  household, day[, seed, start] -> everything Alfred knows once that day is over (the page's second tab)
GET  /moments    household[, seed, start] -> the demo's scripted moments: one click jumps to a day and time
GET  /thoughts   household, day[, seed, start] -> why Alfred woke its LLM that day, what it said and did
GET  /planted    household, day[, seed, start] -> each planted rhythm (the answer key) and what Alfred found for it

The last three are the demo screen's (docs/contracts.md, C11). Each reads the real data when it is there (the
simulator's moments and answer key, the run's `wakes` and `decisions`) and falls back to console/fixtures/.

"simulated" gives made-up homes (`simulate.houses`), each different. "real" replays the one recorded house
(home-a). The same choices always give the same zip; for a specific named household, use the command line
(docs/simulator.md).
"""
import importlib
import json
import logging
import re
import sqlite3
import tempfile
import threading
import time
import zipfile
from collections import OrderedDict, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .simulate import DEFAULT_START, PRESETS, REAL_HOME, _at, generate, home_a_max_days, houses, start_for

log = logging.getLogger("source.simulate_web")

CONSOLE = Path(__file__).resolve().parent.parent / "console"
PAGE = CONSOLE / "simulator.html"
VIEW_PAGE = CONSOLE / "visualizer.html"
FILM_PAGE = CONSOLE / "film.html"  # the pitch's film: the same day with and without Alfred, side by side
MAX_DAYS, MAX_HOUSES, MAX_HOUSE_DAYS = 365, 20, 1000  # 1,000 house-days is about a minute and 1.7 GB unzipped
MAX_BODY = 10_000
REAL_MAX_DAYS = home_a_max_days()  # from the default first day


def _request(form: dict[str, str]) -> tuple[str, int, int]:
    """Form fields -> (kind, days, houses). kind is "simulated" or "real". Raises ValueError with a plain message."""
    kind = form.get("kind", "simulated")
    if kind not in ("simulated", "real"):
        raise ValueError("kind must be simulated or real")
    try:
        days = int(form.get("days", ""))
        count = 1 if kind == "real" else int(form.get("houses", ""))
    except ValueError:
        raise ValueError("days and houses must be whole numbers") from None
    if kind == "real":
        if not 1 <= days <= REAL_MAX_DAYS:
            raise ValueError(f"the real house has {REAL_MAX_DAYS} recorded days: days must be between 1 and {REAL_MAX_DAYS}")
        return kind, days, 1
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")
    if not 1 <= count <= MAX_HOUSES:
        raise ValueError(f"houses must be between 1 and {MAX_HOUSES}")
    if days * count > MAX_HOUSE_DAYS:
        raise ValueError(f"that is {days * count} days of data in total; the limit is {MAX_HOUSE_DAYS}")
    return kind, days, count


def write_zip(out, kind: str, days: int, count: int = 1) -> None:
    """One <house>.jsonl per house, compressed (JSONL shrinks about 80 times). "real" is the one real house."""
    homes = [(REAL_HOME, REAL_HOME)] if kind == "real" else [(h.name, h) for h in houses(count)]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, household in homes:
            lines, _ = generate(household, days)
            info = zipfile.ZipInfo(f"{name}.jsonl", time.localtime()[:6])  # else the file is dated 1980
            info.compress_type = zipfile.ZIP_DEFLATED
            with z.open(info, "w", force_zip64=True) as f:
                for line in lines:
                    f.write(line.encode())


def _zip_name(kind: str, days: int, count: int) -> str:
    return f"alfred-real-house-{days}d.zip" if kind == "real" else f"alfred-{count}-houses-{days}d.zip"


# --- one day for the 3D view -----------------------------------------------------------------------

# The capabilities the view draws, per device type. The rest of a snapshot (about 5 KB) is left out.
# console/visualizer.html has the same table, for files it reads itself.
WATCHED = {
    "temperatureSensor": ("temperatureValue", "humidityValue"),
    "contactSensor": ("sensorState",),
    "bulb": ("power", "brightness", "color_rgb"),
    "plug": ("power", "instantPower", "todayConsumption"),
    "smokeSensor": ("sensorState",),
    "gateway": ("alarmState",),
    "thermostat": ("temperatureValue", "targetTemperature", "heatingState", "mode"),
    "router": ("connectedClients",),
}
VIEW_HOUSEHOLDS = (*PRESETS, REAL_HOME)


def compact(home: dict) -> list[dict]:
    """A get_home snapshot -> one small dict per device, in order, then one for the home's security."""
    out = []
    for dev in home.get("devices", []):
        keep = WATCHED.get(dev.get("type"), ())
        out.append({c["type"]: c.get("value") for c in dev.get("capabilities", []) if c.get("type") in keep})
    security = home.get("security") or {}
    out.append({"status": security.get("status"), "isAlarmed": security.get("isAlarmed")})
    return out


def _household(name: str):
    """A name from the page -> what `generate` takes: a preset, the real house, or house-N of the form's zip."""
    if name in VIEW_HOUSEHOLDS:
        return name
    if name.startswith("house-") and name[6:].isdigit() and 1 <= int(name[6:]) <= MAX_HOUSES:
        return houses(int(name[6:]))[-1]
    raise ValueError(f"unknown household {name!r}: use {', '.join(VIEW_HOUSEHOLDS)} or house-1 to house-{MAX_HOUSES}")


def _default_start(name: str) -> date:
    """A preset's own first day (the demo house starts on 21 Sept: simulate.start_for), else its demo
    fixture's, else the simulator's."""
    if name in PRESETS:
        return start_for(name)
    try:
        return date.fromisoformat((_fixture(name) or {}).get("start"))
    except (TypeError, ValueError):
        return DEFAULT_START


def _day_request(query: dict[str, str]) -> tuple[str, int, int, date]:
    """Query fields -> (household, day, seed, start). Raises ValueError with a plain message."""
    name = query.get("household", "rosa")
    _household(name)
    try:
        day, seed = int(query.get("day", "1")), int(query.get("seed", "1"))
    except ValueError:
        raise ValueError("day and seed must be whole numbers") from None
    try:
        start = date.fromisoformat(query["start"]) if "start" in query else _default_start(name)
    except ValueError:
        raise ValueError("start must be a date, YYYY-MM-DD") from None
    most = home_a_max_days(start) if name == REAL_HOME else MAX_DAYS
    if not 1 <= day <= most:
        raise ValueError(f"day must be between 1 and {most}" + (" for the real house" if name == REAL_HOME else ""))
    return name, day, seed, start


def day_view(name: str, day: int, seed: int = 1, start: date = DEFAULT_START, writes: list | None = None) -> dict:
    """Day `day` (from 1) of the file `generate` writes for this household, cut down for the 3D view.

    `frames` is one [ts, changes] per snapshot. The first carries every device's state; the others only
    what changed since the one before: {device index: {capability: value}}. The index after the last
    device is the home's security. A frame with no changes still says the home reported then.
    """
    if writes is None:
        ready = (demo_run(name, seed, start) or {}).get("day_views", {}).get(str(day))
        if ready:  # pre-computed by source/demo.py, with Alfred's writes
            return ready
        writes = _writes(name, seed, start)  # Alfred at the thermostat (source/demo.py)
    first = start + timedelta(days=day - 1)
    day_start, day_end = _at(first, 0), _at(first + timedelta(days=1), 0)
    writes = [w for w in writes if w[0] < day_end]
    lines, truth = generate(_household(name), day, start=start, seed=seed, thermostat_writes=writes)
    devices, frames, last = [], [], None
    for line in lines:
        if int(line[6:line.index(",")]) < day_start:  # a line starts {"ts":<ms>, — skip earlier days unparsed
            continue
        row = json.loads(line)
        state = compact(row["home"])
        if last is None:
            devices = [{"id": d.get("id"), "name": d.get("name"), "type": d.get("type"), "room": d.get("roomName")}
                       for d in row["home"]["devices"]]
            changes = dict(enumerate(state))
        else:
            changes = {n: {k: v for k, v in now.items() if was.get(k) != v}
                       for n, (was, now) in enumerate(zip(last, state))}
            changes = {n: c for n, c in changes.items() if c}
        frames.append([row["ts"], changes])
        last = state
    iso = first.isoformat()
    return {"household": name, "day": day, "date": iso, "start": day_start, "end": day_end, "seed": seed,
            "max_days": home_a_max_days(start) if name == REAL_HOME else MAX_DAYS,
            "devices": devices, "frames": frames, "alfred_writes": len([w for w in writes if w[0] >= day_start]),
            # The answer key for that day: what the simulator planted. For people, never for the learners.
            "truth": [t for t in truth if t["start"][:10] <= iso <= t.get("end", t["start"])[:10]]}


# --- what Alfred learned: the 3D view's panel and the knowledge tab ---------------------------------

MAX_LEARN_DAYS = 60  # simulate + ingest + learn from day 1: about 7 s for 21 days, so a cap keeps the page usable
STATUS_STEP_MIN = 5  # a habit's status through the day is worked out this often
MAX_RUNS = 12  # learning runs kept in memory; each one serves both /learned and /knowledge for its day


def _device_types(parts: list[str]) -> list[str]:
    """A sensor's parts name kinds such as "bulb-A (power on…)" or "end of home-A (quiet…)": their device types."""
    out = []
    for part in parts:
        m = re.match(r"(?:end of )?(\w+?)-[A-Z]+\b", part)
        if m and m.group(1) != "home" and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _card(sensor: dict) -> dict:
    p = sensor["pattern"]
    return {"id": sensor["id"], "name": sensor.get("name"), "parts": p["parts"], "devices": _device_types(p["parts"]),
            "time_of_day": p["time_of_day"], "weekdays": p.get("weekdays"), "days_seen": p["days_seen"],
            "days_total": p["days_total"], "earliest": p.get("earliest"), "latest": p.get("latest"),
            "late_after": p.get("late_after"), "first_seen": p.get("first_seen"), "learned_at": sensor["learned_at"],
            "confidence": sensor.get("confidence"), "status": sensor["status"],
            "merged_into": sensor.get("merged_into"), "moved_from": p.get("moved_from"),
            **{k: sensor.get(k) for k in CARD_KEYS}}


CARD_KEYS = ("explanation", "means", "if_missing", "importance", "same_as")
NAMES_DIR = Path(__file__).resolve().parent.parent / "data" / "names"  # gitignored, like all of data/
_names_lock = threading.Lock()  # one naming at a time, so two requests never name the same sensor twice


def _names_file(name: str, seed: int, start: date) -> Path:
    """The namer's cards for one simulated home (household, seed and first day), kept across runs and restarts."""
    return NAMES_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', name)}-s{seed}-{start.isoformat()}.json"


def _name(db: sqlite3.Connection, path: Path) -> bool:
    """Names this run's patterns, habits and occasions: saved cards first, the LLM only for those never named
    before. Returns whether any sensor has a name."""
    from .learn.namer import name_all, reuse_cards, saved_cards

    with _names_lock:
        try:
            cards = json.loads(path.read_text()) if path.exists() else {}
        except ValueError:
            cards = {}
        reuse_cards(db, cards)
        model = _namer_model()
        if model is not None and name_all(db, model):
            cards.update(saved_cards(db))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cards, indent=1, ensure_ascii=False))
    return any(n for (n,) in db.execute("SELECT json_extract(data, '$.name') FROM sensors"))


def _namer_model():
    """The namer's LLM, or None without OPENROUTER_API_KEY (the view then shows patterns, unnamed)."""
    from .agent import make_model
    from .config import load_settings

    settings = load_settings()
    if not settings.openrouter_api_key:
        return None
    return make_model(settings.agent_model, settings.openrouter_api_key)


def learned_view(name: str, day: int, seed: int = 1, start: date = DEFAULT_START) -> dict:
    """Days 1 to `day` through the pipeline (simulate, ingest, learn each night), seen from day `day`.

    `sensors`: every sensor Alfred had when the day began, plus those first learned that night
    (`tonight`: learned, fading, merged or moved, from the learners' run at the day's end).
    `statuses`: each active habit's status (source/learn/today.py) every STATUS_STEP_MIN minutes of the day,
    kept only where one changes: [ms, [[status, at] per id in `ids`]].
    `readings`: the occurrences inside that day, as the learners saw them that night: [sensor id, start ms, end ms].
    `named`: whether any sensor has a name. The namer (part 7) names each sensor once, in data/names/, and
    every day of this home reuses that name; each card shows it, also on the days before it was learned.
    """
    return _learned(name, day, seed, start)["learned"]


def knowledge_view(name: str, day: int, seed: int = 1, start: date = DEFAULT_START) -> dict:
    """Everything Alfred knows once day `day` is over: the knowledge tab. Same run as `learned_view`.

    `kinds`: the event types, each with `description` in words ("~2,100 W for ~3 min") and its device's name.
    `patterns`: every sensor, as learned_view's cards (plus last_seen, lag, until…).
    `habits`, `occasions`: habits and occasions, in their shapes (docs/contracts.md); each occasion also gets
    `episode_list`, its episodes' times. `matches`: occasions the day's end looks like (match_now).
    `forecast`, `decisions`: part B for `tomorrow`, the 24 h after the day.
    `modules`: per part, "ok", "missing" (not written yet) or "failed" (it raised: see the log). A part that
    isn't ok has empty lists. `devices`: each device type's name in this home. `named`: anything has a name.
    """
    return _learned(name, day, seed, start)["knowledge"]


# One run per household, day, seed and first day, for both views. A request for a run that is still going
# waits for it rather than starting the same one again (the page asks for /learned and /knowledge together).
_runs: "OrderedDict[tuple, dict]" = OrderedDict()
_runs_lock = threading.Lock()
_running: dict[tuple, threading.Lock] = {}


def _learned(name: str, day: int, seed: int, start: date) -> dict:
    if not 1 <= day <= MAX_LEARN_DAYS:
        raise ValueError(f"Alfred learns from day 1, so the learned view stops at day {MAX_LEARN_DAYS}")
    ready = (demo_run(name, seed, start) or {}).get("views", {}).get(str(day))
    if ready:  # pre-computed by source/demo.py: the moments open at once
        return {**ready, "thoughts": _precomputed_thoughts(name, seed, start, start + timedelta(days=day - 1))}
    key = (name, day, seed, start)
    with _runs_lock:
        if key in _runs:
            _runs.move_to_end(key)
            return _runs[key]
        running = _running.setdefault(key, threading.Lock())
    with running:
        with _runs_lock:
            if key in _runs:
                return _runs[key]
        try:
            out = _learn_once(name, day, seed, start)
            with _runs_lock:
                _runs[key] = out
                while len(_runs) > MAX_RUNS:
                    _runs.popitem(last=False)
        finally:
            with _runs_lock:
                if _running.get(key) is running:
                    del _running[key]
    return out


def _learn_once(name: str, day: int, seed: int, start: date) -> dict:
    """The run itself, not cached: {"learned": learned_view's dict, "knowledge": knowledge_view's dict}."""
    from .pipeline import _parse, run

    # Without Alfred's writes: the learners learn from the people's days (source/demo.py learns the same way).
    lines, _ = generate(_household(name), day, start=start, seed=seed)
    target = start + timedelta(days=day - 1)
    during: dict = {}

    def before_learn(db: sqlite3.Connection, d: date) -> None:
        if d == target:
            during.update(day_statuses(db, d))

    with tempfile.TemporaryDirectory() as tmp:  # habits and occasions learn each night in learn_day
        run(_parse(lines), Path(tmp), before_learn=before_learn)
        db = sqlite3.connect(Path(tmp) / "home.sqlite3")
        try:
            return assemble(db, name, day, seed, start, during)
        finally:
            db.close()


def day_statuses(db: sqlite3.Connection, d: date) -> dict:
    """What Alfred knew while day `d` went on, before that night's learning (pipeline.run's before_learn):
    {"sensors": the learned sensors, "statuses": each habit's status every STATUS_STEP_MIN minutes}."""
    from .learn import storage, to_ms
    from .learn.today import sensor_today

    storage.init(db)
    out = {"sensors": storage.export(db, d)["sensors"]}
    ids, frames, last = None, [], None
    midnight = datetime.combine(d, clock_time())
    for m in range(0, 24 * 60, STATUS_STEP_MIN):
        now = midnight + timedelta(minutes=m)
        today = sensor_today(db, now)
        ids = ids or [st["sensor_id"] for st in today]
        row = [[st["status"], st["at"]] for st in today]
        if row != last:
            frames.append([to_ms(now), row])
            last = row
    out["statuses"] = {"ids": ids or [], "frames": frames}
    return out


def assemble(db: sqlite3.Connection, name: str, day: int, seed: int, start: date, during: dict) -> dict:
    """{"learned", "knowledge", "thoughts"} for day `day`, from a store whose learners have just run that day's
    night (pipeline.run's after_learn, or the end of the run). `during`: day_statuses of that day."""
    from .learn import storage, to_ms

    target = start + timedelta(days=day - 1)
    parts = _Parts()
    named = _name(db, _names_file(name, seed, start))
    after = storage.export(db, target)
    keys = {sid: f"{sid}|{sig}" for sid, sig in db.execute("SELECT id, signature FROM sensors")}
    knowledge = _knowledge(db, target, after["sensors"], keys, parts)  # after the namer: reasons use names
    thoughts = read_thoughts(db, target)

    before = {s["id"]: s for s in during.get("sensors", [])}
    sensors = []
    for s in after["sensors"]:
        was = before.get(s["id"])
        card = _card(was or s)
        card.update(name=s.get("name"), **{k: s.get(k) for k in CARD_KEYS})  # the namer ran after the last night
        card["key"] = keys.get(s["id"])  # its saved name's key (source/learn/namer.py)
        if was is None:
            card["tonight"] = "learned"
        elif s["status"] != was["status"]:
            card["tonight"] = s["status"]  # fading or merged
            card["merged_into"] = s.get("merged_into")
        elif s["pattern"].get("moved_from") != was["pattern"].get("moved_from"):
            card["tonight"] = "moved"
            card["moved_from"] = s["pattern"]["moved_from"]
        else:
            card["tonight"] = None
        if was is None:
            card.update(_card(s), tonight="learned")
        sensors.append(card)
    iso = target.isoformat()
    readings = [[r["sensor_id"], to_ms(datetime.fromisoformat(r["start"])),
                 to_ms(datetime.fromisoformat(r["end"])) if r["end"] else None]
                for r in after["readings"] if r["start"][:10] == iso or (r["end"] or "")[:10] == iso]
    learned = {"household": name, "day": day, "date": iso, "max_days": MAX_LEARN_DAYS, "named": named,
               "sensors": sensors, "statuses": during.get("statuses", {"ids": [], "frames": []}), "readings": readings}
    most = min(MAX_LEARN_DAYS, home_a_max_days(start)) if name == REAL_HOME else MAX_LEARN_DAYS
    return {"learned": learned,
            "knowledge": {"household": name, "day": day, "date": iso, "start": start.isoformat(), "max_days": most,
                          **knowledge},
            "thoughts": thoughts}  # None: no wakes table in this run (the pipeline doesn't run the loop)


# --- the knowledge tab: habits, occasions, forecast and decisions, read defensively ---------------------

class _Parts:
    """The other parts' functions, for one run. A part not written yet is "missing"; one that doesn't import or
    raises is "failed" (logged once, then left alone for the rest of the run). Either way it gives nothing."""

    MODULES = {"habits": (".learn.grouping", "export_habits"), "occasions": (".learn.occasions", "export_occasions"),
               "forecast": (".learn.forecast", "forecast"), "decisions": (".anticipate", "plan_decisions"),
               "warmup": (".learn.warmup", "warmup")}

    def __init__(self) -> None:
        self.modules: dict = {}
        self.state: dict[str, str] = {}
        for part, (module, main) in self.MODULES.items():
            try:
                self.modules[part] = importlib.import_module(module, __package__)
            except ModuleNotFoundError as exc:
                if exc.name == f"{__package__}{module}":
                    self.state[part] = "missing"
                else:  # it is there, but something it imports isn't
                    self.fail(part, exc)
                continue
            except Exception as exc:
                self.fail(part, exc)
                continue
            self.state[part] = "ok" if callable(getattr(self.modules[part], main, None)) else "missing"

    def fail(self, part: str, exc: BaseException) -> None:
        if self.state.get(part) != "failed":
            log.warning("knowledge tab: %s failed, its section stays empty: %s", part, exc, exc_info=exc)
        self.state[part] = "failed"

    def call(self, part: str, fn: str, *args):
        """What the part's function returns, or None when the part (or that function) is missing or failed."""
        f = getattr(self.modules.get(part), fn, None)
        if self.state.get(part) != "ok" or not callable(f):
            return None
        try:
            return f(*args)
        except Exception as exc:
            self.fail(part, exc)
            return None


PATTERN_FACTS = ("spread_min", "last_seen", "lag_min", "until", "duration_min", "after_quiet_min")


def _knowledge(db: sqlite3.Connection, as_of: date, sensors: list[dict], keys: dict[str, str], parts: _Parts) -> dict:
    """Everything Alfred knows once day `as_of` is over (see knowledge_view)."""
    from .learn import storage

    tomorrow = as_of + timedelta(days=1)
    now = datetime.combine(tomorrow, clock_time())  # midnight: the day is over and its night's learning done
    from .learn import signal_devices

    devices = {}
    rows = _rows(db, "SELECT device_id, type, name FROM devices ORDER BY first_ts, device_id")
    signals = signal_devices(rows)
    for dev_id, kind, name in rows:
        devices.setdefault(kind, name)
        if name:  # also by the per-device signal name (docs/contracts.md, C2): "plug:kettle" -> "Kettle",
            devices.setdefault(signals[dev_id], name)  # "phone:c-8f3a" -> "Ana's phone"
    try:
        kinds = [_kind_card(k, devices) for k in storage.load_kinds(db)]
    except sqlite3.Error:
        kinds = []
    patterns = [{**_card(s), **{k: s["pattern"].get(k) for k in PATTERN_FACTS}, "key": keys.get(s["id"])}
                for s in sensors]
    exported = parts.call("occasions", "export_occasions", db)
    wanted = {  # section: (the part it comes from, how to get it), in this order: decisions last
        "habits": ("habits", lambda: _items(parts.call("habits", "export_habits", db), "habits")),
        "occasions": ("occasions", lambda: _with_episodes(db, _items(exported, "occasions"), exported)),
        "matches": ("occasions", lambda: _items(parts.call("occasions", "match_now", db, now), "matches")),
        "forecast": ("forecast", lambda: _items(parts.call("forecast", "forecast", db, now), "forecast")),
        "decisions": ("decisions", lambda: _items(parts.call("decisions", "plan_decisions", db, now), "decisions")),
    }
    found = {}
    for key, (part, get) in wanted.items():
        try:  # dates, sets, numpy numbers… become JSON here, once, not in every response
            found[key] = json.loads(json.dumps(get(), default=_jsonable))
        except Exception as exc:
            parts.fail(part, exc)
    # A part that failed at any point shows nothing, rather than half of what it knows.
    found = {key: found.get(key, []) if parts.state.get(part) == "ok" else [] for key, (part, _) in wanted.items()}
    for h in found["habits"]:  # their saved names' keys (source/learn/namer.py), for with_names
        h["key"] = f"habit:{h['id']}|{','.join(sorted(h.get('patterns', [])))}"
    for o in found["occasions"]:
        o["key"] = f"occasion:{o['id']}"
    named = any(x.get("name") for x in patterns + found["habits"] + found["occasions"])
    warmup = _warmup(db, now, parts)
    return {"tomorrow": tomorrow.isoformat(), "named": named, "modules": dict(parts.state), "devices": devices,
            "kinds": kinds, "patterns": patterns, **found, "warmup": warmup}


def _warmup(db: sqlite3.Connection, now: datetime, parts: _Parts) -> dict | None:
    """How fast the house warms (source/learn/warmup.py, C4), or None when it isn't known."""
    try:
        out = json.loads(json.dumps(parts.call("warmup", "warmup", db, now), default=_jsonable))
    except Exception as exc:
        parts.fail("warmup", exc)
        return None
    return out if isinstance(out, dict) and parts.state.get("warmup") == "ok" else None


def _slug(name: str) -> str:
    """"Washing machine" -> "washing-machine", as the signal names have it."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


def _device_type(device: str) -> str:
    """"plug:kettle" -> "plug" (source.learn.device_type, for a signal's device part)."""
    return device.split(":", 1)[0]


def _kind_card(kind, devices: dict[str, str]) -> dict:
    """One event type (a kind of the grouper, source/learn/kinds.py) for the knowledge tab."""
    from .learn.kinds import UNITS

    device, cap = kind.signal.split(".", 1)
    return {"id": kind.id, "signal": kind.signal, "device": device,
            "device_name": devices.get(device) or devices.get(_device_type(device)),
            "count": kind.count, "duration_s": round(kind.duration_s), "size": kind.size, "unit": UNITS.get(cap),
            "value": kind.value, "description": _kind_words(kind, devices)}


def _kind_words(kind, devices: dict[str, str]) -> str:
    """"~2,100 W for ~3 min", "on for ~43 min", "open for ~40 s", "temperature rises ~0.40 °C over ~60 min",
    "quiet for ~9.1 h, after the light until the contact sensor"."""
    from .learn.kinds import UNITS, fmt_duration, fmt_size

    device, cap = kind.signal.split(".", 1)
    lasting = f"~{fmt_duration(kind.duration_s)}"
    if kind.size is not None:
        unit = UNITS.get(cap, cap)
        if cap in ("temperatureValue", "humidityValue"):  # a slow signal's size is how far it moved
            what = "temperature" if cap == "temperatureValue" else "humidity"
            return f"{what} {'rises' if kind.size > 0 else 'falls'} ~{fmt_size(abs(kind.size))} {unit} over {lasting}"
        return f"~{fmt_size(kind.size)} {unit} for {lasting}"
    if device == "home":  # people's devices all at rest, between two of them
        m = re.fullmatch(r"after (\w+) until (\w+)", kind.value or "")
        return f"quiet for {lasting}" + (f", after the {devices.get(m[1], m[1]).lower()} until the "
                                         f"{devices.get(m[2], m[2]).lower()}" if m else "")
    if _device_type(device) == "phone":  # a phone on the WiFi: someone is home
        return f"{'connected' if kind.value in ('on', None) else kind.value} for {lasting}"
    state = "open" if _device_type(device) == "contactSensor" and kind.value == "alarm" else kind.value or "active"
    return f"{state} for {lasting}"


def _items(value, key: str) -> list[dict]:
    """A part's answer as a list of dicts: the list itself, or the one under `key` (an export's dict)."""
    if isinstance(value, dict):
        value = value.get(key)
    return [x for x in value if isinstance(x, dict)] if isinstance(value, (list, tuple)) else []


def _with_episodes(db: sqlite3.Connection, occasions: list[dict], exported) -> list[dict]:
    """Each occasion with `episode_list`, its episodes' times: from the occasion, its export, or the
    occasion_episodes table (docs/contracts.md). `episodes` stays the count."""
    if not occasions:
        return []
    rows = exported.get("episodes") if isinstance(exported, dict) else None
    if not isinstance(rows, list):
        rows = [dict(zip(("occasion_id", "start", "end", "features"), r)) for r in
                _rows(db, "SELECT occasion_id, start, end, features FROM occasion_episodes ORDER BY start")]
    by_id: dict = defaultdict(list)
    for r in rows:
        if isinstance(r, dict):
            by_id[r.get("occasion_id")].append(_episode(r))
    out = []
    for o in occasions:
        own = o["episodes"] if isinstance(o.get("episodes"), list) else o.get("episode_list")
        episodes = [_episode(e) for e in own if isinstance(e, dict)] if isinstance(own, list) else by_id.get(o.get("id"), [])
        count = o["episodes"] if isinstance(o.get("episodes"), (int, float)) else len(episodes)
        out.append({**o, "episodes": count, "episode_list": episodes})
    return out


def _episode(row: dict) -> dict:
    return {"start": _clock(row.get("start")), "end": _clock(row.get("end")), "features": _json(row.get("features"))}


def _clock(value):
    """Epoch ms (the store's times) -> the home's clock time as ISO text; text is left as it is."""
    from .learn import to_local

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return to_local(int(value if value > 1e11 else value * 1000)).isoformat(timespec="seconds")
    return value


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "item"):  # numpy numbers
        return value.item()
    return str(value)


def _rows(db: sqlite3.Connection, sql: str) -> list[tuple]:
    """The rows, or none when the table isn't there (its part hasn't run)."""
    try:
        return db.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def with_names(view: dict, path: Path, items: str = "sensors") -> dict:
    """The view with every saved name (data/names/) on its `items`: the learned view's sensors, the knowledge
    view's patterns. The views are cached, and a later day's view may have named a sensor this one didn't.
    A name is never taken back."""
    try:
        cards = json.loads(path.read_text()) if path.exists() else {}
    except ValueError:
        cards = {}
    named = [{**s, **cards[s["key"]]} if s.get("name") is None and s.get("key") in cards else s
             for s in view[items]]
    return {**view, items: named, "named": view["named"] or any(s.get("name") for s in named)}


# --- the demo screen (docs/contracts.md, C11): moments, thoughts, planted vs found ---------------------
# Each answer is the real one when the parts it needs are there, else the fixture's (console/fixtures/<household>.json),
# else empty. `source` says which: "simulator" or "run", "fixture", or "none".

FIXTURES = CONSOLE / "fixtures"
DEMO_RUNS = Path(__file__).resolve().parent.parent / "data" / "demo"  # runs pre-computed by source/demo.py (part F)
_demo_cache: dict[Path, tuple[float, dict]] = {}


def demo_run(name: str, seed: int, start: date) -> dict | None:
    """What source/demo.py pre-computed for this home (data/demo/<stem>.json), or None: the moments with their
    focus, Alfred's thermostat writes on the moment days, and the views of those days. Re-read when it changes."""
    path = DEMO_RUNS / f"{_names_file(name, seed, start).stem}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _demo_cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        out = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("could not read %s", path)
        return None
    if not isinstance(out, dict):
        return None
    _demo_cache[path] = (mtime, out)
    return out


def _writes(name: str, seed: int, start: date) -> list[tuple[int, str, object]]:
    """Alfred's thermostat writes in the pre-computed run, [(ms, capability, value)], for `generate`."""
    rows = (demo_run(name, seed, start) or {}).get("writes") or []
    return [(int(w[0]), str(w[1]), w[2]) for w in rows if isinstance(w, list) and len(w) == 3]
THOUGHT_KINDS = ("habit_soon", "arrival", "departure", "habit_missing", "occasion")
MATCHES = ("yes", "partly", "no")


def _fixture(name: str) -> dict | None:
    """The demo fixture for a household, or None. Read on every call, so editing it shows on the next click."""
    if not re.fullmatch(r"[a-z0-9-]+", name):
        return None
    try:
        out = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return out if isinstance(out, dict) else None


def _demo_request(query: dict[str, str]) -> tuple[str, int, int, date, bool]:
    """Query fields -> (household, day, seed, start, simulated). The household defaults to "demo"; one only a
    fixture knows (the demo house before its preset lands) is allowed, with simulated False."""
    name = query.get("household", "demo")
    try:
        return (*_day_request({**query, "household": name}), True)
    except ValueError as exc:
        if "unknown household" not in str(exc) or _fixture(name) is None:
            raise
    _, day, seed, start = _day_request({k: v for k, v in query.items() if k != "household"})
    return name, day, seed, (start if "start" in query else _default_start(name)), False


def moments_view(name: str, seed: int = 1, start: date = DEFAULT_START, simulated: bool = True) -> dict:
    """The scripted moments (C12): source/demo.py's (with the habits it found as `focus`), else the simulator's,
    else the fixture's."""
    moments, source = _clean_moments((demo_run(name, seed, start) or {}).get("moments")) if simulated else [], "precomputed"
    if not moments and simulated:
        moments, source = _clean_moments(_sim_moments(name)), "simulator"
    if not moments:
        fixture = _fixture(name) or {}
        moments, source = _clean_moments(fixture.get("moments")), "fixture"
        start = _date(fixture.get("start")) or start
    return {"household": name, "start": start.isoformat(), "seed": seed, "moments": moments,
            "source": source if moments else "none"}


def _sim_moments(name: str):
    """The demo house's moments (simulate.DEMO_MOMENTS, focus not filled yet), or None for another home."""
    from . import simulate

    try:
        return simulate.DEMO_MOMENTS if name == "demo" else None
    except Exception:
        log.exception("reading the moments of %s failed", name)
        return None


def _clean_moments(rows) -> list[dict]:
    """Only moments the page can jump to: a whole day from 1 and an "HH:MM" time. Sorted by day and time."""
    out = []
    for m in rows if isinstance(rows, (list, tuple)) else []:
        m = asdict(m) if is_dataclass(m) and not isinstance(m, type) else m
        if not isinstance(m, dict):
            continue
        try:
            day, time_ = int(m.get("day")), str(m.get("time"))
        except (TypeError, ValueError):
            continue
        if day < 1 or not re.fullmatch(r"\d\d:\d\d", time_):
            continue
        focus = m.get("focus")
        out.append({"id": str(m.get("id") or f"day-{day}-{time_}"), "title": str(m.get("title") or f"Day {day}, {time_}"),
                    "day": day, "time": time_, "focus": [str(f) for f in focus] if isinstance(focus, list) else [],
                    "say": str(m.get("say") or "")})
    return sorted(out, key=lambda m: (m["day"], m["time"]))


def thoughts_view(name: str, day: int, seed: int = 1, start: date = DEFAULT_START, simulated: bool = True) -> dict:
    """Why Alfred woke its LLM on day `day`, and what came of it: one entry per decision a wake led to (one with
    no decision when it led to none), by time. From the run's `wakes` and `decisions` tables, a run pre-computed
    in data/demo/, or the fixture."""
    first = start + timedelta(days=day - 1)
    wakes, source, llm = None, "precomputed", None
    if simulated:  # the learning runs (/learned) don't run the loop, so only source/demo.py's run has wakes
        wakes = _precomputed_thoughts(name, seed, start, first)
        llm = (demo_run(name, seed, start) or {}).get("llm")
    if wakes is None:
        days = (_fixture(name) or {}).get("thoughts")
        rows = days.get(str(day)) if isinstance(days, dict) else None
        wakes, source = ([_thought(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None), "fixture"
    out = {"household": name, "day": day, "date": first.isoformat(), "wakes": wakes or [],
           "source": source if wakes is not None else "none"}
    if llm:  # who wrote the sentences: a model's name, or "scripted" (no model when it was pre-computed)
        out["llm"] = llm
    return out


def _precomputed_thoughts(name: str, seed: int, start: date, day: date) -> list[dict] | None:
    path = DEMO_RUNS / f"{_names_file(name, seed, start).stem}.sqlite3"
    if not path.exists():
        return None
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return read_thoughts(db, day)
    finally:
        db.close()


def read_thoughts(db: sqlite3.Connection, day: date) -> list[dict] | None:
    """The wakes of `day` (part D's `wakes` table: key, ts, data) with the decisions each led to (`decisions`
    rows whose data has `wake`: that key). None when there is no wakes table. Every field is read defensively."""
    if not _rows(db, "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'wakes'"):
        return None
    by_wake: dict = defaultdict(list)
    for (data,) in _rows(db, "SELECT data FROM decisions ORDER BY ts, id"):
        d = _json(data)
        if isinstance(d, dict) and d.get("wake"):
            by_wake[str(d["wake"])].append(d)
    iso, out = day.isoformat(), []
    for key, ts, data in _rows(db, "SELECT key, ts, data FROM wakes ORDER BY ts"):
        at = _clock(ts)
        if not isinstance(at, str) or at[:10] != iso:
            continue
        wake = _json(data)
        wake = wake if isinstance(wake, dict) else {}
        for d in by_wake.get(str(key)) or [None]:
            out.append(_thought({**wake, "key": key, "at": at}, d))
    return sorted(out, key=lambda t: (t["at"] or "", t["key"] or ""))


def _thought(wake: dict, decision: dict | None = None) -> dict:
    """One entry of /thoughts (C11), from a wake (its WakeReason data) and a decision it led to. A fixture row is
    already in this shape and passes through the same reading."""
    key = str(wake.get("key") or "")
    parts = key.split(":")
    kind = wake.get("kind") or (parts[0] if parts[0] in THOUGHT_KINDS else None)
    inner = wake.get("data") if isinstance(wake.get("data"), dict) else {}  # part D's row: {kind, ..., data: {...}}

    def card(what: str) -> dict:
        got = wake.get(what) if isinstance(wake.get(what), dict) else inner.get(what)
        return got if isinstance(got, dict) else {}
    habit_id = wake.get("habit_id") or card("habit").get("id") or (
        parts[1] if kind in ("habit_soon", "habit_missing") and len(parts) > 1 else None)
    occasion_id = wake.get("occasion_id") or card("occasion").get("id") or (
        parts[1] if kind == "occasion" and len(parts) > 1 else None)
    d = decision or {}
    confidence = d.get("confidence", wake.get("confidence"))
    return {"at": _clock(wake.get("at")), "kind": kind, "key": key or None, "habit_id": habit_id, "occasion_id": occasion_id,
            "subject": wake.get("subject") or card("habit").get("name") or card("occasion").get("name")
            or card("phone").get("name"),  # an arrival or a departure: whose phone
            "sentence": d.get("reason") or wake.get("sentence"),
            "confidence": confidence if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None,
            "mode": d.get("mode", wake.get("mode")), "status": d.get("status", wake.get("status")),
            "action": d.get("action", wake.get("action")), "due": d.get("at", wake.get("due"))}


def planted_view(name: str, day: int, seed: int = 1, start: date = DEFAULT_START, simulated: bool = True) -> dict:
    """Each rhythm the simulator planted (its answer key, in plain words) and the habit or occasion Alfred found
    for it by the end of day `day`. The simulator's answer key matched against /knowledge when there is one,
    else the fixture's rows for the latest day up to `day`."""
    key = _answer_key(name, seed, start) if simulated else None
    if key:
        k = knowledge_view(name, day, seed, start)
        rows, source = [_planted_row(entry, k) for entry in key], "simulator"
    else:
        days = (_fixture(name) or {}).get("planted")
        days = {int(d): r for d, r in days.items() if str(d).isdigit() and isinstance(r, list)} if isinstance(days, dict) else {}
        upto = max((d for d in days if d <= day), default=None)
        rows = [_planted_row(r, None) for r in days[upto] if isinstance(r, dict)] if upto else []
        source = "fixture" if upto else "none"
    return {"household": name, "day": day, "date": (start + timedelta(days=day - 1)).isoformat(), "rows": rows,
            "source": source}


def _answer_key(name: str, seed: int, start: date) -> list[dict]:
    """The planted rhythms (C12's answer key, simulate.planted), for a household of people; else none."""
    from .simulate import planted

    preset = PRESETS.get(name)
    if preset is None or not getattr(preset, "people", None):
        return []
    try:
        rows = planted(name, start)
    except Exception:
        log.exception("reading the answer key of %s failed", name)
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("planted")]


def _planted_row(entry: dict, k: dict | None) -> dict:
    """One row of /planted. An entry that already says what was found (a fixture's, or a key that was matched
    elsewhere) keeps it; else it is matched against what Alfred knows (`k`, the knowledge view)."""
    row = {"planted": str(entry.get("planted")), "found": entry.get("found"), "found_text": entry.get("found_text"),
           "match": entry.get("match") if entry.get("match") in MATCHES else None}
    if k is not None and row["match"] is None:
        row.update(_match(entry, k))
    if row["match"] is None:
        row["match"] = "yes" if row["found"] else "no"
    return row


def _match(entry: dict, k: dict) -> dict:
    """The answer key's row -> what Alfred found. Read from the row (all optional): `kind` ("habit", the default,
    "occasion", "warmup", "note" or "rule"), its weekday `windows` ([{"days", "usual"}]) or `usual` and `days`,
    and `person` ({"phone"} or a phone's client id)."""
    kind, days = entry.get("kind") or "habit", set(entry.get("days") or [])
    if kind == "warmup":
        w = k.get("warmup") or {}
        rate = w.get("rate_c_per_h")
        return {"found": "warmup" if rate else None, "match": "yes" if rate else "no",
                "found_text": f"warms {rate:g} °C an hour: 1 °C in about {round(60 / rate)} min" if rate else None}
    if kind == "occasion":
        found = [o for o in k.get("occasions", []) if not days or not o.get("weekdays") or days & set(o["weekdays"])]
        if not found:
            return {"found": None, "found_text": None, "match": "no"}
        o = max(found, key=lambda o: o.get("episodes") or 0)
        many = isinstance(o.get("episodes"), (int, float)) and o["episodes"] >= 2
        guests = (o.get("fingerprint") or {}).get("guest_phones")
        text = " · ".join(str(x) for x in (o.get("name") or o.get("id"), f"seen {o.get('episodes')} times",
                                            f"{guests} guests' phones" if guests else None) if x)
        return {"found": o.get("id"), "found_text": text, "match": "yes" if many else "partly"}
    person = entry.get("person")
    phone = person.get("phone") if isinstance(person, dict) else person
    habits = [h for h in k.get("habits", []) if h.get("status", "active") == "active"]
    if kind == "rule":  # a rule the people follow (the heating): Alfred knows it as steps of their habits
        signals = [s.rsplit(".", 1)[-1] for s in entry.get("signals") or []]
        parts = {p.get("id"): " ".join(p.get("parts") or []) for p in k.get("patterns", [])}
        mine = [h for h in habits if any(sig and sig in parts.get(st.get("pattern") if isinstance(st, dict) else st, "")
                                         for st in h.get("steps") or [] for sig in signals)]
        if not mine:
            return {"found": None, "found_text": None, "match": "no"}
        return {"found": mine[0].get("id"), "match": "partly",
                "found_text": "a step of " + ", ".join(h.get("name") or h.get("id") for h in mine)}
    if kind == "note":  # a fact about a person's days (João at home on Wednesdays): their habit says it, or not
        mine = [h for h in habits if phone and ((h.get("person") or {}).get("phone") == phone)]
        if not mine:
            return {"found": None, "found_text": None, "match": "no"}
        return {"found": mine[0].get("id"), "match": "partly", "found_text": _habit_text(mine[0], mine[0].get("windows"))}
    wanted = [w for w in entry.get("windows") or [] if isinstance(w, dict) and _minute(w.get("usual")) is not None]
    if not wanted and _minute(entry.get("usual")) is not None:
        wanted = [{"usual": entry["usual"], "days": sorted(days)}]
    best = None
    signals = [str(s) for s in entry.get("signals") or []]
    plugs = {s.split(".", 1)[0] for s in signals} if signals and all(s.startswith("plug:") for s in signals) else set()
    devices = _habit_devices(k) if plugs else {}
    for habit in habits:
        who = (habit.get("person") or {}).get("phone") if isinstance(habit.get("person"), dict) else None
        if phone and who and who != phone:
            continue
        windows = [w for w in habit.get("windows") or [] if isinstance(w, dict)] or [{**(habit.get("window") or {}),
                   "days": (habit.get("recurrence") or {}).get("weekdays") or []}]
        gaps = []
        for want in wanted or [{}]:
            on, usual = set(want.get("days") or []), _minute(want.get("usual"))
            here = [w for w in windows if not on or not w.get("days") or on & set(w["days"])]
            if not here:
                gaps.append(None)  # the planted days the habit doesn't cover
                continue
            gaps.append(min((min(g, 1440 - g) for w in here if usual is not None and _minute(w.get("usual")) is not None
                             for g in [abs(_minute(w.get("usual")) - usual)]), default=0))
        covered = [g for g in gaps if g is not None]
        if not covered:
            continue
        score = sum(covered) / len(covered) + 120 * (len(gaps) - len(covered)) / len(gaps)
        score += 0 if not phone or who == phone else 60
        score += 60 if plugs and not plugs & devices.get(habit.get("id"), set()) else 0  # an appliance: its own plug
        if best is None or score < best[0]:
            best = (score, habit, windows, len(covered) == len(gaps))
    if best is None or best[0] > 90:
        return {"found": None, "found_text": None, "match": "no"}
    score, habit, windows, whole = best
    return {"found": habit.get("id"), "found_text": _habit_text(habit, windows),
            "match": "yes" if score <= 30 and whole else "partly"}


def _habit_devices(k: dict) -> dict[str, set[str]]:
    """habit id -> the device parts of its steps' signals ("plug:washing-machine"), from the knowledge's kinds and
    its patterns' signatures (each pattern's `key` is "<id>|<signature>")."""
    kinds = {x.get("id"): x.get("signal") for x in k.get("kinds", []) if isinstance(x, dict)}
    signatures = {p.get("id"): str(p.get("key") or "").partition("|")[2] for p in k.get("patterns", []) if isinstance(p, dict)}
    out: dict[str, set[str]] = {}
    for h in k.get("habits", []):
        mine = set()
        for st in h.get("steps") or []:
            how, _, rest = signatures.get(st.get("pattern") if isinstance(st, dict) else st, "").partition(":")
            for part in rest.split(">", 1) if how == "link" else [rest]:
                mine.add(str(kinds.get(part, part)).split(".", 1)[0])
        out[h.get("id")] = mine
    return out


def _habit_text(habit: dict, windows) -> str:
    """"Waking up · every day · mon–fri 06:42–07:26, sat, sun 08:25–09:15"."""
    when = ", ".join(f"{_days_words(w.get('days'))} {w.get('earliest')}–{w.get('latest')}"
                     for w in windows or [] if isinstance(w, dict) and w.get("earliest") and w.get("latest"))
    who = (habit.get("person") or {}).get("name") if isinstance(habit.get("person"), dict) else None
    return " · ".join(str(x) for x in (habit.get("name") or habit.get("id"), who,
                                        (habit.get("recurrence") or {}).get("text"), when) if x)


def _days_words(days) -> str:
    days = list(days or [])
    if days == ["mon", "tue", "wed", "thu", "fri"]:
        return "weekdays"
    if days == ["sat", "sun"]:
        return "weekends"
    if len(days) == 7:
        return "every day"
    return ", ".join(d[:1].upper() + d[1:] for d in days)


def _minute(hhmm) -> int | None:
    m = re.fullmatch(r"(\d\d?):(\d\d)", str(hhmm or ""))
    return int(m[1]) * 60 + int(m[2]) if m else None


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "AlfredSimulator"

    def log_message(self, fmt, *args) -> None:
        pass

    def _local(self) -> bool:
        """Refuse other hosts and cross-site requests, like the console does."""
        port = self.server.server_address[1]
        ok = {f"localhost:{port}", f"127.0.0.1:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in ok and (origin is None or origin.split("://")[-1] in ok)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, json.dumps({"error": message}).encode(), "application/json")

    def do_GET(self) -> None:
        if not self._local():
            return self._error(403, "Forbidden")
        if self.path in ("/", "/index.html"):
            page = PAGE.read_text(encoding="utf-8").replace("__REAL_MAX_DAYS__", str(REAL_MAX_DAYS))
            return self._send(200, page.encode(), "text/html; charset=utf-8")
        url = urlsplit(self.path)
        if url.path in ("/visualizer", "/film"):
            page = VIEW_PAGE if url.path == "/visualizer" else FILM_PAGE
            return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/day":
            try:
                query = {k: v[-1] for k, v in parse_qs(url.query).items()}
                # alfred=off: the same day, same people, with none of Alfred's writes (what the house does alone).
                view = day_view(*_day_request(query), writes=[] if query.get("alfred") == "off" else None)
            except ValueError as exc:
                return self._error(400, str(exc))
            return self._send(200, json.dumps(view, separators=(",", ":")).encode(), "application/json")
        if url.path in ("/learned", "/knowledge"):
            try:
                args = _day_request({k: v[-1] for k, v in parse_qs(url.query).items()})
                household, _, seed, start = args
                names = _names_file(household, seed, start)
                view = (with_names(learned_view(*args), names) if url.path == "/learned"
                        else with_names(with_names(with_names(knowledge_view(*args), names, "patterns"),
                                                   names, "habits"), names, "occasions"))
            except ValueError as exc:
                return self._error(400, str(exc))
            except Exception:  # a broken learner: say so, rather than drop the connection
                log.exception("%s failed", self.path)
                return self._error(500, "Learning failed on this day: the simulator's log has the details.")
            return self._send(200, json.dumps(view, separators=(",", ":")).encode(), "application/json")
        if url.path in ("/moments", "/thoughts", "/planted"):
            try:
                household, day, seed, start, simulated = _demo_request({k: v[-1] for k, v in parse_qs(url.query).items()})
                view = (moments_view(household, seed, start, simulated) if url.path == "/moments"
                        else (thoughts_view if url.path == "/thoughts" else planted_view)(household, day, seed, start, simulated))
            except ValueError as exc:
                return self._error(400, str(exc))
            except Exception:
                log.exception("%s failed", self.path)
                return self._error(500, "That failed on the simulator: its log has the details.")
            return self._send(200, json.dumps(view, separators=(",", ":"), default=_jsonable).encode(), "application/json")
        self._error(404, "Not found")

    def do_POST(self) -> None:
        if not self._local():
            return self._error(403, "Forbidden")
        if self.path != "/generate":
            return self._error(404, "Not found")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                raise ValueError("request too large")
            form = {k: v[-1] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            kind, days, count = _request(form)
        except ValueError as exc:
            return self._error(400, str(exc))
        with tempfile.TemporaryFile() as tmp:
            write_zip(tmp, kind, days, count)
            size = tmp.tell()
            tmp.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{_zip_name(kind, days, count)}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while chunk := tmp.read(1 << 16):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser cancelled the download


def serve(port: int = 4781) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Alfred simulator: http://localhost:{port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
