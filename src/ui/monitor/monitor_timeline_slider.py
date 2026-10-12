"""Keep recorder navigation keys on its focused Qt slider.

Uses the existing Studio ShortcutOverride pattern; no playback/recorder state.
"""
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QSlider


class MonitorTimelineSlider(QSlider):
    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and self.hasFocus():
            if event.key() in (Qt.Key.Key_Home, Qt.Key.Key_End) and event.modifiers() == Qt.KeyboardModifier.NoModifier:
                event.accept()
                return True
        return super().event(event)
