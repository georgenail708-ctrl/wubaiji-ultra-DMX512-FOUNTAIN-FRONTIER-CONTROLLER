# -*- coding: utf-8 -*-
"""custom_effect_icon_map.py — 自定义效果列表 icon 字段 → tabler 图标名的映射。

R173 任务C：Effect Browser 自定义效果列表历史上把 CustomEffect.icon（"★"/"🎬"/emoji）
直接拼进 QListWidgetItem 文本显示；改为 setIcon(tabler) 后需要一个纯函数把旧存档里的
icon 字符值 + 效果语义翻译成 tabler 图标名。独立成模块，避免继续膨胀 effect_icon_library.py
（已逼近 500 行文件上限）与 effect_browser.py（冻结上帝类，禁止新增独立职责）。

持久化边界：本模块只服务显示层，绝不回写 CustomEffect.icon；
CustomEffect.to_dict()/from_dict() 的字节格式不受影响。
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtWidgets import QListWidgetItem

from .effect_icon_library import _GENERAL_ICONS, _icon_exists, effect_icon_name_for_preset
from .main_icon_library import load_tabler_icon

_LEGACY_CUSTOM_ICON_MAP = {
    "★": "star",
    "🎬": "movie",
}


def custom_effect_icon_name(eff: Any) -> str:
    """把 CustomEffect（含"场景快照"）映射到 tabler 图标名，供列表 setIcon 使用。

    优先级：
      1. 场景快照(snapshot_states 非空) → "movie"（旧值恒为 "🎬"，语义延续）。
      2. 普通自定义效果 → 先走 effect_icon_name_for_preset 同款语义规则按
         name/description/tags 等字段猜测（用户自己起的名字里含"追逐/波浪/..."
         等关键词时命中具体图标）。
      3. 语义规则兜底到通用池时，若 eff.icon 命中已知旧值映射表，改用旧值
         对应的更具体图标（"★"→star）；否则维持语义规则结果。
    """
    if bool(getattr(eff, "snapshot_states", None)):
        return "movie"
    guessed = effect_icon_name_for_preset(eff)
    if guessed in _GENERAL_ICONS:  # 语义规则落入通用兜底池 → 尝试旧值兜底
        legacy = _LEGACY_CUSTOM_ICON_MAP.get(str(getattr(eff, "icon", "") or ""))
        if legacy and _icon_exists(legacy):
            return legacy
    return guessed


def build_custom_effect_item(eff: Any, text: str, icon_color: str) -> QListWidgetItem:
    """构造自定义效果列表项：文本不再拼接 icon 字符，改为 setIcon(tabler)。

    调用方（EffectBrowserWidget._refresh_custom_effect_page，冻结上帝类）只需
    一次调用即可拿到已挂好图标的 item，避免在该函数内堆叠新行数。
    """
    it = QListWidgetItem(text)
    it.setIcon(load_tabler_icon(custom_effect_icon_name(eff), 16, icon_color))
    return it
