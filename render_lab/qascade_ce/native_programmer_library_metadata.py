"""Per-call readonly snapshots; no persistent numeric cache or new owners."""
import ctypes as ct
from types import SimpleNamespace

import numpy as np

from .native_binding import borrowed_pointer
from .native_curve_scalar_metadata import _value, prepare
from .native_curve_scalar_types import Value
from .native_trajectory_values import attach_pool

NUMERIC = (bool, int, float)


def scalar(mode, source=None, argument=0.):
    if source is None:
        source = SimpleNamespace(curve=None, points=[])
    return prepare(mode, source, argument)


def array(prepared, rows, dtype, pointer_type):
    owner = prepared.hold(np.ascontiguousarray(rows, dtype=dtype))
    return borrowed_pointer(owner, pointer_type)


def extend(prepared, options, request):
    source = prepared.request
    pool = bytearray(ct.string_at(source.integers, source.integer_size))
    request.groups = _value(options.get("groups", 1), pool, prepared)
    request.batches = _value(options.get("batches", 0), pool, prepared)
    for name in ("typed_a", "typed_b", "typed_p"):
        if name in options:
            rows = [_value(value, pool, prepared) for value in options[name]]
            owner = prepared.hold((Value * len(rows))(*rows))
            setattr(request, name, ct.cast(ct.addressof(owner), ct.POINTER(Value)))
    values = options.get("values")
    if values is not None:
        rows = [_value(value, pool, prepared) for value in values]
        owner = prepared.hold((Value * len(rows))(*rows))
        request.values = ct.cast(ct.addressof(owner), ct.POINTER(Value))
    prepared.hold(attach_pool(source, pool))


def edges(source):
    return [0., *source.cuts, 1.]


def targets(source):
    rows = [(str((row or {}).get("group_id", "") or ""),
             [str(c) for c in ((row or {}).get("channel_types", []) or []) if str(c)])
            for row in (source.targets or [])]
    return [(group, channels) for group, channels in rows if group and channels]


def segment(source, index):
    from src.ui.trajectory_curve import TrajectorySegment
    return source.segments[index] if index < len(source.segments) else TrajectorySegment()


def resolve(resolver, group, scope):
    try:
        return resolver(group, scope) or []
    except Exception:
        return []


def unit_groups(subgroups, segment_owner):
    from src.ui import trajectory_wing_adapter
    groups = []
    for raw in subgroups:
        ids = [str(fid) for fid in raw if str(fid)]
        if ids:
            groups.append(trajectory_wing_adapter.units(ids, segment_owner)
                          or [[fid] for fid in ids])
    return groups


def flattened(groups):
    units, offsets = [], [0]
    for group in groups:
        units.extend(group)
        offsets.append(len(units))
    return units, offsets


def assign_units(units, values):
    output = {}
    for unit, value in zip(units, values):
        for fid in unit:
            output[str(fid)] = value
    return output


def group_options(segment_owner, n, progress, duration):
    return dict(n=n, group_n=len(segment_owner.group_pauses),
                a=segment_owner.group_pauses, groups=segment_owner.groups,
                batches=segment_owner.group_batches,
                flags=(1 if segment_owner.group_mode == "sequential" else 0)
                      | (2 if segment_owner.direction == "whole" else 0),
                values=[duration], p2=segment_owner.group_pause)
