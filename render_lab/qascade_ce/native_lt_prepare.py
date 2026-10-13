"""Actual C07 batch work and one owned public preparation; research candidate."""
import ctypes as ct
from types import MethodType

from .native_binding import bind_native
from .native_lt_prepare_types import Binding, Request, Report, Value, report
from . import native_lt_prepare_metadata as metadata

_OWNER = None
COUNTERS = dict(requests=0, calls=0, threshold_values=0, attack_values=0, release_values=0,
                brightness_values=0, semantic_errors=0, foreign_fallback=0, native_errors=0, pending=0, inactive=0)
SYMBOLS = ('ce_lt_prepare_v1', 'ce_lt_prepare_abi', 'ce_lt_prepare_request_size',
           'ce_lt_prepare_value_size', 'ce_lt_prepare_report_size')


def counters():
    return COUNTERS.copy()


def prepare_binding(engine):
    args = ([ct.POINTER(Request), ct.POINTER(Report)], [], [], [], [])
    functions = tuple(bind_native(engine.dll, name, signature, ct.c_int)
                      for name, signature in zip(SYMBOLS, args))
    if not all(isinstance(function, ct._CFuncPtr) for function in functions):
        raise RuntimeError('Mounted CE missing LT preparation ABI')
    expected = (1, ct.sizeof(Request), ct.sizeof(Value), ct.sizeof(Report))
    if tuple(function() for function in functions[1:]) != expected:
        raise RuntimeError('LT preparation ABI mismatch')
    return Binding(engine, functions)


def execute(binding, packed):
    result = report()
    COUNTERS['calls'] += 1
    COUNTERS['pending'] += 1
    try:
        code = binding.functions[0](ct.byref(packed.request), ct.byref(result))
        if code != 0:
            raise RuntimeError('LT preparation rejected request: ' + str(code))
    except Exception:
        COUNTERS['native_errors'] += 1
        raise
    finally:
        COUNTERS['pending'] -= 1
    for name in ('threshold_values', 'attack_values', 'release_values', 'brightness_values'):
        COUNTERS[name] += getattr(result, name)
    if result.semantic:
        COUNTERS['semantic_errors'] += 1
        return result, packed.errors[result.selector]
    return result, None


def public_prepare(owner, clip):
    COUNTERS['requests'] += 1
    admitted = False
    if owner is not None and owner is _OWNER:
        admission = owner.admission
        row = owner.admission_metadata
        if type(admission) is MethodType and admission.__self__ is owner.guard:
            function = admission.__func__
            valid = (function is row[0] and function.__code__ is row[1] and function.__defaults__ is row[2]
                     and function.__kwdefaults__ is row[3] and function.__globals__ is row[5])
            if valid:
                for name, value in row[6]:
                    if function.__globals__.get(name, owner.guard.missing) is not value:
                        valid = False
                        break
            if valid:
                try:
                    admitted = admission()
                except Exception:
                    admitted = False
    if not admitted or not metadata.plain_clip(clip, owner.clip_type):
        COUNTERS['foreign_fallback'] += 1
        return None, None
    if not clip.light_tracking_enabled:
        COUNTERS['inactive'] += 1
        return None, None
    packed = metadata.prepare(clip, owner.attack_default, owner.release_default)
    result, error = execute(owner.binding, packed)
    if error is not None:
        return None, error
    return metadata.build_params(owner.params_type, result, metadata.text_color(clip)), None


def install(engine, original, caller, params_type):
    global _OWNER
    from . import native_lt_prepare_scope as scope
    if _OWNER is not None:
        if _OWNER.binding.engine is engine and _OWNER.original is original and _OWNER.caller is caller:
            return _OWNER if _OWNER.admission() else None
        return None
    guard = scope.capture(engine, original, caller, params_type)
    if guard is None:
        COUNTERS['foreign_fallback'] += 1
        return None
    owner = scope.Owner(prepare_binding(engine), original, caller, guard)
    guard.attach(owner)
    _OWNER = owner
    return owner
