# -*- coding: utf-8 -*-
"""音频效果参数契约（R182 重构）—— 引擎侧信号键常量（纯数据，无依赖）。

`audio_mod_draw_compile` 与 UI 侧 `draw_effects` 共同引用本模块的信号键集合
（UI 的展示目录在 ui 层，引擎只认键）。
"""
from __future__ import annotations

from typing import Tuple

#: 支持的信号源键（求值见 audio_mod_draw_compile.source_value）
SIGNAL_KEYS: Tuple[str, ...] = (
    "beat_strength", "bpm_phase", "band_low", "band_mid", "band_high",
    "band_ratio", "onset", "energy", "section",
)

#: 效果参数全键（编译产物 params 的规范字段）
PARAM_KEYS: Tuple[str, ...] = (
    "signal", "wave", "freq", "depth", "attack", "release", "phase",
    "invert", "threshold", "smoothing",
    # R183 M2 扩容（缺省值=旧档行为逐位不变）：
    "freq_unit",        # 频率时基：beat(默认)/hz/bars/dotted/triplet
    "random_mode",      # Random 波形模式：""(默认=旧连续)/discrete/slew
    "threshold_close",  # 迟滞门关阈值 0..1（默认 0=未设→旧单阈值口径）
    "hold_ms",          # 迟滞门最短保持 ms（默认 0=关）
    # R183 3.4b：多灯相位展开度数 0..360（默认 0=同相→引擎短路旧路径逐位兼容；
    # 卡片级参数，compile_graph 聚合 max 为状态级 state["spread_deg"]）
    "spread_deg",
)

#: 频率时基全集（R183 M2；换算系数见 audio_mod_draw_compile._beats_per_cycle，
#: 命名对照 Modulators 手册 Timebase：Hertz/Bars/beat-time/附点记法）
FREQ_UNITS: Tuple[str, ...] = ("beat", "hz", "bars", "dotted", "triplet",
                               "sixteenth")

#: Random 波形模式全集（R183 M2；对照 Random "discrete or slewed"）
RANDOM_MODES: Tuple[str, ...] = ("", "discrete", "slew")
