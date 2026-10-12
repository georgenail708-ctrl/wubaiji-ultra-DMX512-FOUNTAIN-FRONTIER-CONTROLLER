"""DMXTimeline — ProcessorTabsPanel

轨道关联器底部处理器 Dock 包装面板。
该包装面板保持旧版 TrackLinkerPanel 的 process_done/save_state/load_state 接口，
因此 MainWindow 与项目保存链路无需感知内部结构变化。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QScrollArea, QSizePolicy,
    QAbstractScrollArea,
)
from PyQt6.QtCore import pyqtSignal, QSize, Qt

log = logging.getLogger(__name__)


class _DockTabWidget(QTabWidget):
    """底部 Dock 页签：不把任一子页面的大 sizeHint 汇总成 Dock 最小宽度。"""

    def sizeHint(self):  # noqa: N802 - Qt override
        return QSize(360, 220)

    def minimumSizeHint(self):  # noqa: N802 - Qt override
        return QSize(0, 0)


class _DockScrollArea(QScrollArea):
    """滚动页签视口：阻断内部大尺寸控件把底部 Dock 顶宽/顶高。"""

    def sizeHint(self):  # noqa: N802 - Qt override
        return QSize(360, 220)

    def minimumSizeHint(self):  # noqa: N802 - Qt override
        return QSize(0, 0)


class ProcessorTabsPanel(QWidget):
    """处理器 Dock 的选项卡容器。

    当前仅保留轨道关联器页签；旧版“自映射 / 广义 Cue”页签已按需求移除。
    """

    process_done = pyqtSignal(object)
    def __init__(self, pm, nm=None, parent=None):
        super().__init__(parent)
        self._pm = pm
        self._nm = nm
        self._linker = None
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabs = _DockTabWidget()
        tabs.setDocumentMode(True)
        tabs.setMinimumSize(0, 0)
        tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        # S1 重构：删除本地第二套 QTabBar/QTabWidget 样式（与全局主题互斥不一致），
        # 统一走 qascade_theme.GLOBAL_QSS。
        root.addWidget(tabs)
        self._tabs = tabs

        from .track_linker_panel import TrackLinkerPanel
        self._linker = TrackLinkerPanel(self._pm, nm=self._nm)
        self._linker.setMinimumSize(0, 0)
        self._linker.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if hasattr(self._linker, "process_done"):
            self._linker.process_done.connect(self.process_done.emit)

        # 轨道关联器右侧编辑区内容较多。直接作为 QTabWidget 页签时，
        # 子控件的 sizeHint/minimumSizeHint 会向上传播，导致底部 Dock 很难拖小。
        # 外包一层滚动视口后，Dock 可以自由缩放；空间不足时在页签内部滚动。
        linker_page = _DockScrollArea()
        linker_page.setWidgetResizable(True)
        linker_page.setFrameShape(QScrollArea.Shape.NoFrame)
        linker_page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        linker_page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        linker_page.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        linker_page.setMinimumSize(0, 0)
        linker_page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        linker_page.setWidget(self._linker)
        tabs.addTab(linker_page, "轨道关联器")

    def set_network_manager(self, nm):
        self._nm = nm
        if self._linker is not None and hasattr(self._linker, "set_network_manager"):
            self._linker.set_network_manager(nm)

    def set_project_manager(self, pm):
        self._pm = pm
        if self._linker is not None and hasattr(self._linker, "set_project_manager"):
            self._linker.set_project_manager(pm)

    def save_state(self) -> Dict[str, Any]:
        state: Dict[str, Any] = {"version": 2}
        if self._linker is not None and hasattr(self._linker, "save_state"):
            state["track_linker"] = self._linker.save_state()
        return state

    def load_state(self, d: dict):
        if not isinstance(d, dict):
            return
        # 兼容旧工程：旧版 processor_state 直接就是 TrackLinkerPanel 状态。
        linker_state = d.get("track_linker") if "track_linker" in d else d
        if linker_state and self._linker is not None and hasattr(self._linker, "load_state"):
            self._linker.load_state(linker_state)
