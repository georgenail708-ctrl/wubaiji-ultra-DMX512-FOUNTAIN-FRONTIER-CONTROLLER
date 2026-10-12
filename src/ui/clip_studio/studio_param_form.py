# -*- coding: utf-8 -*-
"""Studio 共享原生参数表单：弹窗和常驻属性区复用同一 CardSpec 字段工厂。

工厂由 studio_dialogs 原实现迁入，不引入第二套参数模型。原生文本输入在
Enter/失焦完成；数值步进在释放/滚轮事件结束完成，连续按住只提交最终值。
属性区只发完成编辑信号，应用和撤销仍由原 canvas/panel 回路负责。
参考 Qt QFormLayout / QAbstractSpinBox.keyboardTracking 官方 API。
"""
from __future__ import annotations

import copy

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox,
                             QFormLayout, QGridLayout, QLabel, QLineEdit, QSpinBox,
                             QWidget)

from src.ui import qascade_theme as _th
from src.ui.clip_studio.studio_cards import SPEC_SUB_FIELDS
from src.ui.slider_input_keys import reserve_input_keys

PRESET_COLORS = _th.STUDIO_PRESET_COLORS
_MODE_LABELS = (("range", "范围 from-to"), ("odd", "奇数组"), ("even", "偶数组"),
                ("stride", "选N隔M"))
_SPEC_LABELS = {"start": "从", "end": "到", "take": "取", "gap": "隔"}
_STEP_KEYS = frozenset((Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp,
                        Qt.Key.Key_PageDown))


def _add_spin(form, label, ps, value):
    """沿用 ParamSpec 的范围、步进、单位，禁打字时逐字符写入模型。"""
    if ps.type == "int":
        widget = QSpinBox()
        widget.setRange(int(ps.min), int(ps.max))
        widget.setSingleStep(max(1, int(ps.step)))
        widget.setValue(int(value or 0))
    else:
        widget = QDoubleSpinBox()
        widget.setDecimals(3 if abs(float(ps.step)) < 0.1 else 1)
        widget.setRange(float(ps.min), float(ps.max))
        widget.setSingleStep(float(ps.step) or 0.1)
        widget.setValue(float(value or 0.0))
    widget.setKeyboardTracking(False)
    if ps.unit:
        widget.setSuffix(f" {ps.unit}")
    form.addRow(label, widget)
    return widget


def _add_choice(form, label, choices, current):
    """使用原 choice 值域；读回 itemData 而不是本地化标签。"""
    widget = QComboBox()
    reserve_input_keys(widget)
    for cid, clabel in choices:
        widget.addItem(clabel, cid)
    index = widget.findData(str(current))
    widget.setCurrentIndex(index if index >= 0 else 0)
    form.addRow(label, widget)
    return lambda: widget.currentData()


def _spec_mode_fields(mode, spins) -> None:
    """只编辑当前选择方式实际消费的字段，其余数值仍保留。"""
    fields = {"range": {"start", "end"}, "stride": {"start", "take", "gap"}}
    active = fields.get(str(mode.currentData()), set())
    for key, spin in spins.items():
        spin.setEnabled(key in active)


def _add_spec_subform(form, ps, value):
    """复用原指定组规格；改为窄栏可容纳的标签/字段排列。"""
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(4)
    mode = QComboBox()
    reserve_input_keys(mode)
    for mid, label in _MODE_LABELS:
        mode.addItem(label, mid)
    mode.setCurrentIndex(next((i for i, (mid, _) in enumerate(_MODE_LABELS)
                               if mid == value.get("mode")), 0))
    mode.setObjectName("mode")
    grid.addWidget(mode, 0, 0, 1, 2)
    spins = {}
    for row, (key, lo, hi) in enumerate(SPEC_SUB_FIELDS, start=1):
        grid.addWidget(QLabel(_SPEC_LABELS[key]), row, 0)
        spin = QSpinBox()
        spin.setObjectName(key)
        spin.setKeyboardTracking(False)
        spin.setRange(lo, hi)
        spin.setValue(int(value.get(key, lo) or lo))
        grid.addWidget(spin, row, 1)
        spins[key] = spin
    mode.currentIndexChanged.connect(lambda: _spec_mode_fields(mode, spins))
    _spec_mode_fields(mode, spins)
    form.addRow(ps.label, box)

    def read():
        return {"mode": mode.currentData(), **{key: spin.value() for key, spin in spins.items()}}

    return read


