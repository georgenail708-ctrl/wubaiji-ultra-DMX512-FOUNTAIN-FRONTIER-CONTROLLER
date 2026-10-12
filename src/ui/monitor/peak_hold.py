# -*- coding: utf-8 -*-
"""A10 G20 峰值保持（R183 D 从 channel_grid.py 拆出，纯逻辑无 Qt 依赖）。

背景：混音 25ms/拍（40Hz）vs 监视器 50ms 轮询（20Hz）：短促峰值落在两次采样
之间被漏看。每通道 300ms 窗口内保持窗口内最大值，超时回落最新值（窗口 ≥1.5
个混音拍，肉眼可辨且不至拖成"粘滞"显示）。
"""
from __future__ import annotations

import time

import numpy as np

PEAK_HOLD_SEC = 0.300
PEAK_HOLD_CHANNELS = 512


class PeakHold:
    """逐通道峰值保持（纯逻辑；``feed`` 的 now 可注入 → offscreen 可测）。

    抬升 → 立即显示并重置该通道窗口；窗口内回落 → 保持峰值；
    超时 → 回落最新值并开新窗口（不永久粘住）。
    """

    def __init__(self, channels: int = PEAK_HOLD_CHANNELS):
        self.values = np.zeros(channels, dtype=np.uint8)
        self._since = np.full(channels, -np.inf, dtype=np.float64)

    def reset(self) -> None:
        """清空全部通道状态（切换监控域时调用）。"""
        self.values[:] = 0
        self._since.fill(-np.inf)

    def feed(self, arr, now: float | None = None) -> np.ndarray:
        """喂入一拍采样，返回合成后（峰值保持）的显示帧。"""
        arr = np.asarray(arr, dtype=np.uint8).reshape(-1)[: self.values.size]
        n = arr.size
        now = time.monotonic() if now is None else now
        in_window = (now - self._since[:n]) <= PEAK_HOLD_SEC
        base = np.where(in_window, self.values[:n], arr)
        peak = np.maximum(base, arr)
        # 抬升或超时回落 → 重置计时开新窗；窗口内持平/回落 → 计时不动
        self._since[:n] = np.where((peak > base) | ~in_window, now,
                                   self._since[:n])
        self.values[:n] = peak
        return peak
