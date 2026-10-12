# -*- coding: utf-8 -*-
"""R181 矩阵面板空态引导件：图标 + 引导文案 + 入口按钮。

用途：幻灯片帧列表为空（或尚未载入设备）时，给出**可点的下一步**，而不是
只留一句静态说明。文案/按钮色一律取 `qascade_theme` 令牌；图标走 tabler
矢量（`main_icon_library.load_tabler_pixmap`），禁 emoji。

为什么单独成模块：`matrix_slideshow_panel.py` 已超文件行数基线（门禁只警告），
本控件独立受"新函数 ≤100 行 / CC ≤8"约束，面板侧只留一行构造调用。
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from .. import qascade_theme as _th

#: 入口动作：(按钮文案, 回调, tabler 图标名, 是否主按钮)
GuideAction = Tuple[str, Callable[[], None], str, bool]

_BTN_QSS_PRIMARY = (
    "QPushButton {"
    f" background:{_th.SELECT_BLUE}; color:{_th.TEXT_SELECTED_FG};"
    f" border:1px solid {_th.SELECT_BLUE}; border-radius:{_th.RADIUS}px;"
    " min-height:22px; padding:2px 12px; font-size:11px; }"
    f"QPushButton:hover {{ background:{_th.SELECT_BLUE_HOVER}; }}"
    f"QPushButton:pressed {{ background:{_th.SELECT_BLUE_PRESSED}; }}"
)
_BTN_QSS_SECONDARY = (
    "QPushButton {"
    f" background:{_th.BG_BUTTON}; color:{_th.TEXT_PRIMARY};"
    f" border:1px solid {_th.BORDER_NORMAL}; border-radius:{_th.RADIUS}px;"
    " min-height:22px; padding:2px 12px; font-size:11px; }"
    f"QPushButton:hover {{ background:{_th.BG_BUTTON_HOVER};"
    f" border-color:{_th.FOCUS_BLUE}; }}"
    f"QPushButton:pressed {{ background:{_th.SELECT_BLUE_PRESSED}; }}"
)


def _guide_icon_pixmap(icon_key: Optional[str]) -> Optional[QPixmap]:
    """tabler 矢量图标 → QPixmap（不可用时返回 None，调用方跳过图标行）。"""
    if not icon_key:
        return None
    try:
        from ..main_icon_library import load_tabler_pixmap
        pm = load_tabler_pixmap(str(icon_key), 40, _th.TEXT_DISABLED)
        return pm if (pm is not None and not pm.isNull()) else None
    except Exception:
        return None


def _guide_art_pixmap(image_id: Optional[str]) -> Optional[QPixmap]:
    """B0 空态插画（R183-B2，可选）→ QPixmap（muted 灰染色；失败 → None 走图标回退）。"""
    if not image_id:
        return None
    try:
        from ..art_asset_library import load_art_pixmap
        pm = load_art_pixmap(str(image_id), 96, 96, _th.TEXT_DISABLED)
    except Exception:
        return None
    return pm if (pm is not None and not pm.isNull()) else None


def _make_guide_button(text: str, slot: Callable[[], None], icon_key: str,
                       primary: bool) -> QPushButton:
    """入口按钮：主按钮品牌蓝实心 / 次按钮中性面（令牌 QSS）。"""
    btn = QPushButton(str(text))
    btn.setStyleSheet(_BTN_QSS_PRIMARY if primary else _BTN_QSS_SECONDARY)
    if primary:
        btn.setObjectName("primary")
    if slot is not None:
        btn.clicked.connect(lambda _checked=False, _s=slot: _s())
    if icon_key:
        try:
            from ..main_icon_library import load_tabler_icon
            ic = load_tabler_icon(str(icon_key), 16,
                                  _th.TEXT_SELECTED_FG if primary else _th.TEXT_PRIMARY)
            if ic is not None and not ic.isNull():
                btn.setIcon(ic)
        except Exception:
            pass
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return btn


class MatrixEmptyGuide(QWidget):
    """矩阵面板空态：图（tabler 图标或 B0 插画 `image_id`，R183-B2 可选）+ 标题
    + 提示 + 入口按钮行。

    对外查询口径：`title()` / `button_labels()` / `click(index)`（测试与键盘路径
    复用；`click` 等价于用户按下该入口按钮）。
    """

    def __init__(self, title: str, hint: str = "", icon_key: Optional[str] = None,
                 actions: Sequence[GuideAction] = (), *, parent: Optional[QWidget] = None,
                 image_id: Optional[str] = None):
        super().__init__(parent)
        self._title = str(title or "")
        self._actions: list[GuideAction] = list(actions or [])
        self._buttons: list[QPushButton] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 16, 12, 16)
        lay.setSpacing(6)
        lay.addStretch(1)
        pm = _guide_art_pixmap(image_id) if image_id else None    # B0 插画优先（R183-B2）
        if pm is None:
            pm = _guide_icon_pixmap(icon_key)                     # tabler 图标回退（照旧）
        if pm is not None:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(pm)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_lbl.setStyleSheet("background:transparent;")
            lay.addWidget(icon_lbl)
        if self._title:
            t = QLabel(self._title)
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setWordWrap(True)
            t.setStyleSheet(
                f"color:{_th.TEXT_SECONDARY}; font-size:12px; background:transparent;")
            lay.addWidget(t)
        if hint:
            h = QLabel(str(hint))
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setWordWrap(True)
            h.setStyleSheet(
                f"color:{_th.TEXT_DISABLED}; font-size:11px; background:transparent;")
            lay.addWidget(h)
        if self._actions:
            lay.addSpacing(2)
            lay.addLayout(self._build_action_row())
        lay.addStretch(2)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    def _build_action_row(self) -> QHBoxLayout:
        """入口按钮行（主/次按钮语义由 GuideAction 第 4 位决定）。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        for label, slot, icon_key, primary in self._actions:
            btn = _make_guide_button(label, slot, icon_key, bool(primary))
            self._buttons.append(btn)
            row.addWidget(btn)
        row.addStretch(1)
        return row

    # ── 查询/驱动（测试口径） ────────────────────────────────────────────────

    def title(self) -> str:
        """空态标题文案。"""
        return self._title

    def button_labels(self) -> tuple:
        """入口按钮文案（按加入顺序）。"""
        return tuple(b.text() for b in self._buttons)

    def click(self, index: int) -> bool:
        """按下第 index 个入口按钮（越界返回 False）。"""
        try:
            btn = self._buttons[int(index)]
        except (IndexError, ValueError, TypeError):
            return False
        btn.click()
        return True
