"""Unrun R215-C02 business RED: exact original answers before native-work claims.

ROOT alone may promote/run. Existing oracles and product files are unchanged.
"""
from contextlib import contextmanager
import ctypes
import hashlib
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import warnings

import numpy as np
import pytest

from src.core.models import Clip, FixtureDefinition, FixtureInstance, Project

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'src/engine/clip_output_filters.py').is_file())
ARCHIVE = Path(__file__).with_name('original_filter_functions.py.txt')
ARCHIVE_SHA = '19354a9aa7e8a4d6649775e05abbe554e35de6bfa485851f6441653174ead4d2'


@pytest.fixture
def world(qapp, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'render_lab'))
    from qascade_ce._mount_ce import _mount_ce
    from src.engine import clip_output_filters as public
    mounted = _mount_ce()
    assert mounted.active and mounted.installed, mounted.to_dict()
    raw = ARCHIVE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ARCHIVE_SHA
    namespace = dict(vars(public))
    exec(compile(raw, str(ARCHIVE), 'exec'), namespace)
    from PyQt6 import sip
    from src.core.project_manager import ProjectManager
    from src.engine.playback_engine import PlaybackEngine
    from src.network.network_manager import NetworkManager
    owners = []
    def owner(project):
        previous = ProjectManager._instance
        manager = ProjectManager()
        ProjectManager._instance = previous
        project.setParent(manager)
        manager._project = project
        network = NetworkManager(manager)
        engine = PlaybackEngine(network, manager, parent=manager)
        owners.append((manager, engine, network))
        return engine
    try:
        yield SimpleNamespace(public=public, reference=namespace, owner=owner)
    finally:
        for manager, engine, network in owners:
            engine.stop()
            engine.stop_gen_thread()
            network.stop()
            sip.delete(manager)


def bits(values):
    return b''.join(struct.pack('=d', float(value)) for value in values)


def equal_params(actual, expected):
    assert type(actual) is type(expected) is tuple
    assert tuple(type(value) for value in actual) == tuple(type(value) for value in expected)
    assert bits(actual) == bits(expected)


def counts():
    module = sys.modules.get('qascade_ce.native_filter_prepare')
    return {} if module is None else module.counters()


@contextmanager
def native_calls(operation):
    module = sys.modules.get('qascade_ce.native_filter_prepare')
    function = None if module is None else getattr(module, operation + '_FUNCTION', None)
    calls = []
    if not isinstance(function, ctypes._CFuncPtr):
        yield calls
        return
    previous = getattr(function, 'errcheck', None)
    def observe(result, actual, args):
        calls.append((result, args))
        return result if previous is None else previous(result, actual, args)
    function.errcheck = observe
    try:
        yield calls
    finally:
        if previous is None:
            del function.errcheck
        else:
            function.errcheck = previous


def require_native(before, after, calls, work):
    assert calls and all(code == 0 for code, _args in calls), 'Ordinary math never reached a genuine C return'
    assert after.get(work, 0) > before.get(work, 0), 'No successful native mathematical work'
    for key in ('errors', 'fallbacks', 'foreign', 'pending'):
        assert after.get(key, 0) == before.get(key, 0), key


PARAMETERS = ((1., 0., 1.), (.35, 72., .8), (-0., -0., -0.),
              (float('nan'), float('nan'), float('nan')),
              (float('inf'), float('inf'), float('-inf')),
              (False, True, False), (-7, -1, 9), ('0.25', '80', b'.5'),
              ('bad', None, b'bad'), (1e-310, -1e-310, .999999))


@pytest.mark.parametrize('values', PARAMETERS)
def test_ordinary_params_preserve_bits_types_then_compute_in_ce(world, values):
    clip = Clip(id='r215-params')
    clip.motor_limit_scale, clip.motor_speed_limit_dps, clip.pump_master_scale = values
    expected = world.reference['clip_filter_params'](clip)
    before = counts()
    with native_calls('PARAMETERS') as calls:
        actual = world.public.clip_filter_params(clip)
    equal_params(actual, expected)
    assert world.public.filters_active(clip) == world.reference['filters_active'](clip)
    require_native(before, counts(), calls, 'parameter_rows')


def project_for(ranges):
    definitions, fixtures = [], []
    for index, row in enumerate(ranges):
        identity = 'r215-ranges-' + str(index)
        definitions.append(FixtureDefinition(id=identity, channel_count=3,
            channels=['motor_x', 'motor_y', 'motor_z'], motor_ranges=row))
        fixtures.append(FixtureInstance(id=identity, def_id=identity, universe=1,
                                       start_channel=1 + 3 * index))
    return Project(fixture_defs=definitions, fixtures=fixtures)


def observe(call, policy='always'):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter(policy, RuntimeWarning)
        try:
            value, error = call(), None
        except Exception as exception:
            value, error = None, (type(exception), str(exception))
        return value, error, [(entry.category, str(entry.message)) for entry in caught]


def equal_response(actual, expected):
    assert actual[1:] == expected[1:]
    if expected[0] is None:
        assert actual[0] is None
        return
    assert actual[0].keys() == expected[0].keys()
    for key in expected[0]:
        assert bits((actual[0][key],)) == bits((expected[0][key],))


