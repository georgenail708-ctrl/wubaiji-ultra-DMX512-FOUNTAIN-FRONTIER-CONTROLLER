# -*- coding: utf-8 -*-
"""Draw 画布绘制子过程（R182；R183 修复轮重排）—— 点阵网格 / 卡片 / 连线 / 桩位 / HUD。

与宿主（`DrawCanvas`）鸭子类型协作，依赖宿主属性：
`_graph/_view_scale/_view_offset/_sel_ids/_hover_nid/_link/_drag/_slider/
_painted_nodes/_static_cache/_static_key/_static_offset/_error_edges` 与方法
`_px/_s2w/_card_rect/_rect_of/_out_port/_in_port/_seg_bezier/_scale_band`。

R183 修复轮（01 清单 B2/B4/D1）：卡片本体绘制迁至 `draw_card_skin`（五态 / 8px
圆角 / 标题带 + tabler 图标 / 公共领域画作卡面 / 阶梯字号 / 缓存字体与色对象），
本模块只留编排；视觉体系对齐 `src/ui/clip_studio`，独立实现不 import 其私有件。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap

from ..qascade_theme import (
    BG_BUTTON,
    BG_PANEL_SUB,
    BG_ROOT,
    BORDER_NORMAL,
    BRIGHT_ERROR,
    BRIGHT_SUCCESS,
    BRAND_ORANGE_INK as FOCUS_BLUE,
    TEXT_DISABLED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    cached_font,
)
from . import draw_card_skin as skin
from .. import qascade_theme as theme
from ..canvas_materials import paint_bed, selection_fill
from . import draw_graph as dc
from .draw_canvas_geom import xy
from .draw_cards import (RULE_BAR, SOURCE_BAR, feature_label, icon_for_feature,
                         template_label)
from .draw_effects import effect_icon, effect_label
from .draw_phase_viz import phase_fan_dots

__all__ = ["DrawPaintMixin", "empty_graph", "empty_state_texts"]

_PEN_HAIR = 1.25
_LOD_DETAIL = skin.LOD_DETAIL   # 低于此缩放隐藏卡内控件（改画屏幕等大标题 + 满幅画作）
_DOT_SPACING = 28.0
_STATIC_MARGIN = 120
_GRID_MINOR_LOD = 0.35
_CARD_RADIUS = skin.CARD_RADIUS

_EMPTY_TITLE = "空调制图：从左侧拖入源卡与规则卡开始"
_EMPTY_HINT = "源卡 → 规则卡连线=信号供给；双击规则卡改波形/量；一根源可连多卡"

#: 点阵网格双档（静态层重建时用；模块级预建，非逐帧构造）
_GRID_MINOR = QColor(theme.CANVAS_GRID_MINOR)
_GRID_MAJOR = QColor(theme.CANVAS_GRID_MAJOR)
#: 框选底（焦点蓝 16% 透明；与修前同值）
_RUBBER_FILL = selection_fill(40)
_ICON_CACHE: dict = {}
#: 兼容旧名：波形点列缓存已迁入 draw_card_skin
_WAVE_PTS_CACHE = skin._WAVE_PTS


def empty_graph() -> dict:
    """空图占位（每次新对象；画布会原地改 nodes/edges）。"""
    return {"nodes": [], "edges": []}


def empty_state_texts() -> tuple:
    return _EMPTY_TITLE, _EMPTY_HINT


def card_icon(name: str, color: str = TEXT_PRIMARY, size: int = 16):
    """tabler 小图标（懒建缓存；失败给 None，绘制不阻塞）。"""
    key = (str(name), str(color), int(size))
    if key not in _ICON_CACHE:
        try:
            from ..main_icon_library import load_tabler_icon
            pm = load_tabler_icon(key[0], size, color).pixmap(size, size)
        except Exception:
            pm = None
        _ICON_CACHE[key] = pm
    return _ICON_CACHE[key]


def draw_card_icon(p: QPainter, name: str, x: float, y: float, *,
                   size: int = 16, color: str = TEXT_PRIMARY) -> None:
    """兼容入口：委托 skin.draw_icon（2× 源位图 + 平滑缩放，高分屏/放大不糊）。"""
    skin.draw_icon(p, name, QRectF(float(x), float(y), float(size), float(size)), color)


def _draw_phase_fan_dots(p: QPainter, box: QRectF, spread_deg: float, *,
                         count: int = 8) -> None:
    """spread 轨道每灯相位点（兼容入口；实现见 draw_phase_viz）。"""
    phase_fan_dots(p, box, spread_deg, count)


def _draw_overflow_tag(p: QPainter, box: QRectF, count: int) -> None:
    """chips 截断 \"+N\" 徽标（兼容入口；实现见 draw_card_skin）。"""
    skin.paint_overflow_tag(p, box, count)


