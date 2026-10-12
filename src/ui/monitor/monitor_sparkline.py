# -*- coding: utf-8 -*-
"""数值历史 sparkline（R183 W2；深查② §四 #7）。

需求原文：「数值历史 sparkline：点选通道后 pyqtgraph 近 10s 曲线（环形缓冲
200 点/channel，20Hz 推进；无 pyqtgraph 静默降级）」。

现成资源（2026-09-30 侦察，见 ai-memory/_references/deep-sweep-2-ui-panels.md）：
  * pyqtgraph 0.14.0 仓内已装（零新依赖）→ ``PlotWidget`` 承载曲线
    https://pyqtgraph.readthedocs.io/en/latest/api_reference/widgets/plotwidget.html
  * 无 pyqtgraph → ``available=False``，调用方静默不装配（先例：
    ``src/ui/route_animator/runner_track_ops.py`` 速度剖面浮窗、
    ``monitor_histogram.ValueHistogram``）。
数据=监视器刷新链的同一显示帧（20Hz），不新增数据链；环形缓冲按通道存放，
每通道 200 点（10s@20Hz）。
"""
from __future__ import annotations

import time

import numpy as np

from ..qascade_theme import BG_ROOT, BRIGHT_INFO, TEXT_SECONDARY

SPARK_POINTS = 200              # 200 点 / 通道 = 10s @ 20Hz
SPARK_CHANNELS = 512
SPARK_MIN_INTERVAL = 0.045      # 推进节流：≤20Hz（与面板 50ms 定时器同拍）
SPARK_PLOT_H = 96


def _load_pyqtgraph():
    """惰性导入 pyqtgraph；不可用返回 None（测试可 monkeypatch 此函数）。"""
    try:
        import pyqtgraph as pg
        return pg
    except Exception:
        return None


class RingSeries:
    """逐通道环形缓冲（纯 numpy）：push 一帧，get(ch) 取该通道历史（旧→新）。"""

    def __init__(self, channels: int = SPARK_CHANNELS,
                 points: int = SPARK_POINTS):
        self._buf = np.zeros((max(1, int(channels)), max(1, int(points))),
                             dtype=np.uint8)
        self._points = self._buf.shape[1]
        self._head = 0
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    @property
    def points(self) -> int:
        return self._points

    def push(self, frame) -> None:
        """写入一帧（不足补零、超长截断；非法输入丢弃）。"""
        try:
            arr = np.asarray(frame, dtype=np.uint8).reshape(-1)
        except (TypeError, ValueError):
            return
        n = min(arr.size, self._buf.shape[0])
        self._buf[:n, self._head] = arr[:n]
        self._head = (self._head + 1) % self._points
        self._count = min(self._points, self._count + 1)

    def get(self, channel: int) -> np.ndarray:
        """通道历史（1-based；旧→新，长度=已存点数）；越界返回空表。"""
        ch = int(channel) - 1
        if ch < 0 or ch >= self._buf.shape[0] or self._count <= 0:
            return np.zeros(0, dtype=np.uint8)
        start = (self._head - self._count) % self._points
        idx = (start + np.arange(self._count)) % self._points
        return self._buf[ch, idx].copy()


class ChannelSparkline:
    """选中通道曲线；无 pyqtgraph → 不可用态（widget() 返回 None）。"""

    def __init__(self, parent=None, points: int = SPARK_POINTS):
        self.available = False
        self.series = RingSeries(SPARK_CHANNELS, points)
        self._plot = None
        self._curve = None
        self._channel = 0
        self._last = float("-inf")
        pg = _load_pyqtgraph()
        if pg is None:
            return
        try:
            plot = pg.PlotWidget(parent=parent, background=BG_ROOT)
            plot.setFixedHeight(SPARK_PLOT_H)
            plot.hideButtons()
            plot.setMenuEnabled(False)
            plot.setMouseEnabled(x=False, y=False)
            plot.setYRange(0.0, 255.0, padding=0)
            plot.setXRange(0, max(1, int(points)), padding=0)
            plot.showGrid(x=False, y=True, alpha=0.18)
            for ax in ("bottom", "left"):
                axis = plot.getAxis(ax)
                axis.setPen(pg.mkPen(TEXT_SECONDARY))
                axis.setTextPen(pg.mkPen(TEXT_SECONDARY))
            self._curve = plot.plot(pen=pg.mkPen(BRIGHT_INFO, width=1))
            self._plot = plot
            self.available = True
        except Exception:
            self._plot = None
            self._curve = None
            self.available = False

    def widget(self):
        """装配用 QWidget；不可用时 None（调用方静默跳过）。"""
        return self._plot

    @property
    def channel(self) -> int:
        return self._channel

    def select(self, channel: int) -> None:
        """切换观察通道（0=无）；立即重画当前历史。"""
        ch = int(channel or 0)
        self._channel = ch if 1 <= ch <= SPARK_CHANNELS else 0
        if self.available:
            self._redraw()

    def maybe_push(self, frame, now: float | None = None) -> bool:
        """按 SPARK_MIN_INTERVAL 节流推进；返回本次是否真的写缓冲/重绘。"""
        now = time.monotonic() if now is None else float(now)
        if now - self._last < SPARK_MIN_INTERVAL:
            return False
        self._last = now
        self.push_now(frame)
        return True

    def push_now(self, frame) -> None:
        """立即写一帧并重绘（测试直调；不走节流）。"""
        self.series.push(frame)
        if self.available:
            self._redraw()

    def _redraw(self) -> None:
        if self._curve is None or self._channel <= 0:
            if self._curve is not None:
                self._curve.setData([], [])
            return
        hist = self.series.get(self._channel)
        self._curve.setData(np.arange(hist.size), hist.astype(np.float64))
