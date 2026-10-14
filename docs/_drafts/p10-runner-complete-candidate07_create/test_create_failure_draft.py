"""Original conversion/error and actual v7 token/refusal controls; ALL UNRUN."""
import ctypes as ct
import importlib.util
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('_runner07_create_controls',HERE/'test_create_draft.py')
controls07 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = controls07
spec.loader.exec_module(controls07)
created,complete,original,mounted = controls07.created,controls07.complete,controls07.original,controls07.mounted
controls,register = controls07.controls,controls07.register


@pytest.mark.parametrize('bad',('bad',None))
def test_builtin_coordinate_failure_publishes_original_partial_create_and_dirty_only(created,original,bad):
    actual,reference = controls07.pair(created,original)
    for owner in (actual,reference):
        owner._ensure_legacy_track('train',[(0.,0.),(90.,0.)])
        owner._ce_ctx_dirty = False
    old = actual._trains['train']
    old_state = controls07.slots(old)
    aliases = actual._edges['__legacy_track__train'],actual._owner_map,actual._gap_cache
    points = [(0.,0.),(bad,1.)]
    with pytest.raises((ValueError,TypeError)) as expected:
        reference._add_train_legacy('train',points,3,-5.,8.,2.,123)
    with controls07.create_work(created) as (seen,python_math):
        with pytest.raises(type(expected.value)) as value:
            actual._add_train_legacy('train',points,3,-5.,8.,2.,123)
    assert str(value.value) == str(expected.value)
    assert actual._trains['train'] is not old and controls07.slots(old) == old_state and actual._ce_ctx_dirty
    assert all(a is b for a,b in zip(aliases,(actual._edges['__legacy_track__train'],actual._owner_map,actual._gap_cache)))
    controls07.equivalent(actual,reference)
    assert seen == [(True,3)] and python_math == []
    controls.checked_tick(created[0],actual,reference,.04)


class Foreign:
    def __init__(self,log,name,value):
        self.log,self.name,self.value = log,name,value

    def __str__(self):
        self.log.append((self.name,'str'))
        return str(self.value)

    def __int__(self):
        self.log.append((self.name,'int'))
        return int(self.value)

    def __float__(self):
        self.log.append((self.name,'float'))
        return float(self.value)

    def __bool__(self):
        self.log.append((self.name,'bool'))
        return bool(self.value)


def foreign_arguments(log,count=3):
    values = (('identity','train'),('count',count),('unit',10.),('radius',8.),('speed',2.),('color','#123456'))
    identity,count,unit,radius,speed,color = (Foreign(log,name,value) for name,value in values)
    points = [(Foreign(log,'x0',0.),Foreign(log,'y0',0.)),(Foreign(log,'x1',100.),Foreign(log,'y1',0.))]
    return identity,points,count,unit,radius,speed,color


def test_foreign_legacy_original_conversion_once_in_original_order_and_counted_compatibility(created,original):
    actual,reference = controls07.pair(created,original)
    actual_log,expected_log = [],[]
    expected = reference._add_train_legacy(*foreign_arguments(expected_log))
    before = created[1].dispatch.compatibility_counts()
    with controls07.create_work(created) as (seen,python_math):
        value = actual._add_train_legacy(*foreign_arguments(actual_log))
    after = created[1].dispatch.compatibility_counts()
    assert value == expected and actual_log == expected_log
    assert after[2] == before[2]+1 and after[:2] == before[:2]
    assert seen == [(True,1)] and python_math
    controls07.equivalent(actual,reference)
    controls.checked_tick(created[0],actual,reference,.04)


def test_nonstandard_builtin_point_shape_keeps_exact_original_compatibility_shape(created,original):
    actual,reference = controls07.pair(created,original)
    points = [(0.,0.,8.),(100.,0.,9.)]
    reference._add_train_legacy('train',points,2,10.,8.,2.)
    before = created[1].dispatch.compatibility_counts()
    actual._add_train_legacy('train',points,2,10.,8.,2.)
    assert created[1].dispatch.compatibility_counts()[2] == before[2]+1
    controls07.equivalent(actual,reference)
    # Unsupported shape is compatibility state, never qualified complete CE tick.


