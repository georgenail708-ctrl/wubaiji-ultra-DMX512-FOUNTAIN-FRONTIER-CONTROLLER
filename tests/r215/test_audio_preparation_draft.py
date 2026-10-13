"""Independent original math first, actual native return/work second; ROOT-only."""
from contextlib import contextmanager
import ctypes
import hashlib
import importlib
from pathlib import Path
import struct
import sys
import math
import random
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = next(path for path in HERE.parents if (path / 'src/engine/audio_modulation.py').is_file())
ARCHIVES = {
    'audio_mod_processor_schema': '1ba9db8bf73a80381d8cc5211528fa55ccf2a0c69d81321c51d2e55cb55f804d',
    'audio_modulation': '4a9dfbe5f1a6f1c910936dc37fa6f71ccc9d4e8312d4183d9872600750a98cac',
    'audio_mod_envelope': 'f7624304ce986f7ce86986cf7c5111b1204237af23985499b1c0896ae7f13566',
    'audio_mod_spread': 'caa1d90f14e9563a5e3356463d5f327b3b0ba7ef4a72bcca1d9701b7c1ef0140',
    'audio_mod_processor_state': '228a138d4d12df605b73271cb13cba0b6bf6b114213d289e5b9f9c2bd38e4632',
}


@pytest.fixture
def world(qapp, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'render_lab'))
    from qascade_ce._mount_ce import _mount_ce
    mounted = _mount_ce()
    assert mounted.active and mounted.installed, mounted.to_dict()
    actual, frozen = {}, {}
    for name, expected in ARCHIVES.items():
        module = importlib.import_module('src.engine.' + name)
        actual[name] = module
        raw = (HERE / (name + '.original.py.txt')).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected
        namespace = dict(vars(module))
        exec(compile(raw, str(HERE / (name + '.original.py.txt')), 'exec'), namespace)
        frozen[name] = namespace
    processors = frozen['audio_mod_processor_schema']
    processor_state = frozen['audio_mod_processor_state']
    processor_state.update(normalize_processors=processors['normalize_processors'], processor_key=processors['processor_key'])
    state = frozen['audio_modulation']
    state.update(normalize_points=frozen['audio_mod_envelope']['normalize_points'],
                 clamp_deg=frozen['audio_mod_spread']['clamp_deg'], _processor_fields=processor_state['processor_fields'])
    return SimpleNamespace(actual=actual, frozen=frozen)


def encoded(value):
    if type(value) is float:
        return 'float', struct.pack('=d', value)
    if type(value) is dict:
        return 'dict', tuple((encoded(key), encoded(item)) for key, item in value.items())
    if type(value) in (tuple, list):
        return type(value).__name__, tuple(encoded(item) for item in value)
    return type(value).__name__, value


def observed(call):
    try:
        return encoded(call()), None
    except Exception as error:
        return None, (type(error), str(error))


def counters():
    module = sys.modules.get('qascade_ce.native_audio_prepare')
    return {} if module is None else module.counters()


@contextmanager
def native_returns(operation):
    module = sys.modules.get('qascade_ce.native_audio_prepare')
    function = None if module is None else vars(module).get(operation + '_FUNCTION')
    calls = []
    if not isinstance(function, ctypes._CFuncPtr):
        yield calls
        return
    previous = getattr(function, 'errcheck', None)
    def observe(result, actual, arguments):
        calls.append((actual, result))
        return result if previous is None else previous(result, actual, arguments)
    function.errcheck = observe
    try:
        yield calls
    finally:
        if previous is None:
            del function.errcheck
        else:
            function.errcheck = previous


def numerical_case(world, selection, raw, operation, work):
    module, function = selection
    expected = observed(lambda: world.frozen[module][function](raw))
    before = counters()
    with native_returns(operation) as calls:
        actual = observed(lambda: getattr(world.actual[module], function)(raw))
    assert actual == expected, 'Original values, types, order, float bits or original errors changed'
    assert calls and all(result == 0 for _function, result in calls), 'Ordinary normalization never reached a genuine C return'
    after = counters()
    assert after.get(work, 0) > before.get(work, 0), 'No successful native numerical work'
    for key in ('errors', 'fallbacks', 'foreign', 'pending'):
        assert after.get(key, 0) == before.get(key, 0), key


