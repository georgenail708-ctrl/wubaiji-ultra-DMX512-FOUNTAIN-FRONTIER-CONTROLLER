"""Independent current public scope controls, ROOT-only and not run.

Expected predicates derive from the existing source qualification contract:
foreign suppliers or new dependencies must not acquire ordinary native scope.
These assertions preserve all old EXPECTED hashes and old47 expectations.
"""
from functools import wraps
import ctypes as ct
from types import FunctionType

import pytest

from r215_support import mounted_world, synthetic_project


def source_world(mounted_world, monkeypatch):
    import src.engine.playback_engine as public
    from src.engine import native_anchor_preparation as helper, transition_anchor_causality as causal
    from qascade_ce import native_source_motion_clock as clock
    engine, _network = mounted_world(synthetic_project())
    monkeypatch.setattr(causal, '_QUALIFIED', None)
    monkeypatch.setattr(helper, '_HELPERS', None)
    return engine, public, helper, causal, clock


@pytest.mark.parametrize('timing', ['before-first-qualification', 'after-qualification'])
@pytest.mark.parametrize('name', ['invoke_scalar', 'available', 'install'])
def test_foreign_clock_supplier_never_acquires_or_retains_default_scope(mounted_world, monkeypatch, timing, name):
    engine, public, helper, causal, clock = source_world(mounted_world, monkeypatch)
    if timing == 'after-qualification':
        assert causal.default_scope(engine) is True
    original = getattr(clock, name)
    @wraps(original)
    def foreign(*args, **kwargs):
        return original(*args, **kwargs)
    monkeypatch.setattr(clock, name, foreign)
    assert causal.default_scope(engine) is False


@pytest.mark.parametrize('timing', ['before-first-qualification', 'after-qualification'])
@pytest.mark.parametrize('name', ['prepare', 'invoke_scalar', 'available', 'install'])
def test_same_code_with_foreign_globals_is_not_reviewed_supplier(mounted_world, monkeypatch, timing, name):
    engine, public, helper, causal, clock = source_world(mounted_world, monkeypatch)
    if timing == 'after-qualification':
        assert causal.default_scope(engine) is True
    owner = helper if name == 'prepare' else clock
    original = getattr(owner, name)
    other = FunctionType(original.__code__, dict(original.__globals__), original.__name__,
                         original.__defaults__, original.__closure__)
    monkeypatch.setattr(owner, name, other)
    assert causal.default_scope(engine) is False


@pytest.mark.parametrize('timing', ['before-first-qualification', 'after-qualification'])
def test_new_public_mask_count_alias_is_strictly_owned(mounted_world, monkeypatch, timing):
    engine, public, helper, causal, clock = source_world(mounted_world, monkeypatch)
    if timing == 'after-qualification':
        assert causal.default_scope(engine) is True
    original = public.mask_count
    @wraps(original)
    def foreign(value):
        return original(value)
    monkeypatch.setattr(public, 'mask_count', foreign)
    assert causal.default_scope(engine) is False


@pytest.mark.parametrize('name', ['float', 'max', 'sys'])
@pytest.mark.parametrize('timing', ['before-first-qualification', 'after-qualification'])
def test_prepare_globals_are_checked_before_and_after_binding(mounted_world, monkeypatch, timing, name):
    engine, public, helper, causal, clock = source_world(mounted_world, monkeypatch)
    if timing == 'after-qualification':
        assert causal.default_scope(engine) is True
    if name == 'sys':
        from types import SimpleNamespace
        replacement = SimpleNamespace(modules=helper.sys.modules)
    elif name == 'float':
        replacement = lambda value: float(value)
    else:
        replacement = lambda *values: max(*values)
    monkeypatch.setattr(helper, name, replacement, raising=False)
    assert causal.default_scope(engine) is False


@pytest.mark.parametrize('field', ['argtypes', 'restype'])
def test_late_ctypes_abi_metadata_change_vetoes_default_scope(mounted_world, monkeypatch, field):
    engine, public, helper, causal, clock = source_world(mounted_world, monkeypatch)
    assert causal.default_scope(engine) is True
    function = clock._FUNCTION
    assert isinstance(function, ct._CFuncPtr)
    # This never invokes the modified C pointer. It tests the public predicate.
    monkeypatch.setattr(function, field, [ct.c_int] if field == 'argtypes' else ct.c_double)
    assert causal.default_scope(engine) is False


@pytest.mark.parametrize('timing', ['before-first-qualification', 'after-qualification'])
def test_both_function_and_installed_marker_replaced_together_are_rejected(mounted_world, monkeypatch, timing):
    engine, public, helper, causal, clock = source_world(mounted_world, monkeypatch)
    if timing == 'after-qualification':
        assert causal.default_scope(engine) is True
    foreign = lambda *args: 0
    monkeypatch.setattr(clock, '_FUNCTION', foreign)
    monkeypatch.setattr(clock, '_INSTALLED_FUNCTION', foreign)
    assert causal.default_scope(engine) is False


@pytest.mark.parametrize('timing', ['before-first-qualification', 'after-qualification'])
@pytest.mark.parametrize('field', ['_INT_INPUT', '_DOUBLE_INPUT', '_SCALAR_OPS'])
def test_new_scalar_layout_or_operation_set_cannot_be_captured_as_reviewed(mounted_world, monkeypatch, timing, field):
    engine, public, helper, causal, clock = source_world(mounted_world, monkeypatch)
    if timing == 'after-qualification':
        assert causal.default_scope(engine) is True
    wrong = {'_INT_INPUT': ct.c_short * 16, '_DOUBLE_INPUT': ct.c_float * 24,
             '_SCALAR_OPS': frozenset((31,))}
    monkeypatch.setattr(clock, field, wrong[field])
    assert causal.default_scope(engine) is False

