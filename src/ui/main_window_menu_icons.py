# -*- coding: utf-8 -*-
"""主窗「视图/面板」菜单页面项图标统一（R180 #20，上帝类冻结 → 独立模块）。

现状缺口（离屏截图证实）：
  · `main_window_shortcuts` 往「视图(&V)」尾部挂的 9 个「跳转到 <英文 attr>」
    动作**无图标且显示英文属性名**（"跳转到 effect_browser"）——用户点名的
    "页面切换项无图标"即此；
  · 「面板(&P)」的「命令行代码编曲」icon_key=command_arrange 不在
    `_PANEL_ICON_NAMES` → `_panel_icon` 落到 panel_default（layout-sidebar），
    与"外接摇杆设备"的占位图标撞车（图标不一致）；
  · 「Clip 附加处理」与「效果预设库」共用 wand 图标；
  · 编辑菜单「全局查找」(search) / 帮助菜单「键盘快捷键」(keyboard) 同样
    落 panel_default 兜底。

做法：菜单构建完成后做一次**幂等后处理**——按动作文本定位，补 tabler 图标、
把英文 attr 跳转名翻成与 dock/工具栏一致的中文面板名；并挂 `aboutToShow`
重入，覆盖 shortcuts 模块 singleShot(0) 延迟安装等任何后到动作。
新图标键（clip_studio/cards 等）走 `load_tabler_icon` 直载——**不进**
`make_main_icon` 回退链（未收录 key 最终落 emoji，触全仓禁 emoji 红线）。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMenu

from .main_icon_library import DEFAULT_ICON_COLOR, load_tabler_icon, make_main_icon

log = logging.getLogger(__name__)

_MENU_ICON_SIZE = 16   # QMenu smallPixmap 实际渲染尺寸（与工具栏钮一致）

#: 未收录进 _TABLER_ICON_MAP 的新逻辑键 → tabler 字形名（全部已核实存在于
#: src/ui/icon_assets/tabler/package/icons/outline/）。
_EXTRA_TABLER: Dict[str, str] = {
    "clip_studio":      "cards",       # Clip 附加处理 = 卡片画布 Studio
    "cycle_panels":     "repeat",      # 循环切换面板
    "close_panel":      "square-x",    # 关闭当前面板
    "keyboard":         "keyboard",    # 键盘快捷键对话框
    "workspace_layout": "layout-2",    # 工作区布局子菜单
}

#: 「跳转到 X」动作：dock 属性名（去 _dock）→ (中文面板名, 图标键)。
#: 键序即 install_shortcuts 的 Ctrl+1..9 顺序（main_window_shortcuts._JUMP_DOCKS）。
_JUMP_TARGETS: Dict[str, Tuple[str, str]] = {
    "programmer":     ("编程器",     "programmer"),
    "topview":        ("TopView",    "topview"),
    "effect_browser": ("效果预设库",  "effects"),
    "dispatch":       ("调度台",     "dispatch"),
    "fixture_group":  ("设备组",     "fixture_group"),
    "patch":          ("配接",       "patch"),
    "matrix_pattern": ("矩阵幻灯片",  "matrix"),
    "clip_studio":    ("附加处理",   "clip_studio"),
    "network":        ("网络",       "network"),
}
#: 幂等匹配：中文名 → (attr, 图标键) 反查
_JUMP_BY_CN: Dict[str, Tuple[str, str]] = {
    cn: (attr, key) for attr, (cn, key) in _JUMP_TARGETS.items()
}

#: 「面板(&P)」动作文本前缀 → 图标键（对齐 _build_menu 的 icon_key 意图；
#: command_arrange/clip_studio 修掉 panel_default 兜底与 wand 撞车）。
_PANEL_MENU_ICONS: Tuple[Tuple[str, str], ...] = (
    ("节目管理",     "programs"),
    ("Patch 配接",   "patch"),
    ("TopView 视图", "topview"),
    ("跑动编排",     "route_order"),
    ("Programmer",   "programmer"),
    ("矩阵幻灯片",   "matrix"),
    ("设备组",       "fixture_group"),
    ("效果预设库",   "effects"),
    ("Clip 附加处理", "clip_studio"),
    ("调度台",       "dispatch"),
    ("命令行代码编曲", "command_arrange"),
    ("外接摇杆设备",  "joystick_device"),
    ("Monitor 监视器", "monitor"),
    ("Linker 关联器",  "processor"),
    ("Network 网络配置", "network"),
)

#: 其它菜单的散项（文本前缀 → 图标键），按所在菜单标题分发。
_MISC_MENU_ICONS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "编辑(&E)": (("全局查找", "search"),),
    "帮助(&H)": (("键盘快捷键", "keyboard"),),
    "视图(&V)": (("工作区布局", "workspace_layout"),
                 ("循环切换面板", "cycle_panels"),
                 ("关闭当前面板", "close_panel")),
}


def _menu_icon(key: str, size: int = _MENU_ICON_SIZE) -> QIcon:
    """逻辑键 → QIcon：_EXTRA_TABLER 直载 tabler；其余走 make_main_icon
    （其内部已是 tabler 优先的回退链）。仅喂本模块表内 key，杜绝 emoji 兜底。"""
    tabler = _EXTRA_TABLER.get(key)
    if tabler is not None:
        return load_tabler_icon(tabler, size, DEFAULT_ICON_COLOR)
    return make_main_icon(key, size)


def _apply_jump_item(action) -> bool:
    """「跳转到 X」动作：英文 attr 名 → 中文面板名 + 图标；幂等。"""
    text = action.text()
    if not text.startswith("跳转到 "):
        return False
    suffix = text[len("跳转到 "):].strip()
    entry = _JUMP_TARGETS.get(suffix)
    if entry is None:
        hit = _JUMP_BY_CN.get(suffix)
        if hit is None:
            return False
        key = hit[1]  # 已中文化：只补图标
    else:
        cn, key = entry
        action.setText(f"跳转到 {cn}")
    action.setIcon(_menu_icon(key))
    return True


def _apply_table(menu: QMenu, table) -> int:
    """按文本前缀表给 menu 动作补图标；返回处理数（幂等，重复调用安全）。"""
    n = 0
    for action in menu.actions():
        text = action.text()
        if not text:
            continue
        for prefix, key in table:
            if text.startswith(prefix):
                action.setIcon(_menu_icon(key))
                n += 1
                break
    return n


def _apply_menu(menu: QMenu) -> int:
    """按菜单标题分发到对应处理表（视图菜单先跑跳转项再跑散项）。"""
    n = 0
    if menu.title() == "视图(&V)":
        for action in menu.actions():
            if _apply_jump_item(action):
                n += 1
        n += _apply_table(menu, _MISC_MENU_ICONS["视图(&V)"])
    elif menu.title() == "面板(&P)":
        n += _apply_table(menu, _PANEL_MENU_ICONS)
    else:
        table = _MISC_MENU_ICONS.get(menu.title())
        if table:
            n += _apply_table(menu, table)
    return n


def _sync_studio_icon(win) -> None:
    """Clip 附加处理三处同图标：面板菜单项 / 面板工具栏钮 / dock 切换动作。"""
    icon = _menu_icon("clip_studio")
    tb = getattr(win, "_panel_toolbar", None)
    btn = getattr(tb, "_studio_btn", None) if tb is not None else None
    if btn is not None:
        btn.setIcon(icon)
    dock = getattr(win, "_clip_studio_dock", None)
    if dock is not None:
        try:
            dock.toggleViewAction().setIcon(icon)
        except Exception:
            log.debug("studio dock toggleViewAction 图标设置失败", exc_info=True)


def apply_main_menu_icons(win) -> int:
    """主窗菜单图标统一入口（幂等）。

    菜单未建好（本模块接线点在 _apply_global_style，早于 _build_menu）时
    singleShot(0) 下拍重试 ≤50 次——与 main_window_shortcuts.install_shortcuts
    同款延迟模式，且排在它的重试之后 → 跳转动作必然已挂上。
    另给已处理的菜单挂 aboutToShow 重入，覆盖任何后到动作。
    """
    mb = win.menuBar()
    menus = mb.findChildren(QMenu) if mb is not None else []
    if not menus:
        retries = getattr(win, "_menu_icon_retries", 0) + 1
        win._menu_icon_retries = retries
        if retries <= 50:
            QTimer.singleShot(0, lambda: apply_main_menu_icons(win))
        return 0
    touched = 0
    for menu in menus:
        touched += _apply_menu(menu)
        if not getattr(menu, "_r180_icons_hooked", False):
            menu._r180_icons_hooked = True
            menu.aboutToShow.connect(lambda m=menu: _apply_menu(m))
    _sync_studio_icon(win)
    return touched
