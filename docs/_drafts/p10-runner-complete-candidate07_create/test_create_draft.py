"""Independent original complete state/frame/Effect controls; ALL UNRUN."""
import ctypes as ct
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
import textwrap
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
BASE = HERE.parent/'p10-runner-complete-candidate06_edit'/'test_edit_draft.py'
spec = importlib.util.spec_from_file_location('_runner07_preserved_controls',BASE)
edits = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = edits
spec.loader.exec_module(edits)
controls,register = edits.controls,edits.register
complete,original,mounted = controls.complete,controls.original,controls.mounted


@pytest.fixture
def created(complete,monkeypatch):
    package = sys.modules[complete[2].__package__]
    paths = [HERE,HERE.parent/'p10-runner-complete-candidate06_edit',
        HERE.parent/'p10-runner-complete-candidate05_retire',HERE.parent/'p10-runner-complete-candidate04_edit',
        HERE.parent/'p10-runner-complete-candidate03_scope']
    monkeypatch.setattr(package,'__path__',[*(str(path) for path in paths),*package.__path__])
    installer = __import__(package.__name__+'.native_runner_create_install',fromlist=['install'])
    kind = complete[3]._PROOF.owner
    names = ('tick','get_track_gaps','_edge_gaps','_edge_projection','_rebuild_owner_map',
        '_car_outputs','_set_device_positions_impl','add_track','_ensure_legacy_track','set_path',
        'attach_train_to_track','_add_train_v2','_add_train_legacy')
    before = {name:vars(kind)[name] for name in names}
    if getattr(complete[0]._engine.dll,'ce_runner_train_create_prepare_v7',None) is None:
        raise RuntimeError('SETUP: actual complete create native ABI is unavailable')
    if not installer.install(complete[0]._engine):
        raise RuntimeError('SETUP: reviewed complete create role/class/layout/provider tables are unavailable')
    guard = installer._GUARD
    try:
        yield complete,installer
    finally:
        assert installer.previous.previous.consumers.close_all_owned() == ()
        installer.previous.previous._restore_host(guard.host)
        for name,value in before.items():
            setattr(kind,name,value)
        for name in ('get_track_gap_batch','preview_train_outputs','register_scene',
                     'attach_train_materialized','add_train_materialized'):
            delattr(kind,name)
        prior = installer.previous.previous
        prior.scope_install._GUARD = prior.previous._GUARD = prior._GUARD = installer.previous._GUARD = installer._GUARD = None
        installer.dispatch.discard_compat(installer.dispatch._COMPAT)


@contextmanager
def create_work(created,after=None):
    runtime = created[1].runtime
    calculate,initialize = runtime.Call.calculate.__code__,runtime.initialize.__code__
    numerical = controls.codes(created[0][3])
    # The unchanged original add_train body only chooses the v1/v2 signature.
    numerical.discard(created[0][3]._PROOF.owner.add_train.__code__)
    seen,python_math = [],[]
    def observe(frame,event,value):
        if event == 'call' and frame.f_code in numerical:
            python_math.append(frame.f_code.co_qualname)
        if event == 'return' and frame.f_code is calculate and value is not None:
            call = frame.f_locals['self']
            seen.append((isinstance(call.native.binding.functions['train_create'],ct._CFuncPtr),call.request.operation))
            if after is not None:
                after()
        if event == 'return' and frame.f_code is initialize:
            context = frame.f_locals['context']
            seen.append((isinstance(context[1].functions['train_initialize'],ct._CFuncPtr),0))
    assert sys.getprofile() is None
    sys.setprofile(observe)
    try:
        yield seen,python_math
    finally:
        sys.setprofile(None)


def pair(created,original):
    return controls.pair(created[0],original)


def slots(train):
    result = {}
    for name in type(train).__slots__:
        value = getattr(train,name)
        if name == 'cars':
            value = [tuple(register.deep_bits(getattr(car,field)) for field in type(car).__slots__) for car in value]
        elif type(value) is controls.np.ndarray:
            value = controls.helpers.array(value)
        else:
            value = register.deep_bits(value)
        result[name] = value
    return result


def equivalent(actual,reference):
    edits.equivalent(actual,reference)
    assert {key:slots(train) for key,train in actual._trains.items()} == {key:slots(train) for key,train in reference._trains.items()}


@pytest.mark.parametrize('count',(-5,0,3,2**1000))
def test_direct_constructor_complete_all_slots_arbitrary_count_color_and_fresh_lists(created,original,count):
    actual_kind = created[0][3]._PROOF.module._Train
    reference_kind = sys.modules[original.__module__]._Train
    color = object()
    expected = reference_kind('direct',count,-0.,9.,-3.,color)
    with create_work(created) as (seen,python_math):
        actual = actual_kind('direct',count,-0.,9.,-3.,color)
    assert slots(actual) == slots(expected) and actual.color is color
    assert len({id(actual.cars),id(actual.trail),id(actual.path_points),id(actual.cum)}) == 4
    assert seen == [(True,0)] and python_math == []


