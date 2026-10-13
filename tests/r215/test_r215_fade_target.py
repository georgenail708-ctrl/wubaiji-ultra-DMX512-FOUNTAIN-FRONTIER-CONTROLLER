"""Independent C03 controls; frozen original AST is the full-output oracle.

Freshness cases use real NetworkManager public default/patch binding APIs;
they do not assert that a whole MainWindow patch workflow was reproduced.
ROOT alone dispatches this file under the guarded validation budget.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import MethodType
import warnings

import numpy as np
import pytest

from r215_support import ROOT
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'render_lab'))
spec = importlib.util.spec_from_file_location('r215_fade_reference',
    Path(__file__).with_name('original_reference.py'))
reference_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference_module)
OriginalTargetReference = reference_module.OriginalTargetReference


@pytest.fixture(scope='module')
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(['r215-fade-target'])
    yield app
    app.processEvents()


@pytest.fixture(scope='module')
def mounted(qapp, monkeypatch_module):
    monkeypatch_module.setenv('QASCADE_CT_ENGINE', '1')
    from qascade_ce._mount_ce import _mount_ce
    result = _mount_ce()
    assert result.active, result.to_dict()
    return result


@pytest.fixture(scope='module')
def monkeypatch_module():
    with pytest.MonkeyPatch.context() as patch:
        yield patch


@pytest.fixture
def owner(mounted, qapp):
    from src.core.project_manager import ProjectManager
    from src.engine.playback_engine import PlaybackEngine
    from src.network.network_manager import NetworkManager
    pm, nm = ProjectManager(), NetworkManager()
    engine = PlaybackEngine(nm, pm)
    try:
        yield engine, nm, pm
    finally:
        engine.stop()
        nm.stop()
        engine.deleteLater()
        nm.deleteLater()
        qapp.processEvents()


def oracle(nm):
    result = OriginalTargetReference()
    result._nm = nm
    result._clip_fade_geometry_cache = {}
    return result


def counts():
    module = sys.modules.get('qascade_ce.native_fade_target')
    return {} if module is None else module.counters()


def same(actual, wanted):
    assert actual.dtype == np.float32
    assert actual.shape == (512,)
    assert actual.flags.owndata
    assert actual.flags.writeable
    assert actual.tobytes() == wanted.tobytes()


def target_data(length, dtype, shape=False, strided=False, special=False):
    values = np.arange(length, dtype=np.float64) % 255
    if dtype != np.uint8:
        values = values - 80.25
    values = values.astype(dtype)
    active = (np.arange(length) % 3 == 0)
    if special and length >= 4:
        values[:4] = (np.nan, np.inf, -np.inf, -0.0)
        active[:4] = (True, True, False, True)
    if shape:
        values, active = values.reshape(2, -1), active.reshape(2, -1)
    if strided:
        backing = np.zeros(length*2, dtype=dtype)
        mask = np.zeros(length*2, dtype=bool)
        backing[::2], mask[::2] = values, active
        values, active = backing[::2], mask[::2]
    return values, active


@pytest.mark.parametrize('length,dtype,shape,strided,special', [
    (0,np.uint8,False,False,False), (1,np.uint8,False,False,False),
    (511,np.uint8,False,False,False), (512,np.uint8,False,False,False),
    (513,np.uint8,False,False,False), (512,np.uint8,True,False,False),
    (512,np.uint8,False,True,False), (512,np.float32,False,False,False),
    (512,np.float64,False,False,False), (512,np.float32,False,False,True),
])
@pytest.mark.parametrize('readonly', [False, True])
def test_full_frame_frozen_original_shape_dtype_readonly(owner, length, dtype,
        shape, strided, special, readonly):
    engine, nm, pm = owner
    values, active = target_data(length, dtype, shape, strided, special)
    if readonly:
        values.flags.writeable = active.flags.writeable = False
    before = values.tobytes(), active.tobytes()
    nm._channel_defaults = {1: (values, active)}
    reference = oracle(nm)
    with warnings.catch_warnings(record=True) as old_warnings:
        warnings.simplefilter('always')
        wanted = reference._get_fade_out_target(1, proj=pm.project)
    with warnings.catch_warnings(record=True) as new_warnings:
        warnings.simplefilter('always')
        actual = engine._get_fade_out_target(1, proj=pm.project)
    same(actual, wanted)
    assert [(type(w.message),str(w.message)) for w in new_warnings] == [
        (type(w.message),str(w.message)) for w in old_warnings]
    assert (values.tobytes(), active.tobytes()) == before


def test_ordinary_cold_and_warm_actual_native_work(owner):
    engine, nm, pm = owner
    nm.set_channel_defaults({1: {0: 17, 3: 233, 511: 0}})
    before = counts()
    wanted = oracle(nm)._get_fade_out_target(1, proj=pm.project)
    actual = engine._get_fade_out_target(1, proj=pm.project)
    cold = counts()
    same(actual, wanted)
    assert cold.get('signature_calls',0) - before.get('signature_calls',0) == 1
    assert cold.get('target_calls',0) - before.get('target_calls',0) == 1
    assert cold.get('signature_data_values',0) - before.get('signature_data_values',0) == 512
    assert cold.get('signature_active_values',0) - before.get('signature_active_values',0) == 512
    assert cold.get('target_values',0) - before.get('target_values',0) == 512
    assert cold.get('motor_values',0) - before.get('motor_values',0) == 512
    actual[0] = 99
    warm = engine._get_fade_out_target(1, proj=pm.project)
    after = counts()
    same(warm, wanted)
    assert not np.shares_memory(actual, warm)
    assert after.get('signature_calls',0) - cold.get('signature_calls',0) == 1
    assert after.get('target_calls',0) == cold.get('target_calls',0)
    assert after.get('target_values',0) == cold.get('target_values',0)
    for name in ('foreign','errors','fallbacks'):
        assert after.get(name,0) == before.get(name,0)


def test_no_default_entry_still_has_actual_native_target(owner):
    engine, nm, pm = owner
    before = counts()
    same(engine._get_fade_out_target(1, proj=pm.project),
        oracle(nm)._get_fade_out_target(1, proj=pm.project))
    after = counts()
    assert after.get('target_calls',0) - before.get('target_calls',0) == 1
    assert after.get('target_values',0) - before.get('target_values',0) == 512


class ArraySource:
    def __init__(self, value, label, events, fail_uint8=False):
        self.value, self.label, self.events = value, label, events
        self.fail_uint8 = fail_uint8

    def __array__(self, dtype=None, copy=None):
        self.events.append((self.label, str(np.dtype(dtype))))
        if self.fail_uint8 and dtype == np.uint8:
            raise ValueError('intentional custom signature conversion failure')
        return np.asarray(self.value, dtype=dtype)


@pytest.mark.parametrize('failure', [False, True])
def test_foreign_array_callback_order_and_no_preflight_reexecution(owner, failure):
    engine, nm, pm = owner
    values, active = target_data(512, np.uint8)
    events = []
    nm._channel_defaults = {1: (ArraySource(values,'data',events,failure),
        ArraySource(active,'active',events))}
    reference = oracle(nm)
    expected = reference._get_fade_out_target(1, proj=pm.project)
    old_order = list(events)
    events.clear()
    before = counts()
    actual = engine._get_fade_out_target(1, proj=pm.project)
    after = counts()
    same(actual, expected)
    assert events == old_order
    assert after.get('target_calls',0) == before.get('target_calls',0)
    assert after.get('signature_calls',0) == before.get('signature_calls',0)


def test_foreign_motor_callback_runs_once_at_original_place(owner, monkeypatch):
    engine, nm, pm = owner
    nm.set_channel_defaults({1: {4: 0}})
    events = []
    motor = np.zeros(512, bool)
    motor[[4,100,511]] = True
    def callback(self, universe, project):
        events.append((universe,project))
        return motor.copy()
    monkeypatch.setattr(engine, '_build_motor_channel_mask', MethodType(callback,engine))
    reference = oracle(nm)
    reference._build_motor_channel_mask = MethodType(callback,reference)
    wanted = reference._get_fade_out_target(1, proj=pm.project)
    old_order = list(events)
    events.clear()
    before = counts()
    actual = engine._get_fade_out_target(1, proj=pm.project)
    after = counts()
    same(actual,wanted)
    assert events == old_order and len(events) == 1
    assert after.get('target_calls',0) == before.get('target_calls',0)


def test_foreign_f32_conversion_error_keeps_original_motor_fallback(owner):
    engine, nm, pm = owner
    events = []
    class FailsTarget(ArraySource):
        def __array__(self,dtype=None,copy=None):
            if dtype == np.float32:
                self.events.append((self.label,str(np.dtype(dtype))))
                raise ValueError('intentional custom target conversion failure')
            return super().__array__(dtype=dtype,copy=copy)
    values, active = target_data(512,np.uint8)
    nm._channel_defaults = {1:(FailsTarget(values,'data',events),active)}
    reference = oracle(nm)
    wanted = reference._get_fade_out_target(1,proj=pm.project)
    old_order = list(events)
    events.clear()
    before = counts()
    same(engine._get_fade_out_target(1,proj=pm.project),wanted)
    after = counts()
    assert events == old_order
    assert after.get('target_calls',0) == before.get('target_calls',0)


def test_native_abi_rejects_missing_source_before_writing(owner):
    import ctypes as ct
    from qascade_ce.native_binding import borrowed_pointer
    module = sys.modules.get('qascade_ce.native_fade_target')
    assert module is not None and module.NATIVE is not None, 'ordinary C03 must install actual native functions'
    target = np.full(512,-17.5,np.float32)
    motor = np.zeros(512,bool)
    report = (ct.c_int64*4)(-1,-1,-1,-1)
    before = target.tobytes(),tuple(report)
    result = module.NATIVE['target'](None,1,None,0,
        borrowed_pointer(motor,module.U8),borrowed_pointer(target,module.F32),report)
    assert result == -1
    assert (target.tobytes(),tuple(report)) == before


@pytest.mark.parametrize('initial,updated', [
    ({0:20,1:40},{0:40,1:20}), ({0:30},{1:30}),
])
def test_real_default_public_api_equal_sum_count_change_is_fresh(owner, initial, updated):
    engine, nm, pm = owner
    nm.set_channel_defaults({1: initial})
    first = engine._get_fade_out_target(1, proj=pm.project)
    nm.set_channel_defaults({1: updated})
    wanted = oracle(nm)._get_fade_out_target(1, proj=pm.project)
    assert first.tobytes() != wanted.tobytes(), 'real API must change effective idle output'
    assert sum(initial.values()) == sum(updated.values()) and len(initial) == len(updated)
    same(engine._get_fade_out_target(1, proj=pm.project),wanted)


def test_real_project_binding_patch_address_change_is_fresh(owner):
    from src.core.models import FixtureDefinition, FixtureInstance
    engine, nm, pm = owner
    project = pm.project
    project.fixture_defs = [FixtureDefinition(id='motor',channels=['motor_x'])]
    fixture = FixtureInstance(id='f',def_id='motor',universe=1,start_channel=1)
    project.fixtures = [fixture]
    nm.set_fixture_defaults_from_project(project)
    first = engine._get_fade_out_target(1,proj=project)
    fixture.start_channel = 2
    nm.set_fixture_defaults_from_project(project)
    wanted = oracle(nm)._get_fade_out_target(1,proj=project)
    assert first[0] == 128 and first[1] == 0
    assert wanted[0] == 0 and wanted[1] == 128
    assert np.count_nonzero(nm._channel_defaults[1][1]) == 1
    same(engine._get_fade_out_target(1,proj=project),wanted)
