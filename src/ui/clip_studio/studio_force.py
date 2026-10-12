"""Motor-force card adapter to the single model preset table and native form."""
from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox

from src.core.models import MOTOR_FORCE_PROFILES
from src.ui.clip_studio.studio_graph import param_value

_PARAMS = {"phase_amount": "phase_amt", "accel": "accel",
           "cruise": "cruise", "decel": "decel"}


def form_params(params) -> dict:
    """The displayed values equal the named preset, with authored overrides."""
    preset = str(params.get("preset", "natural"))
    defaults = MOTOR_FORCE_PROFILES.get(preset, {})
    return {**{key: defaults[source] for key, source in _PARAMS.items()
               if source in defaults}, **params}


def compile_fields(node) -> dict:
    """A preset chooses defaults; explicit card controls override those defaults."""
    materialized = {**node, "params": form_params(node.get("params") or {})}
    return {"motor_force_preset": param_value(materialized, "preset"),
            **{f"motor_force_{key}": param_value(materialized, key) for key in _PARAMS}}


def connect_preset_form(form) -> None:
    """Selecting a preset loads its real values; later numeric edits remain live."""
    preset = form.findChild(QComboBox, "preset")
    if preset is None:
        return

    def apply_preset():
        defaults = MOTOR_FORCE_PROFILES.get(str(preset.currentData()), {})
        for key, source in _PARAMS.items():
            spin = form.findChild(QDoubleSpinBox, key)
            if spin is not None and source in defaults:
                spin.setValue(float(defaults[source]))

    preset.currentIndexChanged.connect(apply_preset)
