# -*- coding: utf-8 -*-
"""监视器多域总览条（R183 D；深查② Top10 #10）。

背景（2026-09-30 真工程复核）：真工程 25 域（1-9、13-28），而面板一次只看
一个域 = 其他 24 域是盲区。本条 24px 高：每域一格，色深=该域平均亮度
（复用 512 格热图同一温标 `_heatmap_color`），近满输出叠深色内环（同极值环
语义），点击格即切域（走既有域 SpinBox 槽，不新增切换路径）。

数据只读：``NetworkManager._universe_snapshot``（R178 起逐 tick 覆写的发送
快照）——本模块不写该 dict、不改 network_manager（Fixture Monitor 的
"只监视、不影响输出"语义）。
"""
from __future__ import annotations

import time

import numpy as np
from PyQt6.QtWidgets import QSizePolicy, QWidget
from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen

from ..qascade_theme import (
    BG_DEEP, FOCUS_BLUE, SEP_LINE, TEXT_SECONDARY, TEXT_DISABLED,
    cached_font, MONO_FONT_FAMILIES,
)
from .channel_grid_paint import HEAT_GAP, HEAT_HOT, HEAT_ZERO, _heatmap_color

UNIVERSE_BAR_H = 24                 # 条高（用户指令：24px 横向迷你条）
UNIVERSE_CELL_LIMIT = 64            # 可见格上限（真工程 25 域，留余量）
UNIVERSE_MIN_INTERVAL = 0.100       # 取数节流（秒）：10Hz 足够总览
UNIVERSE_LABEL_MIN_PX = 22          # 格宽 ≥ 此值才画域号


def _value_array(raw) -> np.ndarray:
    """快照值 → float64 一维数组；字节串走 frombuffer（NumPy 不受理 bytes→float）。"""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return np.frombuffer(bytes(raw), dtype=np.uint8).astype(np.float64)
    try:
        return np.asarray(raw, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError):
        return np.zeros(0, dtype=np.float64)


def universe_levels(snapshot, limit: int = UNIVERSE_CELL_LIMIT):
    """纯函数：{域: 512B} → [(域号, 均值0..1, 峰值0..1)]，域号升序取前 limit。

    均值=该域整体亮度（色块深浅）；峰值=是否有近满输出（内环判定）。
    值可为 ndarray / bytes / 列表；空值按零处理；空快照返回空表。
    """
    out = []
    for uni in sorted((snapshot or {}).keys())[: int(limit)]:
        arr = _value_array(snapshot[uni])
        if arr.size == 0:
            out.append((int(uni), 0.0, 0.0))
            continue
        out.append((int(uni), float(arr.mean()) / 255.0,
                    float(arr.max()) / 255.0))
    return out


class UniverseBar(QWidget):
    """24px 多域总览条：每格一域，点击发 universe_clicked(域号)。"""

    universe_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._levels: list = []
        self._current = 0
        self._last = float("-inf")
        self.setFixedHeight(UNIVERSE_BAR_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_tooltip()

    # ── 数据 ─────────────────────────────────────

    def set_current(self, universe: int) -> None:
        """高亮当前监控域（不触发信号）。"""
        u = int(universe or 0)
        if u == self._current:
            return
        self._current = u
        self.update()

    def maybe_refresh(self, snapshot, now: float | None = None) -> bool:
        """按 UNIVERSE_MIN_INTERVAL 节流取数重绘；返回本次是否真的刷新。"""
        now = time.monotonic() if now is None else now
        if now - self._last < UNIVERSE_MIN_INTERVAL:
            return False
        self._last = now
        self.refresh_now(snapshot)
        return True

    def refresh_now(self, snapshot) -> None:
        """立即重算（测试直调；不走节流）。级别未变不重绘。"""
        levels = universe_levels(snapshot)
        if levels != self._levels:
            self._levels = levels
            self.update()

    # ── 命中 ─────────────────────────────────────

    def index_at(self, x: float) -> int:
        """像素 x → 格下标；无数据/越界返回 -1。"""
        n = len(self._levels)
        if n <= 0 or self.width() <= 0:
            return -1
        i = int(x // (self.width() / n))
        return i if 0 <= i < n else -1

    def universe_at(self, x: float) -> int:
        """像素 x → 域号；未命中返回 0。"""
        i = self.index_at(x)
        return int(self._levels[i][0]) if i >= 0 else 0

    def _reset_tooltip(self) -> None:
        self.setToolTip("多域总览：每格一个活跃域，色深=平均亮度；点击切换监控域")

    # ── 交互 ─────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        uni = self.universe_at(e.position().x())
        if uni > 0:
            self.universe_clicked.emit(uni)

    def mouseMoveEvent(self, e):
        i = self.index_at(e.position().x())
        if i < 0:
            self._reset_tooltip()
            return
        uni, mean, peak = self._levels[i]
        self.setToolTip(f"域 {uni}: 平均 {round(mean * 255)} / 峰值 "
                        f"{round(peak * 255)}（点击切换）")

    # ── 绘制 ─────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG_DEEP))
        p.setPen(QPen(QColor(SEP_LINE), 1))
        p.drawLine(0, 0, self.width(), 0)
        n = len(self._levels)
        if n == 0:
            p.setPen(QColor(TEXT_DISABLED))
            p.setFont(cached_font(10, families=MONO_FONT_FAMILIES))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "多域总览: 无活跃域数据")
            p.end()
            return
        cw = self.width() / n
        for i, (uni, mean, peak) in enumerate(self._levels):
            self._paint_cell(p, i=i, cw=cw, uni=uni, mean=mean, peak=peak)
        p.end()

    def _paint_cell(self, p: QPainter, *, i: int, cw: float,
                    uni: int, mean: float, peak: float) -> None:
        """单格：底色=均值温标，近满输出深色内环，当前域焦点蓝描边+编号。"""
        rect = QRectF(i * cw + HEAT_GAP / 2.0, HEAT_GAP / 2.0,
                      cw - HEAT_GAP, self.height() - HEAT_GAP)
        p.fillRect(rect, _heatmap_color(mean) if mean > 0 else QColor(*HEAT_ZERO))
        current = uni == self._current
        if peak * 255.0 >= HEAT_HOT:
            p.setPen(QPen(QColor(BG_DEEP), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect.adjusted(1.0, 1.0, -1.0, -1.0))
        if cw >= UNIVERSE_LABEL_MIN_PX:
            p.setFont(cached_font(9, families=MONO_FONT_FAMILIES))
            p.setPen(QColor(FOCUS_BLUE) if current
                     else QColor(BG_DEEP) if mean > 0.55 else QColor(TEXT_SECONDARY))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(uni))
        if current:
            p.setPen(QPen(QColor(FOCUS_BLUE), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
