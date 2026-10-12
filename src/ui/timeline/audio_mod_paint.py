# -*- coding: utf-8 -*-
"""音频调制 clip 卡片绘制（M1 拆模块：绘制与交互分文件）。

原 `audio_mod_lane.py` 的绘制段原样搬移（该文件超 500 行上限，按架构门禁
拆分；视觉语言逐位不变）：R182 外观对齐 clip_view.ClipItem —— 4px 圆角卡体 ·
BG_PANEL 底 · 顶部 3px 轨道色带（调制色 BRIGHT_SUCCESS）· 选中 12% 填充 +
focus ring · 1px 三态描边 · 省略号时长标题 · 两端淡入手柄 · 色带右端
wave-sine 图标 + MOD 徽标（与普通 clip 的区分标记）。

`paint_mod_surface` 为唯一对外绘制入口（`audio_mod_lane` 再导出，旧 import
路径不破坏）。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF

from .. import qascade_theme as _th
from .timeline_grid import second_grid_positions

MOD_HINT_NO_AUDIO = "导入音频后，选中调制轨用画笔工具绘制调制区段（右键=菜单）"

MOD_CARD_RADIUS = 4.0
MOD_BAND_H = 3.0
MOD_SEL_FILL_ALPHA = 31        # SELECT_BLUE ≈12% 底（同 ClipItem.SEL_FILL_ALPHA）
MOD_RING_ALPHA = 255           # B5：focus ring 实色（修前 160 半透 → 用户反馈选中态不清晰）
_MOD_ICON_PX = 16
_wave_pixmap_cache: dict = {}


def _mod_wave_pixmap():
    """色带右端的 wave-sine 小图标（tabler；懒建缓存，绘制路径不做 IO）。"""
    pm = _wave_pixmap_cache.get("pm")
    if pm is None:
        try:
            from ..main_icon_library import load_tabler_icon
            pm = load_tabler_icon("wave-sine", _MOD_ICON_PX, _th.TEXT_PRIMARY).pixmap(
                _MOD_ICON_PX, _MOD_ICON_PX)
        except Exception:
            pm = None
        _wave_pixmap_cache["pm"] = pm
    return pm


def _paint_args(raw) -> tuple:
    """`mod_paint_state` 元组 → (state, duration, px, drag_span, sel_span)。

    4 元素（旧协议）/5 元素（R182 选中高亮）双兼容，坏数据给空态。
    """
    if not isinstance(raw, (tuple, list)) or len(raw) < 4:
        return ({}, 0.0, 100.0, None, None)
    return (raw[0] if isinstance(raw[0], dict) else {},
            float(raw[1] or 0.0), max(1.0, float(raw[2] or 100.0)),
            raw[3], raw[4] if len(raw) > 4 else None)


def _span_key(sel) -> tuple:
    """选中/悬停 clip → (start, end) 键（dict / 二元组 / None 宽容）。"""
    if isinstance(sel, dict):
        return (float(sel.get("start", 0.0)), float(sel.get("end", 0.0)))
    if isinstance(sel, (tuple, list)) and len(sel) == 2:
        try:
            return (float(sel[0]), float(sel[1]))
        except (TypeError, ValueError):
            return ()
    return ()


def _clip_selected(clip: dict, key: tuple) -> bool:
    """clip 是否命中选中键（空键 = 无选中）。"""
    if not key:
        return False
    try:
        return key == (float(clip["start"]), float(clip["end"]))
    except (KeyError, TypeError, ValueError):
        return False


def _paint_mod_clip(p: QPainter, area: QRectF, clip: dict, px: float,
                    selected: bool) -> None:
    """单张调制 clip 卡片（普通 clip 视觉语言；窄卡自动省略装饰）。"""
    x0 = float(clip.get("start", 0.0)) * px
    x1 = float(clip.get("end", 0.0)) * px
    body = QRectF(x0, 3.0, max(3.0, x1 - x0), max(6.0, area.height() - 7.0))
    path = QPainterPath()
    path.addRoundedRect(body, MOD_CARD_RADIUS, MOD_CARD_RADIUS)
    p.fillPath(path, QBrush(QColor(_th.BG_PANEL)))
    if selected:
        fill = QColor(_th.SELECT_BLUE)
        fill.setAlpha(MOD_SEL_FILL_ALPHA)
        p.fillPath(path, QBrush(fill))
    p.save()
    p.setClipPath(path)
    p.fillRect(QRectF(body.left(), body.top(), body.width(), MOD_BAND_H),
               QColor(_th.BRIGHT_SUCCESS))
    p.restore()
    p.setPen(_mod_border_pen(selected))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    if selected:
        ring = QColor(_th.FOCUS_BLUE)
        ring.setAlpha(MOD_RING_ALPHA)
        p.setPen(QPen(ring, 2))       # B5：02 §4 焦点态 = FOCUS_BLUE 2px
        p.drawRoundedRect(body.adjusted(-0.5, -0.5, 0.5, 0.5),
                          MOD_CARD_RADIUS + 1.0, MOD_CARD_RADIUS + 1.0)
    _paint_mod_marks(p, body)
    _paint_mod_label(p, body, clip)


def _mod_border_pen(selected: bool) -> QPen:
    """1px 三态描边：选中 select_blue / 常态白 10%（与 ClipItem 同族）。"""
    if selected:
        return QPen(QColor(_th.SELECT_BLUE), 1)
    return QPen(QColor(255, 255, 255, 26), 1)


def _paint_mod_marks(p: QPainter, body: QRectF) -> None:
    """MOD 徽标 + wave-sine 图标 + 两端淡入手柄（窄/矮卡自动省略）。"""
    if body.height() >= 26.0 and body.width() >= 56.0:
        font = p.font()
        font.setPixelSize(8)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(_th.BRIGHT_SUCCESS))
        p.drawText(QRectF(body.right() - 34.0, body.bottom() - 14.0, 30.0, 12.0),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "MOD")
    if body.width() >= 46.0:
        pm = _mod_wave_pixmap()
        if pm is not None and not pm.isNull():
            p.drawPixmap(int(body.right() - MOD_BAND_H - _MOD_ICON_PX - 1),
                         int(body.top() + MOD_BAND_H + 3), pm)
    if body.width() >= 26.0 and body.height() >= 14.0:
        _paint_mod_fade_handles(p, body)


def _paint_mod_fade_handles(p: QPainter, body: QRectF) -> None:
    """两端淡入手柄（左/右小三角，指向卡内，与普通 clip 手柄同位）。"""
    cy = body.center().y()
    pen = QColor(255, 255, 255, 128)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(pen))
    left = QPolygonF([QPointF(body.left() + 2.0, cy - 4.0),
                      QPointF(body.left() + 2.0, cy + 4.0),
                      QPointF(body.left() + 7.0, cy)])
    right = QPolygonF([QPointF(body.right() - 2.0, cy - 4.0),
                       QPointF(body.right() - 2.0, cy + 4.0),
                       QPointF(body.right() - 7.0, cy)])
    p.drawPolygon(left)
    p.drawPolygon(right)


def _paint_mod_label(p: QPainter, body: QRectF, clip: dict) -> None:
    """省略号时长标题（区段时长文本；窄卡只画色带不画字）。"""
    if body.width() < 30.0 or body.height() < 18.0:
        return
    try:
        span = max(0.0, float(clip.get("end", 0.0)) - float(clip.get("start", 0.0)))
    except (TypeError, ValueError):
        span = 0.0
    font = p.font()
    font.setPixelSize(10)
    p.setFont(font)
    p.setPen(QColor(_th.TEXT_PRIMARY))
    elide_w = max(10, int(body.width() - 16.0))
    text = p.fontMetrics().elidedText(f"{span:.2f}s", Qt.TextElideMode.ElideRight,
                                      elide_w)
    p.drawText(QRectF(body.left() + 5.0, body.top() + MOD_BAND_H + 1.0,
                      body.width() - 10.0, 14.0),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


def _paint_drag_preview(p: QPainter, area: QRectF, drag_span) -> None:
    """画笔拖拽预览框（虚线；与卡片区分）。"""
    x0, x1 = sorted(drag_span)
    body = QRectF(x0, 3.0, max(3.0, x1 - x0), max(6.0, area.height() - 7.0))
    p.setPen(QPen(QColor(_th.FOCUS_BLUE), 1, Qt.PenStyle.DashLine))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(body, MOD_CARD_RADIUS, MOD_CARD_RADIUS)


def _paint_mod_grid(p: QPainter, area: QRectF, px: float, exposed_rect) -> None:
    visible = area.intersected(QRectF(exposed_rect)) if exposed_rect is not None else area
    x0, x1 = max(0, int(visible.left())), min(int(area.width()), int(visible.right()) + 1)
    p.save()
    p.setClipRect(visible, Qt.ClipOperation.IntersectClip)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1, Qt.PenStyle.DotLine))
    for x in second_grid_positions(px, x0, x1):
        p.drawLine(x, 0, x, int(area.height()))
    p.restore()


def paint_mod_surface(p: QPainter, surface, *, exposed_rect=None) -> None:
    """调制画布共绘：clip 卡片（普通 clip 视觉语言）+ 拖拽预览。

    `surface` 鸭子类型：需提供 `rect()`（QWidget 自带）与 `mod_paint_state()`
    → `(state, audio_duration, px_per_sec, drag_span[, sel_span])`；
    drag_span=(x0,x1)|None（像素）；sel_span=(start,end)|dict|None = 选中/悬停。
    """
    area = QRectF(surface.rect())
    state, audio_duration, px, drag_span, sel = _paint_args(surface.mod_paint_state())
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.fillRect(area, QColor(_th.BG_EDITOR))
    _paint_mod_grid(p, area, px, exposed_rect)
    if audio_duration <= 0:
        p.setPen(QColor(_th.TEXT_DISABLED))
        p.drawText(area, Qt.AlignmentFlag.AlignCenter, MOD_HINT_NO_AUDIO)
        return
    sel_key = _span_key(sel)
    for clip in state.get("clips") or []:
        if isinstance(clip, dict):
            _paint_mod_clip(p, area, clip, px, _clip_selected(clip, sel_key))
    if drag_span is not None:
        _paint_drag_preview(p, area, drag_span)
