"""Independent new controls for the C03 candidate02 review findings.

No frame C invocation in these init/current negative cases. ROOT dispatches
only after reading/admitting, preserving candidate02 and all prior raw results.
"""
import ctypes as ct
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


from test_r215_fade_target import qapp,mounted,monkeypatch_module,owner
from test_r215_fade_target_guard import installed


@pytest.mark.parametrize('module_name,field', [
    ('native_fade_target_runtime','math'),('native_fade_target_runtime','np'),
    ('native_fade_target','np'),('native_fade_target','ct'),
])
def test_initial_foreign_alias_is_not_a_trusted_supplier(owner,monkeypatch,module_name,field):
    import importlib
    from qascade_ce.native_fade_target_guard import FadeGuard
    module,guard = installed()
    monkeypatch.setattr(guard.owner,'_get_fade_out_target',guard.original)
    candidate = importlib.import_module('qascade_ce.'+module_name)
    monkeypatch.setattr(candidate,field,SimpleNamespace(foreign=True))
    result = FadeGuard(guard.engine)
    assert not result.safe


def test_initial_abi_is_checked_against_fixed_contract(owner,monkeypatch):
    import qascade_ce.native_fade_target as math
    from qascade_ce.native_fade_target_guard import FadeGuard
    module,guard = installed()
    monkeypatch.setattr(guard.owner,'_get_fade_out_target',guard.original)
    monkeypatch.setattr(math.NATIVE['target'],'argtypes',[ct.c_double])
    result = FadeGuard(guard.engine)
    assert not result.safe


def test_rejected_install_never_calls_foreign_binder(owner,monkeypatch):
    import qascade_ce.native_fade_target as math
    module,guard = installed()
    events = []
    original = math.bind_native
    def spy(*args,**kwargs):
        events.append('foreign-binder')
        return original(*args,**kwargs)
    monkeypatch.setattr(guard.owner,'_get_fade_out_target',guard.original)
    monkeypatch.setattr(module,'_GUARD',None)
    monkeypatch.setattr(math,'NATIVE',math.NATIVE)
    monkeypatch.setattr(math,'bind_native',spy)
    assert module.install(guard.engine) is False
    assert events == []


def test_current_pointer_supplier_globals_cannot_change_unnoticed(owner,monkeypatch):
    import qascade_ce.native_binding as binding
    module,guard = installed()
    assert guard.source_current()
    monkeypatch.setattr(binding,'ct',SimpleNamespace(cast=lambda *a:None))
    assert not guard.source_current()


def test_cached_target_revisit_updates_motor_provenance(owner):
    from src.core.models import FixtureDefinition,FixtureInstance
    from test_r215_fade_target import oracle,same
    engine,nm,pm = owner
    project = pm.project
    project.fixture_defs = [FixtureDefinition(id='motor',channels=['motor_x'])]
    fixture = FixtureInstance(id='f',def_id='motor',universe=1,start_channel=1)
    project.fixtures = [fixture]
    nm.set_fixture_defaults_from_project(project)
    engine._get_fade_out_target(1,proj=project)
    fixture.start_channel = 2
    nm.set_fixture_defaults_from_project(project)
    engine._get_fade_out_target(1,proj=project)
    fixture.start_channel = 1
    nm.set_fixture_defaults_from_project(project)
    wanted = oracle(nm)
    same(engine._get_fade_out_target(1,proj=project),wanted._get_fade_out_target(1,proj=project))
    # Original public geometry consumer must agree, not merely cached target.
    assert engine._build_motor_channel_mask(1,project).tobytes() == wanted._build_motor_channel_mask(1,project).tobytes()


def test_original_cache_eviction_does_not_drop_default_provenance_only(owner):
    from src.core.models import FixtureDefinition,FixtureInstance
    from test_r215_fade_target import oracle,same
    engine,nm,pm = owner
    project = pm.project
    project.fixture_defs = [FixtureDefinition(id='motor',channels=['motor_x'])]
    fixture = FixtureInstance(id='f',def_id='motor',universe=1,start_channel=1)
    project.fixtures = [fixture]
    nm.set_fixture_defaults_from_project(project)
    engine._get_fade_out_target(1,proj=project)
    # A real changed source retires/reinserts motor, leaving context older.
    fixture.start_channel = 2
    nm.set_fixture_defaults_from_project(project)
    engine._get_fade_out_target(1,proj=project)
    context = ('__fade_target_defaults_c03__',1,id(project))
    motor = ('__motor_channel_mask__',1,id(project),1,1)
    # Real existing bounded store/eviction method, no manually deleted cache.
    index = 0
    while context in engine._clip_fade_geometry_cache:
        engine._store_clip_fade_geometry_cache(('other-geometry-row',index),(index,))
        index += 1
        assert index <= 8192
    assert motor in engine._clip_fade_geometry_cache
    fixture.start_channel = 3
    nm.set_fixture_defaults_from_project(project)
    same(engine._get_fade_out_target(1,proj=project),oracle(nm)._get_fade_out_target(1,proj=project))
