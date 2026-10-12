"""Prepare scalar bindings and hook metadata without installing or calling C."""
from dataclasses import dataclass
from types import FunctionType

import ctypes as ct

from .native_binding import bind_native
from .native_curve_scalar_types import Report, Request, SortReport
from .native_curve_scalar_guard import dependency, entry_dependency, function_metadata
from .native_programmer_transaction import change


@dataclass(frozen=True)
class ScalarBinding:
    engine: object
    function: object


@dataclass(frozen=True)
class ScalarHooks:
    model: object
    trajectory: object
    originals: dict
    wrappers: dict
    dependencies: dict


@dataclass(frozen=True)
class ScalarAdapter:
    binding: ScalarBinding
    hooks: ScalarHooks
    snapshot: object
    changes: tuple
    descriptors: tuple


def _bind(engine, function=None):
    if function is None:
        function = bind_native(engine.dll, "ce_curve_scalar_eval_v2",
            [ct.POINTER(Request), ct.POINTER(Report), ct.POINTER(SortReport)], ct.c_int)
    if function is None:
        raise RuntimeError("Mounted CE DLL is missing ce_curve_scalar_eval_v2")
    return ScalarBinding(engine, function)


def _descriptors(installer, model, trajectory):
    pairs = ((model, "evaluate", installer._evaluate),
             (trajectory, "phase_at", installer._phase_at),
             (trajectory, "_eval_phase", installer._eval_phase),
             (trajectory, "value_at", installer._value_at))
    return tuple((cls, name, vars(cls)[name], wrapper) for cls, name, wrapper in pairs)


def _dependencies(originals):
    names = {"model": ("evaluate", ("_pt_out_ctrl", "_pt_in_ctrl", "_seg_ctrls",
                                    "_mono_ctrl_x", "_bezier_coord", "_eval_segment")),
             "trajectory": ("phase_at", ("_clamp01",))}
    groups = {}
    for group, (method, helpers) in names.items():
        namespace = originals[method].__globals__
        groups[group] = [dependency(namespace, name) for name in helpers]
        for name in helpers:
            groups[group].extend(function_metadata(namespace.get(name)))
        entries = ("evaluate",) if group == "model" else ("phase_at", "_eval_phase", "value_at")
        for entry in entries:
            groups[group].append(entry_dependency(namespace, entry, originals[entry]))
            groups[group].extend(function_metadata(originals[entry]))
    groups["model"].append((originals["evaluate"].__globals__, "_BEZIER_SUBDIV", 32))
    return groups


def _first_hooks(installer):
    from src.core.models import CurveMapping
    from src.ui.trajectory_curve import TrajectoryCurve
    if (installer._ORIGINALS or installer._WRAPPERS or installer._DEPENDENCIES
            or installer._MODEL is not None or installer._TRAJECTORY is not None):
        raise RuntimeError("Scalar hook metadata has no retained canonical owner")
    descriptors = _descriptors(installer, CurveMapping, TrajectoryCurve)
    originals, wrappers = {}, {}
    for cls, name, original, wrapper in descriptors:
        if type(original) is not FunctionType or original is wrapper:
            raise RuntimeError(f"Scalar original descriptor is not owned: {cls.__name__}.{name}")
        originals[name], wrappers[name] = original, wrapper
    hooks = ScalarHooks(CurveMapping, TrajectoryCurve, originals, wrappers,
                        _dependencies(originals))
    return hooks, descriptors


def _retained_hooks(api, installer, previous):
    hooks = previous.hooks
    fields = (("_MODEL", hooks.model), ("_TRAJECTORY", hooks.trajectory),
              ("_ORIGINALS", hooks.originals), ("_WRAPPERS", hooks.wrappers),
              ("_DEPENDENCIES", hooks.dependencies))
    if any(vars(installer).get(name) is not value for name, value in fields):
        raise RuntimeError("Scalar hook metadata no longer belongs to the canonical owner")
    if (api._ENGINE is not previous.binding.engine
            or api._FN is not previous.binding.function
            or api._BINDING_SNAPSHOT is not previous.snapshot):
        raise RuntimeError("Scalar facade no longer belongs to the canonical owner")
    return hooks, _descriptors(installer, hooks.model, hooks.trajectory)


def _hook_changes(installer, hooks, descriptors, change_factory):
    fields = (("_MODEL", hooks.model), ("_TRAJECTORY", hooks.trajectory),
              ("_ORIGINALS", hooks.originals), ("_WRAPPERS", hooks.wrappers),
              ("_DEPENDENCIES", hooks.dependencies))
    rows = [change_factory(installer, name, value) for name, value in fields]
    for cls, name, before, wrapper in descriptors:
        row = change_factory(cls, name, wrapper)
        if row.before is not before:
            raise RuntimeError("Scalar descriptor changed during hook preparation")
        rows.append(row)
    return rows


def prepare_adapter(engine, *, snapshot, previous=None, change_factory=change, function=None):
    from . import native_curve_scalar as api
    from . import native_curve_scalar_install as installer
    if not callable(snapshot):
        raise TypeError("Scalar snapshot provider must be callable")
    if previous is None:
        hooks, descriptors = _first_hooks(installer)
        provider = snapshot
    else:
        hooks, descriptors = _retained_hooks(api, installer, previous)
        provider = previous.snapshot
        if previous.binding.engine is engine:
            return previous
    binding = _bind(engine, function)
    rows = [] if previous is not None else _hook_changes(
        installer, hooks, descriptors, change_factory)
    rows.extend((change_factory(api, "_FN", binding.function),
                 change_factory(api, "_ENGINE", binding.engine)))
    if previous is None:
        rows.append(change_factory(api, "_BINDING_SNAPSHOT", provider))
    return ScalarAdapter(binding, hooks, provider, tuple(rows), descriptors)