@pytest.mark.parametrize('count',(-7,0,4,2**1000))
def test_real_v2_duplicate_replaces_only_train_and_keeps_old_external_state(created,original,count):
    actual,reference = pair(created,original)
    controls.checked_tick(created[0],actual,reference,.04)
    for owner in (actual,reference):
        owner._ensure_legacy_track('train',[(0.,0.),(3.,0.)])
        owner._ce_ctx_dirty = False
    old = actual._trains['train']
    old_state = slots(old)
    aliases = actual._trains,actual._edges,actual._owner_map,actual._gap_cache,actual._edges['__legacy_track__train']
    expected = reference.add_train('train',count,-0.,7.,-2.,123)
    with create_work(created) as (seen,python_math):
        value = actual.add_train('train',count,-0.,7.,-2.,123)
    assert value == expected and actual._trains['train'] is not old and slots(old) == old_state
    assert all(a is b for a,b in zip(aliases,(actual._trains,actual._edges,actual._owner_map,actual._gap_cache,actual._edges['__legacy_track__train'])))
    equivalent(actual,reference)
    assert seen == [(True,1)] and python_math == []
    if count != 2**1000:
        controls.checked_tick(created[0],actual,reference,.04)


LEGACY_CASES = (([],0),([],3),([(0.,0.)],3),([(0.,0.),(100.,0.)],-2),
    ([(0.,0.),(100.,0.)],0),([(0.,0.),(100.,0.)],4),
    ([(0.,0.),(40.,40.),(0.,40.),(40.,0.)],3))


@pytest.mark.parametrize('unit',(-5.,0.,12.))
@pytest.mark.parametrize('points,count',LEGACY_CASES)
def test_real_legacy_one_complete_geometry_attach_all_slots_aliases_and_next_frame(created,original,unit,points,count):
    actual,reference = pair(created,original)
    old,old_state = actual._trains['train'],slots(actual._trains['train'])
    mapping,edges = actual._trains,actual._edges
    expected = reference.add_train('train',points,count,unit,15.,-2.,42,ignored=True)
    with create_work(created) as (seen,python_math):
        value = actual.add_train('train',points,count,unit,15.,-2.,42,ignored=True)
    assert value == expected and actual._trains is mapping and actual._edges is edges
    assert slots(old) == old_state and actual._trains['train'] is not old
    equivalent(actual,reference)
    train,edge = actual._trains['train'],actual._edges['__legacy_track__train']
    if points:
        assert train.path_points is not edge.points and train.cum is not edge.cum
        assert all(getattr(train,name) is getattr(edge,name) for name in ('proj_s','proj_d','s_order','s_sorted','d_sorted'))
    else:
        assert train.cars == train.trail == train.path_points == train.cum == [] and train._home_edge_id is None
    assert seen == [(True,2)] and python_math == []
    controls.checked_tick(created[0],actual,reference,.04)


def test_keyword_path_does_not_change_original_public_signature_dispatch(created,original):
    actual,reference = pair(created,original)
    before = register.registry(actual)
    with pytest.raises(TypeError) as expected:
        reference.add_train('train',path_points=[(0.,0.),(1.,1.)])
    with pytest.raises(type(expected.value),match='path_points'):
        actual.add_train('train',path_points=[(0.,0.),(1.,1.)])
    assert register.registry(actual) == before


@pytest.mark.parametrize('unit',(-5.,0.,12.))
def test_actual_patched_panel_create_uses_original_source_body_and_final_frame(created,original,unit):
    from src.ui.route_animator import runner_panel as ui
    if not created[1].consumers.current():
        raise RuntimeError('SETUP: actual patched Panel source/current supplier records are unavailable')
    namespace = dict(vars(ui))
    exec(compile(textwrap.dedent((HERE/'original_panel_add_train.py.txt').read_text(encoding='utf-8-sig')),str(HERE/'original_panel_add_train.py.txt'),'exec'),namespace)
    actual,reference = pair(created,original)
    panels = []
    for engine in (actual,reference):
        panels.append(SimpleNamespace(_engine=engine,_fallback_car_unit=unit,_train_names={},
            _selected_train_id=None,_default_base_speed=lambda:8.,_build_train_card=lambda tid:None,
            _refresh_card_selection=lambda:None))
    # Source seam call only. Genuine Main/widget/history/save/reopen is separate.
    expected_id = namespace['add_train'](panels[1],'Original','#123456')
    with create_work(created) as (seen,python_math):
        actual_id = ui.RunnerPanel.add_train(panels[0],'Original','#123456')
    assert slots(actual._trains[actual_id]) | {'train_id':expected_id} == slots(reference._trains[expected_id])
    assert panels[0]._selected_train_id == actual_id and panels[0]._train_names[actual_id] == 'Original'
    assert seen == [(True,4)] and python_math == []
    # UUIDs are opaque metadata: align only independent identity before unchanged full-output assertions.
    train = actual._trains.pop(actual_id)
    train.train_id = expected_id
    actual._trains[expected_id] = train
    controls.checked_tick(created[0],actual,reference,.04)
