"""Fixed-source preparation metadata guard, executed once per owned range."""
import hashlib
import inspect
import sys
from types import FunctionType

from qascade_ce.native_curve_scalar_guard import _signature

EXPECTED = {'src.engine.playback_engine:PlaybackEngine._clip_cache_id': 'e60654f45158acaa79ec2ff6b951a2e1ae7e48f194221cb437b5f11a1ef52633', 'src.engine.playback_engine:PlaybackEngine._track_state_signature': '8d61d22a1f6d0e6288ad2fc477f938a7883683054bebb6650898fee1b74407d5', 'src.engine.playback_engine:PlaybackEngine._project_clip_layout_signature': 'c2d0368ac3c6d7c24f1cfc62348ed1e7ebfaff5219ea8b58a1f408dd98491df8', 'src.engine.playback_engine:PlaybackEngine._render_track_state': 'b428a46eeffe96584acb52cbc70002984024448fa3852c4426412699d59e5db2', 'src.engine.playback_engine:PlaybackEngine._render_struct_for_tracks': '3861e1b423e5b5dd57d074efbbbd251b9865b7768c215e607dc0b1e914f18f30', 'src.engine.playback_engine:PlaybackEngine._target_universes_for_clip': '39dd4423078c18d1ac7b868569fe8db9fc3397cce3fe4f089e759decbcac62c5', 'src.engine.playback_engine:PlaybackEngine._clip_index_scope_mask': '6d09fb47941b96043d2484858f1047cf51233b59aeb3186e13bc76ea27ae2973', 'src.engine.playback_engine:PlaybackEngine._build_playback_active_index': 'b2d4a1b4ee95e4fca9204f7b858214dd9f497405332b2067d3bf0baf790e5061', 'src.engine.playback_engine:PlaybackEngine._playback_index_for_tracks': '63224cf2ae1be1b5585513fe8b7f540f762ab016eb134bb32b5ffa680c70a742', 'src.engine.playback_engine:PlaybackEngine._indexed_active_clip_items': 'adac5663e0e614340913aa52f91bccf405796dbd862ae7e4fae7bfc70281a95d', 'src.engine.playback_engine:PlaybackEngine._clip_hold_mode': 'a62cbf2852c40f44a3aa35eceb7c3198e937193e21a84a300e785e9cda03cb3a', 'src.engine.playback_engine:PlaybackEngine._clip_playback_rate': '23212eb475063f45b74871b3458773fe511c6be44a13928d1e1fe368c345b867', 'src.engine.playback_engine:PlaybackEngine._clip_source_duration': '328dc2a9a2547af8b7c887b116ea657b4d2d30b3f0ea37dc30836eb938894f28', 'src.engine.playback_engine:PlaybackEngine._clip_run_mode_source_time': '8c25fbf70f65a59358ddea23b95e8484c94e239d948cec6c382ba1044bfee9d2', 'src.engine.playback_engine:PlaybackEngine._clip_sequential_source_time': 'cd571b17bf5b73f504e6dd604f728e85c6cb2aa72f6aac868e4fec9895efe034', 'src.engine.playback_engine:PlaybackEngine._clip_transition_gain': 'c7f9fe30987b703b24d32ab1030553f708549d6b8d3f5fc092f8292a578da339', 'src.engine.playback_engine:PlaybackEngine._clip_fade_gain': '1f929e9bff443da4786157b143b0702d05cbaf1711be27b180984b11892a2c80', 'src.engine.playback_engine:PlaybackEngine._clip_channel_active_mask': '96ec3f5abe52498dd3df2afc1f413ba86bd512182d55415da1836357e7b1a61a', 'src.engine.playback_engine:PlaybackEngine._clip_base_order_channel_mask': 'c3833c85bd660804ab5f35eaf32dac1ca8052bcf6276ca9b443e29eaafa11181', 'src.engine.playback_engine:PlaybackEngine._expand_motor_pair_mask_for_universe': '643f0de4031502ac59c4901ca5359215662ce17bd7319d718df3219c1d9c3cc6', 'src.engine.playback_engine:PlaybackEngine._track_filter_mask_for_universe': 'c69cb47c8c2fc1196c7ac1bfbd518fbbffceddee0cf0d93493029120b3a78e6b', 'src.engine.playback_engine:PlaybackEngine._track_or_ancestor_muted': 'a0f3152d878a2ac6d78b015c393206a1dabae9f236967d61f3ff02c2980ff1b8', 'src.engine.playback_engine:PlaybackEngine._track_or_ancestor_soloed': '1cd07a8dca4f6d90580195d40a1baa77a9e7e3b4b46097f9ed3a0f14b09faae0', 'src.engine.playback_engine:PlaybackEngine._clip_auto_hold_cycle_time': '733039f181af439fc06ab9230c165c236717a951fbbb7dab523d0285ef0a198e', 'qascade_ce:_hook_indexed_holds.<locals>.ce_indexed': '41e18a399bc667a8cb9c209348717ce73d020a6075928a281e61224313980765', 'qascade_ce:_hook_timeline_range.<locals>.ce_render_timeline_frame_range.<locals>.prepare_progress': 'e422e4b682cafe6a4cfb6e4bac7abe5eca3864d95ba68f5902d580e471c44435', 'qascade_ce.render_path:ce_indexed_active_clip_items': '6cf0d1b1d3b8716afbbeee319344933835974744a412439adcf1cf8c6882d9d9', 'qascade_ce.native_playback_index_install:_build': '00fb12230a07330ec4c3e0d493091524c2210f36aa6ec67bdcfd6fda4566d728', 'qascade_ce.native_playback_index_install:_index': '2bd077f34153d0d0a4f3c281d65d59868a6e6ba7ec51ef95ee53bd40859ceb53', 'qascade_ce.native_playback_index_install:_scope': '2a02e048ab508d38e82e6f3c9282106d15c3769c1fd80102e8a94b2bbfd034ff', 'qascade_ce.native_playback_index_install:_base': 'b4e50f3152d9f8f204dd59b4982661e54fbf591d314793fc7bfdf3b8b0f6939a', 'qascade_ce.native_playback_index_install:_motor': '16de7f78b580c46abeeb89d5e480d628f956da5f6536fad393fe02ca62507601', 'qascade_ce.native_playback_index_install:_filter': 'ea8c180dc9485d8fdfd3c614ede416f94d0f837aea9317da56c0b34667ed21a9', 'qascade_ce.native_source_install:_wrapper.<locals>.wrapped': 'c554c19cd127bd6f6560e8b196d24a5e7b53060f6e0b3cb47872493fe8791cfe', 'src.engine.timeline_bake_context:_yielding_progress.<locals>._wrapped': '3b28f1f2f37da72a25ce44abd66ff1784939501ba9e26906e527eb4441f25272', 'src.engine.timeline_autobake:TimelineAutoBaker._engine_is_playing': '4816c939b46d3c70a92fc7023d57e6ff34e82ef9941c734ff05ad8c9dacb0919', 'src.engine.timeline_autobake:TimelineAutoBaker._make_progress_cb.<locals>._cb': '8723d56d0b4d0073103de71810b3b5730fee9adb014e68c91dde6860e363ef2f', 'src.engine.timeline_autobake:TimelineAutoBaker._on_progress': '9e3206abfdcc0e9f43edadfd1c99fed185bbed0f5f62e7bf44728b6bfe86201c', 'src.engine.native_source_bridge:scalar': 'bb7725f37b4e65f6c777ac82e92b0487c6e069d47b79dd30145f5be1b0bef2ee', 'src.engine.native_source_bridge:sequential_time': '8c0f420a05fbb5beeb06554e626657370b00bd145098f576ba37bb1ff22eb22c', 'src.engine.native_source_bridge:run_mode': 'ad86dde431044067e8a45934d618d76fd4b5589ce1c3cc3d90fe1b339c1342c1', 'src.engine.native_source_bridge_sampling:auto_hold': '64fb98d38cebf83091a9d85341caa69c8db684c7758415cf5098cb7585ba41d8', 'src.engine.native_source_bridge_sampling:scalar_curve': '554e0974caada4e2272c2a6f362de97b7543424dfca4ef0e14e510f8cc618f46', 'src.engine.native_source_bridge_sampling:whole_fade': 'e39f5dbc404f28079fc4f9453c8a963e33fb6f62ee8be1edbd73ff3c0e77479d'}
_CLASS_FINGERPRINT = 'a3d2f507a7d585ee63525d3e6f146e577c4b0ef61e90187587a7eb6c83a0939e'


