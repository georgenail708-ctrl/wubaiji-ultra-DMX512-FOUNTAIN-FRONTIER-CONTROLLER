"""Independent owned constructors and unchanged C03 full-output oracle; ROOT only."""
from contextlib import contextmanager
import ctypes as ct
import importlib.util
from pathlib import Path
import sys
from types import FunctionType

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = next(path for path in HERE.parents if (path / 'src/engine/playback_engine.py').is_file())
REFERENCE = HERE / 'original_reference.py'
spec = importlib.util.spec_from_file_location('r215_c03_original_owned_proxy_reference', REFERENCE)
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


@pytest.fixture
def owned(qapp, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'render_lab'))
    monkeypatch.setenv('QASCADE_CT_ENGINE', '1')
    from qascade_ce._mount_ce import _mount_ce
    mounted = _mount_ce()
    assert mounted.active and mounted.installed, mounted.to_dict()
    from PyQt6 import sip
    from src.core.project_manager import ProjectManager
    from src.core.models import FixtureDefinition, FixtureInstance
    from src.network.network_manager import NetworkManager
    from src.engine.playback_engine import PlaybackEngine
    from src.engine.timeline_bake_inputs import snapshot_inputs
    from src.engine.timeline_bake_context import _owned_engine
    from qascade_ce import _K2FrameCacheNetworkProxy
    live_manager, worker_manager = ProjectManager(), ProjectManager()
    live_network = NetworkManager(live_manager)
    live = PlaybackEngine(live_network, live_manager, parent=live_manager)
    project = live_manager.project
    project.fixture_defs = [FixtureDefinition(id='owned-motor', channels=['motor_x'])]
    project.fixtures = [FixtureInstance(id='owned-f', def_id='owned-motor', universe=1, start_channel=5)]
    live_network.set_fixture_defaults_from_project(project)
    live_network.set_channel_defaults({1: {0: 17, 3: 233, 4: 0, 511: 0}})
    worker = network = None
    try:
        inputs = snapshot_inputs(live, project, worker_manager, {})
        worker, network = _owned_engine(inputs, worker_manager)
        assert type(worker) is PlaybackEngine and type(network) is NetworkManager
        assert worker._timeline_bake_owned is True and worker._pm.project is inputs['project']
        assert worker._pm.project is not project
        proxy = _K2FrameCacheNetworkProxy(network, build_mode=True)
        assert type(proxy) is _K2FrameCacheNetworkProxy and vars(proxy)['_target'] is network
        assert '_channel_defaults' not in vars(proxy)
        assert proxy._channel_defaults is network._channel_defaults
        worker._nm = proxy
        yield worker, network, worker_manager, proxy
    finally:
        if worker is not None:
            worker.stop_gen_thread()
            worker._timer.stop()
        if network is not None:
            network.stop()
        live.stop_gen_thread()
        live._timer.stop()
        live_network.stop()
        sip.delete(worker_manager)
        sip.delete(live_manager)


def oracle(proxy):
    value = reference.OriginalTargetReference()
    value._nm, value._clip_fade_geometry_cache = proxy, {}
    return value


def same(actual, expected):
    assert actual.dtype == np.float32 and actual.shape == (512,)
    assert actual.flags.owndata and actual.flags.writeable
    assert actual.tobytes() == expected.tobytes()


def counts():
    return sys.modules['qascade_ce.native_fade_target'].counters()


@contextmanager
def actual_native_returns():
    native = sys.modules['qascade_ce.native_fade_target'].NATIVE
    calls, previous = [], []
    for name, function in native.items():
        assert isinstance(function, ct._CFuncPtr)
        old = getattr(function, 'errcheck', None)
        previous.append((function, old))
        def observe(result, actual, arguments, label=name, prior=old):
            calls.append((label, actual, result))
            return result if prior is None else prior(result, actual, arguments)
        function.errcheck = observe
    try:
        yield calls
    finally:
        for function, old in previous:
            if old is None:
                del function.errcheck
            else:
                function.errcheck = old


