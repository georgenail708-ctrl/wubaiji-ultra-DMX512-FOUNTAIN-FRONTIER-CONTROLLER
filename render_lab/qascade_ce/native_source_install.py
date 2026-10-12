"""Default public-entry installation, preserving the original error fallback."""
import functools
import inspect
import logging

from . import native_source_motion as motion
from . import native_source_motion_clock as clock
from . import native_source_motion_prelude as prelude
from src.engine import native_source_bridge as bridge
from src.engine import native_source_bridge_direction as directional
from src.engine import native_source_bridge_timing as timing
from src.engine import native_source_bridge_sampling as sampling

log = logging.getLogger(__name__)
_SOURCE_PUBLIC_CALL_CNT = [0]
_SOURCE_PUBLIC_SUCCESS_CNT = [0]
_SOURCE_PUBLIC_FALLBACK_CNT = [0]
_SOURCE_PUBLIC_ERROR_CNT = [0]
_SOURCE_PUBLIC_NATIVE_WORK_CNT = [0]
_SOURCE_PUBLIC_METADATA_CACHE_CNT = [0]
METHODS = {name: bridge.scalar for name in (
    "_clip_motor_force_profile", "_clip_motor_force_source_time", "_clip_playback_rate",
    "_clip_source_duration", "_clip_motor_phase_amount", "_clip_fade_gain")}
METHODS.update({"_clip_sequential_active_batch": bridge.active_batch,
    "_clip_sequential_source_time": bridge.sequential_time,
    "_clip_run_mode_source_time": bridge.run_mode,
    "_clip_channel_change_scores": bridge.scores,
    "_compiled_clip_channel_change_scores": bridge.scores,
    "_apply_clip_motor_force_frame": bridge.force_frame,
    "_clip_channel_fade_gains": directional.gains,
    "_clip_completed_sequential_channel_mask": directional.completed})
METHODS.update({"_clip_sequential_span": timing.span,
    "_clip_sequential_group_timing": timing.group_timing,
    "_clip_completed_sequential_batch_index": timing.completed_batch,
    "_clip_recorded_source_span": timing.recorded_span,
    "_compute_direction_positions": directional.positions})
METHODS.update({"_compiled_clip_frame_index": sampling.nearest,
    "_clip_auto_hold_cycle_time": sampling.auto_hold,
    "_clip_transition_gain": sampling.scalar_curve,
    "_clip_transition_gains": sampling.vector_curve,
    "_clip_fade_gain": sampling.whole_fade})
METHODS.update({"_clip_sequential_timing_detail": prelude.timing_detail,
    "_clip_sequential_batch_count": prelude.batch_count,
    "_clip_sequential_pauses": prelude.pauses,
    "_clip_sequential_offsets": prelude.offsets})
SCHEMAS = {
    "_clip_sequential_timing_detail": ("clip", {}),
    "_clip_sequential_batch_count": ("clip groups", {"groups": None}),
    "_clip_sequential_pauses": ("clip groups fallback", dict(groups=None, fallback=None)),
    "_clip_sequential_offsets": ("step pauses batch_count", {"batch_count": None}),
    "_clip_motor_force_profile": ("clip", {}),
    "_clip_motor_force_source_time": ("clip source_t source_duration", {}),
    "_clip_playback_rate": ("clip", {}),
    "_clip_source_duration": ("clip timeline_duration", {}),
    "_clip_motor_phase_amount": ("clip", {}),
    "_clip_fade_gain": ("clip local_t", {}),
    "_clip_sequential_active_batch": ("local_t single pauses batch_count step", {"step": None}),
    "_clip_sequential_source_time": ("clip local_t source_duration", {}),
    "_clip_run_mode_source_time": ("clip source_t source_duration", {}),
    "_clip_channel_change_scores": ("clip universe source_t timeline_now clip_start active hold_extension",
                                     {"hold_extension": False}),
    "_compiled_clip_channel_change_scores": ("clip universe source_t timeline_now clip_start active compiled hold_extension",
                                              {"hold_extension": False}),
    "_apply_clip_motor_force_frame": ("clip universe source_t source_duration raw compiled proj", {}),
    "_clip_channel_fade_gains": ("clip local_t universe u_fixtures track fixture_defs_by_id proj compiled",
        dict(track=None, fixture_defs_by_id=None, proj=None, compiled=None)),
    "_clip_completed_sequential_channel_mask": ("clip local_t universe u_fixtures track fixture_defs_by_id proj compiled",
        dict(track=None, fixture_defs_by_id=None, proj=None, compiled=None)),
    "_clip_sequential_span": ("clip groups pause single step", {"step": None}),
    "_clip_sequential_group_timing": ("clip", {}),
    "_clip_completed_sequential_batch_index": ("clip local_t", {}),
    "_clip_recorded_source_span": ("clip source_duration", {}),
    "_compute_direction_positions": ("direction K", {}),
    "_compiled_clip_frame_index": ("compiled source_t", {}),
    "_clip_auto_hold_cycle_time": ("clip local_t", {}),
    "_clip_transition_gain": ("clip progress fade_in", {}),
    "_clip_transition_gains": ("clip tt fade_in ease_lut", {"ease_lut": None}),
}


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_SOURCE_PUBLIC_") and name.endswith("_CNT")}