def _function_value(item, seen):
    if id(item) in seen:
        return ('seen', item.__code__.co_qualname)
    seen.add(id(item))
    module = item.__globals__.get('__name__')
    namespace = module in sys.modules and item.__globals__ is vars(sys.modules[module])
    return (module, namespace, _source_value(_signature(item.__code__), seen),
            _source_value(item.__defaults__, seen), _source_value(item.__kwdefaults__, seen),
            tuple(_source_value(cell.cell_contents, seen) for cell in item.__closure__ or ()))


def _collection_value(item, seen):
    if type(item) is frozenset:
        return ('frozenset', tuple(sorted((_source_value(child, seen) for child in item), key=repr)))
    if type(item) is dict:
        return tuple((key, _source_value(child, seen)) for key, child in sorted(item.items()))
    return tuple(_source_value(child, seen) for child in item)


def _source_value(item, seen):
    if type(item) is FunctionType:
        return _function_value(item, seen)
    if isinstance(item, staticmethod):
        return _source_value(item.__func__, seen)
    if type(item) in (tuple, list, dict, frozenset):
        return _collection_value(item, seen)
    if item is None or type(item) in (str, bool, int, float):
        return item
    return ('type', type(item).__module__, type(item).__qualname__)


def class_fingerprint(engine_type):
    # Pure source metadata excludes locations, native addresses and results.
    seen = set()
    rows = tuple((name, _source_value(item, seen)) for name,item in sorted(vars(engine_type).items())
                 if name.startswith(('_clip_','_track_','_playback_')) or name in
                 ('_render_track_state','_render_struct_for_tracks','_target_universes_for_clip','_indexed_active_clip_items'))
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def _exact_code(function):
    module = function.__globals__.get('__name__')
    key = module + ':' + function.__code__.co_qualname
    return (module in sys.modules and function.__globals__ is vars(sys.modules[module])
            and hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest() == EXPECTED.get(key))


