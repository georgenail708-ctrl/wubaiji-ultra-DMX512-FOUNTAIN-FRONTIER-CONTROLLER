# -*- coding: utf-8 -*-
"""R15：HUD 全局总览 —— 一条 mini 时间线显示所有车 nose_s 的归一化位置点。

深查③ R15：HUD 只有卡片，没有全局总览。本模块给 HUD 参数行注入一条紧凑
自绘条（高度 14px，零 pyqtgraph 依赖）：
  · 横轴 = 每列火车所在轨道的弧长归一化 [0,1]；
  · 每列火车一个点（颜色 = 车色，分队 id 提亮），位置 = nose_s / 轨道长；
  · 悬停显示「名称 · 位置% · 油门%」；播放时每帧刷新（`refresh_overview`）。
多轨场景下所有车落在同一条轴上，用颜色区分——这是"一眼掌控"的最小可用版
（不做多行轨道轴，避免 HUD 长高）。

数据全部只读引擎运行态（`_trains` / `_edges` / `_home_edge_id`），不新建第二份
状态；无车/无轨 → 空条（不画点，不报错）。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..qascade_theme import BG_DEEP, SEP_LINE, TEXT_SECONDARY

#: 条高（px）——HUD 参数行内的紧凑高度
BAR_HEIGHT = 14

#: 点的半径（px）
DOT_R = 3.0

#: 底色与轴线（与 HUD 同一主题令牌）
_TRACK_LINE = SEP_LINE
_LABEL = TEXT_SECONDARY
_BG = BG_DEEP


def _dot_of(panel, tid: str, tr, edges) -> Optional[Tuple[str, float, str, float]]:
    """单车 → (tid, 归一化位置, 颜色, 油门)；无车厢/轨道长 0 → None（不画点）。"""
    if not getattr(tr, "cars", None):
        return None
    eid = getattr(tr, "_home_edge_id", None) or tr.cars[0].edge_id
    edge = edges.get(eid)
    length = float(getattr(edge, "length", 0.0) or 0.0)
    if length <= 0.0:
        return None
    pos = max(0.0, min(1.0, float(tr.nose_s) / length))
    color = str(getattr(tr, "color", "#8fd0ff") or "#8fd0ff")
    if "#" in str(tid):
        color = QColor(color).lighter(120).name()
    return (str(tid), pos, color, float(getattr(tr, "throttle", 0.0)))


def train_dots(panel) -> List[Tuple[str, float, str, float]]:
    """引擎运行态 → [(tid, 归一化位置 0..1, 颜色, 油门 0..1), ...]。

    归一化用记下的 `_home_edge_id` 对应边长度，缺边时用当前车头所在边；
    无车/无轨 → 空表（不画点，不报错）。
    """
    engine = getattr(panel, "_engine", None)
    if engine is None:
        return []
    edges = getattr(engine, "_edges", None) or {}
    out: List[Tuple[str, float, str, float]] = []
    for tid, tr in (getattr(engine, "_trains", None) or {}).items():
        dot = _dot_of(panel, tid, tr, edges)
        if dot is not None:
            out.append(dot)
    return out


class FleetOverviewBar(QWidget):
    """mini 时间线：横轴 = 归一化轨道位置，每车一点。"""

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent)
        self._panel = panel
        self._dots: List[Tuple[str, float, str, float]] = []
        self.setFixedHeight(BAR_HEIGHT)
        self.setMinimumWidth(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("全局总览：每条火车所在轨道的归一化位置（颜色=车色）")
        self.setMouseTracking(True)

    def set_dots(self, dots) -> None:
        self._dots = list(dots or [])
        self.update()

    def dots(self) -> list:
        """当前点表（离屏探针直接读）。"""
        return list(self._dots)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        y = self.height() / 2.0
        w = max(1, self.width())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(_BG))
        p.setPen(QPen(QColor(_TRACK_LINE), 1))
        p.drawLine(0, int(y), w, int(y))
        p.setPen(QPen(QColor(_LABEL), 1))
        p.drawText(4, int(y) - 3, "总览")
        for _tid, pos, color, throttle in self._dots:
            x = 2.0 + (w - 4.0) * max(0.0, min(1.0, float(pos)))
            c = QColor(str(color))
            # 油门 < 5% 视为停车：空心圈（一眼看出谁停着）
            if float(throttle) <= 0.05:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(c, 1.4))
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
            p.drawEllipse(QPointF(x, y), DOT_R, DOT_R)

    def mouseMoveEvent(self, event) -> None:
        """悬停：找最近点给「名称 · 位置% · 油门%」提示（无线内点 → 复位提示）。"""
        best = None
        best_d = 12.0
        w = max(1, self.width())
        for tid, pos, _color, throttle in self._dots:
            x = 2.0 + (w - 4.0) * float(pos)
            d = abs(event.position().x() - x)
            if d <= best_d:
                best_d = d
                best = (tid, pos, throttle)
        if best is None:
            self.setToolTip("全局总览：每条火车所在轨道的归一化位置（颜色=车色）")
            return
        tid, pos, throttle = best
        name = str((getattr(self._panel, "_train_names", None) or {}).get(tid, tid))
        self.setToolTip(f"{name} · 位置 {pos * 100:.0f}% · 油门 {throttle * 100:.0f}%")


def build_overview_widget(panel) -> QWidget:
    """建条并挂到面板 `_overview_bar`（幂等：已建直接返回）。"""
    bar = getattr(panel, "_overview_bar", None)
    if bar is None:
        bar = FleetOverviewBar(panel)
        panel._overview_bar = bar
    return bar


def refresh_overview(panel) -> int:
    """刷新总览点表（帧循环调用；无条 → 0，零开销）。返回点数。"""
    bar = getattr(panel, "_overview_bar", None)
    if bar is None:
        return 0
    dots = train_dots(panel)
    bar.set_dots(dots)
    return len(dots)


__all__ = ["FleetOverviewBar", "build_overview_widget", "refresh_overview",
           "train_dots", "BAR_HEIGHT"]
