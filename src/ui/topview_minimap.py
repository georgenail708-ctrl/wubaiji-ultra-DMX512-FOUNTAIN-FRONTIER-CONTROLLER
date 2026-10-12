# -*- coding: utf-8 -*-
"""TopView 小地图（R183/F2 · 深查③ T3）：右下角缩略 + 视口框拖动导航。

真工程 1356 台/10 页（LE D 页 x∈[-648,1492]、y∈[-1160,1178]），"盲缩放"是
导航主痛点。本控件是画布内容区的子控件（不抢画布事件）：

  * 内容 = `TopViewCanvas._get_static_cache()` 整页位图（grid + shapes）按
    「世界 bbox → 控件」等比缩放后贴一张固定尺寸缓存；设备点在此之上叠加，
    透明度分层（位图弱、视口框强、设备点中）。
  * 视口框 = `canvas._viewport_rect_in_world()` 映射到小地图；**拖动框**直接
    反算世界中心写回 `canvas._view_offset`（与 `_zoom_at` 同源变换）。
  * 位图缓存失效口径 = 画布 `_static_cache` 对象身份 + 世界 bbox + 控件尺寸：
    静态缓存重建（网格/图形变更）或视口平移缩放导致 bbox 变化时才重贴。
    设备点只有"位图级"精度，故设备拖动每帧重画也只是几十个点。

注意：本仓禁新增依赖；缩略图走 QPainter 直绘，不用 QGraphicsView。
"""
from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from .hidpi import hidpi_pixmap
from . import qascade_theme as _th

#: 小地图尺寸（任务书：右下角 160×120）。
MINIMAP_W = 160
MINIMAP_H = 120
#: 与画布边缘/底部 chip 的安全间距。
MINIMAP_MARGIN = 10
#: 视口框命中容差（像素）。
VIEWPORT_GRAB_TOL = 6


def world_bounds(panel, canvas=None) -> Optional[Tuple[float, float, float, float]]:
    """当前页内容世界包围盒（设备 + 逻辑设备 + 视口）。

    包含**视口**本身：空页/单点时也要能把视口框画出来（否则拖不动）。
    """
    pts = _content_points(panel)
    if canvas is not None and pts:
        pts.extend(_viewport_corners(canvas))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _content_points(panel) -> list:
    """当前页设备 + 逻辑设备的世界坐标点表（读 TopView 页数据，不读设备记录）。"""
    pts: list = []
    try:
        ids = panel.fixture_ids()
    except Exception:
        ids = []
    for fid in ids:
        try:
            x, y = panel.fixture_pos(str(fid))
        except Exception:
            continue
        pts.append((float(x), float(y)))
    try:
        for ld in (panel.current_page().get("logical_devices") or []):
            pts.append((float(ld.get("pos_x", 0.0)), float(ld.get("pos_y", 0.0))))
    except Exception:
        pass
    return pts


def _viewport_corners(canvas) -> list:
    try:
        r = canvas._viewport_rect_in_world()
    except Exception:
        return []
    return [(r.left(), r.top()), (r.right(), r.bottom())]