RANGES = ([], [{'motor_x': (0., 180.)}],
          [{'motor_x': (0., value)} for value in (30., 360., 120.)],
          [{'motor_x': (0., value)} for value in (30., 360., 120., 240.)],
          [{'motor_x': (-10., 10.), 'motor_y': (80., -80.)}],
          [{'motor_x': (float('nan'), 180.), 'motor_z': (0., 1.)}],
          [{'motor_x': (0., float('inf'))}],
          [{'motor_x': (0., 1e308)}, {'motor_x': (0., 1e308)}],
          [{'motor_x': None, 'motor_y': (1.,)}, {'motor_x': ('bad', 'value')}],
          [{'motor_x': ('0', b'120'), 'motor_y': (False, 2)}])


@pytest.mark.parametrize('ranges', RANGES)
def test_ordinary_span_median_warning_bits_and_real_c_work(world, ranges):
    project = project_for(ranges)
    old_owner, owner = SimpleNamespace(), world.owner(project)
    expected = observe(lambda: world.reference['universe_deg_spans'](old_owner, 1, project))
    before = counts()
    with native_calls('SPANS') as calls:
        actual = observe(lambda: world.public.universe_deg_spans(owner, 1, project))
    equal_response(actual, expected)
    if ranges:
        require_native(before, counts(), calls, 'span_rows')
    else:
        assert counts().get('errors', 0) == before.get('errors', 0)


def test_span_cache_identity_patch_revision_and_replaced_lists(world):
    project = project_for([{'motor_x': (0., 180.)}])
    owner, old_owner = world.owner(project), SimpleNamespace()
    expected = world.reference['universe_deg_spans'](old_owner, 1, project)
    before = counts()
    with native_calls('SPANS') as calls:
        actual = world.public.universe_deg_spans(owner, 1, project)
    equal_response((actual, None, []), (expected, None, []))
    require_native(before, counts(), calls, 'span_rows')
    warm = counts()
    with native_calls('SPANS') as warm_calls:
        assert world.public.universe_deg_spans(owner, 1, project) is actual
    assert warm_calls == [] and counts().get('span_rows', 0) == warm.get('span_rows', 0)
    project.fixture_defs[0].motor_ranges['motor_x'] = (0., 90.)
    project.bump_patch_rev()
    changed = world.public.universe_deg_spans(owner, 1, project)
    expected = world.reference['universe_deg_spans'](old_owner, 1, project)
    equal_response((changed, None, []), (expected, None, []))
    assert changed is not actual and changed['motor_x'] == 90.
    project.fixtures = list(project.fixtures)
    assert world.public.universe_deg_spans(owner, 1, project) is not changed


@pytest.mark.parametrize('policy', ['always', 'error'])
def test_even_median_finite_overflow_matches_original_warning_and_no_early_cache(world, policy):
    project = project_for([{'motor_x': (0., 1e308)}, {'motor_x': (0., 1e308)}])
    actual_owner, original_owner = world.owner(project), SimpleNamespace()
    expected = observe(lambda: world.reference['universe_deg_spans'](original_owner, 1, project), policy)
    actual = observe(lambda: world.public.universe_deg_spans(actual_owner, 1, project), policy)
    equal_response(actual, expected)
    if expected[1] is not None:
        assert not getattr(actual_owner, '_clip_fade_geometry_cache', {})


@pytest.mark.parametrize('field', ['motor_limit_scale', 'motor_speed_limit_dps', 'pump_master_scale'])
def test_custom_float_protocol_runs_original_once_and_keeps_first_exception(world, field):
    class Number:
        def __init__(self):
            self.calls = 0
        def __float__(self):
            self.calls += 1
            return .375
    original, actual = Clip(id='original'), Clip(id='actual')
    old_value, value = Number(), Number()
    setattr(original, field, old_value)
    setattr(actual, field, value)
    expected = world.reference['clip_filter_params'](original)
    with native_calls('PARAMETERS') as calls:
        result = world.public.clip_filter_params(actual)
    equal_params(result, expected)
    assert old_value.calls == value.calls == 1 and calls == []


@pytest.mark.parametrize('field', ['motor_limit_scale', 'motor_speed_limit_dps', 'pump_master_scale'])
def test_huge_builtin_integer_keeps_original_overflow(world, field):
    clip = Clip(id='huge')
    setattr(clip, field, 1 << 4096)
    expected = observe(lambda: world.reference['clip_filter_params'](clip))
    actual = observe(lambda: world.public.clip_filter_params(clip))
    assert expected[1] is not None
    equal_response(actual, expected)


@pytest.mark.parametrize('error_policy', ['ignore', 'raise', 'call'])
def test_nondefault_numpy_median_policy_retains_warning_or_callback_once(world, error_policy):
    project = project_for([{'motor_x': (0., 1e308)}, {'motor_x': (0., 1e308)}])
    previous, previous_callback = np.geterr(), np.geterrcall()
    events = []
    try:
        np.seterr(over=error_policy)
        np.seterrcall(lambda message, flag: events.append((message, flag)))
        expected = observe(lambda: world.reference['universe_deg_spans'](SimpleNamespace(), 1, project))
        old_events, events[:] = list(events), []
        owner = world.owner(project)
        with native_calls('SPANS') as calls:
            actual = observe(lambda: world.public.universe_deg_spans(owner, 1, project))
        equal_response(actual, expected)
        assert events == old_events and calls == []
    finally:
        np.seterr(**previous)
        np.seterrcall(previous_callback)
