"""Matrix channel choices and passive restore using the shared project catalog.

The selected value is a persistent preference. Missing fixtures or definitions
are visible states, and never select pump or write output on the user's behalf.
"""
from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt

from ..project_channels import collect_channel_items, project_items, split_scope
from .matrix_channel_targets import normalized_identity, refresh_panel_profiles, resolve_output_target
from .matrix_channel_profile_ui import refresh_profile_control
from .matrix_channel_profiles import normalize_channel_profiles


def append_definition_channels(combo, definition, seen):
    for label, key in collect_channel_items([definition]):
        if key not in seen:
            combo.addItem(label, key)
            seen.add(key)


def _applicable_count(panel, identity):
    target = resolve_output_target(panel, identity)
    plain, definition = split_scope(target)
    if not target:
        return 0
    return sum(_fixture_supports(row, plain, definition)
               for row in getattr(panel, "_matrix_channel_fixture_info", {}).values())


def _fixture_supports(row, plain, definition):
    if definition and row["def_id"] != definition:
        return False
    if row["channel_addrs"].get(plain):
        return True
    return plain == "dimmer" and any(row["channel_addrs"].get(key) for key in ("r", "g", "b", "w"))


def _saved_item(panel, value, labels, old_label):
    resolved = resolve_output_target(panel, value)
    plain, _definition = split_scope(normalized_identity(value))
    if not resolved and plain.startswith("function_"):
        return "已保存功能通道 · 请选择设备类型"
    known_label = labels.get(resolved)
    label = known_label or _base_saved_label(old_label) or "已保存通道"
    if resolved != normalized_identity(value):
        return label + " · 按当前设备解析"
    if known_label:
        return label
    return label + " · 当前工程暂缺"


def _base_saved_label(label):
    for status in (" · 当前工程暂缺", " · 无适用设备", " · 按当前设备解析"):
        label = label.partition(status)[0]
    return label


def _populate_items(panel, combo, value, old_label):
    items = [("泵", "pump"), ("阀门", "valve"), ("调光", "dimmer")]
    labels = dict((key, label) for label, key in items)
    for label, key in project_items(panel._project_for_matrix()):
        if key not in labels:
            labels[key] = label
            items.append((label, key))
    if value not in labels:
        items.append((_saved_item(panel, value, labels, old_label), value))
    for label, key in items:
        count = _applicable_count(panel, key)
        status = f"当前选择适用 {count} 台" if count else "当前选择无适用设备"
        combo.addItem(label if count else label + " · 无适用设备", key)
        combo.setItemData(combo.count() - 1, status, Qt.ItemDataRole.ToolTipRole)
    combo.setCurrentIndex(combo.findData(value))
    combo.setToolTip(combo.currentData(Qt.ItemDataRole.ToolTipRole) or "")


def populate_channel_choices(panel):
    combo = getattr(panel, "_ch_type_combo", None)
    if combo is None:
        return
    value = str(getattr(panel, "_ch_type", "") or "pump")
    old_index = combo.findData(value)
    old_label = combo.itemText(old_index) if old_index >= 0 else ""
    blocker = QSignalBlocker(combo)
    combo.clear()
    _populate_items(panel, combo, value, old_label)
    del blocker
    panel._ch_type = value
    panel._engine.config.ch_type = value
    refresh_panel_profiles(panel)
    refresh_profile_control(panel)


def finish_channel_rebuild(panel):
    populate_channel_choices(panel)
    panel._reset_matrix_output_state(clear_pattern=True)


def restore_channel_type(panel, value):
    # Preserve the original saved spelling (including legacy bare slots).
    panel._ch_type = str(value or getattr(panel, "_ch_type", "") or "pump")
    panel._engine.config.ch_type = panel._ch_type
    populate_channel_choices(panel)


def restore_runtime_channel(panel, state, apply_runtime_params):
    apply_runtime_params(panel, state)
    _restore_runtime_profiles(panel, state)
    value = state.get("ch_type") or (state.get("slideshow_engine") or {}).get("config", {}).get("ch_type")
    restore_channel_type(panel, value)


def _restore_runtime_profiles(panel, state):
    engine = state.get("slideshow_engine")
    if isinstance(engine, dict):
        panel._engine.config.channel_profiles = normalize_channel_profiles((engine.get("config") or {}).get("channel_profiles"))


def first_channel_offset(panel, fixture):
    target = resolve_output_target(panel, panel._ch_type)
    plain, definition = split_scope(target)
    if not target or (definition and str(fixture.get("def_id", "")) != definition):
        return None
    channels = _fixture_channels(panel, fixture)
    keys = [normalized_identity(channel) for channel in channels]
    return keys.index(plain) if plain in keys else None


def _fixture_channels(panel, fixture):
    channels = fixture.get("channels", [])
    if channels:
        return channels
    definition = panel._get_fixture_def(panel._pm, fixture.get("def_id", ""))
    return list(getattr(definition, "channels", []) or [])
