# -*- coding: utf-8 -*-
"""Draw 画布图 → 调制状态编译（R182）—— 纯函数，UI/driver 同源。

实现是引擎侧纯逻辑 `engine.audio_mod_draw_compile.compile_graph`（放引擎是
硬约束：engine 不得反向 import src.ui）。本模块提供 UI 侧唯一入口，并负责
把 `scope_track_id`（不在 normalize schema 内）回带进 payload —— 否则一次
「应用」就把调制轨关联擦掉（编辑页/共享轨同款守卫）。

产物字段（Project.audio_modulation_state 扩展）：
    sources / rules / target_ch_types / draw_graph
往返幂等：`draw_decompile.graph_from_state` 再 `compile_graph` 逐字段相等。
"""
from __future__ import annotations

from typing import Optional
from copy import deepcopy

from ...engine.audio_mod_draw_compile import compile_graph, decompile_state

__all__ = ["compile_graph", "compile_payload", "decompile_state"]


def compile_payload(graph, base_state: Optional[dict] = None) -> dict:
    """画布图 → 提交 payload（= compile_graph + scope_track_id 回带）。

    `base_state` = 当前工程调制状态（clips/amount/attack/release 等旧字段
    原样保留；sources/rules/target_ch_types/draw_graph 被覆写）。
    """
    st = base_state if isinstance(base_state, dict) else {}
    payload = compile_graph(graph, st)
    if "processors" in st or "processor_mode" in st:
        from ...engine.audio_mod_processor_schema import normalize_processors
        payload["processors"] = deepcopy(normalize_processors(st.get("processors", [])))
        payload["processor_mode"] = "chain" if st.get("processor_mode") == "chain" else "legacy"
    tid = str(st.get("scope_track_id", "") or "")
    if tid:
        payload["scope_track_id"] = tid
    return payload
