# -*- coding: utf-8 -*-
"""网络页统一按钮工厂（R182 前端重做）。

全页（配置页动作行 / 诊断页工具行 / ArtPoll 面板 / 信号测试器）按钮统一到
**同一规格**，消除此前三套并行样式（network_sections._btn_style、
network_diagnostic_widget 内联 QSS、signal_tester_widget._btn）：

  - 高度 22px（= ``MIN_CONTROL_HEIGHT`` 令牌，与全项目控件行同高）
  - 字号 11px（= ``FS_SMALL`` 令牌，全项目次级说明档）
  - 图标 14px（tabler 矢量，语义色）
  - 颜色一律走 qascade_theme 令牌，禁止裸 hex

四种 role（语义由调用方按动作危险度选择）：
  - ``primary``：品牌蓝实底白字（应用配置）
  - ``success``：灰底绿字（启动）
  - ``danger``：灰底红字（停止）
  - ``neutral``：灰底信息蓝字（扫描 / 诊断 / 刷新 / 防火墙）

样式字符串由 :func:`button_style` 纯函数生成（可离线测试；断言"无裸 hex"
与"统一高度/字号"两条规格）。
"""
from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QSizePolicy

from ..main_icon_library import load_tabler_icon
from ..qascade_theme import (
    BG_BUTTON, BG_BUTTON_HOVER, BORDER_NORMAL, ERROR_RED as BRIGHT_ERROR,
    INFO_BLUE as BRIGHT_INFO, STATUS_SUCCESS_INK as BRIGHT_SUCCESS,
    FS_SMALL, MIN_CONTROL_HEIGHT, RADIUS, BRAND_ORANGE as SELECT_BLUE,
    BG_BUTTON_HOVER as SELECT_BLUE_HOVER, BRAND_ORANGE_SELECTED as SELECT_BLUE_PRESSED,
    TEXT_DISABLED, TEXT_PRIMARY as TEXT_SELECTED_FG,
)

#: 统一规格常数（测试与调用方共用；改这里即全页生效）
BUTTON_HEIGHT = MIN_CONTROL_HEIGHT      # 22
BUTTON_FONT_SIZE = FS_SMALL             # 11
BUTTON_ICON_SIZE = 14                   # tabler 14px

#: 语义 role -> （前景色，是否 primary 实底）
ROLE_COLORS = {
    "primary": SELECT_BLUE,
    "success": BRIGHT_SUCCESS,
    "danger": BRIGHT_ERROR,
    "neutral": BRIGHT_INFO,
    "muted": TEXT_DISABLED,
}

#: 命中 3/6 位 hex 字面量（样式里出现即视为绕过令牌体系）。
_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


def button_style(role: str = "neutral", *, checked: bool = False) -> str:
    """生成统一按钮四态 QSS（**无裸 hex**：全部取自 qascade_theme 令牌）。

    ``checked`` 为 True 时补 :checked 态（可勾选按钮：自动刷新 / 扫描中）。
    """
    fg = ROLE_COLORS.get(role, BRIGHT_INFO)
    if role == "primary":
        bg, hover, border, text = SELECT_BLUE, SELECT_BLUE_HOVER, SELECT_BLUE, TEXT_SELECTED_FG
    else:
        bg, hover, border, text = BG_BUTTON, BG_BUTTON_HOVER, BORDER_NORMAL, fg
    qss = (
        f"QPushButton {{ background:{bg}; color:{text}; border:1px solid {border};"
        f" border-radius:{RADIUS}px; min-height:{BUTTON_HEIGHT}px;"
        f" max-height:{BUTTON_HEIGHT}px; padding:0 12px;"
        f" font-size:{BUTTON_FONT_SIZE}px; }}"
        f"QPushButton:hover {{ background:{hover}; }}"
        f"QPushButton:pressed {{ background:{SELECT_BLUE_PRESSED};"
        f" color:{TEXT_SELECTED_FG}; }}"
        f"QPushButton:disabled {{ color:{TEXT_DISABLED}; }}"
    )
    if checked:
        qss += (
            f"QPushButton:checked {{ background:{SELECT_BLUE};"
            f" color:{TEXT_SELECTED_FG}; border-color:{SELECT_BLUE}; }}"
        )
    return qss


def make_button(text: str, icon_name: str = "", role: str = "neutral", *,
                tooltip: str = "", checkable: bool = False,
                icon_color: str | None = None, parent=None) -> QPushButton:
    """网络页标准按钮（统一规格；图标缺失时静默无图标，业务不中断）。

    高度由 QSS 的 ``min-height``/``max-height``（均取 ``MIN_CONTROL_HEIGHT``
    令牌）约束、并把垂直尺寸策略钉成 Fixed——**不用 setFixedHeight**：QSS 的
    min-height 作用于内容盒，叠加 1px 上下边框后实际绘制高为令牌 +2；若同时
    setFixedHeight 二者会互相打架（Qt 以 QSS 为准）。这与全项目 GLOBAL_QSS
    的按钮高度约定一致（同一令牌、同一 "+边框" 结果）。
    """
    btn = QPushButton(text, parent)
    if icon_name:
        color = icon_color or (TEXT_SELECTED_FG if role == "primary"
                               else ROLE_COLORS.get(role, BRIGHT_INFO))
        try:
            btn.setIcon(load_tabler_icon(icon_name, BUTTON_ICON_SIZE, color))
        except Exception:       # 图标库异常不得阻断按钮功能
            pass
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCheckable(checkable)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    btn.setStyleSheet(button_style(role, checked=checkable))
    return btn


def has_bare_hex(source_text: str) -> bool:
    """源码文本是否含**裸 hex 色值**（R182 前端规格门禁用）。

    注意作用对象是**源码**而非生成后的 QSS：令牌经 f-string 展开后本就是
    hex，检查生成串必然命中。门禁要求的是"不写字面量、一律引 qascade_theme"，
    故本函数只对 `.py` 文件正文断言。
    """
    return bool(_HEX_RE.search(source_text or ""))
