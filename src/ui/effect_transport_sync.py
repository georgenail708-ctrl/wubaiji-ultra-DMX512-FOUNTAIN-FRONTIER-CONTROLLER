"""Keep in-progress speed text when transport feedback already matches.

Qt setValue formats the editor even for an unchanged numeric value. Use the
original spin box conversion and range to compare its actual display precision;
caller signal blocking and genuinely different external feedback stay intact.
"""
from PyQt6.QtWidgets import QDoubleSpinBox


def sync_speed_spin(spin: QDoubleSpinBox, speed: float) -> None:
    bounded = max(spin.minimum(), min(spin.maximum(), float(speed)))
    quantized = spin.valueFromText(spin.textFromValue(bounded))
    if spin.value() != quantized:
        spin.setValue(speed)
