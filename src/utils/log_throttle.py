# -*- coding: utf-8 -*-
"""log_throttle.py — 同一日志点的内容感知限频（R181 审计 A P2「[PAR] 日志洪水」）。

问题（真实工程「水仙十字」实测）：渲染热路径里的诊断 warning 每帧用**同一内容**
重发（`_parallel_wing_fit` 的 [PAR] / `_clamp_window_to_duration` 的 [SEQ]），
水仙工程 2 分钟刷出约 100KB 日志 —— 有价值的那一条被上千条重复淹没，写盘也白耗。

语义（审计要求：值不变不记 / 每 interval 秒最多一条 / 变化才记）：

  * **内容签名变化 → 立即记录**（新问题第一时间可见，诊断价值不丢）；
  * **签名相同 → 每 interval 秒最多一条**（稳态重复只留心跳，不再逐帧刷）；
  * 被抑制的重复条数在下一次真正落笔时附在末尾（"另有 N 条相同内容已抑制"），
    保证"刷了多少次"这一信息不丢。

调用方（热路径）纪律：先算好轻量签名再调用；本模块不分配大对象、无锁竞争
（GIL 下的 dict 读改写足够；限频本就非严格语义，极端并发最坏多记一条）。
"""
from __future__ import annotations

import time
from typing import Hashable, Optional, Tuple

#: 默认限频窗口（秒）——审计口径「每 5s 最多一条」。
DEFAULT_INTERVAL = 5.0
#: 状态表容量上限（防止历史 clip 键无限增长，FIFO 逐出最旧一条）。
DEFAULT_CAP = 256


class LogThrottle:
    """按 key 记录"上次签名/时间/抑制计数"的限频器（内容变化即放行）。"""

    __slots__ = ("_interval", "_cap", "_state")

    def __init__(self, interval: float = DEFAULT_INTERVAL, cap: int = DEFAULT_CAP):
        self._interval = max(0.0, float(interval))
        self._cap = max(1, int(cap))
        #: key → (签名, 上次落笔时刻, 已抑制条数)
        self._state: dict = {}

    def allow(self, key: Hashable, signature: tuple = (), *,
              now: Optional[float] = None) -> Tuple[bool, int]:
        """判定本次是否落笔。返回 `(是否记录, 本次实际抑制的重复条数)`。

        签名变化 → `(True, 0)`；同签名且距上次落笔 < interval →
        `(False, 1)`；同签名且超窗 → `(True, 窗口内被抑制的条数)`。
        """
        if now is None:
            now = time.monotonic()
        sig = tuple(signature)
        prev = self._state.get(key)
        if prev is None:
            self._put(key, (sig, now, 0))
            return True, 0
        last_sig, last_t, suppressed = prev
        if last_sig != sig:
            self._put(key, (sig, now, 0))
            return True, 0
        if now - float(last_t) < self._interval:
            self._put(key, (last_sig, last_t, int(suppressed) + 1))
            return False, 1
        self._put(key, (last_sig, now, 0))
        return True, int(suppressed)

    def clear(self) -> None:
        """清空全部限频状态（换工程 / 全量失效时调用，让首条重新可见）。"""
        self._state.clear()

    def _put(self, key: Hashable, value) -> None:
        if key not in self._state and len(self._state) >= self._cap:
            oldest = next(iter(self._state), None)
            if oldest is not None:
                del self._state[oldest]
        self._state[key] = value


def format_suppressed(suppressed: int) -> str:
    """抑制计数的日志尾巴（0 条 → 空串，不污染既有日志文本）。"""
    return f"（相同内容另有 {int(suppressed)} 条已抑制）" if suppressed else ""
