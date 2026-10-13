"""One genuine native batch per public normalization and native-return accounting."""
import ctypes as ct
from types import MappingProxyType

from .native_binding import bind_native
from .native_audio_prepare_types import Binding, Packed, Report, Request, Result, Step, D, I, WORK, array, report

PROCESSORS_FUNCTION = None
STATE_FUNCTION = None
POINTS_FUNCTION = None
ROUND_FUNCTION = None
COUNTERS = dict(calls=0, successes=0, errors=0, fallbacks=0, foreign=0, pending=0, semantic_errors=0)
COUNTERS.update(dict.fromkeys(WORK, 0))


class NativeFailure(RuntimeError):
    """A rejected C batch, distinct from a retained original semantic error."""


def counters():
    return COUNTERS.copy()


def available():
    return all(function is not None for function in (PROCESSORS_FUNCTION, STATE_FUNCTION, POINTS_FUNCTION))


def _specifications():
    batch = [ct.POINTER(Request), ct.POINTER(Report)]
    rows = {kind: ('ce_audio_prepare_' + kind.lower() + '_v1', batch) for kind in ('PROCESSORS', 'STATE', 'POINTS')}
    rows['ROUND'] = ('ce_audio_prepare_round_v1', [ct.c_int, D, I, D, ct.POINTER(Report)])
    rows.update({name: (symbol, []) for name, symbol in (
        ('ABI', 'ce_audio_prepare_abi'), ('SIZE', 'ce_audio_prepare_size'),
        ('STEP_SIZE', 'ce_audio_prepare_step_size'), ('REQUEST_SIZE', 'ce_audio_prepare_request_size'))})
    return rows


def prepare_binding(engine):
    functions = {name: bind_native(engine.dll, symbol, arguments, ct.c_int)
                 for name, (symbol, arguments) in _specifications().items()}
    if not all(isinstance(function, ct._CFuncPtr) for function in functions.values()):
        raise RuntimeError('Mounted CE is missing audio preparation math')
    sizes = (functions['ABI'](), functions['SIZE'](), functions['STEP_SIZE'](), functions['REQUEST_SIZE']())
    if sizes != (1, ct.sizeof(Report), ct.sizeof(Step), ct.sizeof(Request)):
        raise RuntimeError('Audio preparation ABI mismatch')
    return Binding(engine, MappingProxyType(functions))


def _invoke(function, arguments, result):
    COUNTERS['calls'] += 1
    COUNTERS['pending'] += 1
    try:
        code = function(*arguments, ct.byref(result))
        if code:
            raise NativeFailure(f'CE audio preparation failed: {code}')
    except Exception:
        COUNTERS['errors'] += 1
        raise
    finally:
        COUNTERS['pending'] -= 1
    COUNTERS['successes'] += 1
    for name in WORK:
        COUNTERS[name] += getattr(result, name)


def execute(binding, kind, tape):
    packed = tape.pack()
    result = report()
    _invoke(binding.functions[kind], (ct.byref(packed.request),), result)
    if result.semantic:
        COUNTERS['semantic_errors'] += 1
        raise packed.errors[result.error_selector]
    buffers = packed.buffers
    return Result(buffers['output'], buffers['flags'], buffers['order'], buffers['count'])


def raw_round(binding, values, digits):
    inputs, places = array(ct.c_double, values), array(ct.c_int, digits)
    output, result = array(ct.c_double, (0.,) * len(values)), report()
    _invoke(binding.functions['ROUND'], (len(values), inputs, places, output), result)
    return tuple(output)


def install(engine):
    from .native_audio_prepare_owner import install as install_owner
    return install_owner(engine)
