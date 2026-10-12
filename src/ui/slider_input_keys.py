"""Give focused native inputs their navigation keys before window actions.

Uses Qt ShortcutOverride/QKeyEvent.accept; the slider still handles KeyPress.
References: https://doc.qt.io/qt-6/qkeyevent.html and Qt v6.11.0
src/widgets/widgets/qabstractslider.cpp (LGPL/GPL/commercial API, no code copy).
The existing Studio canvas uses the same scoped shortcut declaration.
"""
from PyQt6.QtCore import QEvent, QObject, Qt

_NAVIGATION_KEYS = frozenset((
    Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Left, Qt.Key.Key_Right,
    Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
))
_COMMAND_MODIFIERS = (Qt.KeyboardModifier.ControlModifier |
                      Qt.KeyboardModifier.AltModifier |
                      Qt.KeyboardModifier.MetaModifier)


class _NativeInputKeys(QObject):
    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() != QEvent.Type.ShortcutOverride or not watched.hasFocus():
            return False
        if event.modifiers() & _COMMAND_MODIFIERS:
            return False
        if event.key() in _NAVIGATION_KEYS:
            event.accept()
            return True
        return False


def reserve_input_keys(widget):
    """Install once; QObject parent is the input, with no application filter."""
    guard = getattr(widget, "_qascade_input_keys", None)
    if guard is None:
        guard = _NativeInputKeys(widget)
        widget.installEventFilter(guard)
        widget._qascade_input_keys = guard
    return guard


def reserve_slider_keys(slider):
    """Keep the existing Programmer API while sharing native-input arbitration."""
    return reserve_input_keys(slider)