class MiniMap(QWidget):
    """右下角缩略导航图。"""

    def __init__(self, canvas, parent=None) -> None:
        super().__init__(parent if parent is not None else canvas)
        self._canvas = canvas
        self._backing: Optional[QPixmap] = None
        self._backing_key = None
        self._bounds: Optional[Tuple[float, float, float, float]] = None
        self._dragging = False
        self._hover = False
        self.setFixedSize(MINIMAP_W, MINIMAP_H)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("小地图：拖动方框平移视图；单击跳转；双击适配整页到视口")
        self.raise_()

    # ── 几何映射 ──
    def _view_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, float(MINIMAP_W), float(MINIMAP_H))

    def _compute_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        bounds = world_bounds(self._canvas.panel, self._canvas)
        if bounds is None:
            return None
        min_x, min_y, max_x, max_y = bounds
        span_x = max(1.0, max_x - min_x)
        span_y = max(1.0, max_y - min_y)
        pad = 0.06
        min_x -= span_x * pad
        max_x += span_x * pad
        min_y -= span_y * pad
        max_y += span_y * pad
        # 等比：两轴取同一 scale，短轴居中（避免非等比拉伸让方向感失真）
        span_x = max_x - min_x
        span_y = max_y - min_y
        scale = min(min(self.width(), self.width()) / span_x,
                    self.height() / span_y) if span_x > 0 and span_y > 0 else 1.0
        cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
        half_w, half_h = MINIMAP_W / 2.0 / scale, MINIMAP_H / 2.0 / scale
        return cx - half_w, cy - half_h, cx + half_w, cy + half_h

    def _world_to_map(self, x: float, y: float) -> QPointF:
        b = self._bounds
        if b is None:
            return QPointF(0.0, 0.0)
        sx = MINIMAP_W / max(1e-9, (b[2] - b[0]))
        sy = MINIMAP_H / max(1e-9, (b[3] - b[1]))
        return QPointF((float(x) - b[0]) * sx, (float(y) - b[1]) * sy)

    def _map_to_world(self, pos: QPointF) -> Tuple[float, float]:
        b = self._bounds
        if b is None:
            return 0.0, 0.0
        sx = (b[2] - b[0]) / float(MINIMAP_W)
        sy = (b[3] - b[1]) / float(MINIMAP_H)
        return b[0] + float(pos.x()) * sx, b[1] + float(pos.y()) * sy

    # ── 缓存 ──
    def invalidate(self) -> None:
        self._backing_key = None

    def _refresh_bounds(self) -> None:
        bounds = self._compute_bounds()
        self._bounds = bounds

    def _backing_pixmap(self) -> Optional[QPixmap]:
        """静态缓存位图 → 小地图尺寸（按世界 bbox 裁剪后等比缩放）。"""
        canvas = self._canvas
        try:
            cache = canvas._get_static_cache()
        except Exception:
            cache = None
        bounds = self._bounds
        if cache is None or cache.isNull() or bounds is None:
            return None
        key = (id(cache), cache.cacheKey() if hasattr(cache, "cacheKey") else 0,
               tuple(round(v, 3) for v in bounds), self.width(), self.height(),
               self.devicePixelRatioF(), _th.CANVAS_REVISION)
        if self._backing is not None and self._backing_key == key:
            return self._backing
        scale = max(1e-6, float(getattr(canvas, "_view_scale", 1.0) or 1.0))
        off = getattr(canvas, "_view_offset", QPointF(0.0, 0.0))
        src_x = bounds[0] * scale + off.x()
        src_y = bounds[1] * scale + off.y()
        src_w = (bounds[2] - bounds[0]) * scale
        src_h = (bounds[3] - bounds[1]) * scale
        cache_dpr = cache.devicePixelRatioF()
        src = QRectF(src_x * cache_dpr, src_y * cache_dpr, src_w * cache_dpr, src_h * cache_dpr)
        out = hidpi_pixmap(self.width(), self.height(), self)   # C7：小地图按 DPR 栅格化
        out.fill(QColor(0, 0, 0, 0))
        p = QPainter(out)
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.drawPixmap(QRectF(0.0, 0.0, float(self.width()), float(self.height())),
                         cache, src)
        finally:
            p.end()
        self._backing, self._backing_key = out, key
        return out

    # ── 绘制 ──
    def paintEvent(self, ev) -> None:  # noqa: N802
        self._refresh_bounds()
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(_th.CANVAS_CARD))
            r = self._view_rect().adjusted(0.5, 0.5, -0.5, -0.5)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            bg = QColor(_th.CANVAS_CARD)
            p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
            p.setBrush(QBrush(bg))
            p.drawRoundedRect(r, 4.0, 4.0)
            if self._bounds is None:
                return
            self._paint_content(p)
            self._paint_fixtures(p)
            self._paint_viewport(p)
        finally:
            p.end()

    def _paint_content(self, p: QPainter) -> None:
        pix = self._backing_pixmap()
        if pix is not None and not pix.isNull():
            p.setOpacity(0.55)
            p.drawPixmap(0, 0, pix)
            p.setOpacity(1.0)

    def _paint_fixtures(self, p: QPainter) -> None:
        panel = self._canvas.panel
        color = QColor(_th.TEXT_SECONDARY)
        color.setAlpha(170)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        try:
            ids = panel.fixture_ids()
        except Exception:
            return
        for fid in ids:
            try:
                x, y = panel.fixture_pos(str(fid))
            except Exception:
                continue
            pt = self._world_to_map(x, y)
            p.drawRect(QRectF(pt.x(), pt.y(), 1.6, 1.6))

    def _paint_viewport(self, p: QPainter) -> None:
        rect = self._viewport_map_rect()
        if rect is None:
            return
        pen = QPen(QColor(_th.BRAND_ORANGE if self._dragging or self._hover
                          else _th.CANVAS_SELECTION), 1.6)
        p.setPen(pen)
        fill = QColor(_th.BRAND_ORANGE)
        fill.setAlpha(38)
        p.setBrush(QBrush(fill))
        p.drawRect(rect)

    def _viewport_map_rect(self) -> Optional[QRectF]:
        if self._bounds is None:
            return None
        try:
            r = self._canvas._viewport_rect_in_world()
        except Exception:
            return None
        tl = self._world_to_map(r.left(), r.top())
        br = self._world_to_map(r.right(), r.bottom())
        return QRectF(tl, br).normalized()

    # ── 交互 ──
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._navigate_to(ev.position())
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._dragging:
            self._navigate_to(ev.position())
            ev.accept()
            return
        rect = self._viewport_map_rect()
        inside = bool(rect and rect.contains(ev.position()))
        if inside != self._hover:
            self._hover = inside
            self.setCursor(Qt.CursorShape.OpenHandCursor if inside
                           else Qt.CursorShape.PointingHandCursor)
            self.update()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.update()
        ev.accept()

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        """双击：适配整页内容到视口（等价 `_fit_view_to_content`）。"""
        try:
            self._canvas._fit_view_to_content()
        except Exception:
            return
        self._canvas.update()
        self.invalidate()
        self.update()
        ev.accept()

    def wheelEvent(self, ev) -> None:  # noqa: N802
        """小地图上滚轮 = 画布缩放（锚点仍取画布视口中心，不跳页）。"""
        canvas = self._canvas
        try:
            r = canvas._viewport_rect_in_world()
            center = canvas._world_to_screen(
                QPointF((r.left() + r.right()) / 2.0, (r.top() + r.bottom()) / 2.0))
            factor = 1.15 if ev.angleDelta().y() > 0 else 1.0 / 1.15
            canvas._zoom_at(center, factor)
        except Exception:
            pass
        self.invalidate()
        self.update()
        ev.accept()

    def _navigate_to(self, pos: QPointF) -> None:
        """把视口中心移到小地图上该点对应的世界坐标。"""
        wx, wy = self._map_to_world(pos)
        canvas = self._canvas
        scale = max(0.005, float(getattr(canvas, "_view_scale", 1.0) or 1.0))
        canvas._view_offset = QPointF(
            float(canvas.width()) / 2.0 - wx * scale,
            float(canvas.height()) / 2.0 - wy * scale)
        canvas._static_cache = None
        canvas.update()
        self.update()