@pytest.mark.parametrize('foreign',(False,True))
def test_legacy_huge_attach_count_budget_refusal_preserves_old_good_output_without_old_allocation(created,original,foreign):
    actual,reference = controls07.pair(created,original)
    last = controls.checked_tick(created[0],actual,reference,.04)
    before,usage = register.registry(actual),controls.usage(actual._ce_complete_runner)
    log = []
    arguments = foreign_arguments(log,2**1000) if foreign else ('train',[(0.,0.),(100.,0.)],2**1000,10.,8.,2.,'#123456')
    with pytest.raises(created[1].model.budget.BudgetRefused):
        actual._add_train_legacy(*arguments)
    assert register.registry(actual) == before and actual._ce_complete_runner.last_frame is last
    assert controls.usage(actual._ce_complete_runner) == usage and actual._ce_complete_runner._retirement.stage is None
    assert not any(name in ('x0','y0','x1','y1') for name,kind in log)
    controls.checked_tick(created[0],actual,reference,.04)


@pytest.mark.parametrize('fault',('cancel','owner_change'))
def test_actual_native_prepare_cancel_or_late_owner_change_aborts_kind7_and_restores_charge(created,original,fault):
    actual,reference = controls07.pair(created,original)
    last = controls.checked_tick(created[0],actual,reference,.04)
    before,usage = register.registry(actual),controls.usage(actual._ce_complete_runner)
    dirty = actual._ce_ctx_dirty
    def veto():
        if fault == 'cancel':
            raise created[1].runtime.ledger.RetirementRefused('Actual owner cancelled complete create')
        actual._ce_ctx_dirty = not dirty
    with controls07.create_work(created,veto):
        with pytest.raises(created[1].runtime.ledger.RetirementRefused):
            actual._add_train_legacy('train',[(0.,0.),(100.,0.)],3,10.,8.,2.)
    actual._ce_ctx_dirty = dirty
    assert register.registry(actual) == before and actual._ce_complete_runner.last_frame is last
    assert controls.usage(actual._ce_complete_runner) == usage and actual._ce_complete_runner._retirement.stage is None
    controls.checked_tick(created[0],actual,reference,.04)


def pending(created,owner):
    runtime = created[1].runtime
    spec = created[1].arguments.converted(dict(train_id='new',car_count=3,car_length=10.,radius=8.,base_speed=2.,color='#123456'),runtime.t.V2)
    spec.update(operation=runtime.t.LEGACY,points=[(0.,0.),(100.,0.)])
    allowance = runtime.model.reservation(owner,created[1]._GUARD.base.module,spec)
    packed = runtime.model.Model(owner,spec,allowance)
    call = runtime.Call(owner._ce_complete_runner,packed.request)
    call.calculate()
    return call


@pytest.mark.parametrize('entry_name',('life_abort','life_destroy'))
def test_real_kind7_retained_prototype_refusal_keeps_handle_token_charge_and_retry(created,original,entry_name):
    actual,reference = controls07.pair(created,original)
    controls.checked_tick(created[0],actual,reference,.04)
    native,ledger = actual._ce_complete_runner,created[1].runtime.ledger
    state = native._retirement
    before,usage = register.registry(actual),controls.usage(native)
    call = pending(created,actual)
    pending_usage,token,handle = controls.usage(native),call.token.value,native.context
    entry = state.entries[entry_name]
    saved = entry.function.argtypes
    entry.function.argtypes = []
    try:
        with pytest.raises(ledger.RetirementRefused):
            ledger.cleanup(state) if entry_name == 'life_abort' else native.close()
        assert state.stage.kind == 7 and state.stage.token.value == token and native.context == handle
        assert state.ticket in ledger._OWNERS and controls.usage(native) == pending_usage
        assert register.registry(actual) == before
    finally:
        entry.function.argtypes = saved
    ledger.cleanup(state)
    assert state.stage is None and controls.usage(native) == usage
    controls.checked_tick(created[0],actual,reference,.04)


def test_actual_kind7_wrong_token_refusal_does_not_orphan_pending_and_charge(created,original):
    actual,reference = controls07.pair(created,original)
    controls.checked_tick(created[0],actual,reference,.04)
    native = actual._ce_complete_runner
    call = pending(created,actual)
    usage = controls.usage(native)
    wrong = ct.c_ulonglong(call.token.value+1)
    state = native._retirement
    assert state.entries['life_abort'].function(native.context,state.generation.value,7,ct.byref(wrong)) == -5
    assert state.stage.kind == 7 and state.stage.token.value == call.token.value and controls.usage(native) == usage
    created[1].runtime.ledger.cleanup(state)
    controls.checked_tick(created[0],actual,reference,.04)