PROCESSORS = (
    [{'kind': 'slice'}], [{'kind': 'accent'}], [{'kind': 'motion'}], [{'kind': 'color'}],
    [{'kind': 'slice', 'wet': 'bad', 'params': {'division': '2.5', 'duty': None, 'phase': '-0.5', 'edge': b'.1'}}],
    [{'kind': 'accent', 'wet': float('nan'), 'params': {'floor': float('inf'), 'curve': float('-inf'), 'mode': None}}],
    [{'kind': 'motion', 'wet': -0., 'params': {'min_scale': .8, 'max_scale': .2}}],
    [{'kind': 'slice', 'params': {'division': 3.5}}, {'kind': 'color', 'params': {'hue_deg': '180.25'}}],
    [{'kind': 'accent', 'enabled': False, 'targets': [], 'source': 'bad', 'params': {'floor': True, 'curve': False}}],
    [{'kind': 'slice', 'wet': 1e-310, 'params': {'duty': .049999999999999996, 'edge': -.0}}],
    tuple({'kind': 'slice', 'id': str(index), 'params': {'division': index + .5}} for index in range(33)),
)


@pytest.mark.parametrize('raw', PROCESSORS)
def test_processors_original_bits_types_order_then_actual_ce(world, raw):
    numerical_case(world, ('audio_mod_processor_schema', 'normalize_processors'), raw, 'PROCESSORS', 'processor_values')


STATES = (
    None, {}, {'amount': 'bad', 'attack': None, 'release': b'.125'},
    {'amount': float('nan'), 'attack': float('nan'), 'release': float('inf'), 'spread_deg': float('nan')},
    {'amount': -0., 'attack': -0., 'release': -0., 'phase_from_deg': '360', 'phase_to_deg': float('-inf')},
    {'amount': True, 'attack': False, 'release': -2, 'spread_deg': 361},
    {'clips': [{'start': 3.00005, 'end': 4.00015}, {'start': -.0, 'end': .05}, {'start': .5, 'end': .54999}]},
    {'clips': [{'start': float('nan'), 'end': 1}, {'start': 0, 'end': float('inf')}, {'start': 'bad', 'end': 2}]},
    {'clips': [{'start': 0, 'end': 1, 'wave_freq': -0., 'fade_in': float('nan'), 'fade_out': float('inf')}]},
    {'clips': [{'start': 0, 'end': 2, 'wave_freq': 'bad', 'fade_in': 1.00005, 'fade_out': 1.00015,
                'points': [[.50005, .12345, .4565], [.5, .7], [.5, .3], [-0., -0., -0.]]}]},
    {'clips': [{'start': 1e20, 'end': 1.1e20, 'fade_in': 1e308, 'fade_out': 1e-310}]},
    {'processor_mode': 'chain', 'processors': [{'kind': 'motion', 'params': {'min_scale': .8, 'max_scale': .2}}]},
    {'sources': [{'id': 's', 'phase': float('nan')}], 'rules': [{'params': {'freq': 2.}}],
     'draw_graph': {'nodes': [{'x': 12.25, 'y': -0., 'params': {'depth': .8}, 'ch_types': ['pump']}], 'edges': []}},
)


@pytest.mark.parametrize('raw', STATES)
def test_state_and_nested_clip_points_preserve_original_then_actual_ce(world, raw):
    numerical_case(world, ('audio_modulation', 'normalize_state'), raw, 'STATE', 'state_values')


POINTS = (
    [[0, 0], [1, 1]], [[-.0, -.0, -.0]],
    [[.50005, .12345, .4565], [.50015, .12355, -.4565]],
    [[.5, .1], [.5, .2, .3], [.5, .9], [.5, .7]],
    [[.50004, 1], [.50003, 0], [.50002, .5]],
    [{'t': b'.25', 'v': 'bad', 'c': True}, [False, True, None]],
    [[float('nan'), .4], [.5, float('nan')], [.2, .4, float('nan')], [0, 1]],
    [[float('-inf'), float('inf'), float('-inf')]],
    [[1e-310, 1e-310, 1e-8]], [[.125, .875, -.0005]],
)


@pytest.mark.parametrize('raw', POINTS)
def test_points_quantization_stable_sort_first_last_dedup_then_actual_ce(world, raw):
    numerical_case(world, ('audio_mod_envelope', 'normalize_points'), raw, 'POINTS', 'point_rows')


@pytest.mark.parametrize('degree', [None, -0., float('nan'), float('inf'), -1, 360, b'12.25', 'bad'])
def test_default_degree_supplier_is_native_numeric(world, degree):
    numerical_case(world, ('audio_mod_spread', 'clamp_deg'), degree, 'STATE', 'state_values')


