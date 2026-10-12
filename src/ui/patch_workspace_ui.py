"""Patch editor assembly: definitions, existing grid/table, and patch context.

References: Patching and Fixture Manager (semantics only), Qt APIs.
Reuse: original widgets/models, PanelSection, FlowLayout (Qt BSD-3-Clause),
and already bundled Tabler icons (MIT). No address allocation algorithm here.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QAbstractButton, QGroupBox, QLabel, QLineEdit, QMenu, QScrollArea,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from .widget_kit import PanelSection, scope_qss
from .flow_layout import FlowLayout
from .workspace_search_preferences import bind_search_preference
from . import qascade_theme as theme
from .topview_properties_ui import flow_from_layout, form_section, migrate_form
from .topview_workspace_actions import region_toggle
from .patch_workspace_style import (
    decorate_actions, decorate_definition_items, decorate_library, decorate_table,
    definition_markup,
)


class PatchContext(QObject):
    """Read-only context whose lifetime ends with its search input."""

    def __init__(self, panel, search):
        # QListModel emits modelReset from its destructor. In the library the
        # search input is destroyed before the list, so Qt must disconnect this
        # native receiver before that destructive reset can access its controls.
        super().__init__(search)
        self.panel = panel
        self.search = search
        search.textChanged.connect(self.filter_library)
        panel._def_list.currentItemChanged.connect(self.refresh)
        model = panel._def_list.model()
        model.rowsInserted.connect(self.library_changed)
        model.rowsRemoved.connect(self.library_changed)
        model.modelReset.connect(self.library_changed)
        panel.fixtures_changed.connect(self.refresh)
        for spin in (panel._inst_count_spin, panel._inst_universe_spin,
                     panel._inst_start_ch_spin):
            spin.valueChanged.connect(self.refresh)
        self.library_changed()

    @pyqtSlot()
    def filter_library(self) -> None:
        needle = self.search.text().strip().casefold()
        library = self.panel._def_list
        for row in range(library.count()):
            item = library.item(row)
            haystack = (item.text() + " " + item.toolTip()).casefold()
            hidden = bool(needle and needle not in haystack)
            if item.isHidden() != hidden:
                item.setHidden(hidden)

    @pyqtSlot()
    def library_changed(self) -> None:
        decorate_definition_items(self.panel)
        self.filter_library()
        self.panel._workspace_library_count.setText(
            f"设备类型库 · {self.panel._def_list.count()} 种")
        self.refresh()

    @pyqtSlot()
    def refresh(self) -> None:
        panel = self.panel
        item = panel._def_list.currentItem()
        definition = panel._project.get_fixture_def(
            item.data(Qt.ItemDataRole.UserRole)) if item is not None else None
        if definition is None:
            panel._workspace_definition_context.setText(
                "选择一个设备类型后设置数量与地址。双击类型可编辑通道。")
            panel._workspace_definition_context.setAccessibleName("当前设备类型：未选择")
            panel._workspace_patch_target.setText("尚未选择设备类型")
            return
        panel._workspace_definition_context.setText(
            definition_markup(definition, summary=panel._make_ch_summary(definition)))
        panel._workspace_definition_context.setAccessibleName(
            f"当前设备类型：{definition.name}，每台 {definition.channel_count} 通道")
        count = panel._inst_count_spin.value()
        universe = panel._inst_universe_spin.value()
        start = panel._inst_start_ch_spin.value()
        footprint = count * definition.channel_count
        panel._workspace_patch_target.setText(
            f"{count} 台 · 共 {footprint} 通道\n"
            f"目标 Universe {universe} · 起始 CH {start}\n"
            "设置为待配接参数；点击执行配接后按原规则写入。")


def _library(panel, *, buttons: dict) -> QScrollArea:
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)
    scope_qss(body, f"background:{theme.BG_EDITOR};")
    panel._left_lay = layout  # Existing import-session banner still uses insertWidget.
    panel._workspace_library_count = QLabel("设备类型库")
    panel._workspace_library_count.setFont(theme.cached_font(theme.FS_BODY, bold=True))
    layout.addWidget(panel._workspace_library_count)
    search = QLineEdit()
    search.setPlaceholderText("查找设备类型 / 通道")
    search.setClearButtonEnabled(True)
    search.setAccessibleName("查找 Patch 设备类型")
    search.setFixedHeight(28)
    layout.addWidget(search)
    panel._def_list.setMinimumHeight(160)
    decorate_library(panel._def_list)
    layout.addWidget(panel._def_list, 1)
    layout.addWidget(panel._def_empty)
    row = QWidget()
    flow = FlowLayout(row)
    flow.setContentsMargins(0, 0, 0, 0)
    flow.setSpacing(4)
    for text in ("新建", "模板", "编辑", "删除", "导入"):
        flow.addWidget(buttons[text])
    layout.addWidget(row)
    panel._workspace_library_search = search
    return _scroll(body)


def _scroll(body: QWidget) -> QScrollArea:
    body.setMinimumWidth(0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setMinimumWidth(0)
    scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(body)
    return scroll


def _properties(panel, *, donor, buttons: dict) -> QScrollArea:
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    scope_qss(body, f"background:{theme.BG_EDITOR};")
    context, form = form_section("当前设备类型")
    panel._workspace_definition_context = QLabel()
    panel._workspace_definition_context.setTextFormat(Qt.TextFormat.RichText)
    panel._workspace_definition_context.setWordWrap(True)
    form.addRow(panel._workspace_definition_context)
    layout.addWidget(context)
    boxes = donor.findChildren(QGroupBox, options=Qt.FindChildOption.FindDirectChildrenOnly)
    inst = next(box for box in boxes if box.title() == "配接参数")
    # IO and clear remain accessible from editor menus. The patch button stays here.
    source = inst.layout()
    source.removeWidget(buttons["清空配接…"])
    # IO buttons stay in the original hidden form; editor menus delegate to
    # those same buttons. Only the four patch parameter rows belong here.
    layout.addWidget(migrate_form(inst, title="配接参数 · 1 起始地址", rows=4))
    target, form = form_section("配接目标预览")
    panel._workspace_patch_target = QLabel()
    panel._workspace_patch_target.setWordWrap(True)
    panel._workspace_patch_target.setAccessibleName("待执行配接的参数预览")
    form.addRow(panel._workspace_patch_target)
    layout.addWidget(target)
    panel._legend_section.set_expanded(False)
    layout.addWidget(panel._legend_section)
    hint = PanelSection("操作说明", expanded=False)
    text = QLabel("类型定义描述通道；已配接设备拥有实际地址。\n"
                  "网格查看通道占用，表格查看设备与地址冲突。\n"
                  "地址建议只填入参数；重编号使用原表格选行。")
    text.setWordWrap(True)
    hint.add_widget(text)
    layout.addWidget(hint)
    layout.addStretch(1)
    return _scroll(body)


def _menu(title: str, *, texts: tuple, buttons: dict, parent) -> QToolButton:
    button = QToolButton(parent)
    button.setText(title)
    button.setMinimumHeight(28)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(button)
    for text in texts:
        action = menu.addAction(text)
        action.setIcon(buttons[text].icon())
        action.setToolTip(buttons[text].toolTip())
        action.triggered.connect(buttons[text].click)
        menu.aboutToShow.connect(lambda a=action, b=buttons[text]: a.setEnabled(b.isEnabled()))
    button.setMenu(menu)
    return button


def _header(panel, *, buttons: dict, library, properties) -> QWidget:
    bar = QWidget()
    flow = FlowLayout(bar)
    flow.setContentsMargins(4, 4, 4, 4)
    flow.setSpacing(4)
    scope_qss(bar, f"background:{theme.BG_PANEL};border-bottom:1px solid {theme.SEP_LINE};")
    title = QLabel("Patch")
    title.setFont(theme.cached_font(theme.FS_BODY + 2, bold=True))
    flow.addWidget(title)
    flow.addWidget(_menu("配接表", texts=("导入配接表", "导出配接表"), buttons=buttons, parent=bar))
    flow.addWidget(_menu("清空", texts=("清空配接…",), buttons=buttons, parent=bar))
    flow.addWidget(buttons["添加宇宙"])
    flow.addWidget(region_toggle("类型库", icon="layout-sidebar", region=library, parent=bar))
    flow.addWidget(region_toggle("属性", icon="layout-sidebar-right", region=properties, parent=bar))
    return bar


def _centre(panel, centre) -> None:
    layout = centre.layout()
    header = layout.takeAt(0).layout()
    # Keep the retired, now empty layouts owned by their original widgets.
    header.setParent(centre)
    centre._workspace_header_donor = header
    label = header.takeAt(0).widget()
    label.setText("设备配接 · 每个 Universe 512 通道")
    label.setWordWrap(True)
    layout.insertWidget(0, label)
    table = panel._patch_table
    old_row = table.layout().takeAt(0).layout()
    old_row.setParent(table)
    table._workspace_toolbar_donor = old_row
    table.layout().insertWidget(0, flow_from_layout(old_row))
    table.layout().removeWidget(table._summary)
    layout.insertWidget(1, table._summary)
    # The original grid/table tab widget, models and selection are retained.
    panel._patch_table_tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    layout.setStretchFactor(panel._patch_table_tabs, 1)


def install_workspace(panel) -> None:
    """Called after attach_patch_table; do not rebuild UniverseGrid or table models."""
    root = panel.layout()
    splitter = root.itemAt(0).widget()
    donor = splitter.widget(0)
    centre = splitter.widget(1)
    buttons = {button.text(): button for button in panel.findChildren(QAbstractButton)}
    decorate_actions(buttons)
    properties = _properties(panel, donor=donor, buttons=buttons)
    library = _library(panel, buttons=buttons)
    _centre(panel, centre)
    decorate_table(panel)
    donor.setParent(panel)
    donor.hide()
    splitter.insertWidget(0, library)
    splitter.addWidget(properties)
    splitter.setHandleWidth(4)
    centre.setMinimumWidth(320)
    splitter.setCollapsible(1, False)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 0)
    splitter.setSizes([200, 800, 280])
    workspace = QWidget()
    layout = QVBoxLayout(workspace)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    root.removeWidget(splitter)
    layout.addWidget(_header(panel, buttons=buttons, library=library, properties=properties))
    layout.addWidget(splitter, 1)
    root.addWidget(workspace)
    panel._workspace_splitter = splitter
    panel._workspace_properties = properties
    panel._workspace_library = library
    panel._workspace_context_controller = PatchContext(panel, panel._workspace_library_search)
    bind_search_preference("patch", panel._workspace_library_search,
                           panel._workspace_context_controller.filter_library)
