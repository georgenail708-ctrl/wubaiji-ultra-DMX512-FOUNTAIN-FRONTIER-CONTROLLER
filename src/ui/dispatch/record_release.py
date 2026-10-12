"""Release recorded live scenes using existing scene lifecycle boundaries.

Stop only saved scene authors; do not stop unrelated timeline nodes or seek its
playhead. Clear activation order before fade/chase callbacks can restore a cue.
"""
import logging

from .scene_release import reset_scene_runtime_state, _mark_scene_released
from .store_area_ops import _entries_of
from .chase_runner_ui import teardown_chase


def finish_record_outputs(window):
    window._pause_live_effect_outputs()
    window._auto_release_after_record()


def release_recorded_scenes(window):
    panel = getattr(window, '_dispatch_widget', None)
    if panel is None:
        return
    ids = list(panel.list.active_ids() or ())
    entries = [entry for cid in ids for entry in _entries_of(cid)]
    reset_scene_runtime_state(panel)
    teardown_chase(panel)
    key_filter = getattr(panel, '_scene_key_filter', None)
    if key_filter is not None:
        key_filter._held.clear()
    getattr(panel, '_mouse_flash_held', set()).clear()
    panel.list._pressed_iid = None
    release = getattr(panel, '_release_nodes', None)
    if entries and callable(release) and not release(entries):
        logging.getLogger(__name__).warning('录制结束场景节点未全部释放')
    for cid in ids:
        _mark_scene_released(cid)
    panel.refresh_monitor()
    panel._status.setText('录制已停止，激活场景已释放')


def reset_timeline_record_runtime(engine):
    """Reset release history under the same lock used by live CE/Python frames.

    Main record-stop already pauses writers before clearing layers. Direct
    auto-release may leave an unrelated Timeline playing; its original frame
    owner is serialized here, without stopping its clock or retiring stores.
    """
    from ...engine.timeline_runtime_release import release_playback_runtime
    lock = getattr(engine, "_render_lock", None)
    if lock is None:
        engine.invalidate_all_caches()  # Preserve the original foreign-engine path.
        return
    with lock:
        release_playback_runtime(engine)
