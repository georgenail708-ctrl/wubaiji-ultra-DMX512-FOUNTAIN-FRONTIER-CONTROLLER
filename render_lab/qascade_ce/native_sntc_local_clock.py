"""Typed raw ordinary generator clock on the existing installed CE DLL only."""
import ctypes as ct
import time

from . import native_binding


class Input(ct.Structure):
    _fields_ = [(name, ct.c_uint32) for name in ('abi', 'size', 'playing', 'reserved')] + [
        (name, ct.c_double) for name in ('raw_t0', 'raw_position', 'raw_now')]


class Plan(ct.Structure):
    _fields_ = [(name, ct.c_uint32) for name in ('abi', 'size', 'render', 'reserved')] + [
        ('position', ct.c_double)]


def _entries():
    return (
        ('invoke', 'ce_sntc_local_clock_v1', (ct.POINTER(Input), ct.POINTER(Plan)), ct.c_int32),
        ('size', 'ce_sntc_local_clock_size_v1', (ct.c_uint32,), ct.c_uint32),
    )


def _declarations_current():
    """Independent fixed C primitives/offsets before learning any declaration."""
    from .native_audio_prepare_dependencies import ctypes_original
    if not ctypes_original():
        return False
    primitives = {'u32': ct.c_uint32, 'f64': ct.c_double}
    for name, codes, width in (('u32', ('I', 'L'), 4), ('f64', ('d',), 8)):
        if getattr(primitives[name], '_type_', None) not in codes or ct.sizeof(primitives[name]) != width:
            return False
    class Probe(ct.Structure):
        _fields_ = [('value', ct.c_uint32)]
    descriptor_type = type(vars(Probe)['value'])
    declarations = (
        (Input, 40, (('abi size playing reserved', 0, 'u32'),
                    ('raw_t0 raw_position raw_now', 16, 'f64'))),
        (Plan, 24, (('abi size render reserved', 0, 'u32'), ('position', 16, 'f64'))),
    )
    for kind, size, groups in declarations:
        if (type(kind) is not type(ct.Structure) or kind.__bases__ != (ct.Structure,)
                or any(name in vars(kind) for name in
                       ('__new__', '__init__', '__getattr__', '__getattribute__',
                        '_pack_', '_align_', '_anonymous_', '_layout_'))
                or ct.sizeof(kind) != size or type(kind._fields_) is not list):
            return False
        expected = []
        for names, offset, primitive in groups:
            field_type = primitives[primitive]
            for name in names.split():
                descriptor = vars(kind).get(name)
                if type(descriptor) is not descriptor_type or descriptor.offset != offset:
                    return False
                expected.append((name, field_type))
                offset += ct.sizeof(field_type)
        if tuple(kind._fields_) != tuple(expected):
            return False
    return True


class LocalClockBinding:
    def __init__(self, base):
        from src.engine.lock4_admission import admitted_rows
        from .native_audio_prepare_dependencies import ctypes_original
        from .native_sntc_v1 import Binding
        rows = admitted_rows()
        if (rows is None or type(base) is not Binding or not base.source_current()
                or not ctypes_original() or not _declarations_current()):
            raise RuntimeError('LOCK4 source/installed owner has not been admitted')
        self._base, self._certificate = base, rows
        self._rows = []
        self._map = native_binding.CE_API_MAP
        self._layouts = ((Input, tuple(Input._fields_)), (Plan, tuple(Plan._fields_)))
        self._descriptors = tuple((kind, name, vars(kind).get(name)) for kind, saved in self._layouts
                                 for name in (*[field[0] for field in saved],
                                              '__new__', '__init__', '__getattr__', '__getattribute__'))
        for key, name, args, result in _entries():
            function = native_binding.bind_native(base._dll, name, list(args), result)
            symbol = self._map.get(name, name)
            if not isinstance(function, ct._CFuncPtr) or vars(base._dll).get(symbol) is not function:
                raise RuntimeError('Current installed CE has no LOCK4 entry: ' + name)
            setattr(self, key, function)
            self._rows.append((key, name, symbol, function, args, result,
                               ct.cast(function, ct.c_void_p).value))
        self._rows = tuple(self._rows)
        self._saved_rows = self._rows
        if not self.source_current():
            raise RuntimeError('LOCK4 native identity changed while binding')
        if ct.sizeof(Input) != 40 or ct.sizeof(Plan) != 24 or self.size(0) != 40 or self.size(1) != 24:
            raise RuntimeError('LOCK4 native declaration size mismatch')
        if (Input.raw_t0.offset != 16 or Input.raw_now.offset != 32 or Plan.position.offset != 16):
            raise RuntimeError('LOCK4 native declaration offset mismatch')

    def source_current(self):
        from src.engine.lock4_admission import admitted_rows, metadata_current
        if (admitted_rows() is not self._certificate or not metadata_current(self._certificate)
                or not self._base.source_current()
                or native_binding.CE_API_MAP is not self._map or self._rows is not self._saved_rows
                or any(name in vars(self) for name in ('__init__','source_current','plan'))):
            return False
        for kind, fields in self._layouts:
            if globals().get(kind.__name__) is not kind or tuple(kind._fields_) != fields:
                return False
        if any(vars(kind).get(name) is not saved for kind, name, saved in self._descriptors):
            return False
        entries = _entries()
        if len(self._rows) != len(entries):
            return False
        for row, entry in zip(self._rows, entries):
            key, name, symbol, function, args, result, address = row
            if (key, name, args, result) != entry:
                return False
            if (vars(self).get(key) is not function or self._map.get(name, name) != symbol
                    or vars(self._base._dll).get(symbol) is not function
                    or tuple(function.argtypes or ()) != args or function.restype is not result
                    or ct.cast(function, ct.c_void_p).value != address):
                return False
        return True

    def plan(self, playing, controller):
        if not self.source_current() or type(playing) is not bool:
            raise RuntimeError('LOCK4 raw controller/native owner is not current')
        raw = Input(abi=1, size=40, playing=playing)
        if playing:
            t0 = vars(controller).get('_play_t0', 0.0)
            # Match original float(t0 or 0.0) conversion once; no host arithmetic.
            if t0 is not None and type(t0) not in (float, int, bool):
                raise RuntimeError('Foreign local clock origin scalar')
            raw.raw_t0 = float(t0 or 0.0)
            # Original generator never consumes _play_pos during playing.
            # Forward exact f64 metadata only; no new integer conversion/error.
            position = vars(controller).get('_play_pos')
            if type(position) is float:
                raw.raw_position = position
            raw.raw_now = time.monotonic()
        out = Plan()
        status = self.invoke(ct.byref(raw), ct.byref(out))
        if status != 0:
            raise RuntimeError('LOCK4 C generator plan rejected: ' + str(status))
        if out.abi != 1 or out.size != 24 or out.render not in (0, 1) or out.reserved:
            raise RuntimeError('LOCK4 C generator plan returned invalid fixed metadata')
        return out
