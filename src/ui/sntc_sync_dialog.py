"""Small SNTC status viewer; receiver and timeline ownership stay with MainWindow.

Hide/close never changes playback or the receiver. There is no polling timer or
network worker in this view, and no project/history mutation.
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, QSignalBlocker, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QDialog, QLabel, QVBoxLayout

from . import qascade_theme as theme
from .blender_kit import PanelSection, apply_dialog_chrome, prop_row
from .network_panel.network_button_factory import make_button


@dataclass(frozen=True)
class SntcViewState:
    enabled: bool = False
    status: str = "同步已关闭"
    source: str = "—"
    position: str = "—"
    rate: str = "—"
    error: str = ""


class SntcSyncDialog(QDialog):
    """One reusable modeless viewer, with an explicit receiver enable switch."""

    enabled_requested = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("开启SNTC同步")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumWidth(360)
        self.setFont(theme.cached_font(theme.FS_BODY))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self._enabled = QCheckBox("开启 SNTC 同步", self)
        self._enabled.setAccessibleName("开启 SNTC 同步")
        self._enabled.toggled.connect(self.enabled_requested.emit)
        layout.addWidget(self._enabled)

        section = PanelSection("外部时间线", parent=self)
        self._status = self._plain_label()
        self._source = self._plain_label(selectable=True)
        self._position = self._plain_label(selectable=True)
        self._position.setFont(theme.cached_font(theme.FS_BODY, families=theme.MONO_FONT_FAMILIES))
        self._rate = self._plain_label()
        for title, label in (("状态", self._status), ("来源", self._source),
                             ("时间位置", self._position), ("帧率", self._rate)):
            section.add_widget(prop_row(title, label, label_width=68))
        layout.addWidget(section)

        self._error = self._plain_label()
        self._error.setWordWrap(True)
        layout.addWidget(self._error)
        hint = self._plain_label()
        hint.setWordWrap(True)
        hint.setText("收到时间码时，播放状态和指针跟随外部时间线。"
                     "时间码暂时停更会保持当前输出；没有信号时可自行拖动指针。"
                     "关闭这个窗口会继续同步。")
        layout.addWidget(hint)
        hide = make_button("隐藏窗口", role="neutral", parent=self)
        hide.clicked.connect(self.hide)
        layout.addWidget(hide, alignment=Qt.AlignmentFlag.AlignRight)
        apply_dialog_chrome(self)
        self.present(SntcViewState())

    def _plain_label(self, *, selectable: bool = False) -> QLabel:
        label = QLabel(self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        if selectable:
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def present(self, state: SntcViewState) -> None:
        """Reflect current service state without causing a toggle/restart."""
        blocker = QSignalBlocker(self._enabled)
        try:
            self._enabled.setChecked(state.enabled)
        finally:
            blocker.unblock()
        self._status.setText(state.status)
        self._source.setText(state.source)
        self._position.setText(state.position)
        self._rate.setText(state.rate)
        self._error.setText(state.error)
        self._error.setVisible(bool(state.error))

    def reject(self) -> None:
        # Escape is another way to hide the viewer, not an input/output command.
        self.hide()

    def closeEvent(self, event) -> None:
        self.hide()
        event.ignore()
