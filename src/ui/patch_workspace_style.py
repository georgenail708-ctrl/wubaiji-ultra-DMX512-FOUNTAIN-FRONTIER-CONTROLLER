"""Patch presentation through native Qt widgets and existing theme resources.

References: Qt QListWidgetItem/QTableView APIs; bundled Tabler 3.44.0 (MIT).
Editor and domain semantics only.
No delegate, fixture classification model, address allocator or renderer here.
"""
from __future__ import annotations

from html import escape

from PyQt6.QtCore import QSize, Qt

from ..core.models import ChannelType
from . import qascade_theme as theme
from .main_icon_library import load_tabler_icon


_CHANNEL_GLYPHS = {
    ChannelType.PUMP.value: "droplet",
    ChannelType.VALVE.value: "switch-horizontal",
    ChannelType.MOTOR_X.value: "arrows-move",
    ChannelType.MOTOR_Y.value: "arrows-move",
    ChannelType.MOTOR_Z.value: "arrows-move",
    ChannelType.MOTOR_X_FINE.value: "arrows-move",
    ChannelType.MOTOR_Y_FINE.value: "arrows-move",
    ChannelType.MOTOR_Z_FINE.value: "arrows-move",
    ChannelType.DIMMER.value: "bulb",
    ChannelType.RED.value: "bulb",
    ChannelType.GREEN.value: "bulb",
    ChannelType.BLUE.value: "bulb",
    ChannelType.WHITE.value: "bulb",
}
_ACTION_GLYPHS = {
    "新建": "plus", "模板": "folder", "编辑": "pencil", "删除": "trash",
    "导入": "file-import", "导入配接表": "file-import",
    "导出配接表": "file-export", "清空配接…": "eraser",
    "添加宇宙": "plus", "执行配接": "plug",
    "建议起始通道": "search", "重编号…": "list-numbers",
}


def definition_glyph(definition) -> str:
    """Show only channel families actually present; unknowns stay generic."""
    channels = getattr(definition, "channels", ()) or ()
    glyphs = {_CHANNEL_GLYPHS.get(str(channel), "adjustments-horizontal")
              for channel in channels if channel != ChannelType.PLACEHOLDER.value}
    if len(glyphs) > 1:
        return "components"
    return next(iter(glyphs), "adjustments-horizontal")


def definition_markup(definition, *, summary: str) -> str:
    """Native QLabel rich text; imported names remain literal text."""
    name = escape(str(definition.name))
    channels = escape(str(definition.channel_count))
    return (f'<b style="font-size:{theme.FS_BODY + 2}px;">{name}</b><br>'
            f'每台 {channels} 通道<br>'
            f'<span style="color:{theme.TEXT_SECONDARY};">{escape(summary)}</span>')


def decorate_definition_items(panel) -> None:
    definitions = {definition.id: definition for definition in
                   getattr(panel._project, "fixture_defs", ())}
    library = panel._def_list
    for row in range(library.count()):
        item = library.item(row)
        if not item.icon().isNull():
            continue
        definition = definitions.get(item.data(Qt.ItemDataRole.UserRole))
        item.setIcon(load_tabler_icon(definition_glyph(definition), 18))
        item.setSizeHint(QSize(0, 28))


def decorate_library(library) -> None:
    library.setIconSize(QSize(18, 18))
    library.setSpacing(2)
    library.setTextElideMode(Qt.TextElideMode.ElideRight)
    library.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    library.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    library.setAccessibleName("设备类型库：选择类型，双击编辑通道")


def decorate_actions(buttons: dict) -> None:
    for text, glyph in _ACTION_GLYPHS.items():
        button = buttons[text]
        button.setIcon(load_tabler_icon(glyph, 16))
        button.setIconSize(QSize(16, 16))
        button.setAccessibleName(text)
        if text != "执行配接":
            button.setFixedHeight(28)
    buttons["建议起始通道"].setToolTip(
        "按右侧目标宇宙、设备类型和数量查找连续空位；只填写起始通道，执行配接后才写入。")


def decorate_table(panel) -> None:
    table = panel._patch_table._table
    table.setFont(theme.cached_font(theme.FS_BODY))
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setAccessibleName("已配接设备：名称、类型、宇宙、地址与冲突状态")
    table.verticalHeader().setDefaultSectionSize(28)
    header = table.horizontalHeader()
    header.setFont(theme.cached_font(theme.FS_BODY, bold=True))
    header.setMinimumHeight(28)
    # Initial widths only: preserve interactive resizing, sorting and model roles.
    for column, width in enumerate((168, 156, 64, 88, 72, 108, 128)):
        table.setColumnWidth(column, width)
    panel._patch_table._search.setPlaceholderText("筛选设备名称 / 类型 / 地址…")
    panel._patch_table._search.setAccessibleName("筛选已配接设备")
    panel._patch_table._search.setFixedHeight(28)
    panel._patch_table._uni_combo.setFixedHeight(28)
    panel._patch_table._uni_combo.setAccessibleName("筛选已配接设备的宇宙")
    panel._patch_table_tabs.setIconSize(QSize(16, 16))
    panel._patch_table_tabs.setTabIcon(0, load_tabler_icon("grid-dots", 16))
    panel._patch_table_tabs.setTabIcon(1, load_tabler_icon("table", 16))
