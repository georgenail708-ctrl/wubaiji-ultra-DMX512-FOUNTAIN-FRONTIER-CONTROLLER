"""
Qascade V1.0 — Curve Editor

XY 曲线鼠标拖动编辑器。
  - 编辑区：256x256 逻辑坐标映射到画布
  - 左键点击空白区：添加控制点
  - 左键拖动控制点：移动
  - 右键点击控制点：删除（端点不可删）
  - 提供 get_curve() -> CurveMapping
  - 提供 reset() 恢复线性
"""

from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPainterPath,
    QPaintEvent, QMouseEvent, QFont,
)

# S1 令牌化：颜色常量统一重定向到中心主题令牌（变量名保持不变）
from ..qascade_theme import (
    BG_ROOT, BG_BOX, BORDER_NORMAL,
    TEXT_DISABLED, TEXT_PRIMARY, TEXT_SECONDARY, ACTIVE_ORANGE, SEP_LINE,
    FOCUS_BLUE,
)

try:
    from ...core.models import CurveMapping, CurvePoint
except ImportError:
    try:
        from ...core.models import CurveMapping, CurvePoint
    except ImportError:
        CurveMapping = CurvePoint = None  # type: ignore

# 变量名保留（旧引用兼容），值全部来自 主题设计令牌：
DARK_BG     = BG_ROOT          # #1D1D1D 根底（原 #1e1e1e）
ACCENT      = ACTIVE_ORANGE    # #FFA028 曲线/端点强调橙（原金 #f0c060）
GRID_COLOR  = QColor(BORDER_NORMAL)
# R50 用户点名：曲线改 focus_blue 正式语言（橙强调退役；端点白+暗环）
CURVE_COLOR = QColor(FOCUS_BLUE)
POINT_COLOR = QColor(TEXT_PRIMARY)  # Control point contrast on the light editor.
DRAG_RADIUS = 10   # px，点击判定半径


