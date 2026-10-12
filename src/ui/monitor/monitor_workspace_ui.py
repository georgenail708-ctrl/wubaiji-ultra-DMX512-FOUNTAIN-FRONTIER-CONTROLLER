"""Monitor: shared light chrome; preserve the original channel heatmap values."""
from __future__ import annotations

from ..editor_workspace_regions import caption, editor_split, surface


def install(panel, splitter):
    root = panel.layout()
    root.setSpacing(8)
    sidebar = splitter.widget(1)
    content = sidebar.widget()
    content.setMinimumWidth(268)
    content.setMaximumWidth(16777215)
    sidebar.setMinimumWidth(280)
    sidebar.setMaximumWidth(480)
    surface(content, decorate=True)
    surface(sidebar)
    caption(panel._filter_status_label)
    caption(panel._sel_label)
    editor_split(splitter, [780, 320])
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 0)
    panel._workspace_split = splitter
    surface(panel, role="root")
