"""Fixed originals plus retained owned wrappers; no arbitrary __wrapped__ trust."""
import hashlib
import inspect
import sys
from types import FunctionType

import numpy as np
from numpy.lib import _function_base_impl as numpy_functions
from numpy._core import _ufunc_config as numpy_errors
from numpy._core import _methods as numpy_methods
from numpy._core._multiarray_umath import _ArrayFunctionDispatcher

from .native_curve_scalar_guard import _signature
from .native_filter_prepare_signatures import EXPECTED
from .native_filter_prepare_successor_signatures import (
    ADMITTED_EXPECTED as NEW_EXPECTED, ADMITTED_FUNCTION_NAMES as NEW_FUNCTION_NAMES)
from . import native_filter_prepare_metadata as metadata
from . import native_filter_prepare_scope as scope

DEFAULT_ERRORS = dict(divide='warn', over='warn', under='ignore', invalid='warn')
BUILTINS = ('float', 'int', 'min', 'max', 'abs', 'getattr', 'isinstance', 'type', 'len', 'str')
RUNTIME_GLOBALS = frozenset(('_OWNER', '_UNAVAILABLE', 'PARAMETERS_FUNCTION', 'SPANS_FUNCTION', 'SCOPE_RESOLVER'))
FUNCTIONS = ('_to_float', '_to_int', '_clamp01', '_span_of', '_seq', '_proj_patch_rev',
             '_mask_cache', '_mask_store', 'universe_deg_spans', 'clip_filter_params', 'filters_active')


def code_digest(function):
    return hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest()


def function_metadata(function):
    if type(function) is FunctionType:
        keyword = function.__kwdefaults__
        items = None if keyword is None else tuple(keyword.items())
        return function, function.__code__, function.__defaults__, keyword, items
    return None


def function_current(function, captured):
    if captured is None or type(function) is not FunctionType:
        return False
    current = function_metadata(function)
    if not all(a is b for a, b in zip(current[:4], captured[:4])):
        return False
    return _keyword_current(current[4], captured[4])


def _keyword_current(current, captured):
    if captured is None:
        return current is None
    return (type(current) is tuple and len(current) == len(captured)
            and all(left is right and value is original for (left, value), (right, original) in zip(current, captured)))


def function_current_successor(function, captured):
    if captured is None or type(function) is not FunctionType:
        return False
    if function.__kwdefaults__ is not None:
        return function_current(function, captured)
    return _plain_metadata_current(function, captured)


def _plain_metadata_current(function, captured):
    return (function is captured[0] and function.__code__ is captured[1]
            and function.__defaults__ is captured[2] and captured[3] is None
            and captured[4] is None)


def _new_current_successor(guard):
    if guard.new_functions is None:
        return False
    return all((all(function_current_successor(vars(module).get(name), value)
                    for module, name, value in guard.new_functions),
                all(vars(module).get(name, metadata.MISSING) is value for module, name, value in guard.new_globals)))


def _new_functions(modules):
    result = []
    for module in modules:
        stem = module.__name__.split('.')[-1]
        for name in NEW_FUNCTION_NAMES.get(stem, ()):
            target, local_name, function = scope.locate(module, name)
            if type(function) is not FunctionType or function.__globals__ is not vars(module):
                return None
            if code_digest(function) != NEW_EXPECTED.get(stem + ':' + name) or not scope.own_defaults_original(function, stem + ':' + name):
                return None
            result.append((target, local_name, function_metadata(function)))
    return result


def _new_globals(modules):
    result = []
    for module in modules:
        names = set()
        for name in NEW_FUNCTION_NAMES.get(module.__name__.split('.')[-1], ()):
            function = scope.locate(module, name)[2]
            if type(function) is FunctionType and function.__globals__ is vars(module):
                names.update(function.__code__.co_names)
        result.extend((module, name, vars(module).get(name, metadata.MISSING))
                      for name in names if name not in RUNTIME_GLOBALS)
    return result


