"""The simulator's form: simulated houses or the real one, how many days, download. Local use only.

  uv run python -m source.simulate --serve      # then open http://localhost:4781

GET  /           the page (console/simulator.html)
POST /generate   kind (simulated | real), days, houses -> a zip with one JSONL file per house
GET  /visualizer the 3D view of one day (console/visualizer.html)
GET  /day        household, day[, seed, start] -> that day's snapshots, cut down to what the view draws
GET  /learned    household, day[, seed, start] -> what Alfred learned by that day, and each habit's status through it
GET  /knowledge  household, day[, seed, start] -> everything Alfred knows once that day is over (the page's second tab)

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

from .simulate import DEFAULT_START, PRESETS, REAL_HOME, _at, generate, home_a_max_days, houses

log = logging.getLogger("source.simulate_web")

CONSOLE = Path(__file__).resolve().parent.parent / "console"
PAGE = CONSOLE / "simulator.html"
VIEW_PAGE = CONSOLE / "visualizer.html"
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


def _day_request(query: dict[str, str]) -> tuple[str, int, int, date]:
    """Query fields -> (household, day, seed, start). Raises ValueError with a plain message."""
    name = query.get("household", "rosa")
    _household(name)
    try:
        day, seed = int(query.get("day", "1")), int(query.get("seed", "1"))
    except ValueError:
        raise ValueError("day and seed must be whole numbers") from None
    try:
        start = date.fromisoformat(query["start"]) if "start" in query else DEFAULT_START
    except ValueError:
        raise ValueError("start must be a date, YYYY-MM-DD") from None
    most = home_a_max_days(start) if name == REAL_HOME else MAX_DAYS
    if not 1 <= day <= most:
        raise ValueError(f"day must be between 1 and {most}" + (" for the real house" if name == REAL_HOME else ""))
    return name, day, seed, start


def day_view(name: str, day: int, seed: int = 1, start: date = DEFAULT_START) -> dict:
    """Day `day` (from 1) of the file `generate` writes for this household, cut down for the 3D view.

    `frames` is one [ts, changes] per snapshot. The first carries every device's state; the others only
    what changed since the one before: {device index: {capability: value}}. The index after the last
    device is the home's security. A frame with no changes still says the home reported then.
    """
    lines, truth = generate(_household(name), day, start=start, seed=seed)
    first = start + timedelta(days=day - 1)
    day_start, day_end = _at(first, 0), _at(first + timedelta(days=1), 0)
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
            "devices": devices, "frames": frames,
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
    `habits`, `occasions`: parts A and C of docs/habits-plan.md, in their shapes; each occasion also gets
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
    from .learn import storage, to_ms
    from .learn.today import sensor_today
    from .pipeline import _parse, run

    lines, _ = generate(_household(name), day, start=start, seed=seed)
    target = start + timedelta(days=day - 1)
    during: dict = {}
    parts = _Parts()

    def before_learn(db: sqlite3.Connection, d: date) -> None:
        if d != target:
            return
        storage.init(db)
        during["sensors"] = storage.export(db, d)["sensors"]
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
        during["statuses"] = {"ids": ids or [], "frames": frames}

    with tempfile.TemporaryDirectory() as tmp:  # habits and occasions learn each night in learn_day
        run(_parse(lines), Path(tmp), before_learn=before_learn)
        db = sqlite3.connect(Path(tmp) / "home.sqlite3")
        try:
            named = _name(db, _names_file(name, seed, start))
            after = storage.export(db, target)
            keys = {sid: f"{sid}|{sig}" for sid, sig in db.execute("SELECT id, signature FROM sensors")}
            knowledge = _knowledge(db, target, after["sensors"], keys, parts)  # after the namer: reasons use names
        finally:
            db.close()

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
                          **knowledge}}


# --- the knowledge tab: the other parts of docs/habits-plan.md, read defensively ---------------------

class _Parts:
    """The other parts' functions, for one run. A part not written yet is "missing"; one that doesn't import or
    raises is "failed" (logged once, then left alone for the rest of the run). Either way it gives nothing."""

    MODULES = {"habits": (".learn.grouping", "export_habits"), "occasions": (".learn.occasions", "export_occasions"),
               "forecast": (".learn.forecast", "forecast"), "decisions": (".anticipate", "plan_decisions")}

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
    devices = {}
    for kind, name in _rows(db, "SELECT type, name FROM devices ORDER BY first_ts, device_id"):
        devices.setdefault(kind, name)
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
    return {"tomorrow": tomorrow.isoformat(), "named": named, "modules": dict(parts.state), "devices": devices,
            "kinds": kinds, "patterns": patterns, **found}


def _kind_card(kind, devices: dict[str, str]) -> dict:
    """One event type (a kind of the grouper, source/learn/kinds.py) for the knowledge tab."""
    from .learn.kinds import UNITS

    device, cap = kind.signal.split(".", 1)
    return {"id": kind.id, "signal": kind.signal, "device": device, "device_name": devices.get(device),
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
    state = "open" if device == "contactSensor" and kind.value == "alarm" else kind.value or "active"
    return f"{state} for {lasting}"


def _items(value, key: str) -> list[dict]:
    """A part's answer as a list of dicts: the list itself, or the one under `key` (an export's dict)."""
    if isinstance(value, dict):
        value = value.get(key)
    return [x for x in value if isinstance(x, dict)] if isinstance(value, (list, tuple)) else []


def _with_episodes(db: sqlite3.Connection, occasions: list[dict], exported) -> list[dict]:
    """Each occasion with `episode_list`, its episodes' times: from the occasion, its export, or the
    occasion_episodes table (docs/habits-plan.md). `episodes` stays the count."""
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
        if url.path == "/visualizer":
            return self._send(200, VIEW_PAGE.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/day":
            try:
                view = day_view(*_day_request({k: v[-1] for k, v in parse_qs(url.query).items()}))
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
