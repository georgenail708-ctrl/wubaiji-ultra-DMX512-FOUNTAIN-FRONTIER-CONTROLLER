"""AM23 ordered processor editor; original Draw Apply/undo own submission.

Reference: /ordered device chain and original Qt Draw inspector.
Only native controls and backend catalog; no Clip generation, audio worker,
runtime processor evaluation, extra output, screenshots or polling timer.
"""
from __future__ import annotations

from copy import deepcopy

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt6.QtGui import QKeyEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import qascade_theme as theme
from ..widget_kit import scope_qss
from ..canvas_materials import tone
from ..flow_layout import FlowLayout
from ..main_window_shortcuts import _text_focus
from .draw_processor_draft import catalog
from ...engine.audio_mod_processor_schema import SOURCE_KEYS
from ...engine.xy_reference import channel_type_key

_SOURCES = {
    "energy": "整体能量", "beat_strength": "节拍强度", "band_low": "低频能量",
    "band_mid": "中频能量", "band_high": "高频能量", "onset_flux": "重音变化",
}


def _diagram_paths():
    """Small function diagrams built once; these illustrate roles, not output."""
    paths = {}
    gate = QPainterPath(QPointF(5, 75))
    for x, y in ((15, 75), (15, 25), (30, 25), (30, 75), (45, 75),
                 (45, 25), (60, 25), (60, 75), (75, 75), (75, 25), (90, 25)):
        gate.lineTo(x, y)
    paths["slice"] = gate
    envelope = QPainterPath(QPointF(5, 75))
    envelope.cubicTo(25, 75, 25, 15, 40, 20)
    envelope.cubicTo(55, 20, 70, 75, 95, 75)
    paths["accent"] = envelope
    motion = QPainterPath(QPointF(12, 50))
    for x, y in ((88, 50), (76, 40), (88, 50), (76, 60)):
        motion.lineTo(x, y)
    motion.moveTo(24, 40)
    motion.lineTo(12, 50)
    motion.lineTo(24, 60)
    motion.moveTo(50, 28)
    motion.lineTo(50, 72)
    paths["motion"] = motion
    color = QPainterPath()
    for x, height in ((15, 42), (42, 60), (69, 30)):
        color.addRoundedRect(QRectF(x, 80 - height, 16, height), 2, 2)
    paths["color"] = color
    return paths


_DIAGRAMS = _diagram_paths()
_DIAGRAM_PEN = QPen(tone(theme.CANVAS_SELECTION), 2)


class _ProcessorDiagram(QWidget):
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self._kind = kind
        self.setFixedHeight(90)
        self.setToolTip("功能示意；真实数值与输出由原播放链处理")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), tone(theme.CANVAS_CARD))
        painter.save()
        painter.translate(8, 4)
        painter.scale(max(1, self.width() - 16) / 100, 0.7)
        painter.setPen(_DIAGRAM_PEN)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = _DIAGRAMS.get(self._kind)
        if path is not None:
            painter.drawPath(path)
        painter.restore()
        painter.setFont(theme.cached_font(10))
        painter.setPen(tone(theme.CANVAS_MUTED))
        painter.drawText(QRectF(4, 70, self.width() - 8, 18), Qt.AlignmentFlag.AlignCenter,
                         "处理示意 · 参数应用后生效")
        painter.end()


