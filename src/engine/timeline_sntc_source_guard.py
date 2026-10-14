"""Separate admission of the explicitly reviewed SNTC source successor.

Original clock/controller/render fingerprints and independent tests remain in
their original guards. New pins are source-only until native/output acceptance.
"""
import hashlib
import builtins
import sys
from types import FunctionType, BuiltinFunctionType

from .timeline_sntc_signatures import EXPECTED

_DEFAULTS = {
    ('src.network.sntc_input','SntcInput.__init__'): (None, {'initial_known':1, 'retirement_current':None}),
    ('src.network.sntc_input','SntcInput.init'): ((1,), None),
    ('src.network.sntc_input','SntcInput._call_locked'): (None, {'retirement':False}),
    ('src.ui.sntc_sync_binding','SntcTimelineBridge._present'): ((None,), None),
    ('src.network.artnet_node','make_sntc_input_socket'): (('0.0.0.0',8405,0.1), None),
    ('src.engine.playback_engine','PlaybackEngine._render_position'): (('clip_playback',None), None),
    ('src.ui.timeline.audio_ref_track','AudioRefTrack.play'): ((None,), None),
    ('src.ui.timeline.audio_ref_track','AudioRefTrack._play_local'): ((None,), None),
    ('src.engine.timeline_sntc_source_guard','reference_current'): (None, {'bridge_checked':False}),
}


def _resolve(module, qualified_name):
    value = module
    for name in qualified_name.split('.'):
        if '<locals>' in name:
            return None
        value = vars(value).get(name)
        if value is None:
            return None
    return value.__func__ if isinstance(value, staticmethod) else value


def _canonical(function, module, wanted):
    from qascade_ce.native_curve_scalar_guard import _signature
    return (type(function) is FunctionType and function.__globals__ is vars(module)
            and hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest() == wanted)


def _rows(module_names):
    from qascade_ce.native_curve_scalar_guard import function_metadata
    rows = []
    for name in module_names:
        from .lock4_admission import successor_module_rows
        registered, successor = successor_module_rows(name)
        if registered:
            if successor is None:
                return None
            rows.extend(successor)
            continue
        module = sys.modules.get(name)
        expected = EXPECTED.get(name)
        if module is None or expected is None:
            return None
        rows.append((sys.modules, name, module))
        for qualified, wanted in expected.items():
            if '<locals>' in qualified:
                continue
            function = _resolve(module, qualified)
            if not _canonical(function, module, wanted):
                return None
            defaults,keywords = _DEFAULTS.get((name,qualified),(None,None))
            if function.__defaults__ != defaults or function.__kwdefaults__ != keywords:
                return None
            namespace = vars(module)
            for part in qualified.split('.')[:-1]:
                namespace = vars(namespace[part])
            value = namespace.get(qualified.split('.')[-1])
            rows.append((namespace, qualified.split('.')[-1], value))
            rows.extend(function_metadata(function))
            if function.__kwdefaults__ is not None:
                rows.extend((function.__kwdefaults__,key,value)
                            for key,value in function.__kwdefaults__.items())
        for alias in ('ct','socket','time','threading','native_binding'):
            value = vars(module).get(alias)
            if value is not None:
                expected_module = 'ctypes' if alias == 'ct' else alias
                if alias != 'native_binding' and sys.modules.get(expected_module) is not value:
                    return None
                if alias == 'ct':
                    from qascade_ce.native_audio_prepare_dependencies import ctypes_original
                    if not ctypes_original():
                        return None
                if alias == 'time':
                    for member in ('monotonic_ns','monotonic'):
                        clock = vars(value).get(member)
                        if (type(clock) is not BuiltinFunctionType or clock.__name__ != member
                                or clock.__module__ != 'time'):
                            return None
                rows.append((vars(module),alias,value))
                for member in ('byref','cast','sizeof','POINTER','CDLL','Structure','monotonic_ns','monotonic',
                               'memmove','c_uint32','c_uint64','c_uint8','c_int32','c_int64','c_double',
                               'inet_aton','inet_ntoa','socket','RLock','Lock','Thread','Event','bind_native'):
                    captured = vars(value).get(member)
                    if captured is not None:
                        rows.append((vars(value),member,captured))
                        if type(captured) is FunctionType:
                            rows.extend(function_metadata(captured))
    return rows


