"""Scalar query facade and canonical actual-call/report counter owners."""
import ctypes as ct

from .native_programmer_transaction import change
from .native_curve_scalar_errors import result_value
from .native_curve_scalar_metadata import prepare
from .native_curve_scalar_types import Report, Request, SortReport, SORT_WORK, WORK

_FN = None
_ENGINE = None
_BINDING_SNAPSHOT = None
_PREFIX = "_CURVE_SCALAR_"
_NAMES = ("CALL", "SUCCESS", "RESULT_SUCCESS", "ERROR", "FALLBACK", "UNSUPPORTED", "OVERRIDE",
          *WORK.values(), *SORT_WORK.values())
_COUNTERS = {name: [0] for name in _NAMES}
for _name, _owner in _COUNTERS.items():
    globals()[f"{_PREFIX}{_name}_CNT"] = _owner


def binding_snapshot():
    provider = _BINDING_SNAPSHOT
    return None if provider is None else provider()


def available():
    return binding_snapshot() is not None


def counters():
    return {f"{_PREFIX}{name}_CNT": owner[0] for name, owner in _COUNTERS.items()}


def _record(report, sort_report):
    for field, name in WORK.items():
        _COUNTERS[name][0] += getattr(report, field)
    for field, name in SORT_WORK.items():
        _COUNTERS[name][0] += getattr(sort_report, field)


def evaluate(mode, owner, argument, *, binding=None):
    binding = binding_snapshot() if binding is None else binding
    if binding is None:
        raise RuntimeError("Curve scalar native domain is not installed")
    prepared = prepare(mode, owner, argument)
    report, sort_report = Report(), SortReport()
    _CURVE_SCALAR_CALL_CNT[0] += 1
    rc = binding.function(ct.byref(prepared.request), ct.byref(report), ct.byref(sort_report))
    _record(report, sort_report)
    if rc != 0 or report.status != rc:
        _CURVE_SCALAR_ERROR_CNT[0] += 1
        raise RuntimeError(f"Curve scalar ABI failed: return={rc}, status={report.status}")
    _CURVE_SCALAR_SUCCESS_CNT[0] += 1
    value = result_value(report, prepared)
    _CURVE_SCALAR_RESULT_SUCCESS_CNT[0] += 1
    return value



def prepare_adapter(engine, *, snapshot, previous=None, change_factory=change, function=None):
    from .native_curve_scalar_adapter import prepare_adapter as prepare_install
    return prepare_install(engine, snapshot=snapshot, previous=previous,
                           change_factory=change_factory, function=function)


def install(engine):
    from .native_programmer_owner import install as install_domains
    return install_domains(engine)

