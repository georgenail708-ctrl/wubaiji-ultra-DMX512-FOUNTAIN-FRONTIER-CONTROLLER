"""Reuse one mouse-transparent value label per window during timeline gestures.

Qt 6.11 QToolTip.placeTip resets stylesheet caches on every changed text.
Reuse QLabel and Qascade's existing tooltip tokens without a native tip window.
"""
import weakref

from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QLabel

from ..qascade_theme import EDITOR_IVORY, FS_SMALL, RADIUS


def _label(owner):
    window = owner.window()
    label = getattr(window, "_timeline_drag_value_tip", None)
    if label is None or sip.isdeleted(label):
        label = QLabel(window)
        label.setObjectName("TimelineDragValue")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        label.setStyleSheet(
            f"background-color:{EDITOR_IVORY['panel']}; color:{EDITOR_IVORY['ink']};"
            f"border:1px solid {EDITOR_IVORY['border']}; border-radius:{RADIUS}px;"
            f"padding:3px 6px; font-size:{FS_SMALL}px;")
        label._gesture_owner = lambda: None
        window._timeline_drag_value_tip = label
    return label


def show(owner, text):
    label = _label(owner)
    previous = label._gesture_owner()
    if previous is not owner:
        if previous is not None and not sip.isdeleted(previous):
            previous.destroyed.disconnect(label.hide)
        owner.destroyed.connect(label.hide)
        label._gesture_owner = weakref.ref(owner)
    label.setText(text)
    label.adjustSize()
    window = owner.window()
    position = window.mapFromGlobal(QCursor.pos() + QPoint(12, 16))
    label.move(max(0, min(position.x(), window.width()-label.width())),
               max(0, min(position.y(), window.height()-label.height())))
    label.show()
    label.raise_()


def hide(owner):
    label = getattr(owner.window(), "_timeline_drag_value_tip", None)
    if label is not None and not sip.isdeleted(label) and label._gesture_owner() is owner:
        label.hide()
