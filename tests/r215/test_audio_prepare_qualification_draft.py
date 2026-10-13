"""Independent cold/late ownership controls; ROOT executes after admission."""
import copy
import ctypes as ct
import importlib
import importlib.util
from pathlib import Path
import sys
import struct
import marshal
from types import FunctionType, SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
ORIGINAL = HERE / 'test_audio_preparation_draft.py'
spec = importlib.util.spec_from_file_location('r215_c06_frozen_business', ORIGINAL)
business = importlib.util.module_from_spec(spec)
spec.loader.exec_module(business)
world = business.world


def components():
    names = ('native_audio_prepare', 'native_audio_prepare_owner', 'native_audio_prepare_scope',
             'native_audio_prepare_types', 'native_audio_prepare_guard', 'native_audio_prepare_dependencies')
    modules = [importlib.import_module('qascade_ce.' + name) for name in names]
    return SimpleNamespace(**dict(zip(('api', 'owner', 'scope', 'types', 'guard', 'dependencies'), modules)))


def clone(function):
    result = FunctionType(function.__code__, dict(function.__globals__), function.__name__,
                          function.__defaults__, function.__closure__)
    result.__kwdefaults__ = function.__kwdefaults__
    return result


def cold(monkeypatch, parts):
    retained = parts.owner._OWNER
    assert retained is not None
    for module, name, wrapper in parts.owner._alias_rows(retained.wrappers):
        monkeypatch.setattr(module, name, wrapper.__wrapped__)
    monkeypatch.setattr(parts.owner, '_OWNER', None)
    monkeypatch.setattr(parts.owner, 'SCOPE_TOKEN', None)
    return retained.binding.engine


def unchanged_original_then_zero_c(world, parts):
    raw = [[.20005, .70005, .1005], [0., -0.], [1., 1.]]
    expected = business.observed(lambda: world.frozen['audio_mod_envelope']['normalize_points'](raw))
    before = parts.api.counters()
    with business.native_returns('POINTS') as calls:
        actual = business.observed(lambda: world.actual['audio_mod_envelope'].normalize_points(raw))
    assert actual == expected
    assert calls == []
    after = parts.api.counters()
    for name in ('calls', 'successes', 'errors', 'fallbacks', 'pending', 'point_rows'):
        assert after[name] == before[name], name
    assert after['foreign'] == before['foreign'] + 1


@pytest.mark.parametrize('name', ('Step', 'Request', 'Report'))
@pytest.mark.parametrize('mutation', ('fields_in_place', 'constructor', 'not_a_class'))
def test_late_ctypes_layout_and_constructor_are_vetoed_before_any_c(world, monkeypatch, name, mutation):
    parts = components()
    kind = getattr(parts.types, name)
    if mutation == 'fields_in_place':
        original = list(kind._fields_)
        kind._fields_[0] = ('foreign', ct.c_double)
        try:
            unchanged_original_then_zero_c(world, parts)
        finally:
            kind._fields_[:] = original
        return
    elif mutation == 'constructor':
        monkeypatch.setattr(kind, '__init__', lambda self, *args, **kwargs: None)
    else:
        monkeypatch.setattr(parts.types, name, object())
    unchanged_original_then_zero_c(world, parts)


SYMBOLS = ('ce_audio_prepare_processors_v1', 'ce_audio_prepare_state_v1', 'ce_audio_prepare_points_v1',
           'ce_audio_prepare_round_v1', 'ce_audio_prepare_abi', 'ce_audio_prepare_size',
           'ce_audio_prepare_step_size', 'ce_audio_prepare_request_size')


