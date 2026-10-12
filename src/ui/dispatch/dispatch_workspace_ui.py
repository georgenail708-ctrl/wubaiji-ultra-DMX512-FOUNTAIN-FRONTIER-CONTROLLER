"""Dispatch: original live table and scene slots in two editor regions."""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel

from ..editor_workspace_regions import caption, editor_split, flow_row, surface


def install(panel):
    root = panel.layout()
    root.setContentsMargins(8, 8, 8, 4)
    root.setSpacing(6)
    bar = root.itemAt(0).widget()
    surface(bar, decorate=True, role="raised")
    flow_row(bar)
    for label in bar.findChildren(QLabel):
        caption(label, strong=label.text().startswith("监控"))
    split = panel._split
    # _wire_splitter already restored the user's proportions; never overwrite.
    editor_split(split, split.sizes())
    for index in range(split.count()):
        region = split.widget(index)
        header = region.layout().itemAt(0).widget()
        surface(header, decorate=True, role="raised")
        if isinstance(header, QLabel):
            caption(header, strong=True)
        else:
            flow_row(header)
    caption(panel._status)
    surface(panel, role="root")
