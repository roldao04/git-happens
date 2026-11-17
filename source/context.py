"""What the agent reads when it wakes (docs/event-loop.md §5)."""
from datetime import datetime

from .model import State, WakeReason
from .store import Store


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")


def build_context(now_ms: int, reasons: list[WakeReason], state: State, store: Store,
                  last_run_ms: int | None, stale_ms: int) -> dict:
    devices: dict[str, dict] = {}
    for r in state.values():
        d = devices.setdefault(r.device_id, {"name": r.device, "type": r.device_type})
        d[r.capability] = r.value
        if r.comm_ts is not None:
            d["lastSeen"] = _iso(r.comm_ts)
            age = now_ms - r.comm_ts
            d["fresh"] = age <= stale_ms
            if not d["fresh"]:
                d["staleMinutes"] = age // 60_000

    midnight = datetime.fromtimestamp(now_ms / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
    today = store.events_since(int(midnight.timestamp() * 1000))
    since_last = [e for e in today if last_run_ms is None or e.ts > last_run_ms]

    def line(e):
        return f"{_iso(e.ts)} {e.describe()}"

    return {
        "now": _iso(now_ms),
        "reasons": [r.to_dict() for r in reasons],
        "state": devices,
        "events_since_last_run": [line(e) for e in since_last][-200:],
        "timeline_today": [line(e) for e in today][-500:],
        "baselines": {},  # filled by the nightly review, later
    }
