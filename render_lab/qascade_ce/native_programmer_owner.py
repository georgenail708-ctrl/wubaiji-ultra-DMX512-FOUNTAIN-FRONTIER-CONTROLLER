"""One immutable binding publication for Scalar, Library and Programmer rows.

Metadata is prepared before the sole reversible pointer transaction. Existing
numeric dispatch retains one strong bundle snapshot and never holds this lock
across C, callbacks or rendering. Reference: R211 owner-v2, CPython ExitStack.
"""
import ctypes as ct
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock

from .native_binding import bind_native
from .native_programmer_transaction import change, commit, read, write, RollbackFailure

_LOCK = RLock()
_BUNDLE = None
_UNAVAILABLE = False
_TRACE = []
_ACTIVE_BUNDLE = ContextVar("qascade_programmer_bundle", default=None)


@dataclass(frozen=True)
class Bundle:
    engine: object
    scalar: object
    library: object
    row: object
    adapters: tuple


@dataclass(frozen=True)
class InstallPlan:
    engine: object
    changes: tuple
    bundle: object


def binding_snapshot():
    current = _ACTIVE_BUNDLE.get()
    if current is not None:
        return current
    with _LOCK:
        if _UNAVAILABLE:
            raise RuntimeError("Programmer binding is unavailable after failed metadata rollback")
        return _BUNDLE


@contextmanager
def numeric_scope():
    """One public numerical entry and its callbacks share one retained bundle."""
    if _ACTIVE_BUNDLE.get() is not None:
        yield
        return
    token = _ACTIVE_BUNDLE.set(binding_snapshot())
    try:
        yield
    finally:
        _ACTIVE_BUNDLE.reset(token)


def _scalar_snapshot():
    bundle = binding_snapshot()
    return None if bundle is None else bundle.scalar


def _library_snapshot():
    bundle = binding_snapshot()
    return None if bundle is None else bundle.library


def _row_snapshot():
    bundle = binding_snapshot()
    return None if bundle is None else bundle.row


def _barrier(inject, name):
    if inject is not None:
        inject(name)


def _bind(engine, name, types):
    function = bind_native(engine.dll, name, types, ct.c_int)
    if function is None:
        raise RuntimeError("Mounted CE DLL is missing " + name)
    _TRACE.append((engine, name, function))
    return function


def prepare_install(engine, *, inject=None):
    with _LOCK:
        return _prepare_install(engine, inject)


def _prepare_install(engine, inject):
    if engine is None:
        raise RuntimeError("Programmer domain needs the mounted CE engine")
    if _UNAVAILABLE:
        raise RuntimeError("Programmer metadata requires recovery before reinstall")
    previous = _BUNDLE
    if previous is not None and previous.engine is engine:
        return InstallPlan(engine, (), previous)
    from .native_curve_scalar_types import Request as SQ, Report as SR, SortReport
    from .native_programmer_library_types import Request as LQ, Report as LR
    from .native_programmer_row_types import Request as RQ, Report as RR
    from .native_programmer_library_sort_types import Request as DQ, Report as DR
    _barrier(inject, "bind_scalar")
    scalar_fn = _bind(engine, "ce_curve_scalar_eval_v2", [ct.POINTER(SQ), ct.POINTER(SR), ct.POINTER(SortReport)])
    _barrier(inject, "bind_library")
    library_fn = _bind(engine, "ce_programmer_library_eval", [ct.POINTER(LQ), ct.POINTER(LR)])
    sort_fn = _bind(engine, "ce_programmer_library_sort", [ct.POINTER(DQ), ct.POINTER(DR)])
    _barrier(inject, "bind_row")
    row_fn = _bind(engine, "ce_programmer_row_eval", [ct.POINTER(RQ), ct.POINTER(RR)])
    _barrier(inject, "dependency_row")
    from . import native_curve_scalar, native_programmer_row_install
    from .native_programmer_library_install import prepare_adapter as prepare_library
    from .native_programmer_trajectory import _STATISTICS
    from .native_programmer_trajectory_statistics import LibraryObserver
    old = (None, None, None) if previous is None else previous.adapters
    _barrier(inject, "prepare_scalar")
    scalar = native_curve_scalar.prepare_adapter(engine, snapshot=_scalar_snapshot,
              previous=old[0], change_factory=change, function=scalar_fn)
    _barrier(inject, "prepare_library")
    library = prepare_library(engine, snapshot=_library_snapshot, observer=LibraryObserver(_STATISTICS),
              previous=old[1], change_factory=change, functions=(library_fn, sort_fn))
    _barrier(inject, "prepare_row")
    row = native_programmer_row_install.prepare_adapter(engine, snapshot=_row_snapshot,
              observer=_STATISTICS, previous=old[2], change_factory=change, function=row_fn)
    adapters = (scalar, library, row)
    bundle = Bundle(engine, scalar.binding, library.binding, row.binding, adapters)
    changes = tuple(item for adapter in adapters for item in adapter.changes)
    import sys
    changes += (change(sys.modules[__name__], "_BUNDLE", bundle),)
    return InstallPlan(engine, changes, bundle)


def write_metadata(target, name, value):
    write(target, name, value)


def commit_install(plan, *, setter=write_metadata):
    global _UNAVAILABLE
    with _LOCK:
        try:
            commit(plan.changes, setter=setter)
        except RollbackFailure:
            _UNAVAILABLE = True
            raise
    return True


def install(engine):
    with _LOCK:
        return commit_install(_prepare_install(engine, None))


def binding_trace():
    with _LOCK:
        return tuple(_TRACE)


def counters():
    from . import native_curve_scalar, native_programmer_trajectory
    return native_curve_scalar.counters() | native_programmer_trajectory.counters()


def counter_owners():
    from . import native_curve_scalar, native_programmer_trajectory
    return {name: value for module in (native_curve_scalar, native_programmer_trajectory)
            for name, value in vars(module).items() if name.endswith("_CNT") and isinstance(value, list)}


def metadata_snapshot():
    with _LOCK:
        return {(id(target), name): read(target, name) for target, name in _metadata_slots()}


def _metadata_slots():
    import sys
    from . import native_curve_scalar as api, native_curve_scalar_install as installer
    from .native_programmer_library_install import LibraryHooks
    from .native_programmer_row_install import descriptor_slots
    from src.core.models import CurveMapping
    from src.ui.trajectory_curve import TrajectoryCurve, TrajectorySegment
    slots = [(sys.modules[__name__], name) for name in ("_BUNDLE", "_UNAVAILABLE")]
    slots += [(api, name) for name in ("_FN", "_ENGINE", "_BINDING_SNAPSHOT")]
    slots += [(installer, name) for name in ("_MODEL", "_TRAJECTORY", "_ORIGINALS", "_WRAPPERS", "_DEPENDENCIES")]
    slots += [(CurveMapping, "evaluate")]
    slots += [(TrajectoryCurve, name) for name in ("phase_at", "_eval_phase", "value_at",
              *LibraryHooks.curve_names, *LibraryHooks.shared_names)]
    slots += [(TrajectorySegment, name) for name in LibraryHooks.segment_names]
    slots += list(descriptor_slots())
    return slots
