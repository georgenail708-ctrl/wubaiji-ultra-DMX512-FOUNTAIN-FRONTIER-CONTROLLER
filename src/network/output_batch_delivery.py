"""One router drain owns ordinary and optional CE batch delivery deadlines."""
import time


def _batch_handler(router, records):
    owner = getattr(router._sink, "__self__", router._sink)
    batch = getattr(owner, "send_dmx_batch", None)
    if records is not None or router._attempt_scope is not None:
        batch = None  # Original per-attempt identity boundary remains authoritative.
    return batch


def _note_queue_age(router, group, queued_at):
    now = time.monotonic()
    for universe, _data in group:
        age = max(0.0, now - queued_at.get(universe, now)) * 1000.0
        router._delivery_stats["queue_age_max_ms"] = max(
            router._delivery_stats["queue_age_max_ms"], age)


def _attempt_batch(router, batch, group):
    if not callable(batch) or len(group) <= 1:
        return False
    try:
        handled = bool(batch(dict(group)))
    except Exception:
        return False  # Preserve the original PACK fallback contract.
    if handled:
        completed = time.monotonic()
        for universe, data in group:
            router._last_frames[universe] = data
            router._last_attempt_t[universe] = completed
        router._delivery_stats["attempted"] += len(group)
    return handled


def _attempt_single(router, group, records):
    for universe, data in group:
        if records is None:
            router._send_one(universe, data)
        else:
            router._send_one(universe, data, record=records.get(universe))


def deliver_dequeued(router, frames, records, queued_at, pacer_type):
    batch = _batch_handler(router, records)
    step = 4 if callable(batch) and len(frames) >= 8 else (len(frames) if callable(batch) else 1)
    pacer = pacer_type(len(frames))
    for index in range(0, len(frames), max(1, step)):
        pacer.wait(index)
        group = frames[index:index + max(1, step)]
        _note_queue_age(router, group, queued_at)
        if not _attempt_batch(router, batch, group):
            _attempt_single(router, group, records)
