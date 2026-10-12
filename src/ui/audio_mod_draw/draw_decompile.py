# -*- coding: utf-8 -*-
"""调制状态 → Draw 画布图反编译（R182）—— 纯函数，UI 唯一入口。

实现是引擎侧纯逻辑 `engine.audio_mod_draw_compile.decompile_state`（engine
不得反向 import src.ui，故实现在引擎）。加载/打开 Draw 页时调用：
  · 有 `draw_graph` 编辑态快照 → 原样恢复（布局不漂移）；
  · 否则按 `sources` / `rules` 现场生成稳定排布（旧档 → 空图）。
"""
from __future__ import annotations

from typing import Optional

from ...engine.audio_mod_draw_compile import decompile_state

__all__ = ["decompile_state", "graph_from_state"]

#: 官方入口别名（语义更直白；面板/测试共用）
graph_from_state = decompile_state


def empty_draw_graph() -> dict:
    """空图占位（每次新对象，画布会原地改 nodes/edges）。"""
    return {"nodes": [], "edges": []}
