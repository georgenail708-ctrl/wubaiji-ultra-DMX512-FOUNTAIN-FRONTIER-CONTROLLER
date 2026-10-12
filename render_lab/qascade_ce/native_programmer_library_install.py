"""Prepare Library descriptors; the central owner commits its single journal."""
import functools
import inspect
from contextvars import ContextVar
from dataclasses import dataclass
from types import FunctionType

from .native_programmer_library_qualify import qualified
from .native_programmer_library_routes import curve_route, segment_route, shared_route
from .native_programmer_library_types import UnsupportedNative


class LibraryHooks:
    curve_names = ("segment_progress_at", "shape_boundary_windows",
                   "_shape_segment_field", "_evaluate_path", "_evaluate_shape")
    shared_names = ("_sort_by_direction", "_batch_partition", "reached_fixture_ids",
                    "_morph_alpha_for_subgroup")
    segment_names = ("effective_batches", "batch_offsets_seconds", "sequential_span_seconds")

    def __init__(self, snapshot):
        from src.ui.trajectory_curve import TrajectoryCurve, TrajectorySegment
        self.snapshot, self.curve, self.segment = snapshot, TrajectoryCurve, TrajectorySegment
        self.originals, self.signatures, self.wrappers = {}, {}, {}
        self.canonical, self.segment_canonical = {}, {}
        self.compatibility = ContextVar("programmer_library_compat", default=False)
        self.binding = ContextVar("programmer_library_binding", default=None)

    def _original(self, name, args, kwargs):
        token = self.compatibility.set(True)
        try:
            return self.originals[name](*args, **kwargs)
        finally:
            self.compatibility.reset(token)

    def _route(self, native, name, arguments):
        if name in self.curve_names:
            return curve_route(native, name, arguments)
        if name in self.segment_names:
            return segment_route(native, name, arguments)
        return shared_route(native, name, arguments)

    def _dispatch_bound(self, native, name, args, kwargs):
        try:
            bound = self.signatures[name].bind(*args, **kwargs)
        except TypeError:
            return self._original(name, args, kwargs)
        bound.apply_defaults()
        if not qualified(name, bound.arguments, self):
            native.compat("unsupported")
            return self._original(name, args, kwargs)
        try:
            return self._route(native, name, bound.arguments)
        except UnsupportedNative:
            native.compat("unsupported")
            return self._original(name, args, kwargs)

    def dispatch(self, name, args, kwargs):
        if self.compatibility.get():
            return self.originals[name](*args, **kwargs)
        current = self.binding.get()
        if current is not None:
            return self._dispatch_bound(current, name, args, kwargs)
        from .native_programmer_owner import numeric_scope
        with numeric_scope():
            native = self.snapshot()
            if native is None:
                raise RuntimeError("Programmer Library has no published central binding")
            token = self.binding.set(native)
            try:
                return self._dispatch_bound(native, name, args, kwargs)
            finally:
                self.binding.reset(token)

    def prepare_descriptor(self, owner, name, change_factory):
        descriptor = owner.__dict__[name]
        function = descriptor.__func__ if isinstance(descriptor, (classmethod, staticmethod)) else descriptor
        if type(function) is not FunctionType or getattr(function, "_ce_programmer_library_hook", False):
            raise RuntimeError("Library descriptor is not its first-party original: " + name)
        self.originals[name], self.signatures[name] = function, inspect.signature(function)

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            return self.dispatch(name, args, kwargs)

        wrapper._ce_programmer_library_hook = True
        self.wrappers[name] = wrapper
        replacement = type(descriptor)(wrapper) if isinstance(descriptor, (classmethod, staticmethod)) else wrapper
        return change_factory(owner, name, replacement), descriptor

    def prepare_changes(self, change_factory):
        changes, descriptors = [], []
        for owner, names in ((self.curve, (*self.curve_names, *self.shared_names)),
                             (self.segment, self.segment_names)):
            for name in names:
                change, descriptor = self.prepare_descriptor(owner, name, change_factory)
                changes.append(change)
                descriptors.append((owner, name, descriptor))
        self.canonical = {name: self.wrappers[name] for name in self.curve_names}
        self.canonical.update({name: getattr(self.curve, name)
                               for name in ("segment_bounds", "_sync_boundaries")})
        self.segment_canonical = {name: self.wrappers[name] for name in self.segment_names}
        return tuple(changes), tuple(descriptors)


@dataclass(frozen=True)
class LibraryAdapter:
    binding: object
    hooks: LibraryHooks
    snapshot: object
    changes: tuple
    descriptors: tuple


def descriptor_slots():
    """Central fault protocol can snapshot slots before preparing replacements."""
    from src.ui.trajectory_curve import TrajectoryCurve, TrajectorySegment
    return tuple((TrajectoryCurve, name) for name in
                 (*LibraryHooks.curve_names, *LibraryHooks.shared_names)) + tuple(
                     (TrajectorySegment, name) for name in LibraryHooks.segment_names)


def prepare_adapter(engine, *, snapshot, observer, previous=None, change_factory,
                    functions=None):
    """Build all bindings and changes before any descriptor or global write."""
    from .native_programmer_library import LibraryNative
    from .native_programmer_library_sort import SortBinding
    if previous is not None and previous.binding.engine is engine:
        return LibraryAdapter(previous.binding, previous.hooks, previous.snapshot, (), previous.descriptors)
    function, sort_function = functions or (None, None)
    native = LibraryNative(engine, observer, function=function)
    native.sort_binding = SortBinding(native, function=sort_function)
    if previous is None:
        hooks = LibraryHooks(snapshot)
        changes, descriptors = hooks.prepare_changes(change_factory)
    else:
        hooks, descriptors, changes = previous.hooks, previous.descriptors, ()
        if hooks.snapshot is not snapshot:
            raise RuntimeError("Library rebind must retain its central snapshot provider")
        for owner, name, _ in descriptors:
            descriptor = owner.__dict__[name]
            function = descriptor.__func__ if isinstance(descriptor, (classmethod, staticmethod)) else descriptor
            if function is not hooks.wrappers[name]:
                raise RuntimeError("Library descriptor changed outside the central owner: " + name)
    return LibraryAdapter(native, hooks, snapshot, changes, descriptors)
