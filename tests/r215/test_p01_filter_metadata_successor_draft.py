"""Additional unchanged-output/currentness controls; ROOT alone runs/promotes."""
import pytest
from src.core.models import Clip

from test_filter_preparation_draft import world, counts, native_calls, equal_params, observe, require_native


def guard_module():
    from qascade_ce import native_filter_prepare_guard
    return native_filter_prepare_guard


def test_successor_preserves_original_body_and_actual_native_math(world):
    from qascade_ce import native_filter_prepare_owner as owner
    from qascade_ce.native_filter_prepare_signatures import NEW_EXPECTED
    from qascade_ce.native_filter_prepare_successor_signatures import SUCCESSOR_EXPECTED
    guard = guard_module()
    assert guard.code_digest(guard.Guard.current_original) == NEW_EXPECTED['native_filter_prepare_guard:Guard.current']
    assert guard.code_digest(guard.Guard.current) == SUCCESSOR_EXPECTED['native_filter_prepare_guard:Guard.current']
    assert owner._OWNER.guard.current_original() and owner._OWNER.guard.current()
    clip = Clip(id='p01-native-successor',motor_limit_scale=.375,motor_speed_limit_dps=81.,pump_master_scale=.625)
    expected = world.reference['clip_filter_params'](clip)
    before = counts()
    with native_calls('PARAMETERS') as calls:
        equal_params(world.public.clip_filter_params(clip),expected)
    require_native(before,counts(),calls,'parameter_rows')


@pytest.mark.parametrize('name', ['function_current_successor','_plain_metadata_current',
    'Guard.current_original','_new_current_successor'])
@pytest.mark.parametrize('kind', ['alias','code','defaults','keyword'])
def test_new_successor_metadata_change_vetoes_before_foreign_execution(world,monkeypatch,name,kind):
    guard = guard_module()
    parts = name.split('.')
    target = guard if len(parts) == 1 else guard.Guard
    function = vars(target)[parts[-1]]
    def forbidden(*args):
        raise AssertionError('A foreign successor ran before its retained metadata admission')
    if kind == 'alias':
        monkeypatch.setattr(target,parts[-1],forbidden)
    elif kind == 'code':
        monkeypatch.setattr(function,'__code__',forbidden.__code__)
    elif kind == 'defaults':
        monkeypatch.setattr(function,'__defaults__',(object(),))
    else:
        monkeypatch.setattr(function,'__kwdefaults__',{'foreign':object()})
    clip = Clip(id='p01-late-successor',motor_limit_scale=.375,motor_speed_limit_dps=81.,pump_master_scale=.625)
    expected = world.reference['clip_filter_params'](clip)
    before = counts()
    with native_calls('PARAMETERS') as calls:
        equal_params(world.public.clip_filter_params(clip),expected)
    assert calls == []
    assert counts()['foreign'] > before['foreign']


@pytest.mark.parametrize('changed', ['none','function','code','defaults','keyword'])
def test_none_keyword_successor_has_original_identity_vetoes(world,monkeypatch,changed):
    guard = guard_module()
    def function(value):
        return value
    captured = guard.function_metadata(function)
    actual = function
    if changed == 'function':
        def other(value):
            return value
        actual = other
    elif changed == 'code':
        monkeypatch.setattr(function,'__code__',(lambda value:None).__code__)
    elif changed == 'defaults':
        monkeypatch.setattr(function,'__defaults__',(object(),))
    elif changed == 'keyword':
        monkeypatch.setattr(function,'__kwdefaults__',{'other':object()})
    assert guard.function_current_successor(actual,captured) is guard.function_current(actual,captured)
    assert guard.function_current_successor(actual,captured) is (changed == 'none')


@pytest.mark.parametrize('captured_keyword', [False,True])
@pytest.mark.parametrize('raises', [False,True])
def test_non_none_keyword_keeps_original_items_callback_and_exception_route(world,captured_keyword,raises):
    guard = guard_module()
    events = []
    class Keywords(dict):
        def items(self):
            events.append('items')
            if raises:
                raise RuntimeError('original keyword items failure')
            return super().items()
    def function(value):
        return value
    keyword = Keywords(other=object())
    if captured_keyword:
        function.__kwdefaults__ = keyword
        # Capture without invoking the deliberately exceptional late provider.
        captured = (function,function.__code__,None,keyword,tuple(dict.items(keyword)))
    else:
        captured = guard.function_metadata(function)
        function.__kwdefaults__ = keyword
    events.clear()
    expected = observe(lambda:guard.function_current(function,captured))
    original_events = events[:]
    events.clear()
    actual = observe(lambda:guard.function_current_successor(function,captured))
    assert actual == expected
    assert events == original_events == ['items']


def test_currentness_is_checked_again_on_the_next_public_call(world,monkeypatch):
    guard = guard_module()
    first = Clip(id='p01-first',motor_limit_scale=.375)
    with native_calls('PARAMETERS') as calls:
        equal_params(world.public.clip_filter_params(first),world.reference['clip_filter_params'](first))
    assert calls and all(result == 0 for result,_ in calls)
    def forbidden(*args):
        raise AssertionError('Previous public-call admission was reused after a source change')
    monkeypatch.setattr(guard._plain_metadata_current,'__code__',forbidden.__code__)
    second = Clip(id='p01-second',motor_limit_scale=.625)
    with native_calls('PARAMETERS') as calls:
        equal_params(world.public.clip_filter_params(second),world.reference['clip_filter_params'](second))
    assert calls == []
