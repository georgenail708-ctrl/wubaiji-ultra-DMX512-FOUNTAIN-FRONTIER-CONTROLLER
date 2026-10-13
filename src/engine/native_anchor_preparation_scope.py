"""Fixed cross-track suppliers and current borrowed scalar ABI identities.

This metadata-only qualification never calls a numerical or foreign supplier.
It extends the reviewed C05 route; all historical source stamps stay intact.
"""
import ctypes as ct
import hashlib
import sys
from types import FunctionType

from qascade_ce.native_curve_scalar_guard import _signature

EXPECTED = {
    'prepare': 'b39c4f2de7d3523eb5fd9ef98abb50c4f1a57adba6a5e125bab418851775e4c9',
    'invoke_scalar': 'd085d5aa4d09a4e14373d7aef898c83231f5f11e32a9da5da3a710eb78bbfae5',
    'available': 'c6b648d1e76be629396e5ed6fce79000c18191161ccbf96086ee638af8a85139',
    'install': '6c6fced82e52d56b3a3341d8e6fa9aa4e7c6dee608594e2525365831a9a90dde',
    'mask_count': 'ce757baab12c1c14d9ada420dd4c9189908e2a7c9f63f09a1169deefcb425a7e',
    'bind_native': '4ff447a0708fc9d3f2f7003e30f16910a7f4bed07002e4411cbb3c2f7f639b62',
}
OPS = frozenset((0, 5, 6, 7, 9, 12, 14, 15, 16, 20, 25, 26, 27, 28, 29, 30, 31))
MISSING = object()


def _suppliers(helper, clock, mask, binding):
    return ((helper, 'prepare'), (clock, 'invoke_scalar'), (clock, 'available'),
            (clock, 'install'), (mask, 'mask_count'), (binding, 'bind_native'))


def _original(owner, name):
    function = vars(owner).get(name)
    if type(function) is not FunctionType or function.__globals__ is not vars(owner):
        return False
    if function.__defaults__ is not None or function.__kwdefaults__ is not None:
        return False
    digest = hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest()
    return digest == EXPECTED[name]


def _aliases(helper, clock, mask, binding, public):
    return all((helper.sys is sys, mask.sys is sys, clock.ct is ct,
                public.mask_count is mask.mask_count, clock.bind_native is binding.bind_native,
                not any(name in vars(helper) for name in ('float', 'max')),
                not any(name in vars(clock) for name in ('ValueError', 'RuntimeError'))))


def _layout(clock):
    return all((clock._INT_INPUT is ct.c_int * 16, clock._DOUBLE_INPUT is ct.c_double * 24,
                clock.I is ct.POINTER(ct.c_int), clock.D is ct.POINTER(ct.c_double),
                _operations_original(clock._SCALAR_OPS)))


def _operations_original(value):
    if type(value) is not frozenset:
        return False
    return all(type(item) is int for item in value) and value == OPS


def _arguments_current(function, arguments, result):
    actual = function.argtypes
    return (type(actual) in (list, tuple) and len(actual) == len(arguments)
            and all(left is right for left, right in zip(actual, arguments))
            and function.restype is result)


def _abi(clock):
    function = clock._FUNCTION
    arguments = (ct.c_int, clock.I, clock.D, clock.D, clock.D, clock.D)
    return (isinstance(function, ct._CFuncPtr) and function is clock._INSTALLED_FUNCTION
            and _arguments_current(function, arguments, ct.c_int))


def _binding_rows(clock, binding):
    package = sys.modules.get('qascade_ce')
    engine = None if package is None else vars(package).get('_engine')
    dll = None if engine is None else vars(engine).get('dll')
    if type(binding.CE_API_MAP) is not dict:
        return None
    name = binding.CE_API_MAP.get('ce_source_motion_clock', 'ce_source_motion_clock')
    if type(name) is not str or type(dll) is not ct.CDLL or vars(dll).get(name) is not clock._FUNCTION:
        return None
    return ((package, '_engine', engine), (engine, 'dll', dll),
            (dll, name, clock._FUNCTION), (clock, '_FUNCTION', clock._FUNCTION),
            (clock, '_INSTALLED_FUNCTION', clock._INSTALLED_FUNCTION))


def _function_rows(suppliers):
    return tuple((function, name, getattr(function, name))
                 for owner, key in suppliers for function in (vars(owner)[key],)
                 for name in ('__code__', '__globals__', '__defaults__', '__kwdefaults__'))


def _alias_rows(helper, clock, mask, binding, public):
    fields = ((helper, ('sys', 'float', 'max')), (clock, ('ct', 'bind_native', 'I', 'D',
               '_INT_INPUT', '_DOUBLE_INPUT', '_SCALAR_OPS', 'ValueError', 'RuntimeError')),
              (mask, ('sys',)), (binding, ('CE_API_MAP',)), (public, ('mask_count',)))
    return tuple((owner, key, getattr(owner, key, MISSING)) for owner, names in fields for key in names)


def _inputs_safe(suppliers, helper, context):
    clock, mask, binding, public = context
    return all((all(_original(owner, name) for owner, name in suppliers),
                _aliases(helper, clock, mask, binding, public), _layout(clock), _abi(clock)))


def capture(helper):
    clock = sys.modules.get('qascade_ce.native_source_motion_clock')
    mask = sys.modules.get('src.core.native_mask_math')
    binding = sys.modules.get('qascade_ce.native_binding')
    public = sys.modules.get('src.engine.playback_engine')
    if any(value is None for value in (clock, mask, binding, public)):
        return None
    suppliers = _suppliers(helper, clock, mask, binding)
    if not _inputs_safe(suppliers, helper, (clock, mask, binding, public)):
        return None
    native_rows = _binding_rows(clock, binding)
    if native_rows is None:
        return None
    module_rows = tuple((owner, key, vars(owner)[key]) for owner, key in suppliers)
    rows = _function_rows(suppliers) + module_rows + _alias_rows(helper, clock, mask, binding, public)
    symbol = binding.CE_API_MAP.get('ce_source_motion_clock', MISSING)
    return (rows + native_rows, clock._FUNCTION, tuple(clock._FUNCTION.argtypes),
            clock._FUNCTION.restype, (binding.CE_API_MAP, symbol))


def current(snapshot):
    if snapshot is None:
        return False
    rows, function, arguments, result, (mapping, symbol) = snapshot
    return (all(getattr(owner, key, MISSING) is value for owner, key, value in rows)
            and mapping.get('ce_source_motion_clock', MISSING) is symbol
            and _arguments_current(function, arguments, result))
