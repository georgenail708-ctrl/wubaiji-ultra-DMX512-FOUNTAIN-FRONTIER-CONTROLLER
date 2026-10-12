# -*- coding: utf-8 -*-
"""画布静态层（点阵网格底图缓存）—— 从 `studio_canvas_paint` 拆出（500 行门禁）。

照 `route_animator/canvas.py`（RouteCanvas）先例：静态层 = 「点阵网格底图」一张
QPixmap 缓存，键 = `(宽, 高, scale 档)`；小范围平移（≤ `_STATIC_MARGIN`）直接位移
复用，不改缓存内容。缩放 / 尺寸变化 / 大范围平移 → 重建。

宿主（ClipStudioCanvas）提供：`width/height/_view_scale/_view_offset/_static_cache/
_static_key/_static_offset/_s2w/_px/_scale_band`（鸭子类型；`_static_cache = None`
由宿主的缩放/导航入口作废）。
"""
from __future__ import annotations
from src.ui.hidpi import hidpi_pixmap

import math

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap

from src.ui import qascade_theme as _th
from src.ui.canvas_materials import paint_bed

__all__ = ["StaticLayerMixin", "STATIC_MARGIN", "DOT_SPACING_PX", "LOD_GRID_MINOR"]

#: 静态缓存四周余量（小范围平移位移复用）。
STATIC_MARGIN = 120
#: 点阵间距（屏幕像素档；经 `_px` 折到世界单位）。
DOT_SPACING_PX = 28.0
#: 低于此缩放隐藏次级网格点，只留主网格（4 倍间距）。
LOD_GRID_MINOR = 0.35


class StaticLayerMixin:
    """点阵网格底图的缓存与绘制（宿主 = ClipStudioCanvas）。"""

    # ── 静态层（点阵网格底图）───────────────────────────────────────────
    def _ensure_static(self, p: QPainter) -> None:
        """静态层缓存：键 = (w, h, scale 档)；小范围平移位移复用（照 RouteCanvas）。"""
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return
        key = (w, h, self._scale_band(), self.devicePixelRatioF(), _th.CANVAS_REVISION)
        dx = dy = 0.0
        ok = self._static_cache is not None and self._static_key == key
        if ok:
            dx = self._view_offset.x() - self._static_offset.x()
            dy = self._view_offset.y() - self._static_offset.y()
            ok = abs(dx) <= STATIC_MARGIN and abs(dy) <= STATIC_MARGIN
        if not ok:
            self._rebuild_static()
            self._static_offset = QPointF(self._view_offset)
            dx = dy = 0.0
        if self._static_cache is not None:
            p.drawPixmap(QPointF(-STATIC_MARGIN + dx, -STATIC_MARGIN + dy),
                         self._static_cache)

    def _rebuild_static(self) -> None:
        w, h = self.width(), self.height()
        pm = hidpi_pixmap(w + STATIC_MARGIN * 2, h + STATIC_MARGIN * 2, self)   # C7
        pm.fill(QColor(_th.BG_ROOT))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_bed(p, QRectF(0, 0, w + STATIC_MARGIN * 2, h + STATIC_MARGIN * 2))
        p.translate(STATIC_MARGIN, STATIC_MARGIN)
        p.translate(self._view_offset)
        p.scale(self._view_scale, self._view_scale)
        tl = self._s2w(QPointF(-STATIC_MARGIN, -STATIC_MARGIN))
        br = self._s2w(QPointF(w + STATIC_MARGIN, h + STATIC_MARGIN))
        sp = self._px(DOT_SPACING_PX)
        self._draw_dot_grid(p, tl, br, sp)
        p.end()
        self._static_cache = pm
        self._static_key = (w, h, self._scale_band(), self.devicePixelRatioF(),
                            _th.CANVAS_REVISION)

    def _draw_dot_grid(self, p: QPainter, tl: QPointF, br: QPointF, sp: float) -> None:
        """双档点阵（网格视图先例）：次级=每格一点，主级=每 4 格更亮一点。

        缩小到 `LOD_GRID_MINOR` 以下只画主级（否则密集点阵糊成灰雾，纯耗时）。
        """
        low = self._view_scale < LOD_GRID_MINOR
        minor_pen = QPen(QColor(_th.CANVAS_GRID_MINOR), self._px(0.85))
        major_pen = QPen(QColor(_th.CANVAS_GRID_MAJOR), self._px(1.1))
        x0 = math.floor(tl.x() / sp) * sp
        col0 = int(round(x0 / sp))
        x = x0
        col = col0
        while x <= br.x():
            p.setPen(major_pen if col % 4 == 0 else minor_pen)
            if not low or col % 4 == 0:
                self._draw_grid_column(p, x, (tl.y(), br.y(), sp, low))
            x += sp
            col += 1

    def _draw_grid_column(self, p: QPainter, x: float, spec: tuple) -> None:
        y0, y1, sp, low = spec
        y = math.floor(y0 / sp) * sp
        row = int(round(y / sp))
        while y <= y1:
            if not low or row % 4 == 0:
                p.drawPoint(QPointF(x, y))
            y += sp
            row += 1