@pytest.mark.parametrize('variant', ('normal', 'readonly', 'strided', 'no_default'))
def test_real_owned_snapshot_proxy_cold_warm_output_then_actual_c(owned, variant):
    worker, network, manager, proxy = owned
    if variant == 'no_default':
        network.set_channel_defaults({})
    elif variant == 'readonly':
        for array in network._channel_defaults[1]:
            array.flags.writeable = False
    elif variant == 'strided':
        data, active = network._channel_defaults[1]
        raw, mask = np.zeros(1024, np.uint8), np.zeros(1024, bool)
        raw[::2], mask[::2] = data, active
        network._channel_defaults = {1: (raw[::2], mask[::2])}
    worker._clip_fade_geometry_cache.clear()
    expected = oracle(proxy)._get_fade_out_target(1, proj=manager.project)
    before = counts()
    with actual_native_returns() as calls:
        actual = worker._get_fade_out_target(1, proj=manager.project)
    same(actual, expected)
    assert calls and all(result == 0 for _name, _fn, result in calls)
    assert any(name == 'target' for name, _fn, _result in calls)
    assert counts()['target_values'] == before['target_values'] + 512
    cold = counts()
    actual[0] = 99
    with actual_native_returns() as warm_calls:
        warmed = worker._get_fade_out_target(1, proj=manager.project)
    same(warmed, expected)
    assert not np.shares_memory(actual, warmed)
    assert counts()['target_calls'] == cold['target_calls']
    assert counts()['cache'] == cold['cache'] + 1
    assert all(result == 0 for _name, _fn, result in warm_calls)
    for name in ('foreign', 'errors', 'fallbacks'):
        assert counts()[name] == before[name], name


def test_owned_proxy_public_default_api_equal_sum_count_is_current(owned):
    worker, network, manager, proxy = owned
    network.set_channel_defaults({1: {0: 20, 1: 40}})
    initial = worker._get_fade_out_target(1, proj=manager.project)
    network.set_channel_defaults({1: {0: 40, 1: 20}})
    expected = oracle(proxy)._get_fade_out_target(1, proj=manager.project)
    assert initial.tobytes() != expected.tobytes()
    before = counts()
    with actual_native_returns() as calls:
        actual = worker._get_fade_out_target(1, proj=manager.project)
    same(actual, expected)
    assert any(name == 'target' and result == 0 for name, _fn, result in calls)
    assert counts()['foreign'] == before['foreign']


@pytest.mark.parametrize('mutation', ('subclass', 'foreign_target', 'same_code_foreign_globals', 'instance_defaults_shadow'))
def test_proxy_foreign_controls_keep_original_callbacks_once_and_no_c(owned, monkeypatch, mutation):
    worker, network, manager, proxy = owned
    events = []
    kind = type(proxy)
    if mutation == 'subclass':
        class ForeignProxy(kind):
            def __getattr__(self, name):
                events.append(name)
                return super().__getattr__(name)
        proxy = ForeignProxy(network, build_mode=True)
        worker._nm = proxy
    elif mutation == 'foreign_target':
        class ForeignTarget:
            @property
            def _channel_defaults(self):
                events.append('defaults')
                return network._channel_defaults
        vars(proxy)['_target'] = ForeignTarget()
    elif mutation == 'instance_defaults_shadow':
        vars(proxy)['_channel_defaults'] = {1: network._channel_defaults[1]}
    else:
        original = kind.__getattr__
        cloned = FunctionType(original.__code__, dict(original.__globals__), original.__name__,
                              original.__defaults__, original.__closure__)
        monkeypatch.setattr(kind, '__getattr__', cloned)
    expected = oracle(proxy)._get_fade_out_target(1, proj=manager.project)
    expected_events = list(events)
    events.clear()
    before = counts()
    with actual_native_returns() as calls:
        actual = worker._get_fade_out_target(1, proj=manager.project)
    same(actual, expected)
    assert events == expected_events
    assert calls == []
    assert counts()['foreign'] == before['foreign'] + 1


