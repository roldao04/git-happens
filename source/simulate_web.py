"""The simulator's form: simulated houses or the real one, how many days, download. Local use only.

  uv run python -m source.simulate --serve      # then open http://localhost:4781

GET  /           the page (console/simulator.html)
POST /generate   kind (simulated | real), days, houses -> a zip with one JSONL file per house

"simulated" gives made-up homes (`simulate.houses`), each different. "real" replays the one recorded house
(home-a). The same choices always give the same zip; for a specific named household, use the command line
(docs/simulator.md).
"""
import json
import tempfile
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from .simulate import REAL_HOME, generate, home_a_max_days, houses

PAGE = Path(__file__).resolve().parent.parent / "console" / "simulator.html"
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
