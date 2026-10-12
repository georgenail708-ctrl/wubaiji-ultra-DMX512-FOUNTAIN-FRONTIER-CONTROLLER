"""Linker regions retain the original rule list and scrollable rule editor."""
from __future__ import annotations

from ..editor_workspace_regions import (
    context_controls, editor_split, flow_row, inspector_scroll, surface,
)


def install(panel, splitter):
    root = panel.layout()
    root.setContentsMargins(8, 8, 8, 8)
    root.setSpacing(8)
    toolbar = root.itemAt(0).widget()
    flow_row(toolbar)
    surface(toolbar, decorate=True, role="raised")
    surface(splitter.widget(0), decorate=True)
    header = splitter.widget(1).layout().itemAt(0).widget()
    surface(header, decorate=True, role="raised")
    context_controls(panel._rule_editor)
    panel._rule_editor.setAccessibleName("关联规则属性：源通道到目标通道")
    sidebar = inspector_scroll(panel._rule_editor, minimum=260)
    splitter.addWidget(sidebar)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setStretchFactor(2, 1)
    editor_split(splitter, [180, 340, 420])
    panel._workspace_split = splitter
    surface(panel, role="root")
