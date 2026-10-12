# -*- coding: utf-8 -*-
"""R3 补遗：火车卡片上**内嵌常驻**的油门包络缩略曲线（高 ≤48px，点击展开浮窗）。

深查③ R3 原文建议把包络编辑器内嵌；W2 落地为"卡片内联缩略 + 点击展开"：
  · 缩略自绘（零新依赖）：曲线折线 + 当前播放头 + 时长/状态文字；
  · 点击缩略 → 打开既有 `ThrottleEnvelopeDialog`（贝塞尔编辑器 + 节拍吸附 R4）；
  · 未建包络时画一条 0→1 参考斜线（暗示"可点开编排"），不新建数据；
  · 未播放/无车 → 只画参考线，零开销。
面板侧只需在 `_build_train_card` 尾部一行 `attach_envelope_strip(self, card, lay, tid)`。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..qascade_theme import BG_DEEP, SEP_LINE, TEXT_SECONDARY

log = logging.getLogger(__name__)

#: 缩略高度（px）——R3 要求 ≤48
STRIP_HEIGHT = 44

#: 采样点数（画折线用；足够平滑且便宜）
SAMPLES = 48


class EnvelopeStrip(QWidget):
    """单车包络缩略（点击 → 展开编辑器浮窗）。"""

    clicked = pyqtSignal(str)

    def __init__(self, panel, tid: str, parent=None) -> None:
        super().__init__(parent)
        self._panel = panel
        self._tid = str(tid)
        self._pts: List[Tuple[float, float]] = []
        self._elapsed = -1.0
        self._duration = 0.0
        self._active = False
        self.setFixedHeight(STRIP_HEIGHT)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("油门包络（R3）：点击展开贝塞尔编辑器（含节拍吸附）")
        self.refresh()

    # ── 数据 ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """从面板包络表重采样（坏数据 → 空表 = 画参考斜线）。"""
        try:
            from .runner_envelope import envelopes_of
            env = (envelopes_of(self._panel) or {}).get(self._tid)
        except Exception:
            env = None
        self._pts = []
        self._elapsed = -1.0
        self._duration = 0.0
        self._active = False
        if not env:
            self.update()
            return
        curve = env.get("curve")
        duration = max(1e-3, float(env.get("duration", 1.0) or 1.0))
        self._duration = duration
        self._active = bool(env.get("active"))
        self._elapsed = float(env.get("elapsed", 0.0) or 0.0) if self._active else -1.0
        if curve is None:
            self.update()
            return
        try:
            self._pts = [(duration * i / SAMPLES,
                          max(0.0, min(1.0, float(curve.value_at(duration * i / SAMPLES)))))
                         for i in range(SAMPLES + 1)]
        except Exception:
            log.debug("envelope strip sample failed (tid=%s)", self._tid, exc_info=True)
            self._pts = []
        self.update()

    def sample_points(self) -> list:
        """当前缩略折线（离屏探针直接读）。"""
        return list(self._pts)

    # ── 交互 ────────────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._tid)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.clicked.emit(self._tid)
        super().mouseDoubleClickEvent(event)

    # ── 绘制 ────────────────────────────────────────────────────────────
    def _curve_pen(self) -> QPen:
        color = QColor("#56B65B" if self._active else "#8fd0ff")
        return QPen(color, 1.4)

    def _draw_ref_line(self, p: QPainter, rect) -> None:
        """无包络：画 0→1 参考斜线 + 提示文字（暗示可点击编排）。"""
        p.setPen(QPen(QColor(SEP_LINE), 1.0, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(rect.left(), rect.bottom()),
                   QPointF(rect.right(), rect.top()))
        p.setPen(QColor(TEXT_SECONDARY))
        p.drawText(rect.adjusted(4, 0, -4, 0),
                   Qt.AlignmentFlag.AlignCenter, "油门包络 · 点击编排")

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(BG_DEEP))
        rect = self.rect().adjusted(3, 3, -3, -3)
        p.setPen(QPen(QColor(SEP_LINE), 1.0))
        p.drawLine(QPointF(rect.left(), rect.bottom()),
                   QPointF(rect.right(), rect.bottom()))
        if not self._pts:
            self._draw_ref_line(p, rect)
            return
        p.setPen(self._curve_pen())
        w = max(1, rect.width())
        h = max(1, rect.height())
        prev: Optional[QPointF] = None
        for t, v in self._pts:
            x = rect.left() + w * (t / self._duration if self._duration > 0 else 0.0)
            y = rect.bottom() - h * max(0.0, min(1.0, v))
            cur = QPointF(min(x, rect.right()), y)
            if prev is not None:
                p.drawLine(prev, cur)
            prev = cur
        if self._active and self._elapsed >= 0.0:
            x = rect.left() + w * min(1.0, self._elapsed / self._duration)
            p.setPen(QPen(QColor("#E6B455"), 1.0, Qt.PenStyle.SolidLine))
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        p.setPen(QColor(TEXT_SECONDARY))
        state = "播放中" if self._active else "待机"
        p.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignLeft
                   | Qt.AlignmentFlag.AlignTop,
                   f"包络 {self._duration:.0f}s · {state}")


def attach_envelope_strip(panel, card, layout, tid: str) -> QWidget:
    """把缩略条追加到火车卡片布局（幂等；返回控件）。点击 → 打开编辑器浮窗。"""
    if card is None or layout is None:
        return None
    existing = getattr(card, "_envelope_strip", None)
    if existing is not None:
        existing.refresh()
        return existing
    strip = EnvelopeStrip(panel, tid, card)
    strip.clicked.connect(lambda t, p=panel: _open_editor_for(p, t))
    layout.addWidget(strip)
    try:
        card._envelope_strip = strip
    except Exception:
        log.debug("envelope strip: attach failed", exc_info=True)
    return strip


def _open_editor_for(panel, tid: str) -> None:
    """点击缩略 → 打开编辑器并把火车选中到该车（编辑器读面板选中序）。"""
    try:
        panel._selected_train_id = str(tid)
    except Exception:
        log.debug("envelope strip: select train failed", exc_info=True)
    from .runner_envelope import open_envelope_dialog
    open_envelope_dialog(panel)
    dlg = getattr(panel, "_envelope_dlg", None)
    combo = getattr(dlg, "_train_combo", None) if dlg is not None else None
    if combo is not None:
        idx = combo.findData(str(tid))
        if idx >= 0:
            combo.setCurrentIndex(idx)


def refresh_strips(panel) -> int:
    """刷新全部卡片的缩略条（帧循环节流调用；无条 → 0）。"""
    done = 0
    for card in (getattr(panel, "_cards", None) or {}).values():
        strip = getattr(card, "_envelope_strip", None)
        if strip is None:
            continue
        strip.refresh()
        done += 1
    return done


__all__ = ["EnvelopeStrip", "attach_envelope_strip", "refresh_strips",
           "STRIP_HEIGHT"]
