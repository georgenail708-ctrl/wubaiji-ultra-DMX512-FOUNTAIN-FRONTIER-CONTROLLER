"""Three-region TopView editor assembly around the existing QWidget canvas.

References: 编辑器分区, visual patch (semantics only), Qt QSplitter.
Reuse: PanelSection, FlowLayout (Qt BSD-3-Clause example), Tabler icons (MIT).
Only presentation state lives here; no new coordinates, selection or persistence.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QLabel, QLineEdit, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from .widget_kit import scope_qss
from .flow_layout import FlowLayout
from .workspace_search_preferences import bind_search_preference
from .main_icon_library import load_tabler_icon
from . import qascade_theme as theme
from .topview_properties_ui import build_properties, flow_from_layout
from .topview_workspace_actions import build_menus, original_buttons, region_toggle


_TOOL_HINTS = {
    "select": "框选设备；拖动与操纵柄变换。Ctrl 累计选择，Alt 暂停吸附。",
    "line": "在画布拖动绘制直线，再将设备组沿图形排布。",
    "circle": "在画布拖动绘制圆形，再将设备组沿图形排布。",
    "ellipse": "在画布拖动绘制椭圆，再将设备组沿图形排布。",
    "polyline": "单击添加折线点，右键结束；图形可直接编辑。",
    "track": "绘制轨迹，经过的设备按先后顺序选择；滚轮或 + / - 调整捕获宽度。",
}


class WorkspaceContext(QObject):
    """Read-only presentation updates, owned by the panel; no polling timer."""

    def __init__(self, panel, search):
        super().__init__(panel)
        self.panel = panel
        self.search = search
        self._visible_key = None
        self._visible_cache = set()
        self._visible_sources = None
        search.textChanged.connect(self.filter_groups)
        panel._group_board.installEventFilter(self)
        panel._tabs.currentChanged.connect(self.refresh)
        panel._tool_combo.currentIndexChanged.connect(self.refresh)
        panel.selection_changed.connect(self.refresh)
        panel.layout_changed.connect(self.refresh)
        project_signal = getattr(panel._current_project(), "project_changed", None)
        if project_signal is not None:
            project_signal.connect(self.invalidate)
        self.refresh()

    def invalidate(self, *_args) -> None:
        self._visible_key = None
        self.refresh()

    def _visible_ids(self, page: dict) -> set:
        raw = page.get("visible_fixture_ids") or []
        fixtures = getattr(self.panel._current_project(), "fixtures", [])
        key = (id(page), id(raw), len(raw), id(fixtures), len(fixtures))
        if self._visible_key != key:
            self._visible_cache = set(self.panel.fixture_ids())
            self._visible_key = key
            # Keep the bounded source references so identity keys cannot be reused.
            self._visible_sources = (page, raw, fixtures)
        return self._visible_cache

    def eventFilter(self, watched, event):
        if watched is self.panel._group_board and event.type() == QEvent.Type.LayoutRequest:
            self.filter_groups()
        return super().eventFilter(watched, event)

    def filter_groups(self, *_args) -> None:
        needle = self.search.text().strip().casefold()
        layout = self.panel._group_grid
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is None or not hasattr(widget, "group_id"):
                continue
            haystack = (getattr(widget, "group_name", widget.text()) + " " + widget.toolTip()).casefold()
            hidden = bool(needle and needle not in haystack)
            if widget.isHidden() != hidden:
                widget.setHidden(hidden)

    def refresh(self, *_args) -> None:
        panel = self.panel
        pages = panel._pages
        index = panel._active_page
        page = pages[index] if 0 <= index < len(pages) else {}
        visible = self._visible_ids(page) if pages else set()
        selected = panel.selected_ids
        name = str(page.get("name", "Sheet1"))
        panel._workspace_context.setText(
            f"{name}\n本页可见 {len(visible)} 台 · 可见选中 {len(selected & visible)} 台\n"
            f"全局选中 {len(selected)} 台 · 空间单位 cm")
        panel._workspace_tool_hint.setText(_TOOL_HINTS.get(panel.current_tool, ""))


def _group_region(panel, *, donor: QWidget) -> QWidget:
    body = QWidget()
    body.setMinimumWidth(180)
    body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    layout = QVBoxLayout(body)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)
    scope_qss(body, f"background:{theme.BG_PANEL};")
    heading = QLabel("设备组")
    heading.setStyleSheet(f"color:{theme.TEXT_PRIMARY};font-weight:bold;")
    layout.addWidget(heading)
    search = QLineEdit()
    search.setPlaceholderText("查找设备组")
    search.setClearButtonEnabled(True)
    search.setAccessibleName("查找 TopView 设备组")
    layout.addWidget(search)
    panel._group_scroll.setMinimumHeight(0)
    panel._group_scroll.setFrameShape(panel._group_scroll.Shape.NoFrame)
    layout.addWidget(panel._group_scroll, 1)
    layout.addWidget(panel._group_hint)
    # The original two layout actions keep their connected callbacks.
    old_layout = donor.layout()
    for index in range(old_layout.count()):
        item = old_layout.itemAt(index)
        if item.layout() is not None:
            layout.addWidget(flow_from_layout(item.layout()))
    panel._workspace_group_search = search
    return body


def _header(panel, *, buttons: dict, groups, properties) -> QWidget:
    bar = QWidget()
    flow = FlowLayout(bar)
    flow.setContentsMargins(4, 4, 4, 4)
    flow.setSpacing(4)
    scope_qss(bar, f"background:{theme.BG_PANEL};border-bottom:1px solid {theme.SEP_LINE};")
    title = QLabel("TopView")
    title.setStyleSheet(f"color:{theme.TEXT_PRIMARY};font-weight:bold;")
    flow.addWidget(title)
    for menu in build_menus(panel, buttons=buttons, parent=bar):
        flow.addWidget(menu)
    panel._tool_combo.setAccessibleName("TopView 当前工具")
    flow.addWidget(panel._tool_combo)
    flow.addWidget(region_toggle("设备组", icon="layout-sidebar", region=groups, parent=bar))
    flow.addWidget(region_toggle("属性", icon="layout-sidebar-right", region=properties, parent=bar))
    return bar


def _wrap_canvas_footer(panel) -> None:
    old = panel._overlay_bar
    grid = panel._canvas_frame.layout()
    row, column, rows, columns = grid.getItemPosition(grid.indexOf(old))
    grid.removeWidget(old)
    wrapped = flow_from_layout(old.layout())
    grid.addWidget(wrapped, row, column, rows, columns)
    old.hide()
    panel._overlay_bar = wrapped
    tick = Path(__file__).resolve().parent / "icon_assets/tabler/package/icons/outline/check.svg"
    wrapped.setStyleSheet(f'QCheckBox::indicator:checked {{ image: url("{tick.as_posix()}"); }}')


def _tab_scroll_icons(panel) -> None:
    icons = Path(__file__).resolve().parent / "icon_assets/tabler/package/icons/outline"
    left = (icons / "chevron-left.svg").as_posix()
    right = (icons / "chevron-right.svg").as_posix()
    panel._tabs.tabBar().setStyleSheet(
        'QTabBar QToolButton { padding: 0; min-height: 0; }'
        f'QTabBar QToolButton::left-arrow {{ image: url("{left}"); width: 12px; height: 12px; }}'
        f'QTabBar QToolButton::right-arrow {{ image: url("{right}"); width: 12px; height: 12px; }}')
    for button in panel._tabs.findChildren(QToolButton):
        arrow = button.arrowType()
        if arrow in (Qt.ArrowType.LeftArrow, Qt.ArrowType.RightArrow):
            name = "chevron-left" if arrow == Qt.ArrowType.LeftArrow else "chevron-right"
            button.setIcon(load_tabler_icon(name, 12, theme.TEXT_PRIMARY))


def install_workspace(panel, *, root, splitter, donor, old_header) -> None:
    """Called once after the original UI and overlays have been built."""
    buttons = original_buttons(old_header)
    properties = build_properties(panel, donor=donor, buttons=buttons)
    groups = _group_region(panel, donor=donor)
    old_left = splitter.widget(0)
    old_left.setParent(panel)
    old_left.hide()
    splitter.insertWidget(0, groups)
    splitter.addWidget(properties)
    splitter.setHandleWidth(4)
    splitter.widget(1).setMinimumWidth(320)
    splitter.setCollapsible(1, False)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 0)
    splitter.setSizes([200, 800, 280])
    root.removeWidget(old_header)
    old_header.hide()
    # Menu delegates still use these buttons after the builder locals expire.
    panel._workspace_command_host = old_header
    header = _header(panel, buttons=buttons, groups=groups, properties=properties)
    root.insertWidget(0, header)
    root.setContentsMargins(4, 4, 4, 4)
    _wrap_canvas_footer(panel)
    _tab_scroll_icons(panel)
    panel._workspace_splitter = splitter
    panel._workspace_properties = properties
    panel._workspace_groups = groups
    panel._workspace_context_controller = WorkspaceContext(panel, panel._workspace_group_search)
    bind_search_preference("topview", panel._workspace_group_search,
                           panel._workspace_context_controller.filter_groups)
