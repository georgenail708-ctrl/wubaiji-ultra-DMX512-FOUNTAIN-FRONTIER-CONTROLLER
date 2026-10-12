"""One worker-owned original PlaybackEngine; publish only a fresh full result.

Reuse parallel_render's ownership construction, not its pool/lead-in/environment.
Qt objects are created and retired on the calling bake thread. No live transport
or mutable compilation state is borrowed, quiesced, or restored.
"""
from __future__ import annotations

from contextlib import nullcontext
import time

from PyQt6 import sip

from src.core.project_manager import ProjectManager
from src.network.network_manager import NetworkManager
from .timeline_bake_inputs import check_cancel, inputs_current, snapshot_inputs
from .timeline_cache_notifications import remember_published_inputs
from .audio_mod_draw_dsp import isolated_rule_history
from .timeline_incremental_checkpoint import prepare_owned_incremental, publish_complete
from .timeline_cache_read import prepare_cache_reads
from .timeline_publication_inputs import publication_inputs_current
from .timeline_frame_writer import owned_frame_writer_scope


_CACHE_FIELDS = (
    "_timeline_render_frame_cache", "_timeline_render_record_source_frame_cache",
    "_timeline_render_frame_cache_signature", "_timeline_render_frame_cache_interval",
    "_timeline_render_frame_cache_end", "_timeline_render_lock_signature",
    "_timeline_render_runtime_signature", "_timeline_render_lock_misses",
)


def _owned_engine(inputs, manager):
    from .playback_engine import PlaybackEngine
    manager._project = inputs["project"]
    network = NetworkManager(manager)
    network.set_fixture_defaults_from_project(manager.project)
    for name, value in inputs["network"].items():
        setattr(network, name, value)
    engine = PlaybackEngine(network, manager, parent=manager)
    engine._timeline_bake_owned = True
    engine._timer.setInterval(inputs["interval"])
    for name, value in inputs["seed"].items():
        setattr(engine, name, value)
    # A complete offline show starts at zero, independent of live hold history.
    # Reuse seek's original reset on the owned copies, never on live transport.
    engine.seek(0.0)
    if engine._timeline_render_runtime_signature_value() != inputs["runtime"]:
        from .playback_engine import TimelineBakeAbort
        raise TimelineBakeAbort("runtime changed while preparing the owned worker")
    from .transition_anchor_geometry import prepare_source_geometry
    prepare_source_geometry(engine, inputs)
    return engine, network


def _yielding_progress(progress):
    """★ R195/P0：烘焙线程逐段让出 GIL——用户实机采样显示烘焙+私有渲染把
    单核打满、Qt 主线程饿死（全界面卡）。R188 既定手法：每拍进度回调
    sleep 1.5ms 显式让出（仅烘焙线程，墙钟代价可忽略）。"""
    if progress is None:
        return None

    def _wrapped(*args, **kwargs):
        time.sleep(0.0015)
        return progress(*args, **kwargs)
    return _wrapped


def _compile_and_render(engine, project, opts):
    from .playback_engine import _bake_timeline_frames_locked
    progress = _yielding_progress(opts.get("progress_callback"))
    engine._modulation_gain = 1.0
    engine._timeline_render_cache_building_lock = True
    try:
        engine.precompile_project_clips(project, progress_callback=lambda current, total, _clip: (
            engine._report_timeline_render_progress(
                progress, "compile", current, total, "正在编译片段缓存",
                .05 + .20 * current / max(1, total))))
    finally:
        engine._timeline_render_cache_building_lock = False
    check_cancel(opts)
    signature = engine._cached_project_clip_layout_signature(project.tracks or [])
    runtime = engine._timeline_render_runtime_signature_value()
    engine._report_timeline_render_progress(progress, "render", 0, 1, "正在生成逐帧缓存", .25)
    with owned_frame_writer_scope() if getattr(engine, "_timeline_bake_owned", False) else nullcontext():
        return _bake_timeline_frames_locked(engine, project, opts, signature, runtime)


def _publish(live, worker, inputs, opts):
    prepare_cache_reads(worker, opts)
    if not publication_inputs_current(live, inputs, opts, current=inputs_current):
        return "stale"
    # Expensive fingerprinting stays outside the small epoch publication lock.
    with opts.get("publish_lock", nullcontext()):
        check_cancel(opts)
        if live._pm.project is not inputs["source"]:
            return "stale"
        for name in _CACHE_FIELDS:
            setattr(live, name, getattr(worker, name))
        live._timeline_render_frame_cache_signature = inputs["layout"]
        live._timeline_render_lock_signature = inputs["layout"]
        live._timeline_render_runtime_signature = inputs["runtime"]
        # Worker-local compilation never certifies the live owner's dictionaries.
        live._timeline_render_cache_locked = True
        # Only this original full-result gate certifies full-cache truth.
        live._timeline_ready_range = None
        live._timeline_ready_served = None
        publish_complete(live, worker)
        remember_published_inputs(live, inputs)
        from .timeline_full_temporal import attach_full_temporal
        attach_full_temporal(live)
    return "done"


def bake_in_owned_context(live, project, opts):
    from .playback_engine import TimelineBakeAbort
    manager = ProjectManager()
    worker = network = None
    inputs = None
    try:
        live._report_timeline_render_progress(
            opts.get("progress_callback"), "prepare", 0, 1, "准备独立时间线预渲染", 0.)
        inputs = snapshot_inputs(live, project, manager, opts)
        worker, network = _owned_engine(inputs, manager)
        prepare_owned_incremental(live, worker, inputs, opts)
        with isolated_rule_history():
            from .temporal_journal_range import capture_owned_origin
            capture_owned_origin(worker, opts)
            status = _compile_and_render(worker, manager.project, opts)
        if status != "done":
            return status
        return _publish(live, worker, inputs, opts)
    except TimelineBakeAbort:
        from .timeline_ready_range import publish_handoff
        return publish_handoff(live, worker, inputs, opts)
    finally:
        if worker is not None:
            worker.stop_gen_thread()
            worker._timer.stop()
        if network is not None:
            network.stop()
        # All owned child timers/workers are stopped on their owning thread;
        # no worker event loop is required for a queued DeferredDelete.
        if not sip.isdeleted(manager):
            sip.delete(manager)
