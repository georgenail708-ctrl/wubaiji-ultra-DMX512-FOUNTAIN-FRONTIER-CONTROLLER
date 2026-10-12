"""CurveMapping metadata only; the frozen model remains the numeric oracle."""
import numpy as np

from src.core.models import CurveMapping, CurvePoint


_MODEL_EVALUATE = CurveMapping.evaluate_array
_FIELDS = ("c1x", "c1y", "c2x", "c2y")


def supported(curve):
    if type(curve) is not CurveMapping:
        return False
    method = getattr(curve, "evaluate_array", None)
    if getattr(method, "__func__", None) is not _MODEL_EVALUATE:
        return False
    return all(type(point) is CurvePoint for point in curve.points)


def payload(curve):
    """Preserve authored order, presence flags and exact float64 field bytes."""
    rows, presence = [], []
    for point in curve.points:
        values = tuple(getattr(point, field) for field in _FIELDS)
        rows.append((point.x, point.y, *(0. if value is None else value for value in values)))
        presence.append(tuple(value is not None for value in values))
    points = np.asarray(rows, np.float64).reshape(-1, 6)
    flags = np.asarray(presence, np.uint8).reshape(-1, 4)
    key = ("ce-curve-lut-v1", str(curve.source), str(curve.expression),
           points.tobytes(), flags.tobytes())
    return key, points, flags


def store(engine, key, result):
    cache = getattr(engine, "_seq_delay_lut_cache", None)
    if cache is None:
        cache = {}
        engine._seq_delay_lut_cache = cache
    if len(cache) > 64:
        cache.clear()
    cache[key] = result


def lookup(engine, key):
    cache = getattr(engine, "_seq_delay_lut_cache", None)
    return None if cache is None else cache.get(key)
