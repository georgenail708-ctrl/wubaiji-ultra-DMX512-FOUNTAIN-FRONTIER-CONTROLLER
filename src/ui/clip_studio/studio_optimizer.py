"""Explicit Studio action adapter for the existing Timeline optimizer.

No graph compilation or load operation opens a dialog. Accepted edits use the
original optimizer and one joint Studio/Timeline undo snapshot. The legacy
``preset`` label remains readable from saved files, never an editable receipt.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from src.ui.clip_studio.studio_session import no_inner_snapshots


def optimize_bound_clips(timeline, clip_ids, snapshot_fn) -> int:
    """Run the original modal optimizer; cancel does not create an undo entry."""
    if timeline is None:
        return 0
    valid = [cid for cid in dict.fromkeys(map(str, clip_ids))
             if timeline.ctrl._find_track_and_clip(cid)[1] is not None]
    if len(valid) < 2:
        return 0
    snapped = False

    def snapshot_once():
        nonlocal snapped
        if not snapped:
            snapped = True
            if snapshot_fn is not None:
                snapshot_fn()

    # Existing context owns restoration even if the modal action raises.
    with no_inner_snapshots(timeline):
        timeline._snapshot_for_undo = snapshot_once
        return int(timeline._on_clip_optimize_transitions(valid) or 0)


def run_optimizer_action(panel, node) -> None:
    """Only an explicit action on an enabled, sufficiently bound card may run."""
    if node.get("disabled"):
        panel._on_canvas_notice("请先启用过渡优化卡。")
        return
    sheet_id = str(panel._state.get("active_sheet", ""))
    ids = [str(c.id) for c in panel._bound_clips(sheet_id)]
    if len(set(ids)) < 2:
        panel._on_canvas_notice("请先为本页绑定至少两个 Clip，再执行过渡优化。")
        return
    callback = panel._optimize_fn
    if callback is None:
        panel._on_canvas_notice("当前窗口未连接过渡优化器。")
        return
    changed = callback(ids)
    if changed:
        panel._refresh_compile_status(reset_canvas=False)
        panel._on_canvas_notice(f"已优化 {changed} 对 Clip 的过渡参数。")


class OptimizerActionForm(QWidget):
    """Inspector action with the same flush/discard lifecycle as parameter forms."""

    def __init__(self, panel, node_id, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel("打开原过渡优化器，确认后将参数保存到本页绑定的 Clip。")
        label.setWordWrap(True)
        layout.addWidget(label)
        self._button = QPushButton("执行优化…")
        self._button.clicked.connect(lambda: panel._on_card_edit(node_id))
        layout.addWidget(self._button)

    def flush(self) -> None:
        """Actions have no delayed parameter edits."""

    def discard(self) -> None:
        self._button.setEnabled(False)