def _reader_for(form, ps, value):
    """沿用原字段工厂；动态 rows 原值透传，专属编辑器继续负责行表。"""
    if ps.type == "spec":
        return _add_spec_subform(form, ps, value if isinstance(value, dict) else {})
    if ps.type == "rows":
        return lambda: value
    if ps.type in ("int", "float"):
        widget = _add_spin(form, ps.label, ps, value)
        return lambda: _number_value(widget)
    if ps.type == "bool":
        widget = QCheckBox(ps.label)
        widget.setChecked(bool(value))
        form.addRow(widget)
        return lambda: widget.isChecked()
    if ps.type == "choice":
        return _add_choice(form, ps.label, ps.choices, str(value))
    if ps.type == "color":
        return _color_reader(form, ps, value)
    widget = QLineEdit(str(value or ""))
    form.addRow(ps.label, widget)
    return lambda: widget.text()


def _color_reader(form, ps, value):
    """非预设颜色原值保留，不因打开属性区悄悄改成白色。"""
    choices = PRESET_COLORS
    if str(value) not in {cid for cid, _ in choices}:
        choices += ((str(value), "自定义"),)
    return _add_choice(form, ps.label, choices, str(value))


def _number_value(widget):
    """读回显示精度；Qt 连续 step 的二进制尾差不能成为第二次编辑。"""
    if isinstance(widget, QDoubleSpinBox):
        return round(widget.value(), widget.decimals())
    return widget.value()


class CardParamForm(QWidget):
    """CardSpec→原生表单；完成输入信号携带值，加载/销毁不会提交旧值。"""

    values_edited = pyqtSignal(dict)

    def __init__(self, spec, params, parent=None, *, live=False):
        super().__init__(parent)
        self._live = bool(live)
        if spec.key == "motor_force":
            from src.ui.clip_studio.studio_force import form_params
            params = form_params(params)
        self._original = copy.deepcopy(params)
        self._readers = []
        self._form = QFormLayout(self)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(4)
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for ps in spec.params:
            row = self._form.rowCount()
            reader = _reader_for(self._form, ps, copy.deepcopy(params.get(ps.key, ps.default)))
            self._readers.append((ps.key, reader))
            self._name_field(row, ps.key, ps.label)
        self._field_baseline = {key: copy.deepcopy(reader()) for key, reader in self._readers}
        self._baseline = self.values()
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._emit_values)
        if self._live:
            self._wire_completion()
        if spec.key == "motor_force":
            from src.ui.clip_studio.studio_force import connect_preset_form
            connect_preset_form(self)

    def _name_field(self, row, key, label) -> None:
        item = self._form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        if item is None:
            item = self._form.itemAt(row, QFormLayout.ItemRole.SpanningRole)
        if item is not None and item.widget() is not None:
            item.widget().setObjectName(str(key))
            item.widget().setAccessibleName(str(label))

    def _wire_completion(self) -> None:
        for widget in self.findChildren(QAbstractSpinBox):
            widget.editingFinished.connect(self._queue_commit)
            widget.installEventFilter(self)
            widget.lineEdit().installEventFilter(self)
        for widget in self.findChildren(QComboBox):
            widget.currentIndexChanged.connect(self._queue_commit)
        for widget in self.findChildren(QCheckBox):
            widget.toggled.connect(self._queue_commit)
        for widget in self.findChildren(QLineEdit):
            if not isinstance(widget.parentWidget(), QAbstractSpinBox):
                widget.editingFinished.connect(self._queue_commit)

    def _queue_commit(self, *_args) -> None:
        if self._live:
            self._commit_timer.start(0)

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() in (QEvent.Type.MouseButtonRelease, QEvent.Type.Wheel):
            self._queue_commit()
        elif event.type() == QEvent.Type.KeyRelease and event.key() in _STEP_KEYS:
            self._queue_commit()
        return super().eventFilter(obj, event)

    def values(self) -> dict:
        """未编辑字段保留原值/精度；完成变化的字段使用同一套类型化 reader。"""
        result = copy.deepcopy(self._original)
        for key, reader in self._readers:
            value = reader()
            if key not in result or value != self._field_baseline[key]:
                result[key] = copy.deepcopy(value)
        return result

    def _emit_values(self) -> None:
        if not self._live:
            return
        values = self.values()
        if values != self._baseline:
            self._baseline = copy.deepcopy(values)
            self.values_edited.emit(values)

    def flush(self) -> None:
        """正常切换对象前完成当前输入；没有变化不提交。"""
        self._commit_timer.stop()
        for widget in self.findChildren(QAbstractSpinBox):
            widget.interpretText()
        self._emit_values()

    def discard(self) -> None:
        """撤销/换工程/删除时拒绝旧控件晚到的 focus-out 或 timer。"""
        self._live = False
        self._commit_timer.stop()
