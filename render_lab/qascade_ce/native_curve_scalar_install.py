"""Four default class hooks; complete original compatibility runs once."""
from contextvars import ContextVar

from . import native_curve_scalar_metadata as metadata

_COMPAT = ContextVar("qascade_curve_scalar_compat", default=False)
_ORIGINALS = {}
_WRAPPERS = {}
_DEPENDENCIES = {}
_MODEL = None
_TRAJECTORY = None


def _method_default(owner, name):
    return name not in owner.__dict__ and getattr(type(owner), name) is _WRAPPERS[name]


def _model_default(owner):
    return (type(owner) is _MODEL and metadata.plain_fields(owner, ("points",))
            and _method_default(owner, "evaluate") and _dependencies_default("model"))


def _curve_default(owner):
    return owner.curve is None or _model_default(owner.curve)


def _dependencies_default(group):
    return all(namespace.get(name) is function
               for namespace, name, function in _DEPENDENCIES[group])


def _default(mode, owner):
    if mode == 0:
        return _model_default(owner)
    if type(owner) is not _TRAJECTORY:
        return False
    if not _dependencies_default("trajectory"):
        return False
    fields = ("duration", "loop") if mode == 1 else ("duration", "loop", "curve", "points")
    if not metadata.plain_fields(owner, fields):
        return False
    names = {1: ("phase_at",), 2: ("_eval_phase",),
             3: ("phase_at", "_eval_phase", "value_at")}[mode]
    if not all(_method_default(owner, name) for name in names):
        return False
    return mode == 1 or _curve_default(owner)


def _original(name, owner, argument):
    token = _COMPAT.set(True)
    try:
        return _ORIGINALS[name](owner, argument)
    finally:
        _COMPAT.reset(token)


def _dispatch(mode, name, owner, argument):
    if _COMPAT.get():
        return _ORIGINALS[name](owner, argument)
    from .native_programmer_owner import numeric_scope
    with numeric_scope():
        return _dispatch_bound(mode, name, owner, argument)


def _dispatch_bound(mode, name, owner, argument):
    from . import native_curve_scalar as api
    binding = api.binding_snapshot()
    if binding is None:
        return _ORIGINALS[name](owner, argument)
    if _default(mode, owner) and metadata.accepts(mode, owner, argument):
        return api.evaluate(mode, owner, argument, binding=binding)
    api._CURVE_SCALAR_UNSUPPORTED_CNT[0] += 1
    return _original(name, owner, argument)


def _evaluate(owner, argument):
    return _dispatch(0, "evaluate", owner, argument)


def _phase_at(owner, argument):
    return _dispatch(1, "phase_at", owner, argument)


def _eval_phase(owner, argument):
    return _dispatch(2, "_eval_phase", owner, argument)


def _value_at(owner, argument):
    return _dispatch(3, "value_at", owner, argument)


def _capture_dependencies():
    from .native_curve_scalar_adapter import _dependencies
    _DEPENDENCIES.update(_dependencies(_ORIGINALS))


def install_class_hooks():
    global _MODEL, _TRAJECTORY
    if _ORIGINALS:
        return True
    from src.core.models import CurveMapping
    from src.ui.trajectory_curve import TrajectoryCurve
    _MODEL, _TRAJECTORY = CurveMapping, TrajectoryCurve
    pairs = ((CurveMapping, "evaluate", _evaluate),
             (TrajectoryCurve, "phase_at", _phase_at),
             (TrajectoryCurve, "_eval_phase", _eval_phase),
             (TrajectoryCurve, "value_at", _value_at))
    for cls, name, wrapper in pairs:
        _ORIGINALS[name] = getattr(cls, name)
        _WRAPPERS[name] = wrapper
        setattr(cls, name, wrapper)
    _capture_dependencies()
    return True

