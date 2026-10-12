"""One visible master clock multiplier, separate from the current channel form.

The persisted zero remains a deliberate pause. Restore paths are silent; only
explicit master UI edits emit a control trajectory. Existing channel controls
retain their original per-form scope. Qt controls use the existing page styles.
"""
from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .effect_transport_sync import sync_speed_spin


def install(panel, grid, spin_style, button_style):
    section = QVBoxLayout()
    section.setSpacing(4)
    row = QHBoxLayout()
    row.setSpacing(8)
    title = QLabel("全局速度:", panel)
    title.setToolTip("所有 Effect 的公共速度倍率，与当前通道速度相乘。0 表示全部暂停。")
    row.addWidget(title)
    panel._master_speed_spin = spin = QDoubleSpinBox(panel)
    spin.setObjectName("effectMasterSpeed")
    spin.setRange(0., 16.)
    spin.setDecimals(3)
    spin.setSingleStep(.01)
    spin.setSuffix("×")
    spin.setValue(float(panel._global_speed_factor))
    spin.setFixedWidth(88)
    spin.setStyleSheet(spin_style)
    spin.setAccessibleName("全部 Effect 全局速度倍率")
    spin.setToolTip(title.toolTip())
    spin.valueChanged.connect(lambda value: set_factor(panel, value, emit=True))
    row.addWidget(spin)
    panel._master_speed_resume_btn = button = QPushButton("恢复 1 倍", panel)
    button.setObjectName("effectMasterResume")
    button.setStyleSheet(button_style)
    button.setToolTip("将全局速度恢复为 1 倍，保留各通道速度和已播放设备范围。")
    button.clicked.connect(lambda: set_factor(panel, 1., emit=True))
    row.addWidget(button)
    row.addStretch()
    section.addLayout(row)
    panel._master_speed_status = status = QLabel(panel)
    status.setWordWrap(True)
    section.addWidget(status)
    grid.addLayout(section, 6, 0, 1, 2)
    sync(panel)


def set_factor(panel, factor, *, emit=False):
    """Set the actual master even when a channel form currently owns focus."""
    factor = max(0., min(16., float(factor)))
    previous = float(panel._global_speed_factor)
    if factor != previous:
        panel._clear_current_manual_slice_cache()
    panel._global_speed_factor = factor
    sync(panel)
    if emit:
        # Read-only load/recall never becomes a recorded user gesture.
        panel.controlTrajectoryEvent.emit("effect|master_speed", factor)
        if not panel._has_channel_forms():
            panel._sync_transport_controls_to_active_form()


def sync(panel):
    """Refresh live master feedback without rebuilding/editing the author UI."""
    spin = getattr(panel, "_master_speed_spin", None)
    if spin is None:
        return
    speed = float(panel._global_speed_factor)
    with QSignalBlocker(spin):
        sync_speed_spin(spin, speed)
    panel._master_speed_status.setText("全局已暂停：所有效果保持静帧" if speed == 0.
                                       else "全局倍率作用于所有 Effect")
    panel._master_speed_resume_btn.setEnabled(speed != 1.)
    title = getattr(panel, "_current_speed_title", None)
    if title is not None:
        title.setText("通道速度:" if panel._has_channel_forms() else "效果速度:")
