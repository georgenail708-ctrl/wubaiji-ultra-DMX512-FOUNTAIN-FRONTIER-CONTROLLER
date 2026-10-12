# -*- coding: utf-8 -*-
"""音频效果库蜂巢网格（R182）—— HoneycombGrid 薄包装（组色带语义）。

用户规格（2026-09-29）：效果选择区要像调度台存储区一样"一片蜂巢"式展示
（紧凑瓦片网格），不再纵向长列表。布局/交互/绘制纪律全部在共享控件
`honeycomb_grid.HoneycombGrid`（本模块只补"分组→色带"映射）。
"""
from __future__ import annotations

from typing import Dict, List

from .draw_effects import GROUP_ICONS, effect_art  # noqa: F401  (re-export 供旧引用)
from .honeycomb_grid import PAD, TILE_GAP, TILE_H, TILE_W, HoneycombGrid
from ..qascade_theme import (ACTIVE_ORANGE, BG_PANEL,
                             BG_PANEL_SUB, BORDER_NORMAL, BRIGHT_ERROR, BRIGHT_INFO,
                             BRIGHT_SUCCESS, BRIGHT_WARN, FOCUS_BLUE, SELECT_BLUE,
                             TEXT_DISABLED, TEXT_PRIMARY, TEXT_SECONDARY,
                             BG_PANEL_SUB)

__all__ = ["EffectGrid", "TILE_W", "TILE_H", "TILE_GAP", "PAD"]

#: 分组色带色（走主题令牌；未知组回退焦点蓝）
_GROUP_COLORS: Dict[str, str] = {
    "律动": BRIGHT_WARN, "呼吸": BRIGHT_INFO, "波形": FOCUS_BLUE,
    "频谱": BRIGHT_SUCCESS, "冲击": BRIGHT_ERROR,
    "段落": ACTIVE_ORANGE, "动态": SELECT_BLUE,
}


class EffectGrid(HoneycombGrid):
    """效果库瓦片网格：set_effects([(key,name,group,icon)]) 自动映射色带。

    R183-H：第 6 元组元素 = `draw_effects.effect_art(key)`（B0 效果卡面 id，
    `effects/eff_*` 族）显式传入网格 art 通道；5 元组旧调用零改动（缺省时
    `HoneycombGrid._resolve_art_id` 仍按 `eff_<key>` 约定解析，结果一致）。
    """

    def set_effects(self, items) -> None:
        rows = [(str(k), str(n), str(g), str(i),
                 _GROUP_COLORS.get(str(g), FOCUS_BLUE), str(effect_art(k)))
                for k, n, g, i in (items or [])]
        self.set_items(rows)

    def effect_keys(self) -> List[str]:
        return self.item_keys()
