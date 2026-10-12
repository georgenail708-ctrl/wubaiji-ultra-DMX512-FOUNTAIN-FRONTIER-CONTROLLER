"""Prepare Row descriptors without writes; central owner publishes one bundle."""
import functools
import inspect
from contextvars import ContextVar
from dataclasses import dataclass
from types import FunctionType, SimpleNamespace

from .native_programmer_row_routes import dispatch

ROW_NAMES = ("_compute_merged_value", "_get_merged_value", "_range_factor", "_spatial_factor",
             "_apply_channel_range", "_on_master_changed", "_set_range_state", "_on_range_strip_changed",
             "_apply_curve", "_apply_curve_float")
PANEL_NAMES = ("_traj_resolve_default", "_traj_on_play_tick", "_traj_on_scrub",
               "_mark_layer_sparse_from_fixture_values", "_mark_layer_sparse_from_universe_values",
               "_compose_fill_lights_for_writes", "_expand_channel_output", "_precompiled_channel_output",
               "_row_fixture_channel_value")


def _owners():
    from src.ui import programmer_panel as panel, programmer_automation as automation
    from src.core import dmx_channel_values as motor
    return panel, automation, motor


def descriptor_slots():
    panel, automation, motor = _owners()
    return (tuple((panel.ChannelTypeRow, name) for name in ROW_NAMES)
            + tuple((panel.ProgrammerPanel, name) for name in PANEL_NAMES)
            + ((automation, "automation_read_step"), (panel, "automation_read_step"),
               (motor, "motor_8bit_to_16bit_pair"), (panel, "_logical_motor_pair")))


class RowHooks:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.panel, self.automation, self.motor = _owners()
        self.originals, self.signatures, self.wrappers = {}, {}, {}
        self.binding = ContextVar("programmer_row_binding", default=None)
        self.compatibility = ContextVar("programmer_row_compat", default=False)

    def original(self, native, name, args, kwargs, kind="unsupported"):
        native.observer.compatibility(kind)
        token = self.compatibility.set(True)
        try:
            return self.originals[name](*args, **kwargs)
        finally:
            self.compatibility.reset(token)

    def bound_dispatch(self, native, name, args, kwargs):
        try:
            arguments = self.signatures[name].bind(*args, **kwargs)
        except TypeError:
            return self.original(native, name, args, kwargs)
        arguments.apply_defaults()
        context = SimpleNamespace(native=native, statistics=native.observer,
                                  panel=self.panel, automation=self.automation, hooks=self)
        return dispatch(context, name, arguments.arguments,
                        lambda kind="unsupported": self.original(native, name, args, kwargs, kind))

    def canonical_helper(self, owner, name):
        descriptor = inspect.getattr_static(owner, name)
        function = getattr(descriptor, "__func__", descriptor)
        return function is self.wrappers.get(name) or function is self.originals.get(name)

    def dispatch(self, name, args, kwargs):
        if self.compatibility.get():
            return self.originals[name](*args, **kwargs)
        current = self.binding.get()
        if current is not None:
            return self.bound_dispatch(current, name, args, kwargs)
        from .native_programmer_owner import numeric_scope
        with numeric_scope():
            native = self.snapshot()
            if native is None:
                raise RuntimeError("Programmer Row has no published central binding")
            token = self.binding.set(native)
            try:
                return self.bound_dispatch(native, name, args, kwargs)
            finally:
                self.binding.reset(token)

    def descriptor(self, owner, name, change_factory):
        descriptor = owner.__dict__[name]
        function = descriptor.__func__ if isinstance(descriptor, (classmethod, staticmethod)) else descriptor
        if type(function) is not FunctionType or getattr(function, "_ce_programmer_row_hook", False):
            raise RuntimeError("Row descriptor is not its first-party original: " + name)
        self.originals[name], self.signatures[name] = function, inspect.signature(function)

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            return self.dispatch(name, args, kwargs)

        wrapper._ce_programmer_row_hook = True
        self.wrappers[name] = wrapper
        replacement = type(descriptor)(wrapper) if isinstance(descriptor, (classmethod, staticmethod)) else wrapper
        return change_factory(owner, name, replacement), descriptor

    def prepare(self, change_factory):
        changes, descriptors = [], []
        for owner, name in descriptor_slots():
            if owner is self.panel and name in ("automation_read_step", "_logical_motor_pair"):
                source = "automation_read_step" if name == "automation_read_step" else "motor_8bit_to_16bit_pair"
                changes.append(change_factory(owner, name, self.wrappers[source]))
                descriptors.append((owner, name, owner.__dict__[name], self.wrappers[source]))
                continue
            change, original = self.descriptor(owner, name, change_factory)
            changes.append(change)
            descriptors.append((owner, name, original, self.wrappers[name]))
        return tuple(changes), tuple(descriptors)


@dataclass(frozen=True)
class RowAdapter:
    binding: object
    hooks: RowHooks
    snapshot: object
    changes: tuple
    descriptors: tuple


def prepare_adapter(engine, *, snapshot, observer, previous=None, change_factory, function=None):
    from .native_programmer_row_api import RowNative
    if previous is not None and previous.binding.engine is engine:
        return RowAdapter(previous.binding, previous.hooks, previous.snapshot, (), previous.descriptors)
    native = RowNative(engine, observer, function=function)
    if previous is None:
        hooks = RowHooks(snapshot)
        changes, descriptors = hooks.prepare(change_factory)
    else:
        hooks, descriptors, changes = previous.hooks, previous.descriptors, ()
        if hooks.snapshot is not snapshot:
            raise RuntimeError("Row rebind must retain its central snapshot provider")
        for owner, name, _original, wrapper in descriptors:
            descriptor = owner.__dict__[name]
            function = descriptor.__func__ if isinstance(descriptor, (classmethod, staticmethod)) else descriptor
            if function is not wrapper:
                raise RuntimeError("Row descriptor changed outside the central owner: " + name)
    return RowAdapter(native, hooks, snapshot, changes, descriptors)