class DrawPaintMixin:
    """绘制子过程（paintEvent 编排留在宿主）。"""

    # ── 静态层（点阵网格底图，照 clip_studio/RouteCanvas 先例）───────────
    def _ensure_static(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return
        key = (w, h, self._scale_band(), self.devicePixelRatioF(), theme.CANVAS_REVISION)
        dx = dy = 0.0
        ok = self._static_cache is not None and self._static_key == key
        if ok:
            dx = self._view_offset.x() - self._static_offset.x()
            dy = self._view_offset.y() - self._static_offset.y()
            ok = abs(dx) <= _STATIC_MARGIN and abs(dy) <= _STATIC_MARGIN
        if not ok:
            self._rebuild_static()
            self._static_offset = QPointF(self._view_offset)
            dx = dy = 0.0
        if self._static_cache is not None:
            p.drawPixmap(QPointF(-_STATIC_MARGIN + dx, -_STATIC_MARGIN + dy),
                         self._static_cache)

    def _rebuild_static(self) -> None:
        w, h = self.width(), self.height()
        dpr = max(1.0, float(self.devicePixelRatioF()))   # C7：静态层按 DPR 栅格化
        pm = QPixmap(int((w + _STATIC_MARGIN * 2) * dpr), int((h + _STATIC_MARGIN * 2) * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(BG_ROOT))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_bed(p, QRectF(0, 0, w + _STATIC_MARGIN * 2, h + _STATIC_MARGIN * 2))
        p.translate(_STATIC_MARGIN, _STATIC_MARGIN)
        p.translate(self._view_offset)
        p.scale(self._view_scale, self._view_scale)
        tl = self._s2w(QPointF(-_STATIC_MARGIN, -_STATIC_MARGIN))
        br = self._s2w(QPointF(w + _STATIC_MARGIN, h + _STATIC_MARGIN))
        self._draw_dot_grid(p, tl, br, self._px(_DOT_SPACING))
        p.end()
        self._static_cache = pm
        self._static_key = (w, h, self._scale_band(), self.devicePixelRatioF(),
                            theme.CANVAS_REVISION)

    def _draw_dot_grid(self, p: QPainter, tl: QPointF, br: QPointF, sp: float) -> None:
        """双档点阵：次级每格一点，主级每 4 格更亮；缩小到阈值只画主级。"""
        low = self._view_scale < _GRID_MINOR_LOD
        minor = QPen(_GRID_MINOR, self._px(0.85))
        major = QPen(_GRID_MAJOR, self._px(1.1))
        x0 = math.floor(tl.x() / sp) * sp
        col = int(round(x0 / sp))
        x = x0
        while x <= br.x():
            if not low or col % 4 == 0:
                p.setPen(major if col % 4 == 0 else minor)
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

    # ── 连线 ────────────────────────────────────────────────────────────
    def _draw_edges(self, p: QPainter, vr: QRectF) -> None:
        graph = self._graph
        for e in graph.get("edges") or []:
            src = dc.node_by_id(graph, e.get("from"))
            dst = dc.node_by_id(graph, e.get("to"))
            if src is None or dst is None:
                continue
            bbox = self._rect_of(src).united(self._rect_of(dst))
            if not vr.adjusted(-40, -40, 40, 40).intersects(bbox):
                continue
            p.setPen(self._edge_pen(str(e.get("id", ""))))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._seg_bezier(self._out_port(src), self._in_port(dst)))

    def _edge_pen(self, eid: str) -> QPen:
        if eid in self._error_edges:
            return skin.pen(BRIGHT_ERROR, self._px(_PEN_HAIR + 0.4), dash=True)
        return skin.pen(BRIGHT_SUCCESS, self._px(_PEN_HAIR + 0.2))

    # ── 卡片（本体绘制见 draw_card_skin）────────────────────────────────
    def _draw_cards(self, p: QPainter, vr: QRectF) -> None:
        for node in self._graph.get("nodes") or []:
            r = self._card_rect(node)
            if not vr.adjusted(-self._px(10), -self._px(10),
                               self._px(10), self._px(10)).intersects(r):
                continue
            self._painted_nodes += 1
            if str(node.get("kind", "")) == "source":
                self._draw_source_card(p, node, r)
            else:
                self._draw_rule_card(p, node, r)
        if self._link is not None:
            self._draw_link_preview(p)

    def _stroke_color(self, nid: str) -> QColor:
        """兼容查询：节点描边色（与 skin 五态同源）。"""
        if nid in getattr(self, "_sel_ids", set()):
            return skin.color(FOCUS_BLUE)
        if nid and nid == getattr(self, "_hover_nid", ""):
            return skin.color(TEXT_SECONDARY)
        return skin.color(BORDER_NORMAL)

    def _draw_source_card(self, p: QPainter, node, r: QRectF) -> None:
        feat = str(node.get("feature", ""))
        look = skin.CardLook(
            title=feature_label(feat),
            icon=str(node.get("icon", "") or icon_for_feature(feat)),
            bar=SOURCE_BAR, art_key=f"source:{feat}",
            state=skin.card_state(self, node), scale=self._view_scale)
        skin.paint_source_card(p, node, r, look,
                               template_label(str(node.get("template", ""))))
        self._draw_ports(p, node, out_only=True)

    def _draw_rule_card(self, p: QPainter, node, r: QRectF) -> None:
        """效果卡（R182 效果库形态）：效果名 + 参数控件 + 目标通道 chips。"""
        key = str(node.get("effect", "") or "")
        title = effect_label(key) if key else dc.type_label(str(node.get("ch_type", "")))
        look = skin.CardLook(
            title=title, icon=effect_icon(key) if key else "wave-sine",
            bar=RULE_BAR, art_key=f"fx:{key or node.get('ch_type', '')}",
            state=skin.card_state(self, node), scale=self._view_scale)
        slider = getattr(self, "_slider", None) or {}
        nid = str(node.get("id", ""))
        active = str(slider.get("key", "")) if slider.get("nid") == nid else ""
        skin.paint_rule_card(p, node, r, look, active_key=active,
                             fixture_count=getattr(self, "_scope_fixture_count", 0))
        self._draw_ports(p, node, out_only=False)

    def _ctrl_rect(self, r: QRectF, key: str) -> QRectF:
        """兼容入口：控件世界矩形（与 draw_graph.RULE_CONTROLS 命中区同源）。"""
        return skin.ctrl_rect(r, key)

    def _draw_slider(self, p: QPainter, box: QRectF, frac: float, text: str) -> None:
        """兼容入口：旧「名 值」单串 → 左名右值滑杆。"""
        name, _sep, value = str(text).partition(" ")
        skin.paint_slider(p, box, frac, (name, value))

    def _draw_ports(self, p: QPainter, node, out_only: bool) -> None:
        """桩位：源卡只有出桩（右），规则卡只有入桩（左）；拖线探测染色。"""
        pr = self._px(5.0)
        ports = [("out", self._out_port(node))] if out_only else [("in", self._in_port(node))]
        probe = self._link.get("probe") if self._link else None
        nid = str(node.get("id", ""))
        for key, c in ports:
            fill = BG_BUTTON if key == "out" else BG_PANEL_SUB
            if probe and probe[:2] == (nid, key):
                fill = BRIGHT_SUCCESS if probe[2] else BRIGHT_ERROR
            p.setPen(skin.pen(TEXT_SECONDARY, 1.0, cosmetic=True))
            p.setBrush(skin.brush(fill))
            cx, cy = xy(c)
            p.drawEllipse(QPointF(cx, cy), pr, pr)

    # ── 拖线预览 / 框选 / 空态 / HUD ────────────────────────────────────
    def _draw_link_preview(self, p: QPainter) -> None:
        src = dc.node_by_id(self._graph, self._link["src"])
        if src is None:
            return
        p.setPen(skin.pen(FOCUS_BLUE, self._px(_PEN_HAIR + 0.4), dash=True))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # A1：cur 是 QPointF，seg_bezier 经 draw_canvas_geom.xy 兼容取值
        p.drawPath(self._seg_bezier(self._out_port(src), self._link["cur"]))

    def _draw_rubber(self, p: QPainter) -> None:
        """框选预览（世界坐标：半透明蓝底 + 焦点蓝描边）。"""
        r = QRectF(self._rubber["start"], self._rubber["cur"]).normalized()
        p.setPen(skin.pen(FOCUS_BLUE, 1.0, cosmetic=True))
        p.setBrush(_RUBBER_FILL)
        p.drawRect(r)

    def _draw_empty_state(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        if w < 120 or h < 80:
            return
        m = 24.0
        p.setPen(skin.pen(BORDER_NORMAL, 1.0, cosmetic=True, dash=True))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(m, m, w - 2 * m, h - 2 * m), 8.0, 8.0)
        p.setFont(cached_font(14))
        p.setPen(skin.color(TEXT_SECONDARY))
        p.drawText(QRectF(0, h * 0.42, w, 28), Qt.AlignmentFlag.AlignCenter, _EMPTY_TITLE)
        p.setFont(cached_font(12))
        p.setPen(skin.color(TEXT_DISABLED))
        p.drawText(QRectF(0, h * 0.50, w, 24), Qt.AlignmentFlag.AlignCenter, _EMPTY_HINT)

    def _draw_hud(self, p: QPainter) -> None:
        if self.width() < 90 or self.height() < 40:
            return
        p.setFont(cached_font(11))
        p.setPen(skin.color(TEXT_SECONDARY))
        p.drawText(QRectF(8, self.height() - 20, 64, 16),
                   Qt.AlignmentFlag.AlignLeft, f"{self._view_scale:.0%}")
        if self.width() >= 432:
            p.drawText(QRectF(80, self.height() - 20, self.width() - 88, 16),
                       Qt.AlignmentFlag.AlignLeft,
                       "滚轮=缩放 · 中键/右键拖=平移 · Del=删除 · Home=全览")


_FEATURE_LABELS = {
    "beat_strength": "节拍强度", "bpm_phase": "BPM 相位", "band_low": "低频能量",
    "band_high": "高频能量", "band_ratio": "频段能量比",
}

#: 兼容旧名：波形采样实现迁入 draw_card_skin
_wave_points = skin.wave_points
