"""TopView properties assembly; reuse original widgets and business connections.

References: Qt QFormLayout/QSplitter and the repository's PanelSection.
Qt API use only; FlowLayout is the existing BSD-3-Clause Qt example adaptation.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt

from .widget_kit import PanelSection, scope_qss
from .flow_layout import FlowLayout
from . import qascade_theme as theme


def form_section(title: str, *, expanded: bool = True) -> tuple:
    section = PanelSection(title, expanded=expanded)
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(4)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    section.add_layout(form)
    return section, form


def flow_from_layout(layout) -> QWidget:
    """Reparent existing controls into a wrapping row, without reconnecting them."""
    host = QWidget()
    flow = FlowLayout(host)
    flow.setContentsMargins(0, 0, 0, 0)
    flow.setSpacing(4)
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            flow.addWidget(item.widget())
    return host


def transfer_row(source: QFormLayout, target: QFormLayout, *, wrap: bool = False) -> None:
    row = source.takeRow(0)
    label = row.labelItem.widget() if row.labelItem is not None else None
    field = row.fieldItem
    if field is None:
        return
    content = field.widget() or field.layout()
    if wrap and field.layout() is not None:
        content = flow_from_layout(field.layout())
    if label is None:
        target.addRow(content)
    else:
        if isinstance(label, QLabel):
            label.setWordWrap(True)
            label.setMinimumWidth(0)
        if isinstance(content, QWidget):
            content.setMinimumWidth(0)
        target.addRow(label, content)


def migrate_form(box: QGroupBox, *, title: str, expanded: bool = True,
                 rows: int | None = None) -> PanelSection:
    section, form = form_section(title, expanded=expanded)
    source = box.layout()
    count = source.rowCount() if rows is None else min(rows, source.rowCount())
    for _index in range(count):
        transfer_row(source, form, wrap=True)
    box.hide()
    return section


def _placement_sections(source: QFormLayout) -> list:
    placement, pf = form_section("快捷放置 · cm")
    shape, sf = form_section("图形与组间距离", expanded=False)
    transform, tf = form_section("设备选区变换", expanded=False)
    for index in range(source.rowCount()):
        target = pf if index < 6 else sf if index < 9 else tf
        transfer_row(source, target, wrap=index in (5, 6, 8, 10, 11))
    return [placement, shape, transform]


def _tool_section(panel) -> PanelSection:
    section, form = form_section("当前工具")
    panel._workspace_tool_hint = QLabel()
    panel._workspace_tool_hint.setWordWrap(True)
    form.addRow(panel._workspace_tool_hint)
    width = getattr(panel, "_track_width_spin", None)
    if width is not None:
        width.setSuffix(" px")
        # Spanning rows follow the widgets' original track-only visibility.
        form.addRow(width)
        form.addRow(panel._track_snap_check)
    return section


def _page_geometry(panel, buttons: dict) -> PanelSection:
    section, form = form_section("整页几何缩放", expanded=False)
    form.addRow("比例", panel._scale_spin)
    row = QWidget()
    flow = FlowLayout(row)
    flow.setContentsMargins(0, 0, 0, 0)
    flow.setSpacing(4)
    for text in ("应用", "×0.8", "×1.25"):
        flow.addWidget(buttons[text])
    form.addRow(row)
    hint = QLabel("写回本页设备与图形坐标；与视图缩放独立。")
    hint.setWordWrap(True)
    form.addRow(hint)
    return section


def build_properties(panel, *, donor: QWidget, buttons: dict) -> QScrollArea:
    """Extract the old forms, preserving object identity and undo ownership."""
    boxes = donor.findChildren(QGroupBox, options=Qt.FindChildOption.FindDirectChildrenOnly)
    by_title = {box.title(): box for box in boxes}
    body = QWidget()
    body.setMinimumWidth(0)
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    scope_qss(body, f"background:{theme.BG_PANEL};")
    layout.addWidget(_tool_section(panel))
    context, form = form_section("当前页与选择")
    panel._workspace_context = QLabel()
    panel._workspace_context.setWordWrap(True)
    form.addRow(panel._workspace_context)
    layout.addWidget(context)
    placement = by_title["快捷放置（无需画线）"]
    for section in _placement_sections(placement.layout()):
        layout.addWidget(section)
    for title, expanded in (("实时选择工具（框选后再筛选）", True),
                            ("XY 基准模式", False), ("花样矩阵", False)):
        layout.addWidget(migrate_form(by_title[title], title=title, expanded=expanded))
    layout.addWidget(_page_geometry(panel, buttons))
    layout.addStretch(1)
    for section in body.findChildren(PanelSection):
        section._header.setToolTip(section._header._title)
    for spin in (panel._quick_spacing, panel._quick_matrix_y, panel._quick_circle_radius,
                 panel._mx_dx, panel._mx_dy):
        spin.setSuffix(" cm")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setMinimumWidth(220)
    scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(body)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return scroll
