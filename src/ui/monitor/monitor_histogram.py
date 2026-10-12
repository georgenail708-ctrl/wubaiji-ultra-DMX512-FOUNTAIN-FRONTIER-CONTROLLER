# -*- coding: utf-8 -*-
"""监视器右栏亮度分布直方图（R183 D；深查② Top10 #2）。

现成资源（2026-09-30 侦察）：
  * pyqtgraph 0.14.0（仓内已装，零新依赖）—— PlotWidget 承载坐标轴/网格，
    BarGraphItem 画 64/256 桶柱（来源：
    https://pyqtgraph.readthedocs.io/en/latest/api_reference/graphicsItems/bargraphitem.html ）。
    不用 HistogramLUTItem：其定位是"给 ImageItem 调黑/白场 + LUT"，不接受外部
    已算好的桶计数（同上文档），本需求是纯展示 → BarGraphItem 直用更贴合。
  * 无 pyqtgraph 时 available=False，调用方静默不装配（降级先例：
    src/ui/route_animator/runner_track_ops.py 速度剖面浮窗）。

数值来源：监视器刷新链的同一显示帧（ChannelGrid._data 等值），不新增数据链。
"""
from __future__ import annotations

import time

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ..qascade_theme import BG_ROOT, TEXT_SECONDARY, BRIGHT_INFO, BRIGHT_WARN

HIST_BINS_FULL = 256           # 逐 DMX 值一桶
HIST_BINS_COARSE = 64          # 每桶 4 个 DMX 值（窄栏可辨）
HIST_WIDE_PX = 240             # 宽度自适应阈值：≥ 此宽用 256 桶
HIST_MIN_INTERVAL = 0.200      # 更新节流（秒）：20Hz 刷新下 ≥200ms 才重算/重绘
HIST_PLOT_H = 160              # 右栏占高（px，上限）
HIST_PLOT_MIN_H = 64           # 可压缩下限：小窗时不让面板最小高被这块顶死


def _load_pyqtgraph():
    """惰性导入 pyqtgraph；不可用返回 None（测试可 monkeypatch 此函数）。"""
    try:
        import pyqtgraph as pg
        return pg
    except Exception:
        return None


def choose_bin_count(width_px: int) -> int:
    """桶数自适应：窄栏 64（柱宽可辨），宽栏 256（逐值精读）。"""
    return HIST_BINS_FULL if int(width_px) >= HIST_WIDE_PX else HIST_BINS_COARSE


def histogram_counts(values, bins: int) -> np.ndarray:
    """纯函数：DMX 值序列 → bins 桶计数（值域固定 0..255，越界钳制）。

    256 桶=逐值直方图；64 桶=每桶 4 值。空输入返回全零。
    """
    n = int(bins)
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return np.zeros(n, dtype=np.int64)
    idx = (np.clip(arr, 0.0, 255.0) * n / 256.0).astype(np.int64)
    idx = np.clip(idx, 0, n - 1)
    return np.bincount(idx, minlength=n)[:n]


def _bar_brushes(bins: int) -> list:
    """逐桶刷色：0 桶（无输出）灰、末桶（近满值群）琥珀、其余信息蓝。"""
    out = []
    for i in range(bins):
        if i == 0:
            out.append(QColor("#5A5A5A"))
        elif i == bins - 1:
            out.append(QColor(BRIGHT_WARN))
        else:
            out.append(QColor(BRIGHT_INFO))
    return out


class ValueHistogram:
    """当前域亮度分布直方图；无 pyqtgraph → 不可用态（widget() 返回 None）。"""

    def __init__(self, parent=None):
        self.available = False
        self._plot = None
        self._bar = None
        self._bins = HIST_BINS_COARSE
        self._last = float("-inf")
        pg = _load_pyqtgraph()
        if pg is None:
            return
        try:
            plot = pg.PlotWidget(parent=parent, background=BG_ROOT)
            # 高度上限 160 / 下限 64：小窗可压缩，不让面板最小高被顶死
            plot.setMinimumHeight(HIST_PLOT_MIN_H)
            plot.setMaximumHeight(HIST_PLOT_H)
            plot.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            plot.hideButtons()
            plot.setMouseEnabled(x=False, y=False)
            plot.setMenuEnabled(False)
            plot.setXRange(0.0, 256.0, padding=0)
            plot.setYRange(0.0, 1.0, padding=0)
            plot.showGrid(x=False, y=True, alpha=0.18)
            for ax in ("bottom", "left"):
                axis = plot.getAxis(ax)
                axis.setPen(pg.mkPen(TEXT_SECONDARY))
                axis.setTextPen(pg.mkPen(TEXT_SECONDARY))
            self._plot = plot
            self._bar = pg.BarGraphItem(
                x=self._bucket_centers(self._bins), height=np.zeros(self._bins),
                width=float(256 // self._bins), brushes=_bar_brushes(self._bins),
                pen=None)
            plot.addItem(self._bar)
            self.available = True
        except Exception:
            self._plot = None
            self._bar = None
            self.available = False

    @staticmethod
    def _bucket_centers(bins: int) -> np.ndarray:
        step = 256.0 / bins
        return np.arange(bins) * step + step / 2.0

    def widget(self):
        """右栏装配用 QWidget；不可用时 None（调用方静默跳过）。"""
        return self._plot

    def maybe_update(self, values, now: float | None = None) -> bool:
        """按 HIST_MIN_INTERVAL 节流更新；返回本次是否真的重算/重绘。"""
        if not self.available:
            return False
        now = time.monotonic() if now is None else now
        if now - self._last < HIST_MIN_INTERVAL:
            return False
        self._last = now
        self.update_now(values)
        return True

    def update_now(self, values) -> None:
        """立即重算并按当前宽度选桶数（测试直调；不走节流）。"""
        if not self.available:
            return
        bins = choose_bin_count(self._plot.width())
        counts = histogram_counts(values, bins)
        if bins != self._bins:
            # 桶数变化：x/height/宽度/刷色必须一次性等长提交（分步会广播失败）
            self._bins = bins
            self._bar.setOpts(x=self._bucket_centers(bins),
                              width=float(256 // bins),
                              height=counts.astype(np.float64),
                              brushes=_bar_brushes(bins))
        else:
            self._bar.setOpts(height=counts.astype(np.float64))
        top = float(counts.max()) if counts.size else 0.0
        self._plot.setYRange(0.0, max(1.0, top * 1.12), padding=0)
