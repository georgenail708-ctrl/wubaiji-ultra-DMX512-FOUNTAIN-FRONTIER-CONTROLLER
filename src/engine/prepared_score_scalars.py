"""Descriptor scalar math shares the installed source-clock native domain."""
import sys


def score_scalars(engine, item, index):
    payload = dict(rate=engine._clip_playback_rate(item.clip), start=item._start_t,
        duration=getattr(item.clip, "duration", 0.0) or 0.0,
        priority=item._dr_priority, track_order=item._track_order,
        total_priority=item.total_priority, hold=item.hold_extension, index_valid=index >= 0)
    clock = sys.modules.get("qascade_ce.native_source_motion_clock")
    if clock is not None and clock.available():
        return clock.score_scalars(payload)
    return _python_scalars(payload)


def _python_scalars(payload):
    rate, start = payload["rate"], float(payload["start"])
    cap = float("inf")
    if payload["hold"] and payload["index_valid"]:
        cap = start + max(0.0, float(payload["duration"]))
    return dict(rate=rate if rate > 1e-9 else 1.0, hold_cap=cap,
        tie=float(payload["priority"]) * 1e9
            + (float(payload["track_order"]) + 1.0) * 1e-3 + start * 1e-4,
        preboost=(float(payload["total_priority"]) + 1.0) * 1e12)
