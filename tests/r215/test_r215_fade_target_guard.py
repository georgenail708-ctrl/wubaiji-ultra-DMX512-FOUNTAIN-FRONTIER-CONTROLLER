"""New strict C03 wrapper/current-metadata controls; original30 stays unchanged."""
import ctypes as ct
import sys

import numpy as np
import pytest

from test_r215_fade_target import mounted,monkeypatch_module,qapp,owner,counts,oracle,same


def installed():
    module = sys.modules.get('qascade_ce.native_fade_target_install')
    assert module is not None and module._GUARD is not None
    return module,module._GUARD


def test_exact_wrapper_retains_old_body_and_ordinary_live_scope(owner):
    from src.engine.timeline_temporal_live_scope import default_live_scope,_EXTRA
    engine,nm,pm = owner
    module,guard = installed()
    function = type(engine)._get_fade_out_target
    assert function.__wrapped__ is guard.original
    assert module.wrapper_current(function,type(engine))
    assert _EXTRA['_get_fade_out_target'] == '7f64f146d4f2d367d26f64f00dcdae06e1bb5c88b1403b815d0997a8020649e1'
    assert default_live_scope(engine)


@pytest.mark.parametrize('field', ['__wrapped__','__code__','__defaults__'])
def test_wrapper_metadata_tamper_vetoes_live_scope(owner,monkeypatch,field):
    from src.engine.timeline_temporal_live_scope import default_live_scope
    engine,nm,pm = owner
    module,guard = installed()
    assert module.wrapper_current(guard.wrapper,type(engine))
    replacement = {'__wrapped__':lambda *a,**k:None,
        '__code__':guard.original.__code__, '__defaults__':(pm.project,)}[field]
    if field == '__code__':
        # Preserve the one required closure slot; only executable body changes.
        def factory(original):
            def changed(owner,universe,proj=None):
                return original(owner,universe,proj=proj)
            return changed
        replacement = factory(guard.original).__code__
    monkeypatch.setattr(guard.wrapper,field,replacement)
    assert not module.wrapper_current(guard.wrapper,type(engine))
    assert not default_live_scope(engine)


@pytest.mark.parametrize('mutation', ['render','borrowed_pointer','numpy_sum','native_function','native_argtypes'])
def test_changed_globals_or_binding_use_original_once(owner,monkeypatch,mutation):
    import qascade_ce.native_fade_target as math
    import qascade_ce.native_fade_target_runtime as runtime
    from src.engine.timeline_temporal_live_scope import default_live_scope
    engine,nm,pm = owner
    module,guard = installed()
    nm.set_channel_defaults({1:{0:71,511:0}})
    wanted = oracle(nm)._get_fade_out_target(1,proj=pm.project)
    events = []
    if mutation == 'render':
        monkeypatch.setattr(runtime,'render',lambda *a:events.append('foreign-render'))
    elif mutation == 'borrowed_pointer':
        original = math.borrowed_pointer
        monkeypatch.setattr(math,'borrowed_pointer',lambda *a:events.append('foreign-pointer'))
    elif mutation == 'numpy_sum':
        original = np.sum
        def numeric(*args,**kwargs):
            events.append('sum')
            return original(*args,**kwargs)
        monkeypatch.setattr(np,'sum',numeric)
    elif mutation == 'native_function':
        monkeypatch.setitem(math.NATIVE,'target',lambda *a:events.append('foreign-native'))
    else:
        monkeypatch.setattr(math.NATIVE['target'],'argtypes',[ct.c_double])
    assert not module.wrapper_current(guard.wrapper,type(engine))
    assert not default_live_scope(engine)
    before = counts()
    same(engine._get_fade_out_target(1,proj=pm.project),wanted)
    after = counts()
    assert after['target_calls'] == before['target_calls']
    assert after['signature_calls'] == before['signature_calls']
    assert events == (['sum'] if mutation == 'numpy_sum' else [])


def test_default_change_retires_only_matching_motor_row(owner):
    from src.core.models import FixtureDefinition,FixtureInstance
    engine,nm,pm = owner
    project = pm.project
    project.fixture_defs = [FixtureDefinition(id='motor',channels=['motor_x'])]
    fixture = FixtureInstance(id='f',def_id='motor',universe=1,start_channel=1)
    project.fixtures = [fixture]
    nm.set_fixture_defaults_from_project(project)
    engine._get_fade_out_target(1,proj=project)
    first_key = ('__motor_channel_mask__',1,id(project),1,1)
    unrelated_key = ('__motor_channel_mask__',2,id(project),1,1)
    unrelated = object()
    arbitrary_key,arbitrary = ('unrelated-author-cached-row',),object()
    engine._clip_fade_geometry_cache[unrelated_key] = unrelated
    engine._clip_fade_geometry_cache[arbitrary_key] = arbitrary
    first_row = engine._clip_fade_geometry_cache[first_key]
    fixture.start_channel = 2
    nm.set_fixture_defaults_from_project(project)
    wanted = oracle(nm)._get_fade_out_target(1,proj=project)
    same(engine._get_fade_out_target(1,proj=project),wanted)
    assert engine._clip_fade_geometry_cache[first_key] is not first_row
    assert engine._clip_fade_geometry_cache[unrelated_key] is unrelated
    assert engine._clip_fade_geometry_cache[arbitrary_key] is arbitrary
    snapshot = engine._clip_fade_geometry_cache[first_key]
    before = counts()
    same(engine._get_fade_out_target(1,proj=project),wanted)
    assert engine._clip_fade_geometry_cache[first_key] is snapshot
    assert counts()['target_calls'] == before['target_calls']


def test_instance_motor_override_vetoes_live_scope_and_runs_once(owner,monkeypatch):
    from src.engine.timeline_temporal_live_scope import default_live_scope
    engine,nm,pm = owner
    module,guard = installed()
    events = []
    def motor(universe,project):
        events.append((universe,project))
        result = np.zeros(512,bool)
        result[511] = True
        return result
    monkeypatch.setattr(engine,'_build_motor_channel_mask',motor)
    assert not module.wrapper_current(guard.wrapper,type(engine),instance=engine)
    assert not default_live_scope(engine)
    before = counts()
    result = engine._get_fade_out_target(1,proj=pm.project)
    assert result[511] == 128 and len(events) == 1
    assert counts()['target_calls'] == before['target_calls']
