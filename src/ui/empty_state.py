# -*- coding: utf-8 -*-
"""empty_state.py — Carbon 五段式空态控件（R183-B2）。

从 `widget_kit.EmptyState` 扩参而来；widget_kit 原位 re-export，**既有 21 个
构造点导入路径零改动**。五段序：图 → 标题 → 一句话 → 主按钮 → 次级链接。

* 旧签名 ``EmptyState(text, hint, icon_key, parent)`` 完全兼容（tabler 图标路径保留）。
* 新参（全部 keyword-only，默认 None）：
  - ``image_id``：B0 空态插画 id（``art_asset_library``，如 ``empty_float``），
    muted 灰染色（Carbon 深色规范）；给出时优先于 tabler 图标行。
  - ``action_text`` + ``on_action``：主按钮（SELECT_BLUE 实心，回调可省）。
  - ``link_text`` + ``on_link``：次级链接（扁平文字按钮，FOCUS_BLUE 下划线）。
* 回调是**通用呈现层**接线（点击转发），不接任何业务信号。

纪律（沿 widget_kit）：全部纯 QWidget/QSS 实现，禁 image:url()；颜色一律
qascade_theme 令牌。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from . import qascade_theme as _th

PAD_L = 12

#: B0 插画渲染边长（屏幕像素；muted 灰 = TEXT_DISABLED 染色）。
IMAGE_PX = 96


def _image_pixmap(image_id: str):
    """B0 插画 → QPixmap（muted 灰染色；未登记/渲染失败 → None 走图标回退）。"""
    if not image_id:
        return None
    try:
        from .art_asset_library import load_art_pixmap
        pm = load_art_pixmap(image_id, IMAGE_PX, IMAGE_PX, _th.TEXT_DISABLED)
    except Exception:
        return None
    return pm if (pm is not None and not pm.isNull()) else None


def _icon_label(icon_key: str) -> QLabel:
    """tabler 图标行（旧路径，40px TEXT_DISABLED）。"""
    from .main_icon_library import load_tabler_pixmap
    lab = QLabel()
    lab.setPixmap(load_tabler_pixmap(icon_key, 40, _th.TEXT_DISABLED))
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lab.setStyleSheet("background: transparent;")
    return lab


def _text_label(text: str, color: str, size: int) -> QLabel:
    """居中可换行文本行（标题/一句话共用）。"""
    lab = QLabel(text)
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lab.setWordWrap(True)
    lab.setStyleSheet(
        f"color: {color}; font-size: {size}px; background: transparent;")
    return lab


_ACTION_QSS = (
    f"QPushButton {{ background: {_th.SELECT_BLUE}; color: {_th.TEXT_SELECTED_FG};"
    f" border: 1px solid {_th.SELECT_BLUE}; border-radius: 4px;"
    " min-height: 24px; padding: 2px 18px; }"
    f"QPushButton:hover {{ background: {_th.SELECT_BLUE_HOVER}; }}"
    f"QPushButton:pressed {{ background: {_th.SELECT_BLUE_PRESSED}; }}")

_LINK_QSS = (
    "QPushButton { background: transparent; border: none;"
    f" color: {_th.FOCUS_BLUE}; font-size: 11px;"
    " text-decoration: underline; padding: 2px 4px; }"
    f"QPushButton:hover {{ color: {_th.TEXT_PRIMARY}; }}")


def _action_button(text: str, qss: str, on_click) -> QPushButton:
    """主按钮/次级链接共用构造（回调可为 None = 纯展示）。"""
    btn = QPushButton(str(text))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(qss)
    if on_click is not None:
        btn.clicked.connect(lambda _checked=False, _cb=on_click: _cb())
    return btn


class EmptyState(QWidget):
    """空态占位（Carbon 五段式）：图 → 标题 → 一句话 → 主按钮 → 次级链接。"""

    def __init__(self, text: str, hint: str = "", icon_key: str | None = None,
                 parent=None, *, image_id: str | None = None,
                 action_text: str | None = None, on_action=None,
                 link_text: str | None = None, on_link=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(PAD_L * 2, PAD_L * 2, PAD_L * 2, PAD_L * 2)
        v.setSpacing(8)
        v.addStretch(1)
        pm = _image_pixmap(str(image_id or ""))
        if pm is not None:                                   # B0 插画优先（R183-B2）
            art = QLabel()
            art.setPixmap(pm)
            art.setAlignment(Qt.AlignmentFlag.AlignCenter)
            art.setStyleSheet("background: transparent;")
            v.addWidget(art)
        elif icon_key:                                       # 旧 tabler 图标路径照旧
            v.addWidget(_icon_label(icon_key))
        v.addWidget(_text_label(text, _th.TEXT_SECONDARY, 12))
        if hint:
            v.addWidget(_text_label(hint, _th.TEXT_DISABLED, 11))
        if action_text:
            v.addLayout(self._action_row(action_text, on_action,
                                         link_text, on_link))
        v.addStretch(2)

    @staticmethod
    def _action_row(action_text, on_action, link_text, on_link) -> QHBoxLayout:
        """主按钮 + 次级链接行（Carbon 四/五段；居中）。"""
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch(1)
        row.addWidget(_action_button(action_text, _ACTION_QSS, on_action))
        if link_text:
            row.addWidget(_action_button(link_text, _LINK_QSS, on_link))
        row.addStretch(1)
        return row
