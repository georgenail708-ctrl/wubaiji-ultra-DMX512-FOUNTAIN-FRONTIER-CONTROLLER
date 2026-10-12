"""Sequential cold preparation: metadata parsing and existing cache containers.

The shared source clock performs gate arithmetic, pause normalization, offsets
and complete wing/legacy budgets. No numerical result is prepared in Python.
"""
import json

import numpy as np

from . import native_source_motion_clock as clock

_FUNCTION = None
_SOURCE_PRELUDE_CALL_CNT = [0]
_SOURCE_PRELUDE_SUCCESS_CNT = [0]
_SOURCE_PRELUDE_ERROR_CNT = [0]
_SOURCE_PRELUDE_CACHE_CNT = [0]


def install(engine):
    global _FUNCTION
    if not clock.install(engine):
        return False
    _FUNCTION = clock._FUNCTION
    return True


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_SOURCE_PRELUDE_") and name.endswith("_CNT")}


def _invoke(op, integers, numbers, values=()):
    _SOURCE_PRELUDE_CALL_CNT[0] += 1
    try:
        result = clock.invoke(op, integers, numbers, values)
    except Exception:
        _SOURCE_PRELUDE_ERROR_CNT[0] += 1
        raise
    _SOURCE_PRELUDE_SUCCESS_CNT[0] += 1
    return result


def _batch_inputs(clip, groups):
    if groups is None:
        groups = getattr(clip, "fade_groups", 1)
    groups = int(groups or 1)
    try:
        declared = int(getattr(clip, "fade_group_sequence_batches", 0) or 0)
    except Exception:
        declared = 0
    return [groups, declared]


def _fade(clip):
    try:
        return float(getattr(clip, "fade_in", 0.) or 0.)
    except (TypeError, ValueError):
        return 0.


def _timing_inputs(clip):
    direction = str(getattr(clip, "fade_in_direction", "none") or "none").strip().lower()
    mode = str(getattr(clip, "fade_group_sequence_mode", "parallel") or "parallel").strip().lower()
    integers = _batch_inputs(clip, None) + [int(direction not in ("", "none")),
                                         int(mode == "sequential"), 0, 0, 1]
    numbers = [0., float(getattr(clip, "fade_group_pause", 0.) or 0.),
        float(getattr(clip, "fade_group_single_duration", 0.) or 0.),
        float(getattr(clip, "seq_delay_in", 0.) or 0.), _fade(clip)]
    return integers, numbers


def _pause_list(value):
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            value = json.loads(value)
        except Exception:
            value = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    if value is None:
        return []
    return value if isinstance(value, (list, tuple)) else [value]


def _pairs(value, count):
    if not count:
        return np.zeros(0, np.float64), 0
    raw = _pause_list(value)
    parsed = []
    for item in raw[:count]:
        try:
            parsed.append((1., float(item or 0.)))
        except Exception:
            parsed.append((0., 0.))
    values = np.zeros((count, 2), np.float64)
    if parsed:
        values[:len(parsed)] = parsed
    return values.reshape(-1), len(parsed)


def _counts(clip, groups=None):
    integers = _batch_inputs(clip, groups) + [0, 0, 0, 0, 1]
    result, _ = _invoke(24, integers, [])
    return int(result[5]), int(result[6])


def batch_count(engine, request):
    args = request["args"]
    return _counts(args["clip"], args.get("groups"))[1]


def pauses(engine, request):
    args = request["args"]
    clip, fallback = args["clip"], args.get("fallback")
    if fallback is None:
        fallback = getattr(clip, "fade_group_pause", 0.)
    _, batches = _counts(clip, args.get("groups"))
    values, raw = _pairs(getattr(clip, "fade_group_pauses", []), batches - 1)
    result, output = _invoke(22, [batches - 1, raw, batches - 1],
                             [float(fallback or 0.)], values)
    return output[:int(result[0])].tolist()


def offsets(engine, request):
    args = request["args"]
    raw = [float(value or 0.) for value in (args["pauses"] or [])]
    declared = args.get("batch_count")
    count = len(raw) + 1 if declared is None else int(declared or 1)
    # Capacity bounds are storage metadata; the C operation derives all results.
    capacity = max(1, count, len(raw))
    values = np.zeros(capacity, np.float64)
    values[:len(raw)] = raw
    result, output = _invoke(23, [count, int(declared is not None), len(raw), capacity],
                             [float(args["step"] or 0.)], values)
    return output[:int(result[0])].tolist()


def _log_compression(clip, result, numbers):
    if not result[2]:
        return
    from src.engine.playback_engine import _log_seq_clamp
    _log_seq_clamp(clip, float(result[4]), numbers[0], float(result[3]),
        dict(seq_in_c=float(result[10]), transitional=float(result[11]),
             batches=int(result[6])))


def timing_detail(engine, request):
    clip = request["args"]["clip"]
    key = engine._clip_cache_id(clip)
    stored = engine._clip_seq_timing_cache.get(key)
    if stored is not None and len(stored) >= 6:
        _SOURCE_PRELUDE_CACHE_CNT[0] += 1
        en, groups, saved, single, batches, step = stored[:6]
        return en, groups, list(saved), single, batches, step
    return _cold_timing(engine, clip, key)


def _cold_timing(engine, clip, key):
    integers, numbers = _timing_inputs(clip)
    gate, _ = _invoke(24, integers, numbers)
    count = int(gate[8])
    values, raw = _pairs(getattr(clip, "fade_group_pauses", []), count)
    integers[4:7] = [raw, count, 0]
    if gate[7]:
        numbers[0] = float(getattr(clip, "duration", 0.) or 0.)
    result, output = _invoke(24, integers, numbers, values)
    _log_compression(clip, result, numbers)
    step = None if result[1] < 0. else float(result[1])
    normalized = output[:count].tolist()
    value = (bool(result[7]), int(result[5]), normalized, float(result[0]), int(result[6]), step)
    engine._clip_seq_timing_cache[key] = (*value[:2], list(normalized), *value[3:])
    return value