@pytest.mark.parametrize('symbol', SYMBOLS)
@pytest.mark.parametrize('initial', (False, True))
def test_every_semantic_ce_map_entry_has_cold_and_late_veto(world, monkeypatch, symbol, initial):
    parts = components()
    engine = cold(monkeypatch, parts) if initial else parts.owner._OWNER.binding.engine
    mapping = importlib.import_module('qascade_ce.ce_api_map').CE_API_MAP
    monkeypatch.setitem(mapping, symbol, 'foreign_unreachable_symbol')
    if initial:
        before = parts.api.counters()
        assert parts.owner.install(engine) is False
        assert parts.api.counters()['calls'] == before['calls']
        assert parts.owner._OWNER is None
    else:
        unchanged_original_then_zero_c(world, parts)


SUPPLIERS = (('native_binding', 'bind_native'), ('native_filter_prepare_metadata', 'number'),
             ('native_curve_scalar_guard', '_signature'), ('native_curve_scalar_guard', '_normalize'),
             ('native_programmer_transaction', 'read'), ('native_programmer_transaction', 'write'),
             ('native_programmer_transaction', 'commit'), ('copy', 'deepcopy'))


@pytest.mark.parametrize('selection', SUPPLIERS)
@pytest.mark.parametrize('initial', (False, True))
def test_same_code_foreign_supplier_globals_never_authorize_native(world, monkeypatch, selection, initial):
    parts = components()
    engine = cold(monkeypatch, parts) if initial else parts.owner._OWNER.binding.engine
    module, name = selection
    target = importlib.import_module(module if module == 'copy' else 'qascade_ce.' + module)
    original = getattr(target, name)
    foreign = clone(original)
    assert foreign.__code__ is original.__code__ and foreign.__globals__ is not original.__globals__
    monkeypatch.setattr(target, name, foreign)
    if initial:
        assert parts.owner.install(engine) is False
        assert parts.owner._OWNER is None
    else:
        unchanged_original_then_zero_c(world, parts)


@pytest.mark.parametrize('module', ('scope', 'guard', 'dependencies'))
@pytest.mark.parametrize('initial', (False, True))
def test_function_type_alias_foreign_is_rejected(world, monkeypatch, module, initial):
    parts = components()
    engine = cold(monkeypatch, parts) if initial else parts.owner._OWNER.binding.engine
    monkeypatch.setattr(getattr(parts, module), 'FunctionType', type)
    if initial:
        assert parts.owner.install(engine) is False
    else:
        unchanged_original_then_zero_c(world, parts)


@pytest.mark.parametrize('selection', (('native_audio_prepare_dependencies', '_signature'),
                                      ('native_audio_prepare_guard', '_fixed'),
                                      ('native_audio_prepare_processors', 'prepare'),
                                      ('native_audio_prepare', 'prepare_binding'),
                                      ('native_audio_prepare_guard', 'Guard.binding_current')))
@pytest.mark.parametrize('initial', (False, True))
def test_new_same_code_foreign_globals_are_not_cold_or_current_authority(world, monkeypatch, selection, initial):
    parts = components()
    engine = cold(monkeypatch, parts) if initial else parts.owner._OWNER.binding.engine
    module = importlib.import_module('qascade_ce.' + selection[0])
    key = selection[1]
    target = module if '.' not in key else vars(module)[key.split('.')[0]]
    name = key.split('.')[-1]
    monkeypatch.setattr(target, name, clone(getattr(target, name)))
    if initial:
        assert parts.owner.install(engine) is False
    else:
        unchanged_original_then_zero_c(world, parts)


@pytest.mark.parametrize('initial', (False, True))
def test_copy_dispatch_is_qualified_before_capture(world, monkeypatch, initial):
    parts = components()
    engine = cold(monkeypatch, parts) if initial else parts.owner._OWNER.binding.engine
    calls = []
    def foreign(value, memo):
        calls.append(value)
        return value
    monkeypatch.setitem(copy._deepcopy_dispatch, list, foreign)
    if initial:
        assert parts.owner.install(engine) is False
    else:
        unchanged_original_then_zero_c(world, parts)
    assert calls == []


