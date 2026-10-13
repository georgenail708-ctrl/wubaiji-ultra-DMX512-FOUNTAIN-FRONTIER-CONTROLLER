"""Generic standalone two-CPU op31 ABI controls; ROOT has not run these.

Only original public arithmetic expressions supply expected results. No private
showfile/reference asset, product modification, or source-counter shortcut.
"""
import ast
import ctypes as ct
import math
from pathlib import Path
import struct

import pytest


ROOT = next(path for path in Path(__file__).resolve().parents
            if (path / 'src/engine/native_anchor_preparation.py').is_file())
I, D = ct.POINTER(ct.c_int), ct.POINTER(ct.c_double)


def opaque_name():
    tree = ast.parse((ROOT / 'render_lab/qascade_ce/ce_api_map.py').read_text('utf-8'))
    values = [item.value for item in tree.body if isinstance(item, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'CE_API_MAP' for target in item.targets)]
    assert len(values) == 1
    return ast.literal_eval(values[0]).get('ce_source_motion_clock', 'ce_source_motion_clock')


@pytest.fixture(params=('sse2', 'avx2'))
def actual_clock(request):
    path = ROOT / 'render_lab/qascade_ce' / ('ce_engine_' + request.param + '.dll')
    assert path.is_file(), 'Both real current CPU builds are mandatory'
    owner = ct.CDLL(str(path))
    function = getattr(owner, opaque_name())
    function.argtypes, function.restype = [ct.c_int, I, D, D, D, D], ct.c_int
    return owner, function


def expected(mode, values):
    if mode == 0:
        return max(0.0, values[0])
    if mode == 1:
        return max(0.0, values[0] - 1e-4)
    if mode == 2:
        return max(0.0, values[0] - values[1] - 1e-4)
    return values[0] * 1e6 + (values[1] + 1.0) * 1e-3


NAN = struct.unpack('>d', bytes.fromhex('7ff8000000001234'))[0]
CASES = (
    (0, (-0.,)), (0, (0.,)), (0, (-5e-324,)), (0, (5e-324,)),
    (0, (-math.inf,)), (0, (math.inf,)), (0, (NAN,)),
    (1, (0.,)), (1, (1e-4,)), (1, (math.nextafter(1e-4, -math.inf),)),
    (1, (math.nextafter(1e-4, math.inf),)), (1, (1e300,)), (1, (NAN,)),
    (2, (1., 1.)), (2, (.001, .0009)), (2, (1e300, 1e300)),
    (2, (math.inf, math.inf)), (2, (0., -math.inf)), (2, (NAN, 1.)),
    (3, (-0., -1.)), (3, (0., 0.)), (3, (.7, 3.)),
    (3, (1e300, 1e300)), (3, (-1e300, 1e300)),
    (3, (math.nextafter(1., math.inf), float(2**53))),
    (3, (NAN, 2.)), (3, (math.inf, -math.inf)),
)


@pytest.mark.parametrize('mode,values', CASES)
def test_actual_c05_all24_output_bytes_match_original_expression(actual_clock, mode, values):
    _owner, function = actual_clock
    integers, numbers = (ct.c_int * 16)(mode), (ct.c_double * 24)(*values)
    # Guard words prove op31 writes exactly the24 output slots, not neighbours.
    output = (ct.c_uint64 * 30)(*[0x4142434445464748] * 30)
    before = bytes(output)
    pointer = ct.cast(ct.byref(output, 3 * 8), D)
    assert function(31, integers, numbers, None, pointer, None) == 0
    wanted = (ct.c_double * 24)(expected(mode, values), *([0.] * 23))
    assert bytes(output)[24:24 + 24 * 8] == bytes(wanted)
    assert bytes(output)[:24] == before[:24] and bytes(output)[27 * 8:] == before[27 * 8:]


@pytest.mark.parametrize('op,mode,missing', [
    (-1, 0, None), (32, 0, None), (2**31 - 1, 0, None),
    (31, -1, None), (31, 4, None), (31, 2**31 - 1, None),
    (31, 0, 'integers'), (31, 0, 'values'), (31, 0, 'out'),
])
def test_invalid_op_mode_or_null_inputs_are_rejected_before_any_write(actual_clock, op, mode, missing):
    _owner, function = actual_clock
    integers, numbers = (ct.c_int * 16)(mode), (ct.c_double * 24)(.7, 3.)
    output = (ct.c_uint64 * 24)(*[0x4142434445464748] * 24)
    pause_out = (ct.c_uint64 * 7)(*[0x1020304050607080] * 7)
    before, pause_before = bytes(output), bytes(pause_out)
    assert function(op, None if missing == 'integers' else integers,
                    None if missing == 'values' else numbers, None,
                    None if missing == 'out' else ct.cast(output, D), ct.cast(pause_out, D)) == -1
    assert bytes(output) == before and bytes(pause_out) == pause_before

