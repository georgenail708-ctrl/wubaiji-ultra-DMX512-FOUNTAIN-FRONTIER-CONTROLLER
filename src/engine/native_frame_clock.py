"""Native frame scalars retain the public engine's original state owners."""
import sys


def clock():
    module = sys.modules.get("qascade_ce.native_source_motion_clock")
    return module if module is not None and module.available() else None


def rewind_needed(now, previous):
    native = clock()
    if native is not None:
        return native.rewind_needed(now, previous)
    return previous is not None and now < previous - 1e-9


def consumption_boost(count):
    native = clock()
    if native is not None:
        return native.consumption_boost(count)
    return (float(count) + 1.0) * 1e9


def cache_index(interval, end, now):
    native = clock()
    if native is not None:
        return native.cache_index(interval, end, now)
    interval = max(.001, interval)
    end = max(0., end)
    return int(round(min(max(0., now), end) / interval))
