"""M02 native stored-frame properties; the original panel owns every edit.

Qt 6.11 QFormLayout API: qformlayout.cpp:1257/1985/2018 (Qt licences).
See r185-matrix-redesign-adoption.md, section 11; no upstream code copied.
"""
from __future__ import annotations

import weakref

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from .. import qascade_theme as theme
from ..flow_layout import FlowLayout


def _host():
    from . import matrix_slideshow_panel
    return matrix_slideshow_panel


def native_form(parent=None) -> QFormLayout:
    form = QFormLayout(parent)
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(4)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


def time_spin(value: float, step: float, *, manual: bool = False) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setDecimals(3)
    spin.setRange(0.0, 86400.0)
    spin.setSingleStep(step)
    spin.setValue(value)
    spin.setSuffix(" s")
    spin.setMinimumWidth(96)
    if manual:
        spin.setSpecialValueText("手动")
    return spin


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color:{theme.TEXT_SECONDARY};font-size:{theme.FS_SMALL}px;")
    return label


def _set_fields(panel, form) -> None:
    mp = _host()
    panel._name_edit = QLineEdit()
    panel._name_edit.setPlaceholderText("帧名称")
    panel._name_edit.editingFinished.connect(panel._on_name_changed)
    form.addRow("名称", panel._name_edit)
    panel._hold_spin = time_spin(1.0, 0.1, manual=True)
    panel._hold_spin.setToolTip("单帧停留时长；0=手动切换，最大24小时，可直接键盘输入秒数。")
    panel._hold_spin.valueChanged.connect(panel._on_hold_changed)
    form.addRow("停留", panel._hold_spin)
    panel._trans_spin = time_spin(0.2, 0.05)
    panel._trans_spin.setToolTip("当前帧进入时的过渡；0=立切，最大24小时，可直接键盘输入。")
    panel._trans_spin.valueChanged.connect(panel._on_trans_changed)
    form.addRow("过渡", panel._trans_spin)
    panel._trans_mode_combo = QComboBox()
    for key, label in mp.TRANS_MODES.items():
        panel._trans_mode_combo.addItem(label, key)
    panel._trans_mode_combo.currentIndexChanged.connect(panel._on_trans_mode_changed)
    form.addRow("过渡模式", panel._trans_mode_combo)
    panel._on_value_spin = QSpinBox()
    panel._on_value_spin.setRange(0, 255)
    panel._on_value_spin.setValue(255)
    panel._on_value_spin.valueChanged.connect(panel._on_value_changed)
    form.addRow("ON 值", panel._on_value_spin)
    panel._off_value_spin = QSpinBox()
    panel._off_value_spin.setRange(0, 255)
    panel._off_value_spin.valueChanged.connect(panel._on_off_value_changed)
    form.addRow("OFF 值", panel._off_value_spin)


class SlidePropertiesContext(QObject):
    """Read-only UI reader, parented before the widgets it reads."""

    def __init__(self, panel):
        super().__init__(panel)
        self._owner_ref = None
        self._preview = None

    def bind_owner(self, owner) -> None:
        self._owner_ref = weakref.ref(owner)
        self.refresh()

    def adopt_bpm_row(self, row) -> None:
        owner = self._owner_ref() if self._owner_ref is not None else None
        if owner is not None:
            adopt_bpm_row(owner, row)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show:
            self.refresh()
        return super().eventFilter(watched, event)

    @pyqtSlot()
    def refresh(self) -> None:
        panel = self.parent()
        owner = self._owner_ref() if self._owner_ref is not None else None
        slide = panel._slide
        idx = _slide_index(owner, slide)
        valid = slide is not None and (owner is None or idx >= 0)
        panel._ppt_field_host.setEnabled(valid)
        panel._ppt_preview_host.setVisible(valid)
        panel._ppt_inspect_label.setText(
            f"所查帧 {idx + 1} · {slide.name}" if valid else "请选择或添加幻灯片帧")
        if valid:
            self._show_stored_frame(owner, slide, max(0, idx))

    def _show_stored_frame(self, owner, slide, idx: int) -> None:
        fixtures = owner._fixtures if owner is not None else []
        if self._preview is None:
            self._preview = _stored_thumbnail(slide, fixtures, idx)
            self.parent()._ppt_preview_layout.addWidget(self._preview)
        self._preview._idx = idx
        self._preview._fixtures = self._preview._sample_fixtures(fixtures)
        self._preview.update_slide(slide)
        self._preview.setToolTip(
            f"{slide.name}\n存储状态 ON {slide.on_count()} / {slide.total_count()}\n"
            "只读示意；沿原缩略图采样，最多120个设备点。")


def _slide_index(owner, slide) -> int:
    if owner is None:
        return 0
    return next((i for i, candidate in enumerate(owner._engine.slides)
                 if candidate is slide), -1)


def _stored_thumbnail(slide, fixtures, idx: int):
    thumb = _host().SlideThumbnail(idx, slide, fixtures)
    thumb.THUMB_W = 240
    thumb.THUMB_H = 176
    thumb.setFixedSize(thumb.THUMB_W + 4, thumb.THUMB_H + 28)
    thumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    thumb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    thumb.setCursor(Qt.CursorShape.ArrowCursor)
    return thumb


def build_slide_properties(panel) -> None:
    context = SlidePropertiesContext(panel)
    panel._ppt_properties_context = context
    panel.installEventFilter(context)
    root = QVBoxLayout(panel)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(4)
    panel._ppt_inspect_label = _caption("请选择或添加幻灯片帧")
    root.addWidget(panel._ppt_inspect_label)
    details = QWidget()
    flow = FlowLayout(details)
    flow.setContentsMargins(0, 0, 0, 0)
    flow.setSpacing(8)
    policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    policy.setHeightForWidth(True)
    details.setSizePolicy(policy)
    panel._ppt_preview_host = QWidget()
    preview_layout = QVBoxLayout(panel._ppt_preview_host)
    preview_layout.setContentsMargins(0, 0, 0, 0)
    preview_layout.setSpacing(4)
    preview_layout.addWidget(_caption("所查帧示意 · 只读存储状态"))
    panel._ppt_preview_layout = preview_layout
    flow.addWidget(panel._ppt_preview_host)
    panel._ppt_field_host = QWidget()
    panel._ppt_field_host.setMinimumWidth(240)
    _set_fields(panel, native_form(panel._ppt_field_host))
    flow.addWidget(panel._ppt_field_host)
    root.addWidget(details)
    root.addStretch()
    panel.params_changed.connect(context.refresh)
    context.refresh()


def adopt_bpm_row(panel, row) -> None:
    """Move the already constructed W2 widgets into sequence properties."""
    if row is None:
        return
    panel._slide_param_panel.layout().removeWidget(row)
    layout = row.layout()
    while layout.count():
        layout.takeAt(0)
    form = native_form()
    layout.addLayout(form)
    form.addRow(panel._bpm_enable_chk)
    form.addRow("节拍速度", panel._bpm_spin)
    form.addRow("每帧拍数", panel._bpm_beats_spin)
    panel._bpm_info_lbl.setWordWrap(True)
    form.addRow(panel._bpm_info_lbl)
    panel._ppt_bpm_section.add_widget(row)


__all__ = ["adopt_bpm_row", "build_slide_properties", "native_form", "time_spin"]
