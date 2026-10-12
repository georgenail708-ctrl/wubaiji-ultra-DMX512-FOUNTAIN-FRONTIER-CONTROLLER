"""Five Runner tool preferences; original scene parameters keep their owner."""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QSignalBlocker, Qt
from PyQt6.QtWidgets import QApplication, QLineEdit, QSpinBox

from ..widget_kit import NumSlider
from ..operating_preferences import bool_value, choice_value, number_value, settings

_NUMBER_KEYS = frozenset((Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                          Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End))


def _blocked_set(control, setter, value):
    blocker = QSignalBlocker(control)
    setter(value)
    del blocker


class _NumberMemory(QObject):
    def __init__(self, control, store, key):
        super().__init__(control)
        self.control, self.store, self.key = control, store, key
        control._operating_number_memory = self
        self.text_edited = False
        control.installEventFilter(self)
        if isinstance(control, QSpinBox):
            control.lineEdit().textEdited.connect(self.mark_text_edit)
            control.editingFinished.connect(self.finish_text_edit)

    def remember(self):
        value = self.control.float_value() if isinstance(self.control, NumSlider) else self.control.value()
        self.store.setValue(self.key, value)

    def mark_text_edit(self, _text):
        self.text_edited = True

    def finish_text_edit(self):
        if self.text_edited:
            self.text_edited = False
            self.remember()

    def opens_popup(self, event):
        if not isinstance(self.control, NumSlider):
            return False
        return event.button() == Qt.MouseButton.RightButton or (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier)

    def bind_original_popup(self):
        popup = QApplication.activePopupWidget()
        if popup is None or popup.parentWidget() is not self.control.window():
            return
        editor = popup.findChild(QLineEdit)
        if editor is not None:
            # Original commit was connected first: read its accepted value.
            editor.returnPressed.connect(self.remember)

    def commits_value(self, event):
        if event.type() == QEvent.Type.KeyRelease:
            return event.key() in _NUMBER_KEYS
        if event.type() == QEvent.Type.MouseButtonRelease:
            return event.button() == Qt.MouseButton.LeftButton
        return False

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            watched.wheelEvent(event)
            self.remember()
            return True
        if event.type() == QEvent.Type.MouseButtonPress and self.opens_popup(event):
            watched.mousePressEvent(event)
            self.bind_original_popup()
            return True
        if self.commits_value(event):
            self.remember()
        return False


def _tool_slider(panel, tooltip):
    matches = [control for control in panel.findChildren(NumSlider) if tooltip in control.toolTip()]
    if len(matches) != 1:
        raise RuntimeError("Runner tool slider identity is ambiguous: " + tooltip)
    return matches[0]


def _restore_grid(panel, store):
    shape = choice_value(store, "grid_shape", ("hex", "square"), panel._grid_mode_combo.currentData())
    spacing = number_value(store, "grid_spacing", panel._grid_size_spin.value(), 10, 2000)
    _blocked_set(panel._grid_mode_combo, panel._grid_mode_combo.setCurrentIndex,
                 panel._grid_mode_combo.findData(shape))
    _blocked_set(panel._grid_size_spin, panel._grid_size_spin.setValue, spacing)
    panel._canvas._snap.configure(panel._canvas._snap.enabled, shape, float(spacing))
    panel._grid_mode_combo.activated.connect(
        lambda _index: store.setValue("grid_shape", panel._grid_mode_combo.currentData()))
    _NumberMemory(panel._grid_size_spin, store, "grid_spacing")


def _restore_slider(panel, store, key, field, tooltip):
    control = _tool_slider(panel, tooltip)
    value = number_value(store, key, getattr(panel, field),
                         control.minimum() / control._scale, control.maximum() / control._scale)
    _blocked_set(control, control.set_float, value)
    setattr(panel, field, control.float_value())
    _NumberMemory(control, store, key)


def bind_runner_preferences(panel):
    store = settings("runner", panel)
    _restore_grid(panel, store)
    _restore_slider(panel, store, "min_radius", "_min_radius", "最小转弯半径")
    _restore_slider(panel, store, "snap_tolerance", "_snap_tol_value", "吸附容差")
    visible = bool_value(store, "show_radius", panel._show_radius)
    _blocked_set(panel._radius_btn, panel._radius_btn.setChecked, visible)
    panel._show_radius = visible
    panel._radius_btn.clicked.connect(lambda checked: store.setValue("show_radius", checked))
    panel._canvas.update()
