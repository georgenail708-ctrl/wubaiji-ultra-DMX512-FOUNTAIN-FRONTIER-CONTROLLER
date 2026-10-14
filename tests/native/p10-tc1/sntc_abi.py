"""Research raw ABI only. No host time/rate/freshness arithmetic or product import."""
import ctypes as ct
import os
from pathlib import Path


class Packet(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in ('abi', 'size', 'fps', 'wire_status')] + [
        (n, ct.c_uint8) for n in ('hour', 'minute', 'second', 'frame')] + [
        ('sender', ct.c_uint8 * 4), ('position', ct.c_double)]


class Clock(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in
        ('abi', 'size', 'enabled', 'selected', 'have_playing', 'playing', 'wire_status', 'have_time')] + [
        (n, ct.c_uint64) for n in ('last_rx_ns', 'last_now_ns', 'revision')] + [
        ('position', ct.c_double), ('peer_ipv4', ct.c_uint8 * 4), ('sender', ct.c_uint8 * 4),
        ('fps', ct.c_uint32), ('reserved', ct.c_uint32)] + [
        (n, ct.c_uint64) for n in
        ('position_revision', 'playing_revision', 'selection_revision', 'jump_revision')]


class Snapshot(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in
        ('abi', 'size', 'enabled', 'selected', 'fresh', 'playing', 'have_playing', 'wire_status')] + [
        ('revision', ct.c_uint64), ('position', ct.c_double), ('peer_ipv4', ct.c_uint8 * 4),
        ('sender', ct.c_uint8 * 4), ('last_rx_ns', ct.c_uint64), ('fps', ct.c_uint32),
        ('local_allowed', ct.c_uint32)] + [
        (n, ct.c_uint64) for n in
        ('position_revision', 'playing_revision', 'selection_revision', 'jump_revision')]


class Binding:
    """Future ROOT raw controls; actual product current-owner/DLL guard is pending."""
    def __init__(self, path):
        if os.environ.get('QASCADE_GUARD_ACTIVE') != '1':
            raise RuntimeError('ROOT guard required; RAM hold forbids execution now')
        for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
            if os.environ.get(name) != '1':
                raise RuntimeError('All numerical threads must remain one')
        self.path = Path(path).resolve(strict=True)
        self.dll = ct.CDLL(str(self.path))
        state, view, raw = ct.POINTER(Clock), ct.POINTER(Snapshot), ct.POINTER(ct.c_uint8)
        entries = {
            'decode': ('ce_sntc_decode_v1', [raw, ct.c_uint32, ct.POINTER(Packet)]),
            'init': ('ce_sntc_clock_init_v1', [state, ct.c_uint32, ct.c_double, ct.c_uint32, ct.c_uint32, view]),
            'enabled': ('ce_sntc_clock_enabled_v1', [state, ct.c_uint32, view]),
            'ingest': ('ce_sntc_clock_ingest_v1', [state, raw, ct.c_uint32, raw, ct.c_uint64, ct.c_uint32, view]),
            'sample': ('ce_sntc_clock_sample_v1', [state, ct.c_uint64, view]),
            'release_for_local': ('ce_sntc_clock_release_for_local_v1',
                                  [state, ct.c_uint64, ct.c_double, ct.c_uint32, view]),
            'force_release': ('ce_sntc_clock_force_release_v1', [state, view]),
        }
        for name, (symbol, args) in entries.items():
            function = getattr(self.dll, symbol)
            function.argtypes, function.restype = args, ct.c_int32
            setattr(self, name, function)
        self.size = self.dll.ce_sntc_size_v1
        self.size.argtypes, self.size.restype = [ct.c_uint32], ct.c_uint32
        for index, kind in enumerate((Packet, Clock, Snapshot)):
            if self.size(index) != ct.sizeof(kind):
                raise RuntimeError('Actual native ABI mismatch: ' + kind.__name__)


def raw(data):
    if type(data) is not bytes:
        raise TypeError('Retained raw packet/peer bytes required')
    return (ct.c_uint8 * len(data)).from_buffer_copy(data)
