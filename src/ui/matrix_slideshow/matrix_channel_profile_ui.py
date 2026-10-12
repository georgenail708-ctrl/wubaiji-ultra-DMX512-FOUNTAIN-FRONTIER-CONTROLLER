"""Small explicit output mapping control; native widgets and scoped Matrix undo.

Reuse the shared channel catalog, existing FlowLayout/header and native combo.
Mode names and limits come from real definitions, without inferred motor units.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QSignalBlocker, pyqtSlot
from PyQt6.QtWidgets import QComboBox

from ..project_channels import defs_of
from .matrix_channel_profiles import normalize_channel_profiles, profile_choices, validate_profile
from .matrix_channel_targets import normalized_identity, refresh_panel_profiles


def _definitions(panel):
    return {str(definition.id): definition for definition in defs_of(panel._project_for_matrix())}


def _selected_profile(panel):
    state = normalize_channel_profiles(panel._engine.config.channel_profiles)
    key = normalized_identity(panel._ch_type)
    return state, key, state["by_channel"].get(key)


def refresh_profile_control(panel):
    combo = getattr(panel, "_matrix_channel_profile_combo", None)
    if combo is None:
        return
    state, key, profile = _selected_profile(panel)
    definitions = _definitions(panel)
    blocker = QSignalBlocker(combo)
    combo.clear()
    combo.addItem("原始 DMX", None)
    for label, reference in profile_choices(key, definitions):
        combo.addItem(label, reference)
    _restore_profile_selection(combo, state, key, profile, definitions)
    combo.setEnabled(combo.count() > 1)
    del blocker


def _restore_profile_selection(combo, state, key, profile, definitions):
    if profile is None:
        combo.setCurrentIndex(0)
        combo.setToolTip("使用原始0–255输出；选择模式映射后随当前节目保存，不改已保存帧。")
        return
    if state["version"] != 1 or validate_profile(key, profile, definitions) is None:
        combo.addItem("已保存映射失效 · 暂停输出", profile)
        combo.setCurrentIndex(combo.count() - 1)
        combo.setToolTip("模式定义已改变或暂缺。请重新选择真实模式，或选择原始 DMX；制作帧与偏好保留。")
        return
    combo.setCurrentIndex(combo.findData(profile))
    combo.setToolTip(combo.currentText() + "；仅映射对应设备类型，不改其他类型或制作帧。")


class ProfileSelectionContext(QObject):
    @pyqtSlot(int)
    def commit(self, index):
        from .matrix_ppt_catalog import snapshot_sequence_edit, store_sequence_edit
        panel = self.parent()
        state, key, previous = _selected_profile(panel)
        profile = panel._matrix_channel_profile_combo.itemData(index)
        if previous == profile and state["version"] == 1:
            return
        snapshot_sequence_edit(panel)
        state["version"] = 1
        if profile is None:
            state["by_channel"].pop(key, None)
        else:
            state["by_channel"][key] = profile
        panel._engine.config.channel_profiles = normalize_channel_profiles(state)
        refresh_panel_profiles(panel)
        refresh_profile_control(panel)
        panel._clear_matrix_output_layers()
        panel._reset_matrix_output_state()
        panel._refresh_current_matrix_output()
        store_sequence_edit(panel)
        _mark_profile_dirty(panel)


def _mark_profile_dirty(panel):
    pm = getattr(panel, "_pm", None)
    if pm is not None and pm.project is not None:
        pm._mark_dirty()


def build_profile_control(panel):
    combo = QComboBox()
    combo.setFixedWidth(196)
    panel._matrix_channel_profile_combo = combo
    context = ProfileSelectionContext(panel)
    panel._matrix_channel_profile_context = context
    combo.currentIndexChanged.connect(context.commit)
    refresh_profile_control(panel)
    return combo
