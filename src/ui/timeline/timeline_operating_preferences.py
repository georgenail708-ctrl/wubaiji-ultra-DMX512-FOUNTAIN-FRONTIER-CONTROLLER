"""One validated tool zoom; passive controller/model restoration never writes.

Reuse Qt 6.11 QSettings and the original wheel/menu/Main input paths.
Reference: r189-aux-operating-preferences.md sections 10-12.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSlot

from ..operating_preferences import number_value, settings
from .timeline_zoom import MAX_PX_PER_SEC, MIN_PX_PER_SEC


class _ZoomMemory(QObject):
    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self._store = settings("timeline", self)
        preferred = number_value(self._store, "zoom", 100.0, MIN_PX_PER_SEC, MAX_PX_PER_SEC)
        panel.ctrl.set_zoom(preferred)
        # These are connected after the original zoom handlers, so read their
        # final clamped value. ctrl.zoom_changed also serves passive restoration.
        panel._ruler.zoom_at_requested.connect(self.remember_wheel)
        panel._clip_area.zoom_at_requested.connect(self.remember_wheel)
        for action in panel._workspace_zoom_actions:
            action.triggered.connect(self.remember_menu)

    @pyqtSlot(float, float, float)
    def remember_wheel(self, _zoom, _anchor, _screen_x):
        self.remember()

    @pyqtSlot(bool)
    def remember_menu(self, _checked):
        self.remember()

    def remember(self):
        self._store.setValue("zoom", float(self._panel.ctrl.px_per_sec))


def bind_timeline_zoom(panel):
    panel._operating_zoom_memory = _ZoomMemory(panel)


def remember_main_zoom(window):
    panel = getattr(window, "_timeline_widget", None)
    memory = getattr(panel, "_operating_zoom_memory", None)
    if memory is not None:
        memory.remember()
