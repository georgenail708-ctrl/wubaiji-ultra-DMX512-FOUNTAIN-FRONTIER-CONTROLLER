# -*- coding: utf-8 -*-
"""Draw 画布几何兼容层（01 清单 A1 修复）—— 点/矩形输入归一 + 贝塞尔连线。

A1 根因（用户实测栈）：拖线预览把 ``_link["cur"]``（``QPointF``，见 draw_canvas
mousePressEvent / mouseMoveEvent 赋值）传进按 ``p[0]/p[1]`` 下标取值的
``_seg_bezier`` → ``TypeError: 'QPointF' object is not subscriptable``。

本模块是 ``seg_bezier`` 的唯一实现：``xy()`` 统一接受 tuple / list / QPointF
（及任何带可调用 ``x()/y()`` 的点对象），全部调用点（已落位边
``_draw_edges``、拖线预览 ``_draw_link_preview``、桩位 ``_draw_ports``）都经它取值。

另修（B2 卡片"巨大空灰框"的根因）：``rect_from_bounds`` —— 修前
``QRectF(*node_rect(n))`` 把 ``(x0, y0, x1, y1)`` 当成 ``(x, y, w, h)``，卡片宽高
等于其右下角绝对坐标：越靠右下画得越大、与命中区（draw_graph.hit_node 用正确
边界）不一致，全览/框选/连线裁剪同受影响。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QPainterPath

__all__ = ["xy", "seg_bezier", "rect_from_bounds"]

#: 控制点水平偏移 = max(下限, 水平距 × 系数)（与 clip_studio 同参数，修前数值不变）
_BEZIER_MIN_OFF = 28.0
_BEZIER_K = 0.22


def xy(p) -> tuple:
    """点 → ``(float x, float y)``；兼容 QPointF / QPoint / tuple / list。"""
    if p is None:
        raise TypeError("xy(): point is None")
    getter = getattr(p, "x", None)
    if callable(getter):
        return float(p.x()), float(p.y())
    return float(p[0]), float(p[1])


def seg_bezier(p1, p2) -> QPainterPath:
    """三次贝塞尔连线（两端任意点类型；控制点垂距 0.22×水平距）。"""
    x1, y1 = xy(p1)
    x2, y2 = xy(p2)
    off = max(_BEZIER_MIN_OFF, abs(x2 - x1) * _BEZIER_K)
    path = QPainterPath(QPointF(x1, y1))
    path.cubicTo(QPointF(x1 + off, y1), QPointF(x2 - off, y2), QPointF(x2, y2))
    return path


def rect_from_bounds(bounds) -> QRectF:
    """``(x0, y0, x1, y1)`` 边界 → QRectF（两角构造，保证宽高 = x1-x0 / y1-y0）。"""
    x0, y0, x1, y1 = (float(v) for v in bounds)
    return QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()
