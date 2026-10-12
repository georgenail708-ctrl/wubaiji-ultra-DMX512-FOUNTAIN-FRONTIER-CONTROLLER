"""Native recorder button state, owned by its target input.

Uses Qt QObject receiver lifetime and the repository's PatchContext pattern;
no new timer or recording command. See r185-matrix-redesign-adoption.md §10.1.
"""
from PyQt6.QtCore import QObject, pyqtSlot


class RecordButtonState(QObject):
    def __init__(self, recorder, button):
        super().__init__(button)
        self._recorder = recorder
        self._button = button
        recorder.state_changed.connect(self.refresh)
        self.refresh("")

    @pyqtSlot(str)
    def refresh(self, _state: str) -> None:
        self._button.setText("停止录制" if self._recorder.recording else "录制输出")


def bind_record_button(recorder, button) -> None:
    button._record_state_context = RecordButtonState(recorder, button)
