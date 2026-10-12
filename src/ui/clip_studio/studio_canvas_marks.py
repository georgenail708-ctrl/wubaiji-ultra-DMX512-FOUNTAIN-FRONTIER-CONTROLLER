# -*- coding: utf-8 -*-
"""画布新增视觉层（R183 W2-ST #3/#6/#9/#11/#12/#14/#17 的绘制子过程）。

从 `studio_canvas_paint` 拆出（该文件 500 行门禁已满）。宿主 = ClipStudioCanvas，
鸭子类型依赖：`_px/_card_rect/_header_h/_port_pos/_sel_nodes/_sel_node/_node_status/
_hidden_ids/_node_by_id/_flow_dash/_insert/_highlighted_node` 等。

内容：便签贴纸卡 / 折叠徽标 / 禁用灰色覆盖 / 搜索高亮环 / 边上插入预览。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainterPath, QPen

from src.ui import qascade_theme as _th
from src.ui.clip_studio import studio_theme_nodes as _nt
from src.ui.clip_studio.studio_cards import GROUP_COLORS
from src.ui.clip_studio.studio_collapse import collapsed_badge_text, is_collapsed
from src.ui.clip_studio.studio_note import (NOTE_FILL, NOTE_LINE, NOTE_MAX_LEN, NOTE_TEXT,
                                            is_note, note_text)

__all__ = ["CanvasMarksMixin"]

_NOTE_RADIUS = 4.0
_NOTE_PAD = 8.0
_NOTE_FONT_PX = 10
_BADGE_H = 16.0
_HL_WIDTH = 2.4
_INSERT_DASH = (4.0, 3.0)
_DISABLED_ALPHA = 150


class CanvasMarksMixin:
    """新增视觉层的绘制入口（paintEvent / _draw_node_card 调用；宿主 = 画布）。"""

    # ── 便签贴纸（#3）───────────────────────────────────────────────────
    def paint_note_card(self, p, node, r: QRectF) -> None:
        """贴纸样式：暖黄底 + 深色描边 + 多行文本（左上标题、正文自动折行截断）。"""
        p.setPen(QPen(QColor(NOTE_LINE), self._px(1.0)))
        p.setBrush(QBrush(QColor(NOTE_FILL)))
        p.drawRoundedRect(r, _NOTE_RADIUS, _NOTE_RADIUS)
        f = QFont()
        f.setPixelSize(max(8, min(13, int(self._px(_NOTE_FONT_PX)))))
        p.setFont(f)
        p.setPen(QPen(QColor(NOTE_TEXT)))
        pad = self._px(_NOTE_PAD)
        text = note_text(node) or "双击写点备注…"
        rect = QRectF(r.left() + pad, r.top() + pad,
                      max(8.0, r.width() - 2 * pad), max(8.0, r.height() - 2 * pad))
        p.drawText(rect, int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
                             | Qt.TextFlag.TextWordWrap), text[:NOTE_MAX_LEN])

    def _draw_note_badge(self, p, r: QRectF) -> None:
        """右上角「便签」折角小三角（贴纸识别，1 个 path 无文字）。"""
        d = min(self._px(12.0), r.width() * 0.2, r.height() * 0.3)
        if d < 4.0:
            return
        path = QPainterPath()
        path.moveTo(r.right() - d, r.top())
        path.lineTo(r.right(), r.top())
        path.lineTo(r.right(), r.top() + d)
        path.closeSubpath()
        col = QColor(NOTE_LINE)
        col.setAlpha(160)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(col))
        p.drawPath(path)

    # ── 折叠徽标（#6/#14）──────────────────────────────────────────────
    def draw_collapsed_badge(self, p, node, r: QRectF) -> None:
        """折叠卡标题条右侧徽标：内部 N 张 / 已折叠（贴 header 右端，深色底白字）。"""
        text = collapsed_badge_text(node)
        f = QFont()
        f.setPixelSize(max(8, min(11, int(self._px(10.0)))))
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        w = min(max(24.0, fm.horizontalAdvance(text) + 10.0), r.width() * 0.5)
        h = min(_BADGE_H if self._px(_BADGE_H) > 14.0 else 14.0, r.height())
        rect = QRectF(r.right() - w - 4.0, r.top() + (self._header_h(r) - h) / 2.0, w, h)
        if rect.left() < r.left() + 4.0:
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(_th.BG_INPUT)))
        p.drawRoundedRect(rect, 3.0, 3.0)
        p.setPen(QPen(QColor(_th.TEXT_PRIMARY)))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, int(w - 4.0)))

    # ── 禁用灰色覆盖（#17）─────────────────────────────────────────────
    def paint_disabled_overlay(self, p, node, r: QRectF) -> None:
        """禁用卡：整卡压一层半透明底色（Bypass 视觉语言，静音同族）。"""
        p.setPen(Qt.PenStyle.NoPen)
        col = QColor(_th.BG_ROOT)
        col.setAlpha(_DISABLED_ALPHA)
        p.setBrush(QBrush(col))
        p.drawRoundedRect(r, _nt.NODE_RADIUS, _nt.NODE_RADIUS)

    @staticmethod
    def stroke_color_for(node, selected: bool, active: bool, base) -> QColor:
        """描边色裁决：禁用 → 暗灰（压过状态色）；否则用既有 `_node_stroke_color`。"""
        if isinstance(node, dict) and node.get("disabled"):
            return QColor(_th.TEXT_DISABLED)
        return base

    # ── 搜索高亮（#9）──────────────────────────────────────────────────
    def draw_highlight(self, p, node, r: QRectF) -> None:
        """2s 高亮：卡外一圈加粗亮蓝描边（搜索命中反馈）。"""
        if not self._hl_nid or str(node.get("id")) != str(self._hl_nid):
            return
        pen = QPen(QColor(_th.FOCUS_BLUE), self._px(_HL_WIDTH))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pad = self._px(3.0)
        p.drawRoundedRect(r.adjusted(-pad, -pad, pad, pad), _nt.NODE_RADIUS, _nt.NODE_RADIUS)

    # ── 边上插入预览（#11）─────────────────────────────────────────────
    def draw_insert_preview(self, p) -> None:
        """拖动边中点：中点热点圆点 + 到光标的虚线 + 落点绿/红提示圈。"""
        ins = self._insert
        if ins is None:
            return
        mid, cur = QPointF(ins["mid"]), QPointF(ins["cur"])
        p.setPen(QPen(QColor(_th.FOCUS_BLUE), self._px(1.6), Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(mid, cur)
        p.setBrush(QBrush(QColor(_th.FOCUS_BLUE)))
        p.setPen(Qt.PenStyle.NoPen)
        r = self._px(3.5)
        p.drawEllipse(mid, r, r)
        target = self._node_at(cur)
        ok = bool(target is not None and self._can_relay(target, {"from": "", "to": ""}))
        col = QColor(_th.BRIGHT_SUCCESS if ok else _th.BRIGHT_WARN)
        p.setPen(QPen(col, self._px(1.6)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        g = self._px(6.0)
        p.drawEllipse(cur, g, g)

    # ── 流动 dash（#12）────────────────────────────────────────────────
    def apply_flow_dash(self, pen: QPen, eid) -> QPen:
        """给边笔刷叠 dash 相位（流动动画）；不参与动画 → 原笔返回。"""
        dash = None
        try:
            dash = self._flow_dash(eid)
        except Exception:
            dash = None
        if not dash:
            return pen
        pattern, phase = dash
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([float(p) for p in pattern])
        pen.setDashOffset(float(phase))
        return pen


def is_note_node(node) -> bool:
    """模块级便捷判据（外部只读入口；与 `studio_note.is_note` 同源）。"""
    return is_note(node)


def collapsed_state(node) -> bool:
    """模块级便捷判据（折叠态；与 `studio_collapse.is_collapsed` 同源）。"""
    return is_collapsed(node)


def group_color_of(node) -> QColor:
    """卡色条颜色（组色查表；未知回退 BORDER_NORMAL）—— 给便签/缩略图复用。"""
    from src.ui.clip_studio.studio_graph import node_group
    return QColor(GROUP_COLORS.get(node_group(node), "") or _th.BORDER_NORMAL)