def metadata_current(rows):
    return rows is not None and all(namespace.get(name) is value for namespace, name, value in rows)


def successor_controller_rows(controller, module):
    from ..ui.timeline import timeline_sntc_control  # pure control routing only
    from . import timeline_external_clock
    from .lock4_admission import class_role_names
    if (vars(module).get('TimelineController') is not type(controller)
            or any(name.rsplit('.',1)[-1] in vars(controller) for name in class_role_names(module.__name__)
                   if name.startswith('TimelineController.'))):
        return None
    return _rows(('src.ui.timeline.timeline_controller',
                  'src.ui.timeline.timeline_sntc_control','src.engine.timeline_external_clock',
                  'src.engine.timeline_sntc_source_guard'))


def successor_clock_source(callback):
    module = sys.modules.get('src.ui.main_window')
    from .lock4_admission import clock_source_current
    if clock_source_current(callback):
        return True
    wanted = EXPECTED['src.ui.main_window'].get(callback.__code__.co_qualname)
    return (module is not None and wanted is not None and _canonical(callback, module, wanted)
            and callback.__globals__.get('vars',builtins.vars) is builtins.vars)


def successor_render_source(function, owner):
    from . import timeline_external_clock
    module = sys.modules.get(owner.__module__)
    from .lock4_admission import successor_render_current
    if successor_render_current(function, owner):
        return True
    return (module is not None
            and _canonical(function,module,EXPECTED['src.engine.playback_engine']['PlaybackEngine._render_position'])
            and _rows(('src.engine.timeline_external_clock',)) is not None)


def successor_render_rows():
    from . import timeline_external_clock
    from .lock4_admission import render_scope_rows
    return render_scope_rows()


def binding_class_current():
    return _rows(('qascade_ce.native_sntc_v1','qascade_ce.native_binding')) is not None


def media_class_current():
    return _rows(('qascade_ce.native_sntc_media','qascade_ce.native_binding')) is not None


def media_instance_current(planner):
    module = sys.modules.get('qascade_ce.native_sntc_media')
    if module is None or type(planner) is not vars(module).get('Planner'):
        return False
    certificate = vars(planner).get('_sntc_media_certificate')
    if certificate is None:
        certificate = _rows(('qascade_ce.native_sntc_media',))
        if certificate is None:
            return False
        planner._sntc_media_certificate = certificate
    from .lock4_admission import class_role_names
    if any(name.rsplit('.',1)[-1] in vars(planner) for name in class_role_names(module.__name__)
           if name.startswith('Planner.')):
        return False
    if not metadata_current(certificate):
        return False
    protocol = sys.modules.get('qascade_ce.native_sntc_v1')
    if (protocol is None or vars(module).get('Snapshot') is not vars(protocol).get('Snapshot')
            or vars(module).get('Packet') is not vars(protocol).get('Packet')):
        return False
    for kind,_,groups in module._fixed_layouts():
        expected = tuple((name,primitive) for names,_,primitive in groups for name in names.split())
        if tuple(kind._fields_) != expected:
            return False
    return metadata_current(certificate)


