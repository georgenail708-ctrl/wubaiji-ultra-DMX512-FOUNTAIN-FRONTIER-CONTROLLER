"""One active waveform decoder and one latest request per AudioRefTrack.

Direct Qt queued/native slot and application ownership APIs; no new scheduler.
Result identity is independent of a repeated path. See the R185 lifecycle
review for the original queued-result reproducer, sources and licensing.
"""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, pyqtSlot

from .waveform_loader import WaveformLoader


class WaveformLoadSession(QObject):
    def __init__(self, track):
        super().__init__(track)
        self._track = track
        self._active = None
        self._pending = None
        self._request_id = 0
        track.installEventFilter(self)
        track.destroyed.connect(self.cancel)

    def request(self, path):
        self.cancel()
        self._pending = (self._request_id, path)
        if self._active is None:
            self._start_pending()

    @pyqtSlot()
    def cancel(self):
        self._request_id += 1
        self._pending = None
        if self._active is not None and not sip.isdeleted(self._active):
            self._active.requestInterruption()

    def _start_pending(self):
        if self._pending is None or sip.isdeleted(self._track):
            return
        request_id, path = self._pending
        self._pending = None
        loader = WaveformLoader(path, request_id=request_id)
        self._active = self._track._loader = loader
        loader.result_ready.connect(self._loaded)
        loader.error.connect(self._failed)
        loader.finished.connect(self._finished)
        loader.start()

    def _is_current(self, request_id, path):
        return (not sip.isdeleted(self._track)
                and request_id == self._request_id
                and path == self._track._audio_path)

    @pyqtSlot(int, str, object, float)
    def _loaded(self, request_id, path, peaks, duration):
        if self._is_current(request_id, path):
            self._track._on_waveform_loaded(peaks, duration)

    @pyqtSlot(int, str, str)
    def _failed(self, request_id, path, message):
        if self._is_current(request_id, path):
            self._track._on_waveform_error(message)

    @pyqtSlot()
    def _finished(self):
        if self.sender() is not self._active:
            return
        self._active = None
        if not sip.isdeleted(self._track):
            self._track._loader = None
            self._start_pending()

    def shutdown(self, timeout_ms=2000):
        self.cancel()
        loader = self._active
        if loader is None or sip.isdeleted(loader):
            return True
        stopped = loader.wait(timeout_ms)
        if stopped:
            self._active = None
            if not sip.isdeleted(self._track):
                self._track._loader = None
        return stopped

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Close and not self.shutdown():
            self._track._on_waveform_error("音频解码仍在收尾，请稍后关闭。")
            event.ignore()
            return True
        return super().eventFilter(watched, event)


def shutdown_waveform_loader(window):
    """MainWindow close guard, before other production owners are stopped."""
    timeline = getattr(window, "_timeline_widget", None)
    track = getattr(timeline, "_audio_track", None)
    session = getattr(track, "_waveform_load", None)
    if session is None or session.shutdown():
        return True
    track._on_waveform_error("音频解码仍在收尾，请稍后关闭。")
    return False
