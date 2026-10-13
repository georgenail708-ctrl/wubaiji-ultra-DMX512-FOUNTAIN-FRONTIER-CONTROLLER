"""Independent admission/foreign controls; original 32 business nodes are fixed."""
import ctypes
from contextlib import contextmanager
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from src.core.models import Clip

from test_filter_preparation_draft import world, counts, native_calls, equal_params, project_for, observe


def modules():
    from qascade_ce import native_filter_prepare as api, native_filter_prepare_owner as owner
    from qascade_ce import native_filter_prepare_guard as guard, native_filter_prepare_metadata as metadata
    return api, owner, guard, metadata


def fallback_params(world):
    clip = Clip(id='r215-independent-foreign', motor_limit_scale=.375, motor_speed_limit_dps=81., pump_master_scale=.625)
    expected = world.reference['clip_filter_params'](clip)
    before = counts()
    with native_calls('PARAMETERS') as calls:
        actual = world.public.clip_filter_params(clip)
    equal_params(actual, expected)
    assert calls == []
    assert counts()['foreign'] > before['foreign']


def test_owned_aliases_keep_original_semantic_admission(world):
    from src.engine import playback_engine, clip_frame_evaluation, transition_anchor_causality, timeline_temporal_live_scope
    api, owner, guard, metadata = modules()
    assert owner._OWNER.guard.current()
    assert playback_engine.clip_filter_params is world.public.clip_filter_params
    assert clip_frame_evaluation.filters_active is world.public.filters_active
    assert world.public.clip_filter_params.__wrapped__ is owner._OWNER.originals['clip_filter_params']
    engine = world.owner(project_for([]))
    assert transition_anchor_causality.default_scope(engine)
    assert timeline_temporal_live_scope.default_live_scope(engine)
    clip = Clip(id='r215-qualified-numeric', motor_limit_scale=.42)
    with native_calls('PARAMETERS') as calls:
        actual = playback_engine.clip_filter_params(clip)
    equal_params(actual, world.reference['clip_filter_params'](clip))
    assert calls and calls[0][0] == 0
    assert transition_anchor_causality.default_scope(engine)
    assert timeline_temporal_live_scope.default_live_scope(engine)


@pytest.mark.parametrize('target', ['playback', 'clips'])
def test_foreign_imported_alias_refuses_native_preparation(world, monkeypatch, target):
    from src.engine import playback_engine, clip_frame_evaluation
    module, name = (playback_engine, 'clip_filter_params') if target == 'playback' else (clip_frame_evaluation, 'filters_active')
    monkeypatch.setattr(module, name, lambda clip: None)
    fallback_params(world)


@pytest.mark.parametrize('field', ['motor_limit_scale', 'motor_speed_limit_dps', 'pump_master_scale'])
def test_huge_plain_integer_is_native_selected_semantic_error(world, field):
    clip = Clip(id='r215-independent-overflow')
    setattr(clip, field, 1 << 4096)
    expected = observe(lambda: world.reference['clip_filter_params'](clip))
    before = counts()
    with native_calls('PARAMETERS') as calls:
        actual = observe(lambda: world.public.clip_filter_params(clip))
    assert actual == expected and actual[1][0] is OverflowError
    assert calls and all(code == 0 for code, _ in calls)
    assert counts()['semantic_errors'] == before['semantic_errors'] + 1
    assert counts()['foreign'] == before['foreign'] and counts()['fallbacks'] == before['fallbacks']


@pytest.mark.parametrize('name', ['parameters', 'spans', 'abi', 'size'])
@pytest.mark.parametrize('attribute', ['restype', 'argtypes'])
def test_late_native_signature_mutation_blocks_all_c_calls(world, monkeypatch, name, attribute):
    api, owner, guard, metadata = modules()
    function = getattr(owner._OWNER.binding, name)
    monkeypatch.setattr(function, attribute, ctypes.c_double if attribute == 'restype' else [ctypes.c_double])
    fallback_params(world)


@pytest.mark.parametrize('kind', ['owner', 'binding_engine', 'guard_method', 'same_guard_method_code', 'metadata_code'])
def test_late_owner_or_same_object_source_change_is_foreign(world, monkeypatch, kind):
    api, owner, guard, metadata = modules()
    def unrelated(*args):
        return True
    if kind == 'owner':
        monkeypatch.setattr(owner, '_OWNER', object())
    elif kind == 'binding_engine':
        monkeypatch.setitem(vars(owner._OWNER.binding), 'engine', object())
    elif kind == 'guard_method':
        monkeypatch.setattr(guard.Guard, 'current', unrelated)
    elif kind == 'same_guard_method_code':
        monkeypatch.setattr(guard.Guard.current, '__code__', unrelated.__code__)
    else:
        monkeypatch.setattr(metadata.number, '__code__', unrelated.__code__)
    fallback_params(world)


