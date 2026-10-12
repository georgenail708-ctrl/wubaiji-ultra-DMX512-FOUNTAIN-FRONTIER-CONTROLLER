# -*- coding: utf-8 -*-
"""R4 补遗：曲线画布常驻节拍网格线（不依赖"吸附已开"）。

深查③ R4：`TrajectoryClock.local_grid()` 已具备 (origin, beat_period) 数据，
但既有的 `TrajectoryCurveView._paint_snap_grid` 只在**吸附开启**时才画格线
（`_snap_enabled()` 为假则整段跳过）——于是"看拍子"必须先开启吸附。

本模块提供 :class:`BeatGridCurveView`（`TrajectoryCurveView` 子类）：
  · 只要挂了时钟且有网格，**总是**先画一层节拍竖线（主格亮、细分淡）；
  · 再调用父类 paintEvent（父类的吸附高亮/曲线/控制点全部保留）；
  · 疏化沿用 `trajectory_grid.effective_spacing`（与吸附同一张格，画的格=会吸的格）。
`runner_envelope` 用本类搭编辑器，实现 R4「曲线画布画节拍网格线」。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

log = logging.getLogger(__name__)

#: 网格线颜色（主格/细分）——与 xascade 次级色系同调，避免抢曲线视觉
GRID_MAJOR = QColor(255, 255, 255, 38)
GRID_MINOR = QColor(255, 255, 255, 18)


def attach_beat_clock(view, panel=None):
    """给曲线视图挂一个时钟（已有则原样返回）。无音频环境 → 时钟无网格，不画线。"""
    clock = getattr(view, "_snap_clock", None)
    if clock is not None:
        return clock
    try:
        from ..trajectory_clock import TrajectoryClock
        clock = TrajectoryClock(view)
        clock.attach()
        view.set_snap_clock(clock)
    except Exception:
        log.debug("beat grid: clock attach failed", exc_info=True)
        return None
    return clock


def beat_grid_params(view):
    """返回 (origin, spacing, px_per_sec)；无网格/无时长 → None（不画）。"""
    clock = getattr(view, "_snap_clock", None)
    if clock is None:
        return None
    getter = getattr(clock, "local_grid", None)
    grid = getter() if callable(getter) else None
    if grid is None:
        return None
    try:
        from ..trajectory_grid import effective_spacing
        origin, beat_period = grid
        duration = view._duration() if hasattr(view, "_duration") else 0.0
        if duration <= 1e-9:
            return None
        rect = view._plot_rect()
        px_per_sec = rect.width() / duration
        # division=1 拍为主格；吸附开时与之同源（_snap_grid_params 同一公式）
        division = int(getattr(view, "_snap_division", 0) or 0) or 1
        spacing = effective_spacing(float(beat_period) * division, px_per_sec)
        return (float(origin), float(spacing), float(px_per_sec))
    except Exception:
        log.debug("beat grid: params failed", exc_info=True)
        return None


def paint_beat_grid(view, painter) -> int:
    """在曲线视图上画节拍网格线；返回画的线数（0 = 无网格/不画）。"""
    from ..trajectory_grid import beat_grid_lines
    params = beat_grid_params(view)
    if params is None:
        return 0
    origin, spacing, px_per_sec = params
    duration = view._duration()
    rect = view._plot_rect()
    painter.save()
    drawn = 0
    for t, level in beat_grid_lines(0.0, duration, origin, spacing, px_per_sec):
        x = view._ph_to_x(float(t) / duration)
        painter.setPen(QPen(GRID_MAJOR if level == 0 else GRID_MINOR,
                            1.0, Qt.PenStyle.SolidLine))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        drawn += 1
    painter.restore()
    return drawn


class BeatGridCurveView:
    """混入类：先画节拍网格，再让父类画既有内容（吸附高亮/曲线/控制点不变）。"""

    #: 最近一次 paintEvent 画的网格线数（离屏探针断言用）
    _grid_lines_drawn = 0

    def paintEvent(self, event) -> None:      # noqa: N802 (Qt 命名)
        try:
            painter = QPainter(self)
            self._grid_lines_drawn = paint_beat_grid(self, painter)
            painter.end()
        except Exception:
            log.debug("beat grid: paint failed", exc_info=True)
        super().paintEvent(event)


def _beat_grid_view_class():
    """带常驻节拍网格的曲线视图类（缓存：QWidget 子类只建一次）。"""
    global _VIEW_CLASS
    if _VIEW_CLASS is None:
        from ..trajectory_curve_widget import TrajectoryCurveView

        class _View(BeatGridCurveView, TrajectoryCurveView):
            pass

        _VIEW_CLASS = _View
    return _VIEW_CLASS


#: 动态视图类缓存
_VIEW_CLASS = None


def make_beat_grid_view():
    """构造带常驻节拍网格的曲线视图（R4：画布画节拍网格线）。"""
    return _beat_grid_view_class()()


__all__ = ["BeatGridCurveView", "attach_beat_clock", "beat_grid_params",
           "make_beat_grid_view", "paint_beat_grid"]
