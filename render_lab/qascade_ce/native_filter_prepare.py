"""Mounted filter-preparation math, with real return/work and error accounting."""
import ctypes as ct
import warnings

from .native_binding import bind_native
from .native_filter_prepare_types import Binding, D, I, U, Report, array, report
from . import native_filter_prepare_metadata as metadata

PARAMETERS_FUNCTION = None
SPANS_FUNCTION = None
COUNTERS = dict(calls=0, successes=0, errors=0, fallbacks=0, foreign=0, pending=0,
                semantic_errors=0, parameter_rows=0, parameter_values=0,
                span_rows=0, span_values=0, median_values=0, default_axes=0,
                parameter_cache_hits=0, span_cache_hits=0)


def counters():
    return COUNTERS.copy()


def available():
    return PARAMETERS_FUNCTION is not None and SPANS_FUNCTION is not None


def prepare_binding(engine):
    parameters = bind_native(engine.dll, 'ce_filter_prepare_params_v1',
                             [ct.c_int, D, I, D, U, ct.POINTER(Report)], ct.c_int)
    spans = bind_native(engine.dll, 'ce_filter_prepare_spans_v1',
                        [ct.c_int, I, D, D, I, I, I, I, D, U, ct.POINTER(Report)], ct.c_int)
    abi = bind_native(engine.dll, 'ce_filter_prepare_abi', [], ct.c_int)
    size = bind_native(engine.dll, 'ce_filter_prepare_size', [], ct.c_int)
    if not all(isinstance(function, ct._CFuncPtr) for function in (parameters, spans, abi, size)):
        raise RuntimeError('Mounted CE is missing filter preparation math')
    if abi() != 1 or size() != ct.sizeof(Report):
        raise RuntimeError('Filter preparation ABI mismatch')
    return Binding(engine, parameters, spans, abi, size)


def _invoke(function, arguments, result):
    COUNTERS['calls'] += 1
    COUNTERS['pending'] += 1
    try:
        code = function(*arguments, ct.byref(result))
        if code:
            raise RuntimeError(f'CE filter preparation failed: {code}')
    except Exception:
        COUNTERS['errors'] += 1
        raise
    finally:
        COUNTERS['pending'] -= 1
    COUNTERS['successes'] += 1


def _semantic(result, errors):
    if result.semantic:
        COUNTERS['semantic_errors'] += 1
        raise errors[result.semantic_selector]


def parameters(binding, values):
    prepared = metadata.numbers(values)
    output, active, result = array(ct.c_double, (0.0,) * 3), array(ct.c_uint8, (0,)), report()
    _invoke(binding.parameters, (1, prepared.values, prepared.status, output, active), result)
    COUNTERS['parameter_rows'] += result.parameters // 3
    COUNTERS['parameter_values'] += result.parameters
    _semantic(result, prepared.errors)
    return tuple(output), bool(active[0])


def _warnings(order, overflow):
    for axis in order:
        if overflow[axis]:
            warnings.warn('overflow encountered in reduce', RuntimeWarning, stacklevel=3)


def spans(binding, rows, axes):
    axis, low, high, status_low, status_high, errors = metadata.span_arrays(rows)
    metadata_order, order = array(ct.c_int, (0, 1, 2)), array(ct.c_int, (0, 0, 0))
    output, overflow, result = array(ct.c_double, (0.0,) * 3), array(ct.c_uint8, (0, 0, 0)), report()
    _invoke(binding.spans, (len(rows), axis, low, high, status_low, status_high,
                           metadata_order, order, output, overflow), result)
    for key in ('span_inputs', 'span_values', 'median_values', 'default_axes'):
        target = 'span_rows' if key == 'span_inputs' else key
        COUNTERS[target] += getattr(result, key)
    _semantic(result, errors)
    _warnings(order, overflow)
    return {axes[index]: output[index] for index in order}


def install(engine):
    from .native_filter_prepare_owner import install as install_owner
    return install_owner(engine)
