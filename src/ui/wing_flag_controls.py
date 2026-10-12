"""Shared opt-in wing checkboxes; the caller's model remains the only state."""
from PyQt6 import sip
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QWidget

FLAGS = (("wing_mirror", "反射对称"), ("wing_reverse", "倒转"))


def values(owner, prefix=""):
    return {key: bool(getattr(owner, prefix + key, False)) for key, _ in FLAGS}


def restore(owner, params, prefix=""):
    controls = getattr(owner, "_wing_flag_controls", {})
    for key, _ in FLAGS:
        attr = prefix + key
        checked = bool(params.get(key, False))
        setattr(owner, attr, checked)
        for box in controls.get(attr, []):
            if not sip.isdeleted(box):
                previous = box.blockSignals(True)
                box.setChecked(checked)
                box.blockSignals(previous)


def create(owner, callback, prefix=""):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    controls = getattr(owner, "_wing_flag_controls", None)
    if controls is None:
        controls = owner._wing_flag_controls = {}
    for key, label in FLAGS:
        box = QCheckBox(label)
        box.setObjectName(prefix + key)
        box.setChecked(bool(getattr(owner, prefix + key, False)))
        box.setToolTip("相邻翼交替反射" if key == "wing_mirror" else "倒转每一翼的取值顺序")
        controls.setdefault(prefix + key, []).append(box)
        box.toggled.connect(lambda checked, k=key: _changed(owner, prefix, k, checked, callback))
        layout.addWidget(box)
    return widget


def _changed(owner, prefix, key, checked, callback):
    state = values(owner, prefix)
    state[key] = checked
    restore(owner, state, prefix)
    callback()
