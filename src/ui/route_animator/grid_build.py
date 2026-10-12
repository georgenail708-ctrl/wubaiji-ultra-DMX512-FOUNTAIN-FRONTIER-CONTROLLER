# -*- coding: utf-8 -*-
"""画轨道网格吸附辅助 — 淡色吸附网格 + 落点吸附(类画图软件网格吸附)。

前身是「游戏式网格快速建造」(R178): 按用户口径它不是独立功能, 已改造为
画轨道(track_draw_mode)时的可开关吸附辅助 — 画轨道期间画布上叠加淡色
网格, 落点吸附到格心(六边形)/格点(方形)并显示吸附高亮; 按住 Shift 临时
关闭吸附回退自由扫设备画法; 关闭吸附则完全恢复旧自由画轨道行为。

坐标约定(axial 数学沿用原建造版, 1e-9 往返测试 17 例保持绿):
  * 六边形 = 尖顶(pointy-top) axial 坐标 (q, r), size=外接圆半径;
    取整用三次立方取整(cube rounding)防浮点漂移; 吸附目标 = 格心。
  * 方形 = 正交网格线, size=格边长, 吸附目标 = 网格线交点
    (round(x/size)*size); 悬停格(pixel_to_cell)做淡填充提示。

接线契约(runner_panel):
  * _GameCanvas.__init__ 创建 ``self._snap = SnapGrid()``(普通对象, 无事件过滤);
    画轨道模式的 press/move 在「道岔节点锚定之后、设备吸附之前」查询吸附落点。
  * paintEvent 世界变换内调 ``draw_lattice``(网格线, 静态层之上) 与
    ``draw_highlight``(吸附落点高亮, 链预览之上)。
  * HUD「网格吸附」开关/形状/间距控件 → ``configure`` 即时生效;
    启用态/形状/间距由面板写 QSettings 记最近使用。
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF

from ..qascade_theme import FOCUS_BLUE

__all__ = [
    "SnapGrid",
    "axial_to_pixel", "pixel_to_axial", "pixel_to_cell", "cell_to_pixel",
    "cell_center", "snap_hex_point", "snap_square_point",
]

Cell = Tuple[int, int]
Point = Tuple[float, float]

_SQRT3 = math.sqrt(3.0)
# 可视区格数超过上限即跳过网格线(防低倍缩放卡帧, 沿用建造版阈值)。
_MAX_DRAWN_CELLS = 1600
_LATTICE_ALPHA = 26
_HOVER_FILL_ALPHA = 22


# ── 网格数学 ─────────────────────────────────────────────────────────
def _cube_round(qf: float, rf: float) -> Cell:
    """三次立方取整: 把浮点 axial 投回最近整数格(格边归属确定性)。"""
    xf, zf = float(qf), float(rf)
    yf = -xf - zf
    rx, ry, rz = round(xf), round(yf), round(zf)
    dx, dy, dz = abs(rx - xf), abs(ry - yf), abs(rz - zf)
    if dx > dy and dx > dz:
        rx = -ry - rz
    elif dy > dz:
        ry = -rx - rz
    else:
        rz = -rx - ry
    return (int(rx), int(rz))


def axial_to_pixel(q: int, r: int, size: float) -> Point:
    """尖顶六边形格心世界坐标。"""
    x = size * (_SQRT3 * q + _SQRT3 / 2.0 * r)
    y = size * (3.0 / 2.0 * r)
    return (x, y)


def pixel_to_axial(x: float, y: float, size: float) -> Cell:
    """世界坐标 → 最近格(cube rounding 防浮点误差)。"""
    qf = (_SQRT3 / 3.0 * x - 1.0 / 3.0 * y) / size
    rf = (2.0 / 3.0 * y) / size
    return _cube_round(qf, rf)


def pixel_to_cell(x: float, y: float, size: float) -> Cell:
    """方形网格: 世界坐标 → 格索引(格 = [cx,cx+1)*size × [cy,cy+1)*size)。"""
    return (int(math.floor(x / size)), int(math.floor(y / size)))


def cell_to_pixel(cx: int, cy: int, size: float) -> Point:
    """方形网格: 格索引 → 格心世界坐标。"""
    return ((cx + 0.5) * size, (cy + 0.5) * size)


def cell_center(cell: Cell, size: float, mode: str = "hex") -> Point:
    """格心世界坐标(hex=axial 格心, square=方形格心)。"""
    if mode == "square":
        return cell_to_pixel(cell[0], cell[1], size)
    return axial_to_pixel(cell[0], cell[1], size)


def snap_hex_point(x: float, y: float, size: float) -> Point:
    """六边形吸附: 世界坐标 → 最近格心。"""
    q, r = pixel_to_axial(x, y, size)
    return axial_to_pixel(q, r, size)


def snap_square_point(x: float, y: float, size: float) -> Point:
    """方形吸附: 世界坐标 → 最近网格线交点(格点)。"""
    return (round(x / size) * size, round(y / size) * size)


# ── 吸附状态 + 覆盖层绘制 ────────────────────────────────────────────
class SnapGrid:
    """网格吸附配置与落点状态(挂在 _GameCanvas 上的普通对象, 非模态)。

    * ``enabled`` 关 → 一切查询零开销, 画轨道回到纯设备扫链。
    * ``target``   = 最近吸附落点(世界坐标); ``suspended`` = Shift 临时禁用瞬态。
    * ``_hover``   = 最近一次光标世界坐标(Shift 松开时据此恢复落点)。
    """

    def __init__(self) -> None:
        self.enabled = False
        self.mode = "hex"            # "hex" | "square"
        self.size = 50.0             # 格距(世界单位): hex=外接圆半径, square=边长
        self.target: Optional[Point] = None
        self.suspended = False
        self._hover: Optional[Point] = None

    def configure(self, enabled: bool, mode: str, size: float) -> None:
        """HUD 控件入口; 吸附中改形状/格距即时生效。"""
        self.enabled = bool(enabled)
        self.mode = str(mode or "hex")
        self.size = max(1e-3, float(size))
        if self._hover is not None:
            self.target = self.snap_point(*self._hover) if self.enabled else None
        elif not self.enabled:
            self.clear_pointer()

    def clear_pointer(self) -> None:
        self.target = None
        self.suspended = False
        self._hover = None

    def set_suspended(self, on: bool) -> None:
        """Shift 按下/松开: 挂起时清落点高亮, 松开按最近悬停位恢复。"""
        on = bool(on)
        if on == self.suspended:
            return
        self.suspended = on
        if on:
            self.target = None
        elif self._hover is not None:
            self.target = self.snap_point(*self._hover)

    def snapping(self, canvas) -> bool:
        """此刻是否实际吸附(开吸附 + 画轨道中 + 未按 Shift)。"""
        return bool(self.enabled
                    and getattr(canvas, "_track_draw_mode", False)
                    and not self.suspended)

    def snap_point(self, x: float, y: float) -> Point:
        """世界坐标 → 最近吸附目标(六边形=格心, 方形=格点交线)。"""
        if self.mode == "square":
            return snap_square_point(x, y, self.size)
        return snap_hex_point(x, y, self.size)

    def update_pointer(self, x: float, y: float, suspended: bool) -> bool:
        """光标移动时刷新吸附落点; 返回显示态是否变化(供节流 update)。"""
        self._hover = (x, y)
        self.suspended = bool(suspended)
        new = self.snap_point(x, y) if (self.enabled and not self.suspended) else None
        if new == self.target:
            return False
        self.target = new
        return True

    def cell_at(self, x: float, y: float) -> Cell:
        if self.mode == "square":
            return pixel_to_cell(x, y, self.size)
        return pixel_to_axial(x, y, self.size)

    def cell_poly(self, cell: Cell) -> QPolygonF:
        """悬停/吸附格的多边形轮廓(hex=六边形, square=方格)。"""
        cx, cy = cell_center(cell, self.size, self.mode)
        if self.mode == "square":
            h = self.size / 2.0
            return QPolygonF([QPointF(cx - h, cy - h), QPointF(cx + h, cy - h),
                              QPointF(cx + h, cy + h), QPointF(cx - h, cy + h)])
        pts = QPolygonF()
        for i in range(6):
            ang = math.radians(60.0 * i + 30.0)
            pts.append(QPointF(cx + self.size * math.cos(ang),
                               cy + self.size * math.sin(ang)))
        return pts

    # -- 覆盖层(painter 已在世界变换内, 同 _draw_chain_preview) -------- #
    def draw_lattice(self, painter: QPainter, canvas) -> None:
        """淡色吸附网格线 — 仅画轨道模式且吸附开启时绘制。"""
        if not (self.enabled and getattr(canvas, "_track_draw_mode", False)):
            return
        rect = canvas._vr()
        if self.mode == "hex":
            self._draw_hex_lattice(painter, rect)
        else:
            self._draw_square_lattice(painter, rect)

    def _draw_hex_lattice(self, painter: QPainter, rect: QRectF) -> None:
        size = self.size
        r_lo = math.floor(2.0 * rect.top() / (3.0 * size)) - 1
        r_hi = math.ceil(2.0 * rect.bottom() / (3.0 * size)) + 1
        q_span = rect.width() / (_SQRT3 * size) + (r_hi - r_lo) / 2.0 + 2.0
        if (r_hi - r_lo + 1) * q_span > _MAX_DRAWN_CELLS:
            return
        pen = QPen(QColor(255, 255, 255, _LATTICE_ALPHA), 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for r in range(r_lo, r_hi + 1):
            q_lo = math.floor(rect.left() / (_SQRT3 * size) - r / 2.0) - 1
            q_hi = math.ceil(rect.right() / (_SQRT3 * size) - r / 2.0) + 1
            for q in range(q_lo, q_hi + 1):
                cx, cy = axial_to_pixel(q, r, size)
                poly = QPolygonF()
                for i in range(6):
                    ang = math.radians(60.0 * i + 30.0)
                    poly.append(QPointF(cx + size * math.cos(ang),
                                        cy + size * math.sin(ang)))
                painter.drawPolygon(poly)

    def _draw_square_lattice(self, painter: QPainter, rect: QRectF) -> None:
        size = self.size
        if (rect.width() / size) * (rect.height() / size) > _MAX_DRAWN_CELLS:
            return
        x0 = math.floor(rect.left() / size) * size
        x1 = math.ceil(rect.right() / size) * size
        y0 = math.floor(rect.top() / size) * size
        y1 = math.ceil(rect.bottom() / size) * size
        pen = QPen(QColor(255, 255, 255, _LATTICE_ALPHA), 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        x = x0
        while x <= x1:
            painter.drawLine(QPointF(x, y0), QPointF(x, y1))
            x += size
        y = y0
        while y <= y1:
            painter.drawLine(QPointF(x0, y), QPointF(x1, y))
            y += size

    def draw_highlight(self, painter: QPainter, canvas) -> None:
        """吸附高亮: 悬停格淡描边 + 吸附落点环点 + 上一链点到落点的虚线预览。"""
        if not (self.enabled and getattr(canvas, "_track_draw_mode", False)):
            return
        if self._hover is None or self.suspended:
            return
        accent = QColor(FOCUS_BLUE)
        # 悬停格淡描边(格心/格点归属提示)
        cell_pen = QPen(QColor(accent), 1.0)
        cell_pen.setCosmetic(True)
        cell_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(cell_pen)
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(),
                                _HOVER_FILL_ALPHA))
        painter.drawPolygon(self.cell_poly(self.cell_at(*self._hover)))
        if self.target is None:
            return
        tx, ty = self.target
        # 上一链点 → 吸附落点的预览虚线(看出"这一笔会画到哪")
        chain = getattr(canvas, "_chain_points", None) or []
        if chain:
            prev = QPen(QColor(accent), 1.2)
            prev.setCosmetic(True)
            prev.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(prev)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            lx, ly = chain[-1]
            painter.drawLine(QPointF(lx, ly), QPointF(tx, ty))
        # 落点: 外环 + 中心点(屏幕恒定尺寸, 同 _draw_chain_preview 的 _px 约定)
        rr = canvas._px(9.0)
        ring = QPen(QColor(accent), 1.6)
        ring.setCosmetic(True)
        painter.setPen(ring)
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 60))
        painter.drawEllipse(QPointF(tx, ty), rr, rr)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent))
        painter.drawEllipse(QPointF(tx, ty), canvas._px(2.2), canvas._px(2.2))
