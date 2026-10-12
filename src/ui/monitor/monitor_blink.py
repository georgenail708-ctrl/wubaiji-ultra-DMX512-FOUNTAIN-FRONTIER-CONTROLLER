# -*- coding: utf-8 -*-
"""选中通道 blink / locate（R183 W2；深查② §四 #10）。

需求原文：「locate/blink 选中通道：跨面板写通道先例（`_send_to_track`）——
选中集 blink：Programmer 路径写 255/0 方波 2s（走既有 NM 写口与 layer
语义；只做 2s 后自动停）」。

落层选择（**与任务书措辞的差异，已核实后定案**）：
  写 ``tester`` 层而非 ``programmer`` 层。依据 = 仓内同类先例
  ``src/network/signal_tester.py:100-135``（通道测试）：一次性点亮必须
  ①显式传 ``active`` 掩码，②用独立层，否则单通道写入会把整域其余通道
  显式归零（R141 实测事故，见该文件注释）。且 programmer 层由 Programmer
  面板持有 LTP 权威值——2s 后"自动停"若走 clear 会抹掉用户的编程值；
  tester 层在 BASE_LAYERS 中靠后，blink 期间压过 programmer，停止后
  底层值自然回归，不破坏用户数据。

纯定时逻辑（时钟可注入）→ offscreen/无 Qt 亦可直测。
"""
from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger(__name__)

BLINK_LAYER = "tester"
BLINK_SEC = 2.0              # 总时长（任务书：2s 后自动停）
BLINK_HALF_PERIOD = 0.25     # 半周期：255/0 方波每 250ms 翻转
BLINK_VALUE = 255            # 亮相位值（locate 满输出）


class BlinkController:
    """选中通道方波 blink；``tick`` 由宿主定时器驱动，到期自动停并释放。"""

    def __init__(self, nm, universe: int, channels, layer: str = BLINK_LAYER,
                 *, duration: float = BLINK_SEC,
                 half_period: float = BLINK_HALF_PERIOD, clock=None):
        self._nm = nm
        self._universe = int(universe)
        self._channels = sorted({int(c) for c in (channels or ())
                                 if 1 <= int(c) <= 512})
        self._layer = str(layer)
        self._duration = float(duration)
        self._half = max(1e-3, float(half_period))
        self._clock = clock or time.monotonic
        self._t0 = 0.0
        self._lit = True
        self._running = False

    # ── 生命周期 ─────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    @property
    def channels(self) -> list:
        return list(self._channels)

    def start(self, now: float | None = None) -> bool:
        """开始 blink（立即写亮相位）；无通道/无写口返回 False。"""
        if not self._channels or not hasattr(self._nm, "update_layer"):
            return False
        self._t0 = self._clock() if now is None else float(now)
        self._lit = True
        self._running = True
        self._write(BLINK_VALUE)
        return True

    def tick(self, now: float | None = None) -> bool:
        """推进一拍；返回是否仍在运行（到期自动 stop 并释放）。"""
        if not self._running:
            return False
        now = self._clock() if now is None else float(now)
        if now - self._t0 >= self._duration:
            self.stop()
            return False
        lit = (int((now - self._t0) / self._half) % 2) == 0
        if lit != self._lit:
            self._lit = lit
            self._write(BLINK_VALUE if lit else 0)
        return True

    def stop(self) -> None:
        """停止并释放（清该层点名通道，不碰其余通道/层）。"""
        if not self._running:
            return
        self._running = False
        addrs = [c - 1 for c in self._channels]
        clear = getattr(self._nm, "clear_layer_channels", None)
        if callable(clear):
            try:
                clear(self._layer, self._universe, addrs)
                return
            except Exception:
                pass
        self._write(0, release=True)

    # ── 写入 ─────────────────────────────────────

    def _write(self, value: int, release: bool = False) -> None:
        """点名通道写值，``active`` 掩码只含点名通道（R141 语义）。"""
        try:
            data = np.zeros(512, dtype=np.uint8); active = np.zeros(512, dtype=bool)
            for ch in self._channels:
                data[ch - 1] = int(value); active[ch - 1] = not release
            self._nm.update_layer(self._layer, self._universe, data, active=active)
        except Exception:  # XS-06：首错告警+计数（原静默吞错）
            if not getattr(self, "_err_logged", False):
                self._err_logged = True; log.exception("blink 写通道失败")
            self._err_count = getattr(self, "_err_count", 0) + 1
