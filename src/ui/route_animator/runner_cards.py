# -*- coding: utf-8 -*-
"""跑动面板的火车卡片与火车管理子页面（R183 W2 从 `runner_panel` 原样搬出）。

搬出动机：`runner_panel.py` 已超架构基线（4084 行）需腾出净行数容纳 R3/R10/R15
等新增；本模块两段代码**逐行未改**（仅 `self.` 语义随住址重定，`_TrainCard` /
`TrainManagerDialog` 类体原样），`runner_panel` 反向再导出两名字，既有调用方
（`_build_train_card` / `_open_train_manager` / 既有测试）零迁移。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from ..widget_kit import EmptyState, apply_dialog_chrome
from ..qascade_theme import BG_BOX, BG_DEEP, TEXT_SECONDARY

#: 与 runner_panel 同名短名同源（卡片深面/子页底色/次级文字）
_ELEV = BG_BOX
_BG = BG_DEEP
_FG_DIM = TEXT_SECONDARY


class _TrainCard(QFrame):
    """可点选火车卡片: 点击即选中为「画轨道」的目标火车。"""

    clicked = pyqtSignal(str)

    def __init__(self, tid: str) -> None:
        super().__init__()
        self._tid = tid

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._tid)
        super().mousePressEvent(event)


class TrainManagerDialog(QDialog):
    """火车管理子页面: 竖排火车卡片 + 可滚动 + 鼠标滚轮浏览。"""

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent)
        self._panel = panel
        self.setWindowTitle("火车管理")
        self.resize(440, 760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # 锁链选择工具(页面级, 不放进卡片)
        tool = QWidget()
        tool.setObjectName("chainToolBox")
        tl_box_css = (
            f"QWidget#chainToolBox{{background:{_ELEV};"
            f"border:1px solid rgba(255,255,255,17);border-radius:4px;}}")
        tool.setStyleSheet(tl_box_css)
        tlay = QVBoxLayout(tool)
        tlay.setContentsMargins(8, 6, 8, 6)
        tlay.setSpacing(4)
        row0 = QHBoxLayout()
        lbl0 = QLabel("目标火车")
        lbl0.setStyleSheet(f"color:{_FG_DIM};font-size:11px;background:transparent;")
        row0.addWidget(lbl0)
        self._train_combo = QComboBox()
        self._train_combo.currentIndexChanged.connect(self._on_train_changed)
        row0.addWidget(self._train_combo, 1)
        tlay.addLayout(row0)
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        row1.addWidget(QLabel("选X个"))
        self._chain_count = QSpinBox()
        self._chain_count.setRange(1, 200)
        self._chain_count.setValue(1)
        row1.addWidget(self._chain_count)
        row1.addWidget(QLabel("间隔Y个"))
        self._chain_interval = QSpinBox()
        self._chain_interval.setRange(0, 200)
        self._chain_interval.setValue(1)
        row1.addWidget(self._chain_interval)
        row1.addWidget(QLabel("从第N个"))
        self._chain_start = QSpinBox()
        self._chain_start.setRange(1, 200)
        self._chain_start.setValue(1)
        row1.addWidget(self._chain_start)
        tlay.addLayout(row1)
        row2 = QHBoxLayout()
        apply_btn = QPushButton("应用锁链选择")
        apply_btn.setObjectName("primary")
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.clicked.connect(self._on_apply_chain_selection)
        row2.addWidget(apply_btn)
        clear_btn = QPushButton("清除选择")
        clear_btn.setProperty("role", "cancel")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear_chain_selection)
        row2.addWidget(clear_btn)
        self._chain_label = QLabel("未选择")
        self._chain_label.setStyleSheet(f"color:{_FG_DIM};font-size:11px;background:transparent;")
        row2.addWidget(self._chain_label, 1)
        tlay.addLayout(row2)
        lay.addWidget(tool)

        # 背景仅做局部特化(滚动视口底色); 滚动条外观由全局主题提供。
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            f"QScrollArea{{background:{_BG};border:none;}}"
            f"QScrollArea>QWidget>QWidget{{background:{_BG};}}")
        self._content = QWidget()
        self._content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._cards_layout = QVBoxLayout(self._content)
        self._cards_layout.setContentsMargins(10, 10, 10, 10)
        self._cards_layout.setSpacing(8)
        self._empty_state = EmptyState(
            "暂无火车", "回到主面板点「添加火车」创建", icon_key="train")
        self._cards_layout.addWidget(self._empty_state)
        self._cards_layout.addStretch()
        self._scroll.setWidget(self._content)
        lay.addWidget(self._scroll, 1)
        # 浮窗外框 + 主/次按钮语义(D波统一解剖)
        apply_dialog_chrome(self)
        self._refresh_train_combo()
        self.rebuild()

    def _current_train_id(self) -> str:
        return str(self._train_combo.currentData() or "")

    def _refresh_train_combo(self) -> None:
        self._train_combo.blockSignals(True)
        current = self._current_train_id()
        self._train_combo.clear()
        for tid, name in self._panel._train_names.items():
            self._train_combo.addItem(name, tid)
        if current and self._train_combo.findData(current) >= 0:
            self._train_combo.setCurrentIndex(self._train_combo.findData(current))
        elif self._panel._selected_train_id and self._train_combo.findData(self._panel._selected_train_id) >= 0:
            self._train_combo.setCurrentIndex(self._train_combo.findData(self._panel._selected_train_id))
        self._train_combo.blockSignals(False)
        self._update_chain_label()

    def _on_train_changed(self) -> None:
        self._update_chain_label()

    def _on_apply_chain_selection(self) -> None:
        tid = self._current_train_id()
        if not tid:
            return
        self._panel._apply_chain_selection(
            tid, int(self._chain_count.value()),
            int(self._chain_interval.value()),
            int(self._chain_start.value()))
        self._update_chain_label()

    def _on_clear_chain_selection(self) -> None:
        tid = self._current_train_id()
        if not tid:
            return
        self._panel._clear_chain_selection(tid)
        self._update_chain_label()

    def _update_chain_label(self) -> None:
        tid = self._current_train_id()
        if not tid:
            self._chain_label.setText("未选择火车")
            return
        selected = self._panel._selected_chains.get(tid, set())
        if selected:
            self._chain_label.setText(
                "选中: " + ", ".join(str(i + 1) for i in sorted(selected)))
        else:
            self._chain_label.setText("未选择(统一调整)")

    def _detach_cards(self, orphan: bool = False) -> None:
        """从布局摘除全部火车卡片(空态占位与末尾 stretch 保留)。"""
        for i in reversed(range(self._cards_layout.count())):
            item = self._cards_layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is None or w is self._empty_state:
                continue
            self._cards_layout.removeWidget(w)
            if orphan:
                w.setParent(None)

    def rebuild(self) -> None:
        # 只从布局移除不删卡片对象: 卡片由 panel._cards 统一管理, 不随对话框销毁。
        self._detach_cards()
        for tid in list(self._panel._train_names.keys()):
            card = self._panel._cards.get(tid)
            if card is not None:
                self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
        # 空态(D波): 无火车卡片时显示占位说明, 禁止大片纯黑。
        has_cards = any(
            (self._cards_layout.itemAt(i).widget() is not None
             and self._cards_layout.itemAt(i).widget() is not self._empty_state)
            for i in range(self._cards_layout.count()))
        self._empty_state.setVisible(not has_cards)

    def closeEvent(self, event) -> None:
        # 关闭对话框时把卡片从对话框剥离, 避免随对话框销毁; 下次打开再重新挂入。
        self._detach_cards(orphan=True)
        super().closeEvent(event)


__all__ = ["TrainManagerDialog", "_TrainCard"]
