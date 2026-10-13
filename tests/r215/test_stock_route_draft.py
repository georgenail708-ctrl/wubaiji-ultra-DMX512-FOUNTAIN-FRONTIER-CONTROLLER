"""ROOT-only stock route and retained owned refusal controls; no old edits."""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BASE = HERE / 'test_owned_proxy_fade_draft.py'
spec = importlib.util.spec_from_file_location('r215_fade_owned_perf_original_business', BASE)
business = importlib.util.module_from_spec(spec)
spec.loader.exec_module(business)
owned = business.owned


@pytest.mark.parametrize('mutation', ('ordinary', 'owned_ctor_code', 'owned_registry_entry'))
def test_actual_stock_nm_stays_native_without_owned_only_scans(owned, monkeypatch, mutation):
    from qascade_ce.native_fade_owned_install import _OWNER
    worker, network, manager, proxy = owned
    worker._nm = network
    worker._clip_fade_geometry_cache.clear()
    events = []
    if mutation == 'owned_ctor_code':
        constructor = _OWNER.wrappers[1]
        code = constructor.__code__
        changed = list(code.co_consts)
        changed[0] = 'irrelevant foreign owned constructor metadata'
        monkeypatch.setattr(constructor, '__code__', code.replace(co_consts=tuple(changed)))
    elif mutation == 'owned_registry_entry':
        def foreign_reference():
            events.append('foreign owned reference')
            return proxy
        monkeypatch.setitem(_OWNER.registry[0], id(proxy), (foreign_reference, network))
    expected = business.oracle(network)._get_fade_out_target(1, proj=manager.project)
    before = business.counts()
    with business.actual_native_returns() as calls:
        actual = worker._get_fade_out_target(1, proj=manager.project)
    business.same(actual, expected)
    assert any(name == 'target' and result == 0 for name, _fn, result in calls)
    assert business.counts()['foreign'] == before['foreign']
    assert events == []
    if mutation != 'ordinary':
        worker._nm = proxy
        business.refused(owned, business.counts())
        assert events == []


@pytest.mark.parametrize('mutation', ('core_identity', 'old_entry_code', 'argtypes', 'restype', 'foreign_owned_helper'))
def test_stock_late_affected_core_entry_and_abi_are_refused_before_foreign_callback(owned, monkeypatch, mutation):
    from qascade_ce import native_fade_owned_scope as scope, native_fade_owned_install as owned_install
    from qascade_ce import native_fade_target_install as core_install, native_fade_target as native
    import ctypes as ct
    worker, network, _manager, _proxy = owned
    worker._nm = network
    events = []
    if mutation == 'core_identity':
        monkeypatch.setattr(core_install, '_GUARD', object())
    elif mutation == 'old_entry_code':
        original = owned_install._OWNER.source.core[8][2][0]
        namespace = {}
        exec('def foreign(owner, universe, proj):\n    raise AssertionError("Foreign old entry executed")\n', namespace)
        monkeypatch.setattr(original, '__code__', namespace['foreign'].__code__)
    elif mutation == 'argtypes':
        monkeypatch.setattr(native.NATIVE['target'], 'argtypes', [ct.c_void_p])
    elif mutation == 'restype':
        monkeypatch.setattr(native.NATIVE['target'], 'restype', ct.c_double)
    else:
        def foreign(*args):
            events.append('foreign owned helper')
            raise AssertionError('Stock route must not execute owned helper')
        monkeypatch.setattr(scope, '_target', foreign)
    business.refused(owned, business.counts())
    assert events == []


def test_stock_current_original_motor_callback_keeps_once_order(owned, monkeypatch):
    from types import MethodType
    import numpy as np
    worker, network, manager, _proxy = owned
    worker._nm = network
    events = []
    mask = np.zeros(512, bool)
    mask[17] = True
    def foreign(self, universe, project):
        events.append((universe, project))
        return mask.copy()
    monkeypatch.setattr(worker, '_build_motor_channel_mask', MethodType(foreign, worker))
    original = business.oracle(network)
    original._build_motor_channel_mask = MethodType(foreign, original)
    expected = original._get_fade_out_target(1, proj=manager.project)
    expected_events = list(events)
    events.clear()
    before = business.counts()
    with business.actual_native_returns() as calls:
        actual = worker._get_fade_out_target(1, proj=manager.project)
    business.same(actual, expected)
    assert events == expected_events and len(events) == 1
    assert calls == []
    assert business.counts()['foreign'] == before['foreign'] + 1


def test_cold_wholemodule_scope_and_idempotent_owner_remain_qualified(owned):
    from qascade_ce import native_fade_owned_install as install, native_fade_owned_scope as scope
    owner = install._OWNER
    assert install.install(owner.source.core[0].engine)
    assert scope.retained(owner.source, owner.registry, owner.wrappers)