ERRORS = (
    ('audio_mod_processor_schema', 'normalize_processors', [{'kind': 'slice', 'wet': 1 << 4096}], 'PROCESSORS'),
    ('audio_mod_processor_schema', 'normalize_processors', [{'kind': 'slice', 'params': {'division': 1 << 4096}, 'wet': 1 << 8192}], 'PROCESSORS'),
    ('audio_modulation', 'normalize_state', {'amount': 1 << 4096, 'attack': 1 << 8192}, 'STATE'),
    ('audio_modulation', 'normalize_state', {'attack': 1 << 4096, 'release': 1 << 8192}, 'STATE'),
    ('audio_modulation', 'normalize_state', {'target_ch_types': 7, 'amount': 1 << 4096}, 'STATE'),
    ('audio_modulation', 'normalize_state', {'clips': [{'start': 0, 'end': 1, 'wave_freq': 1 << 4096, 'fade_in': 1 << 8192}]}, 'STATE'),
    ('audio_mod_envelope', 'normalize_points', [[float('nan'), 1 << 4096, 1 << 8192]], 'POINTS'),
    ('audio_mod_envelope', 'normalize_points', [[1 << 4096, 1 << 8192]], 'POINTS'),
)


@pytest.mark.parametrize('module,function,raw,operation', ERRORS)
def test_plain_semantic_errors_keep_first_error_then_actual_native_selector(world, module, function, raw, operation):
    expected = observed(lambda: world.frozen[module][function](raw))
    before = counters()
    with native_returns(operation) as calls:
        actual = observed(lambda: getattr(world.actual[module], function)(raw))
    assert expected[1] is not None and actual == expected
    assert calls and all(result == 0 for _function, result in calls), 'Ordinary semantic error never reached native consumed selector'
    assert counters().get('semantic_errors', 0) == before.get('semantic_errors', 0) + 1
    for key in ('errors', 'fallbacks', 'foreign', 'pending'):
        assert counters().get(key, 0) == before.get(key, 0), key


@pytest.mark.parametrize('raw', [
    {'clips': [{'start': 'bad', 'end': 1 << 4096, 'wave_freq': 1 << 8192}]},
    {'clips': [{'start': float('inf'), 'end': 2, 'wave_freq': 1 << 4096}]},
    {'clips': [{'start': 0., 'end': .0499, 'wave_freq': 1 << 4096, 'points': [[1 << 8192, 0]]}]},
])
def test_invalid_clip_does_not_consume_later_numeric_errors(world, raw):
    numerical_case(world, ('audio_modulation', 'normalize_state'), raw, 'STATE', 'state_values')


@pytest.mark.parametrize('module,function,path', [
    ('audio_mod_processor_schema', 'normalize_processors', 'wet'),
    ('audio_modulation', 'normalize_state', 'amount'),
    ('audio_mod_envelope', 'normalize_points', 'point'),
])
def test_custom_float_protocol_is_original_once_and_zero_c(world, module, function, path):
    class Number:
        def __init__(self):
            self.calls = 0
        def __float__(self):
            self.calls += 1
            return .375
    first, second = Number(), Number()
    def payload(number):
        if path == 'wet':
            return [{'kind': 'accent', 'wet': number}]
        return {'amount': number} if path == 'amount' else [[number, .5]]
    expected = observed(lambda: world.frozen[module][function](payload(first)))
    operation = {'wet': 'PROCESSORS', 'amount': 'STATE', 'point': 'POINTS'}[path]
    with native_returns(operation) as calls:
        actual = observed(lambda: getattr(world.actual[module], function)(payload(second)))
    assert actual == expected and first.calls == second.calls == 1 and calls == []


def test_graph_and_rule_copies_preserve_original_non_aliasing(world):
    raw = STATES[-1]
    actual = world.actual['audio_modulation'].normalize_state(raw)
    expected = world.frozen['audio_modulation']['normalize_state'](raw)
    assert encoded(actual) == encoded(expected)
    assert actual['draw_graph']['nodes'][0] is not raw['draw_graph']['nodes'][0]
    assert actual['draw_graph']['nodes'][0]['params'] is not raw['draw_graph']['nodes'][0]['params']
    assert actual['draw_graph']['nodes'][0]['ch_types'] is not raw['draw_graph']['nodes'][0]['ch_types']
    assert actual['sources'][0] is not raw['sources'][0]
    assert actual['rules'][0] is not raw['rules'][0]
    assert actual['rules'][0]['params'] is raw['rules'][0]['params']


