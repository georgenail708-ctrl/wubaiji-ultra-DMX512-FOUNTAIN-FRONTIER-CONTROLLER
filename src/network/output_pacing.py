"""多域发送短批节奏；复用原router线程，不改生产帧率或输出值。

FPP UDPOutput@891455ae uses absolute send budgets (GPL, semantics only).
Windows uses Python's existing monotonic clock/high-resolution sleep instead
of Linux fq/sendmmsg. Four datagrams form a short burst; all added waits in a
batch fit an 8 ms window. Receiver delivery remains a separate measurement.
"""
from __future__ import annotations

import time


class BatchPacer:
    """绝对截止时间避免每包延时累积；小批次保持直接发送。

    Construct outside queue/mixer locks. ``wait`` returns immediately when
    actual I/O already consumed the time budget; it never catches up by
    repeatedly sleeping and never owns or alters any DMX frame.
    """

    def __init__(self, count: int, *, clock=None, sleep=None):
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        groups = max(0, (int(count) - 1) // 4)
        self._step = min(.0005, .008 / groups) if count >= 8 and groups else 0.0
        self._start = self._clock()

    def wait(self, index: int) -> None:
        """每四包前让接收器排空，最长计划等待8ms，不锁混音/队列。"""
        if self._step == 0 or index == 0 or index % 4:
            return
        delay = self._start + (index // 4) * self._step - self._clock()
        if delay > 0:
            self._sleep(delay)
