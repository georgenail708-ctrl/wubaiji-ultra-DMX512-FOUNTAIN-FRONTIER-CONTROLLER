# -*- coding: utf-8 -*-
from __future__ import annotations
from .topview_selection_context import reset_manual_context
from ..utils.fixture_installation import installation_heading, synchronized_installation_heading
from . import fixture_group_activation as _group_activation

import math
import bisect
import copy
import uuid
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QRectF, QPointF, QPoint, QRect, pyqtSignal, QMimeData, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QBrush, QPixmap, QMouseEvent, QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent, QFont, QPolygonF, QTransform
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTabWidget, QInputDialog, QSpinBox, QDoubleSpinBox, QGroupBox, QFormLayout,
    QMessageBox, QSplitter, QSizePolicy, QLineEdit, QScrollArea, QGridLayout,
    QApplication, QCheckBox, QSlider
)

from . import qascade_theme as _th
from .canvas_materials import paint_bed, selection_fill
from .topview_track_draw import install_track_tool
from .widget_kit import EmptyState, hidpi_pixmap, logical_size
from . import error_catalog as ec

logger = logging.getLogger(__name__)

# 令牌化改造（restyle_spec §3）：
#   - 原 QWidget{} 通配及其对通用控件（按钮/数字框/下拉/分组框/Tab）的整套
#     外观定义与全局 GLOBAL_QSS 重复，按契约删除——通用外观全部交给全局主题。
#   - 只保留面板 chrome 的窄选择器：基底 bg_editor、正文 text_primary、正文 12px。
#   - 页面三种近黑底收敛分层：chrome #2b2b2b→bg_editor(#303030)，
#     画布填充 #202224→bg_root(#1D1D1D)（见 TopViewCanvas.paintEvent）。
STYLE = f"""
TopViewPanel {{ background:{_th.BG_EDITOR}; color:{_th.TEXT_PRIMARY}; font-size:12px; }}
"""


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


# 空列表哨兵：_xform_cache_key 在无逻辑设备/无工程时使用——保证键身份稳定
# （每次新建 [] 会使 id() 变化 → 缓存永远不命中）。
_EMPTY_LDS: List[dict] = []


def _arc_span(shape: dict):
    """若 shape 是部分圆弧（circle 型 + span_angle 且 <360°），返回 (start_rad, span_rad)。

    整圆（无 span_angle 或 span_angle≈360）返回 None，调用方走原整圆快路径。
    角度约定与 _shape_points 一致：start_angle/span_angle 为度，按 cos/sin 正向采样，
    从而拟合曲线与 _shape_points 摆放的设备点完全重合。导入的长弧即走此路（build_fit_shape
    对 arc 输出 type=circle + start_angle/span_angle），故渲染/命中/包围盒都只覆盖真实弧段。
    """
    if "span_angle" not in shape:
        return None
    span = _safe_float(shape.get("span_angle"), 360.0)
    if abs(span) >= 359.9:
        return None
    return math.radians(_safe_float(shape.get("start_angle"), 0.0)), math.radians(span)


def _arc_sample_points(cx: float, cy: float, rx: float, ry: float,
                       start_rad: float, span_rad: float, n: int = 64):
    """沿圆弧等角采样 n+1 个点（含首尾端点），供绘制/命中/包围盒共用。"""
    n = max(2, int(n))
    return [(cx + math.cos(start_rad + span_rad * i / n) * rx,
             cy + math.sin(start_rad + span_rad * i / n) * ry) for i in range(n + 1)]


def _shape_points(shape: dict, count: int) -> List[Tuple[float, float]]:
    count = max(0, int(count or 0))
    if count <= 0:
        return []
    typ = shape.get("type", "line")
    if typ == "line":
        x1, y1 = _safe_float(shape.get("x1")), _safe_float(shape.get("y1"))
        x2, y2 = _safe_float(shape.get("x2")), _safe_float(shape.get("y2"))
        if count == 1:
            return [(x1, y1)]
        return [(x1 + (x2 - x1) * i / (count - 1), y1 + (y2 - y1) * i / (count - 1)) for i in range(count)]
    if typ in ("circle", "ellipse"):
        cx, cy = _safe_float(shape.get("cx")), _safe_float(shape.get("cy"))
        rx = abs(_safe_float(shape.get("rx"), 80.0)) or 80.0
        ry = abs(_safe_float(shape.get("ry"), rx)) or rx
        start = math.radians(_safe_float(shape.get("start_angle"), 0.0))
        span = math.radians(_safe_float(shape.get("span_angle"), 360.0))
        denom = count if abs(span) >= math.radians(359.9) else max(1, count - 1)
        return [(cx + math.cos(start + span * i / denom) * rx, cy + math.sin(start + span * i / denom) * ry) for i in range(count)]
    if typ == "spline":
        # 过设备定位点的平滑曲线采样点（导入时 Catmull-Rom 预采样）
        pts = [(float(x), float(y)) for x, y in shape.get("points", [])]
        if not pts:
            return []
        if count == 1:
            return [pts[0]]
        step = max(1, (len(pts) - 1) / max(1, count - 1))
        return [pts[min(len(pts) - 1, int(round(i * step)))] for i in range(count)]
    if typ == "polyline":
        pts = [(float(x), float(y)) for x, y in shape.get("points", [])]
        if len(pts) < 2:
            return pts[:count]
        # 支持曲线段：每段可有可选控制点 cp1/cp2（三次贝塞尔）
        seg_cps = shape.get("seg_cps", {}) or {}  # {"0": {"cp1":[x,y],"cp2":[x,y]}, ...}
        # 先采样每段的小点，用于计算各段长度和总长度
        _SAMPLE = 32
        seg_samples = []  # list of [(x,y), ...] per segment
        for seg_i, (a, b) in enumerate(zip(pts, pts[1:])):
            cp_data = seg_cps.get(str(seg_i), {})
            if cp_data and ("cp1" in cp_data or "cp2" in cp_data):
                cp1 = cp_data.get("cp1") or [(a[0]*2+b[0])/3, (a[1]*2+b[1])/3]
                cp2 = cp_data.get("cp2") or [(a[0]+b[0]*2)/3, (a[1]+b[1]*2)/3]
                samples = []
                for k in range(_SAMPLE + 1):
                    t = k / _SAMPLE
                    u = 1 - t
                    x = u**3*a[0] + 3*u**2*t*cp1[0] + 3*u*t**2*cp2[0] + t**3*b[0]
                    y = u**3*a[1] + 3*u**2*t*cp1[1] + 3*u*t**2*cp2[1] + t**3*b[1]
                    samples.append((x, y))
                seg_samples.append(samples)
            else:
                seg_samples.append([a, b])
        # 计算各段长度
        seg_lengths = []
        for samples in seg_samples:
            d = sum(math.hypot(samples[k+1][0]-samples[k][0], samples[k+1][1]-samples[k][1]) for k in range(len(samples)-1))
            seg_lengths.append(d)
        total = sum(seg_lengths)
        if total <= 0:
            return [pts[0]] * count
        # 展平所有段的采样点
        flat = []
        for samples in seg_samples:
            flat.extend(samples[:-1])
        flat.append(seg_samples[-1][-1])
        # 按全长度均匀采样 count 个点
        total_flat = sum(math.hypot(flat[k+1][0]-flat[k][0], flat[k+1][1]-flat[k][1]) for k in range(len(flat)-1))
        if total_flat <= 0:
            return [pts[0]] * count
        out = []
        for i in range(count):
            target = total_flat * (i / max(1, count - 1))
            acc = 0.0
            for k in range(len(flat) - 1):
                d = math.hypot(flat[k+1][0]-flat[k][0], flat[k+1][1]-flat[k][1])
                if acc + d >= target or d <= 0:
                    r = 0.0 if d <= 0 else (target - acc) / d
                    out.append((flat[k][0] + (flat[k+1][0]-flat[k][0])*r,
                                flat[k][1] + (flat[k+1][1]-flat[k][1])*r))
                    break
                acc += d
            else:
                out.append(flat[-1])
        return out
    if typ == "pattern_matrix":
        rows = shape.get("rows", []) or []
        x0, y0 = _safe_float(shape.get("x", 40)), _safe_float(shape.get("y", 40))
        pts, y = [], y0
        for row in rows:
            n = max(0, int(row.get("count", 0)))
            dx, dy = _safe_float(row.get("dx", 40), 40), _safe_float(row.get("dy", 40), 40)
            offset = _safe_float(row.get("offset", 0), 0)
            for i in range(n):
                pts.append((x0 + offset + i * dx, y))
            y += dy
        angle = _safe_float(shape.get("angle", 0.0), 0.0)
        if pts and abs(angle) > 0.001:
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
            pts = [_rotate_point_xy(x, y, cx, cy, angle) for x, y in pts]
        return pts[:count]
    return []


def _dist_point_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy)


def _seg_nearest_point(pt, ax: float, ay: float,
                       bx: float, by: float) -> Tuple[float, float]:
    """点 `pt=(x,y)` 在线段 ab 上的**最近点**（投影并夹到段内）。"""
    px, py = float(pt[0]), float(pt[1])
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return float(ax), float(ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return float(ax + t * dx), float(ay + t * dy)


def _nearest_on_polyline(pts, pt) -> Tuple[float, float]:
    """折线/样条采样点集上离 `pt` 最近的点（逐段投影取最小）。"""
    px, py = float(pt[0]), float(pt[1])
    if not pts:
        return px, py
    best, best_d2 = (float(pts[0][0]), float(pts[0][1])), float("inf")
    for k in range(len(pts) - 1):
        qx, qy = _seg_nearest_point(pt, float(pts[k][0]), float(pts[k][1]),
                                    float(pts[k + 1][0]), float(pts[k + 1][1]))
        d2 = (px - qx) * (px - qx) + (py - qy) * (py - qy)
        if d2 < best_d2:
            best_d2, best = d2, (qx, qy)
        if best_d2 <= 1e-12:
            break
    return float(best[0]), float(best[1])


def _nearest_on_ellipse(shape, pt, geo) -> Tuple[float, float]:
    """圆/椭圆（含圆弧段）上离 `pt` 最近的点（按参数角投影；弧外取较近端点）。

    `geo = (cx, cy, rx, ry)`（打包以压位置参数门禁）。
    """
    cx, cy, rx, ry = geo
    px, py = float(pt[0]), float(pt[1])
    dx, dy = px - cx, py - cy
    typ = str(shape.get("type", "") or "")
    span = _arc_span(shape) if typ == "circle" else None
    if span is None:
        if typ == "circle":
            r = math.hypot(dx, dy)
            if r < 1e-9:
                return cx + rx, cy
            return cx + dx * rx / r, cy + dy * rx / r
        ang = math.atan2(dy / max(ry, 1e-9), dx / max(rx, 1e-9))
        return cx + math.cos(ang) * rx, cy + math.sin(ang) * ry
    s, sp = span
    ang = math.atan2(dy / max(ry, 1e-9), dx / max(rx, 1e-9))
    rel = (ang - s) % (2.0 * math.pi)
    if rel > abs(sp):
        e0 = (cx + math.cos(s) * rx, cy + math.sin(s) * ry)
        e1 = (cx + math.cos(s + sp) * rx, cy + math.sin(s + sp) * ry)
        if math.hypot(px - e0[0], py - e0[1]) <= math.hypot(px - e1[0], py - e1[1]):
            return e0[0], e0[1]
        return e1[0], e1[1]
    ang = s + rel
    return cx + math.cos(ang) * rx, cy + math.sin(ang) * ry


def _shape_nearest_point(shape: dict, px: float,
                         py: float) -> Tuple[float, float]:
    """图形上**离 (px,py) 最近的点**（单台设备吸附用）。

    ★★ R170c 修复（用户实报"topview 二维页面弧线 42 台少一个点"）：
      单台设备拖到图形上时，旧代码走 `_shape_points(shape, 1)[0]` —— 对 spline/polyline
      这是图形的**起点**，于是用户把一台设备拖到弧线中段，它会**瞬移到弧线起点**，
      与原本在那里的设备完全重合（画面上表现为"少一个点、起点多一个点"）。
      真实工程实测：`E:\\水仙十字.dmxt` 的「二维」页 102 台里有 1 台
      （`2-Axis Jet - 15m 21`）页面坐标恰好等于该弧线样条的首个采样点，
      而它的模型坐标仍在弧线上第 19 位 —— 就是这个动作留下的。
      正解：单台吸附 = 投影到图形最近点，落在手指/光标附近，不跳位。
    """
    typ = str(shape.get("type", "line") or "line")
    if typ == "line":
        return _seg_nearest_point(
            (px, py), _safe_float(shape.get("x1")), _safe_float(shape.get("y1")),
            _safe_float(shape.get("x2")), _safe_float(shape.get("y2")))
    if typ in ("circle", "ellipse"):
        rx = abs(_safe_float(shape.get("rx"), 80.0)) or 80.0
        ry = abs(_safe_float(shape.get("ry"), rx)) or rx
        return _nearest_on_ellipse(
            shape, (px, py),
            (_safe_float(shape.get("cx")), _safe_float(shape.get("cy")), rx, ry))
    return _nearest_on_polyline(_shape_points(shape, 1_000_000), (px, py))


def _shape_anchor_points(shape: dict) -> List[Tuple[str, float, float]]:
    typ = shape.get("type", "line")
    if typ == "line":
        return [("p1", _safe_float(shape.get("x1")), _safe_float(shape.get("y1"))),
                ("p2", _safe_float(shape.get("x2")), _safe_float(shape.get("y2")))]
    if typ in ("circle", "ellipse"):
        cx, cy = _safe_float(shape.get("cx")), _safe_float(shape.get("cy"))
        rx, ry = abs(_safe_float(shape.get("rx"), 80.0)), abs(_safe_float(shape.get("ry"), 80.0))
        arc = _arc_span(shape) if typ == "circle" else None
        if arc is not None:
            s, sp = arc
            pe = _arc_sample_points(cx, cy, rx, ry, s, sp)
            mid = pe[len(pe) // 2]
            return [("center", cx, cy), ("start", pe[0][0], pe[0][1]),
                    ("end", pe[-1][0], pe[-1][1]), ("mid", mid[0], mid[1])]
        return [("center", cx, cy), ("right", cx + rx, cy), ("bottom", cx, cy + ry),
                ("left", cx - rx, cy), ("top", cx, cy - ry)]
    if typ == "polyline":
        pts = shape.get("points", []) or []
        result = [(f"pt:{i}", _safe_float(x), _safe_float(y)) for i, (x, y) in enumerate(pts)]
        # 曲线段控制点手柄
        seg_cps = shape.get("seg_cps", {}) or {}
        for seg_i_str, cp_data in seg_cps.items():
            if not cp_data:
                continue
            if "cp1" in cp_data:
                result.append((f"cp1:{seg_i_str}", _safe_float(cp_data["cp1"][0]), _safe_float(cp_data["cp1"][1])))
            if "cp2" in cp_data:
                result.append((f"cp2:{seg_i_str}", _safe_float(cp_data["cp2"][0]), _safe_float(cp_data["cp2"][1])))
        return result
    if typ == "pattern_matrix":
        return [("origin", _safe_float(shape.get("x", 40)), _safe_float(shape.get("y", 40)))]
    return []


def _shape_hit_distance(shape: dict, pos: QPointF) -> float:
    px, py = pos.x(), pos.y()
    typ = shape.get("type", "line")
    if typ == "line":
        return _dist_point_to_segment(px, py, _safe_float(shape.get("x1")), _safe_float(shape.get("y1")), _safe_float(shape.get("x2")), _safe_float(shape.get("y2")))
    if typ in ("circle", "ellipse"):
        cx, cy = _safe_float(shape.get("cx")), _safe_float(shape.get("cy"))
        rx, ry = max(1.0, abs(_safe_float(shape.get("rx"), 80.0))), max(1.0, abs(_safe_float(shape.get("ry"), 80.0)))
        arc = _arc_span(shape) if typ == "circle" else None
        if arc is not None:
            pe = _arc_sample_points(cx, cy, rx, ry, arc[0], arc[1])
            return min(_dist_point_to_segment(px, py, pe[k][0], pe[k][1], pe[k + 1][0], pe[k + 1][1])
                       for k in range(len(pe) - 1))
        if typ == "ellipse":
            angle = -_safe_float(shape.get("angle", 0.0), 0.0)
            px, py = _rotate_point_xy(px, py, cx, cy, angle)
        ang = math.atan2((py - cy) / ry, (px - cx) / rx)
        qx, qy = cx + math.cos(ang) * rx, cy + math.sin(ang) * ry
        return math.hypot(px - qx, py - qy)
    if typ in ("spline", "polyline"):
        pts = [(_safe_float(x), _safe_float(y)) for x, y in (shape.get("points", []) or [])]
        if len(pts) < 2:
            return 999999.0
        seg_cps = shape.get("seg_cps", {}) or {}
        dists = []
        for seg_i, (a, b) in enumerate(zip(pts, pts[1:])):
            cp_data = seg_cps.get(str(seg_i), {})
            if cp_data and ("cp1" in cp_data or "cp2" in cp_data):
                # 对曲线段采样进行 hit 检测
                cp1 = cp_data.get("cp1") or [(a[0]*2+b[0])/3, (a[1]*2+b[1])/3]
                cp2 = cp_data.get("cp2") or [(a[0]+b[0]*2)/3, (a[1]+b[1]*2)/3]
                prev = a
                for k in range(1, 17):
                    t = k / 16; u = 1 - t
                    sx = u**3*a[0]+3*u**2*t*cp1[0]+3*u*t**2*cp2[0]+t**3*b[0]
                    sy = u**3*a[1]+3*u**2*t*cp1[1]+3*u*t**2*cp2[1]+t**3*b[1]
                    dists.append(_dist_point_to_segment(px, py, prev[0], prev[1], sx, sy))
                    prev = (sx, sy)
            else:
                dists.append(_dist_point_to_segment(px, py, a[0], a[1], b[0], b[1]))
        return min(dists) if dists else 999999.0
    if typ == "pattern_matrix":
        pts = _shape_points(shape, 9999)
        return min((math.hypot(px - x, py - y) for x, y in pts), default=999999.0)
    return 999999.0


def _rotate_point_xy(x: float, y: float, cx: float, cy: float, angle_deg: float) -> Tuple[float, float]:
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    dx, dy = float(x) - cx, float(y) - cy
    return cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a


def _shape_bounds(shape: dict) -> Tuple[float, float, float, float]:
    typ = shape.get("type", "line")
    if typ in ("circle", "ellipse"):
        cx, cy = _safe_float(shape.get("cx")), _safe_float(shape.get("cy"))
        rx, ry = abs(_safe_float(shape.get("rx"), 80.0)), abs(_safe_float(shape.get("ry"), 80.0))
        arc = _arc_span(shape) if typ == "circle" else None
        if arc is not None:
            pe = _arc_sample_points(cx, cy, rx, ry, arc[0], arc[1])
            xs, ys = [q[0] for q in pe], [q[1] for q in pe]
            return min(xs), min(ys), max(xs), max(ys)
        if typ == "ellipse" and abs(_safe_float(shape.get("angle", 0.0), 0.0)) > 0.001:
            pts = [_rotate_point_xy(cx + sx * rx, cy + sy * ry, cx, cy, _safe_float(shape.get("angle", 0.0), 0.0))
                   for sx, sy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return min(xs), min(ys), max(xs), max(ys)
        return cx - rx, cy - ry, cx + rx, cy + ry
    pts = []
    if typ == "pattern_matrix":
        pts = _shape_points(shape, 9999)
    elif typ == "spline":
        # 2026-08-09 用户实测「选中的图线拖不动」根因: spline 的 anchor 为空
        # → bounds 全 0 → body/bbox 判定失效。用密采样计算真实包围盒。
        pts = _shape_points(shape, 640)
    else:
        pts = [(x, y) for _, x, y in _shape_anchor_points(shape)]
    if not pts:
        return 0.0, 0.0, 0.0, 0.0
    xs, ys = [float(p[0]) for p in pts], [float(p[1]) for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _shape_body_contains(shape: dict, pos: QPointF, padding: float = 0.0) -> bool:
    px, py = pos.x(), pos.y()
    typ = shape.get("type", "line")
    if typ in ("circle", "ellipse"):
        cx, cy = _safe_float(shape.get("cx")), _safe_float(shape.get("cy"))
        rx = max(1.0, abs(_safe_float(shape.get("rx"), 80.0)) + float(padding or 0.0))
        ry = max(1.0, abs(_safe_float(shape.get("ry"), rx)) + float(padding or 0.0))
        angle = -_safe_float(shape.get("angle", 0.0), 0.0)
        if abs(angle) > 0.001:
            px, py = _rotate_point_xy(px, py, cx, cy, angle)
        return (((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2) <= 1.0
    if typ == "pattern_matrix":
        x1, y1, x2, y2 = _shape_bounds(shape)
        pad = max(8.0, float(padding or 0.0))
        return QRectF(x1 - pad, y1 - pad, (x2 - x1) + pad * 2.0, (y2 - y1) + pad * 2.0).contains(pos)
    return False


def _shape_center(shape: dict) -> Tuple[float, float]:
    x1, y1, x2, y2 = _shape_bounds(shape)
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _shape_layout_points(shape: dict) -> List[Tuple[float, float]]:
    """图形的簇代表点：包围盒四角 + 中心（组间距离的簇质心/包围盒计算用）。"""
    x1, y1, x2, y2 = _shape_bounds(shape)
    cx, cy = _shape_center(shape)
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (cx, cy)]


def _pattern_matrix_base_center(shape: dict) -> Tuple[float, float]:
    rows = shape.get("rows", []) or []
    x0, y0 = _safe_float(shape.get("x", 40), 40), _safe_float(shape.get("y", 40), 40)
    pts, y = [], y0
    for row in rows:
        n = max(0, int(row.get("count", 0)))
        dx, dy = _safe_float(row.get("dx", 40), 40), _safe_float(row.get("dy", 40), 40)
        offset = _safe_float(row.get("offset", 0), 0)
        for i in range(n):
            pts.append((x0 + offset + i * dx, y))
        y += dy
    if not pts:
        return x0, y0
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def _rotate_shape_around(shape: dict, cx: float, cy: float, angle_deg: float):
    """把图形绕任意点 (cx, cy) 旋转——选区旋转时图形随设备一起转。"""
    angle_deg = float(angle_deg or 0.0)
    if abs(angle_deg) < 0.001:
        return
    typ = shape.get("type", "line")
    cx, cy = float(cx), float(cy)
    if typ == "line":
        shape["x1"], shape["y1"] = _rotate_point_xy(_safe_float(shape.get("x1")), _safe_float(shape.get("y1")), cx, cy, angle_deg)
        shape["x2"], shape["y2"] = _rotate_point_xy(_safe_float(shape.get("x2")), _safe_float(shape.get("y2")), cx, cy, angle_deg)
    elif typ in ("circle", "ellipse"):
        shape["cx"], shape["cy"] = _rotate_point_xy(_safe_float(shape.get("cx")), _safe_float(shape.get("cy")), cx, cy, angle_deg)
        shape["angle"] = (_safe_float(shape.get("angle", 0.0), 0.0) + angle_deg) % 360.0
    elif typ == "polyline":
        shape["points"] = [_rotate_point_xy(float(x), float(y), cx, cy, angle_deg) for x, y in (shape.get("points", []) or [])]
        for cp_data in (shape.get("seg_cps", {}) or {}).values():
            for key in ("cp1", "cp2"):
                if key in cp_data:
                    cp_data[key] = list(_rotate_point_xy(float(cp_data[key][0]), float(cp_data[key][1]), cx, cy, angle_deg))
    elif typ == "pattern_matrix":
        old_x, old_y = _safe_float(shape.get("x", 40), 40), _safe_float(shape.get("y", 40), 40)
        base_cx, base_cy = _pattern_matrix_base_center(shape)
        new_cx, new_cy = _rotate_point_xy(base_cx, base_cy, cx, cy, angle_deg)
        shape["x"] = old_x + (new_cx - base_cx)
        shape["y"] = old_y + (new_cy - base_cy)
        shape["angle"] = (_safe_float(shape.get("angle", 0.0), 0.0) + angle_deg) % 360.0


def _normalize_angle_deg(angle: float) -> float:
    return ((float(angle) + 180.0) % 360.0) - 180.0


def _sync_base_angle_override(fid, base_angle, fixtures_by_id, fixture_defs_by_id):
    """per-fixture 同步基准特殊规则（2026-08-05 用户实测系列）。

    域25 ch469 数控基底电机（Multi Directional XYZ 3 轴）：+90° 后方向生效但
    整台 180° 镜像（其它往左它往右）→ 再转 180°（总计 +270° → 144.59°，
    一对一，仅该台）。门控: universe=25 且 start_channel=469 且 def 含
    motor_y，其余设备/页零变化。apply 按钮与拖拽刷新两条写基准路径共用，
    防路径间漂移（2026-08-05：刷新路径曾缺此规则导致拖拽后 180° 复现）。
    """
    fi = fixtures_by_id.get(str(fid))
    if fi is not None \
            and int(getattr(fi, "universe", 0) or 0) == 25 \
            and int(getattr(fi, "start_channel", 0) or 0) == 469:
        fd = fixture_defs_by_id.get(str(getattr(fi, "def_id", "")))
        chs = list(getattr(fd, "channels", []) or []) if fd is not None else []
        if "motor_y" in chs:
            return _normalize_angle_deg(base_angle + 270.0)
    return base_angle


def _solve_3x3(a: List[List[float]], b: List[float]) -> Optional[Tuple[float, float, float]]:
    m = [list(row) + [float(bi)] for row, bi in zip(a, b)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-9:
            return None
        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]
        div = m[col][col]
        for j in range(col, 4):
            m[col][j] /= div
        for r in range(3):
            if r == col:
                continue
            factor = m[r][col]
            if abs(factor) <= 1e-12:
                continue
            for j in range(col, 4):
                m[r][j] -= factor * m[col][j]
    return m[0][3], m[1][3], m[2][3]


def _fit_circle(points: List[Tuple[float, float]]) -> Optional[Tuple[float, float, float, float]]:
    if len(points) < 3:
        return None
    sx = sy = sx2 = sy2 = sxy = sx3 = sy3 = sx2y = sxy2 = 0.0
    for x, y in points:
        x = float(x); y = float(y)
        x2, y2 = x * x, y * y
        sx += x; sy += y; sx2 += x2; sy2 += y2; sxy += x * y
        sx3 += x2 * x; sy3 += y2 * y; sx2y += x2 * y; sxy2 += x * y2
    n = float(len(points))
    sol = _solve_3x3(
        [[sx2, sxy, sx], [sxy, sy2, sy], [sx, sy, n]],
        [-(sx3 + sxy2), -(sx2y + sy3), -(sx2 + sy2)],
    )
    if sol is None:
        return None
    d, e, f = sol
    cx, cy = -d / 2.0, -e / 2.0
    r2 = cx * cx + cy * cy - f
    if r2 <= 1.0:
        return None
    radius = math.sqrt(r2)
    errors = [abs(math.hypot(x - cx, y - cy) - radius) for x, y in points]
    rms = math.sqrt(sum(err * err for err in errors) / max(1, len(errors)))
    return cx, cy, radius, rms


def _drop_logical_device_to_shape(panel, shape, drag_logical_id) -> bool:
    """把"被拖动的包/组合设备"代表点吸附到图形最近点，成员按相对偏移整体跟随。

    ★ 2026-08-04 用户：包设备拖到弧线 = 代表点吸附弧线，成员按相对偏移整体跟随；
      与 gizmo 平移同语义写成员真实坐标。
    ★ R170c：吸附目标改为**最近点**（旧写法 `_shape_points(shape, 1)[0]` 跳到图形起点，
      与那里的设备重合 → 用户报"弧线少一个点"）。

    抽成模块级函数是为了压 `_finish_fixture_drop_to_shape` 的行数/CC 门禁。
    """
    for ld in panel.current_page_logical_devices():
        if str(ld.get("id", "")) != drag_logical_id:
            continue
        tx, ty = _shape_nearest_point(
            shape, float(ld.get("pos_x", 0.0)), float(ld.get("pos_y", 0.0)))
        dx = float(tx) - float(ld.get("pos_x", 0.0))
        dy = float(ty) - float(ld.get("pos_y", 0.0))
        ld["pos_x"], ld["pos_y"] = float(tx), float(ty)
        fix_by_id = panel._proj_fixture_map()
        for fid in ld.get("member_fixture_ids", []) or []:
            fi = fix_by_id.get(str(fid))
            if fi is None:
                continue
            try:
                fi.pos_x = float(getattr(fi, "pos_x", 0.0)) + dx
                fi.pos_y = float(getattr(fi, "pos_y", 0.0)) + dy
            except Exception:
                continue
        panel._pos_version = getattr(panel, "_pos_version", 0) + 1
        panel.layout_changed.emit()
        return True
    return False


def _flush_drag_deferred(view) -> None:
    """★ R194/NB-06：pan 分支最先 return——pan 与 shape 拖拽交叠且左键先松时，
    defer 提交在此补发（纯导航 pan 不 emit，避免误标脏工程）。（模块级：函数基线）"""
    if view._drag_shape_id or getattr(view, "_drag_layout_dirty", False):
        view._drag_layout_dirty = False
        view.panel.layout_changed.emit()


def _angular_span_deg(points: List[Tuple[float, float]], cx: float, cy: float) -> float:
    if len(points) < 2:
        return 0.0
    angles = sorted(math.atan2(y - cy, x - cx) for x, y in points)
    gaps = []
    for a, b in zip(angles, angles[1:]):
        gaps.append(b - a)
    gaps.append((angles[0] + math.tau) - angles[-1])
    largest_gap = max(gaps) if gaps else math.tau
    return math.degrees(max(0.0, math.tau - largest_gap))


def _page_shape_start_points(page: dict) -> set:
    """该页所有图形（含花样矩阵）的**首个采样点**集合（误吸附指纹）。"""
    starts = set()
    for sh in (page.get("cad_shapes") or []) + (page.get("pattern_matrices") or []):
        pts = sh.get("points") if isinstance(sh, dict) else None
        if not pts:
            continue
        try:
            starts.add((round(float(pts[0][0]), 6), round(float(pts[0][1]), 6)))
        except Exception:
            continue
    return starts


def _page_coord_pairs(page: dict, fixture_map: dict) -> List[Tuple[str, float, float, float, float]]:
    """`[(fid, 页面x, 页面y, 模型x, 模型y), …]`（页面坐标/模型坐标都读得出的那些）。"""
    out: List[Tuple[str, float, float, float, float]] = []
    fpos = page.get("fixtures")
    if not isinstance(fpos, dict):
        return out
    for fid, q in fpos.items():
        fi = fixture_map.get(str(fid))
        if fi is None or not isinstance(q, dict):
            continue
        try:
            out.append((str(fid), float(q.get("x", 0.0)), float(q.get("y", 0.0)),
                        float(getattr(fi, "pos_x", 0.0)),
                        float(getattr(fi, "pos_y", 0.0))))
        except Exception:
            continue
    return out


def _single_drop_points(shape, pos) -> List[Tuple[float, float]]:
    """单台设备拖到图形时的落点 = **图形上离光标最近的点**。

    ★★ R170c（用户实报"topview 二维页面弧线 42 台少一个点"）：旧写法取
      `_shape_points(shape, 1)[0]` = 图形**起点** → 设备瞬移到起点、与那里的设备
      完全重合（画面：弧线少一个点、起点多一个点）。真实工程实测指纹：
      `2-Axis Jet - 15m 21` 的页面坐标 float 精确等于弧线样条首个采样点。
    """
    if pos is None:
        return _shape_points(shape, 1)
    return [_shape_nearest_point(shape, float(pos.x()), float(pos.y()))]


def _page_is_mirroring(pairs) -> bool:
    """该页是否"页面坐标本就镜像模型坐标"（≥90% 一致；副本页/手工页 → False）。"""
    if len(pairs or ()) < 3:
        return False
    mirrored = sum(1 for _f, px, py, mx, my in pairs
                   if abs(px - mx) < 1e-6 and abs(py - my) < 1e-6)
    return mirrored >= len(pairs) * 0.9


def _heal_page_snap_coords(page: dict, fixture_map: dict) -> int:
    """修一页的误吸附坐标（判据见 `_heal_misplaced_snap_coords`）；返回修复台数。"""
    pairs = _page_coord_pairs(page, fixture_map)
    if not _page_is_mirroring(pairs):
        return 0                       # 故意重排过的页（副本页等）→ 不动
    starts = _page_shape_start_points(page)
    if not starts:
        return 0
    fpos = page["fixtures"]
    healed = 0
    for fid, px, py, mx, my in pairs:
        if abs(px - mx) < 1e-6 and abs(py - my) < 1e-6:
            continue                   # 本来就一致
        if (round(px, 6), round(py, 6)) not in starts:
            continue                   # 不是"误吸附到图形起点"的指纹 → 保留用户摆放
        fpos[fid] = {"x": float(mx), "y": float(my)}
        healed += 1
    return healed


def _heal_misplaced_snap_coords(panel) -> int:
    """修复历史遗留的**误吸附坐标**（把页面坐标还原成模型坐标）；返回修复台数。

    ★★ R170c 背景（用户实报"topview 二维页面弧线 42 个设备少 19/20 号的点"）：
      单台设备拖到图形上时旧代码取 `_shape_points(shape, 1)[0]` = 图形**起点**，
      设备被瞬移到图形起点、与原本在那里的设备完全重合 → 视觉上"少一个点"。
      代码已修（见 `_shape_nearest_point`），但**已经存进工程**的错误坐标要能自愈。

    判据（只修**可证明**是机器写坏、而不是用户摆的）：
      ① 页面坐标 **逐位等于** 某个图形的**首个采样点**（鼠标拖拽绝无可能精确落到
         float 完全相同的值上 —— 这是代码写坏的独有指纹）；
      ② 且该坐标**不等于**这台设备自己的模型坐标（否则本来就对，无需动）；
      ③ 且该页**绝大多数**设备的页面坐标都等于各自模型坐标（≥90%）—— 说明这页的
         页面坐标本就该镜像模型坐标；副本页（导入时整体旋转过，0% 镜像）**一律跳过**，
         绝不把用户有意摆放的副本页打回原形。
    """
    try:
        pages = getattr(panel, "_pages", None)
        fixture_map = panel._proj_fixture_map() if hasattr(panel, "_proj_fixture_map") else {}
    except Exception:
        return 0
    if not isinstance(pages, list) or not fixture_map:
        return 0
    healed = 0
    for page in pages:
        if isinstance(page, dict):
            healed += _heal_page_snap_coords(page, fixture_map)
    if healed:
        try:
            import logging as _lg
            _lg.getLogger(__name__).info(
                "[TopView] 自愈：%d 台设备的页面坐标曾被旧版'拖到图形'误吸附到图形起点，"
                "已按模型坐标还原（弧线不会再少点）", healed)
            panel._pos_version = getattr(panel, "_pos_version", 0) + 1
        except Exception:
            pass
    return healed


def _pca_axis(points: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    if len(points) < 2:
        return 1.0, 0.0, 0.0
    cx = sum(x for x, _ in points) / len(points)
    cy = sum(y for _, y in points) / len(points)
    sxx = sum((x - cx) * (x - cx) for x, _ in points) / len(points)
    syy = sum((y - cy) * (y - cy) for _, y in points) / len(points)
    sxy = sum((x - cx) * (y - cy) for x, y in points) / len(points)
    if abs(sxy) <= 1e-9 and abs(sxx - syy) <= 1e-9:
        return 1.0, 0.0, 1.0
    angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    vx, vy = math.cos(angle), math.sin(angle)
    major = (sxx + syy) / 2.0 + math.sqrt(((sxx - syy) / 2.0) ** 2 + sxy * sxy)
    minor = (sxx + syy) / 2.0 - math.sqrt(((sxx - syy) / 2.0) ** 2 + sxy * sxy)
    ratio = 0.0 if major <= 1e-9 else max(0.0, minor) / major
    if vx < 0.0 or (abs(vx) < 1e-9 and vy < 0.0):
        vx, vy = -vx, -vy
    return vx, vy, ratio


def _cluster_axis_values(values: List[float], tolerance: float = 6.0) -> List[float]:
    clusters: List[List[float]] = []
    for value in sorted(float(v) for v in values):
        if clusters and abs(value - (sum(clusters[-1]) / len(clusters[-1]))) <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _nearest_idx(vals: List[float], v: float) -> int:
    """vals（升序）中 |v - vals[i]| 最小者下标（并列取小下标）。

    与原 `min(range(len(vals)), key=lambda i: abs(v - vals[i]))` 逐位一致：
    bisect_left 定位首个 >= v 的位置，取 lo-1/lo 中更近者；并列（v 恰在
    两值中点）取 lo-1（原 min 返回先出现的较小下标）。比较为 IEEE 浮点
    减法，与 abs 全序等价。NaN 特例：v 为 NaN 时原实现全部键为 NaN，
    min 恒返回 0 —— 此处同样返回 0。
    """
    if v != v:
        return 0
    lo = bisect.bisect_left(vals, v)
    if lo == 0:
        return 0
    if lo == len(vals):
        return lo - 1
    a, b = vals[lo - 1], vals[lo]
    return lo - 1 if (v - a) <= (b - v) else lo


def _is_grid_like(points: List[Tuple[float, float]]) -> bool:
    if len(points) < 4:
        return False
    xs = _cluster_axis_values([p[0] for p in points])
    ys = _cluster_axis_values([p[1] for p in points])
    if len(xs) < 2 or len(ys) < 2:
        return False
    # NaN 轴线值：bisect 对 NaN 语义不同（NaN 视为 >= 一切），回退原
    # 逐下标扫描保逐位一致（真实工程坐标无 NaN，走快速路径）。
    if any(v != v for v in xs) or any(v != v for v in ys):
        cells = set()
        for x, y in points:
            xi = min(range(len(xs)), key=lambda i: abs(float(x) - xs[i]))
            yi = min(range(len(ys)), key=lambda i: abs(float(y) - ys[i]))
            cells.add((xi, yi))
        density = len(cells) / max(1, len(xs) * len(ys))
        return len(cells) == len(points) and density >= 0.65
    cells = set()
    for x, y in points:
        xi = _nearest_idx(xs, float(x))
        yi = _nearest_idx(ys, float(y))
        cells.add((xi, yi))
    density = len(cells) / max(1, len(xs) * len(ys))
    return len(cells) == len(points) and density >= 0.65


def _spline_cluster_angles(
    pts: Dict[str, Tuple[float, float]],
    splines: List[dict],
    ordered: List[str],
    return_assign: bool = False,
) -> Tuple[Dict[str, float], List[str]]:
    """每台设备归属最近且贴合（≤弧线跨度×1%）的弧线，取渲染样条解析切向。

    2026-08-03 用户实测：一页设备可沿多条弧线/星形排列（副池=星形+Spline 分支、
    落叶头=自绘环线），只取单条弧线会让其余分支投影错误「各自为战」。
    阶段 1 每台设备归「全局最近且贴合」的弧线（按弧线顺序逐个认领会让先到的
    弧线抢走本应属后续弧线的设备——至冬 6 条 Spline 2 分 z 层竞争）。
    阈值 1%：贴线设备（挂灯工具/沿弧线布置）距离≈0（底弧线 0.00005%、
    星形 ≤0.2%、上弧线 0.5%）；自由摆放/自绘环线设备（落叶头 2%+）退回。
    星形/整圆(闭合折线)与 Linear 模式走 spline_sample 折线分支（边方向角）。
    返回 (已归属角度(画布镜像 -atan2，与 RotY 一致), 未归属设备列表
    ——退回设备自拟合 Catmull-Rom)。return_assign=True 时追加第三元素
    {fid: (弧线序, 采样点 j)}——_compute_xy_reference_angles 的星形圆簇覆盖
    需要知道每台归属哪条弧线（2026-08-04 副池三维扇子专项）。

    性能（2026-08-03）：纯 Python 逐点全扫 4249 台×14 弧线×1201 采样点 15.5s
    → 包围盒±tol 预筛（盒外设备到任意采样点距离必 > span×1%，归属判定恒不
    成立，严格等价）+ numpy 批量最近点/折线投影（IEEE 双精度逐位一致，对照
    spline_cluster_baseline2.json 验证）。
    """
    from .geometry_splines import spline_sample
    import numpy as _np
    out: Dict[str, float] = {}
    rest = list(ordered)
    # 阶段 1：每台设备归「全局最近且贴合（≤跨度×1%）」的弧线
    assign: Dict[str, Tuple[int, int]] = {}  # fid -> (弧线序, 采样点 j)
    best_d2: Dict[str, float] = {}
    prepared: List[tuple] = []
    for sp in splines:
        sp_pts, sp_ang = spline_sample(sp["points"], mode=sp.get("mode", "cubic"))
        # 跨度 = 包围盒对角线（闭合折线首尾重合，用首尾距会得 0 而误跳过星形）
        spx = [p[0] for p in sp_pts]
        spz = [p[1] for p in sp_pts]
        span = math.hypot(max(spx) - min(spx), max(spz) - min(spz))
        has_nan = not all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in sp_pts)
        prepared.append((sp_pts, sp_ang, span,
                         min(spx), max(spx), min(spz), max(spz), has_nan))
    X = _np.asarray([pts[fid][0] for fid in rest], dtype=float)
    Z = _np.asarray([pts[fid][1] for fid in rest], dtype=float)
    for si, (sp_pts, _ang, span, bx0, bx1, bz0, bz1, has_nan) in enumerate(prepared):
        if span <= 1.0:
            continue
        tol = span * 0.01
        tol2 = tol * tol
        if has_nan:
            # 罕见 NaN/inf 采样点: 回退逐点全扫 (numpy argmin 对 NaN 语义不同)
            for idx in range(len(rest)):
                x, z = pts[rest[idx]]
                best = float("inf")
                j = 0
                for jj, (sx, sz) in enumerate(sp_pts):
                    d2 = (x - sx) * (x - sx) + (z - sz) * (z - sz)
                    if d2 < best:
                        best = d2
                        j = jj
                if best <= tol2 and best < best_d2.get(rest[idx], float("inf")):
                    best_d2[rest[idx]] = best
                    assign[rest[idx]] = (si, j)
            continue
        mask = ((X >= bx0 - tol) & (X <= bx1 + tol)
                & (Z >= bz0 - tol) & (Z <= bz1 + tol))
        idxs = _np.flatnonzero(mask)
        if idxs.size == 0:
            continue
        sx_arr = _np.asarray([p[0] for p in sp_pts], dtype=float)
        sz_arr = _np.asarray([p[1] for p in sp_pts], dtype=float)
        dx = X[idxs, None] - sx_arr[None, :]
        dz = Z[idxs, None] - sz_arr[None, :]
        d2 = dx * dx + dz * dz
        js = _np.argmin(d2, axis=1)
        bests = d2[_np.arange(idxs.size), js]
        for idx, best, jj in zip(idxs.tolist(), bests.tolist(), js.tolist()):
            fid = rest[idx]
            if best <= tol2 and best < best_d2.get(fid, float("inf")):
                best_d2[fid] = best
                assign[fid] = (si, jj)
    # 阶段 2：按弧线分组算角度（弧线序 = match 贴合度升序，先算最贴合的）
    for si in sorted({a[0] for a in assign.values()}):
        sp_pts, sp_ang, _span = prepared[si][0], prepared[si][1], prepared[si][2]
        hit = [(fid, j) for fid, (s2, j) in assign.items() if s2 == si]
        if len(hit) < 2:
            # 单台贴线也取该弧线切向（2026-08-03 agent F：上弧线 2 台贴线
            # 距离 1.9/2.2 却因无邻居被丢，兜底角偏差 43°+）
            for fid, j in hit:
                tx = math.cos(math.radians(sp_ang[j]))
                ty = math.sin(math.radians(sp_ang[j]))
                out[fid] = _normalize_angle_deg(
                    -math.degrees(math.atan2(ty, tx)))
                # 设备 RotY 恒在 [-90,+90]（+x 半球归一，agent
                # E/I 实测星形朝左段差 ~115°）：切向 >90° 时翻转 180°
                if out[fid] > 90.0:
                    out[fid] -= 180.0
                elif out[fid] < -90.0:
                    out[fid] += 180.0
                rest = [f for f in rest if f != fid]
            continue
        _o2t = _spline_order_ts([f for f, _ in hit], pts, sp_pts)
        hit_sorted = sorted(hit, key=lambda t: _o2t[t[0]])
        for idx, (fid, j) in enumerate(hit_sorted):
            tx = math.cos(math.radians(sp_ang[j]))
            ty = math.sin(math.radians(sp_ang[j]))
            prev_id = hit_sorted[idx - 1][0] if idx > 0 else None
            next_id = hit_sorted[idx + 1][0] if idx < len(hit_sorted) - 1 else None
            if prev_id is not None and next_id is not None:
                px, py = pts[prev_id]
                nx, ny = pts[next_id]
                ndx, ndy = nx - px, ny - py
            elif next_id is not None:
                nx, ny = pts[next_id]
                ndx, ndy = nx - pts[fid][0], ny - pts[fid][1]
            elif prev_id is not None:
                px, py = pts[prev_id]
                ndx, ndy = pts[fid][0] - px, pts[fid][1] - py
            else:
                ndx, ndy = tx, ty
            if tx * ndx + ty * ndy < 0.0:
                tx, ty = -tx, -ty
            # 画布 y = -世界 z (镜像): 画布角 = -世界角 — 与 RotY 一致
            out[fid] = _normalize_angle_deg(-math.degrees(math.atan2(ty, tx)))
            # 设备 RotY 恒在 [-90,+90]（+x 半球归一，agent E/I 实测
            # 星形朝左段差 ~115°）：闭合环/星形切向 >90° 时翻转 180°
            if out[fid] > 90.0:
                out[fid] -= 180.0
            elif out[fid] < -90.0:
                out[fid] += 180.0
        rest = [f for f in rest if f not in out]
        if not rest:
            break
    if return_assign:
        return out, rest, assign
    return out, rest


def _is_true_star_spline(sp: dict) -> bool:
    """真 TStarSpline（10 顶点内外交替的星形）判定。

    ★★ 2026-09-17 用户实报修复（三维扇子内圈两台同步后全部电机反向）：
      旧实现以 `len(sp_pts) >= 11`（采样/控制点总数）做代理，而 **Circle Spline
      有 60 个控制点** → 整圆页也进星形臂分支，把「设备→最近外尖」的臂方向
      套到圆周点（圆的"内外顶点"只是圆周上的点）→ 切线被替换成任意弦向、
      部分设备翻 180°。实测该工程 15 条样条**全部**误触发。
      统一判据沿用本仓库既有口径（geometry_splines.py:1460/1580 同款）：
      真星形 = `star` 且**无 mode**（Star Spline 无 mode；Circle Spline 有
      mode="linear"）。
    """
    return bool(sp.get("star")) and "mode" not in sp


def _star_arm_geometry(sp: dict, radius: float):
    """→ (verts, use_arm, arm_tol)：星形顶点（仅真星形非空）、是否用臂方向、臂容差。"""
    sp_pts = sp.get("points") or []
    verts = sp_pts[:10] if (_is_true_star_spline(sp) and len(sp_pts) >= 11) else []
    return verts, len(verts) == 10, max(12.0, radius * 0.02)


def _circle_or_arm_tangent(x: float, y: float, center: tuple,
                           verts, arm_tol: float):
    """圆切线 (tx,ty)；真星形且在臂容差内时改为该臂方向（画布分量 (vx,-vz)）。"""
    cx, cy = center
    tx, ty = -(y - cy), x - cx
    if not verts:
        return tx, ty
    k = min(range(5), key=lambda i: (
        (x - verts[2 * i][0]) ** 2 + (y - verts[2 * i][1]) ** 2))
    if math.hypot(x - verts[2 * k][0], y - verts[2 * k][1]) > arm_tol:
        return tx, ty
    i_in = 2 * ((-k) % 5) + 1
    i_out = 2 * ((1 - k) % 5)
    vx = verts[i_out][0] - verts[i_in][0]
    vz = verts[i_out][1] - verts[i_in][1]
    if abs(vx) > 1e-9 or abs(vz) > 1e-9:
        return vx, -vz
    return tx, ty


def _star_circle_override(
    pts: Dict[str, Tuple[float, float]],
    assign: Dict[str, Tuple[int, int]],
    splines: List[dict],
) -> Dict[str, float]:
    """星形样条外顶点圆簇 → 圆切向覆盖（2026-08-04 用户授权「自动加圆」）。

    副池三维扇子 10 台（两套各 5 台 XY 散台）：恰好位于 TStarSpline 的
    5 个外顶点上（72° 等距、整簇圆拟合 rms≈0.00），但设备摆放沿**圆切向**
    （RotY ≈ 圆切向 ±1~8°），星形折线切向落后 72° → 同步基准角错 72°
    （2026-08-04 实测中位 72°）。对「整簇归属同一颗星形样条且紧贴一个圆」
    的设备簇，覆盖为圆切向（镜像 -atan2 + 归一，与弧线分支同款）。

    门控与现有圆拟合分支相同（_compute_xy_reference_angles 2897 行）：
    跨度 ≥ 25° + 最小半径 ≥ max(5, r×0.35) + 残差 rms ≤ max(6, r×0.08)。
    只处理 star=True 的样条——TSpline3D 弧线（底弧线/上弧线/副池弧线/百变
    等）不受影响，零变化。副池气爆/大气爆的星形边沿+内外顶点混合簇拟合
    残差大（rms≈300~800）→ 门控拒绝，星形切向保持不变。
    """
    out: Dict[str, float] = {}
    # ★★ 2026-09-17 用户实报修复（三维扇子内圈两台同步后全部电机反向）：
    #   门控必须是**真星形**（`star` 且无 `mode`）——`parse_splines` 给
    #   **Circle Spline 也打了 star=True**（geometry_splines.py:357），旧门控
    #   `sp.get("star")` 于是把整圆页也纳入覆盖：重拟合圆 + 用「设备→最近
    #   外尖」的圆切线替换掉 `_spline_cluster_angles` 已算对的弧线切向。
    #   实测水仙十字内圈 12 台：seq125/131（恰在世界 ±X 轴上、切线≈±90°）
    #   被替换成反 180° 的代表值（base −89.97/+89.97 vs 安装 +88.47/−88.47
    #   → 偏差 ±178.44°），sync 后该两台全部电机反向；其余 10 台偏差 <3°。
    #   真星形判据与 `_star_arm_geometry` 共用同一函数（单点定义）。
    star_idx = {si for si, sp in enumerate(splines) if _is_true_star_spline(sp)}
    if not star_idx or not assign:
        return out
    groups: Dict[int, List[str]] = {}
    for fid, (si, _j) in assign.items():
        if si in star_idx:
            groups.setdefault(si, []).append(fid)
    for si, fids in groups.items():
        if len(fids) < 3:
            continue
        point_list = [pts[fid] for fid in fids]
        fit = _fit_circle(point_list)
        if fit is None:
            continue
        cx, cy, radius, rms = fit
        span = _angular_span_deg(point_list, cx, cy)
        min_radius = min((math.hypot(x - cx, y - cy) for x, y in point_list),
                         default=0.0)
        if not (span >= 25.0 and min_radius >= max(5.0, radius * 0.35)
                and rms <= max(6.0, radius * 0.08)):
            continue
        # 按角度排序取邻居弦向判向（与圆拟合分支同款，对任意选择序稳健）
        angle_order = sorted(
            fids, key=lambda fid: math.atan2(pts[fid][1] - cy, pts[fid][0] - cx))
        ordered_index = {fid: idx for idx, fid in enumerate(angle_order)}
        # ★ 星形臂方向（2026-08-04 副池三维扇子专项）：设备精确位于 TStarSpline
        #   外尖顶点（实测距离 0.0），真实安装方向 = 星形臂（内凹→外尖，画布角
        #   = RotY 约定）而非圆切向——圆切向与臂方向差套内常数偏置（第1套
        #   −8.27° 来自内/外半径比 300/600，第2套 −1.17° = 600/1500），用户
        #   实测「向左偏倒」= 该偏置（X1 报告线索「每套 RotY = 圆切向 + 套内
        #   常数偏置，来自星形内/外半径比」）。修复：外尖设备改取臂方向
        #   （内凹((−k)mod5) → 外尖((1−k)mod5)，画布分量 (vx, −vz)，判向/
        #   钳位逻辑不变）；设备不在外尖上（距离 > 阈值）回退圆切向；TCircle
        #   Spline（points 数 ≠ 10）恒回退，零影响。
        verts, use_arm, arm_tol = _star_arm_geometry(splines[si], radius)
        for fid in fids:
            idx = ordered_index[fid]
            x, y = pts[fid]
            tx, ty = _circle_or_arm_tangent(
                x, y, (cx, cy), verts if use_arm else None, arm_tol)
            if abs(tx) <= 1e-9 and abs(ty) <= 1e-9:
                tx, ty = 1.0, 0.0
            prev_id = angle_order[(idx - 1) % len(angle_order)]
            next_id = angle_order[(idx + 1) % len(angle_order)]
            px, py = pts[prev_id]
            nx, ny = pts[next_id]
            ndx, ndy = nx - px, ny - py
            if tx * ndx + ty * ndy < 0.0:
                tx, ty = -tx, -ty
            ang = _normalize_angle_deg(-math.degrees(math.atan2(ty, tx)))
            # 设备 RotY 恒在 [-90,+90]（+x 半球归一，与 CR/弧线同款）
            if ang > 90.0:
                ang -= 180.0
            elif ang < -90.0:
                ang += 180.0
            out[fid] = ang
    return out


def _catmull_rom_tangent(
    pts: Dict[str, Tuple[float, float]],
    ordered: List[str],
    idx: int,
    ts: List[float],
    closed: bool = False,
) -> Tuple[float, float]:
    """弦长参数化 Catmull-Rom 导数（过每台设备的平滑曲线切向，2026-08-03）。

    内部点用中心差分 (P[i+1]-P[i-1])/(t[i+1]-t[i-1])，端点用单向差分——
    对不均匀分布（弧线设备间距不一）比均匀 Catmull-Rom/折线线段方向更平滑，
    是设备定位点拟合平滑弧线的安装切向（用户指正：折线≠弧线）。
    closed=True（闭合环，2026-08-03 agent E：落叶头=平滑闭合环线）：
    邻居回绕 (idx±1) mod n，首尾设备同样取中心差分。
    """
    n = len(ordered)
    if n <= 1:
        return (1.0, 0.0)
    if closed:
        xp, yp = pts[ordered[(idx - 1) % n]]
        xn, yn = pts[ordered[(idx + 1) % n]]
        return (xn - xp, yn - yp)
    if idx <= 0:
        x0, y0 = pts[ordered[0]]
        x1, y1 = pts[ordered[1]]
        dx, dy = x1 - x0, y1 - y0
    elif idx >= n - 1:
        x0, y0 = pts[ordered[n - 2]]
        x1, y1 = pts[ordered[n - 1]]
        dx, dy = x1 - x0, y1 - y0
    else:
        dt = max(1e-9, ts[idx + 1] - ts[idx - 1])
        xp, yp = pts[ordered[idx - 1]]
        xn, yn = pts[ordered[idx + 1]]
        dx, dy = (xn - xp) / dt, (yn - yp) / dt
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return (1.0, 0.0)
    return (dx / length, dy / length)


# ── 落叶头双翼 P2 修复（2026-08-04，探针 _ui_validation/_tmp_leaf_p2_final.py
#    固化为规范实现，本处为生产接入，数值逐行对拍）────────────────────
#    双翼（vesica）zigzag 交错选择序下：±1 邻居 = 对侧翼方向，±2 邻居 =
#    同侧翼方向——现状 CR 自拟合用 ±1 邻居弦向，顶部收拢区方向差 19°-147°。
#    门控：每台 t1/t2 均存在且 circ_mod180(t1+t2) < 20° 为命中，命中占比
#    ≥75% 才启用（V4 实测：落叶头 91%/气爆 93%，百变 35% 不启用）。


def _leaf_norm180(a: float) -> float:
    """归一到 [-180, +180]。"""
    return (a + 180.0) % 360.0 - 180.0


def _leaf_clamp90(a: float) -> float:
    """设备 RotY 恒在 [-90,+90]（+x 半球归一）：翻转 180° 入域。"""
    if a > 90.0:
        a -= 180.0
    elif a < -90.0:
        a += 180.0
    return a


def _leaf_circ_mod180(x: float) -> float:
    """到 180° 整数倍的圆距离 [0,90]：|x| 模 180 = min(x%180, 180-x%180)。"""
    d = x % 180.0
    return min(d, 180.0 - d)


def _leaf_tangent_raw(pts: Dict[str, Tuple[float, float]],
                      ordered: List[str], idx: int, step: int):
    """step 邻居弦向（世界系）→ 原始画布角（-atan2，不钳位）。越界返回 None。"""
    n = len(ordered)
    a = idx - step
    b = idx + step
    if a < 0 or b >= n:
        return None
    ax, az = pts[ordered[a]]
    bx, bz = pts[ordered[b]]
    return _leaf_norm180(-math.degrees(math.atan2(bz - az, bx - ax)))


def _leaf_p2_gate_ratio(pts: Dict[str, Tuple[float, float]],
                        ordered: List[str]) -> Tuple[float, int, int]:
    """镜像签名门控。返回 (ratio, hits, total)。

    每台命中 = t1、t2 均存在且 circ_mod180(t1+t2) < 20°；占比分母 = 全部
    CR 设备台数（端点无双候选不命中）。V4 实测：落叶头 91%/气爆 93%/百变 35%。
    """
    hits, total = 0, len(ordered)
    for idx in range(total):
        t1 = _leaf_tangent_raw(pts, ordered, idx, 1)
        t2 = _leaf_tangent_raw(pts, ordered, idx, 2)
        if t1 is None or t2 is None:
            continue
        if _leaf_circ_mod180(t1 + t2) < 20.0:
            hits += 1
    ratio = hits / total if total else 0.0
    return ratio, hits, total


def _leaf_wing_tangent(pts: Dict[str, Tuple[float, float]],
                       ordered: List[str], pos: Dict[int, Tuple[int, List[int]]],
                       idx: int):
    """翼内前后邻居弦 → 原始画布角（-atan2，不钳位）。端点用翼内单侧段弦。

    pos = _leaf_p2_apply 内按 x 分翼构建的 (翼内下标, 翼序列) 映射；翼序列 =
    投影序过滤（保持 zigzag 同序），翼内 prev/next = 同翼前后设备（两端都有则
    prev→next 弦，端点退化单侧段弦）。无翼内邻居返回 None。
    """
    k, seq = pos[idx]
    prev_i = seq[k - 1] if k > 0 else None
    next_i = seq[k + 1] if k < len(seq) - 1 else None
    if prev_i is not None and next_i is not None:
        ax, az = pts[ordered[prev_i]]
        bx, bz = pts[ordered[next_i]]
    elif next_i is not None:
        ax, az = pts[ordered[idx]]
        bx, bz = pts[ordered[next_i]]
    elif prev_i is not None:
        ax, az = pts[ordered[prev_i]]
        bx, bz = pts[ordered[idx]]
    else:
        return None
    return _leaf_norm180(-math.degrees(math.atan2(bz - az, bx - ax)))


def _leaf_slope_out(raw: float, is_left: bool) -> float:
    """斜率公式（A7 建议，翼内弦 → RotY）：slope = |90-|raw||。

    约定 = 两翼向叶内收拢、倾角 = 叶轮廓坡度：左翼 out = slope-90，
    右翼 out = 90-slope（探针 _tmp_leaf_p2_residual.py 的 wing_local_estimate
    逐位同式）。
    """
    slope = abs(90.0 - abs(raw))
    return slope - 90.0 if is_left else 90.0 - slope


def _closed_leaf_spline_fit(
    values: List[Tuple[float, float]],
    splines: List[dict],
) -> bool:
    """点集是否贴「闭合非星形样条」（vesica 叶形, 2026-08-05 落叶头子集门控）。

    判定（首尾距 <50 与 _arc_tip_direction_fix 同阈值）:
      1) 存在闭合且非星形样条;
      2) 点集包围盒 ⊆ 样条包围盒（+跨度×2% 容差——样条控制点与设备极值存在
         <1 单位浮点差, 落叶头自身盒超出样条盒 <1）;
      3) 中位距 ≤ 跨度×10%（多数设备真贴叶形）。
    至冬 7.0 实测: 落叶头/落叶头气爆命中; 上弧线（盒探出样条盒 ~6000）/
    底弧线（中位 4396）/百变（盒探出）/副池（z>20000 远在盒外）恒 False
    → 底弧线/上弧线/百变/副池零变化。异常返回 False。
    """
    try:
        if not values or not splines or len(values) < 4:
            return False
        xs = [p[0] for p in values]
        zs = [p[1] for p in values]
        dmin_x, dmin_z, dmax_x, dmax_z = min(xs), min(zs), max(xs), max(zs)
        for sp in splines:
            sp_pts = sp.get("points") or []
            if len(sp_pts) < 2 or sp.get("star"):
                continue
            if math.hypot(sp_pts[-1][0] - sp_pts[0][0],
                          sp_pts[-1][1] - sp_pts[0][1]) >= 50.0:
                continue
            sx = [p[0] for p in sp_pts]
            sz = [p[1] for p in sp_pts]
            smin_x, smax_x = min(sx), max(sx)
            smin_z, smax_z = min(sz), max(sz)
            span = math.hypot(smax_x - smin_x, smax_z - smin_z)
            if span <= 1.0:
                continue
            tol = span * 0.02
            if dmin_x < smin_x - tol or dmax_x > smax_x + tol \
                    or dmin_z < smin_z - tol or dmax_z > smax_z + tol:
                continue
            thr = span * 0.10
            ds = []
            for x, z in values:
                best = float("inf")
                for k in range(len(sp_pts) - 1):
                    ax, az = sp_pts[k]
                    bx, bz = sp_pts[k + 1]
                    vx, vz = bx - ax, bz - az
                    seg2 = vx * vx + vz * vz
                    if seg2 <= 1e-12:
                        continue
                    t = max(0.0, min(1.0, ((x - ax) * vx + (z - az) * vz) / seg2))
                    best = min(best, math.hypot(x - (ax + vx * t),
                                                z - (az + vz * t)))
                ds.append(best if best < float("inf") else 0.0)
            ds.sort()
            if ds[len(ds) // 2] <= thr:
                return True
        return False
    except Exception:
        return False


def _leaf_closed_spline_page(pts: Dict[str, Tuple[float, float]],
                             splines: List[dict]) -> bool:
    """整页设备点集是否匹配闭合非星形样条（F4/F5 子集统一化门控）。

    落叶头/落叶头气爆页（vesica 叶形）True; 上弧线/底弧线/百变/副池页 False
    → 非叶形页子集零变化。异常返回 False。
    """
    if not pts:
        return False
    return _closed_leaf_spline_fit(list(pts.values()), splines)


def _leaf_subset_direction_gate(
    pts: Dict[str, Tuple[float, float]],
    ordered: List[str],
    angles: Dict[str, float],
) -> Dict[str, float]:
    """落叶头子集方向门（2026-08-05 F2-L2, 仅子集页级回退分支调用）。

    子集 CR 自拟合的 PCA 序在顶部中心区左右翼交错（顶部 16 台的 z≈-10288
    两台互为序邻居 → 弦向近水平 → base ±13.4 vs 安装 ±35.5, 差 22° →
    推 X/Y 世界偏 22°、Z 扇面斜 22°）。逐台最近邻弦向（排除退化 <5 的
    尖点对——锚点重合, 与 _arc_tip_direction_fix 同保护）vs 当前 base 的
    无向线夹角 >15° → 用翼内弦斜率公式（P2 同款 _leaf_slope_out, 翼别按
    x<组中心）。最近邻 = 同翼贴邻设备（翼方向 = 安装朝向）; 其余设备
    弦向≈base 不触发。非回退分支不调用 → 全选/下方57/底弧线等零变化。
    异常返回原角度（防御）。
    """
    if not pts or not ordered or not angles:
        return angles
    try:
        cx = sum(pts[f][0] for f in ordered) / len(ordered)
        out = dict(angles)
        for fid in ordered:
            if fid not in out:
                continue
            x, z = pts[fid]
            best_f, best_d2 = None, float("inf")
            for f2 in ordered:
                if f2 == fid:
                    continue
                d2 = (pts[f2][0] - x) * (pts[f2][0] - x) \
                    + (pts[f2][1] - z) * (pts[f2][1] - z)
                if d2 < best_d2:
                    best_d2, best_f = d2, f2
            if best_f is None or math.sqrt(best_d2) < 5.0:
                continue
            raw = _leaf_norm180(-math.degrees(
                math.atan2(pts[best_f][1] - z, pts[best_f][0] - x)))
            base = float(out.get(fid, 0.0))
            a1 = abs(_leaf_norm180(base))
            a2 = abs(_leaf_norm180(raw))
            if a1 > 90.0:
                a1 = 180.0 - a1
            if a2 > 90.0:
                a2 = 180.0 - a2
            if abs(a1 - a2) > 15.0:
                out[fid] = _leaf_slope_out(raw, pts[fid][0] < cx)
        return out
    except Exception:
        return angles


def _top_motor_align(
    angles: Dict[str, float],
    pts: Dict[str, Tuple[float, float]],
    ids: List[str],
    leaf_sub: List[str],
    leaf_targets: set,
) -> Dict[str, float]:
    """落叶头顶部电机同步基准硬修正（2026-08-05 用户实测 90° 错位, 一对一）。

    用户 GUI 实测（权威）：落叶头顶部电机推 X 做成前后、推 Y 做成左右、
    且两翼互为反向——右翼顶部设备相对底部整体旋转 90°（朝前）、左翼顶部
    再反向（朝后）；底部设备正确。数据实证（至冬7.0 原始 project.xml）:
    顶部 22 台（叶尖 2 + 倒 V 20, world_z 降序）为**顶层 Object（RotZ=
    ±180 反装, RotY=±35.5~55.6° = 排列切向）**, 其余 51 台（wz ≤ −13041）
    嵌 Multi Directional Drive 机构（RotZ≈0, 排列切向与有效朝向一致,
    底部 ±83~86° 组合正确）——同一 def（全能卡一维 360°）下反装设备的
    有效安装朝向 = 排列切向 ± 90°（右翼 +90、左翼 −90）→ 引擎「基准 =
    排列切向」模型在顶部系统性偏 90° 且两翼镜像。硬修正: 右翼基准 +90°
    摆正、左翼 +90° 再翻转（+180°）= 基准 −90°, 与底部全朝右对齐
    （推 X 世界 ≈0°、推 Y ≈90°, 两翼同向）。门控: 仅「整页叶形基准
    计算」（全选 73 / 子集回退递归, len(leaf_targets) >= len(ids)）且设备
    ∈ 顶部 22 台时生效; 非叶形页（底弧线/上弧线/副池/百变）leaf_targets
    恒空 → False 零变化; 叶形页其余 51 台不在顶部 22 台零变化。异常返回
    原角度（防御）。
    """
    if not angles or not pts or not leaf_sub or not leaf_targets or not ids:
        return angles
    if len(leaf_targets) < len(ids):
        return angles
    try:
        fids = [str(f) for f in ids]
        top_ids = sorted(fids, key=lambda f: pts[f][1])[-22:]
        if len(top_ids) < 2:
            return angles
        out = dict(angles)
        for fid in top_ids:
            if fid not in out:
                continue
            base = float(out[fid] or 0.0)
            # ★ 翼判定用基准符号（右翼基准恒 +35.5~55.6、左翼恒 −35.5~−55.6,
            #   至冬7.0 实测 22/22 台干净分离; 尖点对 anchor 偏移修正后的 pts.x
            #   会误分翼——尖 2 台 offset 不同, pts.x 可能跨过组中心）;
            #   不用 pts.x < cx（2026-08-05 实测尖点右台被误判左翼 −90°）。
            if base < 0.0:
                # 左翼: +90° 再翻转 = −90°（与右翼同向、不镜像）
                out[fid] = _normalize_angle_deg(base - 90.0)
            else:
                # 右翼: +90° 摆正
                out[fid] = _normalize_angle_deg(base + 90.0)
        return out
    except Exception:
        return angles


def _subset_on_closed_leaf_spline(
    pts: Dict[str, Tuple[float, float]],
    ordered: List[str],
    splines: List[dict],
) -> bool:
    """子集点集是否落在闭合非星形样条上（F3 圆拟合排除门控）。

    落叶头顶部 16 台子集: 子集盒 ⊆ 叶形样条盒 + 中位距 1218 ≤ 跨度×10%
    → True → 排除圆拟合走 CR; 副池真圆 10 台（z>20000 在盒外）/底弧线
    （中位 4396 超阈值）恒 False → 圆拟合路径逐字节不变。异常返回 False。
    """
    if not pts or not ordered:
        return False
    return _closed_leaf_spline_fit([pts[fid] for fid in ordered], splines)


def _arc_tip_direction_fix(
    pts: Dict[str, Tuple[float, float]],
    out_arc: Dict[str, float],
    ordered: List[str],
    off_by_fid: Dict[str, Tuple[float, float]],
    assign: Dict[str, Tuple[int, int]],
    splines: List[dict],
) -> Dict[str, float]:
    """弧线归属尖点设备方向修复（2026-08-04 落叶头头部方向不一致）。

    门控: 只处理「闭合且非星形」样条（vesica 双翼叶形, 至冬 7.0 的 7 点
    Spline 首尾重合）的弧线归属设备——开放弧线（底弧线/上弧线/副池/百变
    的 Spline 2 等）与星形样条（副池星形）切向与设备摆放一致, 零触发零变化。
    对门控内设备: 样条切向 vs 最近邻居弦的无向线夹角 ≤15° = 正常贴线
    设备保持原样; >15° = 尖点（vesica 两尖汇聚, 样条尖点处切向与设备实际
    摆放方向系统性不符 19°-114°, 2026-08-04 用户实测「落叶头头部（顶部
    叶尖区）同步方向与底部正确方向不一致」）改用翼内弦斜率公式（P2 同款
    _leaf_slope_out）: slope=|90-|raw||, 左翼=slope-90, 右翼=90-slope。
    翼别按基底电机 local_offsets 的 x 符号（至冬 7.0 实测 73/73 台与
    rot_y 符号相反: offx>0=左翼/offx<0=右翼）——两尖汇聚处两台设备锚点
    重叠, x<cx 分翼失效。无 off 回退 x<组中心。邻居 = 欧氏最近, 排除
    同弧线归属且距离 <300 的设备（尖点对: 落叶头两台锚点重合、气爆两台
    x 差 197; 翼内邻居（翼尾）距离 ~400 不受影响）。
    ★ 2026-08-04 用户实测「顶部倒 V 整体方向错 90°（左右/前后互换）」根因:
    尖点对（锚点重合）的 offx 符号与翼**反向**——两包共享锚点、基底电机
    分居锚点两侧（左翼基底在锚点左、右翼基底在锚点右），offx>0=左翼规则
    在此反向 → 顶部两尖 base=±35.5 镜像互换（各 70.8-90° 错位）。修复:
    有尖点对时翼判定改用两成员**有效位置 x 比较**（pts.x + offx/scale 世界
    换算, 无 off 用 pts.x）——左翼基底 x<右翼基底 x, 与 RotY 符号一致;
    底部叶基尖单台（无对）维持 offx 规则（实测 offx<0=右翼正确）。
    实测（至冬 7.0）: 落叶头顶部叶尖 19.1°/90.0°→0.0°/0.0°、底部叶基尖
    43.9°→4.2°, 73 台 <5° 69→72（98.6% ≥96%）; 底弧线/上弧线/副池
    0 台触发零变化; 气爆/百变同结构尖点同步修复（<5° 各 +4/+2）。
    返回 {fid: 新同步角}, 未触发设备不在返回中。
    """
    if not out_arc or not ordered:
        return {}
    cx = sum(pts[f][0] for f in ordered) / len(ordered)
    fix: Dict[str, float] = {}
    for fid in out_arc:
        my_assign = assign.get(fid)
        if my_assign is None:
            continue
        si = my_assign[0]
        if si >= len(splines):
            continue
        sp = splines[si]
        sp_pts = sp.get("points") or []
        if len(sp_pts) < 2 or sp.get("star"):
            continue
        # 门控: 闭合非星形（vesica 叶形样条）
        if math.hypot(sp_pts[-1][0] - sp_pts[0][0],
                      sp_pts[-1][1] - sp_pts[0][1]) >= 50.0:
            continue
        x, z = pts[fid]
        # ★ 尖点对探测（同弧线近距, 与邻居排除阈值一致 <300; 遍历 out_arc——
        #   ordered 是 CR 退回集, 尖点对成员在 out_arc 内扫不到）。
        pair_mate: Optional[str] = None
        for f2 in out_arc:
            if f2 == fid:
                continue
            a2 = assign.get(f2)
            if a2 is not None and a2[0] == si:
                d2 = (pts[f2][0] - x) * (pts[f2][0] - x) \
                    + (pts[f2][1] - z) * (pts[f2][1] - z)
                if d2 < 300.0 * 300.0:
                    pair_mate = f2
                    break
        best_f2, best_d = None, float("inf")
        for f2 in ordered:
            if f2 == fid:
                continue
            x2, z2 = pts[f2]
            d2 = (x2 - x) * (x2 - x) + (z2 - z) * (z2 - z)
            a2 = assign.get(f2)
            if a2 is not None and a2[0] == si and d2 < 300.0 * 300.0:
                continue  # 同弧线近距重叠簇（尖点对）
            if d2 < best_d:
                best_d, best_f2 = d2, f2
        if best_f2 is None:
            continue
        raw = _leaf_norm180(-math.degrees(
            math.atan2(pts[best_f2][1] - z, pts[best_f2][0] - x)))
        # 无向线夹角（0-90°）: 切向与翼内弦一致 = 正常贴线设备
        a1 = abs(_leaf_norm180(out_arc[fid]))
        a2 = abs(_leaf_norm180(raw))
        if a1 > 90.0:
            a1 = 180.0 - a1
        if a2 > 90.0:
            a2 = 180.0 - a2
        if abs(a1 - a2) <= 15.0:
            continue
        off = off_by_fid.get(str(fid))
        if pair_mate is not None:
            # 尖点对翼判定: 有效位置 x 比较（锚点重合, offx 符号与翼反向）
            try:
                from .import_patch_csv import DEFAULT_IMPORT_SCALE
                _off = off_by_fid.get(str(fid))
                _moff = off_by_fid.get(str(pair_mate))
                my_x = x + (float(_off[0]) / DEFAULT_IMPORT_SCALE
                            if _off else 0.0)
                mx, _mz = pts[pair_mate]
                mate_x = mx + (float(_moff[0]) / DEFAULT_IMPORT_SCALE
                               if _moff else 0.0)
                is_left = my_x < mate_x
            except Exception:
                is_left = (float(off[0]) > 0.0) if off else (x < cx)
        else:
            is_left = (float(off[0]) > 0.0) if off else (x < cx)
        fix[fid] = _leaf_slope_out(raw, is_left)
    return fix


def _leaf_p2_apply(pts: Dict[str, Tuple[float, float]],
                   ordered: List[str]) -> Dict[str, float]:
    """P2 逐台修复（假定门控已通过；未通过时调用方不得使用）。

    返回 {fid: sync}。sync = 画布镜像角（与 RotY 同一约定, [-90,+90]）。
    P2 残余修复（2026-08-04, A7 诊断 20 台，探针 _tmp_leaf_p2_residual.py
    分类）：仅三条断裂分支替换为「翼内弦 + 斜率公式」，正常配对分支（含在域
    选择）与门控逐位保持 P2 原状——纯全翼局部化会把落叶头 idx44 从 2.2° 打成
    9.2°，故只在断裂分支替换：
      建议1 端点回退（t1/t2 缺失）→ 翼内单侧段弦 + 斜率公式；
      建议2 b1 配对滑移（±2 邻居不同翼）→ 翼内前后邻居弦 + 斜率公式；
      建议3 b2 翼尖拐点（t1 在域、t2 出域）→ 斜率公式处理 t2 而非跨翼 t1。
    """
    out: Dict[str, float] = {}
    n = len(ordered)
    if n == 0:
        return out
    cx = sum(pts[f][0] for f in ordered) / n   # 组中心 x（翼判定基准）
    # 翼内序列：按 x 分翼（左翼 x<cx），投影序过滤保持 zigzag 同序
    left = [j for j in range(n) if pts[ordered[j]][0] < cx]
    right = [j for j in range(n) if pts[ordered[j]][0] >= cx]
    pos: Dict[int, Tuple[int, List[int]]] = {}
    for k, j in enumerate(left):
        pos[j] = (k, left)
    for k, j in enumerate(right):
        pos[j] = (k, right)
    for idx, fid in enumerate(ordered):
        t1 = _leaf_tangent_raw(pts, ordered, idx, 1)
        t2 = _leaf_tangent_raw(pts, ordered, idx, 2)
        is_left = pts[fid][0] < cx
        # ── 建议1：端点无 ±2 邻居 → 翼内单侧段弦 + 斜率公式（原回退保留兜底）──
        if t1 is None or t2 is None:
            raw = _leaf_wing_tangent(pts, ordered, pos, idx)
            if raw is not None:
                out[fid] = _leaf_slope_out(raw, is_left)
            else:
                out[fid] = _leaf_clamp90(t1) if t1 is not None else 0.0
            continue
        # ── 建议2：±2 邻居不同翼（zigzag 交错错位配对滑移）→ 仅当楼梯弦 t2 与
        #    翼内弦斜率严重分歧（最近圆差 ≥15°）时换翼内弦 + 斜率公式（t2 方向
        #    全错，实测分歧 23°-174°）；分歧小 = t2 可信（气爆 idx92 分歧 6.6°），
        #    落回原 P2 在域选择逐位不变（无条件替换会把它从 13.9° 打成 20.6°）──
        #    注：分歧用最近圆差（0-180°）而非模180圆距（0-90°）——后者会把反平行
        #    （气爆 idx91 差 186.6°）折叠成 6.6° 误判为可信。
        wm2 = pts[ordered[idx - 2]][0] < cx
        wp2 = pts[ordered[idx + 2]][0] < cx
        if wm2 != is_left or wp2 != is_left:
            raw = _leaf_wing_tangent(pts, ordered, pos, idx)
            if raw is not None and abs(_leaf_norm180(
                    t2 - _leaf_slope_out(raw, is_left))) >= 15.0:
                out[fid] = _leaf_slope_out(raw, is_left)
                continue
        in1 = -90.0 <= t1 <= 90.0
        in2 = -90.0 <= t2 <= 90.0
        if in1 and not in2:
            # ── 建议3：翼尖拐点（t2 恰出域）→ 仅当跨翼弦 t1 与翼内弦斜率严重
            #    分歧（最近圆差 ≥15°）时用斜率公式处理 t2（跨翼弦不可信）；分歧
            #    小 = 两弦共线、t1 已正确，保持 t1（P2 原逻辑逐位不变）——无条件
            #    替换会把落叶头 idx44 从 2.2° 打成 9.2°（A7 回归检查实锤）──
            raw = _leaf_wing_tangent(pts, ordered, pos, idx)
            if raw is not None and abs(_leaf_norm180(
                    t1 - _leaf_slope_out(raw, is_left))) >= 15.0:
                out[fid] = _leaf_slope_out(raw, is_left)
            else:
                out[fid] = _leaf_clamp90(t1)
            continue
        if in2 and not in1:
            tR = t2
        else:                     # 两者同侧（都在/都不在 [-90,90]）取 t2
            tR = t2
        if is_left:               # 左翼
            out[fid] = _leaf_clamp90(tR)
        else:                     # 右翼
            out[fid] = _leaf_clamp90(-tR)
    return out


def _translate_shape(shape: dict, dx: float, dy: float):
    typ = shape.get("type", "line")
    dx, dy = float(dx), float(dy)
    if typ == "line":
        shape["x1"] = _safe_float(shape.get("x1")) + dx; shape["y1"] = _safe_float(shape.get("y1")) + dy
        shape["x2"] = _safe_float(shape.get("x2")) + dx; shape["y2"] = _safe_float(shape.get("y2")) + dy
    elif typ in ("circle", "ellipse"):
        shape["cx"] = _safe_float(shape.get("cx")) + dx; shape["cy"] = _safe_float(shape.get("cy")) + dy
    elif typ in ("polyline", "spline"):
        shape["points"] = [(float(x) + dx, float(y) + dy) for x, y in (shape.get("points", []) or [])]
        for cp_data in (shape.get("seg_cps", {}) or {}).values():
            for key in ("cp1", "cp2"):
                if key in cp_data:
                    cp_data[key] = [float(cp_data[key][0]) + dx, float(cp_data[key][1]) + dy]
    elif typ == "pattern_matrix":
        shape["x"] = _safe_float(shape.get("x", 40)) + dx; shape["y"] = _safe_float(shape.get("y", 40)) + dy


def _clone_shape(shape: dict, dx: float = 0.0, dy: float = 0.0) -> dict:
    cloned = copy.deepcopy(shape)
    cloned["id"] = str(uuid.uuid4())
    if dx or dy:
        _translate_shape(cloned, dx, dy)
    return cloned


def _clone_page(page: dict) -> dict:
    cloned = copy.deepcopy(page)
    cloned["id"] = str(uuid.uuid4())
    cloned["name"] = f"{page.get('name', 'Sheet')} 副本"
    for key in ("cad_shapes", "pattern_matrices"):
        cloned[key] = [_clone_shape(shape) for shape in (cloned.get(key, []) or [])]
    return cloned


# ══════════════════════════════════════════════════════════════════════════
#  R137 页名唯一化（模块级：TopViewPanel 是上帝类，方法数冻结，不得新增方法）
#
#  用户实测的连锁故障：`add_page` 用「页数+1」命名（删掉 Sheet1 再新建 →
#  两个 Sheet2），而 `import_layout_pages` 用 `{name: page}` 按名索引 →
#  重名时**只有第一个命中**：导入内容全并进那一页，另一半页成孤儿（不可达）。
#  根因是「页名不判重」，故在三个写入口（新建/重命名/复制）统一唯一化，
#  导入侧再对「同一次导入里的同名 spec」改名新建 + 同名页只认第一个。
# ══════════════════════════════════════════════════════════════════════════

def _unique_page_name(base: str, pages) -> str:
    """页面唯一名：`Sheet3` 已存在 → `Sheet3 (2)`、`Sheet3 (3)` …。

    `pages` 只用于占位检测（可传「除自己以外的页」以便同名重命名保持原名）。
    """
    used = {str(p.get("name", "")) for p in pages if isinstance(p, dict)}
    name = str(base or "Sheet").strip() or "Sheet"
    if name not in used:
        return name
    k = 2
    while f"{name} ({k})" in used:
        k += 1
    return f"{name} ({k})"


def _import_page_lookup(pages) -> dict:
    """导入页匹配表：同名页只保留**第一个**（重名页各自独立，不再并内容）。"""
    out: dict = {}
    for p in pages:
        if isinstance(p, dict):
            out.setdefault(str(p.get("name", "")), p)
    return out


def _unique_spec_names(specs) -> list:
    """给 page_specs 里的**同名** spec 分配不同页名：第 n 个用 `基名 (n)`（n≥2）。

    同名 spec 若都返回原名，导入侧会按名匹配到同一页 → 两份内容并进一页
    （另一半设备/图形在页面上找不到）。首个同名 spec 仍用原名（与已有页合并，
    重复导入保持幂等），第 2、3… 个才改名，各自成页。
    """
    seen: dict = {}
    out = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            out.append(spec)
            continue
        base = str(spec.get("name", "") or "Sheet")
        n = int(seen.get(base, 0)) + 1
        seen[base] = n
        out.append(dict(spec, name=f"{base} ({n})") if n > 1 else spec)
    return out


def _rotate_shape(shape: dict, angle_deg: float):
    angle_deg = float(angle_deg or 0.0)
    if abs(angle_deg) < 0.001:
        return
    typ = shape.get("type", "line")
    cx, cy = _shape_center(shape)
    if typ == "line":
        shape["x1"], shape["y1"] = _rotate_point_xy(_safe_float(shape.get("x1")), _safe_float(shape.get("y1")), cx, cy, angle_deg)
        shape["x2"], shape["y2"] = _rotate_point_xy(_safe_float(shape.get("x2")), _safe_float(shape.get("y2")), cx, cy, angle_deg)
    elif typ == "ellipse":
        shape["angle"] = (_safe_float(shape.get("angle", 0.0), 0.0) + angle_deg) % 360.0
    elif typ == "circle":
        shape["angle"] = (_safe_float(shape.get("angle", 0.0), 0.0) + angle_deg) % 360.0
    elif typ == "polyline":
        shape["points"] = [_rotate_point_xy(float(x), float(y), cx, cy, angle_deg) for x, y in (shape.get("points", []) or [])]
        for cp_data in (shape.get("seg_cps", {}) or {}).values():
            for key in ("cp1", "cp2"):
                if key in cp_data:
                    cp_data[key] = list(_rotate_point_xy(float(cp_data[key][0]), float(cp_data[key][1]), cx, cy, angle_deg))
    elif typ == "pattern_matrix":
        shape["angle"] = (_safe_float(shape.get("angle", 0.0), 0.0) + angle_deg) % 360.0


def _rotate_shape_about(shape: dict, cx: float, cy: float, angle_deg: float):
    """绕外部支点 (cx,cy) 旋转整个图形：既旋转其几何点，又累加内部 angle。

    与 _rotate_shape 不同——后者绕图形自身中心旋转（只调内部 angle/折线点）；
    本函数用于整组旋转（设备组/整页绕形心转），图形要随形心一起公转。
    """
    angle_deg = float(angle_deg or 0.0)
    if abs(angle_deg) < 1e-6:
        return
    typ = shape.get("type", "line")
    if typ == "line":
        shape["x1"], shape["y1"] = _rotate_point_xy(_safe_float(shape.get("x1")), _safe_float(shape.get("y1")), cx, cy, angle_deg)
        shape["x2"], shape["y2"] = _rotate_point_xy(_safe_float(shape.get("x2")), _safe_float(shape.get("y2")), cx, cy, angle_deg)
    elif typ in ("circle", "ellipse"):
        shape["cx"], shape["cy"] = _rotate_point_xy(_safe_float(shape.get("cx")), _safe_float(shape.get("cy")), cx, cy, angle_deg)
        shape["angle"] = (_safe_float(shape.get("angle", 0.0), 0.0) + angle_deg) % 360.0
    elif typ == "polyline":
        shape["points"] = [list(_rotate_point_xy(float(x), float(y), cx, cy, angle_deg)) for x, y in (shape.get("points", []) or [])]
        for cp_data in (shape.get("seg_cps", {}) or {}).values():
            for key in ("cp1", "cp2"):
                if key in cp_data:
                    cp_data[key] = list(_rotate_point_xy(float(cp_data[key][0]), float(cp_data[key][1]), cx, cy, angle_deg))
    elif typ == "pattern_matrix":
        shape["x"], shape["y"] = _rotate_point_xy(_safe_float(shape.get("x", 40)), _safe_float(shape.get("y", 40)), cx, cy, angle_deg)
        shape["angle"] = (_safe_float(shape.get("angle", 0.0), 0.0) + angle_deg) % 360.0


class TopViewGroupButton(QPushButton):
    """TopView 内置设备组展板按钮：与 Fixture 设备组同步，可拖入画板。

    ★ D 波 24px 行解剖（restyle_spec §10/A）：名称 11px 左、元数据 10px
      text_secondary 右对齐；选中态 = select_blue 白字 + 2px 左竖条（竖条
      沿用用户资产组色——§4.B 数据色禁止主题化）；hover = bg_panel。
      只做呈现，点击/拖拽行为与原按钮一致（鼠标事件经标签透明穿透）。
    """
    def __init__(self, panel: "TopViewPanel", group: dict, fixture_ids: List[str], placed_count: int = 0, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.group = dict(group or {})
        self.group_id = str(self.group.get("id", ""))
        self.fixture_ids = [str(x) for x in fixture_ids]
        self.group_name = str(self.group.get("name", self.group_id or "设备组"))
        self.color = str(self.group.get("color", "#2A4A7A"))
        self._press_pos = QPoint()
        self._drag_started = False
        _group_activation.preserve_group_space(self)
        self.clicked.connect(lambda: self.panel.select_group_from_board(self.group_id))
        self.setFixedHeight(24)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("点击选择当前画板中的组成员；Ctrl+点击增减组；Tab切换焦点，空格选择。拖到画板可放入设备。")
        suffix = f"已放入 {placed_count}" if placed_count else "未放入"
        meta = f"{len(self.fixture_ids)}台 · {suffix}"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 6, 0)
        lay.setSpacing(4)
        self._name_lbl = QLabel(self.group_name)
        self._name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._meta_lbl = QLabel(meta)
        self._meta_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._meta_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._name_lbl, 1)
        lay.addWidget(self._meta_lbl)
        # 行选中语义只驱动外观；业务选择仍走 select_group_from_board。
        self.setCheckable(True)
        self.toggled.connect(self._apply_row_state)
        self._apply_row_state(False)

    def _row_qss(self, checked: bool) -> str:
        if checked:
            bg = str(_th.SELECT_BLUE)
            hover = ""
        else:
            bg = "transparent"
            hover = f"\nQPushButton:hover {{ background: {_th.BG_PANEL}; }}"
        return (
            f"QPushButton {{ background: {bg}; border: none;"
            f" border-left: 2px solid {self.color}; border-radius: 0; padding: 0;"
            f" text-align: left; }}{hover}")

    def _apply_row_state(self, checked: bool):
        self.setStyleSheet(self._row_qss(bool(checked)))
        if checked:
            self._name_lbl.setStyleSheet(
                f"color:{_th.TEXT_SELECTED_FG};font-size:11px;background:transparent;border:none;")
            self._meta_lbl.setStyleSheet(
                "color:rgba(255,255,255,200);font-size:10px;background:transparent;border:none;")
        else:
            self._name_lbl.setStyleSheet(
                f"color:{_th.TEXT_PRIMARY};font-size:11px;background:transparent;border:none;")
            self._meta_lbl.setStyleSheet(
                f"color:{_th.TEXT_SECONDARY};font-size:10px;background:transparent;border:none;")

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_pos = ev.position().toPoint()
            self._drag_started = False
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev: QMouseEvent):
        if not (ev.buttons() & Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(ev)
        if (ev.position().toPoint() - self._press_pos).manhattanLength() < 6:
            return super().mouseMoveEvent(ev)
        if self._drag_started:
            return
        self._drag_started = True
        self.setDown(False)
        mime = QMimeData()
        payload = {
            "source": "topview_group_board",
            "group_id": self.group_id,
            "group_name": self.group_name,
            "fixture_ids": self.fixture_ids,
        }
        mime.setData("application/x-dmx-fixture-group", json.dumps(payload).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if self._drag_started:
            self.setDown(False)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class _SpatialIndex:
    """Grid-based spatial index for culling off-screen fixtures.

    Partitions the world into fixed-size cells. Query returns all fixture IDs
    whose cells overlap a given viewport rectangle (with a 1-cell margin).
    """

    def __init__(self, cell_size: float = 200.0):
        self._cell_size = cell_size
        self._cells: Dict[Tuple[int, int], List[str]] = {}

    def clear(self):
        self._cells.clear()

    def insert(self, fixture_id: str, x: float, y: float):
        cx = int(x / self._cell_size)
        cy = int(y / self._cell_size)
        self._cells.setdefault((cx, cy), []).append(fixture_id)

    def rebuild(self, fixtures: List[Tuple[str, float, float]]):
        """Rebuild from an iterable of (fixture_id, x, y) tuples."""
        self.clear()
        for fid, x, y in fixtures:
            self.insert(fid, x, y)

    def query_visible(self, world_rect: QRectF) -> List[str]:
        """Return fixture_ids whose cells overlap (or neighbour) the given world rectangle."""
        min_cx = int(world_rect.left() / self._cell_size) - 1
        max_cx = int(world_rect.right() / self._cell_size) + 1
        min_cy = int(world_rect.top() / self._cell_size) - 1
        max_cy = int(world_rect.bottom() / self._cell_size) + 1
        result: List[str] = []
        for cx in range(min_cx, max_cx + 1):
            for cy in range(min_cy, max_cy + 1):
                cell = self._cells.get((cx, cy))
                if cell:
                    result.extend(cell)
        return result


class TopViewCanvas(QWidget):
    selection_changed = pyqtSignal(list)
    layout_changed = pyqtSignal()

    def __init__(self, panel: "TopViewPanel"):
        super().__init__()
        self.panel = panel
        self.setMinimumSize(120, 90)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._drag_start: Optional[QPointF] = None
        self._drag_pos: Optional[QPointF] = None
        self._drawing_polyline: List[Tuple[float, float]] = []
        install_track_tool(self)   # R178 轨迹选择工具（逻辑/过滤器全在 topview_track_draw）
        self._hover_shape_id: str = ""
        self._active_shape_id: str = ""
        self._selected_shape_id: str = ""
        self._selected_shape_ids: set = set()
        self._shape_clipboard: Optional[dict] = None
        self._drag_handle: Optional[Tuple[str, str]] = None  # (shape_id, handle_key)
        self._drag_shape_id: str = ""
        self._drag_fixture_ids: List[str] = []
        self._drag_fixture_offsets: Dict[str, Tuple[float, float]] = {}
        # ★ 包/组合设备（逻辑设备）拖动会话（2026-08-04 用户：包设备应与普通设备
        #   一样可拖动到弧线吸附）。_drag_logical_id 非空时拖代表点 + 成员整体平移。
        self._drag_logical_id: str = ""
        self._drag_ld_offset: Tuple[float, float] = (0.0, 0.0)
        self._drag_moved = False
        self._last_draw_pen: object = None
        self._last_draw_brush: object = None
        self._visible_set_key: Optional[tuple] = None
        self._visible_set_cache: set = set()
        # 视野浏览状态：画板数据仍使用世界坐标；这里仅保存屏幕视图变换。
        self._view_scale: float = 1.0
        self._view_offset: QPointF = QPointF(0.0, 0.0)
        self._panning: bool = False
        self._pan_start_screen: Optional[QPointF] = None
        self._pan_start_offset: Optional[QPointF] = None
        self._pending_blank_clear: bool = False
        # 画布内变换 gizmo 拖拽会话：
        #   _gizmo_mode ∈ {"", "move", "x", "y", "rotate"}；支点/起点在按下时锁定，避免漂移。
        self._gizmo_mode: str = ""
        self._gizmo_hover: str = ""
        self._gizmo_center: Optional[Tuple[float, float]] = None
        self._gizmo_last_world: Optional[QPointF] = None
        self._gizmo_last_angle: float = 0.0
        # 角落缩放手柄拖拽会话（2026-08-08 W3）：
        #   _gizmo_scale_anchor = 抓取角对角（固定支点, 世界坐标）
        #   _gizmo_scale_box    = 按下时选中集包围盒（factor 参照系, 世界坐标）
        self._gizmo_scale_anchor: Optional[Tuple[float, float]] = None
        self._gizmo_scale_box: Optional[Tuple[float, float, float, float]] = None
        self._gizmo_bbox_cache: Optional[tuple] = None
        # B2: Spatial culling + LOD
        self._spatial_index = _SpatialIndex(cell_size=200.0)
        self._spatial_index_dirty: bool = True
        # ★ D 波空态（widget_kit.EmptyState, §10.6）：画布无设备且无图形时
        #   居中占位；可见性由 paintEvent 下一拍的 _sync_empty_overlay 同步。
        self._empty_state = EmptyState(
            "画布为空",
            "从左侧「Fixture 设备组」展板拖入设备组开始排布；也可用顶部工具绘制图形或使用快捷放置。",
            icon_key="grid-dots", parent=self, image_id="empty_unboxing")
        self._empty_state.hide()
        # 空间索引构建时的位置版本——索引语义新鲜度判定（增量重绘用；位置
        # 版本前进而索引未重建 = 索引过期，脏区查询退化为线性扫描防漏画）。
        self._spatial_index_built_at: int = -1
        self._static_cache: Optional[QPixmap] = None
        # 画笔/画刷对象缓存（按 LOD/缩放/悬停态），免每台设备每帧构造
        self._fixture_pen_cache: Optional[tuple] = None
        self._gizmo_pen_cache: Optional[tuple] = None
        self.setAcceptDrops(True)

    def _page(self) -> dict:
        return self.panel.current_page()

    def _fit_view_to_content(self):
        """视口自动适配当前页内容（设备 + 包代表点），导入后调用。

        世界坐标跨度可达 ±5000，默认 scale=1 视口完全看不到；按内容边界
        缩放并居中（2026-08-03 用户反馈看不到拟合曲线/设备）。
        """
        try:
            page = self.panel.current_page()
            pts = []
            for fid in self.panel._visible_fixture_ids():
                try:
                    pts.append(self.panel.fixture_pos(str(fid)))
                except Exception:
                    pass
            for ld in (page.get("logical_devices") or []):
                pts.append((_safe_float(ld.get("pos_x"), 0.0), _safe_float(ld.get("pos_y"), 0.0)))
            if not pts:
                return
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            span_x = max(1.0, max(xs) - min(xs))
            span_y = max(1.0, max(ys) - min(ys))
            cw = max(400, float(self.width()))
            ch = max(300, float(self.height()))
            scale = min(cw / span_x, ch / span_y) * 0.85
            scale = max(0.01, min(8.0, scale))
            cx = (min(xs) + max(xs)) / 2.0
            cy = (min(ys) + max(ys)) / 2.0
            self._view_scale = scale
            self._view_offset = QPointF(cw * 0.5 - cx * scale, ch * 0.5 - cy * scale)
            self._static_cache = None
        except Exception:
            pass

    def _screen_to_world(self, pos: QPointF) -> QPointF:
        scale = max(0.005, float(getattr(self, "_view_scale", 1.0) or 1.0))
        off = getattr(self, "_view_offset", QPointF(0.0, 0.0))
        return QPointF((pos.x() - off.x()) / scale, (pos.y() - off.y()) / scale)

    def _world_to_screen(self, pos: QPointF) -> QPointF:
        scale = max(0.005, float(getattr(self, "_view_scale", 1.0) or 1.0))
        off = getattr(self, "_view_offset", QPointF(0.0, 0.0))
        return QPointF(pos.x() * scale + off.x(), pos.y() * scale + off.y())

    def _zoom_at(self, screen_pos: QPointF, factor: float):
        before = self._screen_to_world(screen_pos)
        self._view_scale = max(0.01, min(8.00, self._view_scale * float(factor)))
        self._view_offset = QPointF(screen_pos.x() - before.x() * self._view_scale, screen_pos.y() - before.y() * self._view_scale)
        self._static_cache = None
        self.update()

    def reset_view(self):
        self._view_scale = 1.0
        self._view_offset = QPointF(0.0, 0.0)
        self._static_cache = None
        self.update()

    # ── B2: Spatial culling + LOD ─────────────────────────────────────

    def _viewport_rect_in_world(self) -> QRectF:
        """Return the visible world rectangle for the current viewport."""
        tl = self._screen_to_world(QPointF(0.0, 0.0))
        br = self._screen_to_world(QPointF(float(self.width()), float(self.height())))
        return QRectF(tl, br).normalized()

    def _current_lod(self) -> str:
        scale = self._view_scale
        if scale < 0.15:
            return "minimal"    # 1px dots, no text
        elif scale < 0.40:
            return "low"        # 3px circles, name truncated to 4 chars
        elif scale < 0.80:
            return "medium"     # 5px circles, full name
        else:
            return "high"       # full detail with selection highlight

    def _fixture_name(self, fid: str) -> str:
        fixtures = self.panel.fixtures_by_id()
        return str(getattr(fixtures.get(fid), "name", fid[-4:]))

    def _build_spatial_index(self):
        """Rebuild the spatial index from all fixtures on the current page."""
        self._spatial_index.rebuild(
            (fid, self.panel.fixture_pos(fid)[0], self.panel.fixture_pos(fid)[1])
            for fid in self.panel.fixture_ids()
        )
        self._spatial_index_dirty = False
        self._spatial_index_built_at = getattr(self.panel, "_pos_version", 0)

    def _invalidate_spatial_index(self):
        self._spatial_index_dirty = True
        self.update()

    def _get_static_cache(self) -> QPixmap:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            self._static_cache = None
            return QPixmap()
        key = (w, h, self.devicePixelRatioF(), _th.CANVAS_REVISION)
        if (self._static_cache is not None and logical_size(self._static_cache) == self.size()
                and getattr(self, "_static_material_key", None) == key):
            return self._static_cache
        self._static_cache = hidpi_pixmap(self.width(), self.height(), self)   # C7：主画布静态层按 DPR
        self._static_cache.fill(Qt.GlobalColor.transparent)
        cp = QPainter(self._static_cache)
        cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_grid(cp)
        cp.save()
        self._apply_world_transform(cp)
        self._draw_shapes(cp)
        cp.restore()
        cp.end()
        self._static_material_key = key
        return self._static_cache

    def _fixture_pens(self) -> dict:
        """按 (LOD, 缩放) 缓存画笔/画刷/颜色——绘制语义不变（QPainter setPen/
        setBrush 值语义拷贝），仅免去每台设备每帧的对象构造（探针实测构造
        占每台 ~40% 耗时）。缓存键含缩放：minimal 档画笔宽 = 1.0/scale。
        """
        s = max(0.02, float(getattr(self, "_view_scale", 1.0) or 1.0))
        lod = self._current_lod()
        key = (lod, round(s, 6), _th.CANVAS_REVISION)
        c = getattr(self, "_fixture_pen_cache", None)
        if c is not None and c[0] == key:
            return c[1]
        d: Dict[str, object] = {}
        # 画布专属暖橙选中族（暖橙）：填充=select_orange、描边=active_orange。
        # 十字线绿/蓝双色为方向语义编码，保持原值（restyle_spec §4 保护色）。
        if lod == "minimal":
            d["pen_sel"] = QPen(QColor(_th.CANVAS_SELECTION), 1.0 / s)
            d["pen_norm"] = QPen(QColor(_th.CANVAS_BORDER), 1.0 / s)
            d["brush_sel"] = QBrush(QColor(_th.CANVAS_SELECTION_FILL))
            d["brush_norm"] = QBrush(QColor(_th.CANVAS_CARD))
        elif lod == "low":
            d["pen_sel"] = QPen(QColor(_th.CANVAS_SELECTION), 1)
            d["pen_norm"] = QPen(QColor(_th.CANVAS_BORDER), 1)
            d["brush_sel"] = QBrush(QColor(_th.CANVAS_SELECTION_FILL))
            d["brush_norm"] = QBrush(QColor(_th.CANVAS_CARD))
            d["label_color"] = QColor(_th.TEXT_TERTIARY)
        else:  # medium / high
            d["pen_sel"] = QPen(QColor(_th.CANVAS_SELECTION), 2)
            d["pen_norm"] = QPen(QColor(_th.CANVAS_BORDER), 1)
            d["brush_sel"] = QBrush(QColor(_th.CANVAS_SELECTION_FILL))
            d["brush_norm"] = QBrush(QColor(_th.CANVAS_CARD))
            d["cross_pen"] = QPen(QColor("#44ffaa"), 2)
            d["cross_pen2"] = QPen(QColor("#44aaff"), 1)
            d["label_color"] = QColor(_th.TEXT_TERTIARY)
        self._fixture_pen_cache = (key, d)
        return d

    def _draw_fixture_lod(self, p: QPainter, fid: str, lod: str):
        """Draw a single fixture at the given level of detail."""
        x, y = self.panel.fixture_pos(fid)
        is_sel = fid in self.panel.selected_ids
        # 屏幕恒定最小尺寸：半径按 1/scale 补偿，任何缩放级别设备标记都 ≥3.5px 可见
        # （此前 minimal 档画 1px 点，在世界变换下缩到 <0.15px → 用户缩小后设备"消失"）
        s = max(0.02, float(getattr(self, "_view_scale", 1.0)))
        pens = self._fixture_pens()

        if lod == "minimal":
            r = 3.5 / s
            pen = pens["pen_sel"] if is_sel else pens["pen_norm"]
            brush = pens["brush_sel"] if is_sel else pens["brush_norm"]
            if pen is not self._last_draw_pen:
                p.setPen(pen)
                self._last_draw_pen = pen
            if brush is not self._last_draw_brush:
                p.setBrush(brush)
                self._last_draw_brush = brush
            p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))
            return

        # Ellipse / circle size depends on LOD
        if lod == "low":
            r = max(3.0, 3.5 / s)
        elif lod == "medium":
            r = max(5.0, 3.0 / s)
        else:  # high
            r = 7.0

        rect = QRectF(x - r, y - r, r * 2, r * 2)
        pen = pens["pen_sel"] if is_sel else pens["pen_norm"]
        brush = pens["brush_sel"] if is_sel else pens["brush_norm"]
        if pen is not self._last_draw_pen:
            p.setPen(pen)
            self._last_draw_pen = pen
        if brush is not self._last_draw_brush:
            p.setBrush(brush)
            self._last_draw_brush = brush
        p.drawEllipse(rect)

        # Sync reference lines (medium + high only)
        # ★ 十字线 = 每台同步目标角（2026-08-05 显示层修复）：此前恒画全局角,
        #   画布从不反映逐台朝向——落叶头 73 条十字全同向(=输出统一方向)、
        #   底弧线 120 条十字沿弧线渐变(=每台输出目标), 画布所见 = 真机所出。
        if lod in ("medium", "high") and self.panel.xy_reference_mode_for_fixture(fid) == "sync":
            angle = math.radians(self.panel.xy_reference_angle_for_fixture(fid))
            ux, uy = math.cos(angle), math.sin(angle)
            p.setPen(pens["cross_pen"])
            p.drawLine(QPointF(x - ux * 13.0, y - uy * 13.0), QPointF(x + ux * 13.0, y + uy * 13.0))
            p.setPen(pens["cross_pen2"])
            p.drawLine(QPointF(x + uy * 10.0, y - ux * 10.0), QPointF(x - uy * 10.0, y + ux * 10.0))

        # Text label
        if lod == "low":
            label = self._fixture_name(fid)[:4]
            p.setPen(pens["label_color"])
            p.drawText(QRectF(x + 5, y - 4, 60, 12), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        elif lod in ("medium", "high"):
            label = self._fixture_name(fid)
            p.setPen(pens["label_color"])
            p.drawText(QRectF(x + 9, y - 9, 120, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

        # Selection order marks (medium + high only)
        if lod in ("medium", "high"):
            self._draw_sel_mark_badges(p, x, y, self._get_order_marks_for(fid))

    def _get_order_marks_for(self, fid: str) -> List[int]:
        """Return selection order numbers for a fixture.

        ★ 性能：倒排索引 O(S) 构建一次、绘制 O(1) 查询（原实现每台设备全扫
        选中序，全选 11382 台时每帧 O(V×S)）。选中序列表整体替换时对象
        id 变化自动重建；selected_ids 未包含 fid 时直接返回空（原语义）。
        ★ 2026-08-04 用户实测修复（跨页编号统一）：编号 = 设备在**同页**选中
        设备中的序号（页内保持选择相对序），不再是跨页全局序位置——跨页选中
        时各页设备各自从 1 连续（泵页 1..N、设备包页 1..M），不出现
        1 3 5 7 交错/961..1080 跳号。
        """
        if fid not in self.panel.selected_ids:
            return []
        # ★ 2026-08-05 用户需求（配套编号继承）：追加进来的配套设备显示其源设备
        #   的编号（泵 3 号 ↔ 包 3 号），不再按本页桶位重排。
        inherit = getattr(self.panel, "_companion_inherit_marks", None)
        if inherit and fid in inherit:
            return inherit[fid]
        order_ids = getattr(self.panel, "_selection_order_ids", None) or []
        idx = getattr(self, "_order_marks_index", None)
        # ★ 正确性(审查L3)：Ctrl+点击是就地 append（对象身份不变）——
        #   以 (对象, 长度) 校验，append 后长度变化即重建。
        if (idx is None
                or getattr(self, "_order_marks_src", None) is not order_ids
                or getattr(self, "_order_marks_len", -1) != len(order_ids)):
            idx = {}
            for _page_ids in self.panel._page_grouped_order_buckets(order_ids).values():
                for _i, _sfid in enumerate(_page_ids, start=1):
                    idx.setdefault(str(_sfid), []).append(_i)
            self._order_marks_index = idx
            self._order_marks_src = order_ids
            self._order_marks_len = len(order_ids)
        return idx.get(fid, [])

    def _pkg_order_marks(self) -> Dict[str, List[int]]:
        """选中包代表点编号: 包 = 1 选择单元, 编号 = 包在**本页**单元序中的位置(1 起)。

        ★ 2026-08-04 用户实测: 气爆等纯包页整页无排序编号——编号只画在可见个体
        (_draw_fixture_lod)上, 包成员不可见且代表点不画号 → 气爆"根本没进正常
        识别程序"。此映射给每个选中包的**首成员**补上单元序号(1..N 连续),
        供 _draw_logical_devices 在代表点上方画编号。与 _order_marks_index 同
        缓存模式: 序列表对象/长度变化时自动重建。
        ★ 2026-08-04 跨页编号统一修复: 原实现把全部页单元按 (x,y) 混排后再
        编号——跨页选中时页间位置交错 → 各页编号跳号/交错(1 3 5 7 / 2 4 6 8,
        至冬7.0 实测 238/770/320 类乱序)。改为**按页分组后页内按位置排序**
        (页内视觉序, 编号沿弧线连续), 各页从 1 起各自连续。
        """
        order_ids = getattr(self.panel, "_selection_order_ids", None) or []
        idx = getattr(self, "_pkg_marks_index", None)
        # ★ 正确性(2026-08-04): 包解析按当前页 logical_devices, 切页(Ctrl 跨页
        #   选择序不变)必须重建——旧缓存只按 (序对象, 长度) 判, 切页后返回
        #   上一页的编号 → 新页包代表点无号/错号。
        cur_page = getattr(self.panel, "_active_page", -1)
        if (idx is None
                or getattr(self, "_pkg_marks_src", None) is not order_ids
                or getattr(self, "_pkg_marks_len", -1) != len(order_ids)
                or getattr(self, "_pkg_marks_page", -1) != cur_page):
            idx = {}
            buckets = self.panel._page_grouped_order_buckets(order_ids)
            inherit = getattr(self.panel, "_companion_inherit_marks", None) or {}
            for _pi in sorted(buckets):
                if _pi < 0:
                    continue
                units = self.panel._selection_units_from_base(buckets[_pi])
                for _i, u in enumerate(units, start=1):
                    if u["fids"]:
                        _f0 = str(u["fids"][0])
                        # ★ 2026-08-05 配套编号继承：追加设备显示源设备的编号
                        idx.setdefault(_f0, []).append(inherit.get(_f0, [_i])[0])
            if -1 in buckets:
                units = self.panel._selection_units_from_base(buckets[-1])
                for _i, u in enumerate(units, start=1):
                    if u["fids"]:
                        idx.setdefault(str(u["fids"][0]), []).append(_i)
            self._pkg_marks_index = idx
            self._pkg_marks_src = order_ids
            self._pkg_marks_len = len(order_ids)
            self._pkg_marks_page = cur_page
        return idx

    def _draw_sel_mark_badges(self, p: QPainter, x: float, y: float, marks: List[int]):
        """在 (x, y) 上方画选择序号徽标(与 _draw_fixture_lod 同款式)。"""
        if not marks:
            return
        old_font = p.font()
        f = QFont(old_font)
        f.setPointSizeF(max(7.5, old_font.pointSizeF() - 1.0))
        f.setBold(True)
        p.setFont(f)
        for row_i, number in enumerate(marks):
            text = str(number)
            w = max(16.0, 8.0 + len(text) * 7.0)
            mark_rect = QRectF(x - w / 2.0, y - 25.0 - row_i * 15.0, w, 13.0)
            badge_bg = QColor(_th.ACTIVE_ORANGE)
            badge_bg.setAlpha(225)
            p.setPen(QPen(QColor(_th.POPUP_BORDER), 1))
            p.setBrush(QBrush(badge_bg))
            p.drawRoundedRect(mark_rect, 3.0, 3.0)
            p.setPen(QColor(_th.POPUP_BORDER))
            p.drawText(mark_rect, Qt.AlignmentFlag.AlignCenter, text)
        p.setFont(old_font)

    def _apply_world_transform(self, p: QPainter):
        p.translate(self._view_offset)
        p.scale(self._view_scale, self._view_scale)

    # ── 增量重绘工具（拖动会话 update(rect) + 区域感知绘制）──────────────

    def _lod_draw_extent(self, lod: str) -> float:
        """该 LOD 下设备绘制内容的最大外延（世界单位）——脏矩形扩边用。

        覆盖：圆点半径+画笔、同步十字线(±13)、文字标签(右 129)、选择序号
        徽标(上方，序数 ≤7 时 ≤150)、逻辑设备框(18)。
        """
        if lod == "minimal":
            s = max(0.02, float(getattr(self, "_view_scale", 1.0) or 1.0))
            return (3.5 + 1.0) / s + 2.0
        if lod == "low":
            return 68.0
        return 150.0

    def _screen_rect_to_world_rect(self, r: QRect) -> QRectF:
        """设备坐标矩形 → 世界坐标矩形（_screen_to_world 的矩形版）。"""
        tl = self._screen_to_world(QPointF(r.left(), r.top()))
        br = self._screen_to_world(QPointF(r.right(), r.bottom()))
        return QRectF(tl, br).normalized()

    def _screen_rect_for_world(self, min_x: float, min_y: float, max_x: float, max_y: float,
                               margin_screen: float = 82.0) -> QRect:
        """世界 bbox → 设备坐标脏矩形（含 LOD 外延 + gizmo 环半径/安全边距），
        与视口求交。输入非法时返回整画布（调用方退化为全量重绘）。"""
        if not (max_x >= min_x and max_y >= min_y):
            return self.rect()
        s = max(0.005, float(getattr(self, "_view_scale", 1.0) or 1.0))
        off = getattr(self, "_view_offset", QPointF(0.0, 0.0))
        ext = self._lod_draw_extent(self._current_lod())
        m = ext * s + margin_screen + 4.0
        r = QRectF(min_x * s + off.x() - m, min_y * s + off.y() - m,
                   (max_x - min_x) * s + 2.0 * m, (max_y - min_y) * s + 2.0 * m)
        return r.toAlignedRect().intersected(self.rect())

    def _incremental_update(self, rect: Optional[QRect]) -> None:
        """增量重绘入口：脏矩形接近整画布（≥90%）或无效时退化为全量 update()。"""
        if rect is None:
            self.update()
            return
        full = self.rect()
        if rect.width() * rect.height() >= full.width() * full.height() * 0.9:
            self.update()
            return
        self.update(rect)

    def _fixture_drag_dirty_rect(self, last_pos: QPointF, pos: QPointF) -> Optional[QRect]:
        """设备/包拖动会话脏区：被拖对象 旧+新 位置 bbox（世界）→ 屏幕矩形。"""
        min_x = min_y = math.inf
        max_x = max_y = -math.inf
        n = 0

        def _add(x: float, y: float):
            nonlocal min_x, min_y, max_x, max_y, n
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
            n += 1

        for fid in self._drag_fixture_ids:
            ox, oy = self._drag_fixture_offsets.get(fid, (0.0, 0.0))
            _add(pos.x() + ox, pos.y() + oy)
            _add(last_pos.x() + ox, last_pos.y() + oy)
        if self._drag_logical_id:
            ddx = pos.x() - last_pos.x()
            ddy = pos.y() - last_pos.y()
            for ld in self.panel.current_page_logical_devices():
                if str(ld.get("id", "")) != self._drag_logical_id:
                    continue
                try:
                    lx = float(ld.get("pos_x", 0.0))
                    ly = float(ld.get("pos_y", 0.0))
                except Exception:
                    lx = ly = 0.0
                _add(lx, ly)
                _add(lx - ddx, ly - ddy)
                for m in (ld.get("member_fixture_ids", []) or []):
                    try:
                        mx, my = self.panel.fixture_pos(str(m))
                    except Exception:
                        continue
                    _add(mx, my)
                    _add(mx - ddx, my - ddy)
                break
        if n == 0:
            return None
        return self._screen_rect_for_world(min_x, min_y, max_x, max_y)

    def _gizmo_hover_dirty_rect(self) -> Optional[QRect]:
        """gizmo 悬停高亮变化：只重绘 gizmo 屏幕 bbox（中心 ± 环半径 + 余量）。

        角落缩放手柄画在选中集包围盒四角——包围盒超出环半径时余量扩展到角点。
        """
        g = self._gizmo_geom()
        if not g:
            return None
        s = max(0.005, float(getattr(self, "_view_scale", 1.0) or 1.0))
        off = getattr(self, "_view_offset", QPointF(0.0, 0.0))
        cx = g["cx"] * s + off.x()
        cy = g["cy"] * s + off.y()
        m = 92.0
        box = self._gizmo_bbox()
        if box:
            bx1, by1, bx2, by2 = box
            mx = max(abs(cx - bx1 * s - off.x()), abs(cx - bx2 * s - off.x())) + 16.0
            my = max(abs(cy - by1 * s - off.y()), abs(cy - by2 * s - off.y())) + 16.0
            m = max(m, mx, my)
        return QRect(int(cx - m), int(cy - m), int(m * 2), int(m * 2)).intersected(self.rect())

    def _fixture_rect(self, fid: str) -> QRectF:
        p = self.panel.fixture_pos(fid)
        return QRectF(p[0] - 7, p[1] - 7, 14, 14)

    def _ensure_fixture_hover_index(self) -> Dict[tuple, List[str]]:
        """鼠标命中桶格索引：200 世界单位/格，位置版本或页面变化时重建。

        ★ 性能：原 _fixture_at 每 move 对全页（11382 台）构建 QRectF 线性扫描；
        桶格查询候选通常 ≤ 数十台。语义不变：命中仍按 fixture_ids() 逆序
        （后放置优先）在候选内判定。
        """
        pg = self.panel.current_page()
        key = (id(pg), getattr(self.panel, "_pos_version", 0))
        if (getattr(self, "_hover_index_key", None) == key
                and getattr(self, "_hover_index_page", None) is pg
                and getattr(self, "_hover_index", None) is not None):
            return self._hover_index
        idx: Dict[tuple, List[str]] = {}
        cell = 200.0
        for fid in self.panel.fixture_ids():
            try:
                x, y = self.panel.fixture_pos(fid)
            except Exception:
                continue
            idx.setdefault((int(x / cell), int(y / cell)), []).append(fid)
        self._hover_index = idx
        self._hover_index_key = key
        self._hover_index_page = pg
        return idx

    def _fixture_at(self, pos: QPointF) -> Optional[str]:
        # ★ 性能：桶格候选查询（见 _ensure_fixture_hover_index），命中语义不变
        idx = self._ensure_fixture_hover_index()
        if not idx:
            return None
        cell = 200.0
        bx = int(pos.x() / cell)
        by = int(pos.y() / cell)
        cand = set()
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                cand.update(idx.get((bx + ox, by + oy), ()))
        if not cand:
            return None
        for fid in reversed(self.panel.fixture_ids()):
            if fid in cand and self._fixture_rect(fid).adjusted(-5, -5, 5, 5).contains(pos):
                return fid
        return None

    def _fixture_core_at(self, pos: QPointF) -> Optional[str]:
        """设备中心 ±7px 核心命中（原始 rect，不含 _fixture_at 的 ±5 余量）。

        图线拖动优先级修正用：图形线命中且点不在任何设备核心内 → 优先走图形
        分支（点线拖线）；点在设备核心内 → 走设备分支（点设备拖设备）。
        桶格查询与 _fixture_at 同源，命中仍按 fixture_ids() 逆序（后放置优先）。
        """
        idx = self._ensure_fixture_hover_index()
        if not idx:
            return None
        cell = 200.0
        bx = int(pos.x() / cell)
        by = int(pos.y() / cell)
        cand = set()
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                cand.update(idx.get((bx + ox, by + oy), ()))
        if not cand:
            return None
        for fid in reversed(self.panel.fixture_ids()):
            if fid in cand and self._fixture_rect(fid).contains(pos):
                return fid
        return None

    def _logical_device_at(self, pos: QPointF) -> Optional[dict]:
        """命中本页逻辑设备代表点（18×18 方框 + 余量）。返回 ld dict 或 None。"""
        if not hasattr(self.panel, "current_page_logical_devices"):
            return None
        for ld in reversed(self.panel.current_page_logical_devices()):
            try:
                x = float(ld.get("pos_x", 0.0)); y = float(ld.get("pos_y", 0.0))
            except Exception:
                continue
            if QRectF(x - 12, y - 12, 24, 24).contains(pos):
                return ld
        return None

    def _selection_axis_tolerance(self, values: List[float]) -> float:
        if len(values) < 2:
            return 18.0
        ordered = sorted(float(v) for v in values)
        gaps = [b - a for a, b in zip(ordered, ordered[1:]) if (b - a) > 0.5]
        if not gaps:
            return 18.0
        gaps.sort()
        if gaps[-1] <= 16.0:
            return 18.0
        best_index = -1
        best_ratio = 1.0
        for i, gap in enumerate(gaps[:-1]):
            if gap <= 0.0:
                continue
            ratio = gaps[i + 1] / gap
            if ratio > best_ratio:
                best_ratio = ratio
                best_index = i
        if best_index >= 0 and best_ratio >= 2.0:
            return max(8.0, min(28.0, (gaps[best_index] + gaps[best_index + 1]) * 0.5))
        return max(12.0, min(22.0, gaps[-1] * 0.45))

    def _cluster_selection_items(self, items: List[dict], axis: str) -> List[List[dict]]:
        tolerance = self._selection_axis_tolerance([it[axis] for it in items])
        clusters: List[List[dict]] = []
        centers: List[float] = []
        for it in sorted(items, key=lambda item: (item[axis], item["fallback"])):
            if clusters and abs(float(it[axis]) - centers[-1]) <= tolerance:
                clusters[-1].append(it)
                centers[-1] = sum(float(x[axis]) for x in clusters[-1]) / len(clusters[-1])
            else:
                clusters.append([it])
                centers.append(float(it[axis]))
        return clusters

    def _selection_cluster_center(self, cluster: List[dict], axis: str) -> float:
        if not cluster:
            return 0.0
        return sum(float(it[axis]) for it in cluster) / len(cluster)

    def _selection_line_residual_span(self, items: List[dict], primary_axis: str) -> float:
        if len(items) < 3:
            return 0.0
        cross_axis = "y" if primary_axis == "x" else "x"
        primary = [float(it[primary_axis]) for it in items]
        cross = [float(it[cross_axis]) for it in items]
        primary_mean = sum(primary) / len(primary)
        cross_mean = sum(cross) / len(cross)
        denom = sum((v - primary_mean) ** 2 for v in primary)
        if denom <= 1.0:
            return max(cross) - min(cross)
        slope = sum((p - primary_mean) * (c - cross_mean) for p, c in zip(primary, cross)) / denom
        intercept = cross_mean - slope * primary_mean
        residuals = [c - (slope * p + intercept) for p, c in zip(primary, cross)]
        return max(residuals) - min(residuals)

    def _build_fixture_shape_map(self) -> dict:
        """Map visible fixture/package units to their existing CAD ellipse."""
        from .topview_selection_order import fixture_shape_map
        return fixture_shape_map(self.panel)

    def _selection_box_fixture_ids(self, start: QPointF, end: QPointF) -> List[str]:
        """Order displayed units along curves/drag direction, then expand packages."""
        from .topview_selection_order import box_fixture_ids
        return box_fixture_ids(self, start, end)

    def paintEvent(self, event):
        full_rect = self.rect()
        region = event.rect()
        # ★ 增量重绘（2026-08-05）：拖动会话只 update(脏矩形) → paintEvent 只重画
        #   该区域（背景/静态缓存子块 + 该区设备，其余像素沿用上一帧）。全量路径
        #   的绘制序列与原实现逐字节一致——增量路径只是把同样的绘制按 Qt 裁剪到
        #   脏区并跳过区外设备。
        partial = (region != full_rect and region.isValid()
                   and region.width() * region.height()
                   < full_rect.width() * full_rect.height() * 0.9)
        if not partial:
            region = full_rect
        p = QPainter(self)
        paint_bed(p, self.rect())
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # B2: static cache (grid + shapes) avoids re-drawing unchanging layers
        cache = self._get_static_cache()
        if partial:
            p.drawPixmap(QRectF(region), cache, QTransform.fromScale(cache.devicePixelRatioF(), cache.devicePixelRatioF()).mapRect(QRectF(region)))
        else:
            p.drawPixmap(0, 0, cache)
        p.save()
        # 裁剪只作用于增量路径：实测 setClipRect(整画布) 也会改变亚像素虚线/
        # AA 光栅化（框选 DashLine 398 像素差），全量路径保持零裁剪原样。
        if partial:
            p.setClipRect(region)
        self._apply_world_transform(p)
        if partial:
            # 只画与脏矩形相交的设备（LOD 绘制外延计入查询矩形）
            ext = self._lod_draw_extent(self._current_lod())
            wrect = self._screen_rect_to_world_rect(region)
            wrect = wrect.adjusted(-ext, -ext, ext, ext)
            self._draw_fixtures_lod(p, clip_world=wrect)
        else:
            self._draw_fixtures_lod(p)   # spatially culled + LOD
        self._draw_live_preview(p)
        self._draw_xform_gizmo(p)
        p.restore()
        # 框选虚线：1 世界单位宽 DashLine 在裁剪激活时亚像素光栅化会改变
        # （实测 ±1 像素差）——脏矩形恒包含整个框（旧+新位置并集+边距），
        # 弹开裁剪画框不会越出脏区；全量路径本就无裁剪，行为不变。
        if self._drag_start and self._drag_pos and self.panel.current_tool == "select" and not self._gizmo_mode and not self._drag_fixture_ids and not self._drag_handle:
            p.save()
            self._apply_world_transform(p)
            r = QRectF(self._drag_start, self._drag_pos).normalized()
            if r.width() > 3 or r.height() > 3:
                p.setPen(QPen(QColor(_th.CANVAS_SELECTION), 1, Qt.PenStyle.DashLine))
                p.setBrush(QBrush(selection_fill()))
                p.drawRect(r)
            p.restore()
        if self.width() >= 240 and self.height() >= 40 and (
                not partial or region.intersects(QRect(0, self.height() - 44, self.width(), 44))):
            # ★ D 波「轻 chip」HUD（restyle_spec §10.7/A）：半透明 bg_deep 80%
            #   + 1px 白 17 边 + 4px 圆角；文字 11px text_secondary。
            #   与画布边缘/控件保持 8px 间距，不遮挡内容。
            hint = f"缩放 {self._view_scale:.0%}｜滚轮缩放，中键/Alt+左键拖动画布，方向键平移，Ctrl+0 复位"
            if self.width() < 520:
                hint = f"缩放 {self._view_scale:.0%}"
            p.setFont(_th.cached_font(
                11, families=("Inter", "Segoe UI", "Microsoft YaHei UI")))
            fm = p.fontMetrics()
            chip_h = fm.height() + 10
            chip_w = min(fm.horizontalAdvance(hint) + 16,
                         max(56, self.width() - 16))
            chip = QRect(8, self.height() - chip_h - 8, chip_w, chip_h)
            chip_bg = QColor(_th.BG_DEEP)
            chip_bg.setAlpha(204)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
            p.setBrush(QBrush(chip_bg))
            p.drawRoundedRect(QRectF(chip), 4.0, 4.0)
            p.setPen(QColor(_th.TEXT_SECONDARY))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, hint)
        p.end()
        # 空态覆盖层可见性同步：安排在下一拍执行（paint 内不改动 widget 树）。
        if not getattr(self, "_empty_check_scheduled", False):
            self._empty_check_scheduled = True
            QTimer.singleShot(0, self._sync_empty_overlay)

    def _sync_empty_overlay(self):
        """空态 EmptyState（widget_kit §10.6）：无设备且无图形时居中显示。"""
        self._empty_check_scheduled = False
        try:
            page = self._page()
            empty = not (self.panel.fixture_ids()
                         or page.get("cad_shapes") or []
                         or page.get("pattern_matrices") or []
                         or page.get("logical_devices") or [])
        except Exception:
            return
        w, h = self.width(), self.height()
        show = bool(empty and w >= 240 and h >= 90)
        vw = max(200, min(w - 32, 380))
        vh = max(96, min(h - 24, 160))
        geo = QRect((w - vw) // 2, (h - vh) // 2, vw, vh)
        ov = getattr(self, "_empty_state", None)
        if ov is None:
            return
        changed = ov.geometry() != geo
        ov.setGeometry(geo)
        visible = not ov.isHidden()
        if show and (not visible or changed):
            ov.setVisible(True)
        elif not show and visible:
            ov.hide()

    def _draw_grid(self, p: QPainter):
        scale = max(0.005, float(getattr(self, "_view_scale", 1.0) or 1.0))
        step = 25.0
        while step * scale < 16.0:
            step *= 2.0
        while step * scale > 120.0 and step > 6.25:
            step /= 2.0
        top_left = self._screen_to_world(QPointF(0.0, 0.0))
        bottom_right = self._screen_to_world(QPointF(float(self.width()), float(self.height())))
        min_x = math.floor(min(top_left.x(), bottom_right.x()) / step) * step
        max_x = math.ceil(max(top_left.x(), bottom_right.x()) / step) * step
        min_y = math.floor(min(top_left.y(), bottom_right.y()) / step) * step
        max_y = math.ceil(max(top_left.y(), bottom_right.y()) / step) * step
        p.save(); self._apply_world_transform(p)
        # 网格线随底色 token 化：深底(bg_root)上用白色低透明度双档
        # （minor≈SEP_LINE 21 派生、axes 加倍），替代原 #2b2e31/#34383d 近底色。
        minor = QPen(QColor(_th.CANVAS_GRID_MINOR), 0.75 / scale)
        major = QPen(QColor(_th.CANVAS_GRID_MAJOR), 1.0 / scale)
        x = min_x
        while x <= max_x:
            p.setPen(major if int(round(x / step)) % 4 == 0 else minor)
            p.drawLine(QPointF(x, min_y), QPointF(x, max_y)); x += step
        y = min_y
        while y <= max_y:
            p.setPen(major if int(round(y / step)) % 4 == 0 else minor)
            p.drawLine(QPointF(min_x, y), QPointF(max_x, y)); y += step
        p.setPen(QPen(QColor(_th.CANVAS_AXIS), 1.25 / scale))
        p.drawLine(QPointF(min_x, 0), QPointF(max_x, 0))
        p.drawLine(QPointF(0, min_y), QPointF(0, max_y))
        p.restore()

    def _iter_shapes(self) -> List[dict]:
        return list(self._page().get("cad_shapes", []) or []) + list(self._page().get("pattern_matrices", []) or [])

    # ── 画布内变换 gizmo（选中设备时出现：直角坐标平移控件 + 旋转环）──────────────
    # 像素常量（屏幕恒定；绘制时除以 _view_scale 换算到世界）
    _GZ_AXIS = 56.0       # 轴臂长
    _GZ_HEAD = 11.0       # 箭头大小
    _GZ_SQUARE = 11.0     # 中心方块半边（自由平移热区）
    _GZ_RING = 78.0       # 旋转环半径
    _GZ_RING_TOL = 9.0    # 旋转环命中容差
    _GZ_AXIS_TOL = 8.0    # 轴臂命中容差
    _GZ_SCALE_TOL = 9.0   # 角落缩放手柄命中容差

    def _gizmo_geom(self) -> Optional[dict]:
        """返回当前 gizmo 的世界几何 {center,(px sizes already)}；无选中目标时 None。

        ★ 曲线/形状选中优先（2026-08-03 用户：模仿选中设备的样子，整体箭头
        可调整）——gizmo 围绕曲线形心；否则设备选区形心。
        """
        center = None
        if self._selected_shape_id:
            shape = self._shape_by_id(self._selected_shape_id)
            if shape:
                pts = shape.get("points") or []
                if pts:
                    center = (sum(p[0] for p in pts) / len(pts),
                              sum(p[1] for p in pts) / len(pts))
        if center is None:
            try:
                center = self.panel.gizmo_center()
            except Exception:
                center = None
        if center is None:
            return None
        scale = max(0.005, float(getattr(self, "_view_scale", 1.0) or 1.0))
        return {
            "cx": float(center[0]), "cy": float(center[1]), "scale": scale,
            "axis": self._GZ_AXIS / scale, "head": self._GZ_HEAD / scale,
            "square": self._GZ_SQUARE / scale, "ring": self._GZ_RING / scale,
        }

    def _gizmo_bbox(self) -> Optional[Tuple[float, float, float, float]]:
        """选中作用域目标的包围盒（世界坐标）：设备 + 逻辑设备代表点 + 图形。

        结果按 (目标键, 位置版本) 缓存（同质心缓存）——拖拽/变换直写坐标递增
        位置版本 → 缓存自动失效。无目标或退化为点时返回 None（不画缩放手柄）。
        """
        key = self.panel._xform_cache_key()
        pv = getattr(self.panel, "_pos_version", 0)
        c = getattr(self, "_gizmo_bbox_cache", None)
        if c is not None and c[0] == (key, pv):
            return c[1]
        fids, shapes, lds = self.panel._xform_targets("selection")
        xs: List[float] = []
        ys: List[float] = []
        for fid in fids:
            x, y = self.panel.fixture_pos(fid)
            xs.append(float(x)); ys.append(float(y))
        for ld in lds:
            try:
                xs.append(float(ld.get("pos_x", 0.0)))
                ys.append(float(ld.get("pos_y", 0.0)))
            except Exception:
                continue
        for shape in shapes:
            try:
                bx1, by1, bx2, by2 = _shape_bounds(shape)
                xs.extend((bx1, bx2)); ys.extend((by1, by2))
            except Exception:
                continue
        if not xs:
            self._gizmo_bbox_cache = ((key, pv), None)
            return None
        box = (min(xs), min(ys), max(xs), max(ys))
        if abs(box[2] - box[0]) < 1e-9 and abs(box[3] - box[1]) < 1e-9:
            box = None
        self._gizmo_bbox_cache = ((key, pv), box)
        return box

    def _gizmo_hit(self, pos: QPointF) -> str:
        """命中测试：返回 'move'|'x'|'y'|'rotate'|'scale_nw'|...|''（世界坐标 pos）。"""
        g = self._gizmo_geom()
        if not g:
            return ""
        scale = g["scale"]
        # 角落缩放手柄（选中集包围盒四角）：优先于中心/轴臂/环
        box = self._gizmo_bbox()
        if box:
            x1, y1, x2, y2 = box
            chit = self._GZ_SCALE_TOL / scale
            for name, hx, hy in (("nw", x1, y1), ("ne", x2, y1),
                                 ("sw", x1, y2), ("se", x2, y2)):
                if abs(pos.x() - hx) <= chit and abs(pos.y() - hy) <= chit:
                    return f"scale_{name}"
        cx, cy = g["cx"], g["cy"]
        dx, dy = pos.x() - cx, pos.y() - cy
        sq = g["square"] + (2.0 / scale)
        # 中心方块：自由平移
        if abs(dx) <= sq and abs(dy) <= sq:
            return "move"
        axis = g["axis"]; atol = self._GZ_AXIS_TOL / scale
        # X 轴臂（向右）：|dy| 小且 0<dx<=axis
        if abs(dy) <= atol and 0.0 <= dx <= axis + atol:
            return "x"
        # Y 轴臂（向下）
        if abs(dx) <= atol and 0.0 <= dy <= axis + atol:
            return "y"
        # 旋转环
        ring = g["ring"]; rtol = self._GZ_RING_TOL / scale
        dist = math.hypot(dx, dy)
        if abs(dist - ring) <= rtol:
            return "rotate"
        return ""

    def _gizmo_pens(self, scale: float, hover: str) -> dict:
        """按 (缩放, 悬停态) 缓存 gizmo 画笔/画刷——绘制语义不变（同色同宽），
        免每 paint 构造 ~14 个 Qt 对象（拖动会话内 hover 恒定，缓存命中）。"""
        key = (round(scale, 6), hover)
        c = getattr(self, "_gizmo_pen_cache", None)
        if c is not None and c[0] == key:
            return c[1]
        pen_w = 2.0 / scale
        d = {
            "pen_arm_x": QPen(QColor("#ff5a5a"), pen_w),
            "pen_arm_x_hot": QPen(QColor("#ff5a5a"), pen_w * 1.8),
            "brush_arm_x": QBrush(QColor("#ff5a5a")),
            "pen_arm_y": QPen(QColor("#5aff7a"), pen_w),
            "pen_arm_y_hot": QPen(QColor("#5aff7a"), pen_w * 1.8),
            "brush_arm_y": QBrush(QColor("#5aff7a")),
            "pen_ring": QPen(QColor("#caa64a"), pen_w, Qt.PenStyle.SolidLine),
            "pen_ring_hot": QPen(QColor(_th.ACTIVE_ORANGE), pen_w * 1.8, Qt.PenStyle.SolidLine),  # 最亮描边→active_orange（金黄档收尾）
            "brush_handle": QBrush(QColor(_th.SELECT_ORANGE)),                                    # 填充档→select_orange
            "brush_handle_hot": QBrush(QColor(_th.ACTIVE_ORANGE)),                                # 热态取最亮橙，热>常态层级保持
            "pen_handle": QPen(QColor("#caa64a"), pen_w),
            "pen_move": QPen(QColor("#e8e8e8"), pen_w),
            "brush_move": QBrush(QColor(200, 210, 230, 170)),
            "brush_move_hot": QBrush(QColor(120, 180, 255, 220)),
        }
        self._gizmo_pen_cache = (key, d)
        return d

    def _draw_xform_gizmo(self, p: QPainter):
        """选中设备时在选区形心绘制：直角坐标平移控件（中心方块+X红+Y绿轴臂）+ 旋转环。

        尺寸按 _view_scale 归一，屏幕恒定。拖中心=自由平移；拖 X/Y 轴臂=约束平移；
        拖环=旋转；双击环=输入精确角度。"""
        g = self._gizmo_geom()
        if not g:
            return
        scale = g["scale"]; cx, cy = g["cx"], g["cy"]
        axis = g["axis"]; head = g["head"]; sq = g["square"]; ring = g["ring"]
        hover = self._gizmo_mode or self._gizmo_hover
        pens = self._gizmo_pens(scale, hover)

        def _arm(dx, dy, pen, brush):
            ex, ey = cx + dx * axis, cy + dy * axis
            p.setPen(pen)
            p.drawLine(QPointF(cx, cy), QPointF(ex, ey))
            ang = math.atan2(dy, dx)
            path = QPainterPath()
            path.moveTo(ex, ey)
            for off in (math.radians(148), math.radians(-148)):
                path.lineTo(ex + math.cos(ang + off) * head, ey + math.sin(ang + off) * head)
            path.closeSubpath()
            p.fillPath(path, brush)

        # 旋转环（先画，垫底）
        p.setBrush(Qt.BrushStyle.NoBrush)
        ring_hot = (hover == "rotate")
        p.setPen(pens["pen_ring_hot"] if ring_hot else pens["pen_ring"])
        p.drawEllipse(QPointF(cx, cy), ring, ring)
        # 环上四个旋转把手点
        p.setBrush(pens["brush_handle"])
        p.setPen(pens["pen_handle"])
        hr = 3.5 / scale
        for ha in (0.0, 90.0, 180.0, 270.0):
            hx = cx + math.cos(math.radians(ha)) * ring
            hy = cy + math.sin(math.radians(ha)) * ring
            p.drawEllipse(QPointF(hx, hy), hr, hr)
        # 轴臂
        _arm(1.0, 0.0, pens["pen_arm_x_hot"] if hover == "x" else pens["pen_arm_x"],
             pens["brush_arm_x"])   # +X 右
        _arm(0.0, 1.0, pens["pen_arm_y_hot"] if hover == "y" else pens["pen_arm_y"],
             pens["brush_arm_y"])   # +Y 下
        # 中心方块（自由平移）
        move_hot = (hover == "move")
        p.setPen(pens["pen_move"])
        p.setBrush(pens["brush_move_hot"] if move_hot else pens["brush_move"])
        p.drawRect(QRectF(cx - sq, cy - sq, sq * 2, sq * 2))
        # 角落缩放手柄（选中集包围盒四角，2026-08-08 W3）：屏幕恒定方块
        box = self._gizmo_bbox()
        if box:
            x1, y1, x2, y2 = box
            hs = 8.0 / scale
            for name, hx, hy in (("nw", x1, y1), ("ne", x2, y1),
                                 ("sw", x1, y2), ("se", x2, y2)):
                hot = (hover == f"scale_{name}")
                p.setPen(pens["pen_handle"])
                p.setBrush(pens["brush_handle_hot"] if hot else pens["brush_handle"])
                p.drawRect(QRectF(hx - hs, hy - hs, hs * 2, hs * 2))

    def _shape_by_id(self, sid: str) -> Optional[dict]:
        for shape in self._iter_shapes():
            if str(shape.get("id", "")) == str(sid):
                return shape
        return None

    def _valid_shape_ids(self) -> set:
        return {str(shape.get("id", "")) for shape in self._iter_shapes() if str(shape.get("id", ""))}

    def _set_shape_selection(self, ids: List[str]):
        """整组替换图形选择（保留 _selected_shape_id 作为「最后点中的图形」）。"""
        valid = self._valid_shape_ids()
        ordered = []
        seen = set()
        for sid in ids or []:
            ssid = str(sid)
            if ssid and ssid in valid and ssid not in seen:
                ordered.append(ssid)
                seen.add(ssid)
        self._selected_shape_ids = set(ordered)
        self._selected_shape_id = ordered[-1] if ordered else ""
        self.update()

    def _clear_shape_selection(self):
        if not self._selected_shape_ids and not self._selected_shape_id:
            return
        self._selected_shape_ids.clear()
        self._selected_shape_id = ""
        self.update()

    def _toggle_shape_selection(self, sid: str):
        sid = str(sid or "")
        if not sid or not self._shape_by_id(sid):
            return
        if sid in self._selected_shape_ids:
            self._selected_shape_ids.remove(sid)
            self._selected_shape_id = next(iter(self._selected_shape_ids), "")
        else:
            self._selected_shape_ids.add(sid)
            self._selected_shape_id = sid
        self.update()

    def _shape_select_tolerance(self, shape: dict) -> float:
        """设备-图形关联容差：按图形尺寸自适应，限定 20~40 世界单位。

        2026-08-08 W3 设备连选：点击曲线选中其上设备时，过近会误选相邻设备，
        过大则远离曲线的设备也被拉入；小图形 24、大图形 40 封顶。
        """
        try:
            tol = float(self.panel._shape_assign_tolerance(shape))
        except Exception:
            tol = 32.0
        return max(20.0, min(40.0, tol))

    def _fixture_ids_on_shape(self, shape: dict, tolerance: float) -> List[str]:
        """当前页内与图形关联的设备 id，按「沿图形方向」排序（供区间连选切片）。

        判定：设备位置到图形路径（_shape_points 密采样 + _dist_point_to_segment）
        的最小距离 <= tolerance；包围盒粗筛加速（大页数千台设备时跳过远点）。
        pattern_matrix 的路径=全部格点（单元格数可能大于通用采样数，单独放行）。
        """
        pts = _shape_points(shape, 1_000_000) if str(shape.get("type", "")) == "pattern_matrix" else _shape_points(shape, 640)
        if len(pts) < 2:
            return []
        try:
            x1, y1, x2, y2 = _shape_bounds(shape)
        except Exception:
            x1 = y1 = -1e9
            x2 = y2 = 1e9
        hits: List[Tuple[float, float, str]] = []
        denom = max(1, len(pts) - 1)
        for fid in self.panel.fixture_ids():
            fx, fy = self.panel.fixture_pos(fid)
            if fx < x1 - tolerance or fx > x2 + tolerance or fy < y1 - tolerance or fy > y2 + tolerance:
                continue
            best = 1e18
            best_t = 0.0
            for k in range(denom):
                d = _dist_point_to_segment(fx, fy, pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1])
                if d < best:
                    best = d
                    best_t = k / denom
            if best <= tolerance:
                hits.append((best_t, best, fid))
        hits.sort(key=lambda h: (h[0], h[1]))
        return [fid for _, _, fid in hits]

    def _selected_shape_body_at(self, pos: QPointF, padding: float = 3.0) -> Optional[dict]:
        """点击命中「已选中图形」的填充区域（非边缘）——用于拖动已选图形而不改选择。

        2026-08-09 用户实测「选中的图线拖不动」: spline/polyline/line 无填充
        body（_shape_body_contains 仅支持 circle/ellipse/pattern_matrix），
        点击图形内部（距路径 >12px）会落空清选。回退：已选中图形的包围盒
        （+padding）内命中即视为拖动起点；circle 等有 body 的类型仍走精确判定。
        """
        selected = set(self._selected_shape_ids)
        if self._selected_shape_id:
            selected.add(self._selected_shape_id)
        if not selected:
            return None
        for shape in reversed(self._iter_shapes()):
            sid = str(shape.get("id", ""))
            if sid in selected and _shape_body_contains(shape, pos, padding=padding):
                return shape
        # 包围盒回退（仅无填充 body 的类型）
        for shape in reversed(self._iter_shapes()):
            sid = str(shape.get("id", ""))
            if sid not in selected:
                continue
            if shape.get("type") in ("circle", "ellipse", "pattern_matrix"):
                continue
            x1, y1, x2, y2 = _shape_bounds(shape)
            pad = float(padding or 0.0)
            if QRectF(x1 - pad, y1 - pad, (x2 - x1) + pad * 2.0, (y2 - y1) + pad * 2.0).contains(pos):
                return shape
        return None

    def _shape_body_at(self, pos: QPointF, padding: float = 3.0) -> Optional[dict]:
        for shape in reversed(self._iter_shapes()):
            if _shape_body_contains(shape, pos, padding=padding):
                return shape
        return None

    def _selected_shape_list(self) -> List[dict]:
        """当前选中图形列表（多选集合），供选区旋转等操作与设备一体变换。"""
        valid = self._valid_shape_ids()
        self._selected_shape_ids.intersection_update(valid)
        if self._selected_shape_id and self._selected_shape_id not in self._selected_shape_ids:
            self._selected_shape_id = ""
        wanted = set(self._selected_shape_ids)
        if self._selected_shape_id and not wanted:
            wanted = {self._selected_shape_id}
        return [shape for shape in self._iter_shapes() if str(shape.get("id", "")) in wanted]

    def _copy_shape(self, sid: str):
        shape = self._shape_by_id(sid)
        if not shape:
            return
        self._shape_clipboard = copy.deepcopy(shape)

    def _paste_shape(self, pos: Optional[QPointF] = None):
        if not self._shape_clipboard:
            return
        self.panel._snapshot_for_undo()
        shape = _clone_shape(self._shape_clipboard)
        if pos is None:
            _translate_shape(shape, 24.0, 24.0)
        else:
            cx, cy = _shape_center(shape)
            _translate_shape(shape, pos.x() - cx, pos.y() - cy)
        key = "pattern_matrices" if shape.get("type") == "pattern_matrix" else "cad_shapes"
        self.panel.current_page().setdefault(key, []).append(shape)
        self._selected_shape_id = str(shape.get("id", ""))
        self._selected_shape_ids = {self._selected_shape_id} if self._selected_shape_id else set()
        self.panel.layout_changed.emit()
        self.update()

    def _set_circle_radius_dialog(self, sid: str):
        shape = self._shape_by_id(sid)
        if not shape or shape.get("type") not in ("circle", "ellipse"):
            return
        old_rx = max(5.0, abs(_safe_float(shape.get("rx"), 80.0)))
        old_ry = max(5.0, abs(_safe_float(shape.get("ry"), old_rx)))
        if shape.get("type") == "circle":
            radius, ok = QInputDialog.getDouble(
                self, "设置圆半径", "半径:", old_rx, 5.0, 100000.0, 1)
            if ok:
                self.panel._snapshot_for_undo()
                shape["rx"] = shape["ry"] = float(radius)
        else:
            rx, ok = QInputDialog.getDouble(
                self, "设置椭圆横向半径", "横向半径:", old_rx, 5.0, 100000.0, 1)
            if not ok:
                return
            ry, ok = QInputDialog.getDouble(
                self, "设置椭圆纵向半径", "纵向半径:", old_ry, 5.0, 100000.0, 1)
            if ok:
                self.panel._snapshot_for_undo()
                shape["rx"] = float(rx)
                shape["ry"] = float(ry)
        self.panel.layout_changed.emit()
        self.update()

    def _shape_at(self, pos: QPointF, tolerance: float = 12.0) -> Optional[dict]:
        best, best_d = None, tolerance
        for shape in reversed(self._iter_shapes()):
            d = _shape_hit_distance(shape, pos)
            if d <= best_d:
                best, best_d = shape, d
        return best

    def _drag_fixture_positions(self) -> List[Tuple[float, float]]:
        # ★ 性能：复用拖动会话可见集（原实现每 move 重建 11382 元素 set）；
        #   会话外调用（无缓存）时按原逻辑构建，行为一致。
        vis = getattr(self, "_drag_visible_set", None)
        if vis is None:
            vis = set(self.panel.fixture_ids())
        pts = [self.panel.fixture_pos(fid) for fid in self._drag_fixture_ids if fid in vis]
        # ★ 包/组合设备拖动：吸附判定用代表点位置（成员不在页可见集，不入坐标表）。
        if self._drag_logical_id:
            for ld in self.panel.current_page_logical_devices():
                if str(ld.get("id", "")) == self._drag_logical_id:
                    pts.append((float(ld.get("pos_x", 0.0)), float(ld.get("pos_y", 0.0))))
                    break
        return pts

    def _move_drag_logical(self, pos: QPointF):
        """拖动包/组合设备：代表点跟随光标，成员真实坐标整体平移（与 gizmo 平移同语义）。

        只移动当前拖动的包——与 translate_current 不同，不因"选中集含多个包"而连锁移动。
        """
        ox, oy = self._drag_ld_offset
        nx, ny = float(pos.x() + ox), float(pos.y() + oy)
        for ld in self.panel.current_page_logical_devices():
            if str(ld.get("id", "")) != self._drag_logical_id:
                continue
            dx = nx - float(ld.get("pos_x", 0.0))
            dy = ny - float(ld.get("pos_y", 0.0))
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                return
            ld["pos_x"], ld["pos_y"] = nx, ny
            fix_by_id = self.panel._proj_fixture_map()
            for fid in ld.get("member_fixture_ids", []) or []:
                fi = fix_by_id.get(str(fid))
                if fi is None:
                    continue
                try:
                    fi.pos_x = float(getattr(fi, "pos_x", 0.0)) + dx
                    fi.pos_y = float(getattr(fi, "pos_y", 0.0)) + dy
                except Exception:
                    continue
            self.panel._pos_version = getattr(self.panel, "_pos_version", 0) + 1
            return

    def _shape_for_fixture_drop(self, pos: QPointF, tolerance: float = 20.0) -> Optional[dict]:
        target = self._shape_at(pos, tolerance=tolerance)
        if target:
            return target
        pts = self._drag_fixture_positions()
        if not pts:
            return None
        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)
        target = self._shape_at(QPointF(cx, cy), tolerance=tolerance)
        if target:
            return target
        best, best_d = None, tolerance
        for x, y in pts:
            shape = self._shape_at(QPointF(x, y), tolerance=tolerance)
            if not shape:
                continue
            d = _shape_hit_distance(shape, QPointF(x, y))
            if d <= best_d:
                best, best_d = shape, d
        return best

    def _handle_at(self, pos: QPointF) -> Optional[Tuple[str, str]]:
        for shape in reversed(self._iter_shapes()):
            sid = str(shape.get("id", ""))
            if not sid:
                continue
            for key, x, y in _shape_anchor_points(shape):
                if QRectF(x - 7, y - 7, 14, 14).contains(pos):
                    return sid, key
        return None

    def _draw_one_shape(self, p: QPainter, shape: dict, active: bool):
        typ = shape.get("type")
        if typ == "line":
            p.drawLine(QPointF(shape.get("x1", 0), shape.get("y1", 0)), QPointF(shape.get("x2", 0), shape.get("y2", 0)))
        elif typ in ("circle", "ellipse"):
            cx, cy, rx, ry = shape.get("cx", 0), shape.get("cy", 0), abs(shape.get("rx", 50)), abs(shape.get("ry", 50))
            angle = _safe_float(shape.get("angle", 0.0), 0.0) if typ == "ellipse" else 0.0
            arc = _arc_span(shape) if typ == "circle" else None
            if arc is not None:
                # 部分圆弧：只画真实弧段（长弧不再渲染成整圆）
                pts = _arc_sample_points(float(cx), float(cy), rx, ry, arc[0], arc[1])
                path = QPainterPath(QPointF(*pts[0]))
                for qx, qy in pts[1:]:
                    path.lineTo(QPointF(qx, qy))
                p.drawPath(path)
            elif abs(angle) > 0.001:
                p.save(); p.translate(float(cx), float(cy)); p.rotate(angle)
                p.drawEllipse(QRectF(-rx, -ry, rx * 2, ry * 2))
                p.restore()
            else:
                p.drawEllipse(QRectF(cx - rx, cy - ry, rx * 2, ry * 2))
        elif typ == "spline":
            # ★ 过设备点的平滑曲线（导入时 Catmull-Rom 预采样，2026-08-03）——
            #   采样点已平滑，直接连线段（与 polyline 全直线同路径，QPainterPath
            #   在该绘制链上实测不出像素）。
            raw_pts = shape.get("points", []) or []
            pts = [QPointF(float(x), float(y)) for x, y in raw_pts]
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
        elif typ == "polyline":
            raw_pts = shape.get("points", []) or []
            pts = [QPointF(float(x), float(y)) for x, y in raw_pts]
            seg_cps = shape.get("seg_cps", {}) or {}
            if not seg_cps:
                # 全直线，快速路径
                for a, b in zip(pts, pts[1:]):
                    p.drawLine(a, b)
            else:
                # 混合直线/曲线，用 QPainterPath
                if pts:
                    path = QPainterPath(pts[0])
                    for seg_i, (a, b) in enumerate(zip(pts, pts[1:])):
                        cp_data = seg_cps.get(str(seg_i), {})
                        if cp_data and ("cp1" in cp_data or "cp2" in cp_data):
                            cp1_raw = cp_data.get("cp1") or [(a.x()*2+b.x())/3, (a.y()*2+b.y())/3]
                            cp2_raw = cp_data.get("cp2") or [(a.x()+b.x()*2)/3, (a.y()+b.y()*2)/3]
                            path.cubicTo(QPointF(float(cp1_raw[0]), float(cp1_raw[1])),
                                         QPointF(float(cp2_raw[0]), float(cp2_raw[1])), b)
                        else:
                            path.lineTo(b)
                    p.drawPath(path)
                    # 当选中时绘制控制点辅助线
                    if active:
                        old_pen = p.pen()
                        p.setPen(QPen(QColor(_th.SELECT_ORANGE), 1, Qt.PenStyle.DashLine))
                        for seg_i_str, cp_data in seg_cps.items():
                            if not cp_data:
                                continue
                            try:
                                seg_i = int(seg_i_str)
                                if seg_i < len(pts) - 1:
                                    a, b = pts[seg_i], pts[seg_i + 1]
                                    if "cp1" in cp_data:
                                        cp1 = QPointF(float(cp_data["cp1"][0]), float(cp_data["cp1"][1]))
                                        p.drawLine(a, cp1)
                                    if "cp2" in cp_data:
                                        cp2 = QPointF(float(cp_data["cp2"][0]), float(cp_data["cp2"][1]))
                                        p.drawLine(b, cp2)
                            except Exception:
                                pass
                        p.setPen(old_pen)
        elif typ == "pattern_matrix":
            old = p.pen(); p.setPen(QPen(QColor("#b79cff"), 1, Qt.PenStyle.DashLine))
            for x, y in _shape_points(shape, 9999): p.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
            p.setPen(old)
        # 分配/定位点：所有路径都显示等距参考点，选中/悬停/拖拽激活时更醒目，便于继续微调。
        marker_count = 16 if active else 8
        pts = _shape_points(shape, marker_count)
        old_pen, old_brush = p.pen(), p.brush()
        p.setPen(QPen(QColor(_th.POPUP_BORDER), 1))
        # 画布专属暖橙选中族：激活参考点 active_orange，常规参考点 bright_info。
        p.setBrush(QBrush(QColor(_th.ACTIVE_ORANGE if active else _th.BRIGHT_INFO)))
        r = 3.8 if active else 2.6
        for x, y in pts:
            p.drawEllipse(QRectF(x - r, y - r, r * 2, r * 2))
        p.setPen(old_pen); p.setBrush(old_brush)

    def _draw_shape_handles(self, p: QPainter, shape: dict):
        old_pen, old_brush = p.pen(), p.brush()
        for key, x, y in _shape_anchor_points(shape):
            if key.startswith("cp1:") or key.startswith("cp2:"):
                # 曲线控制点：橙色菱形（active_orange 描边 + select_orange 填充）
                p.setPen(QPen(QColor(_th.ACTIVE_ORANGE), 1))
                p.setBrush(QBrush(QColor(_th.SELECT_ORANGE)))
                diamond = QPainterPath()
                diamond.moveTo(x, y - 6)
                diamond.lineTo(x + 6, y)
                diamond.lineTo(x, y + 6)
                diamond.lineTo(x - 6, y)
                diamond.closeSubpath()
                p.drawPath(diamond)
            elif key.startswith("pt:"):
                p.setPen(QPen(QColor(_th.ACTIVE_ORANGE), 1))
                p.setBrush(QBrush(QColor(_th.BG_ROOT)))
                p.drawEllipse(QRectF(x - 5, y - 5, 10, 10))
            else:
                p.setPen(QPen(QColor(_th.ACTIVE_ORANGE), 1))
                p.setBrush(QBrush(QColor(_th.BG_ROOT)))
                p.drawRect(QRectF(x - 5, y - 5, 10, 10))
        p.setPen(old_pen); p.setBrush(old_brush)

    def _draw_shapes(self, p: QPainter):
        # ★ 线宽屏幕恒定（÷scale）：世界变换下固定 2px 在世界单位里只剩
        #   0.2px（scale≈0.1）抗锯齿后不可见——用户反馈看不到拟合曲线
        #   （2026-08-03）。与设备标记 3.5/s 同一约定。
        s = max(0.02, float(getattr(self, "_view_scale", 1.0) or 1.0))
        for shape in self._iter_shapes():
            sid = str(shape.get("id", ""))
            active = sid in self._selected_shape_ids or sid in {self._hover_shape_id, self._active_shape_id, self._selected_shape_id}
            # 三档描边层级：激活形 active_orange > 命中/悬停 select_orange > 常规 bright_info
            color = _th.ACTIVE_ORANGE if sid == self._active_shape_id else (_th.SELECT_ORANGE if active else _th.BRIGHT_INFO)
            width = 3.0 / s if active else 2.0 / s
            p.setPen(QPen(QColor(color), width))
            self._draw_one_shape(p, shape, active)
            if active:
                self._draw_shape_handles(p, shape)

    def _draw_live_preview(self, p: QPainter):
        tool = self.panel.current_tool
        if self._drag_start and self._drag_pos and tool in ("line", "circle", "ellipse"):
            p.setPen(QPen(QColor(_th.ACTIVE_ORANGE), 2, Qt.PenStyle.DashLine))
            s, e = self._drag_start, self._drag_pos
            if tool == "line":
                p.drawLine(s, e)
            else:
                rx, ry = abs(e.x() - s.x()), abs(e.y() - s.y())
                if tool == "circle": ry = rx
                p.drawEllipse(QRectF(s.x() - rx, s.y() - ry, rx * 2, ry * 2))
        if self._drawing_polyline:
            p.setPen(QPen(QColor(_th.ACTIVE_ORANGE), 2, Qt.PenStyle.DashLine))
            pts = [QPointF(x, y) for x, y in self._drawing_polyline]
            if self._drag_pos is not None:
                pts.append(self._drag_pos)
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)

    def _visible_fixture_set(self) -> set:
        """缓存当前页可见设备集合；列表对象/长度不变即复用，避免每次 paint 重建 set。"""
        lst = self.panel.fixture_ids()
        key = (id(lst), len(lst))
        if (self._visible_set_key == key and getattr(self, "_visible_set_list", None) is lst
                and self._visible_set_cache is not None):
            return self._visible_set_cache
        s = set(lst)
        self._visible_set_key = key
        self._visible_set_list = lst
        self._visible_set_cache = s
        return s

    def _draw_fixtures_lod(self, p: QPainter, clip_world: Optional[QRectF] = None):
        """Spatially-culled LOD-aware fixture renderer. Replaces _draw_fixtures.

        clip_world 非空 = 增量重绘（拖动会话 update(rect)）：只绘制与该世界脏
        矩形（调用方已含 LOD 绘制外延）相交的设备。空间索引语义过期（位置版本
        前进而索引未重建）时在线性补扫漂移出格位的设备——索引查询保绘制顺序
        （与全量路径一致，重叠 AA 圆点顺序敏感），补扫防长距离拖动漏画。
        全量路径的查询/兜底逻辑与原实现逐字节一致。
        """
        # Lazy rebuild spatial index if fixture layout changed
        if self._spatial_index_dirty:
            self._build_spatial_index()

        lod = self._current_lod()
        all_visible = self._visible_fixture_set()

        if clip_world is not None:
            if clip_world.width() > 0 and clip_world.height() > 0:
                candidates = self._spatial_index.query_visible(clip_world)
                # 索引语义过期（拖动会话内位置已前进、索引仍是旧格位）时补扫：
                # 索引查询保绘制顺序（与全量路径一致——重叠 AA 圆点顺序不同会
                # 产生 ±1 像素差），线性补扫只把「漂移出新格位」的设备追加在
                # 索引序之后，防长距离拖动漏画（≤200 世界单位漂移内索引查询
                # 自带 ±1 格余量，本补扫仅在超长拖动时触发）。
                if (getattr(self, "_spatial_index_built_at", -1)
                        != getattr(self.panel, "_pos_version", 0)):
                    x0 = clip_world.left()
                    y0 = clip_world.top()
                    x1 = clip_world.right()
                    y1 = clip_world.bottom()
                    seen = set(candidates)
                    # 索引过期只可能是本次拖动的设备漂移出原格位；补扫拖动集即可，
                    # 避免每个 move 全量扫描所有可见设备。
                    moved = list(self._drag_fixture_ids)
                    if self._drag_logical_id:
                        for ld in self.panel.current_page_logical_devices():
                            if str(ld.get("id", "")) == self._drag_logical_id:
                                moved.extend(str(m) for m in (ld.get("member_fixture_ids", []) or []))
                                break
                    extra = []
                    for fid in moved:
                        if fid in seen or fid not in all_visible:
                            continue
                        x, y = self.panel.fixture_pos(fid)
                        if x0 <= x <= x1 and y0 <= y <= y1:
                            extra.append(fid)
                    if extra:
                        candidates = candidates + extra
            else:
                candidates = []
            for fid in candidates:
                if fid not in all_visible:
                    continue
                self._draw_fixture_lod(p, fid, lod)
            self._draw_logical_devices(p)
            return

        view_rect = self._viewport_rect_in_world()

        # Query spatial index; fall back to full iteration on empty/zero-area rect
        candidates = self._spatial_index.query_visible(view_rect) if view_rect.width() > 0 and view_rect.height() > 0 else list(all_visible)

        # Safety: candidates empty but fixtures exist → force rebuild & re-query;
        # still empty → full iteration (slow but correct).
        if not candidates and all_visible:
            self._build_spatial_index()
            candidates = self._spatial_index.query_visible(
                view_rect
            ) if view_rect.width() > 0 and view_rect.height() > 0 else list(all_visible)
            if not candidates:
                candidates = list(all_visible)

        for fid in candidates:
            if fid not in all_visible:
                continue
            self._draw_fixture_lod(p, fid, lod)

        self._draw_logical_devices(p)

    def _draw_logical_devices(self, p: QPainter):
        """画本页逻辑/组合设备代表点（方形双环标记，区别于普通圆点）。

        成员真实坐标不画（仅在各自 FixtureInstance 中、供引擎用）；这里只画代表点 + 名称。
        选中状态：代表点的任一成员在选择集中即视为选中。
        """
        lds = self.panel.current_page_logical_devices() if hasattr(self.panel, "current_page_logical_devices") else []
        if not lds:
            return
        sel = self.panel.selected_ids
        old_font = p.font()
        for ld in lds:
            try:
                x = float(ld.get("pos_x", 0.0)); y = float(ld.get("pos_y", 0.0))
            except Exception:
                continue
            members = [str(m) for m in (ld.get("member_fixture_ids", []) or [])]
            is_sel = any(m in sel for m in members)
            mode = str(ld.get("control_mode", "merged") or "merged")
            kind = str(ld.get("kind", "unit") or "unit")
            # 类型编码（组合=橙 / 单元=蓝）保 hue，迁入主题令牌；选中覆盖为
            # select_orange 深橙 + active_orange 描边（画布专属暖橙选中族）。
            base = QColor(_th.ACTIVE_ORANGE) if kind == "composite" else QColor(_th.FOCUS_BLUE)
            if is_sel:
                base = QColor(_th.SELECT_ORANGE)
            p.setPen(QPen(QColor(_th.ACTIVE_ORANGE) if is_sel else QColor(_th.TEXT_TERTIARY), 2 if is_sel else 1))
            p.setBrush(QBrush(base))
            # 外方框
            p.drawRoundedRect(QRectF(x - 9, y - 9, 18, 18), 3.0, 3.0)
            # 内环（merged=实心点，decomposed=空心，提示已展开）
            p.setBrush(QBrush(QColor(_th.POPUP_BORDER)) if mode == "decomposed" else QBrush(QColor(_th.TEXT_SELECTED_FG)))
            p.drawEllipse(QRectF(x - 3.5, y - 3.5, 7, 7))
            # ★ 包内任一成员为同步基准 → 代表点画同步十字线（随全局角旋转）。
            #   基准成员（基底电机）折叠不绘制，若代表点也无反馈用户会以为
            #   「点同步没反应」——基准其实已建立并在旋转（2026-08-03 用户反馈）。
            sync_ref = next((m for m in members
                             if self.panel.xy_reference_mode_for_fixture(str(m)) == "sync"), None)
            if sync_ref:
                # ★ 十字线 = 该包同步基准成员的目标角（2026-08-05 显示层修复,
                #   与 _draw_fixture_lod 同口径）——包代表点画布所见 = 真机所出。
                ang = math.radians(self.panel.xy_reference_angle_for_fixture(str(sync_ref)))
                ux, uy = math.cos(ang), math.sin(ang)
                p.setPen(QPen(QColor("#44ffaa"), 2))
                p.drawLine(QPointF(x - ux * 13.0, y - uy * 13.0), QPointF(x + ux * 13.0, y + uy * 13.0))
                p.setPen(QPen(QColor("#44aaff"), 1))
                p.drawLine(QPointF(x + uy * 10.0, y - ux * 10.0), QPointF(x - uy * 10.0, y + ux * 10.0))
            cnt = len(members)
            tag = f"{ld.get('name','组合')}·{cnt}{'分' if mode=='decomposed' else '合'}"
            p.setPen(QColor(_th.BRIGHT_WARN) if kind == "composite" else QColor(_th.FOCUS_BLUE))
            p.drawText(QRectF(x + 11, y - 9, 150, 18),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tag)
            # ★ 包代表点选择序号(2026-08-04 用户: 纯包页整页无编号——编号只画在
            #   可见个体上, 包成员不可见 → 代表点补编号, 高亮必有编号)。
            if is_sel and members:
                self._draw_sel_mark_badges(p, x, y, self._pkg_order_marks().get(members[0], []))
        p.setFont(old_font)

    def _finish_fixture_drop_to_shape(self):
        if not self._drag_fixture_ids and not self._drag_logical_id:
            return False
        pos = self._drag_pos or self._drag_start
        target = self._shape_for_fixture_drop(pos, tolerance=32.0) if pos is not None else None
        if target:
            self._active_shape_id = str(target.get("id", "")) if target else ""
        if not self._active_shape_id:
            return False
        shape = self._shape_by_id(self._active_shape_id)
        if not shape:
            return False
        # ★ 包/组合设备吸附（2026-08-04 用户：包设备拖到弧线 = 代表点吸附弧线，
        #   成员按相对偏移整体跟随；与 gizmo 平移同语义写成员真实坐标）。
        if self._drag_logical_id:
            if _drop_logical_device_to_shape(self.panel, shape, self._drag_logical_id):
                self._active_shape_id = ""
                self._spatial_index_dirty = True
                self.update()
                return True
        visible = set(self.panel.fixture_ids())
        ids = [fid for fid in self._drag_fixture_ids if fid in visible]
        pts = (_single_drop_points(shape, pos) if len(ids) == 1
               else _shape_points(shape, len(ids)))
        for fid, (x, y) in zip(ids, pts):
            self.panel.set_fixture_pos(fid, x, y)
        self.panel._refresh_sync_reference_for_ids(ids)
        self.panel._set_selection(ids)
        self.panel.layout_changed.emit()
        self._active_shape_id = ""
        self._spatial_index_dirty = True
        self.update()
        return True

    def _show_shape_context_menu(self, pos: QPointF, global_pos) -> bool:
        """右键单击已选中的形状时显示操作菜单。显示过菜单返回 True。"""
        from PyQt6.QtWidgets import QMenu
        shape = self._shape_by_id(self._selected_shape_id)
        if not shape:
            shape = self._shape_at(pos) or self._shape_body_at(pos)
        if not shape:
            return False
        sid = str(shape.get("id", ""))
        typ = shape.get("type")
        # 右键的图形不在多选集时收拢为单选（2026-08-08 恢复 shape 多选）
        if sid not in self._selected_shape_ids:
            self._set_shape_selection([sid])
        menu = QMenu(self)
        # 右键菜单外观已由全局 GLOBAL_QSS 的 QMenu 四态样式接管
        # （restyle_spec §3：删除与全局重复的页面级菜单 QSS）。
        act_copy = menu.addAction("复制图形")
        act_copy.triggered.connect(lambda: self._copy_shape(sid))
        act_paste = menu.addAction("粘贴图形")
        act_paste.setEnabled(bool(self._shape_clipboard))
        act_paste.triggered.connect(lambda: self._paste_shape(pos))
        if typ in ("circle", "ellipse"):
            act_radius = menu.addAction("设置圆半径…")
            act_radius.triggered.connect(lambda: self._set_circle_radius_dialog(sid))
        menu.addSeparator()
        if typ == "polyline":
            pts = shape.get("points", []) or []
            seg_count = max(0, len(pts) - 1)
            seg_cps = shape.get("seg_cps", {}) or {}
            has_any_curve = any(seg_cps.get(str(i)) for i in range(seg_count))
            has_any_straight = any(not seg_cps.get(str(i)) for i in range(seg_count))

            if has_any_straight:
                act_all_curve = menu.addAction("将所有直线段转为曲线")
                act_all_curve.triggered.connect(lambda: self._convert_polyline_segs(sid, to_curve=True, seg_indices=None))
            if has_any_curve:
                act_all_straight = menu.addAction("将所有曲线段恢复直线")
                act_all_straight.triggered.connect(lambda: self._convert_polyline_segs(sid, to_curve=False, seg_indices=None))

            if seg_count > 1:
                menu.addSeparator()
                sub = menu.addMenu("按段操作…")   # 子菜单样式随全局 QMenu
                for i in range(seg_count):
                    is_curve = bool(seg_cps.get(str(i)))
                    _seg_label = "曲线 → 直线" if is_curve else "直线 → 曲线"
                    label = f"  第 {i+1} 段：{_seg_label}"
                    act = sub.addAction(label)
                    act.triggered.connect(lambda checked=False, _i=i, _curve=not is_curve:
                        self._convert_polyline_segs(sid, to_curve=_curve, seg_indices=[_i]))

            menu.addSeparator()
        act_rot_l = menu.addAction("左转 15°")
        act_rot_l.triggered.connect(lambda: self.rotate_selected_shape(-15.0))
        act_rot_r = menu.addAction("右转 15°")
        act_rot_r.triggered.connect(lambda: self.rotate_selected_shape(15.0))
        act_rot_90 = menu.addAction("右转 90°")
        act_rot_90.triggered.connect(lambda: self.rotate_selected_shape(90.0))
        act_rot_custom = menu.addAction("输入角度旋转…")
        act_rot_custom.triggered.connect(self.rotate_selected_shape_dialog)
        menu.addSeparator()
        act_del = menu.addAction("删除形状")
        act_del.triggered.connect(lambda: self._delete_shape(sid))
        menu.exec(global_pos)
        return True

    def _show_canvas_context_menu(self, pos: QPointF, global_pos) -> bool:
        """右键菜单（2026-08-08 恢复）：选中设备上=翻面/旋转/删除/排布；
        空白处=新增页/复制页/重命名页/删除页/删除图形/移除选中设备 +
        配套设备追加（与 2026-08-05 配套功能共存）。显示过菜单返回 True。
        """
        from PyQt6.QtWidgets import QMenu
        panel = self.panel
        menu = QMenu(self)
        # 右键菜单外观已由全局 GLOBAL_QSS 的 QMenu 四态样式接管
        # （restyle_spec §3：删除与全局重复的页面级菜单 QSS）。
        hit_device = self._fixture_at(pos) is not None or self._logical_device_at(pos) is not None
        if hit_device and panel.selected_ids:
            act = menu.addAction("选区左转 15°")
            act.triggered.connect(lambda: panel.rotate_selected_fixtures(-15.0))
            act = menu.addAction("选区右转 15°")
            act.triggered.connect(lambda: panel.rotate_selected_fixtures(15.0))
            menu.addSeparator()
            act = menu.addAction("绕X轴翻面")
            act.triggered.connect(lambda: panel.flip_selected_fixtures("x"))
            act = menu.addAction("绕Y轴翻面")
            act.triggered.connect(lambda: panel.flip_selected_fixtures("y"))
            menu.addSeparator()
            act = menu.addAction("排布到最后一个图形")
            act.triggered.connect(lambda: panel.arrange_group_on_last_shape())
            menu.addSeparator()
            act = menu.addAction("移除选中设备")
            act.triggered.connect(panel.remove_selected_from_canvas)
        else:
            menu.addAction("新增页", panel.add_page)
            menu.addAction("复制页", panel.copy_page)
            menu.addAction("重命名页", panel.rename_page)
            menu.addAction("删除页", panel.delete_page)
            menu.addSeparator()
            menu.addAction("删除图形", panel.delete_last_shape)
            menu.addAction("移除选中设备", panel.remove_selected_from_canvas)
            try:
                pages = panel.companion_pages_for_current()
            except Exception:
                pages = []
            if pages:
                menu.addSeparator()
                if len(pages) == 1:
                    act = menu.addAction("自动追加选入配套设备")
                    act.triggered.connect(lambda: self._append_companion_from_menu(pages[0]))
                else:
                    sub = menu.addMenu("自动追加选入配套设备")   # 子菜单样式随全局 QMenu
                    for pg in pages:
                        act = sub.addAction(str(pg.get("name", "?")))
                        act.triggered.connect(lambda checked=False, _pg=pg:
                                              self._append_companion_from_menu(_pg))
        menu.exec(global_pos)
        return True

    def _append_companion_from_menu(self, companion_page: dict):
        """执行配套设备追加；未匹配时提示，空选择/成功则静默（选择条自动刷新）。"""
        try:
            if not self.panel._ordered_selected_ids():
                return
            added = self.panel.append_companion_devices(companion_page)
        except Exception as exc:
            logger.warning("追加配套设备失败: %s", exc)
            QMessageBox.warning(self, "TopView", "追加配套设备失败：%s" % exc)
            return
        if added <= 0:
            QMessageBox.information(
                self, "TopView",
                "未找到当前选中设备对应的配套设备（页「%s」）。"
                % str(companion_page.get("name", "?")))
        else:
            self.update()

    def rotate_selected_shape(self, angle_deg: float):
        sid = str(self._selected_shape_id or self._active_shape_id or "")
        shape = self._shape_by_id(sid) if sid else None
        if not shape:
            return
        _rotate_shape(shape, angle_deg)
        self.panel.layout_changed.emit()
        self.update()

    def rotate_selected_shape_dialog(self):
        angle, ok = QInputDialog.getDouble(self, "旋转图形", "旋转角度（度，正数顺时针）:", 15.0, -3600.0, 3600.0, 1)
        if ok:
            self.rotate_selected_shape(float(angle))

    def _convert_polyline_segs(self, sid: str, to_curve: bool, seg_indices):
        """to_curve=True: 直线段转曲线; False: 曲线段恢复直线。seg_indices=None 表示全部段。"""
        shape = self._shape_by_id(sid)
        if not shape or shape.get("type") != "polyline":
            return
        pts = shape.get("points", []) or []
        seg_count = max(0, len(pts) - 1)
        seg_cps = shape.setdefault("seg_cps", {})
        indices = seg_indices if seg_indices is not None else list(range(seg_count))
        for i in indices:
            if i < 0 or i >= seg_count:
                continue
            if to_curve:
                if not seg_cps.get(str(i)):
                    # 自动生成控制点：在端点垂直方向偏移 1/3 处
                    ax, ay = float(pts[i][0]), float(pts[i][1])
                    bx, by = float(pts[i+1][0]), float(pts[i+1][1])
                    dx, dy = bx - ax, by - ay
                    # 法向偏移：垂直方向偏移 30% 长度
                    perp_x, perp_y = -dy * 0.3, dx * 0.3
                    seg_cps[str(i)] = {
                        "cp1": [ax + dx/3 + perp_x, ay + dy/3 + perp_y],
                        "cp2": [ax + dx*2/3 + perp_x, ay + dy*2/3 + perp_y],
                    }
            else:
                seg_cps.pop(str(i), None)
        # 清理空字典
        if not any(seg_cps.values()):
            shape.pop("seg_cps", None)
        self._static_cache = None
        self.panel.layout_changed.emit()
        self.update()

    def _delete_shape(self, sid: str):
        """从当前页面删除指定 id 的形状。"""
        page = self.panel.current_page()
        for key in ("cad_shapes", "pattern_matrices"):
            shapes = page.get(key, []) or []
            new_shapes = [s for s in shapes if str(s.get("id", "")) != sid]
            if len(new_shapes) != len(shapes):
                page[key] = new_shapes
                break
        if self._selected_shape_id == sid:
            self._selected_shape_id = ""
        if hasattr(self, "_selected_shape_ids"):
            self._selected_shape_ids.discard(sid)
        self._static_cache = None
        self.panel.layout_changed.emit()
        self.update()

    def event(self, ev):
        """R137 撤销钩子：几何手势的按下/释放**外包**一层，不改鼠标处理本体。

        `TopViewPanel` 是上帝类（方法数冻结）且 `mousePressEvent`/`mouseReleaseEvent`
        已顶格（行数/CC 只许缩小），故把「按下压快照、释放没拖动就弹栈」挂在这里：
        先让 Qt 走完原本的 mousePress/mouseRelease（拖动状态已就绪），再按结果
        压/弹撤销条目。非鼠标事件直接原样传递，行为不变。
        """
        from PyQt6.QtCore import QEvent
        if ev.type() == QEvent.Type.MouseButtonPress:
            r = super().event(ev)
            if ev.button() == Qt.MouseButton.LeftButton:
                _undo_arm_on_press(self.panel, self)
            return r
        if ev.type() == QEvent.Type.MouseButtonRelease:
            r = super().event(ev)
            _undo_settle_release(self.panel, bool(self._drag_moved))
            return r
        return super().event(ev)

    def mousePressEvent(self, ev: QMouseEvent):
        screen_pos = ev.position()
        pos, tool = self._screen_to_world(screen_pos), self.panel.current_tool
        self._drag_moved = False
        self._pending_blank_clear = False
        if ev.button() == Qt.MouseButton.MiddleButton or (ev.button() == Qt.MouseButton.LeftButton and ev.modifiers() & Qt.KeyboardModifier.AltModifier):
            self._panning = True
            self._pan_start_screen = screen_pos
            self._pan_start_offset = QPointF(self._view_offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            ev.accept(); return
        if ev.button() == Qt.MouseButton.RightButton and tool == "polyline" and self._drawing_polyline:
            if len(self._drawing_polyline) >= 2: self.panel.add_shape({"id": str(uuid.uuid4()), "type": "polyline", "points": self._drawing_polyline[:]})
            self._drawing_polyline = []; self._drag_pos = None; self.update(); return
        # 画布内变换 gizmo（选 select 工具且有选中设备时）：命中优先于选/拖设备，避免拖控件误选。
        if tool == "select" and ev.button() == Qt.MouseButton.LeftButton:
            hit = self._gizmo_hit(pos)
            # 命中 gizmo 才接管，但若光标下其实压着真实设备/逻辑设备，让位给正常选择，
            # 避免大判定区(旋转环/箭头臂)吞掉附近设备的点击。
            if hit and self._fixture_at(pos) is None and self._logical_device_at(pos) is None:
                self._gizmo_mode = hit
                g = self._gizmo_geom()
                self._gizmo_center = (g["cx"], g["cy"]) if g else None
                self._gizmo_last_world = pos
                if self._gizmo_center is not None:
                    self._gizmo_last_angle = math.degrees(math.atan2(
                        pos.y() - self._gizmo_center[1], pos.x() - self._gizmo_center[0]))
                self._drag_start = pos; self._drag_pos = pos; self._drag_moved = False
                # 空画布上点中 gizmo 区域但未拖动时仍要能清空选择（释放时回落到 select 逻辑）。
                self._pending_blank_clear = not bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
                # 角落缩放：锁定支点=对角 + 原始包围盒（拖动中 factor 参照系）
                self._gizmo_scale_anchor = None
                self._gizmo_scale_box = None
                if hit.startswith("scale_"):
                    box = self._gizmo_bbox()
                    if box:
                        bx1, by1, bx2, by2 = box
                        corner = hit.split("_", 1)[1]
                        anchor = {"nw": (bx2, by2), "ne": (bx1, by2),
                                  "sw": (bx2, by1), "se": (bx1, by1)}[corner]
                        self._gizmo_scale_anchor = anchor
                        self._gizmo_scale_box = box
                if hit.startswith("scale_"):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif hit == "rotate":
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                else:
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update(); return
        # 选择工具下右键：命中形状 → 形状菜单；否则画布菜单（选中设备上=
        # 翻面/旋转/删除/排布，空白处=页面管理+删除+配套设备追加，2026-08-08）
        if ev.button() == Qt.MouseButton.RightButton and tool == "select":
            if not self._show_shape_context_menu(pos, ev.globalPosition().toPoint()):
                self._show_canvas_context_menu(pos, ev.globalPosition().toPoint())
            return
        if tool in ("line", "circle", "ellipse"):
            self._drag_start = pos; self._drag_pos = pos; self.setCursor(Qt.CursorShape.CrossCursor); return
        if tool == "polyline":
            self._drawing_polyline.append((pos.x(), pos.y())); self._drag_pos = pos; self.setCursor(Qt.CursorShape.CrossCursor); self.update(); return
        handle = self._handle_at(pos)
        if handle:
            self._drag_handle = handle
            self._selected_shape_id = handle[0]
            self._selected_shape_ids.add(handle[0])
            self._drag_start = pos; self._drag_pos = pos
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.update(); return
        # ★ 曲线/形状左键点击选中+拖动：走下方 shape 命中逻辑（_selected_shape_id
        #   + _drag_shape_id + _drag_start，2026-08-03 用户：拟合曲线应可选中可移动）。
        #   此处不重复拦截——1812 行 shape 命中已设置完整拖动状态。
        # 逻辑/组合设备代表点：点击=选中其全部真实成员（受当前页守卫；成员可能不在本页可见）。
        # ★ 拖动（2026-08-04 用户：包设备应与普通设备一样可拖动到弧线吸附）。
        #   拖动对象 = 包代表点本身（成员按相对偏移整体跟随），与 gizmo 平移同语义。
        ld = self._logical_device_at(pos)
        if ld:
            self.panel._reset_box_selection_chain()
            members = [str(m) for m in (ld.get("member_fixture_ids", []) or [])]
            mods = ev.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                self.panel._append_selection(members, emit=False)
            else:
                self.panel._set_selection(members, emit=False)
            if members:
                self.panel.last_clicked_id = members[0]
            self.panel.emit_selection()
            self._drag_logical_id = str(ld.get("id", ""))
            self._drag_ld_offset = (float(ld.get("pos_x", 0.0)) - pos.x(),
                                    float(ld.get("pos_y", 0.0)) - pos.y())
            self._drag_fixture_ids = []
            self._drag_fixture_offsets = {}
            self._drag_visible_set = set(self.panel.fixture_ids())
            self._drag_start = pos; self._drag_pos = pos; self._drag_moved = False
            self._active_shape_id = ""
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update(); return
        # ★ 图线拖动优先级修正 (2026-08-08 用户实测: 图线根本移动不了):
        #   _fixture_at 命中范围 ±12px 与图形线命中 12px 几乎一致 → 真实工程
        #   图线上排满设备时点线必先中设备, 图形永远进不了拖动分支。
        #   判定: 图形线命中且点不在任何设备中心 ±7px(原始 rect, 无余量)内 →
        #   优先走图形分支(选中图形+其上设备, 拖图形); 点在设备核心内 → 设备分支。
        #   _shape_at 结果下移复用(不二次计算); _handle_at 手柄分支保持优先。
        shape_hit = self._shape_at(pos)
        if shape_hit is None or self._fixture_core_at(pos) is not None:
            fid = self._fixture_at(pos)
        else:
            fid = None
        if fid:
            self.panel._reset_box_selection_chain()
            ids, mods = self.panel.fixture_ids(), ev.modifiers()
            keep_existing_group = (
                fid in self.panel.selected_ids
                and len(self.panel.selected_ids) > 1
                and not (mods & Qt.KeyboardModifier.ControlModifier)
                and not (mods & Qt.KeyboardModifier.ShiftModifier)
            )
            if keep_existing_group:
                pass
            elif mods & Qt.KeyboardModifier.ControlModifier and mods & Qt.KeyboardModifier.ShiftModifier and self.panel.last_clicked_id in ids:
                a, b = ids.index(self.panel.last_clicked_id), ids.index(fid)
                self.panel._append_selection(ids[min(a, b):max(a, b) + 1], emit=False)
            elif mods & Qt.KeyboardModifier.ControlModifier:
                self.panel._toggle_selection(fid, emit=False)
            elif mods & Qt.KeyboardModifier.ShiftModifier and self.panel.last_clicked_id in ids:
                a, b = ids.index(self.panel.last_clicked_id), ids.index(fid)
                self.panel._set_selection(ids[min(a, b):max(a, b) + 1], emit=False)
            else:
                self.panel._set_selection([fid], emit=False)
            reset_manual_context(self.panel, preserve=keep_existing_group)
            self.panel.last_clicked_id = fid
            # 物理拖动集只取当前页可见设备：跨 sheet 连选时 selected_ids 可能含别页 fid，
            # 不能让它们进入拖动(否则会把别页设备坐标写进当前页、污染其真实位置)。
            # 选择集本身不动，保留 group/emit 行为。
            current_vis = set(self.panel.fixture_ids())
            # 拖动会话复用可见集，避免每次 move 逐设备重建。
            self._drag_visible_set = current_vis
            drag_ids = [d for d in self.panel._ordered_selected_ids() if d in current_vis]
            self._drag_fixture_ids = drag_ids or [fid]
            self._drag_fixture_offsets = {x: (self.panel.fixture_pos(x)[0] - pos.x(), self.panel.fixture_pos(x)[1] - pos.y()) for x in self._drag_fixture_ids}
            self._drag_start = pos; self._drag_pos = pos
            self._active_shape_id = ""
            self.panel.emit_selection(); self.setCursor(Qt.CursorShape.ClosedHandCursor); self.update(); return
        selected_shape = self._selected_shape_body_at(pos)
        if selected_shape and not (ev.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)):
            # 点中已选中图形的填充区：不改选择，直接进入拖动。
            # Shift/Ctrl+点让位给图形分支（Shift=沿曲线区间连选，Ctrl=多选切换）。
            self.panel._reset_box_selection_chain()
            sid = str(selected_shape.get("id", ""))
            self._selected_shape_id = sid
            self.panel._clear_selection(emit=False)
            self.panel.emit_selection()
            self._drag_shape_id = sid
            self._drag_start = pos; self._drag_pos = pos
            self._pending_blank_clear = False
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.update(); return
        shape = shape_hit
        if shape:
            self.panel._reset_box_selection_chain()
            sid = str(shape.get("id", ""))
            mods = ev.modifiers()
            tol = self._shape_select_tolerance(shape)
            on_shape = self._fixture_ids_on_shape(shape, tol)
            if mods & Qt.KeyboardModifier.ShiftModifier and on_shape:
                # ★ Shift+点图形 = 沿图形区间连选（2026-08-08 W3 核心：
                #   用户「曲线设备无法快捷键连选」）。以点击处最近设备为锚点 b，
                #   上次点击设备为锚点 a，在沿图形序上取 [min(a,b):max(a,b)+1]。
                #   上次点击不在本图形上 → 全取该图形设备（连选整条曲线）。
                self._set_shape_selection([sid])
                nearest_i, best = 0, 1e18
                for i, fid in enumerate(on_shape):
                    fx, fy = self.panel.fixture_pos(fid)
                    d = math.hypot(fx - pos.x(), fy - pos.y())
                    if d < best:
                        nearest_i, best = i, d
                last = getattr(self.panel, "last_clicked_id", "")
                if last in on_shape:
                    a = on_shape.index(last)
                    picked = on_shape[min(a, nearest_i):max(a, nearest_i) + 1]
                else:
                    picked = list(on_shape)
                if mods & Qt.KeyboardModifier.ControlModifier:
                    self.panel._append_selection(picked, emit=False)
                else:
                    self.panel._set_selection(picked, emit=False)
                self.panel.last_clicked_id = picked[nearest_i] if nearest_i < len(picked) else (picked[-1] if picked else "")
                self.panel.emit_selection()
                self._drag_shape_id = sid
                self._drag_start = pos; self._drag_pos = pos
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self.update(); return
            if mods & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+点：多选切换（2026-08-08 恢复 shape 多选）
                self._toggle_shape_selection(sid)
                self._drag_shape_id = sid if sid in self._selected_shape_ids else ""
            else:
                # 点击图形：选中图形 + 选中其上关联设备（曲线设备点选，
                # 2026-08-08 W3：排布在曲线上的设备直接点曲线即可选中）。
                self._set_shape_selection([sid])
                if on_shape:
                    self.panel._set_selection(on_shape, emit=False)
                    self.panel.last_clicked_id = on_shape[0]
                else:
                    self.panel._clear_selection(emit=False)
                self.panel.emit_selection()
                self._drag_shape_id = sid
            self._drag_start = pos; self._drag_pos = pos
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.update(); return
        self._drag_start = pos; self._drag_pos = pos
        self._pending_blank_clear = not bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        self.update()

    def _move_shape_handle(self, sid: str, key: str, pos: QPointF):
        shape = self._shape_by_id(sid)
        if not shape:
            return
        typ = shape.get("type")
        if typ == "line":
            if key == "p1": shape["x1"], shape["y1"] = pos.x(), pos.y()
            elif key == "p2": shape["x2"], shape["y2"] = pos.x(), pos.y()
        elif typ in ("circle", "ellipse"):
            cx, cy = _safe_float(shape.get("cx")), _safe_float(shape.get("cy"))
            if key == "center": shape["cx"], shape["cy"] = pos.x(), pos.y()
            elif key in ("right", "left"):
                shape["rx"] = max(5.0, abs(pos.x() - cx))
            elif key in ("bottom", "top"):
                val = max(5.0, abs(pos.y() - cy))
                if typ == "circle": shape["rx"] = shape["ry"] = val
                else: shape["ry"] = val
        elif typ == "polyline" and key.startswith("pt:"):
            try:
                idx = int(key.split(":", 1)[1])
                pts = list(shape.get("points", []) or [])
                if 0 <= idx < len(pts): pts[idx] = (pos.x(), pos.y()); shape["points"] = pts
            except Exception:
                pass
        elif typ == "polyline" and (key.startswith("cp1:") or key.startswith("cp2:")):
            try:
                cp_type, seg_i_str = key.split(":", 1)
                seg_cps = shape.setdefault("seg_cps", {})
                if seg_i_str not in seg_cps:
                    seg_cps[seg_i_str] = {}
                seg_cps[seg_i_str][cp_type] = [pos.x(), pos.y()]
            except Exception:
                pass
        elif typ == "pattern_matrix" and key == "origin":
            shape["x"], shape["y"] = pos.x(), pos.y()
        self._static_cache = None
        self.panel.layout_changed.emit()

    def mouseMoveEvent(self, ev: QMouseEvent):
        screen_pos = ev.position()
        pos, tool = self._screen_to_world(screen_pos), self.panel.current_tool
        if self._panning and self._pan_start_screen is not None and self._pan_start_offset is not None:
            delta = screen_pos - self._pan_start_screen
            self._view_offset = QPointF(self._pan_start_offset.x() + delta.x(), self._pan_start_offset.y() + delta.y())
            self._static_cache = None
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update(); ev.accept(); return
        # 画布内变换 gizmo 拖拽：move=自由平移；x/y=约束平移；rotate=绕支点旋转。
        if self._gizmo_mode and self._gizmo_center is not None and self._gizmo_last_world is not None:
            cx, cy = self._gizmo_center
            if self._gizmo_mode.startswith("scale_"):
                # 角落缩放：以对角为固定支点等比缩放选中集。
                # factor = max(轴向投影比)（零宽/零高轴自动忽略），clamp [0.05, 100]。
                box = getattr(self, "_gizmo_scale_box", None)
                anchor = getattr(self, "_gizmo_scale_anchor", None)
                if box is not None and anchor is not None:
                    ax, ay = anchor
                    x1, y1, x2, y2 = box
                    wx = x2 - ax if abs(x2 - ax) >= abs(x1 - ax) else x1 - ax
                    wy = y2 - ay if abs(y2 - ay) >= abs(y1 - ay) else y1 - ay
                    cands: List[float] = []
                    if abs(wx) > 1e-9:
                        cands.append(abs((pos.x() - ax) / wx))
                    if abs(wy) > 1e-9:
                        cands.append(abs((pos.y() - ay) / wy))
                    factor = max(cands) if cands else 1.0
                    factor = max(0.05, min(100.0, factor))
                    if abs(factor - 1.0) > 1e-4:
                        self.panel.scale_current(factor, (ax, ay), defer_ui=True)
                        self._drag_moved = True
                        self._gizmo_ui_dirty = True
                self._gizmo_last_world = pos
                self._drag_pos = pos
                return
            if self._gizmo_mode == "rotate":
                cur = math.degrees(math.atan2(pos.y() - cy, pos.x() - cx))
                delta = cur - self._gizmo_last_angle
                if delta > 180.0: delta -= 360.0
                elif delta < -180.0: delta += 360.0
                if abs(delta) > 1e-4:
                    # ★ 性能：拖动会话内 defer_ui（refresh/layout_changed 合并到
                    #   release）——不再每 move 全量重建 41 组按钮 + 级联
                    #   save_state/xy/matrix 的 O(N) 同步。
                    self.panel.rotate_current(delta, center=(cx, cy), defer_ui=True)
                    self._gizmo_last_angle = cur
                    self._drag_moved = True
                    self._gizmo_ui_dirty = True
            else:
                dx = pos.x() - self._gizmo_last_world.x()
                dy = pos.y() - self._gizmo_last_world.y()
                if self._gizmo_mode == "x":
                    dy = 0.0
                elif self._gizmo_mode == "y":
                    dx = 0.0
                if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                    self.panel.translate_current(dx, dy, defer_ui=True)
                    self._drag_moved = True
                    self._gizmo_ui_dirty = True
                # 形心随平移移动，支点同步跟随
                self._gizmo_center = (cx + dx, cy + dy)
            self._gizmo_last_world = pos
            self._drag_pos = pos
            # ★ 增量重绘：translate/rotate_current(defer) 内部已按 旧+新 目标
            #   脏区 update(rect)——这里不再全画布 update()
            return
        if tool in ("line", "circle", "ellipse", "polyline"):
            self.setCursor(Qt.CursorShape.CrossCursor)
        if self._drag_handle:
            self._move_shape_handle(self._drag_handle[0], self._drag_handle[1], pos)
            self._drag_pos = pos
            self._drag_moved = True; self.update(); return
        if self._drag_shape_id and self._drag_start and tool == "select":
            last_pos = self._drag_pos or self._drag_start
            dx, dy = pos.x() - last_pos.x(), pos.y() - last_pos.y()
            shape = self._shape_by_id(self._drag_shape_id)
            if shape and (abs(dx) > 0.001 or abs(dy) > 0.001):
                # ★ R193/P1-3：defer 到 release（同 gizmo 先例）——per-move emit=60-144Hz 级联风暴
                _translate_shape(shape, dx, dy); self._drag_layout_dirty = True
            self._drag_pos = pos
            self._drag_moved = True
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.update(); return
        last_pos = self._drag_pos or pos
        self._drag_pos = pos
        if (self._drag_fixture_ids or self._drag_logical_id) and self._drag_start:
            self._drag_moved = True
            # ★ 性能：复用按下时构建的可见集（O(D×N) → O(D)）
            _vis = getattr(self, "_drag_visible_set", None)
            for fid in self._drag_fixture_ids:
                ox, oy = self._drag_fixture_offsets.get(fid, (0.0, 0.0))
                self.panel.set_fixture_pos(fid, pos.x() + ox, pos.y() + oy, visible=_vis)
            if self._drag_logical_id:
                self._move_drag_logical(pos)
            target = self._shape_for_fixture_drop(pos, tolerance=28.0)
            self._active_shape_id = str(target.get("id", "")) if target else ""
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            # ★ 增量重绘：只重画被拖设备 旧+新 位置 bbox（+gizmo/LOD 外延）
            self._incremental_update(self._fixture_drag_dirty_rect(last_pos, pos))
            return
        if self._drag_start:
            # ★ 框选/画线预览增量重绘：脏区 = (起点, 上次位置, 当前位置) bbox
            if tool == "polyline" and self._drawing_polyline:
                px, py = self._drawing_polyline[-1]
                box_rect = self._screen_rect_for_world(
                    min(px, last_pos.x(), pos.x()), min(py, last_pos.y(), pos.y()),
                    max(px, last_pos.x(), pos.x()), max(py, last_pos.y(), pos.y()),
                    margin_screen=6.0)
            else:
                box_rect = self._screen_rect_for_world(
                    min(self._drag_start.x(), last_pos.x(), pos.x()),
                    min(self._drag_start.y(), last_pos.y(), pos.y()),
                    max(self._drag_start.x(), last_pos.x(), pos.x()),
                    max(self._drag_start.y(), last_pos.y(), pos.y()),
                    margin_screen=6.0)
            self._incremental_update(box_rect)
            return
        self._hover_shape_id = ""
        # gizmo 悬停高亮（select 工具、未拖动时）
        if tool == "select":
            gh = self._gizmo_hit(pos)
            if gh != self._gizmo_hover:
                self._gizmo_hover = gh
            if gh:
                cur = Qt.CursorShape.SizeAllCursor if gh == "rotate" else (
                    Qt.CursorShape.SizeFDiagCursor if gh.startswith("scale_") else (
                        Qt.CursorShape.SizeHorCursor if gh == "x" else
                        Qt.CursorShape.SizeVerCursor if gh == "y" else Qt.CursorShape.OpenHandCursor))
                self.setCursor(cur)
                # ★ 悬停高亮只重绘 gizmo bbox（原全画布 update）
                self._incremental_update(self._gizmo_hover_dirty_rect())
                return
        else:
            self._gizmo_hover = ""
        h = self._handle_at(pos)
        if h:
            self._hover_shape_id = h[0]; self.setCursor(Qt.CursorShape.SizeAllCursor); self.update(); return
        if self._fixture_at(pos):
            self.setCursor(Qt.CursorShape.OpenHandCursor); self.update(); return
        shape = self._shape_at(pos)
        if shape:
            # ★ 曲线悬停显示十字拖动箭头（2026-08-03 用户：选中后可拖动的指示）
            self._hover_shape_id = str(shape.get("id", ""))
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if self._panning:
            self._panning = False
            self._pan_start_screen = None
            self._pan_start_offset = None
            self._pending_blank_clear = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            _flush_drag_deferred(self)  # ★ R194/NB-06
            ev.accept(); return
        if self._gizmo_mode:
            moved = self._drag_moved
            was_scale = self._gizmo_scale_anchor is not None
            self._gizmo_mode = ""
            self._gizmo_center = None
            self._gizmo_last_world = None
            self._gizmo_last_angle = 0.0
            self._gizmo_scale_anchor = None
            self._gizmo_scale_box = None
            if moved:
                # 真正变换过：提交布局，结束本次交互。
                # ★ 性能：补上拖动会话内 defer 掉的 refresh()（组按钮/索引重建
                #   一次），layout_changed 由下面原有 emit 统一提交。
                self._drag_start = None; self._drag_pos = None
                self._pending_blank_clear = False
                self.setCursor(Qt.CursorShape.ArrowCursor)
                if getattr(self, "_gizmo_ui_dirty", False):
                    self._gizmo_ui_dirty = False
                    self.panel.refresh()
                if was_scale:
                    # 缩放收尾：_finish_geometry_change 内部刷新同步基准
                    # (_refresh_sync_reference_for_ids) + save_state + layout_changed，
                    # 十字线方向随新几何重算（2026-08-08 W3 角落缩放手柄）。
                    self.panel._finish_geometry_change(list(self.panel._ordered_selected_ids()))
                else:
                    self.panel.layout_changed.emit()
                self.update(); return
            # 未拖动（只是点了一下 gizmo 区域）：回落到 select 工具逻辑，让空画布点击能清空选择。
        if self._drag_handle:
            self._pending_blank_clear = False
            self._drag_handle = None; self._drag_start = None; self._drag_pos = None; self.panel.layout_changed.emit(); self.update(); return
        if self._drag_shape_id:
            self._pending_blank_clear = False
            self._drag_shape_id = ""; self._drag_start = None; self._drag_pos = None; self._drag_layout_dirty = False
            self.panel.layout_changed.emit(); self.setCursor(Qt.CursorShape.ArrowCursor); self.update(); return  # ★ R193/P1-3：release 统一提交
        if self._drag_fixture_ids or self._drag_logical_id:
            dropped = self._finish_fixture_drop_to_shape()
            self._drag_fixture_ids = []; self._drag_fixture_offsets = {}; self._drag_start = None; self._drag_pos = None
            self._drag_logical_id = ""; self._drag_ld_offset = (0.0, 0.0); self._drag_visible_set = None
            self._pending_blank_clear = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if not dropped:
                self.panel.layout_changed.emit(); self.panel.refresh()
            return
        if not self._drag_start: return
        pos, start, tool = self._screen_to_world(ev.position()), self._drag_start, self.panel.current_tool
        if tool == "line" and math.hypot(pos.x() - start.x(), pos.y() - start.y()) > 4:
            self.panel.add_shape({"id": str(uuid.uuid4()), "type": "line", "x1": start.x(), "y1": start.y(), "x2": pos.x(), "y2": pos.y()})
        elif tool in ("circle", "ellipse"):
            rx, ry = abs(pos.x()-start.x()), abs(pos.y()-start.y())
            if tool == "circle": ry = rx
            if rx > 3 and ry > 3:
                self.panel.add_shape({"id": str(uuid.uuid4()), "type": tool, "cx": start.x(), "cy": start.y(), "rx": max(5, rx), "ry": max(5, ry)})
        elif tool == "select":
            r = QRectF(start, pos).normalized()
            if r.width() > 4 and r.height() > 4:
                box_ids = self._selection_box_fixture_ids(start, pos)
                if box_ids:
                    if self._pending_blank_clear:
                        self.panel._reset_box_selection_chain()
                    self.panel._apply_box_selection(box_ids, emit=True)
                elif self._pending_blank_clear:
                    self.panel._clear_selection(emit=False)
                    self.panel.emit_selection()
            elif self._pending_blank_clear:
                self.panel._clear_selection(emit=False)
                self.panel.emit_selection()
        self._pending_blank_clear = False
        self._drag_start = None; self._drag_pos = None; self._active_shape_id = ""; self.setCursor(Qt.CursorShape.ArrowCursor); self.update()

    def leaveEvent(self, ev):
        self._hover_shape_id = ""; self._gizmo_hover = ""; self.update(); super().leaveEvent(ev)

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        """双击旋转环：弹出输入框，按精确角度旋转选区。"""
        if self.panel.current_tool != "select" or ev.button() != Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(ev)
        pos = self._screen_to_world(ev.position())
        if self._gizmo_hit(pos) == "rotate":
            g = self._gizmo_geom()
            angle, ok = QInputDialog.getDouble(
                self, "旋转选中设备", "角度（°，顺时针为正）：", 90.0, -360.0, 360.0, 1)
            if ok and abs(angle) > 1e-6 and g is not None:
                self.panel.rotate_current(float(angle), center=(g["cx"], g["cy"]))
            ev.accept(); return
        super().mouseDoubleClickEvent(ev)

    def _drag_payload_fixture_ids(self, ev) -> List[str]:
        mime = ev.mimeData()
        if mime.hasFormat("application/x-dmx-fixture-group"):
            try:
                data = bytes(mime.data("application/x-dmx-fixture-group")).decode("utf-8")
                payload = json.loads(data)
                return [str(x) for x in payload.get("fixture_ids", [])]
            except Exception:
                return []
        return []

    def dragEnterEvent(self, ev: QDragEnterEvent):
        if ev.mimeData().hasFormat("application/x-dmx-fixture-group") or self.panel.selected_ids:
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev: QDragMoveEvent):
        target = self._shape_at(self._screen_to_world(ev.position()), tolerance=20.0)
        sid = str(target.get("id", "")) if target else ""
        if sid != self._active_shape_id:
            self._active_shape_id = sid
            self.update()
        if target:
            ev.acceptProposedAction()
        else:
            ev.acceptProposedAction()

    def dragLeaveEvent(self, ev):
        self._active_shape_id = ""
        self.update()
        super().dragLeaveEvent(ev)

    def _drag_payload_group(self, ev) -> tuple[List[str], str]:
        mime = ev.mimeData()
        if mime.hasFormat("application/x-dmx-fixture-group"):
            try:
                data = bytes(mime.data("application/x-dmx-fixture-group")).decode("utf-8")
                payload = json.loads(data)
                return [str(x) for x in payload.get("fixture_ids", [])], str(payload.get("group_id", ""))
            except Exception:
                return [], ""
        return [], ""

    def dropEvent(self, ev: QDropEvent):
        pos = self._screen_to_world(ev.position())
        target = self._shape_at(pos, tolerance=20.0)
        ids, gid = self._drag_payload_group(ev)
        if ids:
            if target is None:
                target = self._shape_at(pos, tolerance=34.0)
            self._active_shape_id = str(target.get("id", "")) if target else ""
            # ★ R137：拖放落点前先压撤销快照（模块级包装，与内部同 token 去重）
            _add_group_to_canvas_with_undo(self.panel, ids, pos, target, gid)
            self._active_shape_id = ""
            self.update()
            ev.acceptProposedAction()
            return

        # 兼容旧逻辑：如果外部拖放没有携带设备组 payload，则仅对当前画板中已选设备重排到目标图形。
        if target and self.panel.selected_ids:
            # ★ R137：拖放重排设备到目标图形（直写坐标）——动作入口自打撤销快照
            _undo_snapshot_action(self.panel, "drop_to_shape")
            self._active_shape_id = str(target.get("id", ""))
            ids = self.panel._ordered_selected_ids()
            pts = _shape_points(target, len(ids))
            for fid, (x, y) in zip(ids, pts):
                self.panel.set_fixture_pos(fid, x, y)
            self.panel._refresh_sync_reference_for_ids(ids)
            self.panel._set_selection(ids, emit=False); self.panel.emit_selection(); self.panel.layout_changed.emit(); self.update()
            ev.acceptProposedAction()
        else:
            self._active_shape_id = ""
            self.update()
            ev.ignore()

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        if delta:
            self._zoom_at(ev.position(), 1.25 if delta > 0 else 1 / 1.25)
            ev.accept(); return
        super().wheelEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_0,) and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.reset_view(); ev.accept(); return
        pan_step = 80.0
        if ev.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            dx = pan_step if ev.key() == Qt.Key.Key_Right else (-pan_step if ev.key() == Qt.Key.Key_Left else 0.0)
            dy = pan_step if ev.key() == Qt.Key.Key_Down else (-pan_step if ev.key() == Qt.Key.Key_Up else 0.0)
            self._view_offset = QPointF(self._view_offset.x() - dx, self._view_offset.y() - dy)
            self._static_cache = None
            self.update(); ev.accept(); return
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.panel._ordered_selected_ids():
                self.panel.remove_selected_from_canvas()
                ev.accept()
                return
            if self._selected_shape_id:
                self._delete_shape(self._selected_shape_id)
                ev.accept()
                return
        super().keyPressEvent(ev)



def _spline_order_ts(ordered, pts, sp_pts) -> Dict[str, float]:
    """设备沿样条参数位置 t∈[0,1]（spline_param 折线投影, numpy 批量逐位等价）。

    与 geometry_splines.spline_param 同段序/同算术/clamp/退化段跳过；
    IEEE 双精度广播逐元素 = Python 标量。_spline_cluster_angles 阶段 2
    排序键批量版（性能 2026-08-03：全选 4249 台时 spline_param 排序 1.7s）。
    """
    import numpy as _np
    cp = _np.asarray(sp_pts, dtype=float)
    seg = cp[1:] - cp[:-1]
    seg2 = (seg * seg).sum(axis=1)
    ok = seg2 > 1e-12
    k_ok = _np.flatnonzero(ok)
    n_seg = max(1, len(sp_pts) - 1)
    if k_ok.size == 0:
        return {fid: 0.0 for fid in ordered}
    ax = cp[:-1][ok, 0]
    az = cp[:-1][ok, 1]
    vx = seg[ok, 0]
    vz = seg[ok, 1]
    s2k = seg2[ok]
    P = _np.asarray([pts[fid] for fid in ordered], dtype=float)
    t_mat = ((P[:, 0, None] - ax[None, :]) * vx[None, :]
             + (P[:, 1, None] - az[None, :]) * vz[None, :]) / s2k[None, :]
    t_mat = _np.clip(t_mat, 0.0, 1.0)
    px = ax[None, :] + vx[None, :] * t_mat
    pz = az[None, :] + vz[None, :] * t_mat
    d2 = ((P[:, 0, None] - px) * (P[:, 0, None] - px)
          + (P[:, 1, None] - pz) * (P[:, 1, None] - pz))
    kv = _np.argmin(d2, axis=1)
    ts = (k_ok[kv] + t_mat[_np.arange(P.shape[0]), kv]) / n_seg
    return {fid: float(t) for fid, t in zip(ordered, ts)}



def _normalize_xy_ref(panel, raw_xy_ref):
    """把存档里的 `xy_reference_by_fixture` 规整为运行时形态（逐设备）。

    ★★ 深查修正（XY 配置静默丢失）：原实现按 `mode == "sync"` **过滤**，而
      `save_state` 写的是**整份**字典 → 所有非 sync（default/自定义角度等）设备的
      XY 参考配置在存盘/还原往返中被静默丢弃（用户："我设好的角度回来就没了"）。
      保存写什么、加载就读什么：这里只做类型过滤，不再按 mode 丢数据。
    """
    if not isinstance(raw_xy_ref, dict):
        return {}
    out = {}
    for fid, cfg in raw_xy_ref.items():
        if not isinstance(cfg, dict):
            continue
        base = _safe_float(cfg.get("base_angle_deg", cfg.get("angle_deg", 0.0)), 0.0)
        out[str(fid)] = {
            **dict(cfg),
            "base_angle_deg": base,
            "angle_deg": float(panel._sync_angle_with_global(base, dict(cfg))),
        }
    return out
def _release_stale_sync_entries(ref_by_fid: dict, keep_ids) -> int:
    """释放不在本次同步目标内的旧同步条目（返回清除数）。

    ★★ 2026-09-17 用户实报（换设备组后，老设备组的 Z 轴仍在受控）**根治**：
      原 `apply_xy_reference_mode` 只**写入**本次目标，从不清理早先同步过、
      如今不在目标内的条目 → `_xy_reference_by_fixture` 只增不减：
        ① 旧设备继续保持 sync 态，继续吃全局角与 Z 命令（用户实测症状）；
        ② Z 命令是**全局广播**（`NetworkManager.set_xy_reference_z_command`
           作用于整个 `xy_reference_by_universe`）→ 任何残留条目都会响应，
           故必须按**全集替换**清理，而不是只清当前页。
      统一语义：一次「同步」= 本次目标集替换整个同步集合；不在目标内的旧条目
      一律释放（等价于对它们执行"取消同步"）。
      例外：`x_only_align` 尖点对（由 `_apex_xonly_align_ids` 在写入段显式
      加入，不来自用户选择）不在此清理。
    """
    keep = {str(f) for f in (keep_ids or ())}
    removed = 0
    for fid in list(ref_by_fid.keys()):
        if str(fid) in keep:
            continue
        cfg = ref_by_fid.get(str(fid)) or {}
        if str(cfg.get("mode", "")).lower() != "sync":
            continue
        if bool(cfg.get("x_only_align", False)):
            continue
        ref_by_fid.pop(str(fid), None)
        removed += 1
    return removed


def _canonicalize_base_to_install(panel, fid: str, base_deg: float) -> float:
    """把同步基准对齐到离**真实安装朝向**最近的 180° 代表（幂等）。

    ★★ 2026-09-17 用户实报（三维扇子内圈两台同步后全部电机反向）
    **系统级漏斗修正**：
      引擎语义「世界 = 安装朝向 + 电机角」（静止电机 = 目标 − 基准）要求
      **基准必须与真实安装同向**（|偏差| ≤ 90°）。而基准由多条分支产出
      （弧线切向 / 圆覆盖 / 星形臂 / 倒装 +180 / CR 自拟合），任一分支落到
      错误的 180° 代表上，该台世界方向就整体反 180°（用户实测：内圈两台
      全部电机反向、且仅同步模式）。此前各轮都在逐个分支上打补丁，仍会
      遗漏（半圈设备 |切线|>90° 时 ±90 折叠是有损的）。
      本函数提供**唯一不变量**：无论上游给什么值，一律校正到与安装同向的
      代表。故在上层唯一写入口调用即可覆盖全部上游分支与历史存档值。
    幂等：已正确的基准逐位不变（至冬7.0 副池 base==install → 零变化）；
    例外：2D XY（base 语义是画布镜像 −安装）与无 rot_y 数据（旧工程/手动
    配接）不参与校正，与 `_xy_install_angle` 的门控一致。
    """
    fi = panel.fixtures_by_id().get(str(fid))
    if fi is None:
        return base_deg
    if panel._fixture_is_2d_xy(str(fid)):
        return base_deg
    install = installation_heading(fi)
    if install is None:
        return base_deg
    # ★ 用**圆上有向距离**比较两个 180° 代表，避免 ±180 边界的归一化歧义
    #   （`_normalize_angle_deg(−180)` 返回 +180，用 abs(delta)>90 判定会在
    #    恰差 180° 时两个代表"同样远"而误判）。d(θ)=cos 距离，取更近者。
    b = float(base_deg)
    d_same = math.cos(math.radians(b - float(install)))
    d_flip = math.cos(math.radians(b + 180.0 - float(install)))
    if d_flip > d_same + 1e-12:
        return _normalize_angle_deg(b + 180.0)
    return base_deg
def build_topview_ui(panel) -> None:
    """_build_ui 的构建段（2026-09-18 可维护性搬移，语义零变化）。

        ★ 纯搬移：`self.X` 已按 AST 精确重写为 `panel.X`（210 处），无手改；
          依赖本模块常量/helper，故留在同文件（拆文件会循环 import）。
        """
    btn_autogroup_save, root, top, top_scroll, top_widget = build_topview_ui_head(panel)
    left, left_scroll, lf, qpf, quick_place_box, splitter = build_topview_ui_left_panel(panel, btn_autogroup_save=btn_autogroup_save, root=root, top=top, top_scroll=top_scroll, top_widget=top_widget)
    btn_ref_default, ref_box, ref_row, rf = build_topview_ui_spacing_tools(panel, lf, qpf, quick_place_box)
    build_topview_ui_xy_ref_and_canvas(panel, btn_ref_default=btn_ref_default, left=left, left_scroll=left_scroll, lf=lf, ref_box=ref_box, ref_row=ref_row, rf=rf, root=root, splitter=splitter)
    from .topview_workspace_ui import install_workspace
    install_workspace(panel, root=root, splitter=splitter, donor=left, old_header=top_scroll)

def build_topview_ui_head(panel) -> tuple[object, object, object, object, object]:
    """build_topview_ui 的第 1/4 段（机械切分，语义零变化）。"""
    """_build_ui 的构建段（2026-09-18 可维护性搬移，语义零变化）。

    ★ 纯搬移：`self.X` 已按 AST 精确重写为 `panel.X`（210 处），无手改；
      依赖本模块常量/helper，故留在同文件（拆文件会循环 import）。
    """
    root = QVBoxLayout(panel); root.setContentsMargins(6,6,6,6); root.setSpacing(4)
    panel.setMinimumSize(0, 0)
    panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    top_scroll = QScrollArea()
    top_scroll.setWidgetResizable(True)
    top_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    top_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    top_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    top_scroll.setMinimumHeight(34)
    top_scroll.setMaximumHeight(52)
    top_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    top_widget = QWidget()
    top_widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    top = QHBoxLayout(top_widget); top.setContentsMargins(0,0,0,0); top.setSpacing(8)
    # ★ R178 中文化+data 键（用户可见名与内部键分离；track=画轨道选设备）
    from .topview_track_draw import build_tool_combo
    panel._tool_combo = build_tool_combo(panel)
    top.addWidget(QLabel("工具:")); top.addWidget(panel._tool_combo)
    for text, fn, tip in [
            ("刷新设备组", panel.manual_refresh, "立即同步 Fixture 设备组展板"),
            ("新增页", panel.add_page, "新建一张空白画布页"),
            ("复制页", panel.copy_page, "复制当前页（含图形与设备布局）"),
            ("重命名页", panel.rename_page, "重命名当前页"),
            ("删除页", panel.delete_page, "删除当前页"),
            ("删除图形", panel.delete_last_shape, "删除最近绘制的图形"),
            ("移除选中设备", panel.remove_selected_from_canvas, "把选中的设备从画布移除")]:
        b = QPushButton(text); b.setFixedHeight(24); b.setToolTip(tip); b.clicked.connect(fn); top.addWidget(b)
    top.addStretch()
    # ★ 整体缩放（几何级：连带设备坐标+图线一起缩放，写回存储，影响 Matrix/路由几何）
    top.addWidget(QLabel("整体缩放"))
    panel._scale_spin = QDoubleSpinBox()
    panel._scale_spin.setRange(0.1, 5.0)
    panel._scale_spin.setSingleStep(0.1)
    panel._scale_spin.setValue(1.0)
    panel._scale_spin.setDecimals(2)
    panel._scale_spin.setFixedWidth(64)
    panel._scale_spin.setToolTip("以当前页形心为中心几何缩放：设备坐标与图线一起缩放并写回存储")
    top.addWidget(panel._scale_spin)
    b_scale = QPushButton("应用"); b_scale.setFixedHeight(24)
    b_scale.setToolTip("以当前页形心为中心，按输入比例几何缩放（设备坐标与图线一起写回存储）")
    b_scale.clicked.connect(lambda: panel.scale_current_page(float(panel._scale_spin.value())))
    top.addWidget(b_scale)
    b_s08 = QPushButton("×0.8"); b_s08.setFixedHeight(24)
    b_s08.setToolTip("整体缩小到 80%")
    b_s08.clicked.connect(lambda: panel.scale_current_page(0.8))
    top.addWidget(b_s08)
    b_s125 = QPushButton("×1.25"); b_s125.setFixedHeight(24)
    b_s125.setToolTip("整体放大到 125%")
    b_s125.clicked.connect(lambda: panel.scale_current_page(1.25))
    top.addWidget(b_s125)
    # ★ Excel 导入/导出按钮
    btn_export = QPushButton("导出布局表")
    btn_export.setFixedHeight(24)
    btn_export.setToolTip("将当前 TopView 设备坐标导出为 .xlsx 表格")
    btn_export.clicked.connect(panel._export_layout_excel)
    top.addWidget(btn_export)
    btn_import = QPushButton("导入布局表")
    btn_import.setFixedHeight(24)
    btn_import.setToolTip("从 .xlsx 表格导入设备坐标")
    btn_import.clicked.connect(panel._import_layout_excel)
    top.addWidget(btn_import)
    btn_merge = QPushButton("按类型合并")
    btn_merge.setFixedHeight(24)
    btn_merge.setToolTip("选 2 种设备类型合并：灯+非灯按就近配灯，否则按下标配对（永久；TopView 单点）")
    btn_merge.clicked.connect(panel.merge_device_types_dialog)
    top.addWidget(btn_merge)
    btn_clear_merge = QPushButton("清除合并")
    btn_clear_merge.setFixedHeight(24)
    btn_clear_merge.setToolTip("清除全部虚拟合并设备（含旧的错误两两组），成员恢复为单独显示")
    btn_clear_merge.clicked.connect(panel.clear_all_logical_devices)
    top.addWidget(btn_clear_merge)
    # ★ 自动编组（多圆拓扑推荐）：仅多圆布局有效；选中一组→可换一组→满意保存为设备组。
    btn_autogroup = QPushButton("自动编组")
    btn_autogroup.setFixedHeight(24)
    btn_autogroup.setToolTip("多圆布局拓扑推荐：连续圆弧拼弧线 / 各圆同相位小弧成排。点此选中一组，"
                             "「换一组」翻新，满意后「保存编组」存为 Fixture 设备组。")
    btn_autogroup.clicked.connect(panel.auto_group_start)
    top.addWidget(btn_autogroup)
    btn_autogroup_next = QPushButton("换一组")
    btn_autogroup_next.setFixedHeight(24)
    btn_autogroup_next.setToolTip("换下一个候选编组；候选用尽则换随机种子翻新（同种子可复现）")
    btn_autogroup_next.clicked.connect(panel.auto_group_next)
    top.addWidget(btn_autogroup_next)
    btn_autogroup_save = QPushButton("保存编组")
    btn_autogroup_save.setFixedHeight(24)
    return (btn_autogroup_save, root, top, top_scroll, top_widget)


def build_topview_ui_left_panel(panel, *, btn_autogroup_save, root, top, top_scroll, top_widget) -> tuple[object, object, object, object, object, object]:
    """build_topview_ui 的第 2/4 段（机械切分，语义零变化）。"""
    btn_autogroup_save.setToolTip("将当前自动编组（含成员顺序）保存为 Fixture 设备组")
    btn_autogroup_save.clicked.connect(panel.save_live_selection_to_fixture_group)
    top.addWidget(btn_autogroup_save)
    top_scroll.setWidget(top_widget)
    root.addWidget(top_scroll)
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    left_scroll = QScrollArea()
    left_scroll.setWidgetResizable(True)
    left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    left_scroll.setMinimumSize(0, 0)
    left_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    left = QWidget(); left.setMinimumWidth(0); left.setMaximumWidth(16777215); left.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    lf = QVBoxLayout(left); lf.setContentsMargins(0,0,0,0); lf.setSpacing(6)
    lf.addWidget(QLabel("Fixture 设备组展板（拖入画板才显示）"))
    panel._group_combo = QComboBox(); panel._group_combo.hide()  # 兼容旧状态/外部调用，不再作为主要交互入口
    panel._group_combo.currentIndexChanged.connect(panel._on_group_changed)
    panel._group_scroll = QScrollArea(); panel._group_scroll.setWidgetResizable(True); panel._group_scroll.setMinimumHeight(150)
    panel._group_board = QWidget(); panel._group_grid = QGridLayout(panel._group_board); panel._group_grid.setContentsMargins(4,4,4,4); panel._group_grid.setSpacing(2)
    panel._group_scroll.setWidget(panel._group_board)
    lf.addWidget(panel._group_scroll)
    panel._group_hint = QLabel("在 Fixture 中创建设备组后会同步显示在这里。拖到右侧画板即可放入并显示；点击已放入的组可选中其设备。")
    panel._group_hint.setWordWrap(True); panel._group_hint.setStyleSheet(f"color:{_th.TEXT_SECONDARY};font-size:11px;")
    lf.addWidget(panel._group_hint)
    row = QHBoxLayout(); b1 = QPushButton("沿最后图形排布"); b1.clicked.connect(panel.arrange_group_on_last_shape); row.addWidget(b1); b2 = QPushButton("选中设备排布"); b2.clicked.connect(lambda: panel.arrange_group_on_last_shape(use_selected=True)); row.addWidget(b2); lf.addLayout(row)

    quick_place_box = QGroupBox("快捷放置（无需画线）"); qpf = QFormLayout(quick_place_box)
    panel._quick_spacing = QDoubleSpinBox(); panel._quick_spacing.setRange(1, 1000); panel._quick_spacing.setValue(54.0)
    panel._quick_matrix_cols = QSpinBox(); panel._quick_matrix_cols.setRange(1, 200); panel._quick_matrix_cols.setValue(8)
    panel._quick_matrix_y = QDoubleSpinBox(); panel._quick_matrix_y.setRange(1, 1000); panel._quick_matrix_y.setValue(42.0)
    panel._quick_circle_radius = QDoubleSpinBox(); panel._quick_circle_radius.setRange(10, 3000); panel._quick_circle_radius.setValue(120.0)
    qpf.addRow("横向间距", panel._quick_spacing)
    qpf.addRow("矩阵列数", panel._quick_matrix_cols)
    qpf.addRow("矩阵纵距", panel._quick_matrix_y)
    qpf.addRow("圆形半径", panel._quick_circle_radius)
    # ★ 拖入按导入排布（2026-08-03 用户）：开启后拖组进新页面按导入工程时
    #   该组设备的排布放置（形心对齐落点），而非默认线排/矩阵。
    panel._use_import_layout = False
    panel._chk_import_layout = QCheckBox("拖入按导入排布")
    panel._chk_import_layout.setToolTip("开启后，把设备组拖进新页面时按导入工程时该组的排布放置（形心对齐落点）")
    panel._chk_import_layout.setStyleSheet(f"color:{_th.TEXT_TERTIARY};font-size:10px;")
    panel._chk_import_layout.toggled.connect(lambda on: setattr(panel, "_use_import_layout", bool(on)))
    qpf.addRow(panel._chk_import_layout)
    qrow1 = QHBoxLayout()
    qb_line = QPushButton("直线"); qb_line.clicked.connect(lambda: panel.quick_place("line")); qrow1.addWidget(qb_line)
    qb_matrix = QPushButton("矩阵"); qb_matrix.clicked.connect(lambda: panel.quick_place("matrix")); qrow1.addWidget(qb_matrix)
    qb_circle = QPushButton("圆形"); qb_circle.clicked.connect(lambda: panel.quick_place("circle")); qrow1.addWidget(qb_circle)
    qpf.addRow(qrow1)
    qrow2 = QHBoxLayout()
    qb_rot_l = QPushButton("左转15°"); qb_rot_l.clicked.connect(lambda: panel.rotate_selected_shape(-15.0)); qrow2.addWidget(qb_rot_l)
    qb_rot_r = QPushButton("右转15°"); qb_rot_r.clicked.connect(lambda: panel.rotate_selected_shape(15.0)); qrow2.addWidget(qb_rot_r)
    qpf.addRow(qrow2)
    # ★ 组间距离（2026-08-08 W3 恢复）：滑块按 % 相对拉开/收拢图形/设备簇
    panel._layout_spacing_default_percent = 100
    panel._layout_spacing_slider = QSlider(Qt.Orientation.Horizontal)
    panel._layout_spacing_slider.setRange(0, 1000)
    panel._layout_spacing_slider.setSingleStep(5)
    panel._layout_spacing_slider.setPageStep(25)
    panel._layout_spacing_slider.setValue(panel._layout_spacing_default_percent)
    return (left, left_scroll, lf, qpf, quick_place_box, splitter)


def build_topview_ui_spacing_tools(panel, lf, qpf, quick_place_box) -> tuple[object, object, object, object]:
    """build_topview_ui 的第 3/4 段（机械切分，语义零变化）。"""
    panel._layout_spacing_slider.setToolTip("相对调整图形/设备簇之间的距离；100% 不变，小于 100% 收拢，大于 100% 放大。")
    panel._layout_spacing_label = QLabel(f"{panel._layout_spacing_default_percent}%")
    panel._layout_spacing_label.setMinimumWidth(58)
    panel._layout_spacing_slider.valueChanged.connect(
        lambda v: panel._layout_spacing_label.setText(f"{int(v)}%"))
    spacing_row = QHBoxLayout()
    spacing_row.addWidget(panel._layout_spacing_slider, 1)
    spacing_row.addWidget(panel._layout_spacing_label)
    qpf.addRow("组间距离", spacing_row)
    qrow_spacing = QHBoxLayout()
    qb_space_apply = QPushButton("应用组间距离")
    qb_space_apply.clicked.connect(panel.apply_layout_group_spacing)
    qrow_spacing.addWidget(qb_space_apply)
    qb_space_reset = QPushButton(f"重置{panel._layout_spacing_default_percent}%")
    def _reset_spacing_percent():
        panel._layout_spacing_slider.setValue(panel._layout_spacing_default_percent)
    qb_space_reset.clicked.connect(_reset_spacing_percent)
    qrow_spacing.addWidget(qb_space_reset)
    qpf.addRow(qrow_spacing)
    # ★ 选区几何变换（2026-08-08 恢复）：设备绕选区质心旋转/翻面，图形随动
    panel._fixture_rotate_angle = QDoubleSpinBox()
    panel._fixture_rotate_angle.setRange(-3600.0, 3600.0)
    panel._fixture_rotate_angle.setDecimals(1)
    panel._fixture_rotate_angle.setSingleStep(1.0)
    panel._fixture_rotate_angle.setValue(15.0)
    panel._fixture_rotate_angle.setSuffix("°")
    qpf.addRow("选区旋转角", panel._fixture_rotate_angle)
    qrow_rot = QHBoxLayout()
    qb_rot_sel_l = QPushButton("选区左转")
    qb_rot_sel_l.clicked.connect(lambda: panel.rotate_selected_fixtures(-abs(float(panel._fixture_rotate_angle.value()))))
    qrow_rot.addWidget(qb_rot_sel_l)
    qb_rot_sel_r = QPushButton("选区右转")
    qb_rot_sel_r.clicked.connect(lambda: panel.rotate_selected_fixtures(abs(float(panel._fixture_rotate_angle.value()))))
    qrow_rot.addWidget(qb_rot_sel_r)
    qpf.addRow(qrow_rot)
    qrow_flip = QHBoxLayout()
    qb_flip_x = QPushButton("绕X轴翻面")
    qb_flip_x.clicked.connect(lambda: panel.flip_selected_fixtures("x"))
    qrow_flip.addWidget(qb_flip_x)
    qb_flip_y = QPushButton("绕Y轴翻面")
    qb_flip_y.clicked.connect(lambda: panel.flip_selected_fixtures("y"))
    qrow_flip.addWidget(qb_flip_y)
    qpf.addRow(qrow_flip)
    lf.addWidget(quick_place_box)

    sel_box = QGroupBox("实时选择工具（框选后再筛选）"); form = QFormLayout(sel_box)
    panel._live_sel_label = QLabel("实时组：0 台")
    panel._live_sel_label.setWordWrap(True)
    panel._live_sel_label.setStyleSheet(f"color:{_th.FOCUS_BLUE};font-size:11px;")
    form.addRow(panel._live_sel_label)

    quick_row1 = QHBoxLayout()
    panel._btn_sel_all = QPushButton("全部"); panel._btn_sel_all.clicked.connect(lambda: panel.apply_selection_preset("all")); quick_row1.addWidget(panel._btn_sel_all)
    panel._btn_sel_odd = QPushButton("奇数"); panel._btn_sel_odd.clicked.connect(lambda: panel.apply_selection_preset("odd")); quick_row1.addWidget(panel._btn_sel_odd)
    panel._btn_sel_even = QPushButton("偶数"); panel._btn_sel_even.clicked.connect(lambda: panel.apply_selection_preset("even")); quick_row1.addWidget(panel._btn_sel_even)
    form.addRow(quick_row1)

    quick_row2 = QHBoxLayout()
    panel._btn_sel_left = QPushButton("左半"); panel._btn_sel_left.clicked.connect(lambda: panel.apply_selection_preset("left")); quick_row2.addWidget(panel._btn_sel_left)
    panel._btn_sel_right = QPushButton("右半"); panel._btn_sel_right.clicked.connect(lambda: panel.apply_selection_preset("right")); quick_row2.addWidget(panel._btn_sel_right)
    form.addRow(quick_row2)

    panel._sel_take = QSpinBox(); panel._sel_take.setRange(1, 9999); panel._sel_take.setValue(1)
    panel._sel_start = QSpinBox(); panel._sel_start.setRange(1, 9999); panel._sel_start.setValue(1)
    panel._sel_gap = QSpinBox(); panel._sel_gap.setRange(0, 9999); panel._sel_gap.setValue(1)
    form.addRow("选几个", panel._sel_take); form.addRow("从第几个", panel._sel_start); form.addRow("间隔几个", panel._sel_gap)
    bsel = QPushButton("应用间隔实时选择"); bsel.clicked.connect(panel.apply_selection_filter); form.addRow(bsel)
    bsave = QPushButton("保存实时组到 Fixture"); bsave.clicked.connect(panel.save_live_selection_to_fixture_group); form.addRow(bsave)
    lf.addWidget(sel_box)
    ref_box = QGroupBox("XY 基准模式"); rf = QFormLayout(ref_box)
    panel._xy_ref_label = QLabel("选区：0 台 / 同步：0 台")
    panel._xy_ref_label.setWordWrap(True)
    panel._xy_ref_label.setStyleSheet(f"color:{_th.FOCUS_BLUE};font-size:11px;")
    rf.addRow(panel._xy_ref_label)
    ref_row = QHBoxLayout()
    btn_ref_default = QPushButton("默认模式")
    return (btn_ref_default, ref_box, ref_row, rf)


def build_topview_ui_xy_ref_and_canvas(panel, *, btn_ref_default, left, left_scroll, lf, ref_box, ref_row, rf, root, splitter):
    """build_topview_ui 的第 4/4 段（机械切分，语义零变化）。"""
    btn_ref_default.clicked.connect(lambda: panel.apply_xy_reference_mode("default"))
    ref_row.addWidget(btn_ref_default)
    btn_ref_sync = QPushButton("同步模式")
    btn_ref_sync.clicked.connect(lambda: panel.apply_xy_reference_mode("sync"))
    ref_row.addWidget(btn_ref_sync)
    rf.addRow(ref_row)
    panel._xy_ref_global_angle = QDoubleSpinBox()
    panel._xy_ref_global_angle.setRange(-180.0, 180.0)
    panel._xy_ref_global_angle.setDecimals(2)
    panel._xy_ref_global_angle.setSingleStep(1.0)
    panel._xy_ref_global_angle.setValue(panel._xy_reference_global_angle_deg)
    panel._xy_ref_global_angle.valueChanged.connect(panel.set_xy_reference_global_angle)
    rf.addRow("基准旋转角", panel._xy_ref_global_angle)
    # ★ 扇面角度（2026-08-04 用户）：三维设备 Z 轴（自转/扇面）同步基准方向。
    #   默认 0° = 水平 +X；改变后全部同步三维设备的 motor_z 跟随，扇面水平平行。
    panel._xy_ref_z_angle = QDoubleSpinBox()
    panel._xy_ref_z_angle.setRange(-180.0, 180.0)
    panel._xy_ref_z_angle.setDecimals(2)
    panel._xy_ref_z_angle.setSingleStep(1.0)
    panel._xy_ref_z_angle.setValue(panel._xy_reference_z_angle_deg)
    panel._xy_ref_z_angle.valueChanged.connect(panel.set_xy_reference_z_angle)
    rf.addRow("扇面角度", panel._xy_ref_z_angle)
    lf.addWidget(ref_box)
    mx_box = QGroupBox("花样矩阵"); mf = QFormLayout(mx_box)
    panel._mx_rows = QSpinBox(); panel._mx_rows.setRange(1, 32); panel._mx_rows.setValue(3)
    panel._mx_base = QSpinBox(); panel._mx_base.setRange(1, 200); panel._mx_base.setValue(8)
    panel._mx_dx = QDoubleSpinBox(); panel._mx_dx.setRange(1, 500); panel._mx_dx.setValue(40)
    panel._mx_dy = QDoubleSpinBox(); panel._mx_dy.setRange(1, 500); panel._mx_dy.setValue(40)
    panel._mx_delta = QSpinBox(); panel._mx_delta.setRange(-50, 50); panel._mx_delta.setValue(-1)
    panel._mx_rows_text = QLineEdit(); panel._mx_rows_text.setPlaceholderText("可选：每行=count,dx,dy,offset；如 8,40,35,0;6,45,42,20")
    for lab, w in [("行数", panel._mx_rows), ("首行数量", panel._mx_base), ("横间隔", panel._mx_dx), ("纵间隔", panel._mx_dy), ("每行数量变化", panel._mx_delta), ("每行自定义", panel._mx_rows_text)]: mf.addRow(lab, w)
    bmx = QPushButton("生成矩阵并排布组"); bmx.clicked.connect(panel.create_pattern_matrix_and_arrange); mf.addRow(bmx); lf.addWidget(mx_box); lf.addStretch()
    left_scroll.setWidget(left)
    splitter.addWidget(left_scroll)
    right = QWidget(); right.setMinimumSize(0, 0); right.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    rv = QVBoxLayout(right); rv.setContentsMargins(0,0,0,0); rv.setSpacing(4)
    # 页签仅用于切换 TopView 页面，不承载页面内容。
    # QTabWidget 默认会为内部空页面分配可扩展高度，导致真正的画布被挤到右下角，
    # 看起来像网格只从下方一小块开始显示；这里固定为标签栏高度，让画布获得全部剩余空间。
    panel._tabs = QTabWidget(); panel._tabs.currentChanged.connect(panel._on_tab_changed)
    panel._tabs.setMaximumHeight(34)
    panel._tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    rv.addWidget(panel._tabs, 0)
    panel._canvas = TopViewCanvas(panel); rv.addWidget(panel._canvas, 1); splitter.addWidget(right); splitter.setStretchFactor(0, 0); splitter.setStretchFactor(1, 1); splitter.setSizes([260, 780]); root.addWidget(splitter, 1)
    # R183/F2：标尺/参考线/吸附分级/小地图/PNG 导出（全在 topview_overlays，本行零逻辑）
    from .topview_overlays import install_overlays; install_overlays(panel)






def _persist_topview_state(panel, project=None) -> bool:
    """把面板状态回写工程 —— **仅当面板状态正是从该工程灌入的**（见 `__init__`）。

    返回 True = 已写。判据不成立时（面板还停在别的工程 / 空 Sheet1）直接跳过：
    这是"保存后再加载 topview 信息丢失"（用户第三次实报）的结构性修复 ——
    历次只调调用顺序，任何重排都可能让回写先跑到面板被灌入之前，故改为
    与顺序无关的判据。
    """
    proj = project if project is not None else panel._current_project()
    if proj is None or not hasattr(proj, "topview_state"):
        return False
    if getattr(panel, "_hydrated_for", None) is not proj:
        return False
    try:
        proj.topview_state = panel.save_state()
        return True
    except Exception:
        return False


#: 配套页类别段：`设备包` ↔ `泵`/`散台`（与 `geometry_splines.COMPANION_CATEGORY_SEGMENTS`
#: 同源；R128 细化会把类别段夹在页名中间，故三处判定都必须用"含段"而非 `endswith`）
_COMPANION_PAIRS = {"设备包": ("泵", "散台"), "泵": ("设备包",), "散台": ("设备包",)}


# ══════════════════════════════════════════════════════════════════════════
#  R137 手势撤销快照（模块级：TopViewPanel 上帝类方法数冻结）
#
#  缺陷（实测撤销栈计数不变）：设备拖动 / gizmo 平移旋转缩放 / 下拉菜单里的
#  几何入口（整页缩放、沿图形排布、花样矩阵、按导入排布、移除设备、清除合并）
#  都**直写坐标/页数据而不打快照** → Ctrl+Z 还原不了。
#
#  手法：手势类在**起点**打一次快照（mousePress 命中可拖动对象时，见
#  `_undo_arm_on_press`），release 时没拖动过就弹掉刚压的栈顶
#  （避免"点一下没动也产生撤销点"）；菜单类入口在函数首行直接 `_snapshot_for_undo`
#  （与已修好的 add_page 同款）。连续调用（拖动会话内每帧）由会话标记去重。
# ══════════════════════════════════════════════════════════════════════════

def _undo_stack_top(panel):
    """撤销栈顶条目对象（无 PM/空栈/异常 → None）。"""
    try:
        pm = getattr(panel, "_pm", None)
        stack = getattr(pm, "_undo_stack", None)
        return stack[-1] if stack else None
    except Exception:
        return None


def _undo_drop_entry(panel, entry) -> None:
    """弹掉本次手势刚压的那**条**撤销条目（按对象身份，不是按位置）。

    身份比对最稳：栈满时 `_snapshot` 会 pop(0) 挤掉最老的条目，长度不再是
    「+1」，按长度判定就会漏弹；`is` 比对无论栈怎么变都只弹自己压的那条。
    顺带清掉 redo 栈（与 `_snapshot` 写入路径一致：新快照作废重做链），否则
    撤销点报的是「未被观察到的条目」，Ctrl+Z 看起来没反应。
    """
    if entry is None:
        return
    try:
        pm = getattr(panel, "_pm", None)
        stack = getattr(pm, "_undo_stack", None)
        if stack and stack[-1] is entry:
            stack.pop()
            redo = getattr(pm, "_redo_stack", None)
            if redo is not None:
                redo.clear()
    except Exception:
        pass


def _undo_settle_release(panel, moved: bool) -> None:
    """手势释放结算：拖过 → 保留快照（Ctrl+Z 还原这一步）；没拖 → 弹掉刚压的那条。

    按**对象身份**弹（见 `_undo_drop_entry`），不会误弹按下→松开之间别处压的条目。
    """
    state = getattr(panel, "_undo_gesture", None)
    if state is None:
        return
    panel._undo_gesture = None
    if not moved:
        _undo_drop_entry(panel, state[1])


def _undo_snapshot_xform(panel, defer_ui) -> None:
    """平移/旋转/缩放的**直调**入口快照。

    `defer_ui=True` 只出现在 gizmo 拖动会话内部帧（起点已由 `_undo_arm_on_press`
    压过），会话进行中也不重复压；其余调用（双击旋转环、按钮/外部直调）在这里
    补快照——原先一次不压，Ctrl+Z 还原不了。
    """
    if defer_ui or getattr(panel, "_undo_gesture", None) is not None:
        return
    try:
        panel._snapshot_for_undo()
    except Exception:
        pass


def _undo_arm_on_press(panel, canvas) -> None:
    """R137：鼠标按下的**写入模式**判定 —— 命中设备/包/gizmo/图形/手柄才压快照。

    在画布 mousePressEvent 处理完（拖动状态已就绪）之后由 canvas.event() 调用；
    这次快照捕捉的是「拖动开始前」的几何（拖动期间 defer_ui 帧不再重复压）。
    没拖动就松开时由 `_undo_settle_release` 把刚压的栈顶弹掉 —— 点一下不产生撤销点。

    ★ 性能：按下是**每次点击**都会发生的高频动作，故优先走 `_snapshot_scoped`
    （交互级轻快照，剥离 clip frames 的大负重；与 main_window 的拖拽手势起点同款），
    否则 10 万帧工程上每点一下设备都要序列化全部帧（R57 实测 ~1.1s）。
    """
    dragging = bool(getattr(canvas, "_gizmo_mode", "")
                    or getattr(canvas, "_drag_fixture_ids", None)
                    or getattr(canvas, "_drag_logical_id", "")
                    or getattr(canvas, "_drag_shape_id", "")
                    or getattr(canvas, "_drag_handle", None))
    if not dragging:
        return
    pm = getattr(panel, "_pm", None)
    try:
        from ..core.project_manager import DOMAIN_TOPVIEW
        project = panel._current_project()
        if (hasattr(project, "topview_state")
                and not _persist_topview_state(panel, project)):
            return   # 回写判据不成立（面板状态非本工程灌入）→ 快照不含改动，跳过
        if hasattr(pm, "_snapshot_scoped"):
            pm._snapshot_scoped(domain=DOMAIN_TOPVIEW)
        else:
            panel._snapshot_for_undo()
    except Exception:
        try:
            panel._snapshot_for_undo()
        except Exception:
            pass
    panel._undo_gesture = ("_press", _undo_stack_top(panel))


#: 菜单/非手势入口的「一票到底」快照去重窗口（秒）：同一次拖放事件里
#: canvas.dropEvent 与 add_fixture_group_to_canvas 都会调，但只该压一条。
_UNDO_ACTION_WINDOW_SEC = 0.15


def _undo_snapshot_action(panel, token: str) -> None:
    """菜单/非手势入口的一次性快照（同 token 在极短窗口内只压一条）。"""
    import time as _time
    now = _time.monotonic()
    last = getattr(panel, "_undo_action_last", None)
    if (isinstance(last, tuple) and len(last) == 2 and last[0] == token
            and (now - float(last[1])) < _UNDO_ACTION_WINDOW_SEC):
        return
    panel._undo_action_last = (token, now)
    try:
        panel._snapshot_for_undo()
    except Exception:
        pass


def _add_group_to_canvas_with_undo(panel, fixture_ids, pos, shape=None, group_id=""):
    """R137：拖放落点入口 —— **先**压撤销快照，再走 add_fixture_group_to_canvas。

    与 `add_fixture_group_to_canvas` 内部那次同 token、短窗口去重 → 一次拖放
    只产生一条撤销条目，且压在任何写入之前（快照 = 拖入前状态）。
    （模块级而非新增方法：TopViewPanel 方法数冻结。）
    """
    _undo_snapshot_action(panel, "add_group_to_canvas")
    return panel.add_fixture_group_to_canvas(fixture_ids, pos, shape, group_id)


def _companion_match(name: str):
    """页名 → `(同类前缀, 要找的类别元组)`；非配套页返回 `("", ())`。

    `实用·设备包·气爆包` → `("实用", ("泵", "散台"))`
    `实用·泵`            → `("实用", ("设备包",))`
    """
    nm = str(name or "")
    for cat, want in _COMPANION_PAIRS.items():
        idx = nm.find(f"·{cat}")
        if idx > 0:
            return nm[:idx], want
    return "", ()


def _page_visible_ids(panel) -> set:
    """当前页可见设备 id 集合（异常时返回空集 —— 调用方自己 `or mother` 兜底）。"""
    try:
        return set(panel._page_device_order())
    except Exception:
        return set()


def _append_missing(ordered: list, extra: list) -> list:
    """把 `extra` 里不在 `ordered` 的项按序追加（保序去重）。"""
    seen = set(ordered)
    out = list(ordered)
    for fid in extra:
        if fid not in seen:
            out.append(fid)
            seen.add(fid)
    return out


def _base_order_with_mother(panel, ids: list) -> list:
    """R165：保存设备组时写入的 `base_order_fixture_ids` —— **优先母集合**。

    当前选择若是母集合里的子集（奇数/偶数/选N隔M 工具得到的），旧写法
    `base_order = list(ids)` 会把母集合丢成子集，于是"母集合 N 台里奇偶错相"
    这层语义存不下来（用户要的"可追溯"缺口）。
    R121 的约束（保存不得清掉当前选择）不受影响：只改写入的序列，不动选中态。
    """
    ids = [str(x) for x in (ids or []) if str(x)]
    mother = [str(x) for x in (getattr(panel, "_live_base_ids", None) or []) if str(x)]
    if not (mother and set(ids) <= set(mother)):
        return list(ids)
    visible = _page_visible_ids(panel)
    ordered = [fid for fid in mother if fid in visible] or mother
    return _append_missing(ordered, ids)


def _restore_live_selection(panel, state) -> None:
    """R165：从存档恢复**母集合/子集/派生名称**（可追溯）。

    旧工程没有这三个键 → 保持 `__init__` 默认，行为与改前一致（不会凭空造出
    一个母集合）。此前只存子集不存母集合，导致工程重载 / 撤销 / 切页签后
    `_topview_base_order_ids` 静默退化成"母集合 = 子集"。
    """
    if not isinstance(state, dict):
        return
    for key, attr in (("live_base_ids", "_live_base_ids"),
                      ("live_selection_ids", "_live_selection_ids")):
        vals = [str(x) for x in (state.get(key) or []) if str(x)]
        setattr(panel, attr, vals)
    panel._live_selection_name = str(state.get("live_selection_name") or "TopView实时选择")


def _box_chain_base(panel, current):
    """R195：框选链序作过滤基（页面导入序不再丢弃链序）；链缺失回退页面序。"""
    sel_set = set(current)
    chain = [fid for fid in (getattr(panel, "_box_chain_order_ids", None) or [])
             if fid in sel_set]
    if chain and len(chain) == len(sel_set):
        units = panel._selection_units_from_base(chain, sort_by_pos=False)
        # Curves keep the canvas direction; grids retain their established x/y filter contract.
        return chain, not _lattice_aligned([unit["pos"] for unit in units])
    out = [fid for fid in panel._page_device_order() if fid in sel_set]
    return (out, False) if out else (current, True)


def _sort_units_visual(units: list) -> None:
    """R195：单元视觉排序（原地）——链序优先 + 网格护栏。

    ①格点预检：多数单元按列/行对齐（网格/多排）→ 直接 (x,y)（08-04 语义
    不变；小网格贪心链会蛇形误判，必须先拦）。②最近邻链序：弧/圆/椭圆环/
    折线的自然遍历序（与形状无关，编号沿曲线连续）。③链无效 → (x,y)。
    用户实报：水仙十字 JET 椭圆环弧段（无 CAD 圆形状）旧 (x,y) 序在弧两腿
    间锯齿跳，奇偶选择全乱。
    """
    import math as _m
    if len(units) < 5:
        units.sort(key=lambda u: (u["pos"][0], u["pos"][1]))
        return
    if _lattice_aligned([u["pos"] for u in units]):   # R195：公共格点预检
        units.sort(key=lambda u: (u["pos"][0], u["pos"][1]))
        return
    # 堆叠单元（同坐标）共享链位：按唯一坐标建链，稳定排序保相对序
    uniq_pos = list(dict.fromkeys(u["pos"] for u in units))
    order = _nearest_neighbor_chain_order(uniq_pos)
    if order is not None:
        units.sort(key=lambda u, _o=order: _o.get(u["pos"], 0))
    else:
        units.sort(key=lambda u: (u["pos"][0], u["pos"][1]))


def _lattice_aligned(points: list) -> bool:
    """R196: mirrored curve points are not a two-dimensional Cartesian grid."""
    from .topview_selection_order import rectangular_lattice
    return rectangular_lattice(points)


def _chain_ends_disagree_drag(ordered_items, dx: float, dy: float) -> bool:
    """R195：链序端点是否与拖动方向相反（左→右拖应左端起编；纵向同理）。"""
    if not ordered_items:
        return False
    fx, fy = ordered_items[0]["x"], ordered_items[0]["y"]
    lx, ly = ordered_items[-1]["x"], ordered_items[-1]["y"]
    if abs(dx) >= abs(dy):
        return (dx < 0.0) != (fx > lx)
    return (dy < 0.0) != (fy > ly)


def _merge_box_chain(chain: list, ordered_box: list, chain_set: set) -> None:
    """R201/P10（真实应用探针实测）：chain_complete 分支的收链规则。

    旧实现无条件 extend——同一范围重复框选一次链就成倍复制（240→480→720
    重复条目），徽标/效果序全乱。新语义：重扫未带来新设备 = 显式重置
    （08-04 定案"重新框选是显式重置"），以本次拖动链序为准；带来新设备 =
    只追加新 id。
    """
    fresh = [fid for fid in ordered_box if fid not in chain_set]
    if not fresh:
        chain[:] = list(ordered_box)
    else:
        chain.extend(fresh)


def _drag_axis_sort(units: list, dx: float, dy: float) -> None:
    """08-04/08-11 定案：无有效链/环时的拖动方向扫描（竖/横切线）兜底序。"""
    if abs(dx) >= abs(dy):
        x_reverse = dx < 0.0
        units.sort(key=lambda it: (-it["x"] if x_reverse else it["x"]))
    else:
        y_reverse = dy < 0.0
        units.sort(key=lambda it: (-it["y"] if y_reverse else it["y"]))


def _cycle_chain_valid(d2, order_idx, n) -> bool:
    """闭环判据：贪心走链按**环**处理，每点的两个最近邻恰为环上两邻。

    开链判据（`_chain_valid`）对闭环必然失败——环的链首尾在环上相邻、却不
    是链内相邻对 → 整圆框选永远判链失败回退拖动排序（用户 2026-10-06 实报
    "整圆被与Y轴平行的平行线横切"）。本判据把首尾视为相邻，专认闭环。
    """
    cycle = {(order_idx[k], order_idx[(k + 1) % n]) for k in range(n)}
    cycle |= {(b, a) for a, b in cycle}
    for i in range(n):
        ranked = sorted((d2[i][j], j) for j in range(n) if j != i)[:2]
        if any((i, j) not in cycle for _d, j in ranked):
            return False
    return True


def _ring_polar_order(points: list, dx: float, dy: float):
    """闭环的**极坐标扫描**序（用户定案："圆弧设备排序应该按极坐标一样扫"）。

    贪心走链 + 闭环判据通过后，绕点集质心按极角排序（CCW），再把起点旋转到
    拖动方向的极值设备（左→右拖 = 最左点起编）。开弧/网格/散点返回 None，
    由调用方回退链序/网格判据。
    """
    import math as _m
    n = len(points)
    if n < 5 or len(set(points)) < n:
        return None
    d2 = [[(a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 for b in points] for a in points]
    for i in range(n):
        d2[i][i] = float("inf")
    nn_dist = [min(d2[i]) for i in range(n)]
    walk = _greedy_nn_walk(d2, n, nn_dist.index(max(nn_dist)))
    if walk is None or not _cycle_chain_valid(d2, walk, n):
        return None
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    from .topview_selection_order import _scan_key
    ring = sorted(((_m.atan2(p[1] - cy, p[0] - cx), p) for p in points),
                  key=lambda item: item[0])
    start = min(range(n), key=lambda k: _scan_key({"x": ring[k][1][0], "y": ring[k][1][1]}, dx, dy))
    ring = ring[start:] + ring[:start]
    return {p: k for k, (_a, p) in enumerate(ring)}


def _union_adjacent(parent: list, pts: list, limit: float) -> None:
    """把步距 ≤ limit 的点对并查集合并（kNN 连通分量）。"""
    import math as _m
    n = len(pts)
    for i in range(n):
        for j in range(i + 1, n):
            if _m.dist(pts[i], pts[j]) <= limit:
                ri, rj = _find_root(parent, i), _find_root(parent, j)
                if ri != rj:
                    parent[ri] = rj


def _find_root(parent: list, a: int) -> int:
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a


def _unit_clusters(units: list) -> list:
    """按 kNN 连通分量把单元分成空间紧邻簇（阈值 = 2×中位最近邻步距）。"""
    import math as _m
    n = len(units)
    if n < 2:
        return [list(units)]
    pts = [(it["x"], it["y"]) for it in units]
    nn = [min(_m.dist(p, q) for j, q in enumerate(pts) if j != i) for i, p in enumerate(pts)]
    limit = max(2.0 * sorted(nn)[n // 2], 1e-6)
    parent = list(range(n))
    _union_adjacent(parent, pts, limit)
    groups = {}
    for i, it in enumerate(units):
        groups.setdefault(_find_root(parent, i), []).append(it)
    return list(groups.values())


def _order_curve_units(units: list, dx: float, dy: float) -> None:
    """单簇最优序：闭环 → 极角扫描；开曲线 → 链序（拖向对齐）；否则拖动方向。"""
    pts = [(it["x"], it["y"]) for it in units]
    polar = _ring_polar_order(pts, dx, dy)
    if polar is not None:
        units.sort(key=lambda it: polar[(it["x"], it["y"])])
        return
    chain = None if _lattice_aligned(pts) else _nearest_neighbor_chain_order(pts)
    if chain is not None:
        units.sort(key=lambda it: chain[(it["x"], it["y"])])
        if _chain_ends_disagree_drag(units, dx, dy):
            units.reverse()
        return
    _drag_axis_sort(units, dx, dy)


def _order_unassigned_curve_aware(unassigned: list, dx: float, dy: float,
                                  page_rank=None) -> None:
    """★ R195 共性修复（原地排序）：无 CAD 形状的框选回退序改**曲线链序**。

    此前按拖动方向 (x,y) 排序，非单调弧两腿间锯齿跳——TopView 奇偶/间隔与
    Programmer 范围条带/编号全部消费这个序，用户实报两处同乱。网格/散点
    （_lattice_aligned 或链验证不过）保留原拖动方向语义（08-04/08-11 定案）；
    链序端点按拖动方向对齐（左→右拖 = 左端起编）。（模块级：函数基线）
    R201/P10（2026-10-06 用户实报）：多圆/整圆布局按空间紧邻分簇，簇内
      闭环走极角扫描、开曲线走链序；簇与簇之间按「设备顺序」（页面设备序
      page_rank）衔接——"一个圆播放完按设备顺序走到下一个圆"。
    """
    if _lattice_aligned([(it["x"], it["y"]) for it in unassigned]):
        _drag_axis_sort(unassigned, dx, dy)
        return
    clusters = _unit_clusters(unassigned)
    if len(clusters) <= 1:
        _order_curve_units(unassigned, dx, dy)
        return
    for cluster in clusters:
        _order_curve_units(cluster, dx, dy)
    if page_rank:
        clusters.sort(key=lambda c: min(page_rank.get(str(it["fid"]), len(page_rank))
                                        for it in c))
    else:
        from .topview_selection_order import _scan_key
        clusters.sort(key=lambda c: min(_scan_key(it, dx, dy) for it in c))
    unassigned[:] = [it for cluster in clusters for it in cluster]


def _greedy_nn_walk(d2, n, start):
    """★ R195 修订：**双向**贪心——每步从链头/链尾两端中选更近的未用点扩展。
    单向贪心在起点分叉处（两邻等距，如椭圆弧平顶）走错一侧，链尾被迫跳
    大步、2NN 验证必挂；双向让分叉两侧对称生长，链自然正确。"""
    head = tail = start
    used = {start}
    order_idx = [start]
    for _ in range(n - 1):
        best_side, best_j, best_d = 0, -1, float("inf")
        for j in range(n):
            if j in used:
                continue
            if d2[head][j] < best_d:
                best_side, best_j, best_d = 0, j, d2[head][j]
            if d2[tail][j] < best_d:
                best_side, best_j, best_d = 1, j, d2[tail][j]
        if best_j < 0:
            return None
        used.add(best_j)
        if best_side == 0:
            order_idx.insert(0, best_j)
            head = best_j
        else:
            order_idx.append(best_j)
            tail = best_j
    return order_idx


def _chain_valid(d2, order_idx, n, step_med):
    """一维曲线嵌入判据：内部点的**两个最近邻**恰为链前驱+后继，端点的最近
    邻为其唯一链邻。（网格已由 _lattice_aligned 预检拦截，不依赖此判据；
    椭圆弧平顶处步距/非链距近等距不再误杀。）"""
    chain_pairs = {(order_idx[k], order_idx[k + 1]) for k in range(n - 1)}
    chain_pairs |= {(b, a) for a, b in chain_pairs}
    for pos_i, i in enumerate(order_idx):
        need = 2 if 0 < pos_i < n - 1 else 1
        ranked = sorted((d2[i][j], j) for j in range(n) if j != i)[:need]
        if len(ranked) < need:
            return False
        if any((i, j) not in chain_pairs for _d, j in ranked):
            return False
    return True


def _nearest_neighbor_chain_order(points: list):
    """R195：点集的**曲线链序**（弧/环/折线的自然遍历序），非链返回 None。

    贪心最近邻走链 + 链性验证（各点到最近非链邻 ≥ 1.3× 局部链步距才认定为
    一维曲线；网格/散点不满足 → None → 调用方回退 (x,y)）。与形状无关：圆弧/
    椭圆弧/样条弧/直线链全部得到沿曲线的连续序。（用户实报：JET 椭圆环弧段
    无 CAD 圆形状，旧 (x,y) 序在弧两腿间锯齿跳，奇偶选择全乱。）
    """
    n = len(points)
    if n < 5 or len(set(points)) < n:
        return None
    d2 = [[(a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 for b in points] for a in points]
    for i in range(n):
        d2[i][i] = float("inf")
    import math as _m
    nn_dist = [min(d2[i]) for i in range(n)]
    order_idx = _greedy_nn_walk(d2, n, nn_dist.index(max(nn_dist)))
    if order_idx is None:
        return None
    import math as _m2
    steps = sorted(_m2.sqrt(d2[order_idx[k]][order_idx[k + 1]]) for k in range(n - 1))
    step_med = steps[len(steps) // 2]
    if step_med <= 1e-9 or not _chain_valid(d2, order_idx, n, step_med):
        return None
    return {points[i]: k for k, i in enumerate(order_idx)}


def _order_members_by_base(member: List[str], base_order) -> List[str]:
    """Order only the group's actual members; mother order cannot add members."""
    from src.core.fixture_selection import ordered_members
    return ordered_members(member, base_order)


class TopViewPanel(QWidget):
    layout_changed = pyqtSignal()
    selection_changed = pyqtSignal(list)
    fixture_group_saved = pyqtSignal(str, list, str, list)
    xy_reference_changed = pyqtSignal(dict)

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self._pm = project if hasattr(project, "project") or hasattr(project, "_project") else None
        self._project = (getattr(project, "project", None) or getattr(project, "_project", None) or project)
        self.current_tool = "select"; self.selected_ids: set[str] = set(); self.last_clicked_id = ""
        self._selection_order_ids: List[str] = []
        self._box_chain_order_ids: List[str] = []
        self._box_chain_active: bool = False
        self._live_base_ids: List[str] = []
        self._live_selection_ids: List[str] = []
        self._live_selection_name = "TopView实时选择"
        # ★ 配套编号继承表（2026-08-05 用户需求）：追加的配套设备 fid → 其源设备
        #   的徽标号。画布徽标按继承号显示；清空选择时作废。
        self._companion_inherit_marks: Dict[str, List[int]] = {}
        # ★ 2026-09-19（用户第三次实报"保存后再加载 topview 信息丢失"）：
        #   `_hydrated_for` = **面板内部状态是从哪个工程对象灌入的**。
        #   回写工程前必须 `_hydrated_for is 当前工程` —— 否则就是把别的工程
        #   （或空 Sheet1）的布局当成最新状态写进去。加载工程时
        #   `_sync_xy_reference` 正是这么把刚读进来的存档当场覆盖的（页/选中/
        #   XY 同步基准/Z 角全丢）。历次修法只调调用顺序，任何重排都可能再复发，
        #   故改为这个**不依赖顺序**的判据（见 `_persist_state`）。
        self._hydrated_for = None
        self._pages: List[dict] = [] ; self._active_page = 0
        self._xy_reference_by_fixture: Dict[str, dict] = {}
        self._xy_reference_global_angle_deg: float = 0.0
        # ★ Z 轴扇面基准角（2026-08-04 用户）：三维设备（MDD/副池三维扇子）Z 扇面的
        #   同步目标方向，默认 0° = 水平 +X；每台电机角 = 扇面角 − 安装基准角（切线），
        #   写入 motor_z（引擎 xy_reference.Z 输出），全组扇面水平平行。
        self._xy_reference_z_angle_deg: float = 0.0
        # TopView 默认画板为空；左侧内置设备组展板与 Fixture 设备组同步。
        # 用户从展板拖入画板后，设备才加入当前页的 visible_fixture_ids 并显示。
        self._active_group_id = ""
        # 自动编组（多圆拓扑推荐）会话状态：引擎/候选列表/当前下标/随机种子。
        self._autogroup_engine = None
        self._autogroup_candidates: List[object] = []
        self._autogroup_index = 0
        self._autogroup_seed = 0
        # ★ R38-D9：布局版本号 + 空间分组缓存。
        #   spatial_groups_for_fixture_ids 是 O(设备×图形) 的几何归属循环，且每组
        #   还要走 _fixture_number_order_ids（内部 _all_fixture_ids() 重建 11382
        #   元素集合 + 正则排序）。effect 播放时它是"每帧 × 每效果 × 每通道"被调
        #   的，零缓存 → 这就是"打开 Programmer 后全局 PPT"的成本主体。
        #   失效口径：只要布局/页/设备表变了就必须失效，否则会按旧图形分组输出
        #   到错误的设备（P0，比卡顿严重）。layout_changed 有近 30 处 emit，逐处
        #   加 bump 必漏，所以直接把信号连到 bump。
        self._layout_version: int = 0
        self._spatial_group_cache: dict = {}
        # ★ R137 撤销：手势会话标记 (token, 压栈前栈长)。手势起点只压一条快照，
        #   未拖动时 release 侧弹栈回退（见模块级 _undo_snapshot_gesture_start）。
        self._undo_gesture = None
        self._undo_action_last = None
        self.setStyleSheet(STYLE); self._build_ui(); self.load_state(getattr(self._project, "topview_state", {}) or {}); self.refresh()
        try:
            self.layout_changed.connect(self._bump_layout_version)
        except Exception:
            logger.debug("topview: 连接 layout_changed 版本号时失败")
        try: self._project.project_changed.connect(self.refresh)
        except Exception:
            logger.debug("连接项目变更信号时失败")
        try:
            self._project.project_changed.connect(self._bump_layout_version)
        except Exception:
            logger.debug("topview: 连接 project_changed 版本号时失败")

    def _bump_layout_version(self) -> None:
        """★ R38-D9：布局/页/设备表变化 → 作废空间分组缓存。

        宁可多失效一次（重算一遍几何），也绝不能少失效一次（按旧图形分组
        输出到错误设备）。
        """
        self._layout_version = int(getattr(self, "_layout_version", 0)) + 1
        cache = getattr(self, "_spatial_group_cache", None)
        if cache:
            cache.clear()

    def _build_ui(self):
        return build_topview_ui(self)

    def _current_project(self):
        return (getattr(self._pm, "project", None) or getattr(self._pm, "_project", None) or self._project) if self._pm is not None else self._project
    def fixtures_by_id(self) -> Dict[str, object]:
        # 缓存:每次 paintEvent 都会调,几百设备重建 dict 是纯浪费。键=(列表对象身份,长度)。
        # 增删设备会改身份/长度触发重建;改属性因存的是对象引用,仍读到最新值。
        flist = getattr(self._current_project(), "fixtures", []) or []
        key = (id(flist), len(flist))
        if (getattr(self, "_fix_by_id_key", None) == key
                and getattr(self, "_fix_by_id_list", None) is flist
                and getattr(self, "_fix_by_id_cache", None) is not None):
            return self._fix_by_id_cache
        d = {str(getattr(f, "id", "")): f for f in flist}
        self._fix_by_id_key = key
        self._fix_by_id_list = flist
        self._fix_by_id_cache = d
        return d
    def fixture_defs_by_id(self) -> Dict[str, object]:
        dlist = getattr(self._current_project(), "fixture_defs", []) or []
        key = (id(dlist), len(dlist))
        if (getattr(self, "_fixdef_by_id_key", None) == key
                and getattr(self, "_fixdef_by_id_list", None) is dlist
                and getattr(self, "_fixdef_by_id_cache", None) is not None):
            return self._fixdef_by_id_cache
        d = {str(getattr(fd, "id", "")): fd for fd in dlist}
        self._fixdef_by_id_key = key
        self._fixdef_by_id_list = dlist
        self._fixdef_by_id_cache = d
        return d
    def _fixture_is_xy(self, fid: str) -> bool:
        """判定设备 def 是否含 motor_x+motor_y 两轴。

        结果只取决于 def 通道表，按 def_id 缓存并以 channels 列表 (身份,长度) 校验
        失效——组选中热路径逐台 list(channels)+线性 in 扫描 → 每 def 仅算一次，其余
        为 dict 命中（2107 台组选中残余热点消除）。设备 def 表变化即自动失效。
        """
        fi = self.fixtures_by_id().get(str(fid))
        if fi is None:
            return False
        def_id = str(getattr(fi, "def_id", ""))
        fd = self.fixture_defs_by_id().get(def_id)
        channels = (getattr(fd, "channels", []) or []) if fd is not None else []
        cache = getattr(self, "_fixture_xy_cache", None)
        if cache is None:
            cache = {}
            self._fixture_xy_cache = cache
        hit = cache.get(def_id)
        if hit is not None and hit[0] is channels:
            return hit[1]
        result = "motor_x" in channels and "motor_y" in channels
        cache[def_id] = (channels, result)
        return result
    def _fixture_is_2d_xy(self, fid: str) -> bool:
        """二维数控判别（2026-08-09 用户实测）：def 含 motor_x+motor_y 且**无**
        motor_z——平面两轴数控设备（改版至冬1 = 二维数控页 240 台 2-Axis Jet）。

        这类设备同步目标 = 统一世界方向（本地 X 沿世界横坐标、Y 沿世界纵坐标）。
        三维设备（Nozzle Fan 等含 motor_z, 主池/副池扇子）返回 False → 逐台
        切线现状保持。调气爆等无电机 def 不含 motor_x/y → False。
        """
        fi = self.fixtures_by_id().get(str(fid))
        if fi is None:
            return False
        fd = self.fixture_defs_by_id().get(str(getattr(fi, "def_id", "")))
        channels = list(getattr(fd, "channels", []) or []) if fd is not None else []
        return "motor_x" in channels and "motor_y" in channels and "motor_z" not in channels

    def _xy_install_angle(self, fid, base_angle, context_ids=None):
        """Bind synchronization to the complete physical installation once.

        3D fan Z uses world = installation + motor; its base is installation.
        Planar XY uses the existing mirrored compensation convention. A zero
        RotY with nonzero RotX/RotZ is valid data, including a 180-degree mount.
        Legacy unlocated fixtures retain geometric synchronization.
        """
        fixture = self.fixtures_by_id().get(str(fid))
        by_id = self.fixtures_by_id()
        heading = synchronized_installation_heading(
            fixture, lambda: (by_id[key] for key in
                (self._page_xy_sync_target_ids() if context_ids is None else context_ids)
                if key in by_id))
        if heading is None:
            return _canonicalize_base_to_install(self, fid, float(base_angle))
        if self._fixture_is_2d_xy(str(fid)):
            return _normalize_angle_deg(-heading)
        return _normalize_angle_deg(heading)

    def _all_fixture_ids(self) -> List[str]:
        return list(self.fixtures_by_id().keys())

    def _pages_visible_cache_key(self) -> tuple:
        """页可见结构键：内容化键（页 id + 可见设备 id + 逻辑设备成员 id）。

        不用 id() 内存地址，避免对象 GC 后地址复用导致错误命中。只在选择/切页等
        低频路径调用，内容化成本可接受。"""
        parts = []
        for page in self._pages:
            if not isinstance(page, dict):
                parts.append(("", (), ()))
                continue
            vis = page.get("visible_fixture_ids") or []
            lds = page.get("logical_devices") or []
            ld_parts = []
            for ld in lds:
                if not isinstance(ld, dict):
                    continue
                members = ld.get("member_fixture_ids") or []
                ld_parts.append((str(ld.get("id", "") or ""), tuple(str(x) for x in members)))
            parts.append((
                str(page.get("id", "") or ""),
                tuple(str(x) for x in vis),
                tuple(ld_parts),
            ))
        return tuple(parts)

    def _all_pages_visible_ids(self) -> set:
        """所有 Sheet 上已放置(可见)设备的并集——跨 sheet 连选的全局可见范围。

        用于在切页/刷新时保留跨页选择：只剔除真正不存在(已删)的设备，不误删其它页选择。

        ★ 性能(组选中热路径)：结果只取决于设备集 + 各页可见/逻辑成员列表结构，按
        结构键缓存，页/设备集变化即失效（_ordered_selected_ids 等每选一次都调用）。
        """
        flist = getattr(self._current_project(), "fixtures", []) or []
        key = (id(flist), len(flist), self._pages_visible_cache_key())
        cached = getattr(self, "_all_pages_visible_cache", None)
        if (cached is not None
                and getattr(self, "_all_pages_visible_cache_key", None) == key
                and getattr(self, "_all_pages_visible_flist", None) is flist):
            return cached
        all_ids = set(self._all_fixture_ids())
        out = set()
        for page in self._pages:
            for fid in (page.get("visible_fixture_ids", []) or []):
                sfid = str(fid)
                if sfid in all_ids:
                    out.add(sfid)
        # 逻辑设备成员也算「全局可见范围」，否则 refresh/load_state 的剪枝会丢掉
        # 点击代表点选中的成员（成员不在任何页的 visible_fixture_ids 中）。
        out |= self._selectable_member_ids(all_pages=True)
        self._all_pages_visible_cache = out
        self._all_pages_visible_cache_key = key
        self._all_pages_visible_flist = flist
        return out

    def _group_fixture_ids(self, gid: str) -> List[str]:
        if not gid:
            return []
        for g in (getattr(self._current_project(), "fixture_groups", []) or []):
            if str(g.get("id", "")) == str(gid):
                all_ids = self._all_fixture_ids()
                member = [str(x) for x in (g.get("fixture_ids", g.get("fixtures", [])) or [])
                          if str(x) in all_ids]
                # ★ R195：base_order（R165 母集合序）只做顺序追溯，不得拼进成员
                return _order_members_by_base(member, g.get("base_order_fixture_ids") or [])
        return []

    def _visible_fixture_ids(self) -> List[str]:
        """当前页可见设备 id（**只读**，绝不回写页面数据）。

        ★★★ R169 修复（用户实报"有的设备显示缺了，比如弧线二维 42 个第 19 号不见了"）：
          旧实现是**有副作用的 getter** —— 末尾 `page["visible_fixture_ids"] = out` 把
          "不在 `fixtures_by_id()` 里的 id"**永久剔除**。而 `fixtures_by_id()` 只读
          `_current_project().fixtures`：只要**任何一次**调用发生在"工程尚未挂上 / 正在
          换工程"的窗口里，页面数据就被静默重写。

          实测（`tools/_probe_r169_visible.py`，内存内复现）：
            页面 42 台 → 工程未挂载的瞬间调用 `fixture_ids()` → **页面被回写成 0 台**
            → 工程恢复后 42 台**全部永久丢失**，第 19 号再也回不来。

          修法：本函数**只过滤返回值，不改数据**。真正的修剪（删除设备后同步可见集）
          应由明确的"配接/工程变更"路径负责 —— 而不是每次绘制顺手改工程。
        """
        page = self.current_page()
        raw = page.get("visible_fixture_ids") or []
        all_ids = set(self._all_fixture_ids())
        out: List[str] = []
        seen: set = set()
        for fid in raw:
            sfid = str(fid)
            if sfid in all_ids and sfid not in seen:
                out.append(sfid); seen.add(sfid)
        return out
    def fixture_ids(self) -> List[str]:
        """当前 TopView 画板中已经被拖入/放置的可见设备。默认为空。"""
        return self._visible_fixture_ids()
    def current_page(self) -> dict:
        if not self._pages: self._pages = [{"id":"page-1","name":"Sheet1","fixtures":{},"cad_shapes":[],"pattern_matrices":[]}]
        self._active_page = max(0, min(self._active_page, len(self._pages)-1)); return self._pages[self._active_page]
    def current_page_logical_devices(self) -> List[dict]:
        """本页逻辑/组合设备 dict 列表（剔除成员已全部删除的）。"""
        page = self.current_page()
        lds = page.get("logical_devices", []) or []
        valid_ids = set(self.fixtures_by_id().keys())
        out = []
        for ld in lds:
            members = [str(m) for m in (ld.get("member_fixture_ids", []) or []) if str(m) in valid_ids]
            if members:
                out.append(ld)
        return out
    def _default_fixture_pos(self, fid: str) -> Tuple[float, float]:
        ids = self.fixture_ids()
        idx = ids.index(fid) if fid in ids else 0
        count = max(1, len(ids))
        fi = self.fixtures_by_id().get(fid)
        raw_x = getattr(fi, "pos_x", None)
        raw_y = getattr(fi, "pos_y", None)
        try:
            raw_xf = float(raw_x)
            raw_yf = float(raw_y)
            has_meaningful_model_pos = abs(raw_xf) > 1.0 or abs(raw_yf) > 1.0
        except Exception:
            raw_xf = raw_yf = 0.0
            has_meaningful_model_pos = False
        # 兼容已有模型坐标：只有明确不是 0,0 的旧坐标才沿用；默认 0,0 视为“未定位”。
        if has_meaningful_model_pos:
            return raw_xf, raw_yf
        canvas_w = max(520, self._canvas.width() if hasattr(self, "_canvas") and self._canvas.width() > 80 else 900)
        canvas_h = max(360, self._canvas.height() if hasattr(self, "_canvas") and self._canvas.height() > 80 else 520)
        cx, cy = canvas_w * 0.5, canvas_h * 0.5
        spacing_x, spacing_y = 54.0, 42.0
        cols = max(1, min(10, int(math.ceil(math.sqrt(count)))))
        rows = int(math.ceil(count / cols))
        col, row = idx % cols, idx // cols
        x = cx + (col - (cols - 1) / 2.0) * spacing_x
        y = cy + (row - (rows - 1) / 2.0) * spacing_y
        margin = 36.0
        return max(margin, min(canvas_w - margin, x)), max(margin, min(canvas_h - margin, y))

    def fixture_pos(self, fid: str) -> Tuple[float, float]:
        """读某设备坐标（**只读，无副作用**）。

        ★★ R131 修复（跨页选择被静默截断 + 别页设备被注入幽灵坐标）：
          原先在设备不在本页坐标表时 `fpos[fid] = ...` —— **读操作就地写入当前页**。
          而 `rotate_selected_fixtures` 首行 `[self.fixture_pos(fid) for fid in ids]`
          对**跨页选中集**逐台调用 → 每台别页设备在当前页被写进一份"默认布局坐标"
          （`save_state` 一并落盘），重开工程时该设备位置漂移到别页数值；
          `get_all_fixture_positions` 也会返回这份幽灵坐标。
          （`set_fixture_pos` 有 `if fid not in visible: return` 守卫，而本函数没有。）
          修：缺失时**只算出默认值返回，不落盘**；真正要落盘的位置全部走
          `set_fixture_pos`（有可见守卫）。
        """
        page = self.current_page(); fpos = page.get("fixtures") or {}
        if fid not in fpos:
            return self._default_fixture_pos(fid)      # ★ 不写回（见 docstring）
        v = fpos[fid]; return _safe_float(v.get("x")), _safe_float(v.get("y"))
    def set_fixture_pos(self, fid: str, x: float, y: float, visible: Optional[set] = None):
        # TopView is multi-sheet; keep coordinates in current sheet only.
        # 防御：仅当该设备已在当前页可见时才写入，避免跨 sheet 拖动把别页设备
        # 坐标注入当前页(否则 get_all_fixture_positions 会用幽灵坐标覆盖其真实位置)。
        # ★ 性能：拖动热路径每 move 对每台调用——visible 集由会话缓存传入
        #   （默认 None 时行为与原实现完全一致：按需构建）。
        if visible is None:
            visible = set(self._visible_fixture_ids())
        if str(fid) not in visible:
            return
        self.current_page().setdefault("fixtures", {})[fid] = {"x": float(x), "y": float(y)}
        # ★ 性能：位置写入版本号——canvas 鼠标命中桶格索引据此失效
        self._pos_version = getattr(self, "_pos_version", 0) + 1

    def get_device_positions(self) -> Dict[str, Tuple[float, float]]:
        """当前页已放置设备的坐标表 {fid: (x, y)} — 跑动编排(Route Animator)等外部消费者用。

        跳过 xy≈0 的未定位设备（与 skill_export 的过滤约定一致）。
        """
        out: Dict[str, Tuple[float, float]] = {}
        for fid in self.fixture_ids():
            try:
                x, y = self.fixture_pos(str(fid))
            except Exception:
                continue
            if abs(x) < 1e-6 and abs(y) < 1e-6:
                continue
            out[str(fid)] = (float(x), float(y))
        return out

    def xy_reference_mode_for_fixture(self, fid: str) -> str:
        cfg = self._xy_reference_by_fixture.get(str(fid), {}) or {}
        return "sync" if str(cfg.get("mode", "default")).lower() == "sync" else "default"

    def xy_reference_angle_for_fixture(self, fid: str) -> float:
        cfg = self._xy_reference_by_fixture.get(str(fid), {}) or {}
        # ★ 同步十字线统一世界方向显示（2026-08-08 用户实测）：同步基准的展示
        #   语义 = 统一世界方向——所有 sync 设备十字线（含非 uniform 的扇子页
        #   146/20 台）画「推 X 应指的方向」= 偏移 + 全局角，不含安装基准 base
        #   （切线）。引擎输出保持各自正确行为（二维=世界方向、三维扇子=切线）
        #   ——只动显示，不碰引擎。二维页（uniform）与此同式 → 值不变。
        if self.xy_reference_mode_for_fixture(fid) == "sync":
            offset = _safe_float(cfg.get("angle_offset_deg", 0.0), 0.0)
            return _normalize_angle_deg(offset + self._xy_reference_global_angle_deg)
        # 默认（非 sync）设备：按旧格式/引擎口径返回原值（含 base）。
        offset = _safe_float(cfg.get("angle_offset_deg", 0.0), 0.0)
        if "physical_angle_deg" in cfg or "compensation_angle_deg" in cfg:
            base = _safe_float(cfg.get("physical_angle_deg",
                                       cfg.get("compensation_angle_deg", 0.0)), 0.0)
        elif "base_angle_deg" in cfg or "angle" in cfg:
            base = _safe_float(cfg.get("base_angle_deg", cfg.get("angle", 0.0)), 0.0)
        elif "angle_deg" in cfg:
            return _safe_float(cfg.get("angle_deg", 0.0), 0.0)
        else:
            base = 0.0
        # ★ 关同步显示=设备安装基准(切线), 与开同步(世界方向)形成可见区别
        #   (2026-08-09 用户实测: 2D XY 240 台 uniform 关同步也返回世界方向
        #   → 开/关十字线完全一样 → 「没有同步效果」。uniform 只约束引擎目标
        #   角(_sync_angle_with_global 用于 set_xy_reference_global_angle 写 cfg),
        #   显示层关同步一律画 base 基准, 不随 uniform 走世界方向。)
        return _normalize_angle_deg(float(base) + offset + self._xy_reference_global_angle_deg)

    def _sync_angle_with_global(self, base_angle: float, cfg: Optional[dict] = None) -> float:
        offset = _safe_float((cfg or {}).get("angle_offset_deg", 0.0), 0.0)
        # ★ 圆排多设备统一化（2026-08-04）：统一设备的世界目标角 = 偏移 + 全局角
        #   （不叠加安装基准 base——静止波束 = 安装 + (目标−安装) = 目标，全部
        #   设备同一世界方向；基准单独承载安装朝向供引擎作差）。
        if bool((cfg or {}).get("uniform_world_angle", False)):
            return _normalize_angle_deg(offset + self._xy_reference_global_angle_deg)
        return _normalize_angle_deg(float(base_angle) + offset + self._xy_reference_global_angle_deg)

    def set_xy_reference_global_angle(self, angle_deg: float):
        angle = _normalize_angle_deg(float(angle_deg or 0.0))
        # ★ 旋转前修剪非法基准（旧数据恢复的假 fid/非 XY 设备），防它们随全局角
        #   空转（审查 R4）。
        if self._prune_invalid_sync_references() > 0:
            self._emit_xy_reference_changed()
        if abs(angle - self._xy_reference_global_angle_deg) < 1e-6:
            return
        self._xy_reference_global_angle_deg = angle
        if hasattr(self, "_xy_ref_global_angle") and abs(self._xy_ref_global_angle.value() - angle) > 1e-6:
            old = self._xy_ref_global_angle.blockSignals(True)
            self._xy_ref_global_angle.setValue(angle)
            self._xy_ref_global_angle.blockSignals(old)
        for fid, cfg in list(self._xy_reference_by_fixture.items()):
            if str((cfg or {}).get("mode", "default")).lower() != "sync":
                continue
            base = _safe_float(cfg.get("base_angle_deg", cfg.get("angle_deg", 0.0)), 0.0)
            updated = dict(cfg)
            updated["base_angle_deg"] = float(base)
            updated["angle_deg"] = float(self._sync_angle_with_global(base, updated))
            self._xy_reference_by_fixture[str(fid)] = updated
        self._update_xy_reference_label()
        self._emit_xy_reference_changed()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()

    def set_xy_reference_z_angle(self, angle_deg: float):
        """Z 轴扇面基准角（-180°~+180°，默认 0°=水平+X）。

        引擎在 motor_z 输出时用「扇面角 − 安装基准角」计算每台三维设备的电机角，
        全组扇面水平平行（2026-08-04 用户需求）。变更即生效：engine map 由
        xy_reference_changed 重建。
        """
        angle = _normalize_angle_deg(float(angle_deg or 0.0))
        if abs(angle - self._xy_reference_z_angle_deg) < 1e-6:
            return
        self._xy_reference_z_angle_deg = angle
        if hasattr(self, "_xy_ref_z_angle") and abs(self._xy_ref_z_angle.value() - angle) > 1e-6:
            old = self._xy_ref_z_angle.blockSignals(True)
            self._xy_ref_z_angle.setValue(angle)
            self._xy_ref_z_angle.blockSignals(old)
        # cfg 内快照同步（引擎以 state 全局值优先，cfg 值作存档记录/旧数据回退）
        for fid, cfg in list(self._xy_reference_by_fixture.items()):
            if str((cfg or {}).get("mode", "default")).lower() != "sync":
                continue
            updated = dict(cfg)
            updated["z_angle_deg"] = float(angle)
            self._xy_reference_by_fixture[str(fid)] = updated
        self._update_xy_reference_label()
        self._emit_xy_reference_changed()
        if hasattr(self, "_canvas"):
            self._canvas.update()

    def _selected_xy_fixture_ids(self) -> List[str]:
        return [fid for fid in self._ordered_selected_ids() if self._fixture_is_xy(fid)]

    def _xy_completeness_score(self, fid: str) -> int:
        """XY 两轴完整度：16bit（motor_x_fine+motor_y_fine 齐全）=2，8bit=1。

        仅对 _fixture_is_xy 为真的设备有意义；用于设备包内挑选「真正具备完整 XY
        两轴的那一个子设备」（用户原则：一套整体设备只有一个 XY 基底电机）。
        """
        fi = self.fixtures_by_id().get(str(fid))
        fd = self.fixture_defs_by_id().get(str(getattr(fi, "def_id", "")) if fi is not None else "")
        channels = list(getattr(fd, "channels", []) or []) if fd is not None else []
        return 2 if ("motor_x_fine" in channels and "motor_y_fine" in channels) else 1

    def _xy_reference_rank(self, fid: str) -> tuple:
        """同步基准择优排序键：(XY 完整度, def 通道数)。

        平局时选通道更完整的设备（如 12ch 基底电机 vs 脏 16bit 二仪 5ch），
        避免旧实现按成员列表序选主导致二仪/喷头等「恰好含 Y 的简化 def」夺基准
        （审查 R5：265/265 包全选错场景）。
        """
        score = self._xy_completeness_score(str(fid))
        fi = self.fixtures_by_id().get(str(fid))
        fd = self.fixture_defs_by_id().get(str(getattr(fi, "def_id", "")) if fi is not None else "")
        n = len(list(getattr(fd, "channels", []) or [])) if fd is not None else 0
        return (score, n)

    def _prune_invalid_sync_references(self) -> int:
        """清除所有非法同步基准条目，返回清除数。

        非法 = fid 不在工程 / 非 XY 设备（def 修正后二仪/喷头等）/ 包内收缩掉的
        非目标。旧数据防御：旧版本或手动配置可能把简化电机设备写入 sync 态，
        原 _refresh_sync_reference_for_ids 只 pop 本次 sync_ids 内的收缩项，
        defs 修正后这些条目漏网、继续随全局角旋转（审查 R3/R4）。
        """
        all_sync = [
            str(fid) for fid, cfg in self._xy_reference_by_fixture.items()
            if str((cfg or {}).get("mode", "default")).lower() == "sync"
        ]
        if not all_sync:
            return 0
        xy_ids = [fid for fid in all_sync if self._fixture_is_xy(str(fid))]
        targets = set(self._sync_reference_target_ids(xy_ids))
        removed = 0
        for fid in all_sync:
            cfg = self._xy_reference_by_fixture.get(str(fid)) or {}
            # ★ 尖点 X-only（2026-08-05）：落叶头尖点对为同步显式加入的 X-only
            #   设备（x_only_align）——非 XY 不在目标集内, 但属有意同步, 不剪。
            if str(fid) not in targets and not bool(cfg.get("x_only_align", False)):
                self._xy_reference_by_fixture.pop(str(fid), None)
                removed += 1
        return removed

    def _sync_reference_target_ids(self, ids: List[str]) -> List[str]:
        """同步基准的修改目标集：每个设备包至多保留一个真正 XY 两轴的子设备。

        包 = 页上逻辑设备（包 kind=package 或用户组合）。包内可能有多个子设备
        的 def 都含 motor_x/motor_y（如二仪云台），但用户组合原则是「一套整体设备只有
        一个 XY 基底电机」——按 _xy_completeness_score 挑最完整者（16bit 优先），
        其余成员不参与同步基准（跟随/不动）。散台（非任何包成员）保持自身。
        """
        candidates = [str(fid) for fid in (ids or []) if self._fixture_is_xy(str(fid))]
        if not candidates:
            return []
        cand_set = set(candidates)
        pkg_id_by_member: Dict[str, str] = {}
        pkg_members: Dict[str, List[str]] = {}
        for page in (self._pages if isinstance(self._pages, list) else []):
            if not isinstance(page, dict):
                continue
            for ld in (page.get("logical_devices", []) or []):
                members = [str(m) for m in (ld.get("member_fixture_ids", []) or [])]
                if not members:
                    continue
                ld_id = str(ld.get("id") or id(ld))
                pkg_members[ld_id] = members
                for m in members:
                    pkg_id_by_member.setdefault(m, ld_id)
        out: List[str] = []
        seen_pkgs: set = set()
        for fid in candidates:
            pkg_id = pkg_id_by_member.get(fid)
            if pkg_id is None:
                out.append(fid)
                continue
            if pkg_id in seen_pkgs:
                continue
            seen_pkgs.add(pkg_id)
            pkg_cands = [m for m in pkg_members.get(pkg_id, []) if m in cand_set]
            if pkg_cands:
                # 择优：16bit 完整 XY 优先，平局选 def 通道更多者（基底电机 12ch
                # 优于脏简化 def），不按成员列表序（审查 R5）。
                out.append(max(pkg_cands, key=lambda m: self._xy_reference_rank(m)))
        return out

    def _apex_xonly_align_ids(self, ids: List[str]) -> List[str]:
        """落叶头尖点对 X-only 同步加入（2026-08-05 用户实测一对一硬修）。

        用户实测：落叶头弧线最顶上一台（配接约 域25 通道464）推 X 方向与其它
        设备不符。根因（至冬7.0 原始 project.xml 实证）：该设备 + 对称台
        （域2 通道331）为 5ch 全能卡一维（仅 motor_x, 无 motor_y → 非 XY →
        不在同步目标内）, 且 rotz=±180 反装、安装朝向与所属包交叉（左包右挂
        roty=+35.39 / 右包左挂 roty=−35.50——6a4673c 顶部 22 台 XYZ 摆正时
        同结构但非 XY 未被覆盖）→ 推 X 时 motor_x 原始旋转在交叉挂载下世界
        反向（≈180° 相反）。硬修：整页叶形基准计算门控内把尖点对 X-only 显式
        加入同步（base=180° 反装交叉翻转, angle=0 统一世界角）→ 推 X 世界
        ≈0° 与其它顶部一致（输出侧 y 自映射见 xy_reference x_only_align）。
        门控 = 与 _top_motor_align 同款：仅整页叶形基准计算（全选 73 / 子集
        回退递归, len(leaf_targets) >= len(ids)）; 非叶形页（底弧线/上弧线/
        副池/百变）leaf_targets 恒空 → 空; 叶形页其余设备不在尖点包 → 空;
        异常返回空（防御）。
        """
        if not ids or len(ids) < 2:
            return []
        try:
            _all_splines = list(getattr(self._current_project(), "splines", []) or [])
        except Exception:
            _all_splines = []
        try:
            _leaf_targets = self._leaf_page_targets_for_subset(ids, _all_splines)
        except Exception:
            _leaf_targets = set()
        if not _leaf_targets or len(_leaf_targets) < len(ids):
            return []
        fixtures = self.fixtures_by_id()
        defs = self.fixture_defs_by_id()
        # 尖点对 = world_z 最高的 2 台同步目标（叶尖）所属包
        tops = sorted(ids, key=lambda f: -_safe_float(
            getattr(fixtures.get(str(f)), "world_z", 0.0), 0.0))[:2]
        # 包代表（base_fixture_id）= 全能卡一维 X-only（5ch 仅 motor_x）——
        # 尖点对交叉挂载即该台; 非 X-only 则跳过（防御）
        pkg_base: Dict[str, str] = {}
        for page in (self._pages if isinstance(self._pages, list) else []):
            if not isinstance(page, dict):
                continue
            for ld in (page.get("logical_devices", []) or []):
                members = [str(m) for m in (ld.get("member_fixture_ids", []) or [])]
                if not members:
                    continue
                base = str(ld.get("base_fixture_id", "") or members[0])
                for m in members:
                    pkg_base.setdefault(m, base)
        out: List[str] = []
        for t in tops:
            base_fid = pkg_base.get(str(t))
            if not base_fid or base_fid in out:
                continue
            fi = fixtures.get(base_fid)
            fd = defs.get(str(getattr(fi, "def_id", ""))) if fi is not None else None
            chs = list(getattr(fd, "channels", []) or []) if fd is not None else []
            if "motor_x" in chs and "motor_y" not in chs:
                out.append(base_fid)
        return out

    def _fixture_is_flipped(self, fid: str) -> bool:
        """Legacy tangent correction for the physically same +/-180 mount.

        Final writes use installation_heading, so this geometric intermediate
        cannot override the fixture's directed installation or its valid zero Y.
        """
        fi = self.fixtures_by_id().get(str(fid))
        if fi is None:
            return False
        rx = _safe_float(getattr(fi, "rot_x", 0.0), 0.0)
        rz = _safe_float(getattr(fi, "rot_z", 0.0), 0.0)
        return (abs((rx % 360.0) - 180.0) < 1e-3
                and abs((rz % 360.0) - 180.0) < 1e-3)

    def _flip_sync_angles(self, angles: Dict[str, float],
                          ids: List[str]) -> Dict[str, float]:
        """Correct the legacy tangent representative before physical binding.

        +180 and -180 rotations are identical. The final per-fixture write
        resolves full installation via _xy_install_angle for both spellings.
        """
        if not angles:
            return angles
        flipped = [str(fid) for fid in (ids or []) if self._fixture_is_flipped(str(fid))]
        if not flipped:
            return angles
        out = dict(angles)
        for fid in flipped:
            if fid in out:
                # raw = −存储 RotY → +180 = 180 − 存储 = 物理安装朝向
                out[fid] = _normalize_angle_deg(float(out[fid]) + 180.0)
        return out

    def _circle_anchor_cluster_angles(
        self, pts: Dict[str, tuple], ordered: List[str],
        splines: list,
    ) -> Dict[str, float]:
        """主池圆页逐环切线同步角（2026-08-08 用户「拟合完成之后按照圆去同步」）。

        按整圆/星形样条锚点（_circle_anchor_centers，世界坐标）分簇：每台
        设备归「归一化距离 d/R 最接近 1.45（放大环中位 1.25~1.65R）且在
        [0.6R, 2.0R] 窗口内」的锚点——多锚点窗口重叠时按放大比判定归属
        （2026-08-08 修正：邻近锚点圆心距 ~3000 而窗口 2.5R 互相吞设备 →
        邻圆放大环设备被切进错误小带 → 方向修正错乱翻转）。簇内按距离
        分带（间距 > max(200, 0.15×中位) 切新带；中心锚点内外两层环
        = 两个圆），每带 ≥4 台按圆心切线角（与 _fit_circle 分支同公式，
        位置角环序相邻中点差做 ±180° 方向修正）。仅 out_arc 空时由调用方
        启用——副池扇子等已有正确覆盖的路径零变化。不翻转（RotZ 门控由
        _flip_sync_angles 单独管，本路径不调用）。
        """
        try:
            from .geometry_splines import _circle_anchor_centers
        except Exception:
            return {}
        _anchors = _circle_anchor_centers(splines)
        if not _anchors:
            return {}
        # 每台设备归「放大比最接近 1.45 且窗口 [0.6R, 2.0R]」的锚点
        _owner: Dict[str, int] = {}
        for _fid in ordered:
            _x, _y = pts[_fid]
            _best = -1
            _best_gap = float("inf")
            for _ai, (_cx, _cz, _ar, _star) in enumerate(_anchors):
                _d = math.hypot(_x - _cx, _y - _cz)
                if _d < 0.6 * _ar or _d > 2.0 * _ar:
                    continue
                _gap = abs(_d / _ar - 1.45)
                if _gap < _best_gap:
                    _best = _ai
                    _best_gap = _gap
            if _best >= 0:
                _owner[_fid] = _best
        _by_anchor: Dict[int, list] = {}
        for _fid, _ai in _owner.items():
            _cx, _cz, _ar, _star = _anchors[_ai]
            _dd = math.hypot(pts[_fid][0] - _cx, pts[_fid][1] - _cz)
            _by_anchor.setdefault(_ai, []).append((_dd, _fid))
        out: Dict[str, float] = {}
        used: set = set()
        for _ai, _cand in _by_anchor.items():
            _cx, _cz, _ar, _star = _anchors[_ai]
            if len(_cand) < 4:
                continue
            _cand.sort()
            _bands: list = [[_cand[0]]]
            for _dd, _ff in _cand[1:]:
                _bm = _bands[-1][0][0]
                if _dd - _bm > max(200.0, 0.15 * _ar):
                    _bands.append([])
                _bands[-1].append((_dd, _ff))
            for _band in _bands:
                if len(_band) < 4:
                    continue
                _fids = [_ff for _, _ff in _band]
                _bpt = {f: pts[f] for f in _fids}
                _angle_order = sorted(
                    _fids,
                    key=lambda f: math.atan2(_bpt[f][1] - _cz, _bpt[f][0] - _cx))
                _n = len(_angle_order)
                for _i, _fid in enumerate(_angle_order):
                    _x, _y = _bpt[_fid]
                    _tx, _ty = -(_y - _cz), _x - _cx
                    if abs(_tx) <= 1e-9 and abs(_ty) <= 1e-9:
                        _tx, _ty = 1.0, 0.0
                    _pr = _angle_order[(_i - 1) % _n]
                    _nx = _angle_order[(_i + 1) % _n]
                    _ndx = _bpt[_nx][0] - _bpt[_pr][0]
                    _ndy = _bpt[_nx][1] - _bpt[_pr][1]
                    if _tx * _ndx + _ty * _ndy < 0.0:
                        _tx, _ty = -_tx, -_ty
                    out[_fid] = _normalize_angle_deg(
                        -math.degrees(math.atan2(_ty, _tx)))
                    used.add(_fid)
        return out

    def _build_xy_pts(self, fids: List[str]) -> Tuple[Dict[str, Tuple[float, float]],
                                                       Dict[str, Tuple[float, float]]]:
        """包锚点反算世界点集（2026-08-05 提取, 口径与同步基准计算一致）。

        ★ 包锚点（2026-08-04 A1 终验补齐）：生产 targets = 包内基底电机 fid,
        其 world 坐标含包内局部偏移（落叶头 73/73 台恒 9.4 单位）→ P2 翼
        判定/PCA 排序失真 → 生产真机中位 73.84°;终验复刻/探针用包锚点
        （world_xy(top_package)）达 98.5%/94.8%。由页逻辑设备 local_offsets
        （画布偏移 = csv_to_canvas 原始差值,与导入一致）反算包锚点世界坐标;
        散台/无偏移成员保持自身（与复刻口径一致）。
        返回 (pts, off_by_fid)——off_by_fid 供 _arc_tip_direction_fix 翼判定。
        """
        pts: Dict[str, Tuple[float, float]] = {}
        off_by_fid: Dict[str, Tuple[float, float]] = {}
        try:
            for page in (self._pages if isinstance(self._pages, list) else []):
                if not isinstance(page, dict):
                    continue
                for ld in (page.get("logical_devices", []) or []):
                    _offs = ld.get("local_offsets") or {}
                    for m in (ld.get("member_fixture_ids", []) or []):
                        off = _offs.get(str(m))
                        if off:
                            off_by_fid.setdefault(
                                str(m), (float(off[0]), float(off[1])))
        except Exception:
            off_by_fid = {}
        for fid in fids:
            fi = self.fixtures_by_id().get(str(fid))
            wx = _safe_float(getattr(fi, "world_x", 0.0), 0.0)
            wz = _safe_float(getattr(fi, "world_z", 0.0), 0.0)
            if abs(wx) > 1.0 or abs(wz) > 1.0:
                off = off_by_fid.get(str(fid))
                if off:
                    try:
                        from .import_patch_csv import (
                            DEFAULT_IMPORT_SCALE, Y_AXIS_SIGN)
                        pts[fid] = (wx - off[0] / DEFAULT_IMPORT_SCALE,
                                    wz - off[1] / (DEFAULT_IMPORT_SCALE * Y_AXIS_SIGN))
                    except Exception:
                        pts[fid] = (wx, wz)
                else:
                    pts[fid] = (wx, wz)
            else:
                pts[fid] = self.fixture_pos(str(fid))
        return pts, off_by_fid

    def _page_device_ids(self, page: dict) -> List[str]:
        """页设备集 = 可见设备 + 逻辑设备成员（去重, 只保留真实设备）。

        与 _tmp_leaf16_verify 探针的页设备口径一致（落叶头页可见 0 台 +
        成员 584 台）。供 F2-L1 页级回退 / F4/F5 叶形页判定使用。
        """
        valid = set(self.fixtures_by_id().keys())
        out: List[str] = []
        seen: set = set()
        for fid in (page.get("visible_fixture_ids", []) or []):
            sfid = str(fid)
            if sfid in valid and sfid not in seen:
                out.append(sfid)
                seen.add(sfid)
        for ld in (page.get("logical_devices", []) or []):
            for m in (ld.get("member_fixture_ids", []) or []):
                sfid = str(m)
                if sfid in valid and sfid not in seen:
                    out.append(sfid)
                    seen.add(sfid)
        return out

    def _subset_pages_leaf_match(self, ordered: List[str],
                                 splines: List[dict]) -> bool:
        """子集设备所属页的整页点集是否匹配闭合非星形样条（2026-08-05 F4/F5）。

        落叶头/落叶头气爆 = 闭合叶形样条页（子集镜像签名 <75% 时统一化门控）;
        上弧线（盒探出样条盒 ~6000）/底弧线/百变/副池恒 False → 零变化。
        异常返回 False。
        """
        try:
            if not ordered or not splines:
                return False
            ordered_set = set(ordered)
            for page in (self._pages if isinstance(self._pages, list) else []):
                if not isinstance(page, dict):
                    continue
                page_ids = self._page_device_ids(page)
                if not page_ids or not any(f in ordered_set for f in page_ids):
                    continue
                targets = self._sync_reference_target_ids(page_ids)
                if len(targets) < 4:
                    continue
                page_pts, _ = self._build_xy_pts(targets)
                if _leaf_closed_spline_page(page_pts, splines):
                    return True
            return False
        except Exception:
            return False

    def _match_splines_for_subset_pages(self, ordered: List[str],
                                        all_splines: List[dict]) -> List[dict]:
        """子集页级样条回退（2026-08-05 F2-L1）: 子集设备所属页的整页设备集
        匹配样条, 各页结果取并集（按对象 id 去重）。

        页匹配空（子集包围盒只盖叶形样条一部分, 顶部 16 台页匹配 0 条）时
        按整页点集匹配——落叶头页 73 台 → 只返回叶形样条, 不再回退全工程样条
        错归属（V4 实测 37° 偏: 样条[6] d=123.7 吃走 2 台左翼）。全部失败
        返回空 → 调用方回退全样条（防御）。异常返回空。
        """
        try:
            from .geometry_splines import match_splines_to_page
            if not ordered or not all_splines:
                return []
            ordered_set = set(ordered)
            result: List[dict] = []
            seen: set = set()
            for page in (self._pages if isinstance(self._pages, list) else []):
                if not isinstance(page, dict):
                    continue
                page_ids = self._page_device_ids(page)
                if not page_ids or not any(f in ordered_set for f in page_ids):
                    continue
                targets = self._sync_reference_target_ids(page_ids)
                if len(targets) < 4:
                    continue
                page_pts, _ = self._build_xy_pts(targets)
                matched = match_splines_to_page(
                    [page_pts[f] for f in targets], all_splines)
                for sp in matched:
                    if id(sp) not in seen:
                        seen.add(id(sp))
                        result.append(sp)
            return result
        except Exception:
            return []

    def _leaf_page_targets_for_subset(self, ordered: List[str],
                                      splines: List[dict]) -> set:
        """子集设备所属「闭合非星形样条页」（落叶头/落叶头气爆）的整页同步目标集。

        与 _subset_pages_leaf_match 同门控（_leaf_closed_spline_page）; 返回所有
        命中页的目标并集。供子集整页基准回退使用（2026-08-05: 子集方向 = 设备
        在全集中的真实方向）。异常返回空集。
        """
        out: set = set()
        try:
            if not ordered or not splines:
                return out
            ordered_set = set(ordered)
            for page in (self._pages if isinstance(self._pages, list) else []):
                if not isinstance(page, dict):
                    continue
                page_ids = self._page_device_ids(page)
                if not page_ids or not any(f in ordered_set for f in page_ids):
                    continue
                targets = self._sync_reference_target_ids(page_ids)
                if len(targets) < 4:
                    continue
                page_pts, _ = self._build_xy_pts(targets)
                if _leaf_closed_spline_page(page_pts, splines):
                    out.update(targets)
            return out
        except Exception:
            return out

    def _compute_xy_reference_angles(
        self, ids: List[str], return_cluster: bool = False,
    ):
        # ★ 同步基准的目标是包内基底电机（成员不在页可见集，只画代表点）——
        #   过滤必须放行全部真实设备，否则 ordered 恒空 → 基准角度全 0 →
        #   「同步没效果」（2026-08-03 用户实测：底弧线 120 台位置有效成弧线，
        #   但 _compute_xy_reference_angles 返回 0 台）。
        # return_cluster=True 时返回 (angles, 圆簇设备集)——圆排多设备统一化
        #   （2026-08-04）：星形外顶点圆簇（_star_circle_override 门控）成员
        #   同步后统一世界方向（静止 = 全局角、推 X/Y 世界一致、扇面水平）。
        _cluster: set = set()

        def _ret(out_dict: Dict[str, float]):
            return (out_dict, _cluster) if return_cluster else out_dict

        valid = set(self.fixtures_by_id().keys())
        ordered = [str(fid) for fid in ids or [] if str(fid) in valid]
        if not ordered:
            return _ret({})
        # 位置优先用真实世界坐标（场地坐标，沿父链变换）——包内局部/画布
        # 坐标受包 RotY 旋转影响，切向方向会失真；world 缺失时回退画布坐标。
        # ★ 包锚点反算口径见 _build_xy_pts（2026-08-05 提取, 逻辑逐字节不变）。
        pts, off_by_fid = self._build_xy_pts(ordered)
        point_list = [pts[fid] for fid in ordered]
        try:
            # ★ 样条读提前（2026-08-05 F4/F5）：子集统一化门控需要全工程样条
            #   判断「闭合非星形样条页」; 读取失败 = 空列表, 与旧路径失败语义一致。
            _all_splines = list(getattr(self._current_project(), "splines", []) or [])
        except Exception:
            _all_splines = []
        out: Dict[str, float] = {}
        # ★ 落叶头页子集 → 整页全量基准（2026-08-05 用户「任意选择子集同步方向
        #   必须正确」）：子集（任意台数 1..72）直接取所属叶形页整页计算值——
        #   子集参与计算不改变设备世界方向语义。实测子集退化链（探针 wt_probe_*）:
        #   顶部尖点对（2116/2692）±35.5°→±2°（样条尖点切向）、叶基中部 12 台
        #   ±86°→±12°（PCA 序 CR 邻居交错对侧翼, 弦向近水平 = 90° 翻转）、
        #   随机 30 台 13-20 台 180°/90° 翻转（P2 门控在子集序上误用）、2-3 台
        #   KeyError 崩溃、单台恒 0.0。整页 73 台计算值 = 该设备真实方向（全选
        #   验证正确）→ 子集方向与全集一致。全选 73 台 len(_leaf_targets)==
        #   len(ordered) → 不触发, 全选路径零变化; 非叶形页（底弧线/上弧线等）
        #   设备不在 _leaf_targets → 零变化。
        try:
            _leaf_targets = self._leaf_page_targets_for_subset(ordered, _all_splines)
        except Exception:
            _leaf_targets = set()
        _leaf_sub = [f for f in ordered if f in _leaf_targets]
        if _leaf_sub and len(_leaf_targets) > len(ordered):
            _full_ang, _full_clu = self._compute_xy_reference_angles(
                list(_leaf_targets), return_cluster=True)
            _out_leaf = {f: _full_ang[f] for f in _leaf_sub if f in _full_ang}
            _cluster |= set(_leaf_sub)
            _rest = [f for f in ordered if f not in _leaf_targets]
            if _rest:
                _rest_ang, _rest_clu = self._compute_xy_reference_angles(
                    _rest, return_cluster=True)
                _out_leaf = {**_out_leaf, **_rest_ang}
                _cluster |= _rest_clu
            return _ret(_out_leaf)
        # ★ 对称双翼统一化（2026-08-05 用户语义：同步 = 统一世界方向, 两翼不
        #   反向——与副池三维扇子统一化（10b1d99/dbe8922）同机制扩展）。镜像
        #   签名门控（P2 同款, 全设备集 PCA 序）命中率 ≥75% 的设备组 = 对称
        #   双翼（至冬7.0 实测：落叶头 91.8%/落叶头气爆 94.4% 命中; 底弧线
        #   5.0%/底弧线气爆 2.9%/上弧线 13.9%/上弧线气爆 12.5%/副池气爆 4.2%/
        #   副池大气爆 10.0%/百变 70.8% 全不命中 → 零变化）。命中设备写入
        #   uniform_world_angle → 引擎内容旋转 = base − angle（世界目标 =
        #   偏移+全局角, 不含 base）→ 世界波束 = 内容角 + 偏移 + 全局角 +
        #   (安装朝向−base) 单台残差——全部设备一致、两翼同向; base 计算
        #   即使再错（如 1418416 修复前的尖点对 ±35.5 互换）也只留单台残差,
        #   不再翼级翻转整翼反向。
        if len(ordered) >= 4:
            try:
                _ax0, _ay0, _ = _pca_axis(point_list)
                _ord0 = sorted(
                    ordered, key=lambda f: pts[f][0] * _ax0 + pts[f][1] * _ay0)
                _sym_ratio, _sym_hits, _sym_total = _leaf_p2_gate_ratio(
                    pts, _ord0)
                if _sym_ratio >= 0.75:
                    _cluster |= set(ordered)
                # ★ 落叶头子集统一化（2026-08-05 F4/F5）：子集（顶部 16 台等）
                #   镜像签名门控不足 75%（V4 实测 0%）→ 不统一 → 逐台切向世界
                #   发散（37° 偏/邻居翻转 180°）。子集设备所属页整页点集落在
                #   「闭合非星形样条」（vesica 叶形, 落叶头/落叶头气爆页）上 →
                #   子集同属叶形页 → 整子集统一世界方向（与全选 73 台对称双翼
                #   统一化同机制: 推 X 世界 ≈0°、推 Y ≈90°、两翼同向、Z 扇面
                #   水平）。上弧线（盒探出样条盒 ~6000）/底弧线/百变/副池恒
                #   False → 零变化; 下方 57 台 P2=89% ≥75% 走上方短路不触发。
                elif self._subset_pages_leaf_match(ordered, _all_splines):
                    _cluster |= set(ordered)
            except Exception:
                pass
        if len(ordered) == 1:
            return _ret(self._flip_sync_angles({ordered[0]: 0.0}, ordered))

        # ★ 样条 = 均匀参数自然三次样条（2026-08-03 反编译 CreateSpline3D
        #   + 120 台设备实测残差 ≤0.19 世界单位，即渲染弧线本身）：弧线重合时切向
        #   直接取渲染弧线解析导数；仍须检查与设备重合，不重合不用（退回设备
        #   定位点自拟合 Catmull-Rom）。
        #   性能（2026-08-03）：全选 275 台时 deviation/param/最近点三个 Python
        #   全扫共 ~660ms（且全工程设备对单条弧线判定必拒绝，白算）——批量 numpy
        #   IEEE 逐位等价（对照 sync_angles_baseline.json 验证）。
        order_key: Dict[str, float] = {}
        # ★ 分簇归属（2026-08-03 用户实测：一页设备可沿多条弧线/星形排列——
        #   副池=星形+Spline 分支；只取单条弧线会让其余分支投影错误
        #   「各自为战」）。每台设备归最近且贴合（≤跨度×1%）的弧线取渲染
        #   样条解析切向；未归属设备退回设备自拟合 Catmull-Rom。
        out_arc: Dict[str, float] = {}
        _assign: Dict[str, tuple] = {}
        _full_ordered_before_cluster = list(ordered)
        _subset_page_fallback = False
        try:
            # ★ 分簇样条输入 = 页匹配（2026-08-04 A1 终验修正）：全工程样条会让
            #   落叶头 26 台错归属 7 条别的样条（距离 ≤1% 跨度但方向错）→ 气爆
            #   P2 门控 71.6%<75% 不启用 → 落叶头 65.8%/气爆 41.7%；页匹配
            #   （match_splines_to_page 只返回落叶头页的 7 点 Spline）→ 98.5%/
            #   94.8%（P2 探针 _tmp_leaf_p2_final.py 基线）。参数与 P2 探针一致
            #   （min_overlap_ratio=0.5、min_fit_ratio=0.0 默认）。
            #   ★ 子集页级回退（2026-08-05 F2-L1）：子集点集（顶部 16 台）包围盒
            #   只盖叶形样条一部分 → 页匹配空 → 旧路径回退全工程样条 → 错归属
            #   别的样条（V4 实测 37° 偏: 样条[6] d=123.7 吃走 2 台左翼）。
            #   改按子集设备所属页的整页设备集匹配（各页取并集）, 仍空才回退
            #   全样条（防御）。页匹配/整页匹配均非空时与旧路径逐字节一致。
            from .geometry_splines import match_splines_to_page
            _matched = match_splines_to_page(point_list, _all_splines)
            if _matched:
                _splines = _matched
            else:
                _page_matched = self._match_splines_for_subset_pages(
                    ordered, _all_splines)
                _subset_page_fallback = bool(_page_matched)
                _splines = _page_matched or _all_splines
            if _splines:
                out_arc, ordered, _assign = _spline_cluster_angles(
                    pts, _splines, ordered, return_assign=True)
        except Exception:
            out_arc, ordered = out_arc, ordered
        # ★ 星形样条外顶点圆簇 → 圆切向覆盖（2026-08-04 用户授权「自动加圆」）：
        #   副池三维扇子两套各 5 台散台在星形外顶点上（圆拟合 rms≈0），但摆放
        #   沿圆切向，星形切向错 72°——只在「整簇归属同一颗星形样条且紧贴一个
        #   圆」时覆盖（_star_circle_override 门控），弧线归属页面零变化。
        _circle_ov: Dict[str, float] = {}
        if out_arc and _assign:
            try:
                _circle_ov = _star_circle_override(pts, _assign, _splines)
                if _circle_ov:
                    out_arc = {**out_arc, **_circle_ov}
            except Exception:
                pass
            # ★ 圆排多设备统一化（2026-08-04）：圆簇成员 = 星形外顶点圆覆盖命中的
            #   设备（_star_circle_override 门控 = 通用几何门：整簇贴同一颗星形
            #   样条 + 紧贴一个圆）。调用方据此写 uniform_world_angle —— 弧线
            #   （底弧线/上弧线等非星形样条）恒不命中，零变化。|= 并集保留
            #   对称双翼统一化（2026-08-05）已加入的设备。
            _cluster |= set((_circle_ov or {}).keys())
            # ★ 弧线归属尖点方向修复（2026-08-04 用户实测: 落叶头头部（顶部
            #   叶尖区）同步方向与底部正确方向不一致——vesica 双翼在顶部叶尖/
            #   底部叶基的汇聚点设备贴 7 点 Spline 但样条在尖点处切向与设备
            #   实际摆放方向系统性不符 19°-114°）。仅「闭合非星形样条」
            #   （vesica 叶形）的弧线归属设备参与; 开放弧线/星形（底弧线/
            #   上弧线/副池/百变的 Spline 2）切向与设备一致, 零触发零变化。
            try:
                # ★ 子集页级回退时尖点修复邻居 = 整子集（2026-08-05 F2-L1）：
                #   落叶头顶部 16 台全贴叶形样条 → 退回集空 → 旧调用邻居为空
                #   → 尖点对（锚点重合）不修复 → base 取样条尖点切向（与安装
                #   朝向差 19°-114°）→ 推 X/Y 世界偏。回退分支用整子集找邻居
                #   （翼尾邻居 ~400 不受同弧线 <300 排除影响, 与全选 73 台
                #   同口径）; 非回退分支保持现状（rest 邻居）。
                _tip_fix = _arc_tip_direction_fix(
                    pts, out_arc,
                    _full_ordered_before_cluster if _subset_page_fallback else ordered,
                    off_by_fid, _assign, _splines)
                if _tip_fix:
                    out_arc = {**out_arc, **_tip_fix}
            except Exception:
                pass
        if not ordered:
            # ★ 落叶头顶部电机 ±90° 摆正（2026-08-05 用户实测）: 全样条归属
            #   时在 out_arc 上应用; 门控与主路径同（_top_motor_align）。
            return _ret(self._flip_sync_angles(
                _top_motor_align(out_arc, pts, ids, _leaf_sub, _leaf_targets),
                ids))
        point_list = [pts[fid] for fid in ordered]

        if _is_grid_like(point_list):
            return _ret(self._flip_sync_angles(
                {**out_arc, **{fid: 0.0 for fid in ordered}}, ordered))

        # ★ 落叶头子集圆拟合排除（2026-08-05 F3）：顶部 16 台子集点云近似圆弧段
        #   （r=2256 span=145° rms=174 通过门限）→ 伪圆拟合接管 12 台 → 圆切向
        #   世界方向乱 + 邻居翻转 180°。子集落在「闭合非星形样条」（叶形）上 →
        #   排除圆拟合走 CR 自拟合（与全选路径同机制）; 副池真圆/星形外顶点等
        #   不命中（副池 z>20000 远在叶形样条盒外）→ 圆拟合路径逐字节不变。
        if _subset_on_closed_leaf_spline(pts, ordered, _all_splines):
            fit = None
        else:
            fit = _fit_circle(point_list)
        if fit is not None:
            cx, cy, radius, rms = fit
            min_radius = min((math.hypot(x - cx, y - cy) for x, y in point_list), default=0.0)
            span = _angular_span_deg(point_list, cx, cy)
            if span >= 25.0 and min_radius >= max(5.0, radius * 0.35) and rms <= max(6.0, radius * 0.08):
                circular = span >= 300.0 and len(ordered) >= 4
                if circular:
                    angle_order = sorted(
                        ordered,
                        key=lambda item: math.atan2(pts[item][1] - cy, pts[item][0] - cx)
                    )
                    ordered_index = {fid: idx for idx, fid in enumerate(angle_order)}
                else:
                    angle_order = ordered
                    ordered_index = {fid: idx for idx, fid in enumerate(ordered)}
                for fid in ordered:
                    idx = ordered_index.get(fid, 0)
                    x, y = pts[fid]
                    tx, ty = -(y - cy), x - cx
                    if abs(tx) <= 1e-9 and abs(ty) <= 1e-9:
                        tx, ty = 1.0, 0.0
                    if circular:
                        prev_id = angle_order[(idx - 1) % len(angle_order)]
                        next_id = angle_order[(idx + 1) % len(angle_order)]
                    else:
                        prev_id = ordered[idx - 1] if idx > 0 else None
                        next_id = ordered[idx + 1] if idx < len(ordered) - 1 else None
                    if prev_id and next_id:
                        px, py = pts[prev_id]
                        nx, ny = pts[next_id]
                        ndx, ndy = nx - px, ny - py
                    elif next_id:
                        nx, ny = pts[next_id]
                        ndx, ndy = nx - x, ny - y
                    elif prev_id:
                        px, py = pts[prev_id]
                        ndx, ndy = x - px, y - py
                    else:
                        ndx, ndy = tx, ty
                    if tx * ndx + ty * ndy < 0.0:
                        tx, ty = -tx, -ty
                    out[fid] = _normalize_angle_deg(-math.degrees(math.atan2(ty, tx)))
                return _ret(self._flip_sync_angles({**out_arc, **out}, ordered))

        # ★ 主池圆页逐圆切线（2026-08-08 用户「拟合完成之后按照圆去同步计算」）：
        #   整页点云含 9 个同心放大环圆 → 整页 _fit_circle rms 巨大（1677 vs
        #   门限 530）失败、样条分簇空（设备在 1.25~1.65R 放大环上贴样条失败）
        #   → 旧路径 CR 把多圆混成一条线 → 等差伪切线乱角（用户实测「同步
        #   部分被翻转」）。按整圆/星形锚点分簇，每环单独切线角（与上方圆拟合
        #   分支同公式+邻居方向修正）。
        #   无条件执行（2026-08-08 修正）：气爆页 out_arc=30 非空时旧条件跳过
        #   本分支 → 318 台仍走 CR 伪切线。输入用 _full_ordered_before_cluster
        #   （样条分簇会过滤 ordered——气爆 30 台被接管后其余 318 台从列表消失,
        #   必须用原始全量）。副池扇子等已有 _star_circle_override 正确覆盖的
        #   路径:锚点分簇与圆切向同公式, 结果一致; 环带外设备保留 out_arc 值。
        try:
            _circ_anchor = self._circle_anchor_cluster_angles(
                pts, _full_ordered_before_cluster, _all_splines)
        except Exception:
            _circ_anchor = {}
        if _circ_anchor:
            _merged_anchor = {**_circ_anchor}
            for _f in out_arc:
                if _f not in _merged_anchor:
                    _merged_anchor[_f] = out_arc[_f]
            # ★ 二维数控页统一世界方向（2026-08-09 用户实测「同步方向错误」）：
            #   锚点分簇环上的 2D XY 设备（def 含 motor_x+motor_y 且无 motor_z
            #   ——改版至冬1 = 二维数控页 240 台 2-Axis Jet）并入圆簇 →
            #   uniform_world_angle=True → 引擎输出统一世界方向（本地 X 沿世界
            #   横坐标、Y 沿世界纵坐标, 随全局角整体旋转; 引擎世界目标 = 偏移+
            #   全局角, 安装基准仅作差, 全组同向）。三维设备（Nozzle Fan 含
            #   motor_z——主池/副池扇子）不入簇 → 逐台切线现状逐位不变; 无 2D
            #   XY 设备页/其它工程零变化。
            _cluster |= {_f for _f in _merged_anchor
                         if self._fixture_is_2d_xy(str(_f))}
            return _ret(self._flip_sync_angles(_merged_anchor, _full_ordered_before_cluster))

        # ★ 设备定位点自拟合平滑曲线（用户指正：折线≠弧线）：弦长参数化
        #   Catmull-Rom 过每台设备，导数 = 安装切向。
        # ★ 闭合环检测（2026-08-03 agent E 实测：落叶头 73 台=平滑闭合环线）。
        #   ★ 2026-08-04 修正：原「选择序首尾距」判定依赖输入序，而生产页面
        #   序受 set 遍历 hash 种子影响每进程不同 → 首尾距随机 → 闭环漏判/
        #   误判 → P2 偶用坏序输出乱角（终验 Part B 落叶头 73.84°→90° 波动
        #   实锤）。闭环检测统一在 PCA 投影序上做（只依赖坐标，确定性）；
        #   P2 门控/apply 恒在 PCA 序上算（探针 _tmp_leaf_p2_final.py 同口径
        #   98.5%/94.8%）；闭环 CR 用绕质心角度环序（平滑切线，确定性）。
        closed_loop = False
        if not order_key and len(ordered) >= 4:
            axis_x, axis_y, _ = _pca_axis(point_list)
            for fid in ordered:
                order_key[fid] = pts[fid][0] * axis_x + pts[fid][1] * axis_y
            _pc = sorted(ordered, key=lambda fid: order_key[fid])
            x0, y0 = pts[_pc[0]]
            x1, y1 = pts[_pc[-1]]
            d_fl = math.hypot(x1 - x0, y1 - y0)
            gaps = sorted(
                math.hypot(pts[_pc[k + 1]][0] - pts[_pc[k]][0],
                           pts[_pc[k + 1]][1] - pts[_pc[k]][1])
                for k in range(len(_pc) - 1))
            if gaps and d_fl <= gaps[len(gaps) // 2] * 1.5:
                closed_loop = True
        if not order_key:
            # ★ 退化防御（2026-08-05, 与 CE hook 语义一致）：原闭环块仅 len>=4
            #   时填充 order_key, 2-3 台子集（样条归属后剩余 <4 台）直接 sorted
            #   空键表 → KeyError → 生产同步静默失效（用户实测子集同步无反应）。
            #   补 PCA 投影序（确定性, 仅坐标依赖）——>=4 台路径恒不触发,
            #   与旧行为逐位一致。
            axis_x, axis_y, _ = _pca_axis(point_list)
            for fid in ordered:
                order_key[fid] = pts[fid][0] * axis_x + pts[fid][1] * axis_y
        ordered_sorted = sorted(ordered, key=lambda fid: order_key[fid])
        cr_order = ordered_sorted
        if closed_loop and len(ordered) >= 4:
            _cx = sum(pts[f][0] for f in ordered) / len(ordered)
            _cy = sum(pts[f][1] for f in ordered) / len(ordered)
            cr_order = sorted(
                ordered,
                key=lambda f: math.atan2(pts[f][1] - _cy, pts[f][0] - _cx))
        # ★ 落叶头双翼 P2 修复（2026-08-04, 探针 _tmp_leaf_p2_final.py 固化）：
        #   双翼 zigzag 交错序下 ±1 邻居 = 对侧翼方向、±2 邻居 = 同侧翼——
        #   现状 CR 自拟合用 ±1 邻居弦向, 顶部收拢区方向差 19°-147°。
        #   镜像签名门控（每台 circ_mod180(t1+t2)<20° 命中, 占比 ≥75%）通过
        #   才启用 P2（V4 实测：落叶头 91%/气爆 93%, 百变 35% 不启用 →
        #   门控不通过时保持现状 CR 零变化）。
        p2_map: Optional[Dict[str, float]] = None
        if len(ordered_sorted) >= 4:
            try:
                _p2_ratio, _p2_hits, _p2_total = _leaf_p2_gate_ratio(
                    pts, ordered_sorted)
                if _p2_ratio >= 0.75:
                    p2_map = _leaf_p2_apply(pts, ordered_sorted)
            except Exception:
                p2_map = None
        ts = [0.0]
        for k in range(1, len(cr_order)):
            x0, y0 = pts[cr_order[k - 1]]
            x1, y1 = pts[cr_order[k]]
            ts.append(ts[-1] + math.hypot(x1 - x0, y1 - y0))
        out = {}
        for idx, fid in enumerate(cr_order):
            if p2_map is not None:
                # P2 门控通过：±2 邻居（同侧翼）弦向 + 几何翼判定, 已含
                # ±90 钳位与画布镜像（探针逐台对拍一致）
                out[fid] = p2_map.get(fid, 0.0)
                continue
            dx, dy = _catmull_rom_tangent(pts, cr_order, idx, ts,
                                          closed=closed_loop)
            # 画布镜像（与弧线分支统一）：画布角 = -世界角 = RotY
            out[fid] = _normalize_angle_deg(-math.degrees(math.atan2(dy, dx)))
            # 设备 RotY 恒在 [-90,+90]（+x 半球归一）：闭合环线
            # （落叶头）朝左段切向 >90° 时翻转 180°
            if out[fid] > 90.0:
                out[fid] -= 180.0
            elif out[fid] < -90.0:
                out[fid] += 180.0
        _merged = {**out_arc, **out}
        # ★ 落叶头子集方向门（2026-08-05 F2-L2, 仅子集页级回退分支）：
        #   顶部中心区左右翼交错 → 序邻居弦向近水平 → base 偏 22°;
        #   最近邻弦向（翼方向）修正, 非回退分支零变化。
        if _subset_page_fallback and _merged:
            _merged = _leaf_subset_direction_gate(
                pts, _full_ordered_before_cluster, _merged)
        # ★ 落叶头顶部电机同步基准硬修正（2026-08-05 用户实测 90° 错位）:
        #   整页叶形基准（全选 73 / 子集回退递归）顶部 22 台 ±90° 摆正,
        #   与底部全朝右对齐; 其他页/底部/中部零变化（门控见 _top_motor_align）。
        _merged = _top_motor_align(_merged, pts, ids, _leaf_sub, _leaf_targets)
        return _ret(self._flip_sync_angles(_merged, ordered))

    def _emit_xy_reference_changed(self):
        project = self._current_project()
        if hasattr(project, "topview_state"):
            try:
                _persist_topview_state(self, project)
            except Exception:
                pass
        self.xy_reference_changed.emit(dict(self._xy_reference_by_fixture))

    def ensure_xy_reference_initialized(self) -> int:
        """导入/打开工程后兜底：默认状态不进同步模式 —— 同步由用户手动选择
        （apply_xy_reference_mode 按钮，2026-08-09 用户硬性要求）。

        引擎 build_xy_reference_map 只认 mode=sync 条目 → 不写 cfg = 零同步
        输出 = 电机透传安装朝向（默认静止），与「同步基准 = 世界 XY 轴平行
        方向」的同步语义有真实区别。uniform_world_angle 仅在用户 apply 时按
        簇判定（2D XY 复用三维非统一分支）。旧存档中已有的 sync 条目由
        load_state 原样恢复（含引擎层 2D XY 强制非统一兜底）。调用方
        （main_window L7163/L8357）以返回值>0 决定是否重建引擎 map，返回 0
        → 跳过重建，行为自洽。
        """
        return 0

    def _page_xy_sync_target_ids(self, page=None) -> List[str]:
        """整页 XY 同步目标集：当前页可见 XY 设备 ∪ 页内包/组合设备成员，
        经包收缩（每包至多保留一个 XY 基底电机）。

        ★ 2026-08-11 用户定案：同步基准 = 整页全选计算并锁定；任何子集操作
        只作遮罩（mask），不得重算基准。
        """
        # Keep the page's established fixture order.  The sync-angle solver uses
        # that order when assigning spline/arc tangents; feeding it a set makes
        # the same arc produce a different per-fixture reference on each run.
        from .topview_sync_context import whole_frame_page
        page = whole_frame_page(self, page)
        page_ids = self._page_device_ids(page)
        # 组合设备的成员通常不在 visible_fixture_ids，而是按导入顺序挂在
        # logical_devices 下。导入顺序可能与弧线位置顺序不同；同步角度计算的
        # 样条/邻居回退会消费这个序，直接使用导入序会在反复保存/恢复或切换
        # 同步模式后重新把弧线邻接关系打乱。复用 TopView 已有的包级视觉排序，
        # 让同步与框选、实时选择共享同一条几何顺序真源。
        has_packages = bool(page.get("logical_devices")) if isinstance(page, dict) else False
        if page_ids and has_packages:
            try:
                units = self._selection_units_from_base(page_ids, sort_by_pos=True, page=page)
                ordered = [fid for unit in units for fid in unit.get("fids", [])]
                if len(ordered) == len(page_ids) and set(ordered) == set(page_ids):
                    page_ids = ordered
            except Exception:
                # 兼容旧工程/不完整包数据：保留原页面序，不能因排序辅助失败
                # 阻断同步基准本身。
                pass
        if not page_ids:
            page_ids = list(self.fixture_ids())
        return self._sync_reference_target_ids(page_ids)

    def _refresh_sync_reference_for_ids(self, ids: List[str]):
        sync_ids = [
            str(fid) for fid in ids or []
            if self._fixture_is_xy(str(fid)) and self.xy_reference_mode_for_fixture(str(fid)) == "sync"
        ]
        # 包内至多一个目标（真正的 XY 基底电机），其余成员不参与基准重算。
        targets = self._sync_reference_target_ids(sync_ids)
        # ★ 全量修剪残留 sync 态：收缩掉的成员（多 XY 包）与 def 修正后不再是
        #   XY 的历史条目（二仪/喷头）一并清除，防旧角度继续随全局角旋转发散
        #   （审查 P2-3 / R3）。
        self._prune_invalid_sync_references()
        sync_ids = targets
        if not sync_ids:
            return
        # ★ 2026-08-11 用户定案：基准 = 整页锁定，子集只作遮罩。仅当本次拖拽/
        #   排列覆盖整页 XY 目标集（全页操作 → 几何变化需要新基准）才重算；
        #   子集操作保留 apply 时锁定的 base_angle_deg/uniform_world_angle，
        #   只重刷全局角联动 angle_deg 与 page_id —— 防子集个别设备基准带偏。
        full_targets = self._page_xy_sync_target_ids()
        if full_targets and set(sync_ids) >= set(full_targets):
            angles, cluster = self._compute_xy_reference_angles(full_targets, return_cluster=True)
        else:
            angles, cluster = None, set()
        for fid in sync_ids:
            cfg = dict(self._xy_reference_by_fixture.get(str(fid), {}) or {})
            if angles is not None:
                base_angle = float(angles.get(fid, 0.0))
                # ★ per-fixture 同步基准规则（2026-08-05）：域25 ch469 数控基底
                #   电机 180° 镜像 → +270°（与 apply_xy_reference_mode 同规则,
                #   拖拽/排列路径共用防漂移）。
                base_angle = _sync_base_angle_override(
                    fid, self._xy_install_angle(fid, base_angle, full_targets),
                    self.fixtures_by_id(), self.fixture_defs_by_id())
            else:
                # 子集遮罩路径：沿用 apply 时锁定的整页基准（含 override 与
                # uniform 判定结果），只重刷全局角联动 —— 防子集重算带偏个别设备。
                base_angle = _safe_float(cfg.get("base_angle_deg", cfg.get("angle_deg", 0.0)), 0.0)
            # ★ 圆排多设备统一化（2026-08-04）：星形外顶点圆簇成员统一世界方向
            #   （引擎按 uniform_world_angle 走统一目标/统一旋转）。flag 先写入，
            #   供 angle_deg 计算读取（目标角 = 偏移 + 全局角，不含 base）。
            #   2D XY 永 False（2026-08-10 用户实测最终纠正：2D XY 复用三维
            #   非统一分支 rad = base+θ，base = −真实安装 → 世界 = θ；uniform
            #   分支 rad = base−θ → 世界 2·base−θ 逐台发散）。
            #   子集遮罩路径不动此 flag —— 保留 apply 时锁定的整页判定值。
            if angles is not None:
                cfg["uniform_world_angle"] = str(fid) in cluster and not self._fixture_is_2d_xy(str(fid))
            cfg.update({
                "mode": "sync",
                "base_angle_deg": base_angle,
                "angle_deg": float(self._sync_angle_with_global(base_angle, cfg)),
                "z_angle_deg": float(self._xy_reference_z_angle_deg),
                "page_id": self.current_page().get("id"),
            })
            self._xy_reference_by_fixture[str(fid)] = cfg
        self._update_xy_reference_label()
        self._emit_xy_reference_changed()

    def _update_xy_reference_label(self):
        if not hasattr(self, "_xy_ref_label"):
            return
        selected = self._ordered_selected_ids()
        xy_selected = [fid for fid in selected if self._fixture_is_xy(fid)]
        sync_count = sum(1 for fid in xy_selected if self.xy_reference_mode_for_fixture(fid) == "sync")
        total_sync = sum(1 for fid in self.fixture_ids() if self.xy_reference_mode_for_fixture(fid) == "sync")
        self._xy_ref_label.setText(
            f"选区：{len(xy_selected)}/{len(selected)} 台 XY / 同步：{sync_count} 台（本页 {total_sync} 台）"
            f"/ 旋转 {self._xy_reference_global_angle_deg:.1f}° / 扇面 {self._xy_reference_z_angle_deg:.1f}°"
        )

    def apply_xy_reference_mode(self, mode: str):
        # 选中含设备包时，目标收缩为该包内真正具备完整 XY 两轴的那一个子设备（基底电机），
        # 其余成员跟随/不动，保证一套整体设备不会被广播出多个同步基准。
        mode = "sync" if str(mode or "").lower() == "sync" else "default"
        if mode == "sync":
            from .topview_sync_context import masked_targets
            compute_ids = self._page_xy_sync_target_ids()
            ids = masked_targets(compute_ids, self._selected_xy_fixture_ids())
            if not ids:
                ec.xy_reference_no_devices().show(self)
                return
            # ★ 2026-08-11 用户定案：基准 = 整页全选计算并锁定，子集选中只作
            #   遮罩开启。计算输入用整页 XY 目标集（含未选中的页内设备），结果
            #   只写入选中的设备 → 子集开启的基准与整页开启完全一致，不会因
            #   子集点集差异重算出个别错误基准。
            angles, cluster = self._compute_xy_reference_angles(compute_ids, return_cluster=True)
            # ★ EDGE-06: 跨页选择可含非当前页设备 → angles 不覆盖它们，跳过不写错误基准
            ids = [f for f in ids if f in angles]
            if not ids:
                QMessageBox.information(
                    self, "XY 基准模式",
                    "所选设备均不在当前页计算集内，请切换到对应页面后重试。")
                return
            _release_stale_sync_entries(self._xy_reference_by_fixture, ids)  # 替换
            for fid in ids:
                base_angle = float(angles.get(fid, 0.0))
                # ★ 2026-08-05 用户实测(权威): 域25 ch469 数控基底电机
                #   (Multi Directional XYZ 3 轴)。+90° 后方向生效但整台 180°
                #   镜像(其它往左它往右) → 再转 180°(总计 +270° → 144.59°,
                #   一对一, 仅该台)。门控: universe=25 且 start_channel=469
                #   且 def 含 motor_y, 其余设备/页零变化。共用共享规则函数
                #   (与拖拽刷新路径一致, 防路径间漂移)。
                base_angle = _sync_base_angle_override(
                    fid, self._xy_install_angle(fid, base_angle, compute_ids),
                    self.fixtures_by_id(), self.fixture_defs_by_id())
                # ★ 2D XY 复用三维同步代码（2026-08-10 用户指令：二维 XY 与
                #   三维 XY 同步效果一致）：三维扇子非 uniform（rad = base+θ，
                #   base = −安装）从没错 —— 2D XY 同样走非统一分支（引擎
                #   build_xy_reference_map 对无 motor_z 且非 x_only 设备强制
                #   uniform=False 兜底），base 已由 _xy_install_angle 取 −真实
                #   安装 → 世界 = θ。2D XY 不再强制 True（防 uniform 分支
                #   rad = base−θ → 世界 2·base−θ 发散）。
                uniform_flag = str(fid) in cluster and not self._fixture_is_2d_xy(str(fid))
                self._xy_reference_by_fixture[str(fid)] = {
                    "mode": "sync",
                    "base_angle_deg": base_angle,
                    # ★ 圆排多设备统一化（2026-08-04）：圆簇成员统一世界方向
                    #   （静止 = 全局角、推 X/Y 世界一致、扇面水平）。
                    "uniform_world_angle": uniform_flag,
                    "angle_deg": float(self._sync_angle_with_global(
                        base_angle, {"uniform_world_angle": uniform_flag})),
                    "z_angle_deg": float(self._xy_reference_z_angle_deg),
                    "page_id": self.current_page().get("id"),
                }
            # ★ 落叶头尖点对 X-only 一对一硬修（2026-08-05 用户实测）：尖点对
            #   （域25 ch464 / 域2 ch331）为 5ch 全能卡一维（仅 motor_x, 非 XY
            #   → 不在同步目标内）且 rotz=±180 反装交叉挂载（左包右挂/右包左挂）
            #   ——6a4673c 顶部 22 台 XYZ 摆正遗漏该对 → 推 X 方向与其它设备
            #   不符。整页叶形基准门控内显式加入同步：base=180°（反装交叉
            #   翻转, 推 X 旋转反向 → 与其它顶部同向）; angle=0（统一世界角）
            #   → 推 X 世界 ≈0°（引擎模型）与其它顶部一致。其余设备/页零变化
            #   （门控见 _apex_xonly_align_ids; 输出变换见 xy_reference
            #   x_only_align 自映射）。
            for fid in self._apex_xonly_align_ids(ids):
                self._xy_reference_by_fixture[str(fid)] = {
                    "mode": "sync",
                    "base_angle_deg": -90.0,
                    "uniform_world_angle": True,
                    "angle_deg": float(self._sync_angle_with_global(
                        -90.0, {"uniform_world_angle": True})),
                    "z_angle_deg": float(self._xy_reference_z_angle_deg),
                    "page_id": self.current_page().get("id"),
                    "x_only_align": True,
                }
        else:
            # 取消同步清除**全部**选中 XY 设备（含被收缩掉的副成员），
            # 防包内非基底成员残留 sync 态随全局角旋转（审查 MEDIUM-3）
            clear_ids = self._selected_xy_fixture_ids()
            for fid in clear_ids:
                self._xy_reference_by_fixture.pop(str(fid), None)
            # ★ 尖点 X-only（2026-08-05）：非 XY 不在 clear_ids 内, 取消同步
            #   时按当前页一并清除, 防残留 sync 态（页外条目不动）。
            cur_page_id = self.current_page().get("id")
            for fid, cfg in list(self._xy_reference_by_fixture.items()):
                if bool((cfg or {}).get("x_only_align", False)) \
                        and str((cfg or {}).get("page_id", "")) == str(cur_page_id):
                    self._xy_reference_by_fixture.pop(str(fid), None)
        self._update_xy_reference_label()
        self._emit_xy_reference_changed()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()

    def _selectable_member_ids(self, all_pages: bool = False) -> set:
        """逻辑/组合设备成员 id 集（存在于工程的真实设备）。

        成员被故意排除在 page['fixtures']/visible_fixture_ids 之外（只画代表点、不画成员、
        不被曲线拟合/跨页拖动覆盖坐标），但点击代表点需要能选中它们 → 选择过滤须把成员
        视为「可选」。仅用于放宽**选择**集，**绝不**放进拖动/写坐标集（那仍只认当前页可见）。
        """
        valid = set(self.fixtures_by_id().keys())
        pages = self._pages if all_pages else [self.current_page()]
        out = set()
        for page in pages:
            if not isinstance(page, dict):
                continue
            for ld in (page.get("logical_devices", []) or []):
                for m in (ld.get("member_fixture_ids", []) or []):
                    if str(m) in valid:
                        out.add(str(m))
        return out

    def _normalize_selection_ids(self, ids: List[str], visible: Optional[set] = None) -> List[str]:
        # ★ R131：`_selection_visible_override` 供"跨页选择保持"路径临时放宽可见集
        #   （几何收尾用；正常路径恒为 None，行为不变）。
        if visible is None:
            visible = getattr(self, "_selection_visible_override", None)
        vis = visible if visible is not None else (set(self.fixture_ids()) | self._selectable_member_ids())
        out = []
        seen = set()
        for fid in ids or []:
            sfid = str(fid)
            if sfid in vis and sfid not in seen:
                out.append(sfid)
                seen.add(sfid)
        return out

    def _normalize_selection_sequence_ids(self, ids: List[str], visible: Optional[set] = None) -> List[str]:
        # ★ 可见集默认含包/组合设备成员（2026-08-04 用户：包设备与普通设备同等
        #   能力——否则实时选择/间隔选择会把选中的包成员过滤掉，选择状态丢失）。
        vis = visible if visible is not None else (set(self.fixture_ids()) | self._selectable_member_ids())
        return [str(fid) for fid in (ids or []) if str(fid) in vis]

    def _unique_preserve_order(self, ids: List[str]) -> List[str]:
        out = []
        seen = set()
        for fid in ids or []:
            sfid = str(fid)
            if sfid and sfid not in seen:
                out.append(sfid)
                seen.add(sfid)
        return out

    def _snapshot_for_undo(self):
        """几何/页面操作前的撤销快照：先持久化当前 TopView 状态再触发 PM 快照。

        与 main_window._snapshot_for_undo 同构（各自收集各自面板状态），保证
        Ctrl+Z 能完整还原页面/坐标/图形。

        ★★ 2026-09-20 修复（与 patch_panel 同款问题）：原实现把
          `from ...core.project_manager import DOMAIN_TOPVIEW` 写在另一个函数末尾
          —— ① 相对层级越界（本模块是 `src.ui.*`，`..` 才到 `src`）→ ImportError；
          ② 使用点在本方法 → 即使路径写对也是 NameError。
          两者都会让 TopView 几何/页面操作的撤销快照失败。
        """
        from ..core.project_manager import DOMAIN_TOPVIEW
        if self._pm is not None and hasattr(self._pm, "_snapshot"):
            try:
                project = self._current_project()
                if hasattr(project, "topview_state"):
                    # ★ R137：写入判据 `_hydrated_for is project` 不成立时（面板状态
                    #   不是从该工程灌入的）回写无效，快照不会包含本次改动 → 跳过，
                    #   否则只是往栈里压一条「撤销了也没变化」的假条目。
                    if _persist_topview_state(self, project):
                        self._pm._snapshot(domain=DOMAIN_TOPVIEW)
            except Exception:
                pass

    def _finish_geometry_change(
        self,
        selected_order: Optional[List[str]] = None,
        refresh_ids: Optional[List[str]] = None,
    ):
        """几何改动收尾：同步基准刷新 + 状态持久化 + 画布重绘 + 选择保持。

        refresh_ids 用于「移动的是一批设备但同步基准只关心其中 XY 子集」的
        路径（拖拽/翻面/旋转后基准必须随新位置重算，否则十字线方向沿用旧位）。
        """
        selected_order = list(selected_order if selected_order is not None else self._ordered_selected_ids())
        refresh_source = list(refresh_ids if refresh_ids is not None else (selected_order or self.fixture_ids()))
        self._refresh_sync_reference_for_ids(refresh_source)
        project = self._current_project()
        try:
            if hasattr(project, "topview_state"):
                _persist_topview_state(self, project)
        except Exception:
            pass
        if hasattr(self, "_canvas"):
            self._canvas.update()
        try:
            self.layout_changed.emit()
        finally:
            # ★★ R131 修复（跨页选择被静默截断到当前页）：`_set_selection` 的可见集是
            #   **仅当前页**（`fixture_ids() | _selectable_member_ids()`），而跨页选择
            #   是 `set_external_fixture_selection` 用 `_all_pages_visible_ids()` 建的。
            #   几何收尾走 `_set_selection` → 跨页部分被过滤丢弃：用户在 Programmer/
            #   设备组里跨 sheet 选一批设备，回 TopView 做一次旋转/翻面/组间距，选择
            #   就只剩当前页那部分，后续 effect/设值只作用本页。
            #   修：这里临时把可见集放宽到**全部页**（与 `set_external_fixture_selection`
            #   同口径），再走同一个 `_set_selection`。
            self._selection_visible_override = self._all_pages_visible_ids()
            try:
                self._set_selection(selected_order, emit=False)
            finally:
                self._selection_visible_override = None
            self._update_live_selection_label(selected_order)
            self._update_xy_reference_label()
            try:
                if hasattr(project, "topview_state"):
                    _persist_topview_state(self, project)
            except Exception:
                pass

    def _set_selection(self, ids: List[str], emit: bool = True):
        # ★ R131：`_selection_visible_override` 供"跨页选择保持"路径临时放宽可见集
        #   （几何收尾走 `_set_selection` 时用；正常路径恒为 None，行为不变）。
        #   放在本函数内联（而非新增方法）—— TopViewPanel 是上帝类，方法数已顶格。
        prev_vis = getattr(self, "_selection_visible_override", None)
        ordered = self._normalize_selection_ids(ids)
        self._selection_order_ids = ordered
        self.selected_ids = set(ordered)
        self._selection_visible_override = prev_vis
        self._reset_box_selection_chain()
        if self.last_clicked_id and self.last_clicked_id not in self.selected_ids:
            self.last_clicked_id = ordered[-1] if ordered else ""
        if emit:
            self.emit_selection()

    def _reset_live_selection_context(self, ids: Optional[List[str]] = None,
                                      name: str = "TopView实时选择"):
        """手动选择重置 live-selection 上下文：外部选择不继承隐藏基础选区。

        与 _set_live_selection 同构但不发射选择信号（供 set_external_fixture_selection
        等同步路径复用，2026-08-08 W3 恢复）。
        """
        visible = set(self.fixture_ids()) | self._selectable_member_ids()
        ordered = self._normalize_selection_sequence_ids(
            ids if ids is not None else self._ordered_selected_ids(), visible)
        self._live_base_ids = list(ordered)
        self._live_selection_ids = list(ordered)
        self._live_selection_name = name or "TopView实时选择"

    def set_external_fixture_selection(self, ids: List[str], emit: bool = False):
        """外部(Programmer 表格/关联器/跑动编排)选中联动 TopView 画布选择。

        与 _set_selection 同构；另重置 live-selection 上下文（外部选择不继承
        隐藏基础选区）。调用方: main_window 关联器/跑动编排, programmer_panel
        表格选中 (2026-08-08 W3 恢复)。

        可见集用跨页全局集(与 refresh 剪枝一致): 外部选择(如 Clip 设备组)可
        跨 sheet, 按当前页过滤会把其它页设备全滤掉 → 选中 0 台(#22b 根因)。
        """
        ordered = self._normalize_selection_ids(ids, visible=self._all_pages_visible_ids())
        self._selection_order_ids = ordered
        self.selected_ids = set(ordered)
        self._reset_box_selection_chain()
        self._reset_live_selection_context(ordered)
        if self.last_clicked_id and self.last_clicked_id not in self.selected_ids:
            self.last_clicked_id = ordered[-1] if ordered else ""
        if emit:
            self.emit_selection()
        else:
            self._update_live_selection_label(ordered)
            self._update_xy_reference_label()
        if hasattr(self, "_canvas"):
            self._canvas.update()

    def _append_selection(self, ids: List[str], emit: bool = True):
        vis = set(self.fixture_ids()) | self._selectable_member_ids()
        changed = False
        for fid in ids or []:
            sfid = str(fid)
            if sfid in vis and sfid not in self.selected_ids:
                self._selection_order_ids.append(sfid)
                self.selected_ids.add(sfid)
                changed = True
        if changed:
            self._reset_box_selection_chain()
        if changed and emit:
            self.emit_selection()

    def _remove_selection(self, ids: List[str], emit: bool = True):
        remove = {str(x) for x in (ids or [])}
        if not remove:
            return
        if not (self.selected_ids & remove):
            return
        self._selection_order_ids = [fid for fid in self._selection_order_ids if fid not in remove]
        self.selected_ids.difference_update(remove)
        self._reset_box_selection_chain()
        if self.last_clicked_id and self.last_clicked_id not in self.selected_ids:
            self.last_clicked_id = self._selection_order_ids[-1] if self._selection_order_ids else ""
        if emit:
            self.emit_selection()

    # ══════════════════════════════════════════════════════════════════════
    #  配套设备自动追加（2026-08-05 用户需求）
    #  设备包与配套泵常分处两个 TopView 页（泵安装在设备下方，世界坐标 x/z
    #  一致）：页名 `<前缀>·设备包` ↔ `<前缀>·泵/散台` 构成配套页对；选中设备
    #  后右键 → 按世界坐标（容差内最近邻、每配套单元至多配一次）把对应配套
    #  设备追加进选择集。至冬7.0 实测：底弧线/上弧线/落叶头 120/72/73 全配，
    #  百变 253/301（其余 48 泵世界坐标落在上弧线气爆区，页内无配套设备）。
    # ══════════════════════════════════════════════════════════════════════

    def companion_pages_for_current(self) -> List[dict]:
        """当前页的配套页列表（同类前缀、反类型页）。

        页名形态（R128 细化后）：`<文件夹>·<类别>·<类型名>`；细化前的旧工程为
        `<文件夹>·<类别>`。两类都要支持 —— 判定改成"**含 `·<类别>` 段**且前缀相同"，
        而不是 `endswith`（★ R131 修复：R128 把类别段夹到中间后，`endswith` 全部漏判
        → 右键「自动追加选入配套设备」菜单项直接消失）。

        设备包页 → 泵/散台页；泵/散台页 → 设备包页。无配套页返回空 → 调用方不显示菜单项。
        """
        page = self.current_page()
        name = str(page.get("name", ""))
        prefix, want = _companion_match(name)
        if not prefix:
            return []
        cur_id = page.get("id")
        out = []
        for p in self._pages:
            if p.get("id") == cur_id:
                continue
            pname = str(p.get("name", ""))
            if not pname.startswith(prefix):
                continue
            if any(f"·{w}" in pname[len(prefix):] for w in want):
                out.append(p)
        return out

    def _logical_device_anchor_world(self, ld: dict) -> Optional[Tuple[float, float]]:
        """逻辑设备锚点世界坐标 (x, z)。

        优先：偏移≈0 的根成员（其世界坐标即包锚点）；兜底：最小偏移成员按
        画布偏移反算锚点（配灯圆化成员偏移失真，取最小偏移者最接近真实锚点）。
        """
        offs = ld.get("local_offsets") or {}
        fallback = None
        best_mag = 1e18
        for m in (ld.get("member_fixture_ids", []) or []):
            off = offs.get(str(m))
            if off is None:
                continue
            try:
                offx, offy = float(off[0]), float(off[1])
            except Exception:
                continue
            fi = self.fixtures_by_id().get(str(m))
            if fi is None:
                continue
            wx = _safe_float(getattr(fi, "world_x", 0.0), 0.0)
            wz = _safe_float(getattr(fi, "world_z", 0.0), 0.0)
            if abs(wx) <= 1.0 and abs(wz) <= 1.0:
                continue
            if abs(offx) < 1e-6 and abs(offy) < 1e-6:
                return (wx, wz)
            mag = abs(offx) + abs(offy)
            if mag < best_mag:
                best_mag = mag
                fallback = (wx, wz, offx, offy)
        if fallback is not None:
            try:
                from .import_patch_csv import DEFAULT_IMPORT_SCALE, Y_AXIS_SIGN
                wx, wz, offx, offy = fallback
                return (wx - offx / DEFAULT_IMPORT_SCALE,
                        wz - offy / (DEFAULT_IMPORT_SCALE * Y_AXIS_SIGN))
            except Exception:
                return None
        return None

    def _companion_page_units(self, page: dict) -> List[Tuple[Tuple[float, float], List[str]]]:
        """配套页单元表：[(锚点世界坐标, 追加设备id列表), ...]。

        逻辑设备单元追加其全部成员（「与在设备组里全选相同」）；
        散台单元追加自身。页内可见设备若已属于某逻辑设备则不重复计。
        """
        units: List[Tuple[Tuple[float, float], List[str]]] = []
        ld_members: set = set()
        for ld in (page.get("logical_devices", []) or []):
            members = [str(m) for m in (ld.get("member_fixture_ids", []) or [])]
            a = self._logical_device_anchor_world(ld)
            if a is not None and members:
                units.append((a, members))
                ld_members.update(members)
        for fid in (page.get("visible_fixture_ids", []) or []):
            sfid = str(fid)
            if sfid in ld_members:
                continue
            fi = self.fixtures_by_id().get(sfid)
            if fi is None:
                continue
            wx = _safe_float(getattr(fi, "world_x", 0.0), 0.0)
            wz = _safe_float(getattr(fi, "world_z", 0.0), 0.0)
            if abs(wx) > 1.0 or abs(wz) > 1.0:
                units.append(((wx, wz), [sfid]))
        return units

    def _current_selection_anchors(self) -> List[Tuple[Tuple[float, float], str, int]]:
        """当前页选中设备的锚点列表（去重，按徽标序）：[(锚点世界坐标, 单元key, 徽标号)]。

        选中成员 → 所属逻辑设备锚点（同 ld 多成员只取一个锚点）；
        选中散台 → 自身世界坐标。只处理当前页设备（跨 sheet 连选的别页
        设备不参与——配套匹配以「当前框选」为准）。
        ★ 2026-08-05 用户需求（配套编号继承）：顺序 = 画布徽标序，徽标号 = 源设备
        在**本页**的编号（1 起连续）——纯包源页按包级单元**视觉序**（_pkg_order_marks
        同源：位置排序）；散台/混合源页按**选择序**（_get_order_marks_for 同源：
        页桶内相对序）。追加的配套设备按此序/此号一一继承（泵 3 号 ↔ 包 3 号）。
        """
        page = self.current_page()
        ld_by_member: dict = {}
        for ld in (page.get("logical_devices", []) or []):
            for m in (ld.get("member_fixture_ids", []) or []):
                ld_by_member.setdefault(str(m), ld)
        visible = set(self.fixture_ids())
        page_ids = [str(f) for f in self._ordered_selected_ids()
                    if str(f) in ld_by_member or str(f) in visible]
        # ★ 纯包源页（选中全为包成员）：锚点按包级单元**视觉序**（_pkg_order_marks
        #   同源：位置排序）——乱序点选时徽标仍沿弧线连续，追加序 = 徽标序。
        if page_ids and all(str(f) in ld_by_member for f in page_ids):
            anchors = []
            for i, u in enumerate(self._selection_units_from_base(page_ids), start=1):
                if not u["fids"]:
                    continue
                ld = ld_by_member.get(str(u["fids"][0]))
                if ld is None:
                    continue
                a = self._logical_device_anchor_world(ld)
                if a is None:
                    continue
                anchors.append((a, "ld:" + str(ld.get("id", "")), i))
            return anchors
        # 散台源页/混合页：选择序（页桶内相对序，_get_order_marks_for 同源）；
        # 混合页（包+散台同选）保持原选择序遍历，各自附本编号流徽标号。
        ld_rank = {}
        for i, u in enumerate(self._selection_units_from_base(page_ids), start=1):
            if u["fids"]:
                ld = ld_by_member.get(str(u["fids"][0]))
                if ld is not None:
                    ld_rank.setdefault(str(ld.get("id", "")), i)
        fx_rank = {str(f): i + 1 for i, f in enumerate(page_ids)}
        anchors: List[Tuple[Tuple[float, float], str, int]] = []
        seen_units: set = set()
        for i, fid in enumerate(page_ids, start=1):
            sfid = str(fid)
            ld = ld_by_member.get(sfid)
            if ld is not None:
                key = "ld:" + str(ld.get("id", ""))
                if key in seen_units:
                    continue
                a = self._logical_device_anchor_world(ld)
                if a is None:
                    continue
                seen_units.add(key)
                anchors.append((a, key, ld_rank.get(str(ld.get("id", "")), i)))
                continue
            if sfid not in visible:
                continue
            fi = self.fixtures_by_id().get(sfid)
            if fi is None:
                continue
            wx = _safe_float(getattr(fi, "world_x", 0.0), 0.0)
            wz = _safe_float(getattr(fi, "world_z", 0.0), 0.0)
            if abs(wx) > 1.0 or abs(wz) > 1.0:
                anchors.append(((wx, wz), "fx:" + sfid, fx_rank.get(sfid, i)))
        return anchors

    def append_companion_devices(self, companion_page: dict) -> int:
        """把当前页选中设备在配套页中的对应设备追加进选择集。

        匹配口径：世界坐标容差 25（百变配泵安装偏移 20 世界单位实测）；
        最近邻贪心 + 每配套单元至多配一次（落叶头叶尖两台锚点重合不串配）。
        ★ 2026-08-05 用户需求（配套编号继承）：追加序 = 源设备徽标序，且每个追加
        配套设备记录其源设备的徽标号（_companion_inherit_marks）——画布徽标按
        继承号显示（泵 3 号 ↔ 包 3 号同编号同点位），坐标匹配对应关系即继承依据。
        返回本次实际追加的设备数（0 = 未找到配套设备）。
        """
        anchors = self._current_selection_anchors()
        units = self._companion_page_units(companion_page)
        if not anchors or not units:
            return 0
        tol = 25.0
        used = [False] * len(units)
        append_ids: List[str] = []
        inherit_marks: Dict[str, List[int]] = {}
        for (ax, az), _key, badge in anchors:
            best_idx = -1
            best_d = tol
            for i, (ua, _ids) in enumerate(units):
                if used[i]:
                    continue
                d = math.sqrt((ax - ua[0]) ** 2 + (az - ua[1]) ** 2)
                if d < best_d:
                    best_d = d
                    best_idx = i
            if best_idx >= 0:
                used[best_idx] = True
                ids = units[best_idx][1]
                append_ids.extend(ids)
                for m in ids:
                    inherit_marks.setdefault(str(m), [badge])
        if not append_ids:
            return 0
        valid = set(self._all_fixture_ids())
        added = 0
        for fid in append_ids:
            sfid = str(fid)
            if sfid in valid and sfid not in self.selected_ids:
                self._selection_order_ids.append(sfid)
                self.selected_ids.add(sfid)
                added += 1
        if added:
            self._companion_inherit_marks.update(inherit_marks)
            self._reset_box_selection_chain()
            self.emit_selection()
        return added

    def _toggle_selection(self, fid: str, emit: bool = True):
        sfid = str(fid)
        if sfid in self.selected_ids:
            self._remove_selection([sfid], emit=emit)
            return
        self._append_selection([sfid], emit=emit)

    def _clear_selection(self, emit: bool = True):
        if not self.selected_ids and not self._selection_order_ids:
            return
        self.selected_ids.clear()
        self._selection_order_ids = []
        self._companion_inherit_marks.clear()
        self._reset_box_selection_chain()
        self._live_base_ids = []
        self._live_selection_ids = []
        self._live_selection_name = "TopView实时选择"
        self.last_clicked_id = ""
        if emit:
            self.emit_selection()

    def _reset_box_selection_chain(self):
        self._box_chain_active = False
        self._box_chain_order_ids = []

    def _apply_box_selection(self, box_ids: List[str], emit: bool = True):
        ordered_box = self._normalize_selection_ids(box_ids)
        if not ordered_box:
            return
        restart = chain_complete = False
        if self._box_chain_active and self._box_chain_order_ids:
            chain_set = set(self._box_chain_order_ids)
            visible_scope = set(self.fixture_ids())
            scope = (set(self._group_fixture_ids(self._active_group_id)) & visible_scope) if self._active_group_id else visible_scope
            if not scope:
                scope = visible_scope
            chain_complete = bool(scope) and scope.issubset(chain_set)
            restart = (not chain_complete) and any(fid in chain_set for fid in ordered_box)
        if (not self._box_chain_active) or restart:
            self._box_chain_order_ids = list(ordered_box)
        elif chain_complete:
            _merge_box_chain(self._box_chain_order_ids, ordered_box, chain_set)
        else:
            chain_set = set(self._box_chain_order_ids)
            self._box_chain_order_ids.extend([fid for fid in ordered_box if fid not in chain_set])
        self._box_chain_active = True
        self._selection_order_ids = list(self._box_chain_order_ids)
        self.selected_ids = set(self._selection_order_ids)
        self._reset_live_selection_context(self._selection_order_ids)
        if self.last_clicked_id and self.last_clicked_id not in self.selected_ids:
            self.last_clicked_id = self._selection_order_ids[-1] if self._selection_order_ids else ""
        if emit:
            self.emit_selection()

    def _prune_selection_to_visible(self, emit: bool = False, scope: Optional[set] = None):
        # scope=None 时按当前页可见集剪（每页独立选择的默认行为）；
        # 传入跨页全局集时只剔除真正不存在的设备，保留跨 sheet 连选。
        # ★ 可见集默认含包/组合设备成员（2026-08-04 用户：切页/刷新不得丢弃包选择）。
        vis = scope if scope is not None else (set(self.fixture_ids()) | self._selectable_member_ids())
        ordered = [fid for fid in self._selection_order_ids if fid in vis]
        if ordered == self._selection_order_ids and set(ordered) == self.selected_ids:
            return
        self._selection_order_ids = ordered
        self.selected_ids = set(ordered)
        self._reset_box_selection_chain()
        if self.last_clicked_id and self.last_clicked_id not in self.selected_ids:
            self.last_clicked_id = ordered[-1] if ordered else ""
        if emit:
            self.emit_selection()
    def refresh(self):
        self._refresh_tabs(); self._refresh_groups()
        self._set_layout_spacing_ui_from_page()
        for fid in self.fixture_ids():
            self.fixture_pos(fid)
        # 用全局可见集剪枝，保留跨 sheet 连选；只剔除已删除的设备。
        self._prune_selection_to_visible(emit=True, scope=self._all_pages_visible_ids())
        if hasattr(self, "_canvas"):
            self._canvas._spatial_index_dirty = True
            self._canvas._static_cache = None
            self._canvas.update()
        self.layout_changed.emit()
    def manual_refresh(self):
        """用户手动刷新 TopView：立即同步 Fixture 设备组展板，不需要新建页面触发刷新。"""
        try:
            if self._pm is not None:
                self._project = getattr(self._pm, "project", None) or getattr(self._pm, "_project", None) or self._project
        except Exception:
            pass
        self.refresh()
    def _refresh_tabs(self):
        sig = (id(self._pages), len(self._pages),
               tuple(str(p.get("name", "Sheet")) for p in self._pages))
        if sig == getattr(self, "_tabs_sig", None):
            if self._pages:
                self._tabs.setCurrentIndex(max(0, min(self._active_page, len(self._pages)-1)))
            return
        self._tabs_sig = sig
        self._tabs.blockSignals(True); self._tabs.clear()
        for page in self._pages: self._tabs.addTab(QWidget(), page.get("name", "Sheet"))
        if self._pages: self._tabs.setCurrentIndex(max(0, min(self._active_page, len(self._pages)-1)))
        self._tabs.blockSignals(False)
    def _refresh_groups(self):
        old_gid = str(self._active_group_id or (self._group_combo.currentData() or ""))
        project = self._current_project()
        groups = list(getattr(project, "fixture_groups", []) or [])
        # ★ 性能(2026-08-05): 展板按钮重建指纹缓存——按钮内容只取决于 组内容
        #   (id/name/color/fixture_ids/base_order) + 当前页可见集 + 工程设备集
        #   (placed_count/过滤)。三者全同 → 重建结果逐字节相同 → 跳过 41 按钮
        #   重建与组合框重灌(拖动提交链每步实测省 ~80ms)。组合框只依赖 groups,
        #   组未变则其状态已是重建后的一致状态。
        visible_ids = tuple(self._visible_fixture_ids()) + tuple(sorted(self._selectable_member_ids()))
        groups_sig = tuple(
            (str(g.get("id", "")), str(g.get("name", "")), str(g.get("color", "")),
             tuple(str(x) for x in (g.get("fixture_ids", g.get("fixtures", [])) or [])),
             tuple(str(x) for x in (g.get("base_order_fixture_ids") or [])))
            for g in groups)
        flist = getattr(project, "fixtures", []) or []
        sig = (groups_sig, visible_ids, id(flist), len(flist))
        if sig == getattr(self, "_group_board_sig", None):
            return
        self._group_board_sig = sig
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        self._group_combo.addItem("请选择 Fixture 设备组（默认不显示）", "")
        found_index = 0
        for g in groups:
            gid = str(g.get("id", ""))
            self._group_combo.addItem(str(g.get("name", gid or "组")), gid)
            if gid and gid == old_gid:
                found_index = self._group_combo.count() - 1
        self._group_combo.setCurrentIndex(found_index)
        self._active_group_id = str(self._group_combo.currentData() or "")
        self._group_combo.blockSignals(False)

        # 重建 TopView 内置设备组展板。该展板只负责选择/拖拽，不会让设备自动显示。
        if hasattr(self, "_group_grid"):
            while self._group_grid.count():
                item = self._group_grid.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            visible = set(visible_ids)
            row = 0
            for g in groups:
                gid = str(g.get("id", ""))
                ids = self._group_fixture_ids(gid)
                placed_count = len([fid for fid in ids if fid in visible])
                btn = TopViewGroupButton(self, g, ids, placed_count)
                self._group_grid.addWidget(btn, row, 0)
                row += 1
            if row <= 0:
                empty = EmptyState("暂无 Fixture 设备组",
                                   "在 Fixture 面板创建设备组后会同步到这里。",
                                   icon_key="components")
                empty.setMinimumHeight(96)
                self._group_grid.addWidget(empty, 0, 0)
            self._sync_group_board_checked()

    def _sync_group_board_checked(self):
        """把展板行的选中外观与 panel._active_group_id 同步（纯视觉）。"""
        lay = getattr(self, "_group_grid", None)
        if lay is None:
            return
        active = set(_group_activation.board_groups(self))
        i = 0
        while True:
            item = lay.itemAt(i)
            if item is None:
                break
            w = item.widget()
            if isinstance(w, TopViewGroupButton):
                want = bool(w.group_id) and str(w.group_id) in active
                if w.isChecked() != want:
                    w.setChecked(want)
                else:
                    w._apply_row_state(want)
            i += 1
    def _on_group_changed(self, _idx: int = -1):
        # 兼容旧下拉入口：设备组切换本身也要形成一次实时选择。
        # 只选中当前 Sheet 已经放入的该组设备，不自动把未放入设备显示到画板。
        self._active_group_id = str(self._group_combo.currentData() or "")
        if self._active_group_id:
            visible_ids = set(self.fixture_ids()) | self._selectable_member_ids()
            ids = [fid for fid in self._group_fixture_ids(self._active_group_id) if fid in visible_ids]
            self._set_selection(ids, emit=False)
            self._live_base_ids = ids
            self._live_selection_ids = ids
            self._live_selection_name = "TopView实时选择-设备组"
        else:
            self._clear_selection(emit=False)
            self._live_base_ids = []
            self._live_selection_ids = []
            self._live_selection_name = "TopView实时选择"
        self._board_group_ids = [self._active_group_id] if self._active_group_id else []
        self._board_selection_ids = self._ordered_selected_ids()
        self.emit_selection()
        self._sync_group_board_checked()
        self._canvas.update()
    def select_group_from_board(self, gid: str):
        """Use the same button activation and Ctrl-group contract as Programmer."""
        _group_activation.activate_board(self, gid)

    def add_fixture_group_to_canvas(self, fixture_ids: List[str], pos: QPointF, shape: Optional[dict] = None, group_id: str = ""):
        """把设备组放入当前画板：加入 visible_fixture_ids，并在落点附近或目标图形上排布。"""
        all_ids = set(self._all_fixture_ids())
        ids = []
        seen = set()
        for fid in fixture_ids:
            fid = str(fid)
            if fid in all_ids and fid not in seen:
                ids.append(fid); seen.add(fid)
        if not ids: return []
        _undo_snapshot_action(self, "add_group_to_canvas")   # ★ R137：写 visible/坐标前压快照
        page = self.current_page()
        visible = page.setdefault("visible_fixture_ids", [])
        for fid in ids:
            if fid not in visible:
                visible.append(fid)
        if group_id:
            placed_groups = page.setdefault("placed_group_ids", [])
            if str(group_id) not in placed_groups:
                placed_groups.append(str(group_id))
        if shape:
            pts = _shape_points(shape, len(ids))
        elif getattr(self, "_use_import_layout", False):
            # ★ 拖入按导入排布（2026-08-03 用户）：开启后按导入工程时该组设备的
            #   位置摆放（fixture.pos_x/pos_y 保留导入画布坐标），形心对齐落点。
            raw: List[Tuple[float, float]] = []
            for fid in ids:
                fi = self.fixtures_by_id().get(str(fid))
                if fi is None:
                    continue
                ix = _safe_float(getattr(fi, "pos_x", 0.0), 0.0)
                iy = _safe_float(getattr(fi, "pos_y", 0.0), 0.0)
                if abs(ix) < 1e-6 and abs(iy) < 1e-6:
                    continue  # 未导入定位（手动创建）→ 不参与
                raw.append((ix, iy))
            if raw and len(raw) == len(ids):
                cx = sum(p[0] for p in raw) / len(raw)
                cy = sum(p[1] for p in raw) / len(raw)
                pts = [(x - cx + float(pos.x()), y - cy + float(pos.y())) for x, y in raw]
            else:
                spacing = self._quick_spacing.value() if hasattr(self, "_quick_spacing") else 54.0
                pts = self._line_points(len(ids), pos.x(), pos.y(), spacing)
        else:
            spacing = self._quick_spacing.value() if hasattr(self, "_quick_spacing") else 54.0
            pts = self._line_points(len(ids), pos.x(), pos.y(), spacing)
        for fid, (x, y) in zip(ids, pts):
            self.set_fixture_pos(fid, x, y)
        self._refresh_sync_reference_for_ids(ids)
        self._set_selection(ids, emit=False)
        self.emit_selection()
        self.refresh()
        return ids

    def remove_selected_from_canvas(self):
        """从当前 TopView 画板移除选中设备；只影响画板显示，不删除 Fixture 实例。"""
        if not self.selected_ids: return
        self._snapshot_for_undo()   # ★ R137：改页数据（visible_fixture_ids），原来不压快照
        page = self.current_page()
        remove = set(self.selected_ids)
        page["visible_fixture_ids"] = [fid for fid in page.get("visible_fixture_ids", []) if str(fid) not in remove]
        # 坐标保留在 page['fixtures'] 中，方便后续重新拖入同设备时沿用/编辑。
        self._clear_selection(emit=False)
        self.emit_selection()
        self.refresh()

    def _clear_canvas_transients(self):
        if hasattr(self, "_canvas"):
            # ★ R194/NB-06：清瞬态前补发 defer 的拖拽提交（切页/refresh 路径
            #   原来直接清 _drag_shape_id，会静默丢掉整段拖拽的 layout 通知）
            if getattr(self._canvas, "_drag_shape_id", "") or getattr(
                    self._canvas, "_drag_layout_dirty", False):
                self._canvas._drag_layout_dirty = False
                self.layout_changed.emit()
            self._canvas._drag_start = None
            self._canvas._drag_pos = None
            self._canvas._drawing_polyline = []
            self._canvas._hover_shape_id = ""
            self._canvas._active_shape_id = ""
            self._canvas._selected_shape_id = ""
            self._canvas._selected_shape_ids.clear()
            self._canvas._drag_shape_id = ""
            self._canvas._drag_fixture_ids = []
            self._canvas._drag_visible_set = None
            self._canvas._drag_fixture_offsets = {}
            self._canvas._drag_logical_id = ""
            self._canvas._drag_ld_offset = (0.0, 0.0)
            self._canvas._drag_handle = None

    def _on_tab_changed(self, idx: int):
        if idx >= 0:
            self._active_page = idx
            self._clear_canvas_transients()
            # 按住连选键(Ctrl)切页时跨 sheet 累积选择，不剪到当前页；
            # 否则保持「每页独立选择」默认，剪到当前页可见集。
            cross_sheet = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
            if not cross_sheet:
                self._prune_selection_to_visible(emit=False)
            # ★ 2026-08-04 用户: 切页后旧页 live_base 不得泄漏到新页的奇偶/间隔
            #   基础序(否则在新页按旧页 1260 台成员隔一会选中"看起来随机"的设备)。
            self._live_base_ids = []
            self._live_selection_ids = []
            self._live_selection_name = "TopView实时选择"
            self._set_layout_spacing_ui_from_page()
            # 切换 TopView 页面后，当前可见设备与选择范围都可能变化；主动补发一次，
            # 让 Programmer、Matrix 等面板立即刷新到当前 Sheet 的实时选择。
            self.emit_selection()
            # 每页可见设备可能不同，必须重建空间索引
            self._canvas._spatial_index_dirty = True
            # ★ 切页自动适配视口（2026-08-03 用户反馈：世界坐标 ±5000 在默认
            #   视口外看不到设备/拟合曲线；导入后当前页可能是空 Sheet1）。
            self._canvas._fit_view_to_content()
            self._canvas.update()
            self._canvas._static_cache = None
            self._canvas.update()
            self.layout_changed.emit()
    def add_page(self):
        self._snapshot_for_undo()
        n = len(self._pages) + 1
        # ★ R137：名字必须**唯一**（原来纯按页数命名：删掉 Sheet1 再新建 →
        #   两个 Sheet2 → import_layout_pages/companion 按名匹配时只命中第一个）。
        name = _unique_page_name(f"Sheet{n}", self._pages)
        self._pages.append({"id":str(uuid.uuid4()),"name":name,"fixtures":{},"cad_shapes":[],"pattern_matrices":[]}); self._active_page = len(self._pages)-1; self.refresh()
    def copy_page(self):
        """复制当前页（含设备坐标与图形，图形换新 id）插入到下一页并切换过去。"""
        if not self._pages:
            self.current_page()
        self._snapshot_for_undo()
        # ★ R137：`X 副本` 可能已存在（复制两次同名页）→ 唯一化，避免重名页并内容。
        cloned = _clone_page(self.current_page())
        cloned["name"] = _unique_page_name(cloned.get("name", "Sheet"), self._pages)
        self._pages.insert(self._active_page + 1, cloned)
        self._active_page += 1
        self.refresh()
    def rename_page(self):
        page = self.current_page(); name, ok = QInputDialog.getText(self, "重命名 TopView 页面", "页面名称:", text=page.get("name", "Sheet"))
        if ok and name.strip():
            self._snapshot_for_undo()
            # ★ R137：同名自动消歧（`Sheet2` → `Sheet2 (2)`）——重名页会让
            #   import_layout_pages 按名索引只命中第一个，导入内容并进一页、其余页不可达。
            page["name"] = _unique_page_name(name, [p for p in self._pages if p is not page])
            self.refresh()
    def delete_page(self):
        if len(self._pages) <= 1: ec.topview_minimum_pages().show(self); return
        self._snapshot_for_undo()
        self._pages.pop(self._active_page); self._active_page = max(0, self._active_page-1); self.refresh()
    def add_shape(self, shape: dict):
        # ★ R137：新增图形（直线/圆/椭圆/曲线/矩阵）直写页数据，原来不压快照
        _undo_snapshot_action(self, "add_shape:" + str(shape.get("id", "")))
        self.current_page().setdefault("cad_shapes", []).append(shape)
        if hasattr(self, "_canvas"):
            self._canvas._static_cache = None
            self._canvas.update()
        self.layout_changed.emit()

    def import_layout_pages(self, page_specs: list) -> int:
        """从导入配接表批量创建/更新 TopView 页面（每类型一个 Sheet）。

        page_specs: [{"name": str,
                      "fixtures": {fixture_id: (x, y)},   # 画布坐标
                      "shapes": [cad_shape_dict, ...],     # 按簇拟合的圆/弧/线
                      "logical_devices": [ld_dict, ...]}]  # 可选：逻辑/组合设备（显示成一个点）
        幂等：已存在同名页面则复用并合并（覆盖坐标、追加未重复图形、并入可见列表）。
        不写入任何朝向/xy_reference——旋转不参与，仅摆位 + 图形。
        返回新建页面数。
        ★ R137：by_name 由 `_import_page_lookup` 建（同名页只认第一个）；同名 spec 由
        `_unique_spec_names` 改名成独立页——重名页各自独立，不再把两份导入内容并进一页。
        """
        by_name = _import_page_lookup(self._pages)
        created = 0
        # ⚠ 清理历史版本误写入的 Python set 键（不可 JSON 序列化 → Ctrl+S 保存
        #   崩溃——审查发现）。放在循环外：即使本次 page_specs 为空（幂等/无
        #   选择组导入路径）也清理全部页面。
        for _p in self._pages:
            if "_visible_fixture_set" in _p:
                _p.pop("_visible_fixture_set", None)
        for spec in _unique_spec_names(page_specs):
            name = str(spec.get("name", "") or "Sheet")
            page = by_name.get(name)
            if page is None:
                page = {"id": str(uuid.uuid4()), "name": name,
                        "fixtures": {}, "cad_shapes": [], "pattern_matrices": [],
                        "visible_fixture_ids": [], "logical_devices": []}
                self._pages.append(page)
                by_name[name] = page
                created += 1
            fixtures = page.setdefault("fixtures", {})
            visible = page.setdefault("visible_fixture_ids", [])
            # ★ 性能：visible 是保序列表，成员判定并行维护 set（页内 O(N²) → O(N)）。
            #   ⚠ set 只是局部临时变量，绝不写回 page dict——页面 dict 会被
            #   Project.to_dict()→json.dump 序列化，set 会让 Ctrl+S 保存崩溃（审查发现）。
            visible_set = set(visible)
            for fid, (x, y) in (spec.get("fixtures", {}) or {}).items():
                fid = str(fid)
                # 复用同名页(再次导入)时不覆盖已放置设备的坐标——保留用户拖动/整体缩放的成果；
                # 仅新设备(尚未在本页)才写入导入坐标。
                if fid not in fixtures:
                    fixtures[fid] = {"x": float(x), "y": float(y)}
                if fid not in visible_set:
                    visible_set.add(fid)
                    visible.append(fid)
            # ★ 性能：导入直写坐标 → 位置版本+1，canvas 鼠标命中桶格索引失效
            self._pos_version = getattr(self, "_pos_version", 0) + 1
            cad = page.setdefault("cad_shapes", [])
            existing_ids = {str(s.get("id", "")) for s in cad}
            for shape in (spec.get("shapes", []) or []):
                if str(shape.get("id", "")) not in existing_ids:
                    cad.append(shape)
            # 逻辑/组合设备：显示成一个代表点；成员真实坐标不进 fixtures（仍在各自实例）。
            # 再次导入时，先剔除「成员集与新逻辑设备重叠」的旧条目，避免同一单元叠两个代表点。
            lds = page.setdefault("logical_devices", [])
            incoming = list(spec.get("logical_devices", []) or [])
            if incoming:
                new_members = {str(m) for d in incoming
                               for m in (d.get("member_fixture_ids", []) or [])}
                lds[:] = [d for d in lds
                          if not ({str(m) for m in (d.get("member_fixture_ids", []) or [])} & new_members)]
            ld_ids = {str(d.get("id", "")) for d in lds}
            for ld in incoming:
                if str(ld.get("id", "")) not in ld_ids:
                    lds.append(ld)
        self.refresh()
        # ★ 导入后视口自动适配页面内容（2026-08-03 用户反馈：世界坐标 ±5000 在
        #   默认 scale=1 视口外，设备与拟合曲线都看不到）。
        if hasattr(self, "_canvas"):
            self._canvas._fit_view_to_content()
            self._canvas.update()
        self.layout_changed.emit()
        return created

    # ══════════════════════════════════════════════════════════════════════
    #  整组平移 / 旋转（画布内 gizmo 驱动；作用对象=当前选中设备，绕选区形心）
    #  写回坐标存储，与缩放同源。整页变换=先全选再拖 gizmo。
    # ══════════════════════════════════════════════════════════════════════
    def transform_scope(self) -> str:
        """平移/旋转作用域恒为 'selection'（gizmo 只作用当前选中设备）。"""
        return "selection"

    def _xform_cache_key(self) -> tuple:
        """变换目标/质心缓存键：目标只取决于 (页面, 选择对象, 逻辑设备列表,
        工程设备列表)。选择/页面/设备增删都会替换对象或改变长度 → 键变 →
        缓存失效；拖动会话内都不变 → 命中 O(1)（原每 paint 重扫 O(N)）。"""
        page = self.current_page()
        lds = page.get("logical_devices")
        if not lds:
            lds = _EMPTY_LDS
        flist = getattr(self._current_project(), "fixtures", None)
        if not flist:
            flist = _EMPTY_LDS
        return (id(page),
                id(getattr(self, "_selection_order_ids", None)),
                id(getattr(self, "selected_ids", None)),
                id(lds), len(lds), len(flist))

    def _xform_targets(self, scope: str = "selection") -> Tuple[List[str], List[dict], List[dict]]:
        """收集作用对象：返回 (设备fid列表, 图形dict列表, 逻辑设备dict列表)。

        - 设备 fid：当前选中**且在本页可见**的真实设备（守「只写当前页可见」——写 page
          ['fixtures'] 画布坐标只认可见集）。
        - 逻辑设备：其成员**全部落在选区内**即纳入（成员本身不在可见集，故用完整选区集判断，
          不能用可见过滤后的子集，否则合并后的设备永远选不进变换）。变换写 ld 代表点 +
          成员真实 pos_x/pos_y（不属于 page['fixtures']，不受可见守卫约束）。
        ★ 性能：结果缓存（目标只取决于选择/页面/设备列表，与位置无关）——拖动
          会话内 translate/rotate/gizmo_center 每步共享同一目标集，不再重复 O(N)
          扫描（探针实测每 paint 重扫 11-14ms）。
        """
        key = self._xform_cache_key()
        cached = getattr(self, "_xform_targets_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        vis = set(self.fixture_ids())
        all_lds = self.current_page_logical_devices()
        sel_full = set(self._ordered_selected_ids())
        sel = [fid for fid in self._ordered_selected_ids() if fid in vis]
        lds = []
        member_ids: set = set()
        for ld in all_lds:
            members = {str(m) for m in (ld.get("member_fixture_ids", []) or [])}
            if members and members <= sel_full:
                lds.append(ld)
                member_ids |= members
        # 直接可见选中的真实设备里，剔除已被逻辑设备接管的成员（避免重复变换）
        sel = [fid for fid in sel if fid not in member_ids]
        result = (sel, [], lds)
        self._xform_targets_cache = (key, result)
        return result

    def _xform_centroid(self, fids: List[str], shapes: List[dict],
                        lds: List[dict]) -> Optional[Tuple[float, float]]:
        xs: List[float] = []
        ys: List[float] = []
        for fid in fids:
            x, y = self.fixture_pos(fid)
            xs.append(float(x)); ys.append(float(y))
        for ld in lds:
            try:
                xs.append(float(ld.get("pos_x", 0.0))); ys.append(float(ld.get("pos_y", 0.0)))
            except Exception:
                continue
        for shape in shapes:
            try:
                scx, scy = _shape_center(shape)
                xs.append(float(scx)); ys.append(float(scy))
            except Exception:
                continue
        if not xs:
            return None
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def _xform_centroid_running_update(self, dx: float, dy: float, angle_deg: float = 0.0,
                                       pivot: Optional[Tuple[float, float]] = None):
        """平移/旋转直写坐标后增量更新质心缓存（质心是线性统计量，变换可直接施加
        于质心）。仅当缓存与 (目标键, 变换前位置版本) 匹配时 O(1) 更新；缓存缺失
        或已被其他路径（拖设备/拖包/导入）作废 → 留给 gizmo_center 按需重算 O(N)。
        浮点差异 ≤1e-9 世界单位（亚像素），绘制无差别。"""
        key = self._xform_cache_key()
        new_pv = getattr(self, "_pos_version", 0)
        c = getattr(self, "_xform_centroid_cache", None)
        if c is None or c[0] != (key, new_pv - 1) or c[1] is None:
            return
        ox, oy = c[1]
        if pivot is not None:
            nx, ny = _rotate_point_xy(ox, oy, float(pivot[0]), float(pivot[1]), angle_deg)
        else:
            nx, ny = ox + dx, oy + dy
        self._xform_centroid_cache = ((key, new_pv), (nx, ny))

    def _canvas_update_dirty(self, min_x: float, min_y: float, max_x: float, max_y: float,
                             n_tgt: int):
        """拖动会话 defer 路径：脏区 = 目标 旧+新 bbox + gizmo/LOD 外延 →
        update(rect)；空目标或近全屏退化为全量 update()。"""
        canvas = getattr(self, "_canvas", None)
        if canvas is None or n_tgt == 0:
            return
        r = canvas._screen_rect_for_world(min_x, min_y, max_x, max_y)
        full = canvas.rect()
        if r.width() * r.height() < full.width() * full.height() * 0.9:
            canvas.update(r)
        else:
            canvas.update()

    def _proj_fixture_map(self) -> Dict[str, object]:
        return self.fixtures_by_id()

    def translate_current(self, dx: float, dy: float, defer_ui: bool = False):
        """按作用域平移：设备坐标 + (整页时)图形 + 逻辑设备代表点/成员真实坐标。

        defer_ui=True（gizmo 拖动会话内）：只重绘画布，refresh()/layout_changed
        合并到 mouseRelease 一次提交——拖动不再每 move 全量重建 41 组按钮 +
        级联 save_state/xy/matrix（11382 台 O(N)×N 次/会话）。
        ★ 增量重绘：defer 路径按 目标 旧+新 位置 bbox update(rect)（含 gizmo/
          LOD 外延），不再每步全画布重绘。
        """
        try:
            dx = float(dx); dy = float(dy)
        except Exception:
            return
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return
        _undo_snapshot_xform(self, defer_ui); scope = self.transform_scope()
        fids, shapes, lds = self._xform_targets(scope)
        if not fids and not shapes and not lds:
            return
        page = self.current_page()
        fpos = page.setdefault("fixtures", {})
        vis = set(self.fixture_ids())
        # 拖动脏区：目标 旧+新 位置 bbox（世界坐标）——min/max 必须同时覆盖
        # 旧与新位置（负方向位移时新 < 旧，只取一侧会得到非法矩形 → 退化为全屏）
        min_x = min_y = math.inf
        max_x = max_y = -math.inf
        n_tgt = 0
        for fid in fids:
            if fid not in vis:
                continue
            x, y = self.fixture_pos(fid)
            nx, ny = float(x) + dx, float(y) + dy
            fpos[fid] = {"x": nx, "y": ny}
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if nx < min_x:
                min_x = nx
            if ny < min_y:
                min_y = ny
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
            if nx > max_x:
                max_x = nx
            if ny > max_y:
                max_y = ny
            n_tgt += 1
        for shape in shapes:
            _translate_shape(shape, dx, dy)
        fix_by_id = self._proj_fixture_map()
        for ld in lds:
            ld_ok = False
            try:
                nx = float(ld.get("pos_x", 0.0)) + dx
                ny = float(ld.get("pos_y", 0.0)) + dy
                ld["pos_x"], ld["pos_y"] = nx, ny
                ld_ok = True
            except Exception:
                pass
            if ld_ok:
                if nx < min_x:
                    min_x = nx
                if ny < min_y:
                    min_y = ny
                if nx > max_x:
                    max_x = nx
                if ny > max_y:
                    max_y = ny
                n_tgt += 1
            # local_offsets 是相对量，平移不改；成员真实坐标整体平移（与代表点同步）
            for fid in ld.get("member_fixture_ids", []) or []:
                fi = fix_by_id.get(str(fid))
                if fi is None:
                    continue
                try:
                    fi.pos_x = float(getattr(fi, "pos_x", 0.0)) + dx
                    fi.pos_y = float(getattr(fi, "pos_y", 0.0)) + dy
                except Exception:
                    continue
        # ★ 正确性(审查A)：gizmo 直写坐标同样递增位置版本——canvas 鼠标命中
        #   桶格索引据此失效（否则变换后悬停持续命中旧格）。
        self._pos_version = getattr(self, "_pos_version", 0) + 1
        # ★ 质心缓存增量更新（质心平移 = 平移到质心）——拖动会话内 gizmo 中心 O(1)
        self._xform_centroid_running_update(dx, dy)
        if defer_ui:
            self._canvas_update_dirty(min_x, min_y, max_x, max_y, n_tgt)
            return
        self.refresh()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()

    def rotate_current(self, angle_deg: float, center: Optional[Tuple[float, float]] = None,
                       defer_ui: bool = False):
        """按作用域绕形心旋转：设备坐标 + (整页时)图形 + 逻辑设备代表点/成员真实坐标/偏移。

        center 给定时以该点为支点（拖拽旋转用，避免每帧形心漂移）；否则取当前作用对象形心。
        ★ 增量重绘：defer 路径按 目标 旧+新 位置 bbox update(rect)（含 gizmo/
          LOD 外延），不再每步全画布重绘。
        """
        try:
            angle_deg = float(angle_deg)
        except Exception:
            return
        if abs(angle_deg) < 1e-6:
            return
        _undo_snapshot_xform(self, defer_ui); scope = self.transform_scope()
        fids, shapes, lds = self._xform_targets(scope)
        if not fids and not shapes and not lds:
            return
        if center is None:
            center = self._xform_centroid(fids, shapes, lds)
        if center is None:
            return
        cx, cy = float(center[0]), float(center[1])
        page = self.current_page()
        fpos = page.setdefault("fixtures", {})
        vis = set(self.fixture_ids())
        # 拖动脏区：目标 旧+新 位置 bbox（世界坐标）——min/max 同时覆盖新旧
        min_x = min_y = math.inf
        max_x = max_y = -math.inf
        n_tgt = 0
        for fid in fids:
            if fid not in vis:
                continue
            x, y = self.fixture_pos(fid)
            nx, ny = _rotate_point_xy(float(x), float(y), cx, cy, angle_deg)
            fpos[fid] = {"x": nx, "y": ny}
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if nx < min_x:
                min_x = nx
            if ny < min_y:
                min_y = ny
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
            if nx > max_x:
                max_x = nx
            if ny > max_y:
                max_y = ny
            n_tgt += 1
        for shape in shapes:
            _rotate_shape_about(shape, cx, cy, angle_deg)
        fix_by_id = self._proj_fixture_map()
        for ld in lds:
            ld_ok = False
            try:
                nx, ny = _rotate_point_xy(
                    float(ld.get("pos_x", 0.0)), float(ld.get("pos_y", 0.0)), cx, cy, angle_deg)
                ld["pos_x"], ld["pos_y"] = nx, ny
                ld_ok = True
            except Exception:
                pass
            if ld_ok:
                if nx < min_x:
                    min_x = nx
                if ny < min_y:
                    min_y = ny
                if nx > max_x:
                    max_x = nx
                if ny > max_y:
                    max_y = ny
                n_tgt += 1
            # local_offsets 是相对向量：仅旋转方向，不平移
            offs = ld.get("local_offsets", {}) or {}
            for k in list(offs.keys()):
                try:
                    ox, oy = float(offs[k][0]), float(offs[k][1])
                    rx, ry = _rotate_point_xy(ox, oy, 0.0, 0.0, angle_deg)
                    offs[k] = [rx, ry]
                except Exception:
                    continue
            for fid in ld.get("member_fixture_ids", []) or []:
                fi = fix_by_id.get(str(fid))
                if fi is None:
                    continue
                try:
                    fi.pos_x, fi.pos_y = _rotate_point_xy(
                        float(getattr(fi, "pos_x", 0.0)), float(getattr(fi, "pos_y", 0.0)), cx, cy, angle_deg)
                except Exception:
                    continue
        # ★ 正确性(审查A)：gizmo 直写坐标同样递增位置版本（悬停命中索引失效）
        self._pos_version = getattr(self, "_pos_version", 0) + 1
        # ★ 质心缓存增量更新（旋转线性作用于质心，支点 = center）
        self._xform_centroid_running_update(0.0, 0.0, angle_deg, (cx, cy))
        if defer_ui:
            self._canvas_update_dirty(min_x, min_y, max_x, max_y, n_tgt)
            return
        self.refresh()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()

    def scale_current(self, factor: float, anchor: Tuple[float, float], defer_ui: bool = False):
        """按作用域以 anchor 为固定支点等比缩放：设备 + 图形 + 逻辑设备代表点/成员。

        gizmo 角落缩放手柄调用（2026-08-08 W3）：factor 相对按下时包围盒计算，
        anchor = 抓取角对角。坐标级缩放（写回 page 存储与成员真实坐标），与
        scale_current_page（整页）正交；defer_ui 语义同 translate/rotate。
        """
        try:
            factor = float(factor)
        except Exception:
            return
        if abs(factor - 1.0) < 1e-6 or factor <= 0:
            return
        _undo_snapshot_xform(self, defer_ui); scope = self.transform_scope()
        fids, shapes, lds = self._xform_targets(scope)
        if not fids and not shapes and not lds:
            return
        ax, ay = float(anchor[0]), float(anchor[1])
        page = self.current_page()
        fpos = page.setdefault("fixtures", {})
        vis = set(self.fixture_ids())

        def sx(v: float) -> float:
            return ax + (float(v) - ax) * factor

        def sy(v: float) -> float:
            return ay + (float(v) - ay) * factor

        min_x = min_y = math.inf
        max_x = max_y = -math.inf
        n_tgt = 0
        for fid in fids:
            if fid not in vis:
                continue
            x, y = self.fixture_pos(fid)
            nx, ny = sx(x), sy(y)
            fpos[fid] = {"x": nx, "y": ny}
            if x < min_x:
                min_x = x
            if y < min_y:
                min_y = y
            if nx < min_x:
                min_x = nx
            if ny < min_y:
                min_y = ny
            if x > max_x:
                max_x = x
            if y > max_y:
                max_y = y
            if nx > max_x:
                max_x = nx
            if ny > max_y:
                max_y = ny
            n_tgt += 1
        for shape in shapes:
            self._scale_shape_geometry(shape, ax, ay, factor)
        fix_by_id = self._proj_fixture_map()
        for ld in lds:
            ld_ok = False
            try:
                nx, ny = sx(ld.get("pos_x", 0.0)), sy(ld.get("pos_y", 0.0))
                ld["pos_x"], ld["pos_y"] = nx, ny
                ld_ok = True
            except Exception:
                pass
            if ld_ok:
                if nx < min_x:
                    min_x = nx
                if ny < min_y:
                    min_y = ny
                if nx > max_x:
                    max_x = nx
                if ny > max_y:
                    max_y = ny
                n_tgt += 1
            offs = ld.get("local_offsets", {}) or {}
            for k in list(offs.keys()):
                try:
                    offs[k] = [float(offs[k][0]) * factor, float(offs[k][1]) * factor]
                except Exception:
                    continue
            for fid in ld.get("member_fixture_ids", []) or []:
                fi = fix_by_id.get(str(fid))
                if fi is None:
                    continue
                try:
                    fi.pos_x = sx(float(getattr(fi, "pos_x", 0.0)))
                    fi.pos_y = sy(float(getattr(fi, "pos_y", 0.0)))
                except Exception:
                    continue
        # ★ 正确性(审查A)：gizmo 直写坐标同样递增位置版本（悬停/包围盒缓存失效）
        self._pos_version = getattr(self, "_pos_version", 0) + 1
        # 质心缓存不增量更新（缩放非平移/旋转线性式）——位置版本前进自动失效重算
        if defer_ui:
            self._canvas_update_dirty(min_x, min_y, max_x, max_y, n_tgt)
            return
        self.refresh()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()

    def gizmo_center(self) -> Optional[Tuple[float, float]]:
        """坐标系箭头叠加层锚点：当前作用对象形心（供画布绘制）。

        ★ 性能：质心缓存（键 = 目标键 + 位置版本）。平移/旋转直写坐标时增量
          更新（质心是线性统计量）；其他路径（拖设备/拖包/导入）位置版本前进 →
          键不匹配 → 重新 O(N) 计算，行为一致。
        """
        scope = self.transform_scope()
        fids, shapes, lds = self._xform_targets(scope)
        if not fids and not shapes and not lds:
            return None
        key = self._xform_cache_key()
        pv = getattr(self, "_pos_version", 0)
        c = getattr(self, "_xform_centroid_cache", None)
        if c is not None and c[0] == (key, pv):
            return c[1]
        cxy = self._xform_centroid(fids, shapes, lds)
        if cxy is not None:
            self._xform_centroid_cache = ((key, pv), cxy)
        return cxy

    def merge_device_types_dialog(self):
        """随时可用：按设备类型(名称)虚拟合并 → 生成 composite 逻辑设备并接入页面。

        与导入时同一抽象（[[logical-composite-devices]]）：选 2+ 类型，按真实实例下标
        配对（A类#i + B类#i …）。成员合成围绕代表点的小环坐标（与代表点连贯，供分解几何），
        从各页可见集移除成员、把逻辑设备挂到首成员所在页（未放置则当前页）。引擎零改动。
        """
        proj = self._current_project()
        if proj is None:
            QMessageBox.information(self, "按类型合并", "无可用工程。")
            return
        fixtures = list(getattr(proj, "fixtures", []) or [])
        defs_by_id = self.fixture_defs_by_id()
        by_type: Dict[str, List[Tuple[str, str]]] = {}
        light_types: set = set()
        type_roles: Dict[str, str] = {}
        positions: Dict[str, Tuple[float, float]] = {}
        def _guess_role(fd, tname):
            chans = [str(c).strip().lower() for c in (getattr(fd, "channels", []) or [])]
            if any(c in ("r", "red", "g", "green", "b", "blue", "w", "white") for c in chans):
                return "light"
            if any(c.startswith("motor") for c in chans):
                return "motor"
            if any("valve" in c for c in chans):
                return "valve"
            if any("pump" in c for c in chans):
                return "pump"
            nm = str(tname); low = nm.lower()
            if any(k in nm for k in ("灯", "光源")) or any(k in low for k in ("led", "rgb", "light", "laser")):
                return "light"
            if "电机" in nm or "motor" in low or "axis" in low or "jet" in low:
                return "motor"
            if "阀" in nm or "valve" in low or "nozzle" in low or "气爆" in nm:
                return "valve"
            if "泵" in nm or "pump" in low:
                return "pump"
            return "other"
        for fi in fixtures:
            fd = defs_by_id.get(str(getattr(fi, "def_id", "")))
            tname = str(getattr(fd, "name", "") or "未知类型") if fd is not None else "未知类型"
            fid = str(getattr(fi, "id", ""))
            by_type.setdefault(tname, []).append(
                (fid, str(getattr(fi, "name", "") or fid)))
            positions[fid] = (float(getattr(fi, "pos_x", 0.0) or 0.0),
                              float(getattr(fi, "pos_y", 0.0) or 0.0))
            if tname not in type_roles and fd is not None:
                r = _guess_role(fd, tname)
                type_roles[tname] = r
                if r == "light":
                    light_types.add(tname)
        if len([t for t, items in by_type.items() if items]) < 2:
            ec.topview_merge_insufficient_types(sum(1 for items in by_type.values() if items)).show(self)
            return
        from .composite_device_dialog import CompositeDeviceDialog
        dlg = CompositeDeviceDialog(by_type, positions=positions,
                                    light_types=light_types, type_roles=type_roles, parent=self)
        dlg.exec()
        new_lds = dlg.result_logical_devices()
        if not new_lds:
            return
        fix_by_id = self.fixtures_by_id()
        # 1) 几何：代表点 = 首个有坐标成员(锚)真实位置；local_offsets = 成员相对代表点的**真实**偏移。
        #    **不改写**成员真实坐标——灯就近分配基于真实坐标，改写小环会毁掉灯 per_shape 几何，
        #    使合并后单控效果与未合并不一致（控制必须与未合并时一模一样，合并只改显示）。
        for ld in new_lds:
            members = [str(m) for m in ld.get("member_fixture_ids", [])]
            rep = None
            for m in members:
                fi = fix_by_id.get(m)
                if fi is None:
                    continue
                rep = (float(getattr(fi, "pos_x", 0.0) or 0.0),
                       float(getattr(fi, "pos_y", 0.0) or 0.0))
                break
            if rep is None:
                rep = (0.0, 0.0)
            ld["pos_x"], ld["pos_y"] = rep
            offs = {}
            for m in members:
                fi = fix_by_id.get(m)
                if fi is not None:
                    offs[m] = [float(getattr(fi, "pos_x", 0.0) or 0.0) - rep[0],
                               float(getattr(fi, "pos_y", 0.0) or 0.0) - rep[1]]
            ld["local_offsets"] = offs
        # 2) 写入工程 logical_devices（按成员集去重，剔旧加新）
        new_members = {str(m) for ld in new_lds for m in ld.get("member_fixture_ids", [])}
        existing = list(getattr(proj, "logical_devices", []) or [])
        existing = [ld for ld in existing
                    if not ({str(m) for m in ld.get("member_fixture_ids", [])} & new_members)]
        existing.extend(new_lds)
        try:
            proj.logical_devices = existing
        except Exception:
            pass
        # 3) 归属页：首成员所在页（未放置则当前页）
        home_idx = {}
        for ld in new_lds:
            members = [str(m) for m in ld.get("member_fixture_ids", [])]
            idx = self._active_page
            for pi, pg in enumerate(self._pages):
                vis = {str(x) for x in (pg.get("visible_fixture_ids", []) or [])}
                if any(m in vis for m in members):
                    idx = pi; break
            home_idx[ld.get("id")] = idx
        # 4) 从所有页移除成员（不再单独画），逻辑设备挂归属页（按成员集去重）
        for pg in self._pages:
            vis = [str(x) for x in (pg.get("visible_fixture_ids", []) or [])]
            pg["visible_fixture_ids"] = [x for x in vis if x not in new_members]
            fx = pg.get("fixtures", {}) or {}
            for m in list(fx.keys()):
                if str(m) in new_members:
                    fx.pop(m, None)
        for ld in new_lds:
            pg = self._pages[max(0, min(home_idx.get(ld.get("id"), self._active_page), len(self._pages) - 1))]
            page_lds = pg.setdefault("logical_devices", [])
            members_set = {str(m) for m in ld.get("member_fixture_ids", [])}
            page_lds[:] = [x for x in page_lds
                           if not ({str(m) for m in x.get("member_fixture_ids", [])} & members_set)]
            page_lds.append(ld)
        # 5) 受影响页按「合并后实际显示的点」(可见个体 + composite 代表点)重新拟合图线，
        #    与导入路径一致——否则合并把成员折叠走后旧图线与显示不符。
        try:
            from .import_patch_csv import fit_shapes_for_points
            affected = {home_idx.get(ld.get("id"), self._active_page) for ld in new_lds}
            for pi in affected:
                if pi < 0 or pi >= len(self._pages):
                    continue
                pg = self._pages[pi]
                vis = [str(x) for x in (pg.get("visible_fixture_ids", []) or [])]
                fx = pg.get("fixtures", {}) or {}
                pts = []
                for fid in vis:
                    v = fx.get(fid)
                    if v:
                        pts.append((_safe_float(v.get("x")), _safe_float(v.get("y"))))
                for ld in (pg.get("logical_devices", []) or []):
                    try:
                        pts.append((float(ld.get("pos_x", 0.0)), float(ld.get("pos_y", 0.0))))
                    except Exception:
                        continue
                pg["cad_shapes"] = fit_shapes_for_points(pts)
        except Exception:
            pass
        self.refresh()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()
        try:
            if self._pm is not None and hasattr(self._pm, "project_changed"):
                self._pm.project_changed.emit()
        except Exception:
            pass
        QMessageBox.information(
            self, "按类型合并",
            f"已生成 {len(new_lds)} 台合并设备（TopView 显示成单点，可在 Programmer 切换合并/分解）。")

    def clear_all_logical_devices(self):
        """清除工程内全部虚拟合并设备（composite + 隐藏单元），成员恢复为各页可见个体。"""
        proj = self._current_project()
        if proj is None:
            return
        existing = list(getattr(proj, "logical_devices", []) or [])
        if not existing:
            QMessageBox.information(self, "清除合并", "当前没有任何虚拟合并设备。")
            return
        n = len(existing)
        ret = QMessageBox.question(
            self, "清除合并",
            f"将清除全部 {n} 个虚拟合并设备（含组合设备与隐藏单元），\n"
            f"成员恢复为各页单独显示的设备。此操作可撤销（Ctrl+Z）。是否继续？")
        if ret != QMessageBox.StandardButton.Yes: return
        self._snapshot_for_undo()   # ★ R137：清除合并改页可见集/fixtures/logical_devices
        # 收集所有曾被折叠走的 composite 成员，恢复其在归属页的可见性
        all_members = {str(m) for ld in existing for m in ld.get("member_fixture_ids", [])}
        fix_by_id = self.fixtures_by_id()
        for pg in self._pages:
            page_lds = pg.get("logical_devices", []) or []
            # 该页 composite 代表点对应的成员，恢复到可见集 + fixtures 坐标
            restore = set()
            for ld in page_lds:
                for m in ld.get("member_fixture_ids", []) or []:
                    if str(m) in fix_by_id:
                        restore.add(str(m))
            if restore:
                vis = [str(x) for x in (pg.get("visible_fixture_ids", []) or [])]
                seen = set(vis)
                for m in restore:
                    if m not in seen:
                        vis.append(m); seen.add(m)
                pg["visible_fixture_ids"] = vis
                fx = pg.setdefault("fixtures", {})
                for m in restore:
                    if m not in fx:
                        fi = fix_by_id.get(m)
                        if fi is not None:
                            fx[m] = {"x": float(getattr(fi, "pos_x", 0.0) or 0.0),
                                     "y": float(getattr(fi, "pos_y", 0.0) or 0.0)}
            pg["logical_devices"] = []
        try:
            proj.logical_devices = []
        except Exception:
            pass
        self.refresh()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()
        try:
            if self._pm is not None and hasattr(self._pm, "project_changed"):
                self._pm.project_changed.emit()
        except Exception:
            pass
        QMessageBox.information(self, "清除合并", f"已清除 {n} 个虚拟合并设备。")

    def scale_current_page(self, factor: float):
        """以当前页形心为中心，几何缩放本页所有设备坐标 + CAD 图形 + 逻辑设备代表点。

        这是**坐标级**缩放（写回 page 存储与成员 FixtureInstance.pos_x/pos_y），与画布
        滚轮视图缩放(_view_scale)正交。下游 Matrix/路由/效果读到的几何随之等比改变，
        解决「导入摆放太开」。factor=1.0 时无操作。
        """
        try:
            factor = float(factor)
        except Exception:
            return
        if abs(factor - 1.0) < 1e-6 or factor <= 0:
            return
        page = self.current_page()
        if not page: return
        self._snapshot_for_undo()   # ★ R137：整页缩放直写坐标/图形/包成员，原来不压快照

        # 1) 收集本页所有几何点求形心（设备 + 逻辑设备代表点）。
        fixtures = page.get("fixtures", {}) or {}
        lds = page.get("logical_devices", []) or []
        xs: List[float] = []
        ys: List[float] = []
        for d in fixtures.values():
            try:
                xs.append(float(d.get("x", 0.0))); ys.append(float(d.get("y", 0.0)))
            except Exception:
                continue
        for ld in lds:
            try:
                xs.append(float(ld.get("pos_x", 0.0))); ys.append(float(ld.get("pos_y", 0.0)))
            except Exception:
                continue
        if not xs:
            return
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)

        def sx(v: float) -> float:
            return cx + (float(v) - cx) * factor

        def sy(v: float) -> float:
            return cy + (float(v) - cy) * factor

        # 2) 设备坐标
        for d in fixtures.values():
            try:
                d["x"] = sx(d.get("x", 0.0)); d["y"] = sy(d.get("y", 0.0))
            except Exception:
                continue

        # 3) CAD 图形 + 矩阵（按 type 缩放几何键；尺寸键(rx/ry/dx/dy)只乘 factor）
        for shape in (list(page.get("cad_shapes", []) or []) +
                      list(page.get("pattern_matrices", []) or [])):
            self._scale_shape_geometry(shape, cx, cy, factor)

        # 4) 逻辑设备代表点 + 局部偏移 + 成员真实坐标
        proj = self._current_project()
        fix_by_id = {str(getattr(f, "id", "")): f for f in getattr(proj, "fixtures", [])} if proj else {}
        for ld in lds:
            try:
                ld["pos_x"] = sx(ld.get("pos_x", 0.0)); ld["pos_y"] = sy(ld.get("pos_y", 0.0))
            except Exception:
                pass
            offs = ld.get("local_offsets", {}) or {}
            for k in list(offs.keys()):
                try:
                    offs[k] = [float(offs[k][0]) * factor, float(offs[k][1]) * factor]
                except Exception:
                    continue
            # 成员真实散布坐标随页面同比缩放：与设备/代表点同以**页形心**为中心（sx/sy），
            # 不能以单元代表点为中心——否则注入 factor*(1-factor)*(rep-形心) 的偏移，使成员
            # 与代表点脱节、违反 member-rep==local_offset*factor 不变式、且重复缩放会累积。
            for fid in ld.get("member_fixture_ids", []) or []:
                fi = fix_by_id.get(str(fid))
                if fi is None:
                    continue
                try:
                    fi.pos_x = sx(float(getattr(fi, "pos_x", 0.0)))
                    fi.pos_y = sy(float(getattr(fi, "pos_y", 0.0)))
                except Exception:
                    continue

        # 同步逻辑设备 dict 回 project（如果代表点/偏移就地改了，project.logical_devices
        # 引用相同 dict 则已生效；保险起见触发刷新）。
        if hasattr(self, "_scale_spin"):
            try:
                self._scale_spin.setValue(1.0)
            except Exception:
                pass
        self.refresh()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()

    @staticmethod
    def _scale_shape_geometry(shape: dict, cx: float, cy: float, factor: float):
        """就地几何缩放单个 CAD/矩阵图形（以 (cx,cy) 为中心，factor 倍）。"""
        def sx(v): return cx + (float(v) - cx) * factor
        def sy(v): return cy + (float(v) - cy) * factor
        t = str(shape.get("type", "") or "")
        if t == "line":
            for k in ("x1", "x2"):
                if k in shape: shape[k] = sx(shape[k])
            for k in ("y1", "y2"):
                if k in shape: shape[k] = sy(shape[k])
        elif t in ("circle", "ellipse"):
            if "cx" in shape: shape["cx"] = sx(shape["cx"])
            if "cy" in shape: shape["cy"] = sy(shape["cy"])
            for k in ("rx", "ry", "r"):
                if k in shape:
                    try: shape[k] = float(shape[k]) * factor
                    except Exception:
                        logger.debug("缩放CAD图形半径/尺寸时异常")
        elif t == "polyline":
            pts = shape.get("points", []) or []
            shape["points"] = [[sx(p[0]), sy(p[1])] for p in pts if len(p) >= 2]
            cps = shape.get("seg_cps", {}) or {}
            for key, cp in list(cps.items()):
                for ck in ("cp1", "cp2"):
                    if ck in cp and len(cp[ck]) >= 2:
                        cp[ck] = [sx(cp[ck][0]), sy(cp[ck][1])]
        elif t == "pattern_matrix":
            if "x" in shape: shape["x"] = sx(shape["x"])
            if "y" in shape: shape["y"] = sy(shape["y"])
            for row in shape.get("rows", []) or []:
                for k in ("dx", "dy", "offset"):
                    if k in row:
                        try: row[k] = float(row[k]) * factor
                        except Exception:
                            logger.debug("缩放pattern_matrix行参数时异常")

    def delete_last_shape(self):
        """删除图形：优先删除画布当前选定图形（多选时全删），再回退删除最后绘制图形。"""
        canvas = getattr(self, "_canvas", None)
        if canvas is not None:
            sids = list(getattr(canvas, "_selected_shape_ids", set()) or [])
            if sids:
                self._snapshot_for_undo()
                for sid in sids:
                    if canvas._shape_by_id(sid):
                        canvas._delete_shape(sid)
                canvas._selected_shape_ids.clear()
                canvas._selected_shape_id = ""
                return
            sid = str(getattr(canvas, "_selected_shape_id", "") or getattr(canvas, "_active_shape_id", "") or "")
            if sid and hasattr(canvas, "_shape_by_id") and canvas._shape_by_id(sid):
                canvas._delete_shape(sid)
                return

        page = self.current_page()
        if page.get("pattern_matrices"):
            page["pattern_matrices"].pop()
        elif page.get("cad_shapes"):
            page["cad_shapes"].pop()
        if canvas is not None:
            canvas.update()
        self.layout_changed.emit()
    def selected_or_group_ids(self, use_selected=False) -> List[str]:
        # ★ R173 性能（perf-hotspot-survey #10）：set(visible_ids) 原写在列表
        # 推导式的 if 子句里，Python 对每个 fid 都会重新求值一次 —— O(G×N) 而非
        # O(N)。提到循环外算一次即可，语义不变（仍是"in 同一个可见集合"）。
        visible_ids = self.fixture_ids()
        if use_selected and self.selected_ids:
            ordered_sel = self._ordered_selected_ids()
            visible_set = set(visible_ids)
            return [fid for fid in ordered_sel if fid in visible_set]
        return visible_ids

    def _line_points(self, count: int, cx: float, cy: float, spacing: float = 54.0) -> List[Tuple[float, float]]:
        count = max(0, int(count or 0)); spacing = float(spacing or 54.0)
        if count <= 0:
            return []
        return [(cx + (i - (count - 1) / 2.0) * spacing, cy) for i in range(count)]

    def _quick_place_target_ids(self) -> List[str]:
        ordered = self._ordered_selected_ids()
        return ordered or self.fixture_ids()

    def _ordered_ids_by(self, ids: List[str], source_order: Optional[List[str]] = None) -> List[str]:
        src = source_order or self._ordered_selected_ids()
        rank = {fid: i for i, fid in enumerate(src)}
        fallback = len(rank) + 100000
        return sorted(ids or [], key=lambda fid: rank.get(fid, fallback))

    def _quick_place_center(self, ids: List[str]) -> Tuple[float, float]:
        if ids:
            pts = [self.fixture_pos(fid) for fid in ids]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        w = self._canvas.width() if hasattr(self, "_canvas") and self._canvas.width() > 80 else 900
        h = self._canvas.height() if hasattr(self, "_canvas") and self._canvas.height() > 80 else 520
        return w / 2.0, h / 2.0

    def quick_place(self, mode: str):
        ids = self._quick_place_target_ids()
        ids = self._ordered_ids_by(ids)
        if not ids:
            QMessageBox.information(self, "快捷放置", "当前 TopView 画板中没有可排布设备，请先从设备组展板拖入设备。")
            return
        cx, cy = self._quick_place_center(ids)
        mode = str(mode or "line")
        spacing = self._quick_spacing.value() if hasattr(self, "_quick_spacing") else 54.0
        if mode == "matrix":
            cols = max(1, int(self._quick_matrix_cols.value() if hasattr(self, "_quick_matrix_cols") else math.ceil(math.sqrt(len(ids)))))
            dx = spacing
            dy = self._quick_matrix_y.value() if hasattr(self, "_quick_matrix_y") else 42.0
            rows = int(math.ceil(len(ids) / cols))
            pts = []
            for i in range(len(ids)):
                col, row = i % cols, i // cols
                pts.append((cx + (col - (min(cols, len(ids)) - 1) / 2.0) * dx,
                            cy + (row - (rows - 1) / 2.0) * dy))
        elif mode == "circle":
            radius = self._quick_circle_radius.value() if hasattr(self, "_quick_circle_radius") else max(80.0, len(ids) * 8.0)
            denom = max(1, len(ids))
            pts = [(cx + math.cos(2 * math.pi * i / denom) * radius,
                    cy + math.sin(2 * math.pi * i / denom) * radius) for i in range(len(ids))]
        else:
            pts = self._line_points(len(ids), cx, cy, spacing)
        for fid, (x, y) in zip(ids, pts):
            self.set_fixture_pos(fid, x, y)
        self._refresh_sync_reference_for_ids(ids)
        self._set_selection(ids, emit=False)
        self.emit_selection()
        self.refresh()

    def rotate_selected_shape(self, angle_deg: float):
        if hasattr(self, "_canvas"):
            self._canvas.rotate_selected_shape(angle_deg)

    def rotate_selected_fixtures(self, angle_deg: float):
        """选区绕质心旋转：选中设备 + 选中图形一起转，图形随设备保持相对位置。

        质心 = 选中设备坐标与图形包围盒角点的均值（含图形时图形也参与质心，
        保证「设备绕图形转/图形绕设备转」的直觉一致）。"""
        ids = self._ordered_selected_ids()
        shapes = self._canvas._selected_shape_list() if hasattr(self, "_canvas") else []
        if len(ids) + len(shapes) < 1:
            QMessageBox.information(self, "选区旋转", "请先在 TopView 中选择已放入画板的设备或图形。")
            return
        if len(ids) == 1 and not shapes:
            QMessageBox.information(self, "选区旋转", "请至少选择 2 台设备，或手动选中一个图形。")
            return
        self._snapshot_for_undo()
        points: List[Tuple[float, float]] = [self.fixture_pos(fid) for fid in ids]
        for shape in shapes:
            x1, y1, x2, y2 = _shape_bounds(shape)
            points.extend([(x1, y1), (x2, y2)])
        if not points:
            return
        cx = sum(x for x, _y in points) / float(len(points))
        cy = sum(y for _x, y in points) / float(len(points))
        for fid in ids:
            x, y = self.fixture_pos(fid)
            nx, ny = _rotate_point_xy(float(x), float(y), cx, cy, float(angle_deg))
            self.set_fixture_pos(fid, nx, ny)
        for shape in shapes:
            _rotate_shape_around(shape, cx, cy, float(angle_deg))
        if ids:
            self._refresh_sync_reference_for_ids(ids)
            self._set_selection(ids, emit=False)
            self.emit_selection()
        self._finish_geometry_change(ids)

    def flip_selected_fixtures(self, axis: str):
        """选区绕质心对称翻面：axis="x" 上下镜像（绕质心水平轴），"y" 左右镜像。"""
        ids = self._ordered_selected_ids()
        if len(ids) < 2:
            QMessageBox.information(self, "选区翻面", "请先在 TopView 中选择至少 2 台设备。")
            return
        points = [self.fixture_pos(fid) for fid in ids]
        cx = sum(x for x, _y in points) / float(len(points))
        cy = sum(y for _x, y in points) / float(len(points))
        axis = str(axis or "").lower()
        self._snapshot_for_undo()
        for fid, (x, y) in zip(ids, points):
            if axis == "x":
                self.set_fixture_pos(fid, float(x), cy * 2.0 - float(y))
            elif axis == "y":
                self.set_fixture_pos(fid, cx * 2.0 - float(x), float(y))
            else:
                return
        self._refresh_sync_reference_for_ids(ids)
        self._set_selection(ids, emit=False)
        self.emit_selection()
        self._finish_geometry_change(ids)

    def _layout_shapes(self) -> List[dict]:
        page = self.current_page()
        return list(page.get("cad_shapes", []) or []) + list(page.get("pattern_matrices", []) or [])

    def _layout_group_clusters(self) -> List[dict]:
        """当前页排布簇：每个图形一簇（归属设备并入），未归属设备按组/单台成簇。

        组间距离(apply_layout_group_spacing)按簇质心相对整体质心成比例拉开/收拢。
        """
        visible_ids = self.fixture_ids()
        if not visible_ids:
            return []
        fixture_points = {fid: self.fixture_pos(fid) for fid in visible_ids}
        shapes = self._layout_shapes()
        clusters: List[dict] = []
        assigned_fixtures: set = set()
        for shape_index, shape in enumerate(shapes):
            clusters.append({
                "name": str(shape.get("id", f"shape-{shape_index}") or f"shape-{shape_index}"),
                "shapes": [shape],
                "fixture_ids": [],
                "points": _shape_layout_points(shape),
            })
        if clusters:
            for fid, (x, y) in fixture_points.items():
                pos = QPointF(float(x), float(y))
                best_idx = -1
                best_dist = 999999999.0
                best_inside = False
                for idx, cluster in enumerate(clusters):
                    shape = cluster["shapes"][0]
                    try:
                        tol = float(self._shape_assign_tolerance(shape))
                        inside = bool(_shape_body_contains(shape, pos, padding=tol))
                        dist = float(_shape_hit_distance(shape, pos))
                    except Exception:
                        continue
                    if inside and not best_inside:
                        best_idx = idx
                        best_dist = dist
                        best_inside = True
                    elif inside == best_inside and dist < best_dist:
                        best_idx = idx
                        best_dist = dist
                if best_idx >= 0:
                    tol = self._shape_assign_tolerance(clusters[best_idx]["shapes"][0])
                    if best_inside or best_dist <= tol:
                        clusters[best_idx]["fixture_ids"].append(fid)
                        clusters[best_idx]["points"].append((float(x), float(y)))
                        assigned_fixtures.add(fid)
        page = self.current_page()
        placed_group_ids = [str(gid) for gid in (page.get("placed_group_ids", []) or []) if str(gid)]
        for gid in placed_group_ids:
            ids = [fid for fid in self._group_fixture_ids(gid)
                   if fid in fixture_points and fid not in assigned_fixtures]
            if not ids:
                continue
            pts = [fixture_points[fid] for fid in ids]
            clusters.append({
                "name": gid,
                "shapes": [],
                "fixture_ids": list(ids),
                "points": list(pts),
            })
            assigned_fixtures.update(ids)
        remaining = [fid for fid in visible_ids if fid not in assigned_fixtures]
        if remaining:
            pts = [fixture_points[fid] for fid in remaining]
            if not clusters:
                clusters.append({
                    "name": "all-visible",
                    "shapes": [],
                    "fixture_ids": list(remaining),
                    "points": list(pts),
                })
            else:
                for fid, pt in zip(remaining, pts):
                    clusters.append({
                        "name": fid,
                        "shapes": [],
                        "fixture_ids": [fid],
                        "points": [pt],
                    })
        out = []
        used_shape_ids: set = set()
        used_fixture_ids: set = set()
        for cluster in clusters:
            fixture_ids = []
            for fid in cluster.get("fixture_ids", []) or []:
                sfid = str(fid)
                if sfid and sfid not in used_fixture_ids:
                    fixture_ids.append(sfid)
                    used_fixture_ids.add(sfid)
            shape_items = []
            for shape in cluster.get("shapes", []) or []:
                # 局部去重（非跨调用缓存）：shape 在本调用内强引用存活，id 不会被复用。
                key = id(shape)
                if key not in used_shape_ids:
                    shape_items.append(shape)
                    used_shape_ids.add(key)
            points = list(cluster.get("points", []) or [])
            if not points:
                points.extend(fixture_points[fid] for fid in fixture_ids if fid in fixture_points)
                for shape in shape_items:
                    points.extend(_shape_layout_points(shape))
            if not points:
                continue
            cx = sum(float(x) for x, _y in points) / float(len(points))
            cy = sum(float(y) for _x, y in points) / float(len(points))
            x1 = min(float(x) for x, _y in points)
            x2 = max(float(x) for x, _y in points)
            y1 = min(float(y) for _x, y in points)
            y2 = max(float(y) for _x, y in points)
            out.append({
                "name": cluster.get("name", ""),
                "shapes": shape_items,
                "fixture_ids": fixture_ids,
                "center": (cx, cy),
                "bounds": (x1, y1, x2, y2),
                "points": points,
            })
        return out

    def _layout_spacing_percent(self) -> float:
        if hasattr(self, "_layout_spacing_slider"):
            return max(0.0, float(self._layout_spacing_slider.value()))
        return float(getattr(self, "_layout_spacing_default_percent", 100))

    def _set_layout_spacing_ui_from_page(self):
        """从当前页恢复组间距离百分比（保存/切页/刷新时调用，滑块无信号静默同步）。"""
        if not hasattr(self, "_layout_spacing_slider"):
            return
        page = self.current_page()
        default_percent = int(getattr(self, "_layout_spacing_default_percent", 100))
        raw = page.get("layout_group_spacing_percent", page.get("layout_group_spacing_px", default_percent))
        try:
            value = int(round(float(raw)))
        except Exception:
            value = default_percent
        value = max(self._layout_spacing_slider.minimum(), min(self._layout_spacing_slider.maximum(), value))
        old = self._layout_spacing_slider.blockSignals(True)
        self._layout_spacing_slider.setValue(value)
        self._layout_spacing_slider.blockSignals(old)
        if hasattr(self, "_layout_spacing_label"):
            self._layout_spacing_label.setText(f"{value}%")

    def apply_layout_group_spacing(self):
        """组间距离：各簇质心绕整体质心按百分比成比例拉开/收拢（图形+设备同移）。

        需要至少两个非空簇；百分比存回当前页并在刷新/切页时恢复。撤销走
        _snapshot_for_undo；几何收尾走 _finish_geometry_change（同步基准刷新）。
        """
        spacing_percent = self._layout_spacing_percent()
        clusters = self._layout_group_clusters()
        movable = [c for c in clusters if c.get("fixture_ids") or c.get("shapes")]
        if len(movable) < 2:
            QMessageBox.information(self, "组间距离", "当前页面至少需要两个图形/设备组簇才能拉开距离。")
            return
        selected_order = self._ordered_selected_ids()
        self._snapshot_for_undo()
        self.current_page()["layout_group_spacing_percent"] = spacing_percent
        centers = [c["center"] for c in movable]
        gcx = sum(x for x, _y in centers) / float(len(centers))
        gcy = sum(y for _x, y in centers) / float(len(centers))
        delta_scale = max(0.0, float(spacing_percent)) / 100.0 - 1.0
        moved_fixtures: set = set()
        moved_shapes: set = set()
        for cluster in movable:
            cx, cy = cluster["center"]
            dx = (float(cx) - gcx) * delta_scale
            dy = (float(cy) - gcy) * delta_scale
            if abs(dx) <= 1e-6 and abs(dy) <= 1e-6:
                continue
            for fid in cluster.get("fixture_ids", []) or []:
                if fid in moved_fixtures:
                    continue
                x, y = self.fixture_pos(fid)
                self.set_fixture_pos(fid, float(x) + dx, float(y) + dy)
                moved_fixtures.add(fid)
            for shape in cluster.get("shapes", []) or []:
                # 局部去重（非跨调用缓存）：shape 在本调用内强引用存活，id 不会被复用。
                key = id(shape)
                if key in moved_shapes:
                    continue
                _translate_shape(shape, dx, dy)
                moved_shapes.add(key)
        self._finish_geometry_change(selected_order, refresh_ids=list(moved_fixtures))

    def _shape_assign_tolerance(self, shape: dict) -> float:
        x1, y1, x2, y2 = _shape_bounds(shape)
        span = max(abs(x2 - x1), abs(y2 - y1), 1.0)
        return max(24.0, min(80.0, span * 0.14))

    def _fixture_number_sort_key(self, fid: str) -> tuple:
        """按设备编号排序键：名称数字 → id 数字 → 原序（universe/channel 次序）。"""
        fi = self.fixtures_by_id().get(str(fid))
        if fi is None:
            return (2, (), 999999, 999999, str(fid))
        name = str(getattr(fi, "name", "") or "")
        numbers = tuple(int(x) for x in re.findall(r"\d+", name))
        universe = int(getattr(fi, "universe", 1) or 1)
        start_channel = int(getattr(fi, "start_channel", 1) or 1)
        if numbers:
            return (0, numbers[-1], universe, start_channel, name.lower(), str(fid))
        id_numbers = tuple(int(x) for x in re.findall(r"\d+", str(fid)))
        if id_numbers:
            return (1, id_numbers[-1], universe, start_channel, name.lower(), str(fid))
        return (2, (), universe, start_channel, name.lower(), str(fid))

    def _valid_fixture_id_set(self) -> set:
        """★ R38-D9：全工程设备 id 集合，按布局版本缓存。

        原先每次 _fixture_number_order_ids 都 set(self._all_fixture_ids())，而它
        是"每个空间分组一次"被调的；11382 台工程下等于每组重建一个上万元素的
        集合。按 _layout_version 缓存，布局一变就作废。
        """
        ver = int(getattr(self, "_layout_version", 0))
        cached = getattr(self, "_valid_id_set_cache", None)
        if cached is not None and cached[0] == ver:
            return cached[1]
        result = set(self._all_fixture_ids())
        self._valid_id_set_cache = (ver, result)
        return result

    def _fixture_number_order_ids(self, ids: List[str]) -> List[str]:
        """按设备编号排序去重（空间分组/编曲边界用，保持工程内真实顺序稳定）。"""
        seen = set()
        unique_ids = []
        valid = self._valid_fixture_id_set()
        for fid in ids or []:
            sfid = str(fid)
            if sfid and sfid in valid and sfid not in seen:
                unique_ids.append(sfid)
                seen.add(sfid)
        return sorted(unique_ids, key=self._fixture_number_sort_key)

    def spatial_groups_for_fixture_ids(self, fixture_ids: List[str]) -> List[List[str]]:
        """当前 TopView 图形归属分组：设备放在哪个图形上就归哪个组。

        调用方: effect_browser 环形/图形类效果边界, main_window 编曲形状分组
        (2026-08-08 W3 恢复)。每组按设备编号排序——局部分组切分图形本身而非
        全局选择序。

        ★ R38-D9：按 (布局版本, 当前页, 入参内容) 缓存。命中时会**逐元素比对
        入参**再返回，不靠 id() 判身份——id 会被回收复用，只比 id/长度可能命中
        到另一份设备集的分组结果 = 输出到错误的设备（P0）。逐元素比对是 O(n)
        的指针比较，相对本方法 O(设备×图形) 的几何循环可忽略。
        """
        ids = [str(fid) for fid in (fixture_ids or []) if str(fid)]
        if len(ids) <= 1:
            return []
        cache = getattr(self, "_spatial_group_cache", None)
        if cache is None:
            cache = {}
            self._spatial_group_cache = cache
        ckey = (int(getattr(self, "_layout_version", 0)), int(self._active_page), len(ids))
        hit = cache.get(ckey)
        if hit is not None and hit[0] == ids:
            return [list(g) for g in hit[1]]
        shapes = self._layout_shapes()
        if not shapes:
            return self._store_spatial_groups(cache, ckey, ids, [])
        allowed_ids = []
        seen_allowed: set = set()
        visible = set(self.fixture_ids())
        for fid in ids:
            if fid in visible and fid not in seen_allowed:
                allowed_ids.append(fid)
                seen_allowed.add(fid)
        if len(allowed_ids) <= 1:
            return self._store_spatial_groups(cache, ckey, ids, [])
        from .topview_spatial_assignment import ShapeMeasurements
        measurements = ShapeMeasurements(self, shapes)
        groups: List[List[str]] = [[] for _ in shapes]
        for fid in allowed_ids:
            try:
                x, y = self.fixture_pos(fid)
            except Exception:
                continue
            pos = QPointF(float(x), float(y))
            best_idx = -1
            best_dist = 999999999.0
            best_inside = False
            for idx, shape in enumerate(shapes):
                try:
                    inside, dist = measurements.measure(idx, pos)
                except Exception:
                    continue
                if inside and not best_inside:
                    best_idx = idx
                    best_dist = dist
                    best_inside = True
                elif inside == best_inside and dist < best_dist:
                    best_idx = idx
                    best_dist = dist
            if best_idx < 0:
                continue
            try:
                tol = float(measurements.tolerance(best_idx))
            except Exception:
                tol = 32.0
            if best_inside or best_dist <= tol:
                groups[best_idx].append(fid)
        ordered_groups: List[List[str]] = []
        for group in groups:
            if not group:
                continue
            ordered_groups.append(self._fixture_number_order_ids(group))
        return self._store_spatial_groups(cache, ckey, ids, ordered_groups)

    def _store_spatial_groups(self, cache: dict, ckey: tuple, ids: List[str],
                              result: List[List[str]]) -> List[List[str]]:
        """★ R38-D9：写入空间分组缓存并返回副本。

        **空结果也必须缓存**——R38 的成本主体恰恰是"算了一整轮 O(设备×图形)
        最后返回 []"这条路径，旧代码对它零记忆，于是每帧每效果每通道重算一次。
        返回副本是因为调用方（effect_browser._normalize_spatial_groups）会对结果
        再加工，不能让它改到缓存里的那份。
        """
        if len(cache) > 16:
            cache.clear()
        cache[ckey] = (list(ids), [list(g) for g in result])
        return [list(g) for g in result]

    def _shape_grouped_selection_order(self, ids: List[str]) -> List[str]:
        """按图形分组重排选中设备序：同图形设备聚在一起，未归属设备排最后。

        Shift+点曲线连选时以该序切片 → 「曲线设备的编号区间」连续（2026-08-08
        用户：曲线设备无法快捷键连选）。单台/无图形页返回原序。
        """
        ids = [str(fid) for fid in (ids or []) if str(fid)]
        if len(ids) <= 1:
            return ids
        shapes = self._layout_shapes()
        if not shapes:
            return ids

        groups: List[List[str]] = [[] for _ in shapes]
        unassigned: List[str] = []
        used_groups: set = set()
        for fid in ids:
            try:
                x, y = self.fixture_pos(fid)
            except Exception:
                unassigned.append(fid)
                continue
            pos = QPointF(float(x), float(y))
            best_idx = -1
            best_dist = 999999999.0
            best_inside = False
            for idx, shape in enumerate(shapes):
                try:
                    tol = float(self._shape_assign_tolerance(shape))
                    inside = bool(_shape_body_contains(shape, pos, padding=tol))
                    dist = float(_shape_hit_distance(shape, pos))
                except Exception:
                    continue
                if inside and not best_inside:
                    best_idx = idx
                    best_dist = dist
                    best_inside = True
                elif inside == best_inside and dist < best_dist:
                    best_idx = idx
                    best_dist = dist
            if best_idx >= 0:
                try:
                    tol = float(self._shape_assign_tolerance(shapes[best_idx]))
                except Exception:
                    tol = 32.0
                if best_inside or best_dist <= tol:
                    groups[best_idx].append(fid)
                    used_groups.add(best_idx)
                    continue
            unassigned.append(fid)

        if len(used_groups) <= 1 and not unassigned:
            return ids
        ordered: List[str] = []
        for group in groups:
            ordered.extend(group)
        ordered.extend(unassigned)
        return ordered or ids

    def arrange_group_on_last_shape(self, use_selected=False):
        page = self.current_page(); shapes = (page.get("cad_shapes", []) or []) + (page.get("pattern_matrices", []) or [])
        if not shapes: ec.topview_no_shapes().show(self); return
        ids = self.selected_or_group_ids(use_selected=use_selected)
        ids = self._ordered_ids_by(ids)
        # ★ R137：重排设备坐标原先不压撤销快照（同 token 短窗口去重，供矩阵入口内联调用）
        _undo_snapshot_action(self, "arrange_last_shape")
        if len(ids) == 1:
            # ★★ R170c：单台 → 吸附最近点（旧写法 `_shape_points(sh, 1)` 会跳到图形起点，
            #   与那里的设备重合 → 用户报"弧线少一个点"）。这里没有落点，用设备自身位置投影。
            try:
                cx, cy = self.fixture_pos(ids[0])
            except Exception:
                cx = cy = 0.0
            pts = [_shape_nearest_point(shapes[-1], float(cx), float(cy))]
        else:
            pts = _shape_points(shapes[-1], len(ids))
        for fid, (x, y) in zip(ids, pts): self.set_fixture_pos(fid, x, y)
        self._refresh_sync_reference_for_ids(ids)
        self._set_selection(ids, emit=False); self.emit_selection(); self.refresh()
    def create_pattern_matrix_and_arrange(self):
        rows = []
        text = self._mx_rows_text.text().strip() if hasattr(self, "_mx_rows_text") else ""
        if text:
            try:
                for part in text.replace("\n", ";").split(";"):
                    part = part.strip()
                    if not part: continue
                    vals = [v.strip() for v in part.split(",")]
                    rows.append({
                        "count": max(0, int(float(vals[0]))),
                        "dx": float(vals[1]) if len(vals) > 1 and vals[1] else self._mx_dx.value(),
                        "dy": float(vals[2]) if len(vals) > 2 and vals[2] else self._mx_dy.value(),
                        "offset": float(vals[3]) if len(vals) > 3 and vals[3] else 0.0,
                    })
            except Exception as exc:
                QMessageBox.warning(self, "花样矩阵", f"每行自定义格式错误：{exc}\n示例：8,40,35,0;6,45,42,20")
                return
        else:
            cnt = self._mx_base.value()
            for r in range(self._mx_rows.value()):
                rows.append({"count": max(0, cnt), "dx": self._mx_dx.value(), "dy": self._mx_dy.value(), "offset": 0 if r % 2 == 0 else self._mx_dx.value()/2}); cnt += self._mx_delta.value()
        _undo_snapshot_action(self, "arrange_last_shape"); self.current_page().setdefault("pattern_matrices", []).append({"id":str(uuid.uuid4()),"type":"pattern_matrix","x":60,"y":60,"rows":rows}); self.arrange_group_on_last_shape(False)  # ★ R137
    def _page_index_by_fixture(self) -> Dict[str, int]:
        """fid → 页索引(可见设备或包成员隶属页)。跨页编号分组用。

        ★ 性能：O(总设备数) 构建一次，页**结构**变化才重建。
        ★ R137 修复（跨页编号/页分组错页）：旧键 `(id(pages), 总成员数)` 只统计
        成员**总数**——设备从 A 页移到 B 页（总数不变、分布变了）时键不变 → 返回
        旧映射 → 徽标编号 / 页分组按旧页归属错页。改用与 `_all_pages_visible_ids`
        同款的**内容化键**（页 id+可见 id+包成员 id），分布一变键即变。
        每台设备至多隶属一页（visible_fixture_ids 互斥，包成员不在任何页 visible 中）。
        """
        pages = self._pages
        key = (id(pages), self._pages_visible_cache_key())
        cached = getattr(self, "_page_index_cache", None)
        if (cached is not None and getattr(self, "_page_index_key", None) == key
                and getattr(self, "_page_index_pages", None) is pages):
            return cached
        idx: Dict[str, int] = {}
        for pi, pg in enumerate(pages):
            for fid in (pg.get("visible_fixture_ids", []) or []):
                idx.setdefault(str(fid), pi)
            for ld in (pg.get("logical_devices", []) or []):
                for m in (ld.get("member_fixture_ids", []) or []):
                    idx.setdefault(str(m), pi)
        self._page_index_cache = idx
        self._page_index_key = key
        self._page_index_pages = pages
        return idx

    def _page_grouped_order_buckets(self, order_ids: List[str]) -> Dict[int, List[str]]:
        """跨页选择序 → 按页分组桶(页内相对序保留, 键=页索引, 未归属页设备键=-1)。

        ★ 2026-08-04 跨页编号统一修复的公共分组: 各页设备在**自己页**的选中序中
        从 1 连续编号, 不再按跨页全局序交错(泵页 1..N、设备包页 1..M)。
        """
        buckets: Dict[int, List[str]] = {}
        if not order_ids:
            return buckets
        page_of = self._page_index_by_fixture()
        for fid in order_ids:
            sfid = str(fid)
            pi = page_of.get(sfid)
            if pi is None:
                buckets.setdefault(-1, []).append(sfid)
            else:
                buckets.setdefault(pi, []).append(sfid)
        return buckets

    def _page_grouped_order_ids(self, order_ids: List[str]) -> List[str]:
        """跨页选择序 → 按页分组序(页序=页面索引序, 页内保持选择相对序)。

        供画布编号徽标与 _ordered_selected_ids 共用, 保证「所见编号 = 输出
        顺序」一致: 对泵应用效果走泵的 1..N、电机走电机的 1..N(各自页/类型)。
        """
        if not order_ids:
            return list(order_ids)
        buckets = self._page_grouped_order_buckets(order_ids)
        out: List[str] = []
        for _pi in sorted(buckets):
            if _pi >= 0:
                out.extend(buckets[_pi])
        # 未归属页设备(不在任何页的可见/包成员)放最后
        out.extend(buckets.get(-1, []))
        return out

    def _ordered_selected_ids(self) -> List[str]:
        # 用全局可见集(所有页并集)过滤，使跨 sheet 连选的设备能进入 emit/设备组创建。
        # 普通模式下 selected_ids 只含当前页，全局集是其超集，结果与按当前页过滤一致。
        visible = self._all_pages_visible_ids()
        ordered = [fid for fid in self._selection_order_ids if fid in visible and fid in self.selected_ids]
        if ordered:
            # ★ 2026-08-04 跨页编号统一: 按页分组(页内相对序保留)再输出——画布编号
            #   徽标/Programmer 载入序/效果序列全部按页各自连续, 不再跨页交错跳号。
            return self._page_grouped_order_ids(ordered)
        # ★ 回退用页面设备列表序（含包/组合设备成员，2026-08-04 用户：
        #   包设备选择状态不得丢失——成员不在 visible_fixture_ids，原回退会丢弃）。
        return [fid for fid in self._page_device_order() if fid in self.selected_ids]

    def _page_device_order(self) -> List[str]:
        """页面设备列表序 = 可见设备（放置/导入序）+ 包/组合设备成员（页内顺序）。

        2026-08-04 用户实测：奇数/偶数/间隔选择必须按「选择列表序/设备序」隔一，
        不能按位置/区域分。此序即「设备序」——与设备列表/拖入顺序一致，与
        画布位置无关。★ 奇偶/间隔/左右半选择在此基础上按**包级单元**隔一
        （_selection_units_from_base：包=一个整体，包内灯同进退，仅 effect/
        matrix 路径才单控逐灯）；本序仍是平铺设备序（含全部包成员），供
        _ordered_selected_ids 回退/过滤与单元折叠的底序使用。
        """
        out = list(self.fixture_ids())
        seen = set(out)
        for ld in self.current_page_logical_devices():
            for m in (ld.get("member_fixture_ids", []) or []):
                sfid = str(m)
                if sfid not in seen:
                    out.append(sfid)
                    seen.add(sfid)
        return out

    def _selection_filter_base(self) -> tuple[List[str], bool]:
        # ★ 2026-08-04 用户实测修复：框选产生的 _selection_order_ids 是
        #   _selection_box_fixture_ids 的**位置扫描序**（按拖动方向 x/y 排序、
        #   弧线按角度排序），odd/even/间隔在此序上隔一 = 按位置/区域分
        #   （探针：2×4 网格框选后 odd = 整排「半边」）。
        #   修复：框选（_box_chain_active）→ 改用页面设备列表序过滤选中；
        #   逐台点击/Ctrl（_box_chain_active=False）→ 保留用户自定义选择序。
        # ★ 2026-08-13 用户新症状(坐标注入编号序)：用户已建立的选择序被奇偶等
        #   工具按坐标排布重新注入。返回 established 标志供调用方决定过滤是否
        #   保序：已建立(实时组/点击追加)→ 纯过滤继承原序；全新框选仍按
        #   08-04 语义在页面视觉序上隔一。
        # ★ R195 用户实报（真实工程复现）：弧线框选后奇偶/间隔错乱——链序被
        #   换成页面序后，_selection_units_from_base 的 (x,y) 重排在非 x 单调
        #   弧（半圆/U 形）两腿间锯齿跳，odd 前后 8 台角度 172°→163° 来回跳。
        #   框选链序本身即弧线角度序（同曲线按曲线序、多曲线按扫掠相遇序、
        #   拖动方向感知）——直接作为**已建立序**继承（established=True，
        #   不再按坐标重排）。x 单调布局下链序与 (x,y) 序一致，行为不变；
        #   仅修复非单调弧。链缺失/不同步时回退旧路径。
        current = self._ordered_selected_ids()
        if not current:
            return self._page_device_order(), False
        if self._live_base_ids and set(current) == set(self._live_selection_ids):
            visible = set(self._page_device_order())
            out = [fid for fid in self._live_base_ids if fid in visible]
            return (out or current), True
        if self._box_chain_active:
            return _box_chain_base(self, current)   # R195：链序基
        return current, True

    def _member_to_logical_map(self, page=None) -> Dict[str, dict]:
        """本页逻辑/组合设备(包)成员 fid → 所属包 dict(仅成员在表内,页内一次建表)。"""
        out: Dict[str, dict] = {}
        devices = self.current_page_logical_devices() if page is None else page.get("logical_devices", []) or []
        for ld in devices:
            for m in (ld.get("member_fixture_ids", []) or []):
                out.setdefault(str(m), ld)
        return out

    def _selection_units_from_base(self, base: List[str], sort_by_pos: bool = True, page=None) -> List[dict]:
        """平铺设备序 → 包级选择单元序。

        ★ 2026-08-04 用户语义定案:TopView 选择 = **包级**——框选/奇偶/间隔/
        左右半选中的是设备包(逻辑/组合设备,画布代表点),**不是灯**;奇偶/间隔
        按包列表序隔一选一,包内全部灯同进退,绝不被奇偶/间隔拆开(气爆页
        实测:6 灯包在灯级隔一 → 每包必拆半,奇偶按下全部包半选高亮 → 选中
        状态不可视)。散台(非包成员)1 台 1 单元。单元位置 = 该包首个成员
        在平铺序中的位置(包列表序);代表点坐标供左/右半位置排序。
        单控(包内逐灯)只保留在 effect/matrix 效果应用路径,不参与选择。
        """
        m2ld = self._member_to_logical_map(page)
        units: List[dict] = []
        by_ld: Dict[str, dict] = {}
        for fid in (base or []):
            sfid = str(fid)
            ld = m2ld.get(sfid)
            if ld is not None:
                uid = str(ld.get("id", "") or "")
                u = by_ld.get(uid)
                if u is None:
                    try:
                        ux = float(ld.get("pos_x", 0.0) or 0.0)
                        uy = float(ld.get("pos_y", 0.0) or 0.0)
                    except Exception:
                        ux = uy = 0.0
                    u = {"fids": [], "pos": (ux, uy)}
                    by_ld[uid] = u
                    units.append(u)
                if sfid not in u["fids"]:
                    u["fids"].append(sfid)
            else:
                units.append({"fids": [sfid], "pos": self.fixture_pos(sfid)})
        # ★ 2026-08-04 用户实测: 包列表序(导入序)与弧线视觉序不一致 → 奇偶/间隔
        #   在列表序隔一, 沿弧线视觉图案混乱("选2隔2又隔1")。
        #   单元序统一按代表点位置(x,y)排序 = 画布视觉序(与左/右半一致)——
        #   奇偶/间隔/全部在视觉序上隔一, 编号沿弧线连续。
        # ★ 2026-08-13 用户新症状(坐标注入编号序): 上述排序只适用于全新框选的
        #   视觉序过滤; 用户已建立的选择序必须纯过滤继承原序, 不得按坐标重排。
        #   sort_by_pos=False 时保持 base 原序(奇偶/间隔隔一、全部恒等)。
        if sort_by_pos:  # R195：视觉排序（链序/网格护栏，见 _sort_units_visual）
            _sort_units_visual(units)
        return units

    def _set_live_selection(self, ids: List[str], name: str, base: Optional[List[str]] = None):
        visible_order = self.fixture_ids()
        # ★ 含包/组合设备成员（2026-08-04 用户：实时选择/间隔选择选中包后不得丢弃）。
        visible = set(visible_order) | self._selectable_member_ids()
        ordered = self._normalize_selection_sequence_ids(ids, visible)
        self._selection_order_ids = ordered
        self.selected_ids = set(ordered)
        self._reset_box_selection_chain()
        self._live_base_ids = self._normalize_selection_sequence_ids(base or ordered, visible)
        self._live_selection_ids = ordered
        self._live_selection_name = name or "TopView实时选择"
        self.emit_selection()
        if hasattr(self, "_canvas"):
            self._canvas.update()

    def set_route_order_selection(self, ids: List[str], name: str = "跑动顺序",
                                  base: Optional[List[str]] = None,
                                  group_id: str = "", force_visible: bool = True):
        """跑动编排面板写回 TopView 选择顺序（含基准序/设备组/可见性强制）。

        历史调用方: 旧跑动顺序面板(R18 W5 已整文件删除); 当前无活跃
        调用方, 保留该方法供后续跑动编排写回复用。
        force_visible 时把顺序设备全部置为当前页可见（缺失补入页尾），否则
        目标页不显示设备时顺序会被 normalize 过滤掉。
        """
        ordered = [str(fid) for fid in (ids or []) if str(fid)]
        self._snapshot_for_undo()
        if force_visible and ordered:
            page = self.current_page()
            visible = [str(x) for x in page.setdefault("visible_fixture_ids", [])]
            ordered_unique = self._unique_preserve_order(ordered)
            ordered_set = set(ordered_unique)
            existing_ordered = [fid for fid in ordered_unique if fid in visible]
            remaining = [fid for fid in visible if fid not in ordered_set]
            missing = [fid for fid in ordered_unique if fid not in set(visible)]
            page["visible_fixture_ids"] = existing_ordered + missing + remaining
            for fid in ordered_unique:
                self.fixture_pos(fid)
        visible = set(self.fixture_ids()) | self._selectable_member_ids()
        selected_order = self._normalize_selection_sequence_ids(ordered, visible)
        base_order = self._normalize_selection_sequence_ids(base or ordered, visible)
        if not base_order:
            base_order = list(selected_order)
        self._selection_order_ids = list(selected_order)
        self.selected_ids = set(selected_order)
        self._reset_box_selection_chain()
        self._live_base_ids = list(base_order)
        self._live_selection_ids = list(selected_order)
        self._live_selection_name = name or "跑动顺序"
        self._active_group_id = str(group_id or "")
        if self.last_clicked_id and self.last_clicked_id not in self.selected_ids:
            self.last_clicked_id = selected_order[-1] if selected_order else ""
        self.emit_selection()
        if hasattr(self, "_canvas"):
            self._canvas.update()
        self.layout_changed.emit()

    def _update_live_selection_label(self, ids: Optional[List[str]] = None):
        if not hasattr(self, "_live_sel_label"):
            return
        shown = ids if ids is not None else self._ordered_selected_ids()
        base_count = len(self._live_base_ids) if self._live_base_ids else len(shown)
        self._live_sel_label.setText(f"实时组：{len(shown)} 台 / 基础选区 {base_count} 台")

    def apply_selection_preset(self, mode: str):
        base, established = self._selection_filter_base()
        if not base:
            self._set_live_selection([], "TopView实时选择", [])
            return
        mode = str(mode or "all")
        # ★ 2026-08-04 用户语义定案:奇偶/左右半在**包级单元序**上操作——包为
        #   一个整体(包内灯同进退),散台逐台;选中单元展开为全部成员 id。
        #   单控(包内逐灯)只保留在 effect/matrix 效果应用路径,不参与选择。
        # ★ 2026-08-13: 已建立序(established)时奇偶/间隔/全部继承原序, 不再
        #   按坐标重排; 左/右半本身按位置语义重排, 不受影响。
        units = self._selection_units_from_base(base, sort_by_pos=not established)
        if mode == "odd":
            chosen = [u for i, u in enumerate(units) if i % 2 == 0]
            name = "TopView实时选择-奇数"
        elif mode == "even":
            chosen = [u for i, u in enumerate(units) if i % 2 == 1]
            name = "TopView实时选择-偶数"
        elif mode == "left":
            ordered = sorted(units, key=lambda u: (u["pos"][0], u["pos"][1]))
            chosen = ordered[:max(1, (len(ordered) + 1) // 2)]
            name = "TopView实时选择-左半"
        elif mode == "right":
            ordered = sorted(units, key=lambda u: (u["pos"][0], u["pos"][1]))
            chosen = ordered[(len(ordered) + 1) // 2:]
            name = "TopView实时选择-右半"
        else:
            chosen = list(units)
            name = "TopView实时选择-全部"
        out = [fid for u in chosen for fid in u["fids"]]
        mother = [fid for u in units for fid in u["fids"]]  # R195：mother=排序后单元序，奇偶相位同基
        self._set_live_selection(out, name, mother)

    def apply_selection_filter(self):
        base, established = self._selection_filter_base()
        # ★ 包级单元(2026-08-04 用户语义:间隔按包列表序,包内灯整体同进退)。
        # ★ 2026-08-13: 已建立序时继承原序过滤, 不按坐标重排。
        units = self._selection_units_from_base(base, sort_by_pos=not established)
        start = max(0, self._sel_start.value() - 1)
        take = max(1, self._sel_take.value())
        gap = max(0, self._sel_gap.value())
        chosen = []
        i = start
        while i < len(units):
            chosen.extend(units[i:i + take])
            i += take + gap
        out = [fid for u in chosen for fid in u["fids"]]
        name = f"TopView实时选择-从{start + 1}选{take}隔{gap}"
        mother = [fid for u in units for fid in u["fids"]]  # R195：mother=排序后单元序
        self._set_live_selection(out, name, mother)

    def _current_page_circle_shapes(self) -> List[dict]:
        """当前页可用于自动编组的圆 / 椭圆图案（CAD shapes）。"""
        page = self.current_page()
        out = []
        for s in (page.get("cad_shapes", []) or []):
            if str(s.get("type", "")) in ("circle", "ellipse"):
                out.append(s)
        return out

    def _autogroup_positions(self) -> Dict[str, Tuple[float, float]]:
        """自动编组的几何点集 = 当前页全部可见设备(圆结构识别需完整布局)。
        ★成员范围限选中由 AutoGroupEngine 的 scope 控制(见 auto_group_start),
        不在此裁剪点集——否则选中的几条弧拟合不出完整圆。"""
        pos: Dict[str, Tuple[float, float]] = {}
        for fid in self.fixture_ids():
            x, y = self.fixture_pos(fid)
            pos[str(fid)] = (float(x), float(y))
        return pos

    def auto_group_start(self):
        """启动多圆拓扑自动编组：构造引擎、生成候选、选中第一组。
        ★圆结构从整页布局识别(完整),成员范围限当前选中(scope)——选中的设备来自
        ≥2个圆即可编组(选中常是几条弧,拟合不出完整圆,故圆用全页识别)。无选中→全页。"""
        from .topview_autogroup import AutoGroupEngine
        positions = self._autogroup_positions()      # 全页几何(识圆)
        sel = self._ordered_selected_ids()           # 当前选中(作成员 scope)
        has_selection = bool(sel)
        if len(positions) < 4:
            QMessageBox.information(self, "自动编组",
                                    "当前页可见设备过少。请先拖入多圆布局的设备。")
            return
        shapes = self._current_page_circle_shapes()   # 整页 CAD 圆作识圆参照
        self._autogroup_seed = 0
        try:
            self._autogroup_engine = AutoGroupEngine(
                positions, shapes or None, scope_ids=(sel or None))
        except Exception as exc:
            QMessageBox.warning(self, "自动编组", f"自动编组初始化失败：{exc}")
            self._autogroup_engine = None
            return
        if not self._autogroup_engine.is_applicable():
            self._autogroup_engine = None
            self._autogroup_candidates = []
            if has_selection:
                msg = ("选中的设备需来自 ≥2 个圆。\n"
                       "请选中分属多个圆的设备(每圆可只选一段弧)后再试。")
            else:
                msg = ("需多圆布局：当前页未检测到 ≥2 个圆 / 图案。\n"
                       "请绘制 / 导入多个圆，或把设备摆成多圆相接后再试。")
            QMessageBox.information(self, "自动编组", msg)
            return
        self._autogroup_candidates = self._autogroup_engine.candidates(seed=0)
        self._autogroup_index = 0
        if not self._autogroup_candidates:
            ec.auto_group_no_candidates().show(self)
            return
        self._apply_current_autogroup_candidate()

    def auto_group_next(self):
        """换下一个候选；候选用尽则换随机种子翻新（同种子可复现）。"""
        if self._autogroup_engine is None:
            self.auto_group_start()
            return
        self._autogroup_index += 1
        if self._autogroup_index >= len(self._autogroup_candidates):
            # 候选用尽：换种子重新生成（确定性：同种子同结果）。
            for _ in range(8):
                self._autogroup_seed += 1
                cands = self._autogroup_engine.candidates(seed=self._autogroup_seed)
                if cands:
                    self._autogroup_candidates = cands
                    self._autogroup_index = 0
                    break
            else:
                QMessageBox.information(self, "自动编组", "已无更多候选编组。")
                self._autogroup_index = len(self._autogroup_candidates) - 1
                return
        self._apply_current_autogroup_candidate()

    def _apply_current_autogroup_candidate(self):
        """把当前候选选中到画布（保留推荐顺序，写入实时选择以便后续保存为组）。"""
        if not self._autogroup_candidates:
            return
        idx = max(0, min(self._autogroup_index, len(self._autogroup_candidates) - 1))
        cand = self._autogroup_candidates[idx]
        ids = [str(fid) for fid in getattr(cand, "member_ids", [])]
        name = f"TopView自动编组-{getattr(cand, 'name', '多圆')}"
        # 复用实时选择通道：成员顺序即推荐相位铺开顺序，保存时写入 base_order_fixture_ids。
        self._set_live_selection(ids, name, ids)
        self._live_selection_name = name
        if hasattr(self, "_live_sel_label"):
            self._live_sel_label.setText(
                f"自动编组 {idx + 1}/{len(self._autogroup_candidates)}（种子 {self._autogroup_seed}）："
                f"{getattr(cand, 'name', '')}\n{len(ids)} 台 / 跨 {getattr(cand, 'circle_count', 0)} 圆"
                f" · 点「换一组」翻新，「保存编组」存为设备组")

    def save_live_selection_to_fixture_group(self):
        ids = self._ordered_selected_ids()
        if not ids:
            ec.topview_no_selection().show(self)
            return
        base_order = list(ids)
        fixture_ids = self._unique_preserve_order(ids)
        base_order = _base_order_with_mother(self, ids)
        default_name = self._live_selection_name if self._live_selection_name and self._live_selection_name != "TopView实时选择" else f"TopView实时组-{self.current_page().get('name', 'Sheet')}"
        name, ok = QInputDialog.getText(self, "保存到 Fixture 设备组", "设备组名称:", text=default_name)
        if not ok:
            return
        name = name.strip() or default_name
        project = self._current_project()
        groups = list(getattr(self._pm, "fixture_groups", getattr(project, "fixture_groups", [])) or [])
        group_id = str(uuid.uuid4())
        group = {
            "id": group_id,
            "name": name,
            "color": "#2A7A5A",
            "fixture_ids": list(fixture_ids),
            "base_order_fixture_ids": list(base_order),
        }
        groups.append(group)
        try:
            if self._pm is not None and hasattr(self._pm, "fixture_groups"):
                self._pm.fixture_groups = groups
            else:
                setattr(project, "fixture_groups", groups)
            # ★★ R121：保存设备组**不得清掉当前选择**（旧实现下发空集 → 下游作用域
            #   被清空 → 设值/存场景全落空）。保存=另存组定义，选择不变。
            self._active_group_id = ""
            cb = getattr(self, "_group_combo", None)
            if cb is not None:
                cb.blockSignals(True); cb.setCurrentIndex(0); cb.blockSignals(False)
            self.fixture_group_saved.emit(group_id, list(fixture_ids), name, list(base_order))
            self._refresh_groups()
            if hasattr(self, "_canvas"):
                self._canvas.update()
            QMessageBox.information(self, "保存完成", f"已保存为 Fixture 设备组：{name}（{len(fixture_ids)} 台 / 序列 {len(base_order)}）")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存实时选择组：\n{e}")

    def emit_selection(self):
        ids = self._ordered_selected_ids()
        self._update_live_selection_label(ids)
        self._update_xy_reference_label()
        _persist_topview_state(self)
        self.selection_changed.emit(ids)
    def get_fixture_positions(self) -> list[dict]:
        out=[]; fixtures=self.fixtures_by_id()
        for fid in self.fixture_ids():
            x,y=self.fixture_pos(fid); fi=fixtures.get(fid); out.append({"id":fid,"fixture_id":fid,"name":getattr(fi,"name",fid),"universe":getattr(fi,"universe",1),"start_channel":getattr(fi,"start_channel",1),"pos_x":x,"pos_y":y,"x":x,"y":y,"selected":fid in self.selected_ids,"page_id":self.current_page().get("id")})
        return out

    def get_all_fixture_positions(self) -> list[dict]:
        """返回所有已配接设备的位置信息（不限于当前 Sheet 的 visible_fixture_ids）。

        优先使用设备在各 Sheet 中的实际坐标；若某设备未出现在任何 Sheet，
        则使用其 FixtureInstance.pos_x/pos_y 作为兜底坐标。
        这样 Matrix 面板在设备配接后即可识别到全部设备，无需先拖入 TopView。
        """
        fixtures = self.fixtures_by_id()
        all_fids = list(fixtures.keys())
        if not all_fids:
            return []
        # 收集各 Sheet 中每个设备的最后一次坐标（优先当前 Sheet）
        pos_map: dict = {}
        # 先扫描所有 Sheet（非当前 Sheet）
        for page in self._pages:
            fpos = page.get("fixtures", {}) or {}
            for fid, v in fpos.items():
                if fid not in pos_map:
                    pos_map[fid] = (_safe_float(v.get("x")), _safe_float(v.get("y")))
        # 再用当前 Sheet 的坐标覆盖（优先级最高）
        cur_fpos = self.current_page().get("fixtures", {}) or {}
        for fid, v in cur_fpos.items():
            pos_map[fid] = (_safe_float(v.get("x")), _safe_float(v.get("y")))
        out = []
        for fid in all_fids:
            fi = fixtures.get(fid)
            if fid in pos_map:
                x, y = pos_map[fid]
            else:
                # 兜底：使用 FixtureInstance 自带坐标
                x = _safe_float(getattr(fi, "pos_x", 0.0))
                y = _safe_float(getattr(fi, "pos_y", 0.0))
            out.append({"id": fid, "fixture_id": fid,
                        "name": getattr(fi, "name", fid),
                        "universe": getattr(fi, "universe", 1),
                        "start_channel": getattr(fi, "start_channel", 1),
                        "pos_x": x, "pos_y": y, "x": x, "y": y,
                        "selected": fid in self.selected_ids,
                        "page_id": self.current_page().get("id")})
        return out

    def get_fixture_positions_for_sheet(self, sheet_name: str) -> list[dict]:
        """返回指定 Sheet 中已放置设备的位置信息，用于 Matrix 面板按 Sheet 导入布局。"""
        fixtures = self.fixtures_by_id()
        for page in self._pages:
            if page.get("name", "") == sheet_name:
                visible = page.get("visible_fixture_ids", [])
                fpos = page.get("fixtures", {}) or {}
                out = []
                for fid in visible:
                    fi = fixtures.get(str(fid))
                    if fi is None:
                        continue
                    v = fpos.get(str(fid), {})
                    x, y = _safe_float(v.get("x")), _safe_float(v.get("y"))
                    out.append({"id": str(fid), "fixture_id": str(fid),
                                "name": getattr(fi, "name", str(fid)),
                                "universe": getattr(fi, "universe", 1),
                                "start_channel": getattr(fi, "start_channel", 1),
                                "pos_x": x, "pos_y": y, "x": x, "y": y,
                                "selected": str(fid) in self.selected_ids,
                                "page_id": page.get("id")})
                return out
        return []

    def get_sheet_names(self) -> list[str]:
        """返回所有 Sheet 页面名称列表。"""
        return [page.get("name", f"Sheet{i+1}") for i, page in enumerate(self._pages)]
    def save_state(self) -> dict:
        if hasattr(self, "_layout_spacing_slider"):
            try:
                self.current_page()["layout_group_spacing_percent"] = self._layout_spacing_percent()
            except Exception:
                pass
        return {
            "pages": self._pages,
            "active_page": self._active_page,
            "selected_ids": list(self._ordered_selected_ids()),
            "selection_order_ids": list(self._ordered_selected_ids()),
            "active_group_id": self._active_group_id,
            # ★★★ R165 可追溯（用户："可追溯的子集和子集之间的 delay 相位"）：
            #   以前只存了**子集**，母集合（`_live_base_ids`）完全没存 → 工程重载 /
            #   撤销 / 切页签后母集合丢失，`_topview_base_order_ids` 静默退化成
            #   "母集合 = 子集"，于是"母集合 N 台里奇偶错相"这种语义再也复现不出来。
            #   这里把母集合与派生名称一并持久化（都是自由 dict 键，旧工程缺键走
            #   默认值，天然向后兼容）。
            "live_base_ids": list(self._live_base_ids),
            "live_selection_ids": list(self._live_selection_ids),
            "live_selection_name": str(self._live_selection_name or ""),
            "xy_reference_by_fixture": dict(self._xy_reference_by_fixture),
            "xy_reference_global_angle_deg": float(self._xy_reference_global_angle_deg),
            "z_angle_deg": float(self._xy_reference_z_angle_deg),
        }
    def load_state(self, state: dict):
        # 见 __init__：记下这次灌入来自哪个工程，作为回写许可的判据
        self._hydrated_for = getattr(self, "_project", None)
        pages = state.get("pages") if isinstance(state, dict) else None
        self._pages = pages if isinstance(pages, list) and pages else [{"id":"page-1","name":"Sheet1","fixtures":{},"cad_shapes":[],"pattern_matrices":[]}]
        self._active_page = int(state.get("active_page", 0) if isinstance(state, dict) else 0)
        raw_sel = [str(x) for x in (state.get("selected_ids", []) if isinstance(state, dict) else [])]
        raw_order = [str(x) for x in (state.get("selection_order_ids", []) if isinstance(state, dict) else [])]
        self._selection_order_ids = raw_order or raw_sel
        self.selected_ids = set(raw_sel or raw_order)
        self._reset_box_selection_chain()
        self._active_group_id = str(state.get("active_group_id", "") if isinstance(state, dict) else "")
        _restore_live_selection(self, state)   # ★ R165 可追溯：恢复母集合
        raw_xy_ref = state.get("xy_reference_by_fixture", {}) if isinstance(state, dict) else {}
        self._xy_reference_global_angle_deg = _normalize_angle_deg(
            _safe_float(state.get("xy_reference_global_angle_deg", 0.0), 0.0) if isinstance(state, dict) else 0.0
        )
        self._xy_reference_z_angle_deg = _normalize_angle_deg(
            _safe_float(state.get("z_angle_deg", 0.0), 0.0) if isinstance(state, dict) else 0.0
        )
        self._xy_reference_by_fixture = _normalize_xy_ref(self, raw_xy_ref)
        # ★ 2D XY 同步基准符号重算（2026-08-10 用户「复用三维同步代码」修正）：
        #   base 语义改为 −真实安装（顶部 ry−180 / 下半弧 −ry，引擎非统一分支
        #   rad = base+θ → 世界 = θ 全组统一）。旧存档（9a0d121 时代）base =
        #   +真实安装（180−ry / ry）→ 恢复时按 _xy_install_angle 重算并强制
        #   非统一（引擎 build 对无 motor_z 且非 x_only 亦强制 uniform=False
        #   双保险）；三维扇子 / 尖点 X-only 原样保留。
        from .topview_sync_context import restored_contexts
        contexts = restored_contexts(self, self._xy_reference_by_fixture)
        for _fid, _cfg in list(self._xy_reference_by_fixture.items()):
            if self._fixture_is_2d_xy(str(_fid)):
                context_ids = contexts.get(_fid, [])
                _cfg["uniform_world_angle"] = False
                _cfg["base_angle_deg"] = float(self._xy_install_angle(
                    str(_fid), _safe_float(_cfg.get("base_angle_deg", 0.0), 0.0), context_ids))
                _cfg["angle_deg"] = float(self._sync_angle_with_global(_cfg["base_angle_deg"], _cfg))
        # ★ 恢复后立即修剪：旧工程保存的 sync 条目可能含已删除设备/非 XY 简化电机
        #   （def 修正后二仪/喷头）——不校验恢复会让它们继续旋转并再次持久化
        #   （审查 R4）。
        self._prune_invalid_sync_references()
        if hasattr(self, "_xy_ref_global_angle"):
            old = self._xy_ref_global_angle.blockSignals(True)
            self._xy_ref_global_angle.setValue(self._xy_reference_global_angle_deg)
            self._xy_ref_global_angle.blockSignals(old)
        if hasattr(self, "_xy_ref_z_angle"):
            old = self._xy_ref_z_angle.blockSignals(True)
            self._xy_ref_z_angle.setValue(self._xy_reference_z_angle_deg)
            self._xy_ref_z_angle.blockSignals(old)
        self._prune_selection_to_visible(emit=False, scope=self._all_pages_visible_ids())
        _heal_misplaced_snap_coords(self)
        self._clear_canvas_transients() if hasattr(self, "_canvas") else None
        self._set_layout_spacing_ui_from_page()
        if hasattr(self, "_tabs"): self._refresh_tabs()
        if hasattr(self, "_group_combo"): self._refresh_groups()
        self.emit_selection()
        self._emit_xy_reference_changed()
        if hasattr(self, "_canvas"):
            self._canvas._spatial_index_dirty = True
            self._canvas._static_cache = None
            self._canvas.update()

    def _ensure_openpyxl(self):
        """R183/F2 (T9)：探测 openpyxl；缺失时**不再弹错误框**（走 csv 降级）。

        旧实现在缺库时弹「缺少 Excel 支持」并返回 None → 「导出/导入布局表」
        两个按钮变成纯报错（真机 venv 未装 openpyxl = 隐形不可用）。现在返回
        None 只表示"该走 csv 通路"（见 `topview_layout_io`），由它按后缀与探测
        结果决定落 .xlsx 还是 .csv，并在状态栏提示实际走的哪条。
        """
        from .table_io_fallback import openpyxl_pack
        return openpyxl_pack()

    def _export_layout_excel(self):
        """R183/F2 (T9)：本体搬到 `topview_layout_io.export_layout_table`（xlsx/csv）。"""
        from .topview_layout_io import export_layout_table
        return export_layout_table(self)

    def _import_layout_excel(self):
        """R183/F2 (T9)：本体搬到 `topview_layout_io.import_layout_table`（xlsx/csv）。"""
        from .topview_layout_io import import_layout_table
        return import_layout_table(self)
