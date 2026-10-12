# -*- coding: utf-8 -*-
"""网络页基础控件：IP 输入框 + 多宇宙列表（R181 排版重做时从 network_config 拆出）。

拆出动机：``network_sections.py`` 的分区构建器与 ``network_config.py`` 都要用这些
控件；放在 network_config 里会形成循环 import。控件行为/文案与拆分前一致，
仅旧的字形图标换为 tabler 矢量（x / plus）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLineEdit, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSpinBox,
    QPushButton, QScrollArea,
)

from ..qascade_theme import (
    BG_BUTTON, BG_INPUT, FOCUS_BLUE, BORDER_NORMAL, SEP_LINE, RADIUS,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DISABLED, TEXT_SELECTED_FG,
    BG_BUTTON_HOVER, BRAND_ORANGE_SELECTED,
    STATUS_WARNING_INK as BRIGHT_WARN, INFO_BLUE as BRIGHT_INFO,
)
from ..main_icon_library import load_tabler_icon

# 语义填充 rgba 分量（QSS alpha 用 0-255 整数）：
_R = (153, 22, 22)     # ERROR_RED #991616


class IPLineEdit(QLineEdit):
    """IP 输入框（等宽字体 + 金=输入值惯例，协议字段语义）。"""

    def __init__(self, placeholder: str = "0.0.0.0", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setStyleSheet(f"""
            QLineEdit {{
                background: {BG_INPUT}; color: {BRIGHT_WARN};
                border: 1px solid {BORDER_NORMAL}; border-radius: {RADIUS}px;
                font-family: Consolas, monospace; font-size: 12px;
                padding: 4px 8px;
            }}
            QLineEdit:focus {{ border-color: {FOCUS_BLUE}; }}
        """)


class UniverseRow(QWidget):
    """宇宙行（26px 行 · 11px 右对齐标签 + 22px 删除钮）。"""

    def __init__(self, value: int = 1, on_delete=None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        lbl = QLabel("域")
        lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
        lay.addWidget(lbl)

        self.spin = QSpinBox()
        self.spin.setRange(1, 32767)
        self.spin.setValue(value)
        self.spin.setFixedWidth(80)
        self.spin.setToolTip("Universe 编号（1-32767）")
        self.spin.setAccessibleName("域编号")
        lay.addWidget(self.spin)

        del_btn = QPushButton()
        del_btn.setIcon(load_tabler_icon("x", 12, TEXT_DISABLED))
        del_btn.setFixedSize(22, 22)
        del_btn.setToolTip("删除该宇宙行")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none;
                border-radius: {RADIUS}px; }}
            QPushButton:hover {{ background: rgba({_R[0]},{_R[1]},{_R[2]},60); }}
        """)
        if on_delete:
            del_btn.clicked.connect(lambda _checked, cb=on_delete: cb(self))
        lay.addWidget(del_btn)
        lay.addStretch()

    def value(self) -> int:
        return self.spin.value()


class UniverseListPanel(QWidget):
    """宇宙列表：标题行（添加钮）+ 可滚动行区。"""

    def __init__(self, title: str, initial_values: list[int] = None, parent=None):
        super().__init__(parent)
        self._rows: list[UniverseRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        header = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;")
        header.addWidget(lbl)
        header.addStretch()
        add_btn = QPushButton("添加")
        add_btn.setIcon(load_tabler_icon("plus", 12, BRIGHT_INFO))
        add_btn.setFixedHeight(22)
        add_btn.setToolTip("添加一个输出/输入宇宙行（1-32767）")
        add_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG_BUTTON};
                color: {BRIGHT_INFO};
                border: 1px solid rgba(111,168,220,80); border-radius: {RADIUS}px;
                padding: 0 8px; font-size: 10px; }}
            QPushButton:hover {{ background: {BG_BUTTON_HOVER}; }}
            QPushButton:pressed {{ background: {BRAND_ORANGE_SELECTED}; color: {TEXT_PRIMARY}; }}
        """)
        add_btn.clicked.connect(lambda: self._add_row(1))
        header.addWidget(add_btn)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(140)
        # D波：补 viewport 内底（QScrollArea 的 background 不落到
        # qt_scrollarea_viewport，需显式选中才不会透出原生白底）。
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: {BG_INPUT};
                border: 1px solid {SEP_LINE}; border-radius: {RADIUS}px; }}
            QScrollArea > QWidget > QWidget {{ background: transparent;
                border: none; }}
            QScrollArea QWidget#_uni_container {{ background: {BG_INPUT}; }}
        """)
        scroll.setObjectName("_uni_scroll")

        self._container = QWidget()
        self._container.setObjectName("_uni_container")
        self._container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._container_lay = QVBoxLayout(self._container)
        self._container_lay.setContentsMargins(4, 4, 4, 4)
        self._container_lay.setSpacing(2)
        self._container_lay.addStretch()
        scroll.setWidget(self._container)
        outer.addWidget(scroll, stretch=1)

        for v in (initial_values if initial_values else [1]):
            self._add_row(v)

    def _add_row(self, value: int = 1):
        row = UniverseRow(value, on_delete=self._remove_row)
        self._rows.append(row)
        idx = self._container_lay.count() - 1
        self._container_lay.insertWidget(idx, row)

    def _remove_row(self, row: UniverseRow):
        if len(self._rows) <= 1:
            return
        if row not in self._rows:
            return
        self._rows.remove(row)
        self._container_lay.removeWidget(row)
        row.deleteLater()

    def values(self) -> list[int]:
        return list(dict.fromkeys(r.value() for r in self._rows))

    def set_values(self, vals: list[int]):
        for row in self._rows:
            self._container_lay.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        for v in (vals if vals else [1]):
            self._add_row(v)
