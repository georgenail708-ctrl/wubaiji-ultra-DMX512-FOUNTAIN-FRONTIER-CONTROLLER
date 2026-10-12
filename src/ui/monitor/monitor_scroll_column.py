# -*- coding: utf-8 -*-
"""监视器右栏滚动容器（01 清单 C1「右半边挤一大坨，窗口根本放不下」）。

根因：右栏（域选择 / 多域总览 / 直方图 / 通道历史 / 录制回放 / 导出 / MA 小键盘 /
过滤规则）是固定栏宽的普通 QWidget + QVBoxLayout，**不在任何滚动
区里**——窗口高度不足时 Qt 把各区块压向最小高（直方图/历史曲线压成一条线、小键盘
只剩一条缝、规则文字互相压叠）。

修法（02 规范 §6.2「内容可能溢出的栏一律放 QScrollArea，宁可滚动不许挤压」）：
  * ``NoSqueezeColumn``：最小高度提示 = 布局建议高度 → 外层
    ``QScrollArea(widgetResizable)`` 不再把它压扁，而是出竖向滚动条；
    区块折叠（PanelSection 收起）时建议高度随之变小，滚动范围自动收缩；
  * ``wrap_right_column``：外包 QScrollArea（无边框、禁横向滚动），宽度 = 栏宽 +
    竖向滚动条宽，滚动条出现时不挤占内容。
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QFrame, QScrollArea, QWidget

__all__ = ["NoSqueezeColumn", "wrap_right_column"]


class NoSqueezeColumn(QWidget):
    """滚动区内的内容栏：只滚不挤（最小高度 = 布局建议高度）。"""

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        base = super().minimumSizeHint()
        return QSize(base.width(), max(base.height(), self.sizeHint().height()))


def wrap_right_column(column: QWidget, width: int) -> QScrollArea:
    """把固定宽的右栏包进竖向滚动区（返回滚动区，直接 addWidget 到 splitter）。"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(column)
    bar_w = max(8, int(scroll.verticalScrollBar().sizeHint().width()))
    scroll.setFixedWidth(int(width) + bar_w)
    return scroll
