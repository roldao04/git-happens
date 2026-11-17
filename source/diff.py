"""Two states → events.

A changed lastCommunicationTime alone is not an event (it's a device heartbeat), but it decides
whether a device is stale.
"""
from .model import Event, State
from .normalize import device_last_seen


def diff(prev: State | None, cur: State, now_ms: int, stale_ms: int,
         prev_now_ms: int | None = None) -> list[Event]:
    """prev_now_ms: when `prev` was polled (staleness then is judged at that time)."""
    events: list[Event] = []
    if prev is None:
        return events  # first snapshot is the baseline

    for key, r in cur.items():
        old = prev.get(key)
        if old is not None and old.value != r.value:
            events.append(Event(now_ms, r.device_id, r.device, r.capability, "change", old.value, r.value))
        elif old is None:
            events.append(Event(now_ms, r.device_id, r.device, r.capability, "change", None, r.value))
    for signal, old in prev.items():
        if signal not in cur:
            events.append(Event(now_ms, old.device_id, old.device, old.capability, "gone", old.value, None))

    prev_seen, cur_seen = device_last_seen(prev), device_last_seen(cur)
    for dev_id, (name, comm) in cur_seen.items():
        was_stale = (dev_id in prev_seen and prev_now_ms is not None
                     and prev_now_ms - prev_seen[dev_id][1] > stale_ms)
        is_stale = now_ms - comm > stale_ms
        if is_stale and not was_stale:
            events.append(Event(now_ms, dev_id, name, "lastCommunicationTime", "stale", None, comm))
        elif was_stale and not is_stale:
            events.append(Event(now_ms, dev_id, name, "lastCommunicationTime", "returned", None, comm))
    return events


def initially_stale(cur: State, now_ms: int, stale_ms: int) -> list[Event]:
    """On startup, report devices that are already silent (diff() can't see a transition)."""
    return [
        Event(now_ms, dev_id, name, "lastCommunicationTime", "stale", None, comm)
        for dev_id, (name, comm) in device_last_seen(cur).items()
        if now_ms - comm > stale_ms
    ]
