"""Independent two-ISA raw C numeric qualification; ROOT alone executes."""
import ctypes as ct
import importlib
import importlib.util
import hashlib
import math
from pathlib import Path
import random
import struct
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
ROOT = next(path for path in HERE.parents if (path / 'render_lab/csrc/ce_engine.c').is_file())


def candidate_module(name):
    full = 'qascade_ce.' + name
    existing = sys.modules.get(full)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(full, ROOT / 'render_lab/qascade_ce' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=('sse2', 'avx2'))
def numeric(request, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'render_lab'))
    from qascade_ce.ctypes_api import apply_semantic_aliases
    candidate_module('native_audio_prepare_types')
    api = candidate_module('native_audio_prepare')
    tape = candidate_module('native_audio_prepare_tape')
    dll = ct.CDLL(str(ROOT / 'render_lab/qascade_ce' / ('ce_engine_' + request.param + '.dll')))
    apply_semantic_aliases(dll)
    return SimpleNamespace(api=api, tape=tape.Tape, binding=api.prepare_binding(SimpleNamespace(dll=dll)))


def bits(values):
    return b''.join(struct.pack('=d', value) for value in values)


def raw_cases():
    examples = [0., -0., 5e-324, -5e-324, sys.float_info.min, -sys.float_info.min,
                sys.float_info.max, -sys.float_info.max, float('inf'), float('-inf'), float('nan')]
    for raw in (0x7ff0000000000001, 0xfff0000000000001, 0x7ff8123456789abc, 0xfff8123456789abc):
        examples.append(struct.unpack('=d', struct.pack('=Q', raw))[0])
    for value in (.0005, -.0005, .00005, -.00005, 1.00005, .12345, float(2**39), -float(2**39), float(2**43), -float(2**43)):
        examples.extend((math.nextafter(value, -math.inf), value, math.nextafter(value, math.inf)))
    generator = random.Random(215060203)
    while len(examples) < 2500:
        examples.append(struct.unpack('=d', struct.pack('=Q', generator.getrandbits(64)))[0])
    values = [value for value in examples for _digits in (3, 4)]
    digits = [digit for _value in examples for digit in (3, 4)]
    return values, digits


def test_actual_c_round_5000_full_binary64_inputs_equal_current_cpython(numeric):
    values, digits = raw_cases()
    expected = tuple(round(value, digit) for value, digit in zip(values, digits))
    before = numeric.api.counters()
    function = numeric.binding.functions['ROUND']
    assert isinstance(function, ct._CFuncPtr)
    returns = []
    def observe(result, actual, arguments):
        returns.append((actual, result))
        return result
    function.errcheck = observe
    try:
        actual = numeric.api.raw_round(numeric.binding, values, digits)
    finally:
        del function.errcheck
    assert len(values) == 5000 and bits(actual) == bits(expected)
    assert returns == [(function, 0)]
    after = numeric.api.counters()
    assert after['round_values'] == before['round_values'] + 5000
    assert after['successes'] == before['successes'] + 1 and after['errors'] == before['errors']
    assert after['pending'] == before['pending']


def test_point_batch_uses_original_signed_zero_round_sort_and_first_last(numeric):
    tape = numeric.tape()
    rows = [[-0., -0., -0.], [.50005, .12345, .4565], [.5, .7, 1e-8], [.5, .3, 0.]]
    for row in rows:
        tape.add('POINT', row, (0., 0., 0., 0))
    group = tape.group('POINT_SORT', 0)
    archive = HERE / 'audio_mod_envelope.original.py.txt'
    raw = archive.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'f7624304ce986f7ce86986cf7c5111b1204237af23985499b1c0896ae7f13566'
    namespace = dict(math=math)
    exec(compile(raw, str(archive), 'exec'), namespace)
    expected = namespace['normalize_points'](rows)
    result = numeric.api.execute(numeric.binding, 'POINTS', tape)
    actual = [list(result.row(index)[:3 if result.flags[index] == 3 else 2]) for index in result.ordered(group)]
    assert [bits(row) for row in actual] == [bits(row) for row in expected]


def test_dropped_clip_skips_later_semantic_error_and_keeps_native_report(numeric):
    tape = numeric.tape()
    header = tape.add('CLIP', ('bad', 1 << 4096), (.05, 0., 0., 0))
    tape.error(AssertionError('Unconsumed child error'))
    tape.steps[header].skip_to = len(tape.steps)
    group = tape.group('CLIP_SORT', 0)
    before = numeric.api.counters()
    result = numeric.api.execute(numeric.binding, 'STATE', tape)
    assert result.ordered(group) == ()
    assert numeric.api.counters()['clip_rows'] == before['clip_rows'] + 1
    assert numeric.api.counters()['semantic_errors'] == before['semantic_errors']


def test_nan_point_consumes_later_original_error_before_discard(numeric):
    tape = numeric.tape()
    tape.add('POINT', (float('nan'), 1 << 4096, 0.), (0., 0., 0., 0))
    expected = tape.errors[0]
    before = numeric.api.counters()
    with pytest.raises(OverflowError) as caught:
        numeric.api.execute(numeric.binding, 'POINTS', tape)
    assert caught.value is expected
    assert numeric.api.counters()['semantic_errors'] == before['semantic_errors'] + 1
    assert numeric.api.counters()['errors'] == before['errors']