def _wrapped_current(function, seen):
    wrapped = getattr(function, '__wrapped__', None)
    return wrapped is None or canonical(wrapped, seen)


def _closures_current(function, seen):
    for cell in function.__closure__ or ():
        value = cell.cell_contents
        if type(value) is FunctionType and not canonical(value, seen):
            return False
    return True


def canonical(function, seen=None):
    if type(function) is not FunctionType:
        return False
    seen = set() if seen is None else seen
    if id(function) in seen:
        return True
    seen.add(id(function))
    if not _exact_code(function):
        return False
    if not _wrapped_current(function, seen):
        return False
    return _closures_current(function, seen)


def default_methods(engine, names):
    if class_fingerprint(type(engine)) != _CLASS_FINGERPRINT or any(name in vars(engine) for name in names):
        return False
    for name in names:
        function = inspect.getattr_static(type(engine),name)
        if isinstance(function,staticmethod):
            function = function.__func__
        if not canonical(function):
            return False
    from qascade_ce import render_path
    from qascade_ce import native_playback_index_install as installed
    return (canonical(render_path.ce_indexed_active_clip_items)
            and all(canonical(function) for function in installed._ORIGINALS.values()))


def _cells(function):
    return dict(zip(function.__code__.co_freevars,
                    (cell.cell_contents for cell in function.__closure__ or ())))


def _baker_current(baker):
    from src.engine.timeline_autobake import TimelineAutoBaker
    return (type(baker) is TimelineAutoBaker
            and not any(name in vars(baker) for name in ('_on_progress', '_engine_is_playing'))
            and canonical(TimelineAutoBaker._on_progress) and canonical(TimelineAutoBaker._engine_is_playing))


def _reporter_current(original):
    reporter = _cells(original).get('progress')
    if reporter is None:
        return True
    if not canonical(reporter) or reporter.__code__.co_qualname != 'TimelineAutoBaker._make_progress_cb.<locals>._cb':
        return False
    return _baker_current(_cells(reporter).get('self'))


def default_progress(callback):
    if callback is None:
        return True
    if not canonical(callback):
        return False
    original = _cells(callback).get('progress_callback')
    if original is None:
        return True
    # Only the existing owned AutoBaker reporter has no callback that inspects
    # the intermediate preparation engine state. Foreign progress stays old.
    if not canonical(original) or original.__code__.co_qualname != '_yielding_progress.<locals>._wrapped':
        return False
    return _reporter_current(original)
