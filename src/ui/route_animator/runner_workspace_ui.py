"""Runner editor shell: original 2D canvas, wrapping tools, scrollable fleet."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QScrollArea, QVBoxLayout

from ..editor_workspace_regions import (
    context_controls, flow_row, horizontal_pair, inspector_scroll, surface,
)
from .runner_operating_preferences import bind_runner_preferences


def build(panel):
    # The canvas is an editor painter, not a native pixel output widget.
    # Its own bed precedes route/device content; no child material film.
    panel._canvas.setProperty("editorSurfaceRole", "canvas")
    root = QVBoxLayout(panel)
    root.setContentsMargins(8, 8, 8, 4)
    root.setSpacing(6)
    hud = panel._build_hud()
    for index in (0, 1):
        row = hud.layout().itemAt(index).widget()
        flow_row(row)
        surface(row, decorate=True, role="raised")
    surface(hud, role="panel")
    root.addWidget(hud)
    switches = panel._build_switch_bar()
    flow_row(switches)
    context_controls(switches)
    surface(switches, role="raised")
    root.addWidget(switches)
    fleet = panel._build_fleet_panel()
    surface(fleet, decorate=True)
    sidebar = inspector_scroll(fleet, minimum=240)
    panel._workspace_split = horizontal_pair(panel._canvas, sidebar, sizes=[800, 300])
    root.addWidget(panel._workspace_split, 1)
    deck = panel._build_train_deck()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(deck)
    scroll.setMinimumHeight(48)
    scroll.setMaximumHeight(200)
    panel._workspace_deck_scroll = scroll
    root.addWidget(scroll)
    status = panel._build_status_bar()
    surface(status, decorate=True, role="raised")
    root.addWidget(status)
    surface(panel, role="root")
    bind_runner_preferences(panel)