class Guard:
    def __init__(self, context, wrappers, originals, new_modules):
        from src.core import models
        public, playback, clip_module = context
        self.public, self.playback, self.clip_module = public, playback, clip_module
        self.models, self.clip_type = models, models.Clip
        self.types = (models.Project, models.FixtureInstance, models.FixtureDefinition)
        self.wrappers, self.originals = wrappers, originals
        self.functions = {name: function_metadata(originals.get(name, vars(public).get(name))) for name in FUNCTIONS}
        self.globals = {name: vars(public).get(name, metadata.MISSING) for name in
                        ('np', '_EPS', '_FALLBACK_DEG_SPAN', 'MOTOR_COARSE_CHANNELS')}
        self.accessors = {kind: kind.__getattribute__ for kind in (self.clip_type, *self.types)}
        self.fields = {kind: {name: inspect.getattr_static(kind, name, metadata.MISSING) for name in names}
                      for kind, names in ((self.clip_type, metadata.FIELDS),
                          (self.types[0], ('fixtures', 'fixture_defs', 'patch_rev')),
                          (self.types[1], ('universe', 'def_id')),
                          (self.types[2], ('id', 'motor_ranges')))}
        self.median, self.geterr = np.median, np.geterr
        self.median_original = self.median.__wrapped__ if type(self.median) is _ArrayFunctionDispatcher else None
        self.numpy_metadata = (function_metadata(self.median_original), function_metadata(np.geterr))
        self.error_getter = numpy_errors._get_extobj_dict
        self.numpy_helpers = {name: function_metadata(function) for name, function in
                              (('numpy_inner_median', numpy_functions._median),
                               ('numpy_ureduce', numpy_functions._ureduce),
                               ('numpy_mean', numpy_methods._mean))}
        self.new_functions = _new_functions(new_modules)
        self.new_globals = _new_globals(new_modules)
        if self.new_functions is not None:
            self._retain_suppliers()
        self.wrapper_metadata = {name: function_metadata(wrapper) for name, wrapper in wrappers.items()}
        self.wrapper_cells = {name: tuple((cell, cell.cell_contents) for cell in wrapper.__closure__ or ())
                              for name, wrapper in wrappers.items()}
        self.safe = self.initial()

    def initial(self):
        return all((self.new_functions is not None, self._constants_original(),
                    self._original_source(), self._numpy_original(), self._builtin_original(),
                    self._initial_aliases()))

    def _retain_suppliers(self):
        for module, names in scope.supplier_rows():
            for name in names:
                function = vars(module)[name]
                self.new_functions.append((module, name, function_metadata(function)))
                self.new_globals.extend((module, key, vars(module).get(key, metadata.MISSING))
                                        for key in function.__code__.co_names)

    def _initial_aliases(self):
        params = vars(self.public).get('clip_filter_params')
        active = vars(self.public).get('filters_active')
        return (any(params is value for value in (self.originals['clip_filter_params'], self.wrappers['clip_filter_params']))
                and any(active is value for value in (self.originals['filters_active'], self.wrappers['filters_active']))
                and self.playback.clip_filter_params is params and self.clip_module.filters_active is active)

    def _builtin_original(self):
        return all(name not in vars(self.public) for name in BUILTINS)

    def _constants_original(self):
        axes = self.globals['MOTOR_COARSE_CHANNELS']
        if type(axes) is not frozenset or not all(type(value) is str for value in axes):
            return False
        if type(self.globals['_EPS']) is not float or type(self.globals['_FALLBACK_DEG_SPAN']) is not float:
            return False
        return all((self.globals['np'] is np,
                    type(self.globals['_EPS']) is float, self.globals['_EPS'] == 1e-6,
                    type(self.globals['_FALLBACK_DEG_SPAN']) is float,
                    self.globals['_FALLBACK_DEG_SPAN'] == 360.0,
                    axes == frozenset(('motor_x', 'motor_y', 'motor_z'))))

    def _original_source(self):
        for name, row in self.functions.items():
            if row is None or row[0].__globals__ is not vars(self.public):
                return False
            if code_digest(row[0]) != EXPECTED.get(name):
                return False
            if not scope.defaults_original(row[0], (1.0,) if name == '_clamp01' else None):
                return False
        return True

    def _numpy_original(self):
        functions = dict(numpy_median=self.numpy_metadata[0], numpy_geterr=self.numpy_metadata[1], **self.numpy_helpers)
        if any(row is None for row in functions.values()):
            return False
        return all((self.median is numpy_functions.median, self.geterr is numpy_errors.geterr,
                    all(code_digest(row[0]) == EXPECTED.get(name) for name, row in functions.items()),
                    self._numpy_defaults(functions)))

    def _numpy_defaults(self, functions):
        defaults = dict(numpy_median=(None, None, False, False), numpy_geterr=None,
                        numpy_inner_median=(None, None, False), numpy_ureduce=(False,),
                        numpy_mean=(None, None, None, False))
        return all(scope.defaults_original(row[0], defaults[name], dict(where=True) if name == 'numpy_mean' else None)
                   for name, row in functions.items())

    def attach(self, owner):
        self.owner = owner
        self.binding_scope = scope.BindingScope(owner)

    def _aliases_current(self):
        return (self.playback.clip_filter_params is self.wrappers['clip_filter_params']
                and self.clip_module.filters_active is self.wrappers['filters_active'])

    def _originals_current(self):
        if not self._builtin_original():
            return False
        for name, captured in self.functions.items():
            function = self.originals.get(name, vars(self.public).get(name))
            if not function_current(function, captured):
                return False
        return all(vars(self.public).get(name) is value for name, value in self.globals.items())

    def _wrappers_current(self):
        for name, wrapper in self.wrappers.items():
            if vars(self.public).get(name) is not wrapper:
                return False
            if vars(wrapper).get('__wrapped__') is not self.originals[name]:
                return False
            if not function_current(wrapper, self.wrapper_metadata[name]):
                return False
            if any(cell.cell_contents is not value for cell, value in self.wrapper_cells[name]):
                return False
        return True

    def current(self):
        return all((self.safe, self._aliases_current(), self._originals_current(),
                    self._wrappers_current(), self._models_current(), _new_current_successor(self),
                    self.binding_scope.current(self.owner)))

    def current_original(self):
        return all((self.safe, self._aliases_current(), self._originals_current(),
                    self._wrappers_current(), self._models_current(), self._new_current(),
                    self.binding_scope.current(self.owner)))

    def _models_current(self):
        current = (self.models.Clip, self.models.Project, self.models.FixtureInstance, self.models.FixtureDefinition)
        if not all(left is right for left, right in zip((self.clip_type, *self.types), current)):
            return False
        if any(kind.__getattribute__ is not value for kind, value in self.accessors.items()):
            return False
        return all(inspect.getattr_static(kind, name, metadata.MISSING) is value
                   for kind, fields in self.fields.items() for name, value in fields.items())

    def _new_current(self):
        if self.new_functions is None:
            return False
        return all((all(function_current(vars(module).get(name), value) for module, name, value in self.new_functions),
                    all(vars(module).get(name, metadata.MISSING) is value for module, name, value in self.new_globals)))

    def numpy_current(self):
        if np.median is not self.median or np.geterr is not self.geterr:
            return False
        if not function_current(self.median.__wrapped__, self.numpy_metadata[0]) or not function_current(np.geterr, self.numpy_metadata[1]):
            return False
        if not self._numpy_helpers_current():
            return False
        if numpy_errors._get_extobj_dict is not self.error_getter or np.geterr.__globals__.get('_get_extobj_dict') is not self.error_getter:
            return False
        return np.geterr() == DEFAULT_ERRORS

    def _numpy_helpers_current(self):
        functions = dict(numpy_inner_median=numpy_functions._median,
                         numpy_ureduce=numpy_functions._ureduce, numpy_mean=numpy_methods._mean)
        return all(function_current(function, self.numpy_helpers[name]) for name, function in functions.items())