class CurveCanvas(QWidget):
    """曲线绘制 + 交互画布（正方形）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(256, 256)
        # WA_StyledBackground：让 QSS 背景/边框在 QWidget 子类上真正生效
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"CurveCanvas{{background:{BG_ROOT}; border:1px solid {SEP_LINE};}}")
        self.setMouseTracking(True)

        # 控制点列表 (x,y 均 0-255)
        self._points: list[list[float]] = [[0.0, 0.0], [255.0, 255.0]]
        self._drag_idx: Optional[int]   = None
        self._hover_idx: Optional[int]  = None

    # ── 坐标转换 ─────────────────────────────
    def _to_canvas(self, x: float, y: float) -> QPointF:
        w, h  = self.width(), self.height()
        pad   = 16
        cw    = w - pad * 2
        ch    = h - pad * 2
        cx    = pad + (x / 255.0) * cw
        cy    = pad + (1.0 - y / 255.0) * ch   # y 轴翻转
        return QPointF(cx, cy)

    def _from_canvas(self, cx: float, cy: float) -> tuple[float, float]:
        w, h  = self.width(), self.height()
        pad   = 16
        cw    = w - pad * 2
        ch    = h - pad * 2
        x     = (cx - pad) / cw * 255.0
        y     = (1.0 - (cy - pad) / ch) * 255.0
        return max(0.0, min(255.0, x)), max(0.0, min(255.0, y))

    def _hit_test(self, pos: QPointF) -> Optional[int]:
        for i, (px, py) in enumerate(self._points):
            cp = self._to_canvas(px, py)
            if (QPointF(pos.x() - cp.x(), pos.y() - cp.y()).manhattanLength()
                    < DRAG_RADIUS):
                return i
        return None

    # ── 鼠标 ─────────────────────────────────
    def mousePressEvent(self, e: QMouseEvent):
        pos = QPointF(e.position())
        idx = self._hit_test(pos)
        if e.button() == Qt.MouseButton.LeftButton:
            if idx is not None:
                self._drag_idx = idx
            else:
                # 添加新点
                x, y = self._from_canvas(pos.x(), pos.y())
                self._points.append([x, y])
                self._points.sort(key=lambda p: p[0])
                self._drag_idx = next(
                    (i for i, p in enumerate(self._points)
                     if abs(p[0] - x) < 0.5), None)
                self.update()
        elif e.button() == Qt.MouseButton.RightButton:
            # 删除（端点不可删）
            if idx is not None and 0 < idx < len(self._points) - 1:
                self._points.pop(idx)
                self.update()
        e.accept()

    def mouseMoveEvent(self, e: QMouseEvent):
        pos = QPointF(e.position())
        if self._drag_idx is not None:
            x, y = self._from_canvas(pos.x(), pos.y())
            # 端点只能上下移动
            if self._drag_idx == 0:
                x = 0.0
            elif self._drag_idx == len(self._points) - 1:
                x = 255.0
            self._points[self._drag_idx] = [x, y]
            self.update()
        else:
            self._hover_idx = self._hit_test(pos)
            self.update()
        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._drag_idx = None
        e.accept()

    # ── 绘制 ─────────────────────────────────
    def paintEvent(self, event: QPaintEvent):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.fillRect(self.rect(), QColor(DARK_BG))

        # 网格（D波精修：rgba(255,255,255,13) · 1.25px 抗锯齿细线）
        p.setPen(QPen(GRID_COLOR, 1.25))
        for i in range(1, 4):
            x = int(16 + (w - 32) * i / 4)
            y = int(16 + (h - 32) * i / 4)
            p.drawLine(x, 16, x, h - 16)
            p.drawLine(16, y, w - 16, y)

        # 对角基准线（线性映射）
        p.setPen(QPen(QColor(BORDER_NORMAL), 1, Qt.PenStyle.DashLine))
        a = self._to_canvas(0, 0)
        b = self._to_canvas(255, 255)
        p.drawLine(a.toPoint(), b.toPoint())

        # 曲线路径
        if len(self._points) >= 2:
            path = QPainterPath()
            sp = self._to_canvas(*self._points[0])
            path.moveTo(sp)
            for pt in self._points[1:]:
                path.lineTo(self._to_canvas(*pt))
            p.setPen(QPen(CURVE_COLOR, 2))
            p.drawPath(path)

        # 控制点（D波：hover 外圈 focus_blue 高亮环）
        for i, (px, py) in enumerate(self._points):
            cp    = self._to_canvas(px, py)
            r     = 6
            is_end   = (i == 0 or i == len(self._points) - 1)
            is_hover = (i == self._hover_idx) or (i == self._drag_idx)
            # R50：端点=白心+focus_blue 环，内点=POINT_COLOR（统一主题语言）
            color = QColor(FOCUS_BLUE) if is_end else POINT_COLOR
            if is_hover:
                color = color.lighter(150)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(FOCUS_BLUE), 1.5))
                p.drawEllipse(cp, r + 3.5, r + 3.5)
            p.setBrush(QBrush(color))
            p.setPen(QPen(QColor(BG_ROOT), 1))
            p.drawEllipse(cp, r, r)

        # 轴标签
        p.setPen(QColor(TEXT_DISABLED))
        axis_font = QFont("Consolas")
        axis_font.setPixelSize(10)
        p.setFont(axis_font)
        p.drawText(16, h - 2, "0")
        p.drawText(w - 24, h - 2, "255")
        p.drawText(4, 20, "255")
        p.drawText(4, h - 16, "0")
        p.end()

    # ── 数据访问 ─────────────────────────────
    def get_points(self) -> list:
        return [list(pt) for pt in self._points]

    def set_points(self, pts: list):
        self._points = [list(p) for p in pts]
        self._points.sort(key=lambda p: p[0])
        self.update()

    def reset(self):
        self._points = [[0.0, 0.0], [255.0, 255.0]]
        self.update()


class CurveEditor(QWidget):
    """曲线编辑器完整组件（画布 + 工具栏）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"CurveEditor{{background:{BG_ROOT};}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # 提示
        hint = QLabel(
            "左键空白处添加点 · 拖动移动点 · 右键删除点（端点不可删）")
        hint.setStyleSheet(
            f"color:{TEXT_SECONDARY}; font-size:10px; background:transparent;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)

        # 画布
        self._canvas = CurveCanvas()
        lay.addWidget(self._canvas, stretch=1)

        # 预设快捷按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        presets = [
            ("线性",     [[0, 0], [255, 255]]),
            ("反转",     [[0, 255], [255, 0]]),
            ("S 形",     [[0, 0], [64, 20], [192, 235], [255, 255]]),
            ("快起慢落", [[0, 0], [80, 200], [255, 255]]),
            ("慢起快落", [[0, 0], [175, 55], [255, 255]]),
            ("全亮",     [[0, 255], [255, 255]]),
            ("全灭",     [[0, 0], [255, 0]]),
        ]
        for name, pts in presets:
            b = QPushButton(name)
            b.setFixedHeight(24)
            # S1 重构：删除与全局主题重复的通用 QPushButton 外观，仅保留布局特化。
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(f"载入预设曲线：{name}")
            b.clicked.connect(lambda _, p=pts: self._canvas.set_points(p))
            btn_row.addWidget(b)
        btn_row.addStretch()

        reset_btn = QPushButton("↺ 重置")
        reset_btn.setFixedHeight(24)
        # S1 重构：删除与全局主题重复的通用按钮 QSS（同上）。
        # ↺ 字形补 Segoe UI Symbol 兜底（防缺字形 tofu，纯呈现层）
        reset_btn.setStyleSheet(
            "QPushButton{font-family:'Segoe UI Symbol','Segoe UI',"
            "'Microsoft YaHei UI',sans-serif;}")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setToolTip("恢复线性曲线（两个端点）")
        reset_btn.clicked.connect(self._canvas.reset)
        btn_row.addWidget(reset_btn)

        lay.addLayout(btn_row)

    def get_curve(self):
        """返回 CurveMapping 对象（或 None 如未导入）"""
        if CurveMapping is None or CurvePoint is None:
            return None
        pts = self._canvas.get_points()
        curve = CurveMapping(
            points=[CurvePoint(p[0], p[1]) for p in pts],
            source="manual"
        )
        return curve

    def reset(self):
        self._canvas.reset()
