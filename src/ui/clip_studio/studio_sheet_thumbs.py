# -*- coding: utf-8 -*-
"""Sheet 缩略图条（R183 W2-ST #16）—— 每张后处理页一枚 80px 缩略，点击切页。

复用小地图的**总览管线**（`studio_minimap.render_overview_pixmap`：框/边/卡 chip
的降采样），不抓画布 —— 非 active 页没有活画布可抓，且抓拍带副作用（见
`studio_minimap` 模块 docstring 的取舍记录）。

控件自绘（`QWidget.paintEvent`）：纯 Qt 无子控件 → offscreen 可渲染可断言；激活页
描边高亮 + 页名 8px 显示。
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from src.ui import qascade_theme as _th
from src.ui.clip_studio.studio_minimap import THUMB_H, THUMB_W, render_overview_pixmap

__all__ = ["SheetThumbStrip"]

_GAP = 4
_LABEL_H = 12
_PAD = 2
_MIN_VISIBLE = 1        # 至少显示一张（空表 → 不画任何项）


class SheetThumbStrip(QWidget):
    """横向缩略图条：`set_sheets(sheets, active_id)` → 重绘；点击发 `sheet_activated`。"""

    sheet_activated = pyqtSignal(str)          # 被点击的 sheet id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list = []                 # [(sid, name, QPixmap, active)]
        self.setFixedHeight(THUMB_H + _LABEL_H + 2 * _PAD + 2)
        self.setToolTip("后处理页缩略：点击切页")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── 数据 ────────────────────────────────────────────────────────────
    def set_sheets(self, sheets, active_id: str = "") -> None:
        """重建缩略表（每页一张 80px 宽位图；页数变化 → 自适应最小宽度）。"""
        items = []
        for s in sheets or ():
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id", ""))
            if not sid:
                continue
            items.append((sid, str(s.get("name", "") or ""),
                          render_overview_pixmap(s, THUMB_W, THUMB_H),
                          sid == str(active_id)))
        self._items = items
        self._sync_size()
        self.update()

    def _sync_size(self) -> None:
        n = max(_MIN_VISIBLE, len(self._items))
        self.setMinimumWidth(n * (THUMB_W + _GAP) + _GAP)
        self.updateGeometry()

    def items(self) -> list:
        """当前缩略项 ``[(sid, name, active), …]``（测试入口；位图不入表）。"""
        return [(sid, name, active) for sid, name, _pm, active in self._items]

    def thumb_pixmap(self, sid):
        """某页缩略位图（测试入口；不存在 → None）。"""
        return next((pm for i, _n, pm, _a in self._items if i == str(sid)), None)

    def item_rect(self, index: int) -> QRectF:
        """第 index 项的绘制矩形（点击命中与绘制同源）。"""
        x = _GAP + int(index) * (THUMB_W + _GAP)
        return QRectF(x, _PAD, THUMB_W, THUMB_H)

    # ── 交互 ────────────────────────────────────────────────────────────
    def _index_at(self, pos) -> int:
        for i in range(len(self._items)):
            if self.item_rect(i).adjusted(0, 0, 0, _LABEL_H).contains(pos):
                return i
        return -1

    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            i = self._index_at(ev.position())
            if i >= 0:
                self.sheet_activated.emit(str(self._items[i][0]))
                ev.accept()
                return
        super().mousePressEvent(ev)

    # ── 绘制 ────────────────────────────────────────────────────────────
    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f = QFont()
        f.setPixelSize(9)
        p.setFont(f)
        fm = QFontMetrics(f)
        for i, (_sid, name, pm, active) in enumerate(self._items):
            r = self.item_rect(i)
            p.drawPixmap(r.toRect(), pm)
            p.setPen(QPen(QColor(_th.CANVAS_SELECTION if active else _th.BORDER_NORMAL),
                          1.6 if active else 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            label = QRectF(r.left(), r.bottom() + 1, r.width(), _LABEL_H)
            p.setPen(QPen(QColor(_th.TEXT_PRIMARY if active else _th.TEXT_SECONDARY)))
            p.drawText(label, Qt.AlignmentFlag.AlignCenter,
                       fm.elidedText(name, Qt.TextElideMode.ElideRight, int(r.width())))
        p.end()