class ProcessorChainPage(QWidget):
    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self._draft = panel._processor_draft
        self._reading = False
        self._selected_id = ""
        self._fields = []
        self._form_epoch = 0
        self._feedback = None
        self.setObjectName("drawProcessorChain")
        self.installEventFilter(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._enabled = QCheckBox("启用处理链（应用后处理原 Clip）")
        self._enabled.setObjectName("processorChainEnabled")
        self._enabled.installEventFilter(self)
        self._enabled.toggled.connect(self._mode_changed)
        root.addWidget(self._enabled)
        self._scope = QLabel()
        self._scope.setWordWrap(True)
        root.addWidget(self._scope)
        actions = QWidget()
        flow = FlowLayout(actions)
        flow.setContentsMargins(0, 0, 0, 0)
        for kind, spec in catalog().items():
            button = QPushButton(f"＋ {spec.get('label', kind)}")
            button.setObjectName(f"processorAdd_{kind}")
            button.setToolTip(str(spec.get("description", "")))
            button.installEventFilter(self)
            button.clicked.connect(lambda _checked=False, key=kind: self._add(key))
            flow.addWidget(button)
        root.addWidget(actions)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        self._path = QLabel("原 Clip 输入 → 按列表顺序处理 → 原输出")
        self._path.setWordWrap(True)
        column.addWidget(self._path)
        self._table = QTableWidget(0, 4)
        self._table.setObjectName("processorChainRows")
        self._table.setHorizontalHeaderLabels(["顺序", "处理器", "状态 / 干湿", "音频源 / 目标"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._selected)
        self._table.installEventFilter(self)
        column.addWidget(self._table, 1)
        order = QHBoxLayout()
        for text, name, callback in (
            ("上移", "processorMoveUp", lambda: self._move(-1)),
            ("下移", "processorMoveDown", lambda: self._move(1)),
            ("删除", "processorRemove", self._remove),
        ):
            button = QPushButton(text)
            button.setObjectName(name)
            button.clicked.connect(callback)
            button.installEventFilter(self)
            order.addWidget(button)
        column.addLayout(order)
        split.addWidget(left)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(250)
        body = QWidget()
        self._form = QFormLayout(body)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll.setWidget(body)
        split.addWidget(scroll)
        split.setChildrenCollapsible(False)
        split.setSizes([700, 300])
        root.addWidget(split, 1)
        self._status = QLabel()
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        scope_qss(self._table, f"background:{theme.CANVAS_CARD};color:{theme.CANVAS_INK};")
        self.refresh()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            if isinstance(event, QKeyEvent) and not _text_focus():
                mods = event.modifiers()
                key = event.key()
                if mods & Qt.KeyboardModifier.ControlModifier and key in (Qt.Key.Key_Z, Qt.Key.Key_Y):
                    event.accept()
                    if event.type() == QEvent.Type.KeyPress:
                        redo = key == Qt.Key.Key_Y or bool(mods & Qt.KeyboardModifier.ShiftModifier)
                        (self._panel._canvas.redo if redo else self._panel._canvas.undo)()
                    return True
        return super().eventFilter(watched, event)

    def _mode_changed(self, enabled):
        if not self._reading:
            self._draft.set_enabled(enabled)

    def _add(self, kind):
        self._selected_id = self._draft.add(kind)
        self.refresh()

    def _move(self, delta):
        self._draft.move(self._selected_id, delta)

    def _remove(self):
        self._draft.remove(self._selected_id)

    def _row(self, row_id=None):
        target = self._selected_id if row_id is None else row_id
        return next((row for row in self._draft.rows if row.get("id") == target), None)

    def _selected(self):
        if self._reading:
            return
        index = self._table.currentRow()
        if 0 <= index < len(self._draft.rows):
            self._selected_id = self._draft.rows[index]["id"]
        self._rebuild_form()

    def refresh(self):
        self._reading = True
        self._enabled.setChecked(self._draft.mode == "chain")
        self._table.setRowCount(len(self._draft.rows))
        active = -1
        for index, row in enumerate(self._draft.rows):
            for col, text in enumerate(self._row_cells(index, row)):
                self._table.setItem(index, col, QTableWidgetItem(text))
            if row.get("id") == self._selected_id:
                active = index
        if active < 0 and self._draft.rows:
            active = 0
            self._selected_id = self._draft.rows[0]["id"]
        if active >= 0:
            self._table.selectRow(active)
        self._table.resizeColumnsToContents()
        self._reading = False
        self._rebuild_form()
        self.refresh_status()

    def _row_cells(self, index, row):
        spec = catalog()[row["kind"]]
        wet = row["wet"]
        state = "旁路" if not row["enabled"] or wet == 0 else "处理"
        targets = ", ".join(row["targets"]) or "尚未选择目标"
        source = _SOURCES.get(row["source"], row["source"])
        return [f"{index + 1:02d}", spec["label"], f"{state} · {wet:.0%}",
                f"{source} · {targets}"]

    def _clear_form(self):
        self._form_epoch += 1
        self._fields = []
        while self._form.count():
            item = self._form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _field(self, label, widget, name):
        widget.setObjectName(name)
        widget.installEventFilter(self)
        self._fields.append(widget)
        self._form.addRow(label, widget)

    def _edit(self, row_id, key, value, *, parameter=False):
        row = self._row(row_id)
        if row is None:
            return
        if parameter:
            params = deepcopy(row.get("params") or {})
            params[key] = value
            self._draft.edit(row_id, {"params": params})
        else:
            self._draft.edit(row_id, {key: value})

    def _guard(self, row_id, key, reader=None, *, parameter=False):
        epoch = self._form_epoch
        def commit(value=None):
            if epoch == self._form_epoch:
                self._edit(row_id, key, reader() if reader else value, parameter=parameter)
        return commit

    def _rebuild_form(self):
        self._clear_form()
        row = self._row()
        if row is None:
            label = QLabel("添加实际处理器后，在这里编辑音频源、目标和参数。")
            label.setWordWrap(True)
            self._form.addRow(label)
            return
        row_id = row["id"]
        spec = catalog()[row["kind"]]
        description = QLabel(str(spec.get("description", "")))
        description.setWordWrap(True)
        self._form.addRow(str(spec.get("label", row["kind"])), description)
        self._form.addRow(_ProcessorDiagram(row["kind"]))
        enabled = QCheckBox("参与处理（关闭即旁路）")
        enabled.setChecked(bool(row.get("enabled", True)))
        enabled.toggled.connect(self._guard(row_id, "enabled"))
        self._field("状态", enabled, "processorEnabled")
        self._number("干湿", "wet", {"min": 0, "max": 1, "step": .01},
                     row.get("wet", 1), row_id=row_id, parameter=False)
        source = QComboBox()
        for key in SOURCE_KEYS:
            source.addItem(_SOURCES.get(key, key), key)
        source.setCurrentIndex(max(0, source.findData(row.get("source"))))
        source.activated.connect(self._guard(row_id, "source", source.currentData))
        self._field("音频控制", source, "processorSource")
        self._targets(row, spec)
        for key, field in spec.get("params", {}).items():
            self._parameter(row, key, field)

    def _parameter(self, row, key, field):
        value = (row.get("params") or {}).get(key, field.get("default"))
        if isinstance(value, bool):
            check = QCheckBox()
            check.setChecked(value)
            check.toggled.connect(self._guard(row["id"], key, parameter=True))
            self._field(field.get("label", key), check, f"processorParam_{key}")
        elif field.get("choices"):
            combo = QComboBox()
            for choice in field["choices"]:
                data, label = choice if isinstance(choice, (tuple, list)) else (choice, choice)
                combo.addItem(str(label), data)
            combo.setCurrentIndex(max(0, combo.findData(value)))
            combo.activated.connect(self._guard(row["id"], key, combo.currentData, parameter=True))
            self._field(field.get("label", key), combo, f"processorParam_{key}")
        else:
            self._number(field.get("label", key), key, field, value, row_id=row["id"])

    def _number(self, label, key, field, value, *, row_id, parameter=True):
        control = QDoubleSpinBox()
        decimals = 0 if isinstance(field.get("default"), int) else 3
        control.setDecimals(int(field.get("decimals", decimals)))
        control.setRange(float(field.get("min", 0)), float(field.get("max", 1)))
        control.setSingleStep(float(field.get("step", .01)))
        control.setKeyboardTracking(False)
        control.setValue(float(value if value is not None else field.get("default", 0)))
        control.editingFinished.connect(self._guard(row_id, key, control.value, parameter=parameter))
        self._field(label, control, f"processorParam_{key}")

    def _targets(self, row, spec):
        group = QGroupBox("真实目标类型")
        layout = QVBoxLayout(group)
        current = set(row.get("targets") or [])
        scope = self._scope_types()
        allowed = set(spec.get("targets") or [])
        options = sorted((scope & allowed) | current)
        for key in options:
            check = QCheckBox(key)
            check.setObjectName(f"processorTarget_{key}")
            check.setChecked(key in current)
            check.setEnabled(key in allowed and key in scope)
            check.installEventFilter(self)
            check.toggled.connect(self._target_slot(row["id"], key))
            layout.addWidget(check)
        if not options:
            layout.addWidget(QLabel("当前范围没有此处理器支持的目标类型。"))
        self._form.addRow(group)

    def _target_slot(self, row_id, key):
        epoch = self._form_epoch
        def commit(checked):
            if epoch == self._form_epoch:
                self._target(row_id, key, checked)
        return commit

    def _target(self, row_id, key, checked):
        row = self._row(row_id)
        if row is not None:
            targets = set(row.get("targets") or [])
            (targets.add if checked else targets.discard)(key)
            self._edit(row_id, "targets", sorted(targets))

    def flush(self):
        for field in tuple(self._fields):
            if isinstance(field, QDoubleSpinBox) and (field.hasFocus() or field.lineEdit().hasFocus()):
                if not field.hasAcceptableInput():
                    return False
                field.interpretText()
                field.editingFinished.emit()
        return True

    def set_feedback(self, packet):
        self._feedback = deepcopy(packet) if isinstance(packet, dict) else None
        self.refresh_status()

    def _scope_types(self):
        return {channel_type_key(key) for key in self._panel._scope_channels}

    def _dry_reason(self):
        if self._draft.mode != "chain":
            return "处理链未启用；打开此页不会改变已有编曲。"
        if not self._draft.rows:
            return "空处理链：原数据直接通过。"
        if not self._scope_types():
            return "当前范围没有可识别的目标类型：原数据直接通过。"
        active = [row for row in self._draft.rows if row["enabled"] and row["wet"] > 0]
        if not active:
            return "全部处理器旁路或干湿为零：原数据直接通过。"
        if not any(self._scope_types().intersection(row["targets"]) for row in active):
            return "当前范围没有已选择的处理目标：原数据直接通过。"
        return ""

    def refresh_status(self):
        from .draw_processor_status import status_text
        scope = self._panel._scope_track_id or self._panel._state.get("scope_folder_id", "")
        self._scope.setText(f"作用范围：{scope or '未绑定'} · 已有 Clip 的处理链")
        packet = self._feedback or {}
        reason = self._dry_reason()
        if reason:
            text = reason
        else:
            text = status_text(packet) or self._feedback_text(packet)
        prefix = "待应用草稿 · " if self._panel._dirty_hint else ""
        self._status.setText(prefix + text)

    def _feedback_text(self, packet):
        source = packet.get("source", "")
        if isinstance(source, dict):
            source = source.get("kind", source.get("mode", ""))
        energy = float(packet.get("energy", 0))
        row = self._row() or {}
        control = float(packet.get(row.get("source", "energy"), 0))
        text = f"音频源 {source} · 能量 {energy:.0%} · 当前控制 {control:.0%} · 按上方顺序处理原 Clip"
        if packet.get("processed_clips", 0) > 0:
            before = float(packet.get("input_peak", 0))
            after = float(packet.get("output_peak", 0))
            text += f"\n原帧 DMX 峰值 {before:.0f} → {after:.0f}（混合与安全滤网前）"
        return text