def test_numpy_error_policy_callback_is_not_changed_by_schema_preparation(world):
    previous, callback = np.geterr(), np.geterrcall()
    events = []
    try:
        np.seterr(over='call', invalid='raise')
        np.seterrcall(lambda message, flags: events.append((message, flags)))
        raw = STATES[3]
        expected = observed(lambda: world.frozen['audio_modulation']['normalize_state'](raw))
        before = np.geterr()
        actual = observed(lambda: world.actual['audio_modulation'].normalize_state(raw))
        assert actual == expected and np.geterr() == before and events == []
    finally:
        np.seterr(**previous)
        np.seterrcall(callback)


def decimal_adversaries():
    values = [0., -0., 5e-324, -5e-324, sys.float_info.min, -sys.float_info.min,
              sys.float_info.max, -sys.float_info.max, 1e308, -1e308]
    for digits, boundary in ((3, float(2**43)), (4, float(2**39))):
        for value in (boundary, -boundary, .5 * 10**-digits, -.5 * 10**-digits, .0005, .00005, 1.00005):
            values.extend((math.nextafter(value, -math.inf), value, math.nextafter(value, math.inf)))
    generator = random.Random(2150601)
    while len(values) < 2500:
        value = struct.unpack('=d', struct.pack('=Q', generator.getrandbits(64)))[0]
        if math.isfinite(value):
            values.append(value)
    return values


def test_decimal_four_digits_property_2500_full_bits_in_real_clip_fades(world):
    raw = {'clips': [{'start': 0., 'end': 1., 'fade_in': value, 'fade_out': abs(value)}
                     for value in decimal_adversaries()]}
    numerical_case(world, ('audio_modulation', 'normalize_state'), raw, 'STATE', 'round_values')


def test_decimal_three_and_four_digits_property_2500_full_bits_in_real_points(world):
    generator = random.Random(2150602)
    points = [[generator.random(), generator.random(), generator.uniform(-1., 1.)] for _ in range(2470)]
    for value in (.0005, -.0005, .00005, -.00005, .9995, -.9995, 1e-9, -1e-9, -0., 0.):
        points.extend([[.12345, .54325, math.nextafter(value, -math.inf)], [.12355, .54335, value],
                       [.12365, .54345, math.nextafter(value, math.inf)]])
    assert len(points) == 2500
    numerical_case(world, ('audio_mod_envelope', 'normalize_points'), points, 'POINTS', 'round_values')


@pytest.mark.parametrize('degree', [0., -0., float('nan'), float('inf'), .0005, 1e-310])
def test_actual_state_degree_default_supplier_and_standalone_supplier_agree(world, degree):
    raw = dict(spread_deg=degree, phase_from_deg=degree, phase_to_deg=degree)
    expected = world.frozen['audio_modulation']['normalize_state'](raw)
    actual = world.actual['audio_modulation'].normalize_state(raw)
    standalone = world.actual['audio_mod_spread'].clamp_deg(degree)
    assert encoded(actual) == encoded(expected)
    assert all(encoded(actual[name]) == encoded(standalone) for name in raw)


@pytest.mark.parametrize('case', [
    ('audio_mod_processor_schema', 'normalize_processors', 'PROCESSORS', [{'kind': 'accent', 'wet': .75}], '_numeric'),
    ('audio_modulation', 'normalize_state', 'STATE', {'amount': .75}, '_clamp01'),
    ('audio_mod_envelope', 'normalize_points', 'POINTS', [[.5, .75]], '_clamp01'),
])
def test_late_same_helper_code_keeps_current_foreign_original_and_zero_ce(world, monkeypatch, case):
    module, function, operation, raw, helper = case
    def changed(*args):
        return 0.
    actual_helper = getattr(world.actual[module], helper)
    monkeypatch.setattr(actual_helper, '__code__', changed.__code__)
    world.frozen[module][helper] = actual_helper
    expected = observed(lambda: world.frozen[module][function](raw))
    with native_returns(operation) as calls:
        actual = observed(lambda: getattr(world.actual[module], function)(raw))
    assert actual == expected and calls == []


def test_late_round_shadow_is_foreign_whole_original_once(world, monkeypatch):
    module = world.actual['audio_mod_envelope']
    events = []
    original = round
    def shadow(value, digits):
        events.append((value, digits))
        return original(value, digits)
    monkeypatch.setattr(module, 'round', shadow, raising=False)
    world.frozen['audio_mod_envelope']['round'] = shadow
    raw = [[.12345, .54325, .4565]]
    expected = observed(lambda: world.frozen['audio_mod_envelope']['normalize_points'](raw))
    old_events, events[:] = list(events), []
    with native_returns('POINTS') as calls:
        actual = observed(lambda: module.normalize_points(raw))
    assert actual == expected and events == old_events and calls == []
