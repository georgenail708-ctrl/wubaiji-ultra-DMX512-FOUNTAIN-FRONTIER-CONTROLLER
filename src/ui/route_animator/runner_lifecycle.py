"""Runner 视图与输出生命周期：复用原计时器、场景与显式释放入口。"""
from __future__ import annotations

def hide_view(panel) -> None:
    """运行中的原帧表继续计算；隐藏只停页面轮询和空闲帧表。"""
    if not panel._playing:
        panel._frame_timer.stop()
    panel._page_poll_timer.stop()


def stop_output(panel) -> None:
    """显式停止复用调度释放，清遮罩并同步原播放按钮。"""
    if not panel.dispatch_release_state({}):
        raise RuntimeError("Runner output release failed")
    panel._play_btn.setChecked(False)


def stop_window_runner(window) -> None:
    """全清空与 Main 真退出共用同一 Runner 释放入口。"""
    panel = getattr(window, "_route_animator_window", None)
    if panel is not None:
        stop_output(panel)


def discard_project_runtime(panel) -> None:
    """仅在工程身份改变时丢弃旧场景与设备；普通视图返回不重建。"""
    from .runner_audio_mod import apply_scene_config
    from .runner_envelope import finish_scene_restore
    stop_output(panel)
    panel._page_scenes = {}
    panel._save_slots = {}
    panel._current_page_id = None
    panel._clear_all_trains()
    panel._clear_all_tracks()
    panel._clear_all_switches()
    panel.set_devices([])
    panel._last_active_ids = []
    panel._last_intensity = {}
    apply_scene_config(panel, None)
    panel._rebuild_engine()
    finish_scene_restore(panel)


def refresh_frame_ui(panel, frame) -> None:
    """可见时更新原画布/HUD；原计算、发布和遮罩仍在面板帧循环。"""
    if not panel.isVisible():
        return
    from .runner_envelope_strip import refresh_strips
    from .runner_hud_overview import refresh_overview
    panel._canvas.set_train_visuals(panel._frame_to_visuals(frame))
    panel._canvas.set_coverage_ids(frame.active_ordered_ids)
    mid = getattr(panel, "_status_mid", None)
    if mid is not None:
        mid.setText(f"已激活 {len(frame.active_ordered_ids)} 台"
                    f" / 本页 {len(panel._device_positions)} 台")
    right = getattr(panel, "_status_right", None)
    if right is not None:
        mode = "CE" if panel._engine._use_ce() else "PY"
        right.setText(f"{mode} · {panel._frame_ms_ema:.1f}ms/帧")
    panel._fleet_tick = (panel._fleet_tick + 1) % 5
    if panel._fleet_tick == 0 and getattr(panel, "_fleet_tree", None) is not None:
        panel._refresh_fleet_tree()
    if panel._fleet_tick == 0:
        refresh_overview(panel)
        refresh_strips(panel)