def test_original_core_wrapper_identity_and_four_old_stamps_remain(owned):
    from qascade_ce.native_fade_target_install import _GUARD, wrapper_current
    from qascade_ce.native_fade_target_guard import ORIGINAL
    from src.engine.timeline_temporal_live_scope import _EXTRA
    worker, _network, _manager, _proxy = owned
    assert type(worker)._get_fade_out_target is _GUARD.wrapper
    assert _GUARD.wrapper.__wrapped__ is _GUARD.original
    assert wrapper_current(_GUARD.wrapper, type(worker), instance=worker)
    assert ORIGINAL['_get_fade_out_target'] == _EXTRA['_get_fade_out_target'] == '7f64f146d4f2d367d26f64f00dcdae06e1bb5c88b1403b815d0997a8020649e1'
    assert len(ORIGINAL) == 4


def installed_owner():
    from qascade_ce.native_fade_owned_install import _OWNER
    assert _OWNER is not None, 'Owned admission successor was not mounted'
    return _OWNER


def refused(owned, before):
    worker, _network, manager, proxy = owned
    expected = oracle(proxy)._get_fade_out_target(1, proj=manager.project)
    with actual_native_returns() as calls:
        actual = worker._get_fade_out_target(1, proj=manager.project)
    same(actual, expected)
    assert calls == []
    assert counts()['foreign'] == before['foreign'] + 1


def test_late_constructor_wrapper_code_is_not_retained_authority(owned, monkeypatch):
    from qascade_ce import native_fade_owned_scope as scope
    owner = installed_owner()
    constructor = owner.wrappers[1]
    original = constructor.__code__
    # A harmless in-place bytecode change keeps freevars/signature untouched.
    constants = list(original.co_consts)
    constants[0] = 'foreign constructor metadata'
    monkeypatch.setattr(constructor, '__code__', original.replace(co_consts=tuple(constants)))
    assert not scope.retained(owner.source, owner.registry, owner.wrappers)
    refused(owned, counts())


@pytest.mark.parametrize('shape', ('callable_reference', 'wrong_tuple_length'))
def test_foreign_registry_entry_cannot_execute_callback(owned, monkeypatch, shape):
    worker, network, _manager, proxy = owned
    owner = installed_owner()
    rows, _lock = owner.registry
    events = []
    def foreign_reference():
        events.append('foreign weak reference')
        return proxy
    invalid = (foreign_reference, network) if shape == 'callable_reference' else (foreign_reference,)
    monkeypatch.setitem(rows, id(proxy), invalid)
    refused(owned, counts())
    assert events == []


def test_target_change_to_another_real_network_manager_is_vetoed(owned, monkeypatch):
    from src.network.network_manager import NetworkManager
    _worker, _network, manager, proxy = owned
    other = NetworkManager(manager)
    other.set_channel_defaults({1: {0: 74, 4: 0}})
    monkeypatch.setitem(vars(proxy), '_target', other)
    try:
        refused(owned, counts())
    finally:
        other.stop()


@pytest.mark.parametrize('mutation', ('borrowed_pointer', 'native_pointer', 'argtypes', 'restype'))
def test_owned_late_supplier_and_native_abi_refusal(owned, monkeypatch, mutation):
    import qascade_ce.native_fade_target as native
    from qascade_ce import native_binding
    events = []
    if mutation == 'borrowed_pointer':
        def foreign(*args):
            events.append('foreign supplier')
            raise AssertionError('Foreign pointer supplier called')
        monkeypatch.setattr(native_binding, 'borrowed_pointer', foreign)
    elif mutation == 'native_pointer':
        monkeypatch.setitem(native.NATIVE, 'target', lambda *args: events.append('foreign native'))
    elif mutation == 'argtypes':
        monkeypatch.setattr(native.NATIVE['target'], 'argtypes', [ct.c_void_p])
    else:
        monkeypatch.setattr(native.NATIVE['target'], 'restype', ct.c_double)
    # Pointer mutation prevents the instrumentation helper's _CFuncPtr assertion;
    # source and final counters still prove that the foreign callable was unused.
    worker, _network, manager, proxy = owned
    expected = oracle(proxy)._get_fade_out_target(1, proj=manager.project)
    before = counts()
    same(worker._get_fade_out_target(1, proj=manager.project), expected)
    assert counts()['signature_calls'] == before['signature_calls']
    assert counts()['target_calls'] == before['target_calls']
    assert counts()['foreign'] == before['foreign'] + 1
    assert events == []
