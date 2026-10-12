"""Thin optional mounted trajectory domain lookup; no numeric fallback here."""
import sys


def domain():
    module = sys.modules.get("qascade_ce.native_trajectory")
    return module if module is not None and module.available() else None


def sample(seq, position):
    native = domain()
    if native is not None and native.accepts_sample(seq, position):
        return native.sample(seq, position)
    return _legacy_sample(seq, position)


def _legacy_pair(left, right, position):
    t0, v0 = left
    t1, v1 = right
    if t1 <= t0:
        return v1
    if isinstance(v0, (int, float)) and isinstance(v1, (int, float)) \
            and not isinstance(v0, bool):
        fraction = (position - t0) / (t1 - t0)
        return v0 + (v1 - v0) * fraction
    return v0 if (position - t0) <= (t1 - position) else v1


def _legacy_sample(seq, position):
    if not seq:
        return None
    if position <= seq[0][0]:
        return seq[0][1]
    if position >= seq[-1][0]:
        return seq[-1][1]
    for left, right in zip(seq, seq[1:]):
        if left[0] <= position <= right[0]:
            return _legacy_pair(left, right, position)
    return seq[-1][1]


def apply_range(ctx, channel, value, destination):
    native = domain()
    if native is not None and native.accepts_value(value, "range", ctx):
        return native.apply(ctx, channel, value, destination, "range")
    return _legacy_range(ctx, channel, value, destination)


def _legacy_range(ctx, channel, value, destination):
    from .trajectory_replay import _apply_strip
    try:
        mode, base, target = value
        base = max(0, min(255, int(round(float(base)))))
        target = max(0, min(255, int(round(float(target)))))
    except (TypeError, ValueError):
        return
    if str(mode) == "flat":
        _apply_strip(ctx, channel, target, destination)
        return
    offsets = ctx.offsets_by_fixture(channel)
    count = len(offsets)
    if not count:
        return
    for universe, offset, index in offsets:
        fraction = index / (count - 1) if count > 1 else 1.0
        if str(mode) == "two_way":
            fraction = 1.0 - abs(1.0 - 2.0 * fraction)
        destination.setdefault(universe, {})[offset] = int(round(base + (target - base) * fraction))


def _legacy_entry(key, released, registry):
    key = str(key or "")
    if key in released:
        return None
    parts = key.split("|")
    if len(parts) < 2:
        return None
    applier = registry.get("|".join(parts[:2]))
    return (applier, "|".join(parts[2:])) if applier is not None else None


def _legacy_apply(ctx, entry, seq, source_t, destination):
    from .trajectory_replay import sample_trajectory
    applier, suffix = entry
    value = sample_trajectory([(float(time), value) for time, value in (seq or [])],
                              float(source_t))
    if value is None:
        return
    try:
        applier(ctx, suffix, value, destination)
    except Exception:
        return


def build(clip, source_t, mappings, released):
    native = domain()
    if native is not None:
        return native.build(clip, source_t, mappings, released)
    from .trajectory_replay import CONTROL_APPLIERS, ReplayContext
    trajectories = getattr(clip, "trajectories", None) or {}
    if not trajectories:
        return {}
    fixture_types, universes, starts = mappings
    ctx = ReplayContext(fixture_types,
                        list(getattr(clip, "base_order_fixture_ids", []) or []),
                        universes, starts)
    destination = {}
    for key, seq in trajectories.items():
        entry = _legacy_entry(key, released, CONTROL_APPLIERS)
        if entry is not None:
            _legacy_apply(ctx, entry, seq, source_t, destination)
    return destination
