"""SNTC ABI on the installed CE owner; never load a separate numerical DLL.

New source implementation, not yet compiled or runtime qualified. Missing or
foreign native entries reject activation; no Python timecode calculation path.
"""
from __future__ import annotations

import ctypes as ct
import sys

from . import native_binding


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


class ConsumerPlan(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in
        ('abi', 'size', 'selected', 'playing', 'position_changed', 'selection_changed',
         'playing_changed', 'discrete_seek', 'local_allowed', 'reserved')] + [('position', ct.c_double)]


def _entries():
    state, view, raw = ct.POINTER(Clock), ct.POINTER(Snapshot), ct.POINTER(ct.c_uint8)
    return {
        'decode': ('ce_sntc_decode_v1', (raw, ct.c_uint32, ct.POINTER(Packet)), ct.c_int32),
        'init': ('ce_sntc_clock_init_v1', (state, ct.c_uint32, ct.c_double, ct.c_uint32, ct.c_uint32, view), ct.c_int32),
        'enabled': ('ce_sntc_clock_enabled_v1', (state, ct.c_uint32, view), ct.c_int32),
        'ingest': ('ce_sntc_clock_ingest_v1', (state, raw, ct.c_uint32, raw, ct.c_uint64, ct.c_uint32, view), ct.c_int32),
        'sample': ('ce_sntc_clock_sample_v1', (state, ct.c_uint64, view), ct.c_int32),
        'release_for_local': ('ce_sntc_clock_release_for_local_v1', (state, ct.c_uint64, ct.c_double, ct.c_uint32, view), ct.c_int32),
        'force_release': ('ce_sntc_clock_force_release_v1', (state, view), ct.c_int32),
        'size': ('ce_sntc_size_v1', (ct.c_uint32,), ct.c_uint32),
        'contract': ('ce_sntc_contract_v1', (), ct.c_uint64),
        'consumer_plan': ('ce_sntc_consumer_plan_v1', (view, view, ct.c_uint32, ct.POINTER(ConsumerPlan)), ct.c_int32),
        'consumer_plan_size': ('ce_sntc_consumer_plan_size_v1', (), ct.c_uint32),
        'origin_raw': ('ce_sntc_origin_v1', (ct.c_double,ct.c_double,ct.POINTER(ct.c_double)), ct.c_int32),
    }


def _declarations_current():
    """Independent C offsets/types before any native call, not size-only checks."""
    from .native_audio_prepare_dependencies import ctypes_original
    if not ctypes_original():
        return False
    kinds = {'u32':ct.c_uint32,'u64':ct.c_uint64,'f64':ct.c_double,'u8':ct.c_uint8,
             'raw4':ct.c_uint8 * 4}
    for key,codes,width in (('u32',('I','L'),4),('u64',('L','Q'),8),
                            ('f64',('d',),8),('u8',('B',),1)):
        if getattr(kinds[key],'_type_',None) not in codes or ct.sizeof(kinds[key]) != width:
            return False
    class Probe(ct.Structure):
        _fields_ = [('value',ct.c_uint32)]
    field_type = type(vars(Probe)['value'])
    common = 'abi size enabled selected'
    declarations = (
        (Packet,32,('abi size fps wire_status',0,'u32'),('hour minute second frame',16,'u8'),
         ('sender',20,'raw4'),('position',24,'f64')),
        (Clock,112,(common+' have_playing playing wire_status have_time',0,'u32'),
         ('last_rx_ns last_now_ns revision',32,'u64'),('position',56,'f64'),
         ('peer_ipv4 sender',64,'raw4'),('fps reserved',72,'u32'),
         ('position_revision playing_revision selection_revision jump_revision',80,'u64')),
        (Snapshot,104,(common+' fresh playing have_playing wire_status',0,'u32'),
         ('revision',32,'u64'),('position',40,'f64'),('peer_ipv4 sender',48,'raw4'),
         ('last_rx_ns',56,'u64'),('fps local_allowed',64,'u32'),
         ('position_revision playing_revision selection_revision jump_revision',72,'u64')),
        (ConsumerPlan,48,('abi size selected playing position_changed selection_changed '
         'playing_changed discrete_seek local_allowed reserved',0,'u32'),('position',40,'f64')),
    )
    for kind,size,*groups in declarations:
        if (type(kind) is not type(ct.Structure) or kind.__bases__ != (ct.Structure,)
                or any(name in vars(kind) for name in ('__new__','__init__','__getattr__','__getattribute__'))
                or ct.sizeof(kind) != size or type(kind._fields_) is not list):
            return False
        expected = []
        for names,offset,key in groups:
            field_kind = kinds[key]
            for name in names.split():
                descriptor = vars(kind).get(name)
                if type(descriptor) is not field_type or descriptor.offset != offset:
                    return False
                expected.append((name,field_kind))
                offset += ct.sizeof(field_kind)
        if tuple(kind._fields_) != tuple(expected):
            return False
    return True


