"""Logging: console + daily-rotated text file + JSON lines.

  data/logs/loop.log     human-readable, same as the console (tail -f it)
  data/logs/loop.jsonl   one JSON object per line, with structured `data` (jq it)

Attach structured data to any log call with `extra={"data": {...}}`; it goes to the JSONL file.
"""
import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

TEXT_FORMAT = "%(asctime)s %(levelname)-5s %(name)-13s %(message)s"
DATE_FORMAT = "%H:%M:%S"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if data is not None:
            out["data"] = data
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


def setup_logging(log_dir: Path | None, level: str = "INFO", console: bool = True) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)  # handlers filter; files always keep DEBUG

    if console:
        h = logging.StreamHandler(sys.stderr)
        h.setLevel(level.upper())
        h.setFormatter(logging.Formatter(TEXT_FORMAT, DATE_FORMAT))
        root.addHandler(h)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        text = TimedRotatingFileHandler(log_dir / "loop.log", when="midnight", backupCount=14, encoding="utf-8")
        text.setLevel(logging.DEBUG)
        text.setFormatter(logging.Formatter(TEXT_FORMAT, "%Y-%m-%d %H:%M:%S"))
        root.addHandler(text)

        js = TimedRotatingFileHandler(log_dir / "loop.jsonl", when="midnight", backupCount=14, encoding="utf-8")
        js.setLevel(logging.DEBUG)
        js.setFormatter(JsonFormatter())
        root.addHandler(js)

    for noisy in ("mcp", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
