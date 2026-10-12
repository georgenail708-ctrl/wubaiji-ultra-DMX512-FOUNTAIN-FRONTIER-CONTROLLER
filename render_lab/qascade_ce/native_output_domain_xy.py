"""Frozen XY metadata packing and synchronous native physical transform.

Rows retain their exact frozen reference list; no permanent bare-id cache keys.
Dynamic Z scope is container metadata. Axis/rotation/rounding math stays native.
"""
import ctypes as ct
import threading
from collections import OrderedDict

import numpy as np


class XYTask(ct.Structure):
    _fields_ = [(name, ct.c_int) for name in
                ("x", "y", "xf", "yf", "z", "zf", "enabled", "cached", "command")] + [
        (name, ct.c_double) for name in
        ("cosine", "sine", "sign", "angle", "base", "zmin", "zmax", "zangle", "zcommand")]


_CACHE = OrderedDict()
_LOCK = threading.RLock()
_ORIGINAL = None


def _address(value):
    return -1 if value is None else int(value)


def _row(ref):
    from src.engine.xy_reference import normalize_reference_mode
    cached = ref.cos_rad is not None and ref.sin_rad is not None
    enabled = (ref.mode_is_sync if ref.mode_is_sync is not None
               else normalize_reference_mode(ref.mode) == "sync")
    return XYTask(x=int(ref.x_addr), y=int(ref.y_addr), xf=_address(ref.x_fine_addr),
        yf=_address(ref.y_fine_addr), z=_address(ref.z_addr), zf=_address(ref.z_fine_addr),
        enabled=int(enabled), cached=1 if cached else 2 if ref.uniform_world else 0,
        command=0, cosine=float(ref.cos_rad or 0.), sine=float(ref.sin_rad or 0.),
        sign=float(ref.y_sign_eff if ref.y_sign_eff is not None else
                   -1. if float(ref.y_axis_sign or 1.) < 0. else 1.),
        angle=float(ref.angle_deg or 0.), base=float(ref.base_angle_deg or 0.),
        zmin=float(ref.z_min_deg if ref.z_min_deg is not None else -180.),
        zmax=float(ref.z_max_deg if ref.z_max_deg is not None else 180.),
        zangle=float(ref.z_angle_deg or 0.), zcommand=0.)


def _rows(refs):
    key = id(refs)
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is None or entry[0] is not refs or entry[1] != tuple(refs):
            rows = (XYTask * len(refs))(*(_row(ref) for ref in refs))
            entry = (refs, tuple(refs), rows)
            _CACHE[key] = entry
            if len(_CACHE) > 128:
                _CACHE.popitem(last=False)
        return entry[2]


def transform(data, refs, options):
    from . import native_output_domain as domain
    if domain.NATIVE is None:
        return None
    inverse, command, selected, clip_owned = options
    original = _rows(refs)
    rows = (XYTask * len(refs)).from_buffer_copy(original)
    if command is not None:
        for row, ref in zip(rows, refs):
            identity = str(ref.fixture_id or "")
            row.command = int((selected is None or identity in selected)
                              and not (clip_owned and identity in clip_owned))
            row.zcommand = float(command)
    return domain.NATIVE.xy(rows, np.asarray(data, np.uint8), inverse)


def native_rotation(data, refs_by_universe, universe, *, to_virtual,
                    z_command_deg=None, z_ids=None, clip_owned_z=None):
    from . import native_output_domain as domain
    refs = refs_by_universe.get(int(universe)) if refs_by_universe else None
    if not refs:
        return data
    try:
        result = transform(data, refs, (to_virtual, z_command_deg, z_ids, clip_owned_z))
        if result is not None:
            return result
    except Exception:
        domain.note_fallback()
    return _ORIGINAL(data, refs_by_universe, universe, to_virtual=to_virtual,
                     z_command_deg=z_command_deg, z_ids=z_ids, clip_owned_z=clip_owned_z)


def install():
    global _ORIGINAL
    from src.engine import xy_reference
    if _ORIGINAL is None:
        _ORIGINAL = xy_reference._apply_xy_reference_rotation
    xy_reference._apply_xy_reference_rotation = native_rotation
