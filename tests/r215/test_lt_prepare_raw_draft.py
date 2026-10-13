"""Independent actual DLL both-ISA bytes/invalid-no-write C07, ROOT only."""
import ctypes as ct
import importlib.util
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
ROOT = next(path for path in HERE.parents if (path / 'render_lab/csrc/ce_engine.c').is_file())


def module(name):
    full = 'qascade_ce.' + name
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, ROOT / 'render_lab/qascade_ce' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    sys.modules[full] = result
    spec.loader.exec_module(result)
    return result


@pytest.fixture(params=('sse2', 'avx2'))
def raw(request, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'render_lab'))
    from qascade_ce.ctypes_api import apply_semantic_aliases
    types = module('native_lt_prepare_types')
    metadata = module('native_lt_prepare_metadata')
    api = module('native_lt_prepare')
    dll = ct.CDLL(str(ROOT / 'render_lab/qascade_ce' / ('ce_engine_' + request.param + '.dll')))
    apply_semantic_aliases(dll)
    return SimpleNamespace(api=api, types=types, metadata=metadata,
                           binding=api.prepare_binding(SimpleNamespace(dll=dll)))


def clip(values):
    return SimpleNamespace(light_tracking_threshold=values[0], light_tracking_attack=values[1],
                           light_tracking_release=values[2], light_tracking_max_brightness=values[3])


CASES = [(0, .08, .22, 0), ('0', float('nan'), float('inf'), None),
         (-1, -float('inf'), -0.0, ''), (10 ** 1000, None, None, 10 ** 1000),
         (-(10 ** 1000), 5e-324, .01, -(10 ** 1000)), (255, .1, .2, 255),
         (256, .1, .2, 256), (127, .1, .2, 128), (-0.9, False, '', False)]


@pytest.mark.parametrize('values', CASES, ids=['numeric-' + str(i) for i in range(len(CASES))])
def test_raw_actual_native_complete_report_bytes_match_original_ordered_math(raw, values):
    threshold, attack, release, brightness = values
    expected = raw.types.Report(abi=1, struct_size=ct.sizeof(raw.types.Report), consumed=4,
        threshold_values=1, attack_values=1, release_values=1, brightness_values=1,
        threshold=max(0, min(255, int(threshold or 128))),
        brightness=max(0, min(255, int(255 if brightness in (None, '') else brightness))),
        attack=max(.01, float(attack or .08)), release=max(.01, float(release or .22)))
    packed = raw.metadata.prepare(clip(values), .08, .22)
    before = raw.api.counters()
    returns, function = [], raw.binding.functions[0]
    def returned(code, actual, arguments):
        returns.append((actual, code))
        return code
    function.errcheck = returned
    try:
        result, error = raw.api.execute(raw.binding, packed)
    finally:
        del function.errcheck
    assert returns == [(function, 0)], 'No actual native return was observed'
    assert error is None and bytes(result) == bytes(expected)
    after = raw.api.counters()
    assert after['calls'] == before['calls'] + 1 and after['native_errors'] == before['native_errors']
    assert after['pending'] == before['pending']
    assert isinstance(raw.binding.functions[0], ct._CFuncPtr)


@pytest.mark.parametrize('index,value', [(0, float('nan')), (0, float('inf')),
    (1, 10 ** 1000), (2, 10 ** 1000), (3, float('nan')), (3, float('inf'))])
def test_raw_first_semantic_selector_stops_at_original_consumed_stage(raw, index, value):
    values = [16, .11, .31, 255]
    values[index] = value
    packed = raw.metadata.prepare(clip(values), .08, .22)
    original_error = packed.errors[index]
    result, error = raw.api.execute(raw.binding, packed)
    assert error is original_error and result.semantic == 1 and result.selector == index
    assert result.consumed == index
    assert (result.threshold_values, result.attack_values, result.release_values,
            result.brightness_values) == tuple(int(stage < index) for stage in range(4))
    assert all(packed.request.values[stage].kind == 0 for stage in range(index + 1, 4))


@pytest.mark.parametrize('mutation', ['abi', 'request_size', 'mode', 'count', 'kind', 'selector',
    'offset', 'length', 'no_pool', 'float_offset', 'float_length', 'report_abi', 'report_size', 'null_request', 'null_report'])
def test_invalid_raw_abi_never_writes_report_or_owned_input_bytes(raw, mutation):
    packed = raw.metadata.prepare(clip((16, .11, .31, 255)), .08, .22)
    request, result = packed.request, raw.types.report()
    ct.memset(ct.addressof(result) + 8, 0xA5, ct.sizeof(result) - 8)
    if mutation == 'abi': request.abi = 2
    elif mutation == 'request_size': request.struct_size -= 1
    elif mutation == 'mode': request.mode = 1
    elif mutation == 'count': request.count = 3
    elif mutation == 'kind': request.values[0].kind = 2
    elif mutation == 'selector': request.values[2].selector = 1
    elif mutation == 'offset': request.values[0].offset = 2 ** 64 - 1
    elif mutation == 'length': request.values[0].length = 2 ** 64 - 1
    elif mutation == 'no_pool': request.integers = None
    elif mutation == 'float_offset': request.values[1].offset = 1
    elif mutation == 'float_length': request.values[1].length = 1
    elif mutation == 'report_abi': result.abi = 2
    elif mutation == 'report_size': result.struct_size -= 1
    before = bytes(result), bytes(request), bytes(packed.owner)
    rc = raw.binding.functions[0](None if mutation == 'null_request' else ct.byref(request),
                                 None if mutation == 'null_report' else ct.byref(result))
    assert rc == -1
    assert (bytes(result), bytes(request), bytes(packed.owner)) == before