def test_foreign_resolver_cannot_reauthorize_old_original(world, monkeypatch):
    from src.engine.filter_preparation_scope import original_filter_function
    api, owner, guard, metadata = modules()
    wrapper = world.public.clip_filter_params
    events = []
    def foreign(name, function):
        events.append(name)
        return owner._OWNER.originals[name]
    monkeypatch.setattr(owner, 'original_for', foreign)
    assert original_filter_function('clip_filter_params', wrapper) is wrapper
    assert events == []
    fallback_params(world)


def test_unknown_wrapped_attribute_never_unwraps(world):
    from src.engine.filter_preparation_scope import original_filter_function
    api, owner, guard, metadata = modules()
    def unknown(clip):
        return (0., 0., 0.)
    unknown.__wrapped__ = owner._OWNER.originals['clip_filter_params']
    assert original_filter_function('clip_filter_params', unknown) is unknown


def test_plain_parameter_memo_uses_exact_type_bits_and_clip_identity(world):
    clip = Clip(id='r215-independent-memo', motor_limit_scale=1)
    expected = world.reference['clip_filter_params'](clip)
    with native_calls('PARAMETERS') as first:
        equal_params(world.public.clip_filter_params(clip), expected)
    assert first
    with native_calls('PARAMETERS') as warm:
        equal_params(world.public.clip_filter_params(clip), expected)
    assert warm == []
    clip.motor_limit_scale = 1.0
    with native_calls('PARAMETERS') as type_changed:
        equal_params(world.public.clip_filter_params(clip), world.reference['clip_filter_params'](clip))
    assert type_changed
    clip.motor_limit_scale = .625
    with native_calls('PARAMETERS') as value_changed:
        equal_params(world.public.clip_filter_params(clip), world.reference['clip_filter_params'](clip))
    assert value_changed
    other = Clip(id=clip.id, motor_limit_scale=.625)
    with native_calls('PARAMETERS') as other_owner:
        equal_params(world.public.clip_filter_params(other), world.reference['clip_filter_params'](other))
    assert other_owner


def test_warm_span_keyword_and_foreign_numpy_getter_keep_identity_without_callback(world, monkeypatch):
    import numpy as np
    from numpy._core import _ufunc_config
    project = project_for([{'motor_x': (0., 180.)}])
    engine = world.owner(project)
    actual = world.public.universe_deg_spans(engine, 1, proj=project)
    events = []
    def foreign():
        events.append('getter')
        return {}
    monkeypatch.setattr(_ufunc_config, '_get_extobj_dict', foreign)
    with native_calls('SPANS') as calls:
        assert world.public.universe_deg_spans(engine, 1, proj=project) is actual
    assert calls == [] and events == []


def first_install_snapshot(world, monkeypatch):
    from src.engine import playback_engine, clip_frame_evaluation
    api, owner, guard, metadata = modules()
    retained = owner._OWNER
    for name, original in retained.originals.items():
        monkeypatch.setattr(world.public, name, original)
    monkeypatch.setattr(playback_engine, 'clip_filter_params', retained.originals['clip_filter_params'])
    monkeypatch.setattr(clip_frame_evaluation, 'filters_active', retained.originals['filters_active'])
    monkeypatch.setattr(owner, '_OWNER', None)
    monkeypatch.setattr(owner, 'SCOPE_RESOLVER', None)
    monkeypatch.setattr(api, 'PARAMETERS_FUNCTION', None)
    monkeypatch.setattr(api, 'SPANS_FUNCTION', None)
    return retained


@contextmanager
def observed_native(functions):
    events, previous = [], []
    def observe(result, function, args):
        events.append((function, result))
        return result
    for function in functions:
        previous.append(getattr(function, 'errcheck', None))
        function.errcheck = observe
    try:
        yield events
    finally:
        for function, old in zip(functions, previous):
            if old is None:
                del function.errcheck
            else:
                function.errcheck = old


@pytest.mark.parametrize('foreign', ['binding_alias', 'guard_class', 'guard_initial_code', 'original_default', 'new_default'])
def test_foreign_before_first_install_cannot_execute_binding_or_abi(world, monkeypatch, foreign):
    api, owner, guard, metadata = modules()
    retained = first_install_snapshot(world, monkeypatch)
    events = []
    def forbidden(*args):
        events.append('foreign-supplier')
        raise AssertionError('Foreign supplier executed before qualification')
    def changed_initial(*args):
        raise AssertionError('Foreign Guard initial executed before qualification')
    if foreign == 'binding_alias':
        monkeypatch.setattr(api, 'bind_native', forbidden)
    elif foreign == 'guard_class':
        monkeypatch.setattr(owner, 'Guard', forbidden)
    elif foreign == 'guard_initial_code':
        monkeypatch.setattr(guard.Guard.initial, '__code__', changed_initial.__code__)
    elif foreign == 'original_default':
        monkeypatch.setattr(world.public._clamp01, '__defaults__', (.25,))
    else:
        monkeypatch.setattr(metadata.number, '__defaults__', (.25,))
    with observed_native((retained.binding.abi, retained.binding.size, retained.binding.parameters, retained.binding.spans)) as native:
        assert owner.install(retained.binding.engine) is False
    assert native == []
    assert events == [] and owner._OWNER is None
    assert world.public.clip_filter_params is retained.originals['clip_filter_params']