class Binding:
    """Retain actual DLL/functions/layout metadata and recheck before each call."""

    def __init__(self, engine):
        from src.engine.timeline_sntc_source_guard import binding_class_current
        if not binding_class_current():
            raise RuntimeError('SNTC source is not the qualified current implementation')
        if not _declarations_current():
            raise RuntimeError('SNTC fixed native declarations are not current')
        from .ctypes_api import CEEngine
        installed = sys.modules.get('qascade_ce')
        if (installed is None or vars(installed).get('_installed') is not True
                or vars(installed).get('_engine') is not engine or type(engine) is not CEEngine
                or type(vars(engine).get('dll')) is not ct.CDLL):
            raise RuntimeError('Installed CE owner is unavailable for SNTC')
        self._engine, self._installed, self._dll = engine, installed, engine.dll
        self._dll_identity = self._dll._handle, self._dll._name
        self._map = native_binding.CE_API_MAP
        self._rows = []
        for key, (name, args, result) in _entries().items():
            function = native_binding.bind_native(self._dll, name, list(args), result)
            symbol = self._map.get(name, name)
            if (not isinstance(function, ct._CFuncPtr) or type(symbol) is not str
                    or vars(self._dll).get(symbol) is not function):
                raise RuntimeError('SNTC native entry unavailable: ' + name)
            setattr(self, key, function)
            self._rows.append((key, name, symbol, function, args, result,
                               ct.cast(function, ct.c_void_p).value))
        self._layouts = tuple((kind, tuple(kind._fields_)) for kind in (Packet, Clock, Snapshot, ConsumerPlan))
        self._descriptors = tuple((kind,name,vars(kind).get(name)) for kind,saved in self._layouts
                                 for name in (*[field[0] for field in saved],
                                              '__new__','__init__','__getattr__','__getattribute__'))
        self._methods = tuple((name, function, function.__code__, function.__defaults__, function.__kwdefaults__)
                              for name, function in vars(type(self)).items() if callable(function)
                              and hasattr(function, '__code__'))
        if not self.source_current():
            raise RuntimeError('SNTC native entry identity changed while binding')
        if self.contract() != 0x7f7a5497f0dee5a4:
            raise RuntimeError('SNTC native source contract mismatch')
        for index, (kind, expected) in enumerate(zip((Packet, Clock, Snapshot), (32, 112, 104))):
            if ct.sizeof(kind) != expected or self.size(index) != expected:
                raise RuntimeError('SNTC actual native ABI mismatch: ' + kind.__name__)
        if ct.sizeof(ConsumerPlan) != 48 or self.consumer_plan_size() != 48:
            raise RuntimeError('SNTC consumer-plan ABI mismatch')

    def origin(self, raw_now, position):
        if not self.source_current():
            raise RuntimeError('Current native SNTC clock origin source changed')
        output = ct.c_double()
        status = self.origin_raw(raw_now,position,ct.byref(output))
        if status != 0:
            raise RuntimeError('Native SNTC clock origin rejected: ' + str(status))
        return output.value

    def source_current(self) -> bool:
        if (sys.modules.get('qascade_ce') is not self._installed
                or vars(self._installed).get('_engine') is not self._engine
                or vars(self._installed).get('_installed') is not True
                or vars(self._engine).get('dll') is not self._dll
                or (self._dll._handle, self._dll._name) != self._dll_identity
                or native_binding.CE_API_MAP is not self._map):
            return False
        for name, function, code, defaults, kwdefaults in self._methods:
            if (vars(type(self)).get(name) is not function or function.__code__ is not code
                    or function.__defaults__ is not defaults or function.__kwdefaults__ is not kwdefaults
                    or name in vars(self)):
                return False
        if any(tuple(kind._fields_) != saved or globals().get(kind.__name__) is not kind
               for kind, saved in self._layouts):
            return False
        if any(vars(kind).get(name) is not value for kind,name,value in self._descriptors):
            return False
        for key, name, symbol, function, args, result, address in self._rows:
            if (vars(self).get(key) is not function or self._map.get(name, name) != symbol
                    or vars(self._dll).get(symbol) is not function
                    or tuple(function.argtypes or ()) != args or function.restype is not result
                    or ct.cast(function, ct.c_void_p).value != address):
                return False
        return True
