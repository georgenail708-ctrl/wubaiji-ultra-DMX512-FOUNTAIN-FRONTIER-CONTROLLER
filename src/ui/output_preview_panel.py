"""TopView workspace adapter; owned, visible-only final-output observation.

Qt/viewport reuse: r185-basic-closeout-preview.md (CLOSE-P).
"""
from time import monotonic

from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QPushButton, QStackedWidget

from ..network.output_preview_control import set_final_output_preview_enabled
from .output_preview_data import PreviewData, describe_output
from .output_preview_canvas import OutputPreviewCanvas
from .editor_materials import apply_material, deep_region


class OutputPreviewPanel(QWidget):
    def __init__(self, topview, nm, parent=None):
        super().__init__(parent or topview)
        self.topview, self.nm = topview, nm
        self.data = PreviewData()
        self.reader = None
        self._active = False
        self._status_key = None
        self._dirty = True
        self._needs_fit = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["布局", "2D 输出"])
        header.addWidget(self.mode)
        fit = QPushButton("适配 F")
        header.addWidget(fit)
        self.status = QLabel("最终发送观测 · 尚未启用")
        header.addWidget(self.status, 1)
        layout.addLayout(header)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.stack.addWidget(topview._canvas)
        self.canvas = OutputPreviewCanvas(self.data, self)
        self.stack.addWidget(self.canvas)
        self.detail = QLabel("点击查看最终 DMX · Shift 框选 · Ctrl 累计 · 中键平移 · 滚轮缩放")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        fit.clicked.connect(self._fit)
        self.canvas.picked.connect(self._pick)
        self.canvas.box_selected.connect(self._box_select)
        topview.selection_changed.connect(self._selection)
        topview.layout_changed.connect(self._invalidate_data)
        manager = topview._pm
        signal = getattr(manager, "project_changed", None)
        if signal is not None:
            signal.connect(self._invalidate_data)
        topview.installEventFilter(self)
        apply_material(self, kind="panel")
        deep_region(self.canvas)
        # Capture only NM, not this QObject, in the destruction fallback.
        self.destroyed.connect(lambda: set_final_output_preview_enabled(nm, False))

    def _mode_changed(self, index):
        target = self.canvas if index == 1 else self.topview._canvas
        self.stack.setCurrentWidget(target)
        self._sync_activity()

    def _fit(self):
        self.canvas.fit()

    def _sync_activity(self):
        active = self.isVisible() and self.mode.currentIndex() != 0
        if active == self._active:
            return
        self._active = active
        set_final_output_preview_enabled(self.nm, active)
        self.reader = self.nm.final_output_reader() if active else None
        if active:
            self._tick()
            self.timer.start()
        else:
            self.timer.stop()
            self.data.clear()
            self.status.setText("最终发送观测 · 停用")

    def _invalidate_data(self, *args):
        self._dirty = True
        self._needs_fit = True
        if self._active:
            self._tick()

    def _configure(self):
        positions = {str(item["id"]): (item["x"], item["y"])
                     for item in self.topview.get_all_fixture_positions()}
        manager = self.topview._pm
        project = getattr(manager, "project", None) if manager is not None else None
        self.data.configure(project or self.topview._project, positions)
        self._dirty = False
        self.canvas.set_selection(self.topview.selected_ids)
        if self._needs_fit:
            self.canvas.fit()
            self._needs_fit = False

    def _tick(self):
        if not self._active:
            return
        if self._dirty:
            self._configure()
        if self.reader is None:
            self.status.setText("最终发送观测不可用")
            return
        changed = self.data.update(self.reader)
        now = monotonic()
        status_changed = self._refresh_status(now)
        self._refresh_views(changed, status_changed, now)
        self._update_detail()

    def _refresh_status(self, now):
        stale = sum(now - frame.attempted_at_monotonic >= 2 for frame in self.data.frames.values())
        key = (self.reader.enabled, self.reader.epoch, len(self.data.frames), stale,
               len(self.data.by_universe), self.mode.currentIndex())
        status_changed = key != self._status_key
        if status_changed:
            self._status_key = key
            prefix = "2D 灯光 · 水流 · 喷头方向（示意）"
            state = "停用" if not self.reader.enabled else f"观测 {len(self.data.frames)}/{len(self.data.by_universe)} 域 · 陈旧 {stale}"
            self.status.setText(f"{prefix} · {state} · epoch {self.reader.epoch} · 发送尝试，非收包确认")
        return status_changed

    def _has_live_water(self, now):
        return any(output.observed and output.water > 0 and now - output.attempted_at < 2
                   for output in self.data.outputs)

    def _refresh_views(self, changed, status_changed, now):
        water = self._has_live_water(now)
        if self.mode.currentIndex() == 1 and (changed or status_changed or water):
            self.canvas.update()

    def _selection(self, ids):
        self.canvas.set_selection(ids)
        self._update_detail()

    def _box_select(self, ids, additive):
        selected = list(self.topview._ordered_selected_ids()) if additive else []
        selected.extend(fid for fid in ids if fid not in selected)
        self.topview.set_external_fixture_selection(selected, emit=True)

    def _pick(self, fixture_id, additive):
        selected = list(self.topview._ordered_selected_ids()) if additive else []
        if fixture_id in selected:
            selected.remove(fixture_id)
        else:
            selected.append(fixture_id)
        self.topview.set_external_fixture_selection(selected, emit=True)

    def _update_detail(self):
        output = next((item for item in self.data.outputs
                       if item.fixture.fixture_id in self.canvas.selected), None)
        if output is not None:
            text = describe_output(output, self.data.frames.get(output.fixture.universe))
            # Full raw channel list is also available on hover; keep chrome bounded.
            self.detail.setText(text.replace("\n", " · ")[:650])
        else:
            self.detail.setText("点击查看最终 DMX · Shift 框选 · Ctrl 累计 · 中键平移 · 滚轮缩放")

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_activity()

    def hideEvent(self, event):
        self.timer.stop()
        if self._active:
            self._active = False
            set_final_output_preview_enabled(self.nm, False)
            self.reader = None
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Close:
            self.timer.stop()
            self._active = False
            self.reader = None
            set_final_output_preview_enabled(self.nm, False)
        return super().eventFilter(watched, event)


def attach_output_preview(topview, nm):
    if hasattr(topview, "_output_preview"):
        return topview._output_preview
    canvas = topview._canvas
    original_parent = canvas.parentWidget()
    was_visible = canvas.isVisible()
    parent_layout = original_parent.layout()
    container = QWidget(original_parent)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    replaced = parent_layout.replaceWidget(canvas, container)
    if replaced is None:
        container.deleteLater()
        raise RuntimeError("TopView canvas is not present in its parent layout")
    try:
        preview = OutputPreviewPanel(topview, nm, container)
        layout.addWidget(preview)
    except Exception:
        canvas.setParent(original_parent)
        parent_layout.replaceWidget(container, canvas)
        if was_visible:
            canvas.show()
        container.deleteLater()
        raise
    # replaceWidget transfers ownership of the old QLayoutItem to this caller.
    del replaced
    topview._output_preview = preview
    return preview


def create_topview_workspace(project_manager, network_manager):
    from .topview_panel import TopViewPanel
    panel = TopViewPanel(project_manager)
    attach_output_preview(panel, network_manager)
    return panel