def test_create_reentry_after_confirmed_generation_close_uses_new_native_state(created,original):
    actual,reference = controls07.pair(created,original)
    actual.add_train('first',2,10.,8.,2.)
    reference.add_train('first',2,10.,8.,2.)
    old = actual._ce_complete_runner
    old.close()
    assert old.context is None and old._retirement.retired
    actual.add_train('second',3,12.,9.,4.)
    reference.add_train('second',3,12.,9.,4.)
    assert actual._ce_complete_runner is not old
    controls07.equivalent(actual,reference)
    # Existing R03/R06 scope refresh/combined lifecycle acceptance stays separate.


def test_empty_new_role_layout_provider_and_consumer_tables_refuse_by_default(created,monkeypatch):
    installer = created[1]
    stamps = installer.guard_module.stamps
    for name,value in (('ROLES',{}),('DEFAULTS',{}),('CLASSES',{}),('LAYOUTS',{}),
                       ('PROVIDERS',()),('CONSUMERS',()),('CONSUMER_OWNER',None)):
        monkeypatch.setattr(stamps,name,value)
    assert not installer.guard_module.canonical(installer.runtime)
    assert not installer.guard_module.canonical_classes(installer.runtime)
    assert not installer.guard_module.layouts_current(installer.types)
    assert not installer.guard_module.providers_current()
    assert not installer.consumers.current()


@pytest.mark.parametrize('source',('constructor','create_entry','actual_consumer'))
def test_changed_actual_source_or_native_prototype_refuses_before_numerical_dispatch(created,original,source):
    actual,reference = controls07.pair(created,original)
    last = controls.checked_tick(created[0],actual,reference,.04)
    native = actual._ce_complete_runner
    before,usage = register.registry(actual),controls.usage(native)
    if source == 'constructor':
        function = created[0][3]._PROOF.module._Train.__init__
        saved = function.__code__
        function.__code__ = function.__code__.replace(co_name='foreign_constructor')
        restore = lambda:setattr(function,'__code__',saved)
    elif source == 'create_entry':
        function = native.binding.functions['train_create']
        saved = function.argtypes
        function.argtypes = []
        restore = lambda:setattr(function,'argtypes',saved)
    else:
        stamps = created[1].guard_module.stamps
        saved = stamps.CONSUMER_OWNER
        stamps.CONSUMER_OWNER = None
        restore = lambda:setattr(stamps,'CONSUMER_OWNER',saved)
    try:
        with pytest.raises((created[1].runtime.ledger.RetirementRefused,created[1].guard_module.scope.ScopeRejected)):
            if source == 'actual_consumer':
                actual.add_train_materialized('new',(10.,2.,'#123456'))
            else:
                actual.add_train('new',3,10.,8.,2.)
        assert register.registry(actual) == before and native.last_frame is last and controls.usage(native) == usage
        assert native._retirement.stage is None
    finally:
        restore()
    controls.checked_tick(created[0],actual,reference,.04)


def test_after_mapping_adoption_exception_blocks_reentry_until_real_generation_retirement(created,original):
    actual,reference = controls07.pair(created,original)
    last = controls.checked_tick(created[0],actual,reference,.04)
    native = actual._ce_complete_runner
    target = created[1].adopt.Prepared.commit.__code__
    def refuse(frame,event,value):
        if event == 'return' and frame.f_code is target:
            raise MemoryError('Injected host publication boundary after original mapping writes')
    assert sys.getprofile() is None
    sys.setprofile(refuse)
    try:
        with pytest.raises(MemoryError,match='publication boundary'):
            actual.add_train('new',3,10.,8.,2.)
    finally:
        sys.setprofile(None)
    assert native.invalid_adoption and native.last_frame is last and native._retirement.stage is None
    assert 'new' in actual._trains  # No arbitrary-OOM transaction rollback claim.
    with pytest.raises(created[1].runtime.ledger.RetirementRefused,match='adoption'):
        actual.add_train('blocked',2,10.,8.,2.)
    native.close()
    assert native.context is None and native._retirement.retired
    actual.add_train('after-close',2,10.,8.,2.)
    assert actual._ce_complete_runner is not native and not actual._ce_complete_runner.invalid_adoption
