# -*- coding: utf-8 -*-
"""matrix_level_window.py — 矩阵输出电平/直方图侧窗（深查②三.Matrix #13）。

现成资源（2026-09-30 侦察）：
  * pyqtgraph 0.14.0 仓内已装（零新依赖）：``PlotWidget`` + ``BarGraphItem``
    画 64/256 桶柱（https://pyqtgraph.readthedocs.io/en/latest/api_reference/
    graphicsItems/bargraphitem.html ）；不用 HistogramLUTItem —— 它的定位是给
    ImageItem 调黑白场，不接受外部已算好的桶计数。
  * **仓内先例直接复用**：``src/ui/monitor/monitor_histogram.py`` 的桶数自适应
    ``choose_bin_count`` / 纯统计 ``histogram_counts`` / 节流常数
    ``HIST_MIN_INTERVAL``（监视器 R183 已用同款，本模块只做取值来源与窗口壳）。
  * 无 pyqtgraph → ``LevelBars.available=False``，窗口退化为纯数字统计（降级先例：
    monitor_histogram / runner_track_ops 速度剖面浮窗）。

数值来源：面板矩阵层**实际写出的值**（``_matrix_layer_cache``，含分区 gamma 后
结果），不是控件近似值；20Hz 定时器只在窗口可见时跑（关闭 = 零开销）。
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel, QVBoxLayout

from ..monitor.monitor_histogram import (
    HIST_MIN_INTERVAL, choose_bin_count, histogram_counts,
)
from .matrix_child_window import MatrixChildWindow
from ..qascade_theme import BG_ROOT, TEXT_SECONDARY

logger = logging.getLogger(__name__)

#: 窗口可见时的轮询间隔（50ms = 20Hz，与任务书一致）
POLL_MS = 50
#: 窄窗用 64 桶、宽窗用 256 桶的宽度阈值（沿用监视器先例口径）
WIDE_PX = 360


def layer_values(layer_cache) -> np.ndarray:
    """矩阵层写值缓存 → 一维 uint8 值数组（纯函数；空层 → 空数组）。

    ``{universe: {0基通道: 0-255}}`` → 所有被写通道的值（未写的通道不参与分布，
    这样"卡值/满值通道群"一眼可见，而不是被 512-活动数 的零压扁）。
    """
    vals = []
    for chans in (layer_cache or {}).values():
        if not isinstance(chans, dict):
            continue
        for val in chans.values():
            try:
                vals.append(max(0, min(255, int(round(float(val))))))
            except (TypeError, ValueError):
                continue
    return np.asarray(vals, dtype=np.uint8)


def bins_for_values(values, width_px: int = WIDE_PX) -> np.ndarray:
    """值序列 → 桶计数（复用监视器桶数/统计口径；纯函数）。"""
    return histogram_counts(values, choose_bin_count(int(width_px)))


def stats_text(values) -> str:
    """一行统计：min/max/mean/活动通道数（纯函数）。"""
    arr = np.asarray(values, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return "min=--  max=--  mean=--  通道=0（未出光）"
    return (f"min={float(arr.min()):5.1f}  max={float(arr.max()):5.1f}  "
            f"mean={float(arr.mean()):6.2f}  通道={int(arr.size)}  "
            f"满值={int((arr >= 255).sum())}")


def _load_pyqtgraph():
    """惰性导入 pyqtgraph；不可用返回 None（测试可 monkeypatch）。"""
    try:
        import pyqtgraph as pg
        return pg
    except Exception:
        return None


class LevelBars:
    """输出电平柱状图（pyqtgraph BarGraphItem；无库 → 不可用态）。"""

    def __init__(self, parent=None):
        self.available = False
        self._plot = None
        self._bar = None
        self._last = float("-inf")
        pg = _load_pyqtgraph()
        if pg is None:
            return
        try:
            plot = pg.PlotWidget(parent=parent, background=BG_ROOT)
            plot.setMinimumHeight(120)
            plot.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            plot.hideButtons()
            plot.setMouseEnabled(x=False, y=False)
            plot.setMenuEnabled(False)
            plot.setXRange(0.0, 256.0, padding=0)
            plot.setYRange(0.0, 1.0, padding=0)
            plot.showGrid(x=False, y=True, alpha=0.18)
            for name in ("bottom", "left"):
                axis = plot.getAxis(name)
                axis.setPen(pg.mkPen(TEXT_SECONDARY))
                axis.setTextPen(pg.mkPen(TEXT_SECONDARY))
            self._plot = plot
            self._bar = pg.BarGraphItem(
                x=self._centers(64), height=np.zeros(64, dtype=np.float64),
                width=float(256 // 64), brush="#71A8FF", pen=None)
            plot.addItem(self._bar)
            self.available = True
        except Exception:
            self._plot = None
            self._bar = None
            self.available = False

    @staticmethod
    def _centers(bins: int) -> np.ndarray:
        step = 256.0 / bins
        return np.arange(bins) * step + step / 2.0

    def widget(self):
        """装配用 QWidget；不可用 → None（调用方退化）。"""
        return self._plot

    def maybe_update(self, values, now: Optional[float] = None) -> bool:
        """按 HIST_MIN_INTERVAL 节流更新；返回本次是否重绘（复用监视器节流口径）。"""
        if not self.available:
            return False
        now = time.monotonic() if now is None else now
        if now - self._last < HIST_MIN_INTERVAL:
            return False
        self._last = now
        self.update_now(values)
        return True

    def update_now(self, values) -> None:
        """立即重算并重绘（测试直调；不走节流）。"""
        if not self.available:
            return
        width = self._plot.width() if self._plot is not None else WIDE_PX
        bins = choose_bin_count(width)
        counts = histogram_counts(values, bins)
        try:
            self._bar.setOpts(x=self._centers(bins),
                              width=float(256 // bins),
                              height=counts.astype(np.float64),
                              brush=("#71A8FF" if bins >= 256 else "#4F8FE0"))
        except Exception:
            logger.debug("matrix: level bars update failed", exc_info=True)
        top = float(counts.max()) if counts.size else 0.0
        if self._plot is not None:
            self._plot.setYRange(0.0, max(1.0, top * 1.12), padding=0)


def panel_layer_values(panel) -> np.ndarray:
    """面板**真实下发**的矩阵输出层 → 值数组（NM 层优先；供窗口/测试直用）。"""
    from .matrix_frame_recorder import snapshot_layer
    try:
        return layer_values(snapshot_layer(panel))
    except Exception:
        logger.debug("matrix: read layer for level window failed", exc_info=True)
        return np.zeros(0, dtype=np.uint8)


class MatrixLevelWindow(MatrixChildWindow):
    """「矩阵 · 输出电平」侧窗：20Hz 喂亮度/写值网格的 DMX 值分布。"""

    def __init__(self, panel, provider: Optional[Callable] = None):
        super().__init__(panel, "矩阵 · 输出电平（20Hz）",
                         size=(560, 300), min_size=(380, 220))
        self._provider = provider or (lambda: panel_layer_values(panel))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self._stats_lbl = QLabel("min=--  max=--  mean=--  通道=0（未出光）")
        self._stats_lbl.setStyleSheet("color:#888;font-size:10px;font-family:Consolas;")
        lay.addWidget(self._stats_lbl)
        self.bars = LevelBars(self)
        widget = self.bars.widget()
        if widget is not None:
            lay.addWidget(widget, stretch=1)
        else:
            lay.addWidget(QLabel("pyqtgraph 不可用：只显示数字统计（功能降级）"))
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)

    # ── 生命周期：只在可见时轮询（关闭 = 零开销） ─────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> bool:
        """拉一次 provider → 统计 + 柱图；返回是否真的重绘了柱图。"""
        try:
            values = self._provider()
        except Exception:
            logger.debug("matrix: level provider failed", exc_info=True)
            values = np.zeros(0, dtype=np.uint8)
        self._stats_lbl.setText(stats_text(values))
        return self.bars.maybe_update(values)


def open_level_window(panel) -> MatrixLevelWindow:
    """打开（或置前）输出电平侧窗；面板上复用同一实例。"""
    win = getattr(panel, "_level_window", None)
    if win is None:
        win = MatrixLevelWindow(panel)
        try:
            panel._level_window = win
        except Exception:
            logger.debug("matrix: attach level window failed", exc_info=True)
    win.show()
    win.raise_()
    win.activateWindow()
    return win


__all__ = ["LevelBars", "MatrixLevelWindow", "POLL_MS", "bins_for_values",
           "layer_values", "open_level_window", "panel_layer_values",
           "stats_text"]
