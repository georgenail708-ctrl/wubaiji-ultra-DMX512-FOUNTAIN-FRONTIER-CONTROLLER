"""C07 actual default numerical-CE coverage; new domain required, ROOT-only.

Proposed native_lt_prepare counter names are documented in DESIGN.md. A missing
domain is an assertion RED, never fabricated zero counters. These tests do not
substitute static legacy LT-table reachability for actual perclip CE work.
"""
import importlib.util
from pathlib import Path

import pytest

from r215_support import mounted_world

HERE = Path(__file__).resolve().parent
PREFIX = 'native_lt_prepare_'
FIELDS = ('requests', 'calls', 'threshold_values', 'attack_values', 'release_values',
          'brightness_values', 'semantic_errors', 'foreign_fallback', 'native_errors')


def business():
    spec = importlib.util.spec_from_file_location('_r215_c07_business_prepare', HERE / 'test_lt_original_business_draft.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def counters():
    from qascade_ce import prepared_timeline
    full = prepared_timeline.counters()
    missing = [PREFIX + field for field in FIELDS if PREFIX + field not in full]
    assert not missing, 'Ordinary LT numeric CE domain is missing: ' + repr(missing)
    return {field: full[PREFIX + field] for field in FIELDS}


def delta(before, after):
    values = {field: after[field] - before[field] for field in FIELDS}
    assert all(type(value) is int and value >= 0 for value in values.values())
    return values


@pytest.mark.parametrize('threshold,brightness,attack,release', [
    (16, 255, .11, .31), (-1, 0, float('nan'), float('inf')),
    ('0', -(10 ** 1000), -float('inf'), None),
    (10 ** 1000, 10 ** 1000, 0.0, -0.0),
    (-(10 ** 1000), None, None, None)])
def test_actual_default_four_scalar_stages_are_one_native_batch_no_python_fallback(
        mounted_world, threshold, brightness, attack, release):
    import qascade_ce
    b = business()
    oracle, project, clip, reference, engine = b.make_world(mounted_world)
    clip.light_tracking_threshold = threshold
    clip.light_tracking_max_brightness = brightness
    clip.light_tracking_attack, clip.light_tracking_release = attack, release
    before = counters()
    lt_before = (qascade_ce._LT_APPLY_CNT[0], qascade_ce._LT_APPLY_FALLBACK_CNT[0], qascade_ce._CE_EXC_CNT[0])
    expected = oracle.pe_original()(reference, clip, b.raw_frame(), 1, .2, project).copy()
    actual = engine._apply_clip_light_tracking(clip, b.raw_frame(), 1, .2, project)
    work = delta(before, counters())
    assert work == dict(requests=1, calls=1, threshold_values=1, attack_values=1,
                        release_values=1, brightness_values=1, semantic_errors=0,
                        foreign_fallback=0, native_errors=0)
    assert qascade_ce._LT_APPLY_CNT[0] == lt_before[0] + 1
    assert (qascade_ce._LT_APPLY_FALLBACK_CNT[0], qascade_ce._CE_EXC_CNT[0]) == lt_before[1:]
    assert actual.tobytes() == expected.tobytes()
    assert b.temporal(engine) == b.temporal(reference)


@pytest.mark.parametrize('field,value,prior', [
    ('light_tracking_threshold', float('nan'), (0, 0, 0, 0)),
    ('light_tracking_threshold', float('inf'), (0, 0, 0, 0)),
    ('light_tracking_attack', 10 ** 1000, (1, 0, 0, 0)),
    ('light_tracking_release', 10 ** 1000, (1, 1, 0, 0)),
    ('light_tracking_max_brightness', float('nan'), (1, 1, 1, 0))])
def test_original_caught_semantic_error_is_selected_by_c_in_first_consumed_stage(
        mounted_world, field, value, prior):
    import qascade_ce
    b = business()
    oracle, project, clip, reference, engine = b.make_world(mounted_world)
    setattr(clip, field, value)
    before = counters()
    lt_before = (qascade_ce._LT_APPLY_CNT[0], qascade_ce._LT_APPLY_FALLBACK_CNT[0], qascade_ce._CE_EXC_CNT[0])
    expected = oracle.pe_original()(reference, clip, b.raw_frame(), 1, .2, project).copy()
    actual = engine._apply_clip_light_tracking(clip, b.raw_frame(), 1, .2, project)
    work = delta(before, counters())
    assert work['requests'] == work['calls'] == work['semantic_errors'] == 1
    assert tuple(work[field] for field in ('threshold_values', 'attack_values',
                                          'release_values', 'brightness_values')) == prior
    assert work['foreign_fallback'] == work['native_errors'] == 0
    assert (qascade_ce._LT_APPLY_CNT[0], qascade_ce._LT_APPLY_FALLBACK_CNT[0],
            qascade_ce._CE_EXC_CNT[0]) == lt_before
    assert actual.tobytes() == expected.tobytes()
    assert b.temporal(engine) == b.temporal(reference)


def test_same_clip_id_runtime_metadata_edit_is_used_by_next_actual_ce_call(mounted_world):
    b = business()
    oracle, project, clip, reference, engine = b.make_world(mounted_world)
    original = oracle.pe_original()
    before = counters()
    identity = clip.id
    outputs = []
    for now, threshold, brightness, attack, release in (
            (.025, 16, 255, .11, .31), (.075, 128, 0, .01, .02),
            (.2, '0', 255, .04, .5)):
        clip.light_tracking_threshold = threshold
        clip.light_tracking_max_brightness = brightness
        clip.light_tracking_attack, clip.light_tracking_release = attack, release
        assert clip.id == identity
        expected = original(reference, clip, b.raw_frame(), 1, now, project).copy()
        actual = engine._apply_clip_light_tracking(clip, b.raw_frame(), 1, now, project)
        assert actual.tobytes() == expected.tobytes()
        assert b.temporal(engine) == b.temporal(reference)
        outputs.append(actual.tobytes())
    work = delta(before, counters())
    assert work['calls'] == work['requests'] == 3
    assert all(work[field] == 3 for field in ('threshold_values', 'attack_values',
                                             'release_values', 'brightness_values'))
    assert work['foreign_fallback'] == work['native_errors'] == work['semantic_errors'] == 0
    assert len(set(outputs)) > 1, 'Recipe did not exercise metadata output changes'
