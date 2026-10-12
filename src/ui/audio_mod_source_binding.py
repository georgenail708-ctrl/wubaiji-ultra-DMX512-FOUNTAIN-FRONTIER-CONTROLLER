# -*- coding: utf-8 -*-
"""AM23-F owner-bound publication from the existing global AudioAnalyzer.

No new capture/timer, no output write, no global lambda holding Main alive.
The owner supplies an already selected analyzer; this adapter never starts it.
"""
from PyQt6.QtCore import QObject, pyqtSlot

from src.engine import audio_mod_feature_view as views


def _mean(bands, start, stop):
    values = bands[start:stop]
    return sum(values) / len(values) if values else 0.0


class AudioModSourceBinding(QObject):
    def __init__(self, owner, analyzer, initialmode):
        super().__init__(owner)
        self._analyzer = analyzer
        self._disposed = False
        self._epoch = views.source_epoch()
        self._mode = "off"
        self._retired_frame = None
        analyzer.frame_ready.connect(self._on_frame)
        owner.destroyed.connect(self.dispose)
        self.set_source(initialmode)

    def set_source(self, mode: str, audio_path: str = ""):
        """Call after the original source switch; this does not switch the analyzer."""
        if self._disposed:
            return
        self._mode = mode
        self._retired_frame = self._analyzer.current_frame()
        self._epoch = views.set_source(mode, audio_path)

    @pyqtSlot(object)
    def _on_frame(self, frame):
        if self._disposed:
            return
        # Collapse queued frames to the actual current snapshot; do not reuse a
        # pre-switch frame (AudioAnalyzer.set_mode itself preserves _last_frame).
        if frame is self._retired_frame or frame is not self._analyzer.current_frame():
            return
        if getattr(self._analyzer, "_mode", self._mode) != self._mode:
            return
        bands = list(getattr(frame, "bands", ()) or ())
        values = {"energy": getattr(frame, "rms", 0.0),
                  "beat_strength": float(bool(getattr(frame, "is_beat", False))),
                  "band_low": _mean(bands, 0, 2),
                  "band_mid": _mean(bands, 2, 4),
                  "band_high": _mean(bands, 4, 8),
                  "onset_flux": getattr(frame, "onset_strength", 0.0),
                  "bpm": getattr(frame, "bpm", 120.0),
                  "beat_phase": getattr(frame, "beat_phase", 0.0),
                  "time": getattr(frame, "t", 0.0),
                  "has_audio": (bool(getattr(frame, "has_audio", False))
                                and float(getattr(frame, "rms", 0.0)) > 1e-5)}
        views.publish_live_snapshot(values, epoch=self._epoch)

    @pyqtSlot()
    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        try:
            self._analyzer.frame_ready.disconnect(self._on_frame)
        except (TypeError, RuntimeError):
            pass
        views.retire_source(self._epoch)
        self._analyzer = None


def bind_audio_mod_source(owner, analyzer, initialmode):
    """Install once after original analyzer/source ownership exists; close calls dispose."""
    return AudioModSourceBinding(owner, analyzer, initialmode)
