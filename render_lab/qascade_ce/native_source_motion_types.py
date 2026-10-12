"""Source-domain ABI and strong buffer references for synchronous CE calls."""
import ctypes as ct

import numpy as np

from .native_binding import borrowed_pointer

F = ct.POINTER(ct.c_float)
D = ct.POINTER(ct.c_double)
U = ct.POINTER(ct.c_uint8)
I = ct.POINTER(ct.c_int)
DIRECTION_POINTERS = {name: (np.float32, F) for name in
    ("gi_in", "gp_in", "gs_in", "gi_out", "gp_out", "gs_out", "ease_lut")}
DIRECTION_POINTERS.update(span_idx=(np.int32, I), motor=(np.uint8, U), pauses=(np.float64, D))
DIRECTION_INTS = ("K", "batch_count", "mode", "te", "curve_in", "curve_out",
                  "strategy_in", "strategy_out", "is_in", "is_out",
                  "motor_no_delay", "completed_enabled", "ratio_nondefault")
DIRECTION_FLOATS = ("local_t", "duration", "fade_in", "fade_out", "delay_in",
                    "delay_out", "single", "step", "scale_in", "scale_out")
REMAP_INTS = ("batch_count", "seed", "cyclic", "motor_no_delay", "parallel",
              "phase_enabled", "force_enabled")
REMAP_FLOATS = ("local_t", "source_t", "duration", "single", "step", "phase_amount",
                "delay_in", "fade_in", "accel", "cruise", "decel")
CURVES = {"linear": 0, "easein": 1, "easeout": 2, "easeinout": 3, "bezier": 4}
FAMILIES = ("Quad", "Cubic", "Quart", "Quint", "Sine", "Expo", "Circ",
            "Elastic", "Back", "Bounce")
CURVES.update({f"{edge}{family}": 100 + fi*4 + vi
               for fi, family in enumerate(FAMILIES)
               for vi, edge in enumerate(("In", "Out", "InOut", "OutIn"))})
STRATEGIES = {name: i for i, name in enumerate(("standard_fade", "immediate",
    "decelerate", "hold_decay", "step_down", "smooth_ease", "delay_then_cut"))}


class DirectionSpec(ct.Structure):
    _fields_ = [(name, pointer) for name, (_, pointer) in DIRECTION_POINTERS.items()] + [
        (name, ct.c_int) for name in DIRECTION_INTS] + [
        (name, ct.c_double) for name in DIRECTION_FLOATS]


class SourceTable(ct.Structure):
    _fields_ = [("times", D), ("frames", U), ("count", ct.c_int)]


class CopyRow(ct.Structure):
    _fields_ = [(name, ct.c_int) for name in
               ("target", "source", "count", "slot", "group", "phase")] + [
        ("relative", ct.c_double)]


class RemapSpec(ct.Structure):
    _fields_ = [("pauses", D)] + [(name, ct.c_int) for name in REMAP_INTS] + [
        (name, ct.c_double) for name in REMAP_FLOATS]


def pointer(value, dtype, kind, refs):
    if value is None:
        return kind()
    array = np.ascontiguousarray(value, dtype=dtype)
    refs.append(array)
    return borrowed_pointer(array, kind)


def direction_spec(request, refs):
    item = DirectionSpec()
    for name, (dtype, kind) in DIRECTION_POINTERS.items():
        value = request.get(name)
        if value is not None:
            required = request.get("K", 0)
            required = 512 if name in ("span_idx", "motor") else required
            required = 256 if name == "ease_lut" else required
            required = request.get("batch_count", 1)-1 if name == "pauses" else required
            if np.asarray(value).size < required:
                raise ValueError(f"short direction buffer: {name}")
        setattr(item, name, pointer(value, dtype, kind, refs))
    for name in DIRECTION_INTS + DIRECTION_FLOATS:
        setattr(item, name, request.get(name, 0))
    return item


def source_table(request, refs):
    times, frames = request["times"], request["frames"]
    count = len(times)
    if not 0 <= count <= 2**31-1:
        raise ValueError("source count outside native int range")
    if np.asarray(frames).shape != (count, 512):
        raise ValueError("source frames must have shape (count, 512)")
    return SourceTable(pointer(times, np.float64, D, refs),
                       pointer(frames, np.uint8, U, refs), count)


def remap_spec(request, refs):
    item = RemapSpec()
    pauses = request.get("pauses")
    if pauses is not None and len(pauses) < request["batch_count"]-1:
        raise ValueError("short sequential pauses")
    item.pauses = pointer(pauses, np.float64, D, refs)
    for name in REMAP_INTS + REMAP_FLOATS:
        setattr(item, name, request.get(name, 0))
    return item
