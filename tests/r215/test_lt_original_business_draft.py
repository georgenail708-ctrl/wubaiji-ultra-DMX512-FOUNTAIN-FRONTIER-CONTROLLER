"""Actual ordinary mounted perclip LT versus archived PE; ROOT-only RED draft.

No CE pointer/producer/class patch; no table eligibility override. Original
public LT/math/oracles/expected bytes remain untouched. Compare every 512 byte,
all engine LT state/time/color and unowned channels after each actual call.
"""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from r215_support import mounted_world
from r215_support import make_stateful_project

HERE = Path(__file__).resolve().parent


def research_module(name):
    spec = importlib.util.spec_from_file_location('_r215_c07_' + name, HERE / ('lt_' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lossless(value):
    if type(value) is float:
        return ('float', value.hex())
    if isinstance(value, np.ndarray):
        return ('array', value.dtype.str, value.shape, value.tobytes())
    if type(value) is dict:
        return {key: lossless(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return (type(value).__name__, tuple(lossless(item) for item in value))
    return value


def temporal(engine):
    return {name: lossless(getattr(engine, name)) for name in (
        '_light_tracking_state', '_light_tracking_time', '_light_tracking_color_cache')}


def raw_frame():
    raw = (np.arange(512, dtype=np.uint16) % 256).astype(np.uint8)
    raw[:8] = (50, 90, 140, 200, 50, 11, 64, 230)
    return raw


def make_world(mounted_world):
    oracle = research_module('original_reference')
    oracle.verify_live_original_sources()
    project = make_stateful_project('light_tracking')
    clip = project.tracks[0].clips[0]
    clip.light_tracking_color = '#3F8BCD'
    clip.light_tracking_threshold = 16
    reference, _ = mounted_world(project)
    engine, _ = mounted_world(project)
    return oracle, project, clip, reference, engine


NUMERIC_CASES = [
    ('light_tracking_threshold', -1),
    ('light_tracking_threshold', '0'),
    ('light_tracking_threshold', 0),
    ('light_tracking_threshold', None),
    ('light_tracking_threshold', 10 ** 1000),
    ('light_tracking_threshold', -(10 ** 1000)),
    ('light_tracking_threshold', -0.9),
    ('light_tracking_threshold', 255.9),
    ('light_tracking_threshold', float('nan')),
    ('light_tracking_threshold', float('inf')),
    ('light_tracking_threshold', -float('inf')),
    ('light_tracking_max_brightness', 0),
    ('light_tracking_max_brightness', None),
    ('light_tracking_max_brightness', ''),
    ('light_tracking_max_brightness', False),
    ('light_tracking_max_brightness', 10 ** 1000),
    ('light_tracking_max_brightness', -(10 ** 1000)),
    ('light_tracking_max_brightness', float('nan')),
    ('light_tracking_max_brightness', float('inf')),
    ('light_tracking_attack', None),
    ('light_tracking_attack', 0.0),
    ('light_tracking_attack', -0.0),
    ('light_tracking_attack', float('nan')),
    ('light_tracking_attack', float('inf')),
    ('light_tracking_attack', -float('inf')),
    ('light_tracking_attack', 10 ** 1000),
    ('light_tracking_release', None),
    ('light_tracking_release', 0.0),
    ('light_tracking_release', float('nan')),
    ('light_tracking_release', float('inf')),
    ('light_tracking_release', -float('inf')),
]


@pytest.mark.parametrize('field,value', NUMERIC_CASES, ids=[field + '-' + str(i) for i, (field, _) in enumerate(NUMERIC_CASES)])
def test_actual_default_perclip_all_512_and_temporal_equal_archived_pe(mounted_world, field, value):
    oracle, project, clip, reference, engine = make_world(mounted_world)
    original = oracle.pe_original()
    setattr(clip, field, value)
    for now in (0.0, .025, .075, .2):
        raw = raw_frame()
        input_bytes = raw.tobytes()
        expected = original(reference, clip, raw.copy(), 1, now, project).copy()
        actual = engine._apply_clip_light_tracking(clip, raw, 1, now, project)
        assert actual.shape == expected.shape == (512,)
        assert actual.dtype == expected.dtype == np.dtype('uint8')
        assert actual.tobytes() == expected.tobytes(), (field, now)
        assert raw.tobytes() == input_bytes, 'Producer mutated source raw bytes'
        assert actual[8:].tobytes() == expected[8:].tobytes() == input_bytes[8:]
        assert temporal(engine) == temporal(reference), (field, now, 'LT state/time/color')


@pytest.mark.parametrize('threshold', [0, '0', -1, 10 ** 1000, -(10 ** 1000), None])
def test_public_ltparams_constructor_keeps_original_behavior_and_fields(mounted_world, threshold):
    from qascade_ce.lt_path import LtParams
    oracle = research_module('original_reference')
    args = ('pump', threshold, float('nan'), -float('inf'), False, 63, 139, 205, 0)
    expected = object.__new__(LtParams)
    oracle.public_lt_initializer()(expected, *args)
    actual = LtParams(*args)
    assert oracle.scalar_fields(actual) == oracle.scalar_fields(expected)


class ProbeScalar:
    def __init__(self, events, field, *, error=False):
        self.events, self.field, self.error = events, field, error
    def __bool__(self):
        self.events.append((self.field, 'bool'))
        return True
    def __int__(self):
        self.events.append((self.field, 'int'))
        if self.error:
            raise ValueError('C07 first conversion sentinel')
        return 16
    def __float__(self):
        self.events.append((self.field, 'float'))
        if self.error:
            raise ValueError('C07 first conversion sentinel')
        return .11


@pytest.mark.parametrize('field,error', [
    ('light_tracking_threshold', False), ('light_tracking_threshold', True),
    ('light_tracking_attack', False), ('light_tracking_attack', True),
    ('light_tracking_release', False), ('light_tracking_release', True),
    ('light_tracking_max_brightness', False), ('light_tracking_max_brightness', True)])
def test_foreign_numeric_callbacks_whole_original_once_and_stop_on_first_error(mounted_world, field, error):
    import qascade_ce
    oracle, project, clip, reference, engine = make_world(mounted_world)
    original = oracle.pe_original()
    expected_events, actual_events = [], []
    setattr(clip, field, ProbeScalar(expected_events, field, error=error))
    expected = original(reference, clip, raw_frame(), 1, .2, project).copy()
    before = qascade_ce._LT_APPLY_CNT[0]
    setattr(clip, field, ProbeScalar(actual_events, field, error=error))
    actual = engine._apply_clip_light_tracking(clip, raw_frame(), 1, .2, project)
    assert actual_events == expected_events, 'Foreign callback repeated or changed order'
    assert qascade_ce._LT_APPLY_CNT[0] == before, 'Foreign value entered ordinary LT C math'
    assert actual.tobytes() == expected.tobytes()
    assert temporal(engine) == temporal(reference)


def test_foreign_clip_descriptor_first_error_blocks_later_metadata(mounted_world):
    import qascade_ce
    from src.core.models import Clip
    oracle, project, clip, reference, engine = make_world(mounted_world)
    original = oracle.pe_original()
    wanted = ('light_tracking_enabled', 'light_tracking_source', 'light_tracking_threshold',
              'light_tracking_attack', 'light_tracking_release', 'light_tracking_max_brightness',
              'light_tracking_color')
    class ForeignClip(Clip):
        def __getattribute__(self, name):
            if name in wanted:
                object.__getattribute__(self, '_c07_events').append(name)
            if name == 'light_tracking_threshold':
                raise RuntimeError('C07 descriptor first error sentinel')
            return super().__getattribute__(name)
    def foreign(events):
        result = object.__new__(ForeignClip)
        result.__dict__.update(clip.__dict__)
        result._c07_events = events
        return result
    expected_events, actual_events = [], []
    expected = original(reference, foreign(expected_events), raw_frame(), 1, .2, project).copy()
    before = qascade_ce._LT_APPLY_CNT[0]
    actual = engine._apply_clip_light_tracking(foreign(actual_events), raw_frame(), 1, .2, project)
    assert actual_events == expected_events
    assert 'light_tracking_attack' not in actual_events
    assert qascade_ce._LT_APPLY_CNT[0] == before
    assert actual.tobytes() == expected.tobytes()
    assert temporal(engine) == temporal(reference)