@pytest.mark.parametrize('selection', (('struct', 'pack'), ('marshal', 'dumps'), ('ctypes', 'POINTER')))
@pytest.mark.parametrize('initial', (False, True))
def test_standard_library_callable_alias_cannot_execute_before_qualification(world, monkeypatch, selection, initial):
    parts = components()
    engine = cold(monkeypatch, parts) if initial else parts.owner._OWNER.binding.engine
    target = importlib.import_module(selection[0])
    calls = []
    def foreign(*args, **kwargs):
        calls.append(args)
        raise AssertionError('Foreign standard library alias was executed')
    before = parts.api.counters()
    monkeypatch.setattr(target, selection[1], foreign)
    if initial:
        assert parts.owner.install(engine) is False
    else:
        assert world.actual['audio_mod_envelope'].normalize_points([[0., 0.], [1., 1.]]) == [[0., 0.], [1., 1.]]
    assert parts.api.counters()['calls'] == before['calls']
    assert calls == []


@pytest.mark.parametrize('mutation', ('global_engine', 'binding_engine', 'dll_slot', 'argtypes', 'restype', 'class_method'))
def test_current_owner_native_abi_and_guard_method_mutations_are_vetoed(world, monkeypatch, mutation):
    parts = components()
    retained = parts.owner._OWNER
    if mutation == 'global_engine':
        monkeypatch.setattr(sys.modules['qascade_ce'], '_engine', object())
    elif mutation == 'binding_engine':
        previous = retained.binding.engine
        object.__setattr__(retained.binding, 'engine', object())
        try:
            unchanged_original_then_zero_c(world, parts)
        finally:
            object.__setattr__(retained.binding, 'engine', previous)
        return
    elif mutation == 'class_method':
        monkeypatch.setattr(parts.guard.Guard, 'binding_current', clone(parts.guard.Guard.binding_current))
    else:
        function = retained.binding.functions['POINTS']
        if mutation == 'dll_slot':
            symbol = parts.dependencies.MAP_EXPECTED['ce_audio_prepare_points_v1']
            monkeypatch.setattr(retained.binding.engine.dll, symbol, object())
        elif mutation == 'argtypes':
            monkeypatch.setattr(function, 'argtypes', [ct.c_void_p])
        else:
            monkeypatch.setattr(function, 'restype', ct.c_double)
    unchanged_original_then_zero_c(world, parts)


def test_foreign_engine_has_no_getattr_or_native_binding_before_rejection(world, monkeypatch):
    parts = components()
    cold(monkeypatch, parts)
    calls = []
    class Foreign:
        def __getattribute__(self, name):
            calls.append(name)
            raise AssertionError('Foreign engine was accessed')
    assert parts.owner.install(Foreign()) is False
    assert calls == []


def test_cold_source_defaults_do_not_self_authorize_from_current_metadata(world, monkeypatch):
    parts = components()
    engine = cold(monkeypatch, parts)
    schema = world.actual['audio_mod_processor_schema']
    monkeypatch.setattr(schema._numeric, '__defaults__', (123.,))
    assert parts.owner.install(engine) is False


def test_cold_prepare_binding_foreign_same_code_is_rejected_before_call(world, monkeypatch):
    parts = components()
    engine = cold(monkeypatch, parts)
    monkeypatch.setattr(parts.api, 'prepare_binding', clone(parts.api.prepare_binding))
    assert parts.owner.install(engine) is False
    assert parts.owner._OWNER is None


def test_plain_default_admission_has_one_actual_c_batch(world):
    parts = components()
    raw = [[0., 0.], [1., 1.]]
    before = parts.api.counters()
    with business.native_returns('POINTS') as calls:
        actual = world.actual['audio_mod_envelope'].normalize_points(raw)
    assert business.encoded(actual) == business.encoded(world.frozen['audio_mod_envelope']['normalize_points'](raw))
    assert len(calls) == 1 and calls[0][1] == 0
    assert parts.api.counters()['successes'] == before['successes'] + 1
