"""R35 菜单/Studio平滑操作；整组选片一次历史，失败不留下半处理。"""
from PyQt6.QtWidgets import QMessageBox

from ...engine.clip_smoothing import commit_smoothing, prepare_smoothing


def apply_smoothing(widget, clip_id, preset_id, custom=None):
    """共用真实入口；准备全组成功后才拍快照和提交。"""
    target_ids = widget._resolve_context_clip_ids(clip_id)
    prepared = []
    try:
        for cid in target_ids:
            _, clip = widget.ctrl._find_track_and_clip(cid)
            if clip is not None:
                prepared.append((cid, clip, prepare_smoothing(clip, preset_id, custom, widget.ctrl._session)))
    except (ValueError, TypeError, MemoryError) as exc:
        QMessageBox.warning(widget, "平滑处理", f"平滑未应用：{exc}")
        return False
    if not prepared:
        return False
    widget._snapshot_for_undo()
    for _, clip, change in prepared:
        commit_smoothing(clip, change)
    widget._refresh_clips_targeted([cid for cid, _, _ in prepared], reset_waveform=True)
    widget._mark_project_dirty()
    name = prepared[0][2].preset_name
    action = f"应用预设「{name}」" if name else "恢复未处理录制"
    QMessageBox.information(widget, "平滑处理", f"已对 {len(prepared)} 个 Clip {action}。")
    return True
