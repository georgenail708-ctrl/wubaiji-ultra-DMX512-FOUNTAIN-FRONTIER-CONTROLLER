"""Owned K2 v2 entry: current task semantics, native math for every frame.

The legacy TimelineCtx ABI and eligibility guards remain intact for callers
using static tables. No historical counter represents prepared-frame success.
"""
import logging
import os
import sys

from src.engine.prepared_frame import FrameBatch

log = logging.getLogger(__name__)
NATIVE = None
STATISTICS = dict(contexts=0, eval_batches=0, eval_tasks=0, override_tasks=0,
                  eval_errors=0, float_errors=0, resolve_batches=0, resolve_groups=0,
                  resolve_candidates=0, resolve_errors=0,
                  completed_frames=0, evaluated_frames=0, memo_resolved_frames=0,
                  no_pending_groups=0, memo_only_frames=0, memo_hits=0)


def counters():
    values = dict(STATISTICS)
    if NATIVE is not None:
        values.update({"ce_" + key: value for key, value in NATIVE.counters().items()})
    for name in ("native_audio_api", "native_output_domain", "native_source_motion", "native_output_filter", "native_transition",
                 "native_source_motion_clock", "native_source_install",
                 "native_source_motion_geometry", "native_source_motion_geometry_install",
                 "native_source_motion_prelude", "native_render_timing",
                 "native_legacy_direction_install", "native_legacy_parallel", "native_curve_lut",
                 "native_trajectory", "native_curve_scalar", "native_programmer_trajectory",
                 "native_anchor_public", "native_clip_change_times", "native_filter_prepare", "native_fade_target"):
        module = sys.modules.get(__package__ + "." + name)
        accessor = getattr(module, "counters", None)
        if accessor is not None:
            values.update({name + "_" + key: value for key, value in accessor().items()})
    return values


def install(engine):
    global NATIVE
    if os.environ.get("QASCADE_CE_TIMELINE_RANGE", "0") != "1":
        return False
    from .prepared_native import PreparedNative
    from src.engine.playback_engine import PlaybackEngine
    native = PreparedNative.from_engine(engine)
    if native is None:
        return False
    NATIVE = native
    setattr(sys.modules[__package__], "_K2_PREPARED_CNT", STATISTICS)
    PlaybackEngine._ce_prepared_frame_factory = staticmethod(lambda: FrameBatch(native, STATISTICS))
    log.info("[CE] K2 prepared whole-frame ABI 1 enabled")
    return True


def table_render_cadence(engine, position):
    """Original task-table index cadence, including its hold seam latch inputs."""
    from . import native_source_motion_clock as clock
    if clock.available():
        flags, values = _cadence_inputs(engine, position)
        return float(clock.invoke_scalar(15, flags, values)[0])
    return _python_table_cadence(engine, position)


def _cadence_inputs(engine, position):
    try:
        interval = float(engine._timer.interval())
    except Exception:
        interval = 0.0
    previous = getattr(engine, "_last_render_time", None)
    if previous is not None:
        try:
            return [1], [interval, float(position), float(previous)]
        except Exception:
            pass
    return [0], [interval, 0.0, 0.0]


def _python_table_cadence(engine, position):
    """Unmounted compatibility only; installed CE owns cadence arithmetic."""
    try:
        interval = float(engine._timer.interval()) / 1000.0
    except Exception:
        interval = 0.0
    if interval < 1e-4:
        interval = 0.025
    previous = getattr(engine, "_last_render_time", None)
    if previous is not None:
        try:
            delta = float(position) - float(previous)
        except Exception:
            delta = 0.0
        if 1e-4 <= delta <= 0.2:
            interval = max(interval, delta)
    return min(max(interval, 0.005), 0.08)


def prepared_tables(engine, inputs, timeline_module):
    if not getattr(engine, "_timeline_bake_owned", False):
        return None
    if getattr(engine, "_ce_prepared_frame_factory", None) is None:
        return None
    native_engine, project, frame_count = inputs
    network = getattr(engine, "_nm", None)
    all_legacy_vetoed = bool(getattr(network, "_xy_reference_by_universe", None))
    all_legacy_vetoed = all_legacy_vetoed or bool(getattr(engine, "light_tracking_enabled", False))
    if not all_legacy_vetoed:
        # Mixed ranges keep their real legacy native table frames. Only their
        # canonical fallback frames use prepared math; old continuation ABI stays.
        return None
    ctx = timeline_module.TimelineCtx(engine=native_engine,
                                     max_uni=timeline_module.k2_max_uni(project))
    if not ctx.available:
        ctx.destroy()
        return None
    try:
        ctx.build_tables(frame_count, [[] for _ in range(frame_count)])
    except BaseException:
        ctx.destroy()
        raise
    engine._ce_indexed_mask_cache = {}
    engine._ce_scope_cache = {}
    STATISTICS["contexts"] += 1
    # Keep original range writer, progress, after_frame and checkpoint lifecycle.
    # No speculative static table of dynamic LT/XY/recorded ownership is built.
    return ctx, set(), {}, None, {}, {}, {}, {}, {}, {}
