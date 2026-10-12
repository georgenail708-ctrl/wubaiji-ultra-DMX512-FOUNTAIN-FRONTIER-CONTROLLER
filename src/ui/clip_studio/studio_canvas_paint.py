# -*- coding: utf-8 -*-
"""Clip Studio 画布绘制混入（R172 设计 §3.4）—— 从 studio_canvas 拆出（≤500 行门禁）。

包含静态层缓存（点阵网格底图）与全部 paintEvent 子过程：边/卡片/美术底图/文字/
桩位/拖线预览/HUD。`CanvasPaintMixin` 与宿主（ClipStudioCanvas）以鸭子类型协作，
依赖宿主属性：`_sheet/_view_scale/_view_offset/_sel_node/_sel_edge/_node_status/
_error_edges/_applied_ids/_link/_painted_nodes/_static_cache/_static_key/_static_offset`
与方法 `_px/_s2w/_card_rect/_port_pos/_seg_bezier/_active_nodes/_scale_band`。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
                         QRegion)

from src.ui import qascade_theme as _th
from src.ui.canvas_materials import paint_card_face, selection_fill
from src.ui.clip_studio import studio_master
from src.ui.clip_studio import studio_note
from src.ui.clip_studio import studio_theme_nodes as _nt
from src.ui.clip_studio.studio_art import paint_card_art, paint_empty_canvas_art
from src.ui.clip_studio.studio_cards import (GROUP_COLORS, PORT_COMPOSITE, PORT_IN,
                                             PORT_OUT)
from src.ui.clip_studio.studio_collapse import is_collapsed
from src.ui.clip_studio.studio_graph import edge_index, node_group, param_value
from src.ui.main_icon_library import load_tabler_pixmap

__all__ = ["CanvasPaintMixin", "empty_sheet", "empty_state_texts", "link_reject_reason",
           "_PEN_HAIR", "_LOD_ART", "_GRID_HINT_TEXT"]

_PEN_HAIR = 1.25                 # 发丝线（照 RouteCanvas）
_LOD_ART = 0.6                   # 低于此缩放隐藏美术/参数摘要/内嵌控件（保留 header 色带+标题）
_LOD_GRID_MINOR = 0.35           # 低于此缩放隐藏次级网格点，只留主网格（4 倍间距）
_STATIC_MARGIN = 120             # 静态缓存四周余量（小范围平移位移复用）
_DOT_SPACING_PX = 28.0
_HEADER_H = 24.0                 # 名义 header 高（世界单位）：标题字体档位推导基准（R183-B1）
_HEADER_ICON_PX = 16.0           # header 右端 tabler 图标边长（屏幕像素）
_ART_H_FRAC = 0.58               # content 区美术缩略图高度占比（不再压住摘要）
_PREVIEW_OPACITY = 0.65          # 拖线预览半透明（不压卡；只画到卡边）
_HUD_HINT = "空格=平移"
_GRID_HINT_TEXT = "松手切换视图"
_EMPTY_TITLE = "从左侧拖入卡片"
_EMPTY_HINT = ""

def _summary_value(node, ps) -> str:
    """Format one typed card value; text cards are not numeric fields."""
    value = param_value(node, ps.key)
    if ps.type == "bool":
        return "开" if value else "关"
    if ps.type == "spec":
        return str(value.get("mode", "range"))
    if ps.type == "choice":
        return next((label for key, label in ps.choices if key == str(value)), str(value))
    if ps.type in ("str", "color"):
        return str(value)
    return f"{value:g}{ps.unit}"


def empty_sheet() -> dict:
    """空图占位（每次调用返回新 dict；勿共享引用——画布会原地改 graph 列表）。"""
    return {"id": "", "name": "", "graph": {"nodes": [], "edges": [], "frames": []}}


def empty_state_texts() -> tuple:
    """空画布引导文案（照 RouteCanvas._draw_empty_state 先例；测试断言用）。"""
    return _EMPTY_TITLE, _EMPTY_HINT


def link_reject_reason(src_id: str, probe: tuple) -> str:
    """非法连线目标的原因文案（拖线预览旁小字提示；兼容目标 → ""）。"""
    nid, port, ok = probe
    if ok or not nid:
        return ""
    if nid == str(src_id):
        return "不能连到自己"
    if port == PORT_OUT:
        return "出桩只能作起点：请连到入桩或复合桩"
    return "请拖到入桩（左）或复合桩（下）上松手"


def _status_pen_width(nid, status, selected) -> float:
    """描边宽（R183-B1）：常态 0.8 / 选中或状态 1.2。
    error/warn/applied 的信号由描边色（亮红/亮黄/亮绿）承载，宽度归入选中档。"""
    return _nt.STROKE_SELECTED if (selected or status in ("error", "warn", "applied")) \
        else _nt.STROKE_NORMAL


class CanvasPaintMixin:
    """绘制子过程（paintEvent 编排留在宿主）。"""

    # ── 静态层（点阵网格底图）：实现迁 `studio_canvas_static.StaticLayerMixin`
    # ── 边 ──────────────────────────────────────────────────────────────
    def _draw_edges(self, p: QPainter, vr: QRectF) -> None:
        """边：样式分支（曲线/直角/直线）+ 流动 dash（选中/运行中）；折叠隐藏边跳过。"""
        idx = edge_index(self._active_nodes(), self._sheet["graph"].get("edges", []))
        hidden = self._hidden_ids()
        for e in self._sheet["graph"].get("edges", []):
            src = idx["by_id"].get(str(e.get("from")))
            dst = idx["by_id"].get(str(e.get("to")))
            if src is None or dst is None:
                continue
            if str(src.get("id")) in hidden or str(dst.get("id")) in hidden:
                continue
            p1 = self._port_pos(src, str(e.get("from_port", PORT_OUT)))
            p2 = self._port_pos(dst, str(e.get("to_port", PORT_IN)))
            # 剔除余量：三种样式恒在两端点 bbox 内，仅需覆盖发丝线宽与桩环：
            # 只扩 8px 时长边贴近视口边缘仍可能漏画 → 滚动/拖动出现残影断弧。
            dist = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
            margin = max(self._px(8.0), 0.25 * dist)
            bbox = self._card_rect(src).united(self._card_rect(dst))
            if not vr.adjusted(-margin, -margin, margin, margin).intersects(bbox):
                continue
            path = self._edge_path(p1, p2, str(e.get("from_port", PORT_OUT)),
                                   str(e.get("to_port", PORT_IN)))
            eid = str(e.get("id", ""))
            p.setPen(self.apply_flow_dash(self._edge_pen(eid), eid))   # #12 流动
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

    def _edge_pen(self, eid: str) -> QPen:
        """错误边红虚线 / 选中蓝 / 悬停浅高亮 / 常态低对比白。"""
        if eid in self._error_edges:
            return QPen(QColor(_th.BRIGHT_ERROR), self._px(_PEN_HAIR + 0.4),
                        Qt.PenStyle.DashLine)
        if eid == self._sel_edge:
            return QPen(QColor(_th.CANVAS_SELECTION), self._px(_PEN_HAIR + 0.6))
        if eid and eid == getattr(self, "_hover_eid", ""):
            return QPen(QColor(_th.BRAND_ORANGE_INK), self._px(_PEN_HAIR + 0.3))
        return QPen(QColor(_th.TEXT_SECONDARY), self._px(_PEN_HAIR))

    # ── 节点卡片 ────────────────────────────────────────────────────────
    def _draw_nodes(self, p: QPainter, vr: QRectF) -> None:
        """可见卡（折叠隐藏子卡跳过）；便签走贴纸自绘，普通卡走两区结构。"""
        hidden = self._hidden_ids()
        for n in self._active_nodes():
            if str(n.get("id")) in hidden:
                continue
            r = self._card_rect(n)
            if not vr.adjusted(-self._px(10), -self._px(10),
                               self._px(10), self._px(10)).intersects(r):
                continue
            self._painted_nodes += 1
            self._draw_node_card(p, n, r)
        if self._link is not None:
            self._draw_link_preview(p)

    def _node_stroke_color(self, nid: str, selected: bool, active: bool = False) -> QColor:
        """R173 Group2：active（多选中最后操作那张）比其余选中更亮——照节点编辑器惯例
        active=白/其余 selected=焦点蓝 的双层区分先例（不引入新令牌，用既有主题色）。
        R182：未选中常态禁止与底色同色（旧=BORDER_NORMAL=BG_PANEL 无轮廓）→
        用更亮的 TEXT_DISABLED 作常态轮廓，hover 抬到 TEXT_TERTIARY。"""
        status = self._node_status.get(nid, "")
        if status == "error":
            return QColor(_th.BRIGHT_ERROR)
        if status == "warn":
            return QColor(_th.BRIGHT_WARN)
        if nid in self._applied_ids:
            return QColor(_th.BRIGHT_SUCCESS)
        if active:
            return QColor(_th.BRAND_ORANGE)
        if selected:
            return QColor(_th.CANVAS_SELECTION)
        if nid and nid == self._hover_nid:
            return QColor(_th.TEXT_TERTIARY)
        return QColor(_th.CANVAS_BORDER)

    def _header_h(self, r: QRectF) -> float:
        """header 高 = 标题文字高 + 4（R183-B1）；不超过卡高 32%
        （低缩放下不吞卡）。标题字体档位由名义高 `_HEADER_H` 推导（与旧值连续）。"""
        fm = QFontMetrics(self._header_font())
        return min(float(fm.height()) + _nt.HEADER_TEXT_EXTRA, r.height() * 0.32)

    def _header_font(self) -> QFont:
        """header 标题字体（世界单位像素档 9-13；由名义 header 高推导，与 R182 连续）。"""
        f = QFont()
        f.setPixelSize(max(9, min(13, int(self._px(_HEADER_H) * 0.55))))
        f.setBold(True)
        return f

    def _content_rect(self, r: QRectF) -> QRectF:
        """content 区（header 下、卡内缩进；美术/摘要/内嵌控件共用，防两处几何漂移）。"""
        top = r.top() + self._header_h(r) + 3.0
        return QRectF(r.left() + 5.0, top, r.width() - 10.0, max(0.0, r.bottom() - 5.0 - top))

    def _draw_node_card(self, p: QPainter, node, r: QRectF) -> None:
        """R182 两区结构（专业软件范式）：header 色带（组色+标题+图标）+
        content 区（美术缩略图 / 参数摘要 elide / 主控过渡卡内嵌控件）。
        R183-B1 像素常量对齐（`studio_theme_nodes`）：圆角 4.0、
        标题底条 rgba(0,0,0,80)+padding(3,2)+圆角 3、header=文字高+4、描边 0.8/1.2。
        桩位画在描边**之下**（卡轮廓连续，不再被 8.2px 桩环切断）；描边最后画。

        R183 W2-ST：#3 便签走贴纸自绘；#6/#14 折叠卡只画 header+徽标；
        #17 禁用卡叠灰色覆盖 + 灰描边。"""
        nid = str(node.get("id", ""))
        spec = self.node_spec(node.get("key"))
        if studio_note.is_note(node):
            self.paint_note_card(p, node, r)
            self._draw_note_badge(p, r)
            self._draw_card_stroke(p, node, r, nid)
            return
        selected = nid in getattr(self, "_sel_nodes", set()) or nid == self._sel_node
        active = nid == self._sel_node and nid != ""
        low = self._view_scale < _LOD_ART
        collapsed = is_collapsed(node)
        paint_card_face(p, r, _nt.NODE_RADIUS)
        self._draw_card_header(p, node, spec, r)
        if collapsed:
            self.draw_collapsed_badge(p, node, r)      # #6/#14 徽标
        elif spec is not None and not low:
            self._draw_card_content(p, node, spec, r)
        self._draw_node_ports(p, node, spec, nid)
        if node.get("disabled"):
            self.paint_disabled_overlay(p, node, r)    # #17
        self._draw_card_stroke(p, node, r, nid)

    def _draw_card_stroke(self, p: QPainter, node, r: QRectF, nid: str) -> None:
        """卡描边 + 搜索高亮环（#9）——便签/普通卡/折叠卡共用收尾（唯一实现点）。"""
        selected = nid in getattr(self, "_sel_nodes", set()) or nid == self._sel_node
        active = nid == self._sel_node and nid != ""
        status = self._node_status.get(nid, "")
        base = self._node_stroke_color(nid, selected, active)
        if node.get("disabled"):
            base = self.stroke_color_for(node, selected, active, base)
        p.setPen(QPen(base, self._px(_status_pen_width(nid, status, selected))))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, _nt.NODE_RADIUS, _nt.NODE_RADIUS)
        self.draw_highlight(p, node, r)                # #9：搜索 2s 高亮

    def _draw_card_header(self, p: QPainter, node, spec, r: QRectF) -> None:
        """header 色带：组色（`GROUP_COLORS`，未知组回退 BORDER_NORMAL）叠加深色半透明
        层 → 白字标题可配；右端 `spec.icon`（tabler 16px，此前字段从未使用）。"""
        head = QRectF(r.left(), r.top(), r.width(), self._header_h(r))
        self._fill_header_band(p, node, r, head)
        self._draw_header_title(p, node, spec, r)
        if spec is not None:
            self._draw_header_icon(p, spec, r, head)
            if studio_master.is_master_card(node):
                studio_master.paint_add_button(p, r, head.height(), self._px(1.0))

    @staticmethod
    def _fill_header_band(p: QPainter, node, r: QRectF, head: QRectF) -> None:
        """铺 header 底色：组色带（R182 语义保留）+ 标题底条
        （rgba(0,0,0,80) 内缩 padding(3,2) 圆角 3，R183-B1），均裁剪到卡圆角内。"""
        p.save()
        path = QPainterPath()
        path.addRoundedRect(r, _nt.NODE_RADIUS, _nt.NODE_RADIUS)
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(_th.CANVAS_HEADER)))
        p.drawRect(head)
        group_color = QColor(GROUP_COLORS.get(node_group(node), "") or _th.BORDER_NORMAL)
        p.fillRect(QRectF(head.left(), head.top(), head.width(), 3.0), group_color)
        pad_w, pad_h = _nt.HEADER_PAD
        bar = head.adjusted(pad_w, pad_h, -pad_w, -pad_h)
        if bar.width() > 2.0 and bar.height() > 1.0:
            p.setBrush(QBrush(QColor(_th.CANVAS_HEADER)))
            p.drawRoundedRect(bar, _nt.HEADER_RADIUS, _nt.HEADER_RADIUS)
        p.restore()

    @staticmethod
    def _header_title_reserve(node, spec) -> float:
        """标题右侧预留：图标 26；主控过渡卡另有 header 加行按钮 → 48。"""
        if spec is None:
            return 9.0
        return 48.0 if studio_master.is_master_card(node) else 26.0

    def _draw_header_title(self, p: QPainter, node, spec, r: QRectF) -> None:
        """白字标题（13px 上限、随 header 高收缩），elide 到 header 可读宽。"""
        head = QRectF(r.left(), r.top(), r.width(), self._header_h(r))
        reserve = self._header_title_reserve(node, spec)
        f = self._header_font()
        p.setFont(f)
        p.setPen(QPen(QColor(_th.TEXT_PRIMARY)))
        name = getattr(spec, "name", "") if spec is not None else str(node.get("key", "?"))
        width = max(16.0, r.width() - reserve - 9.0)
        text = QFontMetrics(f).elidedText(str(name), Qt.TextElideMode.ElideRight, int(width))
        p.drawText(QRectF(r.left() + 9.0, head.top(), width, head.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)

    def _draw_header_icon(self, p: QPainter, spec, r: QRectF, head: QRectF) -> None:
        """右端 tabler 图标（`load_tabler_pixmap`，屏幕恒定 16px 上限）。"""
        pm = load_tabler_pixmap(str(spec.icon), int(_HEADER_ICON_PX), _th.TEXT_PRIMARY)
        if pm is None or pm.isNull():
            return
        d = max(8.0, min(self._px(_HEADER_ICON_PX), head.height() - 6.0))
        rect = QRectF(r.right() - 6.0 - d, head.center().y() - d / 2.0, d, d)
        p.drawPixmap(rect, pm, QRectF(pm.rect()))

    def _draw_card_content(self, p: QPainter, node, spec, r: QRectF) -> None:
        """content 区：主控过渡卡画内嵌控件；其余卡画缩略美术 + 底部 elide 参数摘要。"""
        if studio_master.is_master_card(node):
            studio_master.paint_rows(p, node, r, self._header_h(r), self._px(1.0))
            return
        self._draw_card_art(p, node, spec, r)
        if spec.params:
            self._draw_node_summary(p, node, spec, r)

    def _draw_card_art(self, p: QPainter, node, spec, r: QRectF) -> None:
        """美术缩略图：content 区居中偏上（约 content 高 58%），不再与摘要同区叠画。"""
        content = self._content_rect(r)
        art_h = content.height() * _ART_H_FRAC
        art_w = min(content.width() * 0.72, art_h * 1.5)
        rect = QRectF(content.center().x() - art_w / 2.0, content.top() + 2.0, art_w, art_h)
        paint_card_art(p, rect, spec.art, self._node_status.get(str(node.get("id", "")), ""))

    def _draw_node_summary(self, p: QPainter, node, spec, r: QRectF) -> None:
        """参数摘要一行（content 底部），`QFontMetrics.elidedText` 截断（宽=卡宽-18）。"""
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(QPen(QColor(_th.TEXT_SECONDARY)))
        content = self._content_rect(r)
        p.drawText(QRectF(r.left() + 9.0, content.bottom() - 14.0, r.width() - 18.0, 14.0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self.elided_summary(node, spec, int(r.width() - 18.0)))

    def elided_summary(self, node, spec, width: int = 158) -> str:
        """摘要文本 elide（测试断言入口：长摘要必须以省略号截断，短摘要原样）。"""
        f = QFont()
        f.setPixelSize(10)
        return QFontMetrics(f).elidedText(self._summary_text(node, spec),
                                         Qt.TextElideMode.ElideRight, int(width))

    @staticmethod
    def _summary_text(node, spec) -> str:
        """参数摘要一行（LOD 低档不画；`rows` 动态行表卡不适用=空串）。"""
        parts = []
        for ps in spec.params[:3]:
            if ps.type == "rows":
                continue
            text = _summary_value(node, ps)
            parts.append(f"{ps.label} {text}")
        return " · ".join(parts)

    def _draw_node_ports(self, p: QPainter, node, spec, nid: str) -> None:
        """port = 小圆桩（in 左 / out 右 / 主方向卡另有 composite 桩）。"""
        if spec is None:
            return
        pr = self._px(5.0)
        for port in spec.ports:
            c = self._port_pos(node, port)
            fill = QColor(_th.BG_BUTTON) if port == PORT_OUT else QColor(_th.BG_PANEL_SUB)
            if self._link is not None and self._link.get("probe", ("", "", False))[:2] == (nid, port):
                fill = QColor(_th.BRIGHT_SUCCESS if self._link["probe"][2] else _th.BRIGHT_ERROR)
            p.setPen(QPen(QColor(_th.TEXT_SECONDARY), self._px(1.0)))
            p.setBrush(QBrush(fill))
            p.drawEllipse(c, pr, pr)
            if port == PORT_COMPOSITE:
                p.setPen(QPen(QColor(_th.BRIGHT_WARN), self._px(1.0)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, pr + self._px(2.2), pr + self._px(2.2))

    # ── 拖线预览 / HUD ──────────────────────────────────────────────────
    def _draw_link_preview(self, p: QPainter) -> None:
        """拖线预览：源桩 → 光标（半透明 0.65，且**不压在卡片上**=视觉上只到卡边即止）；
        合法目标绿圈，非法目标红圈/红叉 + 原因小字（环与文字不裁剪、全不透明）。"""
        src = self._node_by_id(self._link["src"])
        if src is None:
            return
        cur = self._link["cur"]
        probe_nid, probe_port, probe_ok = self._link.get("probe", ("", "", False))
        p.save()
        p.setOpacity(_PREVIEW_OPACITY)
        p.setClipRegion(self._cards_excluded_region())
        p.setPen(QPen(QColor(_th.CANVAS_SELECTION), self._px(_PEN_HAIR + 0.4), Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._edge_path(self._port_pos(src, PORT_OUT), cur, PORT_OUT,
                                   probe_port if probe_ok else PORT_IN))
        p.restore()
        if probe_nid and probe_port:
            c = self._port_pos(self._node_by_id(probe_nid), probe_port)
            p.setPen(QPen(QColor(_th.BRIGHT_SUCCESS if probe_ok else _th.BRIGHT_ERROR),
                          self._px(1.6)))
            pr = self._px(7.0)
            p.drawEllipse(c, pr, pr)
        elif probe_nid:
            self._draw_reject_cross(p, cur)
        self._draw_reject_reason(p, cur)

    def _cards_excluded_region(self) -> QRegion:
        """视口区域 − 全部卡片矩形（世界坐标；拖线预览剪裁用，卡上不画预览线）。"""
        vr = self._vr()
        region = QRegion(QRect(int(vr.left()) - 2, int(vr.top()) - 2,
                               int(vr.width()) + 4, int(vr.height()) + 4))
        for n in self._active_nodes():
            region -= QRegion(self._card_rect(n).toAlignedRect())
        return region

    def _draw_reject_reason(self, p: QPainter, cur: QPointF) -> None:
        """非法目标时的原因小字（屏幕恒定字号，红；B9 错误连线提示）。"""
        reason = link_reject_reason(str(self._link["src"]),
                                    self._link.get("probe", ("", "", False)))
        if not reason:
            return
        f = QFont()
        f.setPixelSize(max(9, int(round(self._px(11)))))
        p.setFont(f)
        p.setPen(QPen(QColor(_th.BRIGHT_ERROR)))
        p.drawText(QPointF(cur.x() + self._px(12), cur.y() - self._px(10)), reason)

    def _draw_reject_cross(self, p: QPainter, c: QPointF) -> None:
        d = self._px(7.0)
        pen = QPen(QColor(_th.BRIGHT_ERROR), self._px(2.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))

    def _draw_rubber_band(self, p: QPainter) -> None:
        """R173 Group2：空白区左键拖拽框选——半透明蓝底 + 实线描边（世界坐标绘制）。"""
        r = QRectF(self._rubber_band["start"], self._rubber_band["cur"]).normalized()
        p.setPen(QPen(QColor(_th.CANVAS_SELECTION), self._px(1.0)))
        p.setBrush(QBrush(selection_fill(40)))
        p.drawRect(r)

    def _draw_empty_state(self, p: QPainter) -> None:
        """空 sheet 居中引导（R182 文案「从左侧拖入卡片」；R183-B2 美术两件——
        B0 插画 + `pat_floating_cogs` 低透明底纹（0.05，`_empty_texture_on` 可关）——
        绘制实现移 `studio_art.paint_empty_canvas_art`（本文件 500 行门禁）。"""
        w, h = self.width(), self.height()
        if w < 100 or h < 60:
            return
        paint_empty_canvas_art(p, w, h, getattr(self, "_empty_texture_on", True))
        m = 20.0
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1.0, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(m, m, w - 2 * m, h - 2 * m), 6, 6)
        f = QFont()
        f.setPixelSize(13)
        p.setFont(f)
        p.setPen(QPen(QColor(_th.TEXT_SECONDARY)))
        p.drawText(QRectF(0, h * 0.45, w, 28), Qt.AlignmentFlag.AlignCenter, _EMPTY_TITLE)

    def _draw_hud(self, p: QPainter) -> None:
        """左下角缩放比例 + 快捷键提示（照 RouteCanvas HUD 药丸，简化）。"""
        if self.width() < 90 or self.height() < 40:
            return
        f = QFont()
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QPen(QColor(_th.TEXT_SECONDARY)))
        p.drawText(QRectF(8, self.height() - 20, 60, 16),
                   Qt.AlignmentFlag.AlignLeft, f"{self._view_scale:.0%}")
        if self.width() >= 420:
            p.drawText(QRectF(70, self.height() - 20, self.width() - 78, 16),
                       Qt.AlignmentFlag.AlignLeft, _HUD_HINT)

    # ── 四宫格预显虚框（R178：宽画布自动弹出，拖卡进入即启用）─────────────
    def _draw_grid_preview(self, p: QPainter) -> None:
        """半透明 2x2 预显 overlay（节点编辑器拖入高亮先例）：虚框 + 分隔线 +
        引导文案。画在屏幕坐标系（paintEvent restore 之后），不随缩放。"""
        r = self._grid_hint_rect()
        if r.width() < 80 or r.height() < 60:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(selection_fill(26)))
        p.drawRoundedRect(r, 6, 6)
        pen = QPen(QColor(_th.CANVAS_SELECTION), 1.4, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 6, 6)
        cx, cy = r.center().x(), r.center().y()
        p.drawLine(QPointF(r.left(), cy), QPointF(r.right(), cy))   # 2x2 分隔
        p.drawLine(QPointF(cx, r.top()), QPointF(cx, r.bottom()))
        f = QFont()
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QPen(QColor(_th.TEXT_SECONDARY)))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, _GRID_HINT_TEXT)
        p.restore()
