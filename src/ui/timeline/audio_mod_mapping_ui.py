# -*- coding: utf-8 -*-
"""调制Mapping的原生布局适配；保留原目标/波形和250ms提交语义。

参考Inspector和Qt QSplitter/QFormLayout。直接复用CardParamForm；
逐行combo使用自己的itemData，避免读取循环结束后的另一行widget。
"""
from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget,
                             QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from src.ui import qascade_theme as _th
from src.ui.widget_kit import PanelSection, scope_qss
from src.ui.slider_input_keys import reserve_input_keys
from src.ui.clip_studio.studio_cards import CardSpec, ParamSpec
from src.ui.clip_studio.studio_param_form import CardParamForm
from src.ui.audio_mod_draw.draw_cards import WAVE_CHOICES, type_label

_FEATURE_CHOICES = (("beat_strength", "拍强度"), ("band_low", "低频"),
                    ("band_mid", "中频"), ("band_high", "高频"),
                    ("onset_flux", "onset"), ("constant", "恒定"))


def build_page(page) -> None:
    root = QVBoxLayout(page)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    header = QWidget()
    scope_qss(header, f"background:{_th.BG_PANEL_SUB}; border-bottom:1px solid {_th.SEP_LINE};")
    top = QVBoxLayout(header)
    top.setContentsMargins(12, 8, 12, 8)
    top.setSpacing(4)
    actions = QHBoxLayout()
    page._title = QLabel("音频调制 · 映射")
    page._title.setStyleSheet("font-weight:bold; background:transparent;")
    actions.addWidget(page._title)
    actions.addStretch(1)
    page._close_btn = QPushButton("关闭")
    actions.addWidget(page._close_btn)
    top.addLayout(actions)
    page._summary = QLabel()
    page._summary.setWordWrap(True)
    page._summary.setStyleSheet(f"color:{_th.TEXT_SECONDARY}; background:transparent;")
    top.addWidget(page._summary)
    root.addWidget(header)
    split = QSplitter(Qt.Orientation.Horizontal)
    split.setObjectName("audioModMappingSplit")
    split.setHandleWidth(4)
    left, right = QWidget(), QWidget()
    left.setLayout(page._build_left_column())
    right.setLayout(page._build_right_column())
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(right)
    split.addWidget(left)
    split.addWidget(scroll)
    split.setCollapsible(1, False)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setSizes([240, 640])
    page._mapping_split = split
    root.addWidget(split, 1)


def build_left_column(page):
    col = QVBoxLayout()
    col.setContentsMargins(8, 8, 8, 8)
    col.setSpacing(8)
    section = PanelSection("范围内的目标类型")
    page._channel_list = QListWidget()
    page._channel_list.setMinimumWidth(180)
    page._channel_list.itemChanged.connect(page._on_channel_toggled)
    section.add_widget(page._channel_list)
    col.addWidget(section, 1)
    hint = QLabel("没有显式指定目标时，使用范围内全部类型。")
    hint.setWordWrap(True)
    hint.setStyleSheet(f"color:{_th.TEXT_SECONDARY}; background:transparent;")
    col.addWidget(hint)
    return col


def build_right_column(page):
    col = QVBoxLayout()
    col.setContentsMargins(8, 8, 8, 8)
    col.setSpacing(8)
    section = PanelSection("全局调制")
    page._global_host = QWidget()
    page._global_layout = QVBoxLayout(page._global_host)
    page._global_layout.setContentsMargins(0, 0, 0, 0)
    page._global_layout.setSpacing(4)
    page._global_form = None
    load_global_form(page)
    section.add_widget(page._global_host)
    col.addWidget(section)
    mappings = PanelSection("类型 → 波形")
    hint = QLabel("波形选择作用于包含该类型的调制区段，同时更新默认波形。")
    hint.setWordWrap(True)
    hint.setStyleSheet(f"color:{_th.TEXT_SECONDARY}; background:transparent;")
    mappings.add_widget(hint)
    page._rows_host = QVBoxLayout()
    page._rows_host.setSpacing(4)
    mappings.add_layout(page._rows_host)
    col.addWidget(mappings)
    col.addStretch(1)
    return col


