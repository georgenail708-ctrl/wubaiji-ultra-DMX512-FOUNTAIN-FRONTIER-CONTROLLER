# -*- coding: utf-8 -*-
"""
topview_cad.py — TopView CAD 绘图工具模块 (v2)
================================================
修复：
  - setBrush(Qt.BrushStyle.NoBrush) TypeError → 改用 QBrush()
  - 全面重构 UI，工具栏清晰，功能完整

支持图形类型：直线、折线、圆、弧线、椭圆、矩形、多边形、贝塞尔曲线、自由曲线
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QPen, QBrush, QPainterPath, QPainter, QFont,
    QPolygonF, QCursor,
)
from PyQt6.QtWidgets import (
    QGraphicsItem, QGraphicsPathItem,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QDoubleSpinBox, QGroupBox, QCheckBox,
    QColorDialog, QButtonGroup, QFrame, QSizePolicy,
    QScrollArea, QToolButton, QSpinBox, QStackedWidget,
)

from . import qascade_theme as _th
from .tabler_button import tabler_button
from .widget_kit import prop_row

# ─── 图形类型常量 ─────────────────────────────────────────────────────────────
SHAPE_LINE      = "line"
SHAPE_POLYLINE  = "polyline"
SHAPE_CIRCLE    = "circle"
SHAPE_ARC       = "arc"
SHAPE_ELLIPSE   = "ellipse"
SHAPE_RECT      = "rectangle"
SHAPE_POLYGON   = "polygon"
SHAPE_BEZIER    = "bezier"
SHAPE_FREEHAND  = "freehand"

SHAPE_LABELS = {
    SHAPE_LINE:     "直线",
    SHAPE_POLYLINE: "折线",
    SHAPE_CIRCLE:   "圆",
    SHAPE_ARC:      "弧线",
    SHAPE_ELLIPSE:  "椭圆",
    SHAPE_RECT:     "矩形",
    SHAPE_POLYGON:  "多边形",
    SHAPE_BEZIER:   "贝塞尔",
    SHAPE_FREEHAND: "手绘",
}

SHAPE_ICONS = {
    SHAPE_LINE:     "╱",
    SHAPE_POLYLINE: "⌇",
    SHAPE_CIRCLE:   "○",
    SHAPE_ARC:      "⌒",
    SHAPE_ELLIPSE:  "⬭",
    SHAPE_RECT:     "▭",
    SHAPE_POLYGON:  "⬠",
    SHAPE_BEZIER:   "∿",
    SHAPE_FREEHAND: "✏",
}

# 绘图操作提示
SHAPE_HINTS = {
    SHAPE_LINE:     "点击起点 → 点击终点",
    SHAPE_POLYLINE: "逐点点击 → 双击完成",
    SHAPE_CIRCLE:   "点击圆心 → 点击半径点",
    SHAPE_ARC:      "点击圆心 → 半径点 → 结束点",
    SHAPE_ELLIPSE:  "点击中心 → 点击角点",
    SHAPE_RECT:     "点击对角两点",
    SHAPE_POLYGON:  "逐点点击 → 双击闭合",
    SHAPE_BEZIER:   "起点→控制点1→控制点2→终点",
    SHAPE_FREEHAND: "按住鼠标拖动",
}

DEFAULT_PATH_COLOR  = "#44AAFF"
DEFAULT_GUIDE_COLOR = "#335566"


# ═══════════════════════════════════════════════════════════════════════════════
#  数据模型
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CadShape:
    """一个 CAD 图形的数据模型"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    shape_type: str = SHAPE_LINE
    points: List[Tuple[float, float]] = field(default_factory=list)
    center: Tuple[float, float] = (0.0, 0.0)
    radius: float = 100.0
    radius_y: float = 0.0
    start_angle: float = 0.0
    span_angle: float = 360.0
    color: str = DEFAULT_PATH_COLOR
    line_width: float = 2.0
    is_guide: bool = False
    name: str = ""
    control_points: List[Tuple[float, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "shape_type": self.shape_type,
            "points": list(self.points), "center": list(self.center),
            "radius": self.radius, "radius_y": self.radius_y,
            "start_angle": self.start_angle, "span_angle": self.span_angle,
            "color": self.color, "line_width": self.line_width,
            "is_guide": self.is_guide, "name": self.name,
            "control_points": list(self.control_points),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CadShape":
        s = cls()
        s.id = d.get("id", str(uuid.uuid4())[:8])
        s.shape_type = d.get("shape_type", SHAPE_LINE)
        s.points = [tuple(p) for p in d.get("points", [])]
        s.center = tuple(d.get("center", [0.0, 0.0]))
        s.radius = float(d.get("radius", 100.0))
        s.radius_y = float(d.get("radius_y", 0.0))
        s.start_angle = float(d.get("start_angle", 0.0))
        s.span_angle = float(d.get("span_angle", 360.0))
        s.color = d.get("color", DEFAULT_PATH_COLOR)
        s.line_width = float(d.get("line_width", 2.0))
        s.is_guide = bool(d.get("is_guide", False))
        s.name = d.get("name", "")
        s.control_points = [tuple(p) for p in d.get("control_points", [])]
        return s

    def get_path_points(self, num_points: int = 100) -> List[Tuple[float, float]]:
        """将图形转换为等距采样点，用于设备分布。"""
        num_points = max(2, num_points)

        if self.shape_type == SHAPE_LINE:
            if len(self.points) < 2:
                return []
            p0, p1 = self.points[0], self.points[1]
            return [(p0[0] + (p1[0]-p0[0])*t/(num_points-1),
                     p0[1] + (p1[1]-p0[1])*t/(num_points-1))
                    for t in range(num_points)]

        elif self.shape_type in (SHAPE_POLYLINE, SHAPE_FREEHAND):
            return self._sample_segments(self.points, num_points)

        elif self.shape_type == SHAPE_CIRCLE:
            cx, cy = self.center
            r = self.radius
            return [(cx + r * math.cos(2*math.pi*t/num_points),
                     cy + r * math.sin(2*math.pi*t/num_points))
                    for t in range(num_points)]

        elif self.shape_type == SHAPE_ARC:
            cx, cy = self.center
            r = self.radius
            start = math.radians(self.start_angle)
            span = math.radians(self.span_angle)
            return [(cx + r * math.cos(start + span*t/(num_points-1)),
                     cy + r * math.sin(start + span*t/(num_points-1)))
                    for t in range(num_points)]

        elif self.shape_type == SHAPE_ELLIPSE:
            cx, cy = self.center
            rx = self.radius
            ry = self.radius_y if self.radius_y > 0 else rx
            return [(cx + rx * math.cos(2*math.pi*t/num_points),
                     cy + ry * math.sin(2*math.pi*t/num_points))
                    for t in range(num_points)]

        elif self.shape_type == SHAPE_RECT:
            if len(self.points) < 2:
                return []
            p0, p1 = self.points[0], self.points[1]
            x0, y0 = min(p0[0], p1[0]), min(p0[1], p1[1])
            x1, y1 = max(p0[0], p1[0]), max(p0[1], p1[1])
            perimeter = 2*(x1-x0) + 2*(y1-y0)
            if perimeter < 1e-6:
                return [(x0, y0)] * num_points
            result = []
            for i in range(num_points):
                d = perimeter * i / max(1, num_points-1)
                w, h = x1-x0, y1-y0
                if d <= w:
                    result.append((x0+d, y0))
                elif d <= w+h:
                    result.append((x1, y0+(d-w)))
                elif d <= 2*w+h:
                    result.append((x1-(d-w-h), y1))
                else:
                    result.append((x0, y1-(d-2*w-h)))
            return result

        elif self.shape_type == SHAPE_POLYGON:
            if len(self.points) < 3:
                return self._sample_segments(self.points, num_points)
            closed = list(self.points) + [self.points[0]]
            return self._sample_segments(closed, num_points)

        elif self.shape_type == SHAPE_BEZIER:
            return self._sample_bezier(num_points)

        return []

    def _sample_segments(self, pts: list, num_points: int) -> List[Tuple[float, float]]:
        if len(pts) < 2:
            return [pts[0]] * num_points if pts else []
        lengths = []
        total = 0.0
        for i in range(len(pts)-1):
            dx, dy = pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1]
            seg = math.sqrt(dx*dx + dy*dy)
            lengths.append(seg)
            total += seg
        if total < 1e-6:
            return [pts[0]] * num_points
        result = []
        for i in range(num_points):
            target = total * i / max(1, num_points-1)
            accum = 0.0
            for si, seg_len in enumerate(lengths):
                if accum + seg_len >= target or si == len(lengths)-1:
                    t = (target - accum) / max(1e-6, seg_len)
                    t = max(0.0, min(1.0, t))
                    x = pts[si][0] + (pts[si+1][0]-pts[si][0]) * t
                    y = pts[si][1] + (pts[si+1][1]-pts[si][1]) * t
                    result.append((x, y))
                    break
                accum += seg_len
        return result

    def _sample_bezier(self, num_points: int) -> List[Tuple[float, float]]:
        pts = self.points
        cps = self.control_points
        if len(pts) < 2:
            return list(pts) * num_points if pts else []
        p0, p3 = pts[0], pts[-1]
        if len(cps) >= 2:
            p1, p2 = cps[0], cps[1]
        elif len(cps) == 1:
            p1 = cps[0]
            p2 = ((p0[0]+p3[0])/2, (p0[1]+p3[1])/2)
        else:
            p1 = (p0[0]+(p3[0]-p0[0])/3, p0[1]+(p3[1]-p0[1])/3)
            p2 = (p0[0]+2*(p3[0]-p0[0])/3, p0[1]+2*(p3[1]-p0[1])/3)
        result = []
        for i in range(num_points):
            t = i / max(1, num_points-1)
            u = 1.0 - t
            x = u**3*p0[0] + 3*u**2*t*p1[0] + 3*u*t**2*p2[0] + t**3*p3[0]
            y = u**3*p0[1] + 3*u**2*t*p1[1] + 3*u*t**2*p2[1] + t**3*p3[1]
            result.append((x, y))
        return result


# ═══════════════════════════════════════════════════════════════════════════════
#  图形 QGraphicsItem
# ═══════════════════════════════════════════════════════════════════════════════

class CadShapeItem(QGraphicsPathItem):
    """CAD 图形的 QGraphicsItem 表示"""

    def __init__(self, shape: CadShape, parent=None):
        super().__init__(parent)
        self.shape_data = shape
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setAcceptHoverEvents(True)
        self._hovered = False
        self._rebuild_path()

    def _rebuild_path(self):
        s = self.shape_data
        path = QPainterPath()

        if s.shape_type == SHAPE_LINE and len(s.points) >= 2:
            path.moveTo(s.points[0][0], s.points[0][1])
            path.lineTo(s.points[1][0], s.points[1][1])

        elif s.shape_type in (SHAPE_POLYLINE, SHAPE_FREEHAND) and len(s.points) >= 2:
            path.moveTo(s.points[0][0], s.points[0][1])
            for p in s.points[1:]:
                path.lineTo(p[0], p[1])

        elif s.shape_type == SHAPE_CIRCLE:
            cx, cy = s.center
            r = s.radius
            path.addEllipse(QPointF(cx, cy), r, r)

        elif s.shape_type == SHAPE_ARC:
            cx, cy = s.center
            r = max(1.0, s.radius)
            rect = QRectF(cx-r, cy-r, 2*r, 2*r)
            path.arcMoveTo(rect, s.start_angle)
            path.arcTo(rect, s.start_angle, s.span_angle)

        elif s.shape_type == SHAPE_ELLIPSE:
            cx, cy = s.center
            rx = max(1.0, s.radius)
            ry = max(1.0, s.radius_y if s.radius_y > 0 else rx)
            path.addEllipse(QPointF(cx, cy), rx, ry)

        elif s.shape_type == SHAPE_RECT and len(s.points) >= 2:
            p0, p1 = s.points[0], s.points[1]
            x0, y0 = min(p0[0], p1[0]), min(p0[1], p1[1])
            w, h = abs(p1[0]-p0[0]), abs(p1[1]-p0[1])
            if w > 0 and h > 0:
                path.addRect(x0, y0, w, h)

        elif s.shape_type == SHAPE_POLYGON and len(s.points) >= 3:
            path.moveTo(s.points[0][0], s.points[0][1])
            for p in s.points[1:]:
                path.lineTo(p[0], p[1])
            path.closeSubpath()

        elif s.shape_type == SHAPE_BEZIER and len(s.points) >= 2:
            p0 = s.points[0]
            p3 = s.points[-1]
            cps = s.control_points
            path.moveTo(p0[0], p0[1])
            if len(cps) >= 2:
                path.cubicTo(cps[0][0], cps[0][1],
                             cps[1][0], cps[1][1],
                             p3[0], p3[1])
            elif len(cps) == 1:
                path.quadTo(cps[0][0], cps[0][1], p3[0], p3[1])
            else:
                path.lineTo(p3[0], p3[1])

        self.setPath(path)
        self._update_pen()

    def _update_pen(self):
        s = self.shape_data
        color = QColor(s.color)
        if s.is_guide:
            color.setAlphaF(0.45)
        lw = s.line_width
        pen = QPen(color, lw)
        pen.setCosmetic(False)
        if s.is_guide:
            pen.setStyle(Qt.PenStyle.DashLine)
        if self._hovered or self.isSelected():
            pen.setWidthF(lw + 2.0)
            pen.setColor(QColor("#FFFFFF"))
        self.setPen(pen)
        # ★ 修复：使用 QBrush() 而非 Qt.BrushStyle.NoBrush
        self.setBrush(QBrush())

    def refresh(self):
        self._rebuild_path()

    def hoverEnterEvent(self, event):
        self._hovered = True
        self._update_pen()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self._update_pen()
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._update_pen()
        return super().itemChange(change, value)


# ═══════════════════════════════════════════════════════════════════════════════
#  CAD 工具面板（重构 UI）
# ═══════════════════════════════════════════════════════════════════════════════

# 本地面板主题常量已并入全局 主题令牌（restyle_spec §1/§2/§3）：
# 夜蓝底/紫灰字全部退役；_ORANGE 死常量按审计意见清除。
_DARK = _th.BG_ROOT          # 原 #1e1e1e
_PANEL = _th.BG_DEEP         # 原 #13131F 夜蓝容器
_BORDER = _th.BORDER_NORMAL  # 原 #252540
_ACCENT = _th.FOCUS_BLUE     # 原 #4A90E2（accent 蓝描边/文字→focus_blue）
_GREEN = _th.BRIGHT_SUCCESS  # 原 #44CC88（文字级绿）
_RED = _th.BRIGHT_ERROR      # 原 #E24444（文字级红）
_FG = _th.TEXT_PRIMARY       # 原 #D0D0E8
_DIM = _th.TEXT_SECONDARY    # 原 #6070A0

# ★ D 波统一（restyle_spec §10/B）：工具按钮行高统一 26px；
#   常规工具按钮外观交给全局 GLOBAL_QSS（QPushButton 四态含 :checked），
#   页面仅保留语义色按钮（绿=分布选中/蓝=分布全部/红=删除）——§3 允许的语义色容器。
_BTN_H = 26

_BTN_GREEN = (
    f"QPushButton{{background:{_th.SUCCESS_GREEN};color:{_th.TEXT_SELECTED_FG};"
    f"border:1px solid {_th.SUCCESS_GREEN};border-radius:4px;"
    "padding:2px 10px;font-size:11px;font-weight:bold;"
    f"min-height:{_BTN_H}px;}}"
    f"QPushButton:hover{{background:{_th.BRIGHT_SUCCESS};border-color:{_th.BRIGHT_SUCCESS};}}"
    f"QPushButton:pressed{{background:{_th.ANIM_GREEN};color:{_th.TEXT_SELECTED_FG};}}"
    f"QPushButton:disabled{{background:#303030;color:{_th.TEXT_DISABLED};border-color:#262626;}}"
)
_BTN_BLUE = (
    f"QPushButton{{background:{_th.INFO_BLUE};color:{_th.TEXT_PRIMARY};"
    f"border:1px solid {_th.INFO_BLUE};border-radius:4px;"
    "padding:2px 10px;font-size:11px;"
    f"min-height:{_BTN_H}px;}}"
    f"QPushButton:hover{{background:{_th.BRIGHT_INFO};color:{_th.TEXT_SELECTED_FG};"
    f"border-color:{_th.BRIGHT_INFO};}}"
)
_BTN_RED = (
    f"QPushButton{{background:{_th.ERROR_RED};color:{_th.TEXT_SELECTED_FG};"
    f"border:1px solid {_th.ERROR_RED};border-radius:4px;"
    "padding:2px 10px;font-size:11px;"
    f"min-height:{_BTN_H}px;}}"
    f"QPushButton:hover{{background:{_th.BRIGHT_ERROR};border-color:{_th.BRIGHT_ERROR};}}"
)
# 下拉/数字框：全局 GLOBAL_QSS 已提供四态外观，这里只保留字号与内距的局部特化。
_COMBO = (
    f"QComboBox{{font-size:11px;padding:2px 6px;min-height:{_th.MIN_CONTROL_HEIGHT - 4}px;}}"
)
_SPIN = (
    f"QDoubleSpinBox,QSpinBox{{font-size:11px;padding:2px;min-height:{_th.MIN_CONTROL_HEIGHT - 4}px;}}"
)


class CadToolPanel(QWidget):
    """CAD 绘图工具面板 — 重构版，清晰易用"""

    shape_added = pyqtSignal(object)          # CadShape
    shape_deleted = pyqtSignal(str)           # shape_id（设计预留, 无消费方）
    distribute_requested = pyqtSignal(str, int, bool)  # shape_id, num, use_selected（设计预留, 无消费方）
    tool_changed = pyqtSignal(str)            # 当前工具名（""=选择模式）（设计预留, 无消费方）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_tool = ""
        self._shapes: List[CadShape] = []
        self._current_color = DEFAULT_PATH_COLOR
        self.setMinimumWidth(220)
        self.setMaximumWidth(260)
        self.setStyleSheet(f"background:{_DARK};color:{_FG};")
        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────────
    #  UI 构建
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── 标题 ──────────────────────────────────────────────────────────────
        title = QLabel("CAD 绘图工具")
        title.setStyleSheet(
            f"color:{_ACCENT};font-size:13px;font-weight:bold;"
            f"padding:6px 0 4px 0;border-bottom:1px solid {_BORDER};")
        root.addWidget(title)

        # ── 绘图工具选择 ──────────────────────────────────────────────────────
        # 分组框外观（含标题）交给全局 GLOBAL_QSS（restyle_spec §3 去重）。
        tools_grp = QGroupBox("绘图工具")
        tg_lay = QVBoxLayout(tools_grp)
        tg_lay.setContentsMargins(6, 12, 6, 6)
        tg_lay.setSpacing(4)

        # 第一行：基础图形
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        for st in [SHAPE_LINE, SHAPE_POLYLINE, SHAPE_RECT, SHAPE_CIRCLE]:
            btn = self._make_tool_btn(st)
            row1.addWidget(btn)
        tg_lay.addLayout(row1)

        # 第二行：高级图形
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        for st in [SHAPE_ARC, SHAPE_ELLIPSE, SHAPE_POLYGON, SHAPE_BEZIER, SHAPE_FREEHAND]:
            btn = self._make_tool_btn(st)
            row2.addWidget(btn)
        tg_lay.addLayout(row2)

        # 操作提示
        self._hint_lbl = QLabel("选择工具后在画布上点击绘制")
        self._hint_lbl.setStyleSheet(
            f"color:{_th.TEXT_SECONDARY};font-size:10px;padding:3px 2px;"
            f"background:{_th.BG_INPUT};border-radius:3px;")
        self._hint_lbl.setWordWrap(True)
        tg_lay.addWidget(self._hint_lbl)

        # 选择/移动模式按钮（外观走全局 QPushButton 四态，:checked=select_blue）
        sel_btn = QPushButton("⊹  选择 / 移动设备")
        sel_btn.setCheckable(True)
        sel_btn.setChecked(True)
        sel_btn.setFixedHeight(_BTN_H)
        sel_btn.setToolTip("进入选择/移动模式：画布点击选中设备，选中图形可拖拽")
        sel_btn.clicked.connect(lambda: self._on_tool_selected(""))
        self._sel_btn = sel_btn
        tg_lay.addWidget(sel_btn)

        root.addWidget(tools_grp)

        # ── 画笔属性 ──────────────────────────────────────────────────────────
        pen_grp = QGroupBox("画笔属性")
        pg_lay = QVBoxLayout(pen_grp)
        pg_lay.setContentsMargins(6, 12, 6, 6)
        pg_lay.setSpacing(6)

        # 颜色 + 线宽（prop_row 行版式，restyle_spec §10.1）
        ink_w = QWidget()
        color_row = QHBoxLayout(ink_w)
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(6)
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(36, _BTN_H)
        self._color_btn.setStyleSheet(
            f"background:{self._current_color};border:2px solid {_th.BG_BUTTON_HOVER};"
            f"border-radius:4px;")
        self._color_btn.setToolTip("点击选择画笔颜色")
        self._color_btn.clicked.connect(self._pick_color)
        color_row.addWidget(self._color_btn)
        color_row.addStretch()
        lw_lbl = QLabel("线宽:")
        lw_lbl.setStyleSheet(f"color:{_DIM};font-size:10px;")
        color_row.addWidget(lw_lbl)
        self._lw_spin = QDoubleSpinBox()
        self._lw_spin.setRange(0.5, 10.0)
        self._lw_spin.setValue(2.0)
        self._lw_spin.setSingleStep(0.5)
        self._lw_spin.setFixedWidth(58)
        self._lw_spin.setStyleSheet(_SPIN)
        self._lw_spin.setToolTip("新绘图形的线宽")
        color_row.addWidget(self._lw_spin)
        pg_lay.addWidget(prop_row("颜色", ink_w, label_width=34))

        # 辅助线选项
        self._guide_cb = QCheckBox("设为辅助线（虚线，不参与设备分布）")
        self._guide_cb.setToolTip("辅助线仅作参考显示；分布路径下拉中不会出现")
        self._guide_cb.setStyleSheet(
            f"color:{_DIM};font-size:10px;spacing:4px;")
        pg_lay.addWidget(self._guide_cb)

        root.addWidget(pen_grp)

        # ── 设备分布 ──────────────────────────────────────────────────────────
        dist_grp = QGroupBox("设备分布到路径")
        dg_lay = QVBoxLayout(dist_grp)
        dg_lay.setContentsMargins(6, 12, 6, 6)
        dg_lay.setSpacing(6)

        # 路径选择
        self._path_combo = QComboBox()
        self._path_combo.setStyleSheet(_COMBO)
        self._path_combo.setToolTip("选择要把设备分布上去的路径（辅助线不可选）")
        dg_lay.addWidget(prop_row("路径", self._path_combo, label_width=34))

        # 分布数量
        self._num_spin = QSpinBox()
        self._num_spin.setRange(0, 1000)
        self._num_spin.setValue(0)
        self._num_spin.setSpecialValueText("全部设备")
        self._num_spin.setStyleSheet(_SPIN)
        self._num_spin.setToolTip("每条路径上分布的设备数；0 = 全部设备")
        dg_lay.addWidget(prop_row("数量", self._num_spin, label_width=34))

        # 分布按钮
        dist_sel_btn = QPushButton("⊹  将选中设备分布到路径")
        dist_sel_btn.setStyleSheet(_BTN_GREEN)
        dist_sel_btn.setFixedHeight(_BTN_H)
        dist_sel_btn.setToolTip("将画布中已选中的设备等距分布到所选路径上")
        dist_sel_btn.clicked.connect(lambda: self._on_distribute(True))
        dg_lay.addWidget(dist_sel_btn)

        dist_all_btn = QPushButton("⊹  全部设备均匀分布")
        dist_all_btn.setStyleSheet(_BTN_BLUE)
        dist_all_btn.setFixedHeight(_BTN_H)
        dist_all_btn.setToolTip("将所有设备均匀分布到所选路径上")
        dist_all_btn.clicked.connect(lambda: self._on_distribute(False))
        dg_lay.addWidget(dist_all_btn)

        root.addWidget(dist_grp)

        # ── 图形列表 ──────────────────────────────────────────────────────────
        list_grp = QGroupBox("已绘制图形")
        lg_lay = QVBoxLayout(list_grp)
        lg_lay.setContentsMargins(6, 12, 6, 6)
        lg_lay.setSpacing(4)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(120)
        scroll.setStyleSheet(
            f"QScrollArea{{border:1px solid {_th.BORDER_NORMAL};border-radius:4px;"
            f"background:{_th.BG_DEEP};}}"
            f"QScrollBar:vertical{{background:transparent;width:6px;}}"
            f"QScrollBar::handle:vertical{{background:{_th.SCROLLBAR_HANDLE};border-radius:3px;}}")
        self._list_container = QWidget()
        self._list_container.setStyleSheet(f"background:{_PANEL};")
        self._list_vlay = QVBoxLayout(self._list_container)
        self._list_vlay.setContentsMargins(4, 4, 4, 4)
        self._list_vlay.setSpacing(2)
        self._list_vlay.addStretch()
        scroll.setWidget(self._list_container)
        lg_lay.addWidget(scroll)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        del_btn = tabler_button("删除选中", "trash", _th.TEXT_SELECTED_FG)
        del_btn.setStyleSheet(_BTN_RED)
        del_btn.setFixedHeight(_BTN_H)
        del_btn.setToolTip("删除上方下拉框当前选中的路径图形")
        del_btn.clicked.connect(self._on_delete_selected)
        btn_row.addWidget(del_btn)
        clear_btn = tabler_button("清空全部", "trash-x", _th.TEXT_SELECTED_FG)
        clear_btn.setStyleSheet(_BTN_RED)
        clear_btn.setFixedHeight(_BTN_H)
        clear_btn.setToolTip("删除本面板记录的全部 CAD 图形（含辅助线）")
        clear_btn.clicked.connect(self._on_clear_all)
        btn_row.addWidget(clear_btn)
        lg_lay.addLayout(btn_row)

        root.addWidget(list_grp)
        root.addStretch()

    def _make_tool_btn(self, shape_type: str) -> QPushButton:
        icon = SHAPE_ICONS[shape_type]
        label = SHAPE_LABELS[shape_type]
        hint = SHAPE_HINTS[shape_type]
        btn = QPushButton(f"{icon}")
        btn.setCheckable(True)
        # 行高统一 26px（restyle_spec §10/B：次工具栏 24~26px 同行一致）
        btn.setFixedSize(38, _BTN_H)
        btn.setToolTip(f"{label}\n{hint}")
        btn.setFont(QFont("Arial", 12))
        btn.clicked.connect(lambda checked, st=shape_type: self._on_tool_selected(st))
        if not hasattr(self, '_tool_btns'):
            self._tool_btns: Dict[str, QPushButton] = {}
        self._tool_btns[shape_type] = btn
        return btn

    # ─────────────────────────────────────────────────────────────────────────
    #  事件处理
    # ─────────────────────────────────────────────────────────────────────────
    def _on_tool_selected(self, shape_type: str):
        self._current_tool = shape_type
        # 更新按钮状态
        for st, btn in getattr(self, '_tool_btns', {}).items():
            btn.setChecked(st == shape_type)
        if hasattr(self, '_sel_btn'):
            self._sel_btn.setChecked(shape_type == "")
        # 更新提示
        if shape_type and hasattr(self, '_hint_lbl'):
            hint = SHAPE_HINTS.get(shape_type, "")
            label = SHAPE_LABELS.get(shape_type, "")
            self._hint_lbl.setText(f"[{label}] {hint}")
            self._hint_lbl.setStyleSheet(
                f"color:{_th.FOCUS_BLUE};font-size:10px;padding:3px 2px;"
                f"background:{_th.BG_INPUT_EDIT};border-radius:3px;"
                f"border:1px solid rgba(113,168,255,90);")
        elif hasattr(self, '_hint_lbl'):
            self._hint_lbl.setText("选择工具后在画布上点击绘制")
            self._hint_lbl.setStyleSheet(
                f"color:{_th.TEXT_SECONDARY};font-size:10px;padding:3px 2px;"
                f"background:{_th.BG_INPUT};border-radius:3px;")
        self.tool_changed.emit(shape_type)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._current_color), self, "选择画笔颜色")
        if c.isValid():
            self._current_color = c.name()
            self._color_btn.setStyleSheet(
                f"background:{c.name()};border:2px solid {_th.BG_BUTTON_HOVER};border-radius:4px;")

    def _on_distribute(self, use_selected: bool):
        idx = self._path_combo.currentIndex()
        if idx < 0:
            return
        shape_id = self._path_combo.itemData(idx)
        if not shape_id:
            return
        num = self._num_spin.value()
        self.distribute_requested.emit(shape_id, num, use_selected)

    def _on_delete_selected(self):
        idx = self._path_combo.currentIndex()
        if idx >= 0:
            shape_id = self._path_combo.itemData(idx)
            if shape_id:
                self.shape_deleted.emit(shape_id)

    def _on_clear_all(self):
        for s in list(self._shapes):
            self.shape_deleted.emit(s.id)

    # ─────────────────────────────────────────────────────────────────────────
    #  数据管理
    # ─────────────────────────────────────────────────────────────────────────
    @property
    def current_tool(self) -> str:
        return self._current_tool

    @property
    def is_guide(self) -> bool:
        return self._guide_cb.isChecked()

    @property
    def line_width(self) -> float:
        return self._lw_spin.value()

    @property
    def color(self) -> str:
        return self._current_color

    def add_shape(self, shape: CadShape):
        self._shapes.append(shape)
        self._refresh_list()
        self._refresh_path_combo()

    def remove_shape(self, shape_id: str):
        self._shapes = [s for s in self._shapes if s.id != shape_id]
        self._refresh_list()
        self._refresh_path_combo()

    def clear_shapes(self):
        self._shapes.clear()
        self._refresh_list()
        self._refresh_path_combo()

    def get_shapes(self) -> List[CadShape]:
        return list(self._shapes)

    def set_shapes(self, shapes: List[CadShape]):
        self._shapes = list(shapes)
        self._refresh_list()
        self._refresh_path_combo()

    def _refresh_list(self):
        # 清空
        while self._list_vlay.count() > 1:
            item = self._list_vlay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # 重建
        for s in self._shapes:
            icon = SHAPE_ICONS.get(s.shape_type, "?")
            name = s.name or SHAPE_LABELS.get(s.shape_type, "?")
            guide_tag = " [辅助线]" if s.is_guide else ""
            row = QWidget()
            row.setStyleSheet(
                f"background:{_th.BG_DEEP};border-radius:3px;"
                f"border-left:3px solid {s.color};")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 3, 4, 3)
            rl.setSpacing(4)
            lbl = QLabel(f"{icon}  {name}{guide_tag}")
            lbl.setStyleSheet(f"color:{_FG};font-size:10px;background:transparent;border:none;")
            rl.addWidget(lbl, 1)
            self._list_vlay.insertWidget(self._list_vlay.count()-1, row)

    def _refresh_path_combo(self):
        self._path_combo.clear()
        for s in self._shapes:
            if not s.is_guide:
                icon = SHAPE_ICONS.get(s.shape_type, "?")
                name = s.name or SHAPE_LABELS.get(s.shape_type, "?")
                self._path_combo.addItem(f"{icon} {name}", s.id)
        if self._path_combo.count() == 0:
            self._path_combo.addItem("（暂无可用路径）", "")
