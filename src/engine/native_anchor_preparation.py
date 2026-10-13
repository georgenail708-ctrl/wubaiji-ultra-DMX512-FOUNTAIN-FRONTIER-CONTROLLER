"""Native cross-track scalar preparation with original unmounted semantics."""
import hashlib
import sys
from types import FunctionType

_SOURCE = '01174bf9425da442162ada96aa59fb7358f11b564f1d85ffea67563407b2121d'
_HELPERS = None
_MISSING = object()


def prepare(mode, *values):
    clock = sys.modules.get('qascade_ce.native_source_motion_clock')
    if clock is not None and clock.available():
        return float(clock.invoke_scalar(31, [mode], values)[0])
    if mode == 0:
        return max(0.0, values[0])
    if mode == 1:
        return max(0.0, values[0] - 1e-4)
    if mode == 2:
        return max(0.0, values[0] - values[1] - 1e-4)
    return values[0] * 1e6 + (values[1] + 1.0) * 1e-3


def qualified_source(name, function):
    if name != '_build_cross_track_fade_in_anchor' or _SOURCE is None:
        return False
    from qascade_ce.native_curve_scalar_guard import _signature
    namespace = vars(sys.modules['src.engine.playback_engine'])
    if type(function) is not FunctionType or function.__globals__ is not namespace:
        return False
    digest = hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest()
    if digest != _SOURCE:
        return False
    return bind_reviewed_helpers() and current()


def current():
    from .native_anchor_preparation_scope import current as scope_current
    return scope_current(_HELPERS)


def bind_reviewed_helpers():
    """Capture metadata only after the exact reviewed helper code is installed."""
    global _HELPERS
    from .native_anchor_preparation_scope import capture
    _HELPERS = capture(sys.modules[__name__])
    return _HELPERS is not None


_PREPARE_SOURCE = 'b39c4f2de7d3523eb5fd9ef98abb50c4f1a57adba6a5e125bab418851775e4c9'
