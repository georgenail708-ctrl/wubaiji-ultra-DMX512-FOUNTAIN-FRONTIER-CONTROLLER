"""Owning typed SNTC media plans on the already installed CE DLL.

Source implementation only. No media decoder/player/FFT/second DLL loader and
no numerical fallback. Planner state is a draft until its consumer commits it.
"""
import ctypes as ct

from . import native_binding
from .native_sntc_v1 import Snapshot, Packet


class Input(ct.Structure):
    _fields_ = [('abi',ct.c_uint32),('size',ct.c_uint32)] + [
        (name,ct.c_uint64) for name in ('provider_epoch','audio_epoch','audio_history_epoch','video_epoch')] + [
        (name,ct.c_double) for name in ('clip_start','clip_duration','audio_history_t')] + [
        ('audio_samples',ct.c_uint64)] + [(name,ct.c_uint32) for name in
        ('sample_rate','audio_ready','audio_history_known','player_ready','player_playing','player_seekable','muted')] + [
        ('player_ms',ct.c_int64)] + [(name,ct.c_double) for name in ('video_anchor_t','video_fps','video_rate')] + [
        (name,ct.c_int64) for name in ('video_anchor_frame','video_in','video_out')] + [
        ('video_total',ct.c_uint64)] + [(name,ct.c_uint32) for name in
        ('video_ready','video_direction','video_loop','video_published','video_rate_parse_status')] + [
        ('video_anchor_dir',ct.c_int32)] + [(name,ct.c_uint32) for name in
        ('video_pending','video_failed','reserved')] + [(name,ct.c_uint64) for name in
        ('video_published_epoch','video_pending_epoch')] + [(name,ct.c_int64) for name in
        ('video_published_frame','video_pending_frame')]


class State(ct.Structure):
    _fields_ = [(name,ct.c_uint32) for name in
        ('abi','size','selected','playing','have_playing','have_video')] + [(name,ct.c_uint64) for name in
        ('provider_epoch','selection_revision','jump_revision','position_revision','playing_revision','revision',
         'video_epoch','audio_epoch')] + [('position',ct.c_double),('video_frame',ct.c_int64)]


class Plan(ct.Structure):
    _fields_ = [(name,ct.c_uint32) for name in
        ('abi','size','event','selected','apply_position','apply_transport','playing','audio_inside',
         'audio_has_samples','audio_compute','suppress_onset_pulse','apply_audio_ms',
         'video_decode','video_hold','player_seek_unavailable','pause_outside_clip')] + [
        (name,ct.c_double) for name in ('timeline_dt','audio_t','feature_t')] + [
        (name,ct.c_int64) for name in ('audio_ms','audio_sample','video_frame')] + [
        ('video_play_dir',ct.c_int32),('reserved',ct.c_uint32)]


class ReferenceInput(ct.Structure):
    _fields_ = [(name,ct.c_uint32) for name in
        ('abi','size','player_ready','player_playing','player_seekable','muted',
         'have_last_request','request_pending','have_play_start','reserved')] + [
        (name,ct.c_uint64) for name in ('provider_epoch','audio_epoch','last_request_audio_epoch',
         'pending_audio_epoch','now_ns','last_io_ns','play_started_ns')] + [
        (name,ct.c_int64) for name in ('player_ms','last_target_ms','pending_target_ms')] + [
        (name,ct.c_uint32) for name in ('transport_pending','transport_action')] + [
        ('transport_audio_epoch',ct.c_uint64)]


class ReferenceState(ct.Structure):
    _fields_ = [(name,ct.c_uint32) for name in ('abi','size','have_source','playing')] + [
        (name,ct.c_uint64) for name in ('provider_epoch','audio_epoch')]


class ReferencePlan(ct.Structure):
    _fields_ = [(name,ct.c_uint32) for name in
        ('abi','size','seek_requested','seek_unavailable','transport_action','pending_same',
         'ack_pending','requires_io','ack_transport','reserved')] + [
        ('target_ms',ct.c_int64),('request_ns',ct.c_uint64),('next',ReferenceState)]