def binding_current(bridge):
    module = sys.modules.get('src.ui.sntc_sync_binding')
    if module is None or vars(module).get('SntcTimelineBridge') is not type(bridge):
        return False
    certificate = vars(bridge).get('_sntc_source_certificate')
    if certificate is None:
        certificate = _rows(('src.ui.sntc_sync_binding','src.network.sntc_input',
            'qascade_ce.native_sntc_v1','qascade_ce.native_binding','src.network.artnet_node',
            'qascade_ce.native_sntc_media','src.ui.timeline.sntc_reference_audio',
            'src.ui.timeline.timeline_sntc_control','src.engine.timeline_external_clock',
            'src.engine.timeline_sntc_source_guard'))
        if certificate is None:
            return False
        bridge._sntc_source_certificate = certificate
    from .lock4_admission import class_role_names
    if any(name.rsplit('.',1)[-1] in vars(bridge)
           for name in class_role_names('src.ui.sntc_sync_binding') if name.startswith('SntcTimelineBridge.')):
        return False
    receiver = vars(bridge).get('_receiver')
    if receiver is not None:
        host = sys.modules.get('src.network.sntc_input')
        native = sys.modules.get('qascade_ce.native_sntc_v1')
        transport = sys.modules.get('src.network.artnet_node')
        if (host is None or native is None or transport is None
                or vars(native).get('Binding') is None
                or type(vars(bridge).get('_native')) is not vars(native).get('Binding')
                or type(receiver) is not vars(host).get('SntcInput')
                or vars(receiver).get('_native') is not vars(bridge).get('_native')
                or type(vars(receiver).get('_clock')) is not vars(native).get('Clock')
                or vars(receiver).get('_snapshot_type') is not vars(native).get('Snapshot')
                or vars(receiver).get('_socket_factory') is not vars(transport).get('make_sntc_input_socket')):
            return False
        if vars(bridge).get('_plan_type') is not vars(native).get('ConsumerPlan'):
            return False
        for field,name in (('_source_current','source_current'),('_current_playing','current_playing')):
            callback = vars(receiver).get(field)
            if (getattr(callback,'__self__',None) is not bridge
                    or getattr(callback,'__func__',None) is not vars(type(bridge)).get(name)):
                return False
        retirement = vars(receiver).get('_retirement_current')
        if (getattr(retirement,'__self__',None) is not vars(bridge).get('_native')
                or getattr(retirement,'__func__',None) is not vars(vars(native).get('Binding')).get('source_current')):
            return False
    return (metadata_current(certificate)
            and (vars(bridge).get('_reference_audio') is None
                 or reference_current(vars(bridge).get('_reference_audio'), bridge_checked=True)))


def reference_current(consumer, *, bridge_checked=False):
    module = sys.modules.get('src.ui.timeline.sntc_reference_audio')
    track_module = sys.modules.get('src.ui.timeline.audio_ref_track')
    if module is None or track_module is None or type(consumer) is not vars(module).get('SelectedReferenceAudio'):
        return False
    certificate = vars(consumer).get('_sntc_reference_certificate')
    if certificate is None:
        certificate = _rows(('src.ui.timeline.sntc_reference_audio','src.ui.timeline.audio_ref_track',
                            'qascade_ce.native_sntc_media'))
        if certificate is None:
            return False
        consumer._sntc_reference_certificate = certificate
    if not metadata_current(certificate):
        return False
    bridge,track,planner = (vars(consumer).get(name) for name in ('_bridge','_track','_planner'))
    if (bridge is None or track is None or type(track) is not vars(track_module).get('AudioRefTrack')
            or vars(bridge).get('_reference_audio') is not consumer
            or vars(track).get('_sntc_reference') is not consumer):
        return False
    window = vars(bridge).get('_window')
    if window is None:
        return False
    timeline = vars(window).get('_timeline_widget')
    if timeline is None or vars(timeline).get('_audio_track') is not track:
        return False
    from .lock4_admission import class_role_names
    for obj,prefix,source_name in ((consumer,'SelectedReferenceAudio.','src.ui.timeline.sntc_reference_audio'),
                                  (track,'AudioRefTrack.','src.ui.timeline.audio_ref_track')):
        if any(name.rsplit('.',1)[-1] in vars(obj) for name in class_role_names(source_name)
               if name.startswith(prefix)):
            return False
    return (media_instance_current(planner) and planner._base is vars(bridge).get('_native')
            and (bridge_checked or binding_current(bridge)))


def provider_current(engine, bridge):
    if not binding_current(bridge):
        return False
    controller = vars(bridge).get('_controller')
    window = vars(bridge).get('_window')
    if controller is None or window is None:
        return False
    timeline = vars(window).get('_timeline_widget')
    return (vars(bridge).get('_engine') is engine and vars(engine).get('_sntc_bridge') is bridge
            and vars(controller).get('_sntc_bridge') is bridge
            and vars(window).get('_engine') is engine and timeline is not None
            and vars(timeline).get('ctrl') is controller and vars(bridge).get('_native').source_current())


def controller_external_clock(controller, bridge):
    if not binding_current(bridge) or vars(bridge).get('_controller') is not controller:
        raise RuntimeError('Foreign external timeline clock supplier')
    engine = vars(bridge).get('_engine')
    if engine is None or not provider_current(engine,bridge):
        raise RuntimeError('External timeline clock owner is not current')
    return bridge.generator_clock()
