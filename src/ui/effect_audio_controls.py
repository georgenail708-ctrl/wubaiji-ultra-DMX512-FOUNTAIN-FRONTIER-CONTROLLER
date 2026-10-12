"""Explicit deep-audio capability UI for actual Effect feature consumers."""
from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from . import audio_pitch_controls as pitch


# These IDs consume pitch/confidence/chord fields or their owned histories.
# Pure spectrum/centroid presets intentionally do not claim pitch capability.
PITCH_PRESET_IDS = frozenset("atx_" + suffix for suffix in (
    "pitch_height", "pitch_octave_fold", "pitch_glide_trail", "pitch_confidence_gate",
    "chord_root_zone", "chord_quality_tint", "chord_change_accent", "harmonic_tension",
    "melody_contour_track", "harmony_stack", "pitch_stability_hold",
)) | frozenset({"r189_audio_interval_open", "r189_audio_chord_wipe"})


class EffectPitchControls(QWidget):
    """Owned Qt slots disconnect with the page; reuse its existing playback timer."""

    def __init__(self, panel):
        super().__init__(panel._param_container)
        self._panel = panel
        self._bound_analyzer = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.button = QPushButton("分析当前参考音频的音高与和弦", self)
        layout.addWidget(self.button)
        self.button.clicked.connect(self.request)
        panel._play_timer.timeout.connect(self.refresh)

    def _analyzer(self):
        return pitch.existing_analyzer(getattr(self._panel, "_audio_analyzer", None))

    def _bind(self, analyzer):
        if analyzer is self._bound_analyzer:
            return
        for name in ("file_loaded", "frame_ready"):
            signal = getattr(self._bound_analyzer, name, None)
            if signal is not None:
                try:
                    signal.disconnect(self.refresh)
                except (RuntimeError, TypeError):
                    pass
        self._bound_analyzer = analyzer
        for name in ("file_loaded", "frame_ready"):
            signal = getattr(analyzer, name, None)
            if signal is not None:
                signal.connect(self.refresh)

    @pyqtSlot()
    def refresh(self):
        if self.isHidden():
            return
        analyzer = self._analyzer()
        self._bind(analyzer)
        pitch.refresh_pitch_controls(analyzer, self.note, self.button)

    @pyqtSlot()
    def request(self):
        pitch.request_pitch_analysis(self._analyzer())
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()


def finish_parameters(panel):
    control = getattr(panel, "_audio_pitch_controls", None)
    required = getattr(panel, "_selected_id", "") in PITCH_PRESET_IDS
    if control is None and required:
        control = EffectPitchControls(panel)
        panel._audio_pitch_controls = control
        # This persistent row is outside dynamic parameter/span ownership.
        panel._param_lay.addWidget(control, 99, 0, 1, 2)
    if control is not None:
        if required:
            panel._param_container.show()
        control.setVisible(required)
        control.refresh()
