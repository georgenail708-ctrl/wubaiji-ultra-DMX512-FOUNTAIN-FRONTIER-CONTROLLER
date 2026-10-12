"""Original streamed peaks with cooperative QThread lifecycle.

Qt6.11 QThread APIs (no upstream code copied); SoundFile0.13.1 BSD3.
Ref: r185-waveform-loader-lifecycle-review.md. Peaks algorithm is migrated
from AudioRefTrack; result_ready never shadows native QThread.finished.
"""
from __future__ import annotations

import logging

import numpy as np
from PyQt6.QtCore import QCoreApplication, QThread, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


def _consume_peaks(peaks, buffer, peak_index, samples_per_peak):
    while len(buffer) >= samples_per_peak and peak_index < len(peaks):
        peaks[peak_index] = float(np.abs(buffer[:samples_per_peak]).max())
        buffer = buffer[samples_per_peak:]
        peak_index += 1
    return buffer, peak_index


class WaveformLoader(QThread):
    """One blocking decode; the application owns its Qt lifetime until done."""

    result_ready = pyqtSignal(int, str, object, float)
    error = pyqtSignal(int, str, str)

    def __init__(self, path, parent=None, *, request_id=0):
        super().__init__(parent if parent is not None else QCoreApplication.instance())
        self._path = path
        self.request_id = request_id
        self.finished.connect(self.deleteLater)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

    def run(self):
        try:
            result = self._decode()
            if result is not None and not self.isInterruptionRequested():
                self.result_ready.emit(self.request_id, self._path, *result)
        except Exception as error:
            if not self.isInterruptionRequested():
                self.error.emit(self.request_id, self._path, str(error))

    def _decode(self):
        if self.isInterruptionRequested():
            return None
        try:
            import soundfile as sf
        except ImportError:
            return self._librosa_result()
        return self._soundfile_result(sf)

    def _soundfile_result(self, sf):
        info = sf.info(self._path)
        duration = info.frames / info.samplerate
        count = max(2000, min(50000, int(duration * 20)))
        samples_per_peak = max(1, info.frames // count)
        peaks = np.zeros(count, dtype=np.float32)
        buffer = np.empty(0, dtype=np.float32)
        peak_index = 0
        with sf.SoundFile(self._path, "r") as source:
            while peak_index < count:
                if self.isInterruptionRequested():
                    return None
                chunk = source.read(1024 * 256, dtype="float32", always_2d=True)
                if chunk.shape[0] == 0:
                    break
                mono = chunk.mean(axis=1) if info.channels > 1 else chunk[:, 0]
                buffer = np.concatenate([buffer, mono])
                buffer, peak_index = _consume_peaks(peaks, buffer, peak_index, samples_per_peak)
        if len(buffer) > 0 and peak_index < count:
            peaks[peak_index] = float(np.abs(buffer).max())
            peak_index += 1
        return peaks[:peak_index], duration

    def _librosa_result(self):
        try:
            import librosa
        except ImportError as error:
            raise RuntimeError("请安装 soundfile 或 librosa：pip install soundfile librosa") from error
        audio, sample_rate = librosa.load(self._path, sr=None, mono=True)
        if self.isInterruptionRequested():
            return None
        duration = librosa.get_duration(y=audio, sr=sample_rate)
        count = max(2000, min(50000, int(duration * 20)))
        chunk = max(1, len(audio) // count)
        peaks = np.array([
            np.abs(audio[index * chunk:(index + 1) * chunk]).max()
            for index in range(count)
        ], dtype=np.float32)
        return peaks, duration

    @pyqtSlot()
    def _on_app_quit(self):
        self.requestInterruption()
        if not self.wait(2000):
            logger.error("Waveform decoder has not stopped before application quit: %s", self._path)
