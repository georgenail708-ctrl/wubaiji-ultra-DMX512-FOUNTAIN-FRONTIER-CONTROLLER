"""Network: persistent original apply/start/stop controls and resizable I/O."""
from __future__ import annotations

from ..editor_workspace_regions import (
    context_controls, horizontal_pair, surface,
)


def install(panel, root):
    page = panel._tabs.widget(0)
    content = page.widget()
    layout = content.layout()
    layout.removeWidget(panel._status_section)
    root.addWidget(panel._tabs, 1)
    root.addWidget(panel._status_section)
    surface(content, decorate=True)
    surface(panel._status_section, decorate=True, role="raised")
    surface(panel, role="root")
    root.setContentsMargins(8, 8, 8, 8)
    root.setSpacing(8)
    panel._tabs.setDocumentMode(True)
    panel._tabs.currentChanged.connect(lambda: decorate_current(panel))


def output_discovery(output, discovery):
    output.setMinimumWidth(220)
    discovery.setMinimumWidth(240)
    return horizontal_pair(output, discovery, sizes=[480, 480])


def decorate_current(panel):
    # Lazy tabs already have their original owner and business connections.
    widget = panel._tabs.currentWidget()
    if widget is not None and not getattr(widget, "_workspace_decorated", False):
        context_controls(widget)
        widget._workspace_decorated = True
