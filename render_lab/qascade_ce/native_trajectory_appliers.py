"""Original registry/context dispatch, with native rounding and sparse math."""
from .native_trajectory import evaluate
from .native_trajectory_types import Applier, Event, Offset, pointer, request, rows
from .native_trajectory_values import (attach_pool, decode_integer, output_pool,
                                       parsed, raise_semantic)


def _metadata(ctx, channel, value, kind):
    if kind != "range":
        offsets = [(uni, off, 0) for uni, off in ctx.offsets_for(channel)]
        return 0, 0, value, value, offsets
    try:
        mode, base, target = value
    except (TypeError, ValueError):
        return None
    mode = str(mode)
    if mode == "flat":
        offsets = [(uni, off, 0) for uni, off in ctx.offsets_for(channel)]
        return 1, 2, base, target, offsets
    return 1, int(mode == "two_way"), base, target, ctx.offsets_by_fixture(channel)


def _offset_rows(offsets):
    universes = list(dict.fromkeys(uni for uni, _, _ in offsets))
    index = {uni: i for i, uni in enumerate(universes)}
    items = [Offset(universe=index[uni], offset=off, fixture=fixture)
             for uni, off, fixture in offsets]
    return universes, rows(Offset, items)


def _apply_native(metadata):
    kind, mode, base, target, offsets = metadata
    pool = bytearray()
    value = parsed(base, pool)
    end = parsed(target, pool) if kind == 1 else value
    universes, offset_rows = _offset_rows(offsets)
    appliers = rows(Applier, [Applier(kind=kind, mode=mode, offset=0,
                                    count=len(offsets), value=value, target=end)])
    events = rows(Event, [Event() for _ in offsets])
    payload = request()
    payload.appliers, payload.applier_count = pointer(appliers, Applier), 1
    payload.offsets, payload.offset_count = pointer(offset_rows, Offset), len(offsets)
    payload.event_out, payload.event_capacity = pointer(events, Event), len(events)
    integers = attach_pool(payload, pool)
    output = output_pool(payload, len(offsets) * 132 + 1)
    report = evaluate(2, payload, (metadata, offset_rows, appliers, events, integers, output))
    return report, events, output, universes


def apply(ctx, channel, value, destination, kind):
    metadata = _metadata(ctx, channel, value, kind)
    if metadata is None:
        return
    report, events, output, universes = _apply_native(metadata)
    if report.semantic in (2, 6, 7):
        return
    for event in events[:report.event_count]:
        destination.setdefault(universes[event.universe], {})[event.offset] = (
            decode_integer(event.value, output))
    raise_semantic(report.semantic)


def _key_parts(key, released, registry):
    key = str(key or "")
    if key in released:
        return None
    parts = key.split("|")
    if len(parts) < 2:
        return None
    applier = registry.get("|".join(parts[:2]))
    return (applier, "|".join(parts[2:])) if applier is not None else None


def _apply_control(ctx, entry, seq, source_t, destination):
    from src.processing.dispatch.trajectory_replay import sample_trajectory
    from .native_trajectory import registered_builtin
    applier, suffix = entry
    value = sample_trajectory([(float(time), value) for time, value in (seq or [])],
                              float(source_t))
    if value is None:
        return
    registered_builtin(applier)
    try:
        applier(ctx, suffix, value, destination)
    except Exception:
        return


def build(clip, source_t, mappings, released):
    from src.processing.dispatch.trajectory_replay import CONTROL_APPLIERS, ReplayContext
    trajectories = getattr(clip, "trajectories", None) or {}
    if not trajectories:
        return {}
    fixture_types, universes, starts = mappings
    ctx = ReplayContext(fixture_types,
                        list(getattr(clip, "base_order_fixture_ids", []) or []),
                        universes, starts)
    destination = {}
    for key, seq in trajectories.items():
        entry = _key_parts(key, released, CONTROL_APPLIERS)
        if entry is not None:
            _apply_control(ctx, entry, seq, source_t, destination)
    return destination
