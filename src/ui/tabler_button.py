# -*- coding: utf-8 -*-
"""tabler_button.py — 带 tabler 矢量图标的 QPushButton 工厂（R173 emoji 残留矢量化）。

原写法 `QPushButton("🗑 删除选中")` 把 emoji 拼进文字（字体缺字时显示方框）；改为
文字 + setIcon(tabler)。做成一行工厂，调用点逐行替换构造语句，不给旧超限函数加行。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QPushButton, QWidget

from .main_icon_library import load_tabler_icon


def tabler_button(text: str, icon: str, color: str, size: int = 14,
                  parent: Optional[QWidget] = None) -> QPushButton:
    """返回文字为 text、图标为 tabler `icon`（染 color）的按钮；图标缺失时只剩文字。"""
    btn = QPushButton(text, parent)
    btn.setIcon(load_tabler_icon(icon, size, color))
    btn.setIconSize(QSize(size, size))
    return btn