def _fixed_layouts():
    # Independent C offsets, not a copy of the mutable class's current fields.
    return (
        (Input,240,(('abi size',0,ct.c_uint32),
            ('provider_epoch audio_epoch audio_history_epoch video_epoch',8,ct.c_uint64),
            ('clip_start clip_duration audio_history_t',40,ct.c_double),('audio_samples',64,ct.c_uint64),
            ('sample_rate audio_ready audio_history_known player_ready player_playing player_seekable muted',72,ct.c_uint32),
            ('player_ms',104,ct.c_int64),('video_anchor_t video_fps video_rate',112,ct.c_double),
            ('video_anchor_frame video_in video_out',136,ct.c_int64),('video_total',160,ct.c_uint64),
            ('video_ready video_direction video_loop video_published video_rate_parse_status',168,ct.c_uint32),
            ('video_anchor_dir',188,ct.c_int32),('video_pending video_failed reserved',192,ct.c_uint32),
            ('video_published_epoch video_pending_epoch',208,ct.c_uint64),
            ('video_published_frame video_pending_frame',224,ct.c_int64))),
        (State,104,(('abi size selected playing have_playing have_video',0,ct.c_uint32),
            ('provider_epoch selection_revision jump_revision position_revision playing_revision revision video_epoch audio_epoch',24,ct.c_uint64),
            ('position',88,ct.c_double),('video_frame',96,ct.c_int64))),
        (Plan,120,(('abi size event selected apply_position apply_transport playing audio_inside '
            'audio_has_samples audio_compute suppress_onset_pulse apply_audio_ms video_decode video_hold '
            'player_seek_unavailable pause_outside_clip',0,ct.c_uint32),
            ('timeline_dt audio_t feature_t',64,ct.c_double),('audio_ms audio_sample video_frame',88,ct.c_int64),
            ('video_play_dir',112,ct.c_int32),('reserved',116,ct.c_uint32))),
        (ReferenceInput,136,(('abi size player_ready player_playing player_seekable muted '
            'have_last_request request_pending have_play_start reserved',0,ct.c_uint32),
            ('provider_epoch audio_epoch last_request_audio_epoch pending_audio_epoch '
             'now_ns last_io_ns play_started_ns',40,ct.c_uint64),
            ('player_ms last_target_ms pending_target_ms',96,ct.c_int64),
            ('transport_pending transport_action',120,ct.c_uint32),('transport_audio_epoch',128,ct.c_uint64))),
        (ReferenceState,32,(('abi size have_source playing',0,ct.c_uint32),
            ('provider_epoch audio_epoch',16,ct.c_uint64))),
        (ReferencePlan,88,(('abi size seek_requested seek_unavailable transport_action pending_same '
            'ack_pending requires_io ack_transport reserved',0,ct.c_uint32),
            ('target_ms',40,ct.c_int64),('request_ns',48,ct.c_uint64),('next',56,ReferenceState))),
    )


