"""Independent original cross-track anchor values and useful native work."""
import ast
import hashlib
import textwrap
from pathlib import Path

import numpy as np
import pytest

from r215_support import mounted_world, synthetic_project

from r215_support import ROOT
ORIGINAL_SHA = '92ec19ef795a154eecd56205ebd2c97873cd01d3541619ac9811a3022a760756'
REFERENCE = Path(__file__).with_name('cross_track_original.py.txt')


def original_cross_track(engine, *args, **kwargs):
    import src.engine.playback_engine as public
    raw = REFERENCE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ORIGINAL_SHA
    node = ast.parse(textwrap.dedent(raw.decode('utf-8'))).body[0]
    namespace = dict(vars(public))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 str(REFERENCE), 'exec'), namespace)
    return namespace[node.name](engine, *args, **kwargs)


@pytest.mark.parametrize('position', [0.7, 1.5, 4.0])
def test_original_public_cross_track_full512_and_deps_require_native_preparation(mounted_world, position):
    from qascade_ce import native_source_motion_clock as clock
    project = synthetic_project()
    engine, network = mounted_world(project)
    clip = project.tracks[1].clips[0]
    clip.start_time = position
    candidates = engine._find_previous_transition_clips(clip, 1, track=project.tracks[1], proj=project)
    assert candidates, 'fixture must exercise a real prior candidate'
    wanted, dependencies = original_cross_track(engine, clip, 1, project.tracks[1], project, candidates)
    before = clock.counters()
    actual, actual_dependencies = engine._build_cross_track_fade_in_anchor(
        clip, 1, project.tracks[1], project, candidates)
    np.testing.assert_array_equal(actual.view(np.uint32), wanted.view(np.uint32))
    assert actual.shape == (512,) and actual.dtype == np.float32
    assert actual_dependencies == dependencies and actual_dependencies
    after = clock.counters()
    assert after.get('_SOURCE_CLOCK_CROSS_TRACK_CNT', 0) > before.get('_SOURCE_CLOCK_CROSS_TRACK_CNT', 0), \
        'real cross-track anchor still computes numerical preparation in Python'
    assert after['_SOURCE_CLOCK_ERROR_CNT'] == before['_SOURCE_CLOCK_ERROR_CNT']
    network.stop()
