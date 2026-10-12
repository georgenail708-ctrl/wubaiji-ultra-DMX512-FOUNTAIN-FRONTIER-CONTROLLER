# -*- coding: utf-8 -*-
"""监视器录制/回放（R183 W2；深查② §四 #14）。

需求原文：「录制/回放：环形 512×600 帧缓冲（~60MB 内）记录监视器流 30s +
时间轴回放滑条（回放时暂停实时刷新）」。

实测占用：512 B/帧 × 600 帧 = **0.3 MB**（远低于报告给的 60MB 上限估算，
故容量可放心取满 30s@20Hz）。纯 numpy 环形缓冲，无 Qt 依赖。
"""
from __future__ import annotations

import numpy as np

REC_FRAMES = 600          # 30s @ 20Hz 刷新率
REC_CHANNELS = 512


class FrameRecorder:
    """固定容量环形帧缓冲：push 覆写最旧帧，index 0 = 当前保留的最旧帧。"""

    def __init__(self, frames: int = REC_FRAMES, channels: int = REC_CHANNELS):
        self._cap = max(1, int(frames))
        self._buf = np.zeros((self._cap, max(1, int(channels))), dtype=np.uint8)
        self._head = 0        # 下一写入位置
        self._count = 0       # 已存帧数（≤ cap）

    # ── 写入 ─────────────────────────────────────

    def push(self, frame) -> None:
        """写入一帧（长度不足补零、超长截断；非法输入丢弃）。"""
        try:
            arr = np.asarray(frame, dtype=np.uint8).reshape(-1)
        except (TypeError, ValueError):
            return
        n = min(arr.size, self._buf.shape[1])
        if n <= 0:
            return
        self._buf[self._head, :n] = arr[:n]
        self._buf[self._head, n:] = 0
        self._head = (self._head + 1) % self._cap
        self._count = min(self._cap, self._count + 1)

    def clear(self) -> None:
        self._head = 0
        self._count = 0

    # ── 读取 ─────────────────────────────────────

    @property
    def count(self) -> int:
        return self._count

    @property
    def capacity(self) -> int:
        return self._cap

    @property
    def is_full(self) -> bool:
        return self._count >= self._cap

    def duration_seconds(self, fps: float = 20.0) -> float:
        return self._count / max(1e-9, float(fps))

    def frame_at(self, index: int) -> np.ndarray:
        """按回放下标取帧（0=最旧；越界钳制）；空缓冲返回零帧。"""
        if self._count <= 0:
            return np.zeros(self._buf.shape[1], dtype=np.uint8)
        i = max(0, min(self._count - 1, int(index)))
        pos = (self._head - self._count + i) % self._cap
        return self._buf[pos].copy()

    def latest(self) -> np.ndarray:
        return self.frame_at(self._count - 1)

    def slider_max(self) -> int:
        """回放滑条上界（无帧时 0：滑条禁用）。"""
        return max(0, self._count - 1)

    def index_for_position(self, position: int) -> int:
        """滑条位置 → 帧下标（越界钳制，空缓冲返回 0）。"""
        return max(0, min(self.slider_max(), int(position)))
