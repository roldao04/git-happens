"""Cached LLM replies, for an instant and repeatable demo (docs/contracts.md C10).

  cached(kind, payload, call) -> str

The key is the sha256 of `kind` and the payload as sorted JSON. The caller leaves out anything
that only says when the request was made, so the same question always has the same key. Replies
are appended to data/llm-cache/<kind>.jsonl (gitignored); the last line for a key wins.

ALFRED_LLM_CACHE:
  (unset) or on   a hit never calls the model; a miss calls it and stores the reply
  only            a miss raises CacheMiss instead of calling (the demo: no network)
  off             never reads the cache; still stores what the model says, so a run refreshes it

ALFRED_LLM_CACHE_DIR moves the folder (the tests use a temporary one).

Only models with a name are cached (model_name): the name is part of the key, and the tests'
fake models, which have none, never touch the cache.
"""
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

log = logging.getLogger("source.llm_cache")

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "llm-cache"
MODES = ("on", "only", "off")


class CacheMiss(RuntimeError):
    """ALFRED_LLM_CACHE=only and the reply isn't cached."""


def mode() -> str:
    m = (os.environ.get("ALFRED_LLM_CACHE") or "on").strip().lower()
    if m not in MODES:
        log.warning("ALFRED_LLM_CACHE=%r is not one of %s; using 'on'", m, MODES)
        return "on"
    return m


def folder() -> Path:
    return Path(os.environ.get("ALFRED_LLM_CACHE_DIR") or DEFAULT_DIR)


def key(kind: str, payload: dict) -> str:
    text = kind + "\n" + json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def model_name(model) -> str | None:
    """The model's name (an OpenRouter slug), or None for a model without one (not cached)."""
    for attr in ("model_name", "model"):
        v = getattr(model, attr, None)
        if isinstance(v, str) and v:
            return v
    return None


def _path(kind: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in kind) or "llm"
    return folder() / f"{safe}.jsonl"


def lookup(kind: str, k: str) -> str | None:
    """The stored reply for key `k`, or None."""
    path = _path(kind)
    if not path.exists():
        return None
    found = None
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a half-written line: ignored
        if isinstance(row, dict) and row.get("key") == k and isinstance(row.get("reply"), str):
            found = row["reply"]
    return found


def store(kind: str, k: str, reply: str) -> None:
    try:
        path = _path(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"key": k, "reply": reply,
                                "stored": datetime.now().isoformat(timespec="seconds")}) + "\n")
    except OSError as exc:  # a cache that can't be written must not cost the reply
        log.warning("llm cache: could not store a %s reply: %s", kind, exc)


def _before(kind: str, payload: dict) -> tuple[str, str, str | None]:
    m, k = mode(), key(kind, payload)
    hit = lookup(kind, k) if m != "off" else None
    if hit is None and m == "only":
        raise CacheMiss(f"no cached {kind} reply (ALFRED_LLM_CACHE=only), key {k[:12]}")
    return m, k, hit


def cached(kind: str, payload: dict, call: Callable[[], str]) -> str:
    """The cached reply to this request, or `call()`'s (then stored)."""
    _, k, hit = _before(kind, payload)
    if hit is not None:
        log.debug("llm cache hit: %s %s", kind, k[:12])
        return hit
    reply = call()
    store(kind, k, reply)
    return reply


async def acached(kind: str, payload: dict, call: Callable[[], Awaitable[str]]) -> str:
    """cached() for an async call."""
    _, k, hit = _before(kind, payload)
    if hit is not None:
        log.debug("llm cache hit: %s %s", kind, k[:12])
        return hit
    reply = await call()
    store(kind, k, reply)
    return reply
