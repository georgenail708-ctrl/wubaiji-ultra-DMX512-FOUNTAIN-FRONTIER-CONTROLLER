"""Snapshot plain source metadata; no clamp, modulo, interpolation or override."""
import ctypes as ct

from .native_curve_scalar_types import FallbackPoint, ModelPoint, Prepared, Request
from .native_curve_scalar_types import Value, pointer
from .native_trajectory_values import attach_pool, encode

NUMERIC = (bool, int, float)
CONVERTIBLE = (*NUMERIC, str, bytes, type(None))


def _plain_pair(row):
    return type(row) in (list, tuple) and len(row) == 2


def _fallback_safe(points):
    if type(points) not in (list, tuple):
        return False
    return all(_plain_pair(row) and type(row[0]) in NUMERIC
               and type(row[1]) in NUMERIC for row in points)


def plain_fields(owner, fields):
    cls = type(owner)
    if cls.__getattribute__ is not object.__getattribute__:
        return False
    if any(key in cls.__dict__ and type(cls.__dict__[key]) not in CONVERTIBLE
           for key in fields):
        return False
    state = object.__getattribute__(owner, "__dict__")
    return all(key in state for key in fields)


def _point_safe(point):
    from src.core.models import CurvePoint
    fields = ("x", "y", "c1x", "c1y", "c2x", "c2y")
    if type(point) is not CurvePoint or not plain_fields(point, fields):
        return False
    return type(point.x) in NUMERIC and all(type(getattr(point, key)) in CONVERTIBLE
                                          for key in fields[1:])


def _model_safe(model):
    from src.core.models import CurveMapping
    if type(model) is not CurveMapping or not plain_fields(model, ("points",)):
        return False
    if type(model.points) not in (list, tuple):
        return False
    return all(_point_safe(point) for point in model.points)


def accepts(mode, owner, argument):
    if mode == 0:
        return type(argument) in NUMERIC and _model_safe(owner)
    if type(argument) not in CONVERTIBLE:
        return False
    if mode in (1, 3):
        if not _timing_safe(owner):
            return False
    if mode == 1:
        return True
    return _curve_safe(owner) and _fallback_safe(owner.points)


def _timing_safe(owner):
    return type(owner.duration) in CONVERTIBLE and type(owner.loop) in CONVERTIBLE


def _curve_safe(owner):
    return owner.curve is None or _model_safe(owner.curve)


def _text(value, selector, prepared):
    kind = 1 if type(value) is str else 2
    try:
        return Value(kind=8, selector=selector, size=kind, number=float(value))
    except Exception as error:
        semantic = {OverflowError: 1, TypeError: 6, ValueError: 7}.get(type(error), 9)
        prepared.errors[selector] = (type(error), str(error))
        return Value(kind=8, selector=selector, size=kind, offset=semantic)


def _value(value, pool, prepared):
    selector = prepared.select(value)
    if type(value) in NUMERIC:
        return encode(value, pool, selector)
    if type(value) in (str, bytes):
        return _text(value, selector, prepared)
    if value is None:
        return Value(kind=9, selector=selector)
    return Value(kind=0, selector=selector)


def _model_rows(model, pool, prepared):
    fields = ("x", "y", "c1x", "c1y", "c2x", "c2y")
    rows = []
    for point in model.points:
        values = {key: _value(getattr(point, key), pool, prepared) for key in fields}
        present = sum(1 << index for index, key in enumerate(fields[2:])
                      if getattr(point, key) is not None)
        rows.append(ModelPoint(**values, present=present))
    owner = prepared.hold((ModelPoint * len(rows))(*rows))
    prepared.request.model_points = pointer(owner, ModelPoint)
    prepared.request.model_count = len(rows)


def _fallback_rows(source, pool, prepared):
    # Preserve raw input order. Only actual C fallback consumption may sort.
    rows = source.points or []
    values = [FallbackPoint(_value(row[0], pool, prepared),
                            _value(row[1], pool, prepared)) for row in rows]
    owner = prepared.hold((FallbackPoint * len(values))(*values))
    prepared.request.fallback_points = pointer(owner, FallbackPoint)
    prepared.request.fallback_count = len(values)


def _duration(source, pool, prepared):
    value = source.duration
    prepared.request.duration = _value(value, pool, prepared)
    if value is None or (type(value) in (str, bytes) and not value):
        prepared.request.flags |= 4
    if source.loop:
        prepared.request.flags |= 1


def prepare(mode, source, argument):
    request = Request(abi=1, struct_size=ct.sizeof(Request), mode=mode)
    prepared = Prepared(source, request)
    pool = bytearray()
    if mode in (1, 3):
        _duration(source, pool, prepared)
    request.argument = _value(argument, pool, prepared)
    if mode == 0:
        _model_rows(source, pool, prepared)
    elif mode in (2, 3):
        if source.curve is not None:
            request.flags |= 2
            _model_rows(source.curve, pool, prepared)
        _fallback_rows(source, pool, prepared)
    prepared.hold(attach_pool(request, pool))
    return prepared