def _global_spec(page):
    choices = _FEATURE_CHOICES
    current = str(page._state.get("feature", "beat_strength"))
    if current not in dict(choices):
        choices += ((current, current),)
    fields = (ParamSpec("amount", "调制量", "float", 1.0, 0.0, 1.0, 0.01),
              ParamSpec("attack", "淡入", "float", 0.05, 0.0, 30.0, 0.01, unit="s"),
              ParamSpec("release", "淡出", "float", 0.2, 0.0, 30.0, 0.01, unit="s"),
              ParamSpec("feature", "特征", "choice", "beat_strength", choices=choices))
    return CardSpec("audio_mod_global", "audio", "全局调制", "", "activity", "",
                    ports=(), params=fields)


def load_global_form(page) -> None:
    discard_global_form(page)
    spec = _global_spec(page)
    values = {ps.key: page._state[ps.key] for ps in spec.params if ps.key in page._state}
    form = CardParamForm(spec, values, page._global_host, live=True)
    page._global_form = form
    form.values_edited.connect(lambda values, obj=form: _global_values(page, obj, values))
    page._global_layout.addWidget(form)
    for key, attr in (("amount", "_amount"), ("attack", "_attack"),
                      ("release", "_release"), ("feature", "_feature")):
        setattr(page, attr, form.findChild(QWidget, key))
    for key, widget in (("amount", page._amount), ("attack", page._attack), ("release", page._release)):
        widget.setSingleStep(0.1)
        widget.valueChanged.connect(lambda value, k=key, obj=form: _number_changed(page, obj, k, value))


def _number_changed(page, form, key, value) -> None:
    if page._global_form is form and page._state.get(key) != value:
        page._on_param(key, value)


def _global_values(page, form, values) -> None:
    if page._global_form is not form:
        return
    for key, value in values.items():
        if page._state.get(key) != value:
            page._on_param(key, value)


def discard_global_form(page) -> None:
    form = getattr(page, "_global_form", None)
    if form is not None:
        page._global_form = None
        form.discard()
        page._global_layout.removeWidget(form)
        form.hide()
        form.deleteLater()


def flush_global_form(page) -> None:
    if page._global_form is not None:
        page._global_form.flush()


def refresh_target_rows(page) -> None:
    while page._rows_host.count():
        widget = page._rows_host.takeAt(0).widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()
    enabled = set(page._enabled_targets())
    for key in page._scope_channels:
        page._rows_host.addWidget(_wave_row(page, key, key in enabled))


def sync_wave_rows(page) -> None:
    """按真实clip/default_wave回显现有行，不创建控件或触发二次提交。"""
    for index in range(page._rows_host.count()):
        host = page._rows_host.itemAt(index).widget()
        wave = host.findChild(QComboBox)
        key = wave.objectName().partition(":")[2]
        current = page._wave_of(key)
        with QSignalBlocker(wave):
            if wave.findData(current) < 0:
                wave.addItem(current, current)
            wave.setCurrentIndex(wave.findData(current))


def _wave_row(page, key, enabled):
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    label = QLabel(type_label(key))
    label.setWordWrap(True)
    row.addWidget(label, 1)
    wave = QComboBox()
    reserve_input_keys(wave)
    for text, value in WAVE_CHOICES:
        wave.addItem(text, value)
    current = page._wave_of(key)
    if wave.findData(current) < 0:
        wave.addItem(current, current)
    wave.setCurrentIndex(wave.findData(current))
    wave.setObjectName(f"audioModWave:{key}")
    wave.currentIndexChanged.connect(lambda i, c=key, obj=wave: page._on_wave(c, obj.itemData(i)))
    row.addWidget(wave, 1)
    host.setEnabled(enabled)
    return host
