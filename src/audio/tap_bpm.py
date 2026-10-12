"""
tap_bpm.py — Tap BPM 手动校准
记录用户点击节奏，计算BPM，置信度高时覆盖自动检测结果。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import time
import numpy as np


@dataclass
class TapBPMResult:
    bpm: float = 120.0
    confidence: float = 0.0
    tap_count: int = 0
    method: str = "tap"


class TapBPMManager:
    """Tap BPM管理器：记录点击间隔，去异常值后平均计算BPM。"""

    def __init__(self, max_taps: int = 8):
        self._max_taps = int(max_taps)
        self._tap_times: List[float] = []
        self._bpm: float = 120.0
        self._confidence: float = 0.0

    def tap(self) -> TapBPMResult:
        """记录一次点击。返回当前BPM估计。"""
        now = time.perf_counter()
        self._tap_times.append(now)
        # 保持最近N次
        if len(self._tap_times) > self._max_taps:
            self._tap_times = self._tap_times[-self._max_taps:]

        if len(self._tap_times) < 2:
            return TapBPMResult(bpm=120.0, confidence=0.0, tap_count=len(self._tap_times))

        # 计算点击间隔
        intervals = np.diff(self._tap_times)
        if len(intervals) < 1:
            return TapBPMResult(bpm=120.0, confidence=0.0, tap_count=len(self._tap_times))

        # 去异常值: 超过2倍标准差的间隔丢弃
        mean_int = float(np.mean(intervals))
        std_int = float(np.std(intervals))
        valid = intervals[(intervals > mean_int * 0.5) & (intervals < mean_int * 2.0)]

        if len(valid) < 1:
            valid = intervals[-2:]  # 至少用最近2个间隔

        avg_interval = float(np.mean(valid))
        if avg_interval < 0.05:
            return TapBPMResult(bpm=120.0, confidence=0.0, tap_count=len(self._tap_times))

        self._bpm = 60.0 / avg_interval
        # 折叠到合理范围 (40-300 BPM)
        while self._bpm > 300:
            self._bpm /= 2.0
        while self._bpm < 40:
            self._bpm *= 2.0

        # 置信度: interval一致性越高, confidence越高
        if len(valid) >= 3:
            cv = std_int / max(mean_int, 1e-6)  # 变异系数
            self._confidence = float(np.clip(1.0 - cv, 0.0, 1.0))
        else:
            self._confidence = 0.3

        return self.result()

    def reset(self):
        self._tap_times.clear()
        self._bpm = 120.0
        self._confidence = 0.0

    def result(self) -> TapBPMResult:
        return TapBPMResult(
            bpm=round(self._bpm, 1),
            confidence=round(self._confidence, 2),
            tap_count=len(self._tap_times),
            method="tap",
        )

    @property
    def is_confident(self) -> bool:
        return self._confidence > 0.9 and len(self._tap_times) >= 4
