# -*- coding: utf-8 -*-
"""Grand Master（总控推杆）**末级增益** —— 语义移植（纯 numpy，无 Qt）。

语义：
    ``applyGM(channel, value)``：
        · `ValueMode == Limit`  → ``value = MIN(value, gm)``
        · `ValueMode == Reduce` → ``value = floor(value * gm_fraction + 0.5)``
    默认 `Reduce`（乘性压暗，本仓采用）+ 默认 `ChannelMode == Intensity`
    （只压亮度通道）。本仓先落 **AllChannels**（推杆语义 = "全场总闸"，
    不受通道类型判定错误影响）——`Intensity` 模式需要 per-fixture 通道分组
    掩码，属可选精化项，故本模块只实现乘性 Reduce，且提供 `gain_from_percent`。

为什么独立成模块：`network_manager.py` 是 4000+ 行的既有大文件且已有基线债务，
把纯函数放这里、NM 侧只留"一个属性 + 一处调用"（末级 frame 处理函数），
避免继续在混音热路径里堆逻辑。

应用点（"最小侵入点"结论）：`NetworkManager._prepare_batch_frame`（批量混音器
的唯一末级处理）与 `_send_to_hw`（单帧/直发路径的末级处理）——两者正是
"一次性写完要发出去的帧"的最后一站，与 在 `Universe::writeValue` 最后
施加 GM 的位置同构；且**在混音与快照之后**，故监控区/取值路径看到的是
"编程值"，总控只影响真正下发到硬件的字节。
"""
from __future__ import annotations

from typing import Any

#: 默认值 = 100%（1.0 = 零开销直通，不触碰数据）
DEFAULT_GM = 1.0
#: 推杆上限（允许 boost？不 —— GM 是"压"的总闸；>1 会把值推到 255 之上无意义）
GM_MAX = 1.0
GM_MIN = 0.0


def gain_from_percent(percent: Any, fallback: float = 100.0) -> float:
    """滑块百分比（0..100）→ 增益 0..1；非法 → `fallback`（默认 100%）。"""
    try:
        p = float(percent)
    except (TypeError, ValueError):
        p = float(fallback)
    if p != p:                      # NaN
        p = float(fallback)
    return max(GM_MIN, min(GM_MAX, p / 100.0))


def percent_from_gain(gain: Any) -> float:
    """增益 0..1 → 百分比 0..100（滑块回填用）。"""
    try:
        g = float(gain)
    except (TypeError, ValueError):
        return 100.0
    if g != g:
        return 100.0
    return max(0.0, min(100.0, g * 100.0))


def apply_grand_master(data, gain) -> Any:
    """把总控增益乘到一帧 DMX（uint8/float 皆可）；`gain>=1.0` 零开销直通。

    逐元素与 逐字节一致：``floor(value * gain + 0.5)``（四舍五入到整数），
    再夹到 0..255 并落回 uint8。`gain` 非法（None/NaN/负）→ 直通原帧
    （宁可不动，也绝不把输出算坏）。
    """
    try:
        g = float(gain)
    except (TypeError, ValueError):
        return data
    if g != g or g < 0.0:
        return data
    if g >= 1.0:
        return data
    try:
        import numpy as np
        arr = np.asarray(data, dtype=np.float32) * g
        return np.clip(np.floor(arr + 0.5), 0.0, 255.0).astype(np.uint8)
    except Exception:
        # numpy 缺席/异常 → 逐元素回退（512 通道纯 Python，只在这条兜底路径上付代价）
        try:
            return bytes(max(0, min(255, int(v * g + 0.5))) for v in data)
        except Exception:
            return data


__all__ = ["DEFAULT_GM", "GM_MAX", "GM_MIN", "gain_from_percent",
           "percent_from_gain", "apply_grand_master"]
