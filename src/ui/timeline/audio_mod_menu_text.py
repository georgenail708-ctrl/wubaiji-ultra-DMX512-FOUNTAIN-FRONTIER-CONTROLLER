# -*- coding: utf-8 -*-
"""调制轨区段菜单的文案与图标（01 清单 B5）—— 从 audio_mod_lane 拆出（该文件近 500 行）。

两处修复：
  * **文案**：波形项与 Draw 大页同一套标签（``draw_cards.wave_label``：恒定/Sine/…/
    鲨齿/采样保持/游走），并转义 ``&``——修前 "S&H" 被 Qt 当助记符渲染成下划线 "SH"
    （用户截图 13-14-46 实锤）；
  * **图标**：区段项补 tabler 图标，破坏性动作走普通 clip 右击菜单同一真源
    ``clip_context_menu.tint_danger_action_icons``（红色），两类 clip 菜单心智一致。
"""
from __future__ import annotations

__all__ = ["wave_menu_text", "decorate_mod_menu"]

#: 菜单项文案前缀 → tabler 图标名（前缀匹配：条目文案带动态时间段）
_PREFIX_ICONS = (
    ("删除此区段", "trash"),
    ("清空全部区段", "trash-x"),
    ("拆分于此点", "scissors"),
    ("此区段淡入淡出", "adjustments-horizontal"),
    ("淡入淡出（全局包络）", "adjustments-horizontal"),
    ("波形", "wave-sine"),
)


def wave_menu_text(wave: str) -> str:
    """波形值 → 菜单文案（Draw 同源中文标签；``&`` 转义为 ``&&``）。"""
    try:
        from ..audio_mod_draw.draw_cards import wave_label
        text = wave_label(wave)
    except Exception:
        text = wave if wave else "恒定"
    return str(text).replace("&", "&&")


def _icon_for(text: str) -> str:
    for prefix, name in _PREFIX_ICONS:
        if str(text).startswith(prefix):
            return name
    return ""


def decorate_mod_menu(menu) -> None:
    """一级条目按文案前缀补 tabler 图标；破坏性项再统一红色着色（失败静默）。"""
    try:
        from ..main_icon_library import load_tabler_icon
        from .. import qascade_theme as _th
        for action in menu.actions():
            name = "" if action.isSeparator() else _icon_for(action.text())
            if name:
                action.setIcon(load_tabler_icon(name, 16, _th.TEXT_SECONDARY))
        from .clip_context_menu import tint_danger_action_icons
        tint_danger_action_icons(menu)
    except Exception:
        return