class Planner:
    def __init__(self, binding):
        from src.engine.timeline_sntc_source_guard import media_class_current
        if not media_class_current() or not binding.source_current():
            raise RuntimeError('Current native SNTC media owner/source unavailable')
        self._base, self._dll = binding, binding._dll
        self._map = native_binding.CE_API_MAP
        self._layout_rows = []
        descriptor_type = type(vars(Packet)['abi'])
        for kind,size,groups in _fixed_layouts():
            if (kind.__bases__ != (ct.Structure,) or ct.sizeof(kind) != size
                    or any(name in vars(kind) for name in ('__new__','__init__','__getattr__','__getattribute__'))):
                raise RuntimeError('Foreign native media declaration')
            expected = []
            for names,offset,primitive in groups:
                for name in names.split():
                    descriptor = vars(kind).get(name)
                    if type(descriptor) is not descriptor_type or descriptor.offset != offset:
                        raise RuntimeError('Native media field offset mismatch: ' + name)
                    expected.append((name,primitive))
                    self._layout_rows.append((vars(kind),name,descriptor))
                    offset += ct.sizeof(primitive)
            if type(kind._fields_) is not list or tuple(kind._fields_) != tuple(expected):
                raise RuntimeError('Native media field types/order mismatch')
            self._layout_rows.append((globals(),kind.__name__,kind))
            self._layout_rows.extend((vars(kind),name,vars(kind).get(name))
                for name in ('__new__','__init__','__getattr__','__getattribute__'))
        pointer = ct.POINTER
        self._functions = {}
        declarations = {
            'init': ('ce_sntc_media_init_v1',(pointer(State),ct.c_uint32),ct.c_int32),
            'plan': ('ce_sntc_media_plan_v1',(pointer(State),pointer(Snapshot),pointer(Input),pointer(Plan)),ct.c_int32),
            'size': ('ce_sntc_media_size_v1',(ct.c_uint32,),ct.c_uint32),
            'contract': ('ce_sntc_media_contract_v1',(),ct.c_uint64),
            'reference_init': ('ce_sntc_reference_init_v1',(pointer(ReferenceState),ct.c_uint32),ct.c_int32),
            'reference_plan': ('ce_sntc_reference_plan_v1',
                (pointer(ReferenceState),pointer(Plan),pointer(ReferenceInput),pointer(ReferencePlan)),ct.c_int32),
            'reference_size': ('ce_sntc_reference_size_v1',(ct.c_uint32,),ct.c_uint32),
        }
        for key,(name,args,result) in declarations.items():
            function = native_binding.bind_native(self._dll,name,list(args),result)
            symbol = self._map.get(name,name)
            if not isinstance(function,ct._CFuncPtr) or vars(self._dll).get(symbol) is not function:
                raise RuntimeError('Native SNTC media entry unavailable: ' + name)
            self._functions[key] = (name,symbol,function,args,result,ct.cast(function,ct.c_void_p).value)
        if self._functions['contract'][2]() != 0xf1856d3ec60d78dd:
            raise RuntimeError('Actual native SNTC media source contract mismatch')
        for index,(_,size,_) in enumerate(_fixed_layouts()):
            actual = self._functions['size'][2](index) if index < 3 else self._functions['reference_size'][2](index-3)
            if actual != size:
                raise RuntimeError('Actual native SNTC media ABI mismatch')

    def source_current(self):
        from src.engine.timeline_sntc_source_guard import media_instance_current
        return (media_instance_current(self) and self._base.source_current() and self._dll is self._base._dll
            and native_binding.CE_API_MAP is self._map
            and tuple(self._functions) == ('init','plan','size','contract',
                                        'reference_init','reference_plan','reference_size')
            and all(namespace.get(name) is value for namespace,name,value in self._layout_rows)
            and all(self._map.get(name,name)==symbol and vars(self._dll).get(symbol) is function
                and tuple(function.argtypes or ())==args and function.restype is result
                and ct.cast(function,ct.c_void_p).value==address
                for name,symbol,function,args,result,address in self._functions.values()))

    def _require(self):
        if not self.source_current():
            raise RuntimeError('Native SNTC media source changed')

    def new_state(self):
        self._require()
        return State()

    def init(self, state):
        self._require()
        if type(state) is not State:
            raise TypeError('Owned native media state required')
        result = self._functions['init'][2](ct.byref(state),ct.sizeof(state))
        if result != 0:
            raise RuntimeError('Native SNTC media init rejected: ' + str(result))

    def copy_state(self, state):
        self._require()
        if type(state) is not State:
            raise TypeError('Owned native media state required')
        clone = State()
        ct.memmove(ct.byref(clone),ct.byref(state),ct.sizeof(State))
        return clone

    def new_input(self):
        self._require()
        source = Input()
        source.abi,source.size = 1,ct.sizeof(Input)
        return source

    def plan(self, state, snapshot, source):
        self._require()
        if type(state) is not State or type(snapshot) is not Snapshot or type(source) is not Input:
            raise TypeError('Owned current native media types required')
        output = Plan()
        result = self._functions['plan'][2](ct.byref(state),ct.byref(snapshot),ct.byref(source),ct.byref(output))
        if result != 0:
            raise RuntimeError('Native SNTC media plan rejected: ' + str(result))
        return output

    def new_reference_state(self):
        self._require()
        state = ReferenceState()
        result = self._functions['reference_init'][2](ct.byref(state),ct.sizeof(state))
        if result != 0:
            raise RuntimeError('Native reference state init rejected: ' + str(result))
        return state

    def new_reference_input(self):
        self._require()
        source = ReferenceInput()
        source.abi,source.size = 1,ct.sizeof(source)
        return source

    def copy_reference_state(self, state):
        self._require()
        if type(state) is not ReferenceState:
            raise TypeError('Owned reference state required')
        clone = ReferenceState()
        ct.memmove(ct.byref(clone),ct.byref(state),ct.sizeof(ReferenceState))
        return clone

    def reference_plan(self, state, base, source):
        self._require()
        if type(state) is not ReferenceState or type(base) is not Plan or type(source) is not ReferenceInput:
            raise TypeError('Owned current reference IO types required')
        output = ReferencePlan()
        result = self._functions['reference_plan'][2](ct.byref(state),ct.byref(base),ct.byref(source),ct.byref(output))
        if result != 0:
            raise RuntimeError('Native reference IO plan rejected: ' + str(result))
        return output