def _schema(name, function):
    parameters = inspect.signature(function).parameters
    if any(value.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
           for value in parameters.values()):
        fields, defaults = SCHEMAS[name]
        return fields.split(), defaults
    names = [name for name in parameters if name != "self"]
    defaults = {name: value.default for name, value in parameters.items()
                if value.default is not inspect.Parameter.empty}
    return names, defaults


def _call(engine, original, handler, request):
    _SOURCE_PUBLIC_CALL_CNT[0] += 1
    if motion.available() and clock.available():
        try:
            before = motion._SOURCE_MOTION_SUCCESS_CNT[0] + clock._SOURCE_CLOCK_SUCCESS_CNT[0]
            result = handler(engine, request)
            _SOURCE_PUBLIC_SUCCESS_CNT[0] += 1
            after = motion._SOURCE_MOTION_SUCCESS_CNT[0] + clock._SOURCE_CLOCK_SUCCESS_CNT[0]
            _record_work(after != before)
        except Exception:
            _SOURCE_PUBLIC_ERROR_CNT[0] += 1
            log.exception("native public source failed: %s", request["name"])
        else:
            if isinstance(result, sampling.NativeSourceMathError):
                raise result.error
            return result
    _SOURCE_PUBLIC_FALLBACK_CNT[0] += 1
    return original(engine, *request["positional"], **request["keyword"])


def _record_work(computed):
    target = _SOURCE_PUBLIC_NATIVE_WORK_CNT if computed else _SOURCE_PUBLIC_METADATA_CACHE_CNT
    target[0] += 1


def _wrapper(name, original, handler):
    names, defaults = _schema(name, original)

    @functools.wraps(original)
    def wrapped(engine, *args, **kwargs):
        values = defaults.copy()
        values.update(zip(names, args))
        values.update(kwargs)
        request = dict(name=name, args=values, positional=args, keyword=kwargs)
        return _call(engine, original, handler, request)

    return wrapped


def _static_wrapper(name, original, handler):
    names, defaults = _schema(name, original)

    def fallback(engine, *args, **kwargs):
        return original(*args, **kwargs)

    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        values = defaults.copy()
        values.update(zip(names, args))
        values.update(kwargs)
        request = dict(name=name, args=values, positional=args, keyword=kwargs)
        return _call(None, fallback, handler, request)

    return staticmethod(wrapped)


def _window(clip, single, pauses, batches, delay):
    direction = str(getattr(clip, "fade_in_direction", "none") or "none").strip().lower()
    transition = getattr(clip, "fade_in", 0.) if direction not in ("", "none") else 0.
    raw = [getattr(clip, "duration", 0.) or 0., single, delay, transition or 0.]
    result, changed = clock.invoke(1, [batches, len(pauses), 2], raw, pauses)
    if result[2]:
        from src.engine.playback_engine import _log_seq_clamp
        _log_seq_clamp(clip, result[4], raw[0], result[3],
                       dict(seq_in_c=delay, transitional=transition, batches=batches))
    return float(result[0]), changed.tolist(), None if result[1] < 0. else float(result[1])


def _remap_original(engine, clip, copy_map, context):
    return context["fallback"](copy_map, bool(context["seed"]))


def _remap_native(engine, request):
    from src.engine.native_source_bridge_remap import render
    args = request["args"]
    return render(engine, args["clip"], args["copy_map"], args["context"])


def _remap_dispatch(engine, clip, copy_map, context):
    request = dict(name="_clip_sequential_remapped_source_frame",
        args=dict(clip=clip, copy_map=copy_map, context=context),
        positional=(clip, copy_map, context), keyword={})
    return _call(engine, _remap_original, _remap_native, request)


def install(engine):
    """Call after binding the domain DLL; mount repeatedly without stacking wrappers."""
    from src.engine import playback_engine as public
    if not motion.available() and not motion.install(engine):
        return False
    if not prelude.install(engine):
        return False
    cls = public.PlaybackEngine
    if cls.__dict__.get("_ce_source_public_installed", False):
        return True
    for name, handler in METHODS.items():
        original = getattr(cls, name)
        factory = _static_wrapper if name == "_compiled_clip_frame_index" else _wrapper
        setattr(cls, name, factory(name, original, handler))
        if name == "_clip_channel_fade_gains":
            cls._ce_source_direction_bundle = _wrapper(name, original, directional.bundle)
    cls._ce_source_remap = _remap_dispatch
    public._sequential_single_window = _window
    public._recorded_cycle = timing.recorded_cycle
    public._hold_score_cap = clock.hold_cap
    cls._ce_source_public_installed = True
    return True
