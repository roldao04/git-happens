"""The simulator's form: simulated houses or the real one, how many days, download. Local use only.

  uv run python -m source.simulate --serve      # then open http://localhost:4781

GET  /           the page (console/simulator.html)
POST /generate   kind (simulated | real), days, houses -> a zip with one JSONL file per house
GET  /visualizer the 3D view of one day (console/visualizer.html)
GET  /day        household, day[, seed, start] -> that day's snapshots, cut down to what the view draws
GET  /learned    household, day[, seed, start] -> what Alfred learned by that day, and each habit's status through it

"simulated" gives made-up homes (`simulate.houses`), each different. "real" replays the one recorded house
(home-a). The same choices always give the same zip; for a specific named household, use the command line
(docs/simulator.md).
"""
import json
import re
import sqlite3
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .simulate import DEFAULT_START, PRESETS, REAL_HOME, _at, generate, home_a_max_days, houses

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


# --- what Alfred learned, for the 3D view -----------------------------------------------------------

MAX_LEARN_DAYS = 60  # simulate + ingest + learn from day 1: about 7 s for 21 days, so a cap keeps the page usable
STATUS_STEP_MIN = 5  # a habit's status through the day is worked out this often


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
            "merged_into": sensor.get("merged_into"), "moved_from": p.get("moved_from")}


@lru_cache(maxsize=12)
def learned_view(name: str, day: int, seed: int = 1, start: date = DEFAULT_START) -> dict:
    """Days 1 to `day` through the pipeline (simulate, ingest, learn each night), seen from day `day`.

    `sensors`: every sensor Alfred had when the day began, plus those first learned that night
    (`tonight`: learned, fading, merged or moved, from the learners' run at the day's end).
    `statuses`: each active habit's status (source/learn/today.py) every STATUS_STEP_MIN minutes of the day,
    kept only where one changes: [ms, [[status, at] per id in `ids`]].
    `readings`: the occurrences inside that day, as the learners saw them that night: [sensor id, start ms, end ms].
    """
    from .learn import storage, to_ms
    from .learn.today import sensor_today
    from .pipeline import _parse, run

    if not 1 <= day <= MAX_LEARN_DAYS:
        raise ValueError(f"Alfred learns from day 1, so the learned view stops at day {MAX_LEARN_DAYS}")
    lines, _ = generate(_household(name), day, start=start, seed=seed)
    target = start + timedelta(days=day - 1)
    during: dict = {}

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

    with tempfile.TemporaryDirectory() as tmp:
        run(_parse(lines), Path(tmp), before_learn=before_learn)
        db = sqlite3.connect(Path(tmp) / "home.sqlite3")
        try:
            after = storage.export(db, target)
        finally:
            db.close()

    before = {s["id"]: s for s in during.get("sensors", [])}
    sensors = []
    for s in after["sensors"]:
        was = before.get(s["id"])
        card = _card(was or s)
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
    return {"household": name, "day": day, "date": iso, "max_days": MAX_LEARN_DAYS, "sensors": sensors,
            "statuses": during.get("statuses", {"ids": [], "frames": []}), "readings": readings}


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
        if url.path == "/learned":
            try:
                view = learned_view(*_day_request({k: v[-1] for k, v in parse_qs(url.query).items()}))
            except ValueError as exc:
                return self._error(400, str(exc))
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
