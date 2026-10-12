# -*- coding: utf-8 -*-
"""Draw 功能节点（节点范式）—— 卡体五态 / 标题带 / 波形 / 内嵌控件。

从 draw_canvas_paint 拆出（该文件顶 500 行红线）。版式照 02 设计规范 §8::

    ┌──────────────────────────────┐
    │▎[tabler 16] 标题 13px/600     │  标题带 24（字高 13 + 8 → 4px 栅格取 24）
    ├──────────────────────────────┤
    │  功能预览 / 信号说明          │  内容区：与节点语义相符的波形和参数
    │  控件 / 摘要（11px 微标签）    │  控件行高 16、行距 4、内边距 8
    └──────────────────────────────┘
    圆角 8 · 1px 发丝线（cosmetic）· hover 描边提亮一档 · 选中 FOCUS_BLUE 2px

参照：专业节点编辑器（标题带 + 内嵌数值滑杆「左名右值、填充入槽」）；
OP viewer（卡面即画面）。五态：default / hover / pressed（拖动中）/
selected（焦点）/ disabled（node["disabled"]，整卡 50% 不透明）。
性能（03 §四）：字体走 qascade_theme.cached_font；QColor/QPen/QBrush/卡片圆角
路径/波形折线/省略文案全部模块级缓存 —— paintEvent 零逐帧构造。
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFontMetricsF, QPainter, QPainterPath, QPen,
                         QPolygonF)

from ..qascade_theme import (
    BG_BOX,
    BG_DEEP,
    BG_INPUT,
    BG_PANEL,
    BG_PANEL_SUB,
    BORDER_NORMAL,
    BRIGHT_SUCCESS,
    BRIGHT_WARN,
    BRAND_ORANGE_INK as FOCUS_BLUE,
    BRAND_ORANGE_SELECTED as SELECT_BLUE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
    cached_font,
)
from . import draw_graph as dc
from .. import qascade_theme as theme
from ..canvas_materials import paint_card_face
from .draw_cards import SOURCE_CATALOG, ch_label, wave_label
from .draw_phase_viz import phase_fan_dots

__all__ = ["CardLook", "card_state", "paint_source_card", "paint_rule_card",
           "paint_slider", "paint_target_chips", "paint_overflow_tag", "draw_icon",
           "color", "pen", "brush", "wave_points", "ctrl_rect", "LOD_DETAIL",
           "CARD_RADIUS", "HEADER_H"]

CARD_RADIUS = 8.0          # 02 §3 卡片/浮层
CTRL_RADIUS = 2.0          # 02 §3 输入框/芯片
HEADER_H = 24.0
PAD = 8.0                  # 02 §1 SM
ICON_PX = 16               # 02 §7 工具栏/卡头图标
TITLE_PX, MICRO_PX = 13, 11
LOD_DETAIL = 0.62          # 低于此缩放：隐控件，画作满幅 + 屏幕等大标题
_ICON_RENDER_PX = 32       # 图标 2× 超采样（缩放到 200% 仍锐利）

DEFAULT, HOVER, PRESSED, SELECTED, DISABLED = (
    "default", "hover", "pressed", "selected", "disabled")
#: 五态 → (底色 token, 描边 token, 描边宽 px)
_STATE_STYLE = {
    DEFAULT: (BG_PANEL, theme.CANVAS_BORDER, 1.0),
    HOVER: (BG_PANEL_SUB, TEXT_TERTIARY, 1.0),
    PRESSED: (BG_PANEL_SUB, TEXT_SECONDARY, 1.0),
    SELECTED: (BG_PANEL, FOCUS_BLUE, 2.0),
    DISABLED: (BG_PANEL, BORDER_NORMAL, 1.0),
}
_SLIDER_KEYS = ("depth", "freq", "attack", "release", "threshold", "smoothing",
                "spread_deg")
_SOURCE_HINTS = {spec[1]: spec[2] for spec in SOURCE_CATALOG.values()}
_ALIGN_L = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
_ALIGN_R = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
_ALIGN_C = Qt.AlignmentFlag.AlignCenter

_COLORS: dict = {}
_PENS: dict = {}
_BRUSHES: dict = {}
_PATHS: dict = {}
_ELIDED: dict = {}
_ICONS: dict = {}
_WAVE_PTS: dict = {}


# ── 缓存对象工厂（热路径只查表）────────────────────────────────────────
def color(token: str, alpha: int = 255) -> QColor:
    key = (str(token), int(alpha))
    c = _COLORS.get(key)
    if c is None:
        c = QColor(str(token))
        c.setAlpha(int(alpha))
        _COLORS[key] = c
    return c


def pen(token: str, width: float = 1.0, alpha: int = 255, *,
        cosmetic: bool = False, dash: bool = False) -> QPen:
    key = (str(token), float(width), int(alpha), bool(cosmetic), bool(dash))
    cached = _PENS.get(key)
    if cached is None:
        cached = QPen(color(token, alpha), float(width))
        cached.setCosmetic(bool(cosmetic))
        if dash:
            cached.setStyle(Qt.PenStyle.DashLine)
        _PENS[key] = cached
    return cached


def brush(token: str, alpha: int = 255) -> QBrush:
    key = (str(token), int(alpha))
    b = _BRUSHES.get(key)
    if b is None:
        b = QBrush(color(token, alpha))
        _BRUSHES[key] = b
    return b


def _card_path(w: float, h: float) -> QPainterPath:
    """原点对齐的卡片圆角路径（按尺寸缓存；绘制时 translate 到卡位）。"""
    key = (round(float(w), 2), round(float(h), 2))
    path = _PATHS.get(key)
    if path is None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.0, 0.0, float(w), float(h)), CARD_RADIUS, CARD_RADIUS)
        _PATHS[key] = path
    return path


def elide(text: str, px: int, bold: bool, width: float) -> str:
    key = (str(text), int(px), bool(bold), int(width))
    s = _ELIDED.get(key)
    if s is None:
        fm = QFontMetricsF(cached_font(int(px), bool(bold)))
        s = fm.elidedText(str(text), Qt.TextElideMode.ElideRight, max(0.0, float(width)))
        if len(_ELIDED) > 4096:
            _ELIDED.clear()
        _ELIDED[key] = s
    return s


def _load_icon(name: str, tint: str):
    try:
        from ..main_icon_library import load_tabler_icon
        return load_tabler_icon(str(name), _ICON_RENDER_PX, str(tint)).pixmap(
            _ICON_RENDER_PX, _ICON_RENDER_PX)
    except Exception:
        return None


def draw_icon(p: QPainter, name: str, rect: QRectF, tint: str) -> None:
    """tabler 图标画进 rect（2× 源位图 + 平滑缩放；失败静默）。"""
    key = (str(name), str(tint))
    if key not in _ICONS:
        _ICONS[key] = _load_icon(name, tint)
    pm = _ICONS[key]
    if pm is None or pm.isNull():
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawPixmap(rect, pm, QRectF(pm.rect()))
    p.restore()


# ── 卡片外观描述 + 状态 ─────────────────────────────────────────────────
@dataclass(frozen=True)
class CardLook:
    """一张卡的绘制输入（标题/图标/色带/画作 key/五态/当前缩放）。"""

    title: str
    icon: str
    bar: str
    art_key: str
    state: str = DEFAULT
    scale: float = 1.0

    @property
    def low(self) -> bool:
        return self.scale < LOD_DETAIL


def card_state(canvas, node) -> str:
    """节点五态：disabled > pressed（拖动中）> selected > hover > default。"""
    nid = str(node.get("id", ""))
    if node.get("disabled"):
        return DISABLED
    drag = getattr(canvas, "_drag", None)
    if drag and drag.get("moved") and drag.get("nid") == nid:
        return PRESSED
    if nid in getattr(canvas, "_sel_ids", set()):
        return SELECTED
    if nid and nid == getattr(canvas, "_hover_nid", ""):
        return HOVER
    return DEFAULT


# ── 卡体：底 + 标题带 + 描边；预览由节点类型提供 ─────────────────────────
def paint_card_shell(p: QPainter, r: QRectF, look: CardLook) -> QRectF:
    """画卡体五态 + 标题带 + 描边；返回功能内容区矩形。"""
    bg, stroke, width = _STATE_STYLE.get(look.state, _STATE_STYLE[DEFAULT])
    path = _card_path(r.width(), r.height())
    content = QRectF(r.left(), r.top() + HEADER_H, r.width(),
                     max(0.0, r.height() - HEADER_H))
    p.save()
    if look.state == DISABLED:
        p.setOpacity(0.5)
    paint_card_face(p, r, CARD_RADIUS)
    p.translate(r.left(), r.top())
    p.setPen(Qt.PenStyle.NoPen)
    if look.state in (HOVER, PRESSED):
        p.setBrush(brush(bg, 45))
        p.drawPath(path)
    p.setClipPath(path)
    p.translate(-r.left(), -r.top())
    _paint_header(p, r, look)
    p.setClipping(False)
    p.translate(r.left(), r.top())
    p.setPen(pen(stroke, width, cosmetic=True))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.restore()
    return content


def _paint_header(p: QPainter, r: QRectF, look: CardLook) -> None:
    """标题带：底 + 左 3px 色带 + 下沿发丝线 + 图标 + 标题（LOD 下只留色带）。"""
    p.fillRect(QRectF(r.left(), r.top(), r.width(), HEADER_H), color(theme.CANVAS_HEADER))
    p.fillRect(QRectF(r.left(), r.top(), 3.0, HEADER_H), color(look.bar))
    p.fillRect(QRectF(r.left(), r.top() + HEADER_H - 1.0, r.width(), 1.0),
               color(BORDER_NORMAL))
    if look.low:
        return
    icon = QRectF(r.left() + PAD, r.top() + (HEADER_H - ICON_PX) / 2.0,
                  float(ICON_PX), float(ICON_PX))
    draw_icon(p, look.icon, icon, look.bar)
    text = QRectF(icon.right() + 4.0, r.top(),
                  max(0.0, r.right() - icon.right() - 4.0 - PAD), HEADER_H)
    p.setFont(cached_font(TITLE_PX, True))
    p.setPen(color(TEXT_PRIMARY))
    p.drawText(text, _ALIGN_L, elide(look.title, TITLE_PX, True, text.width()))


def paint_lod_title(p: QPainter, r: QRectF, title: str, scale: float) -> None:
    """缩小视图：卡名以**屏幕 12px 等大**居中（节点编辑器缩小态同语义）。"""
    px = int(round(min(48.0, max(13.0, 12.0 / max(float(scale), 0.05)))))
    area = r.adjusted(PAD, HEADER_H, -PAD, 0.0)
    p.setFont(cached_font(px, True))
    p.setPen(color(TEXT_PRIMARY))
    p.drawText(area, _ALIGN_C, elide(title, px, True, area.width()))


# ── 源卡 / 效果卡入口 ───────────────────────────────────────────────────
def paint_source_card(p: QPainter, node, r: QRectF, look: CardLook, sub: str) -> None:
    """源卡：卡体 + 模板/信号说明（11px）；缩小视图只画大标题。"""
    content = paint_card_shell(p, r, look)
    if look.low:
        paint_lod_title(p, r, look.title, look.scale)
        return
    area = content.adjusted(PAD, 4.0, -PAD, -4.0)
    p.setFont(cached_font(MICRO_PX))
    p.setPen(color(TEXT_SECONDARY))
    hint = _SOURCE_HINTS.get(str(node.get("feature", "")), sub)
    if sub and sub != "源信号":
        hint = f"{sub} · {hint}"
    p.drawText(area, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft |
               Qt.TextFlag.TextWordWrap, hint)


def paint_rule_card(p: QPainter, node, r: QRectF, look: CardLook, *,
                    active_key: str = "", fixture_count: int = 0) -> None:
    """效果卡：卡体 + 波形下拉 / 预览 / 七滑杆 / 目标 chips。"""
    paint_card_shell(p, r, look)
    if look.low:
        paint_lod_title(p, r, look.title, look.scale)
        return
    params = node.get("params") if isinstance(node.get("params"), dict) else {}
    paint_dropdown(p, ctrl_rect(r, "wave"), wave_label(str(params.get("wave", ""))))
    paint_preview(p, node, ctrl_rect(r, "preview"))
    vals = slider_values(params)
    for key in _SLIDER_KEYS:
        frac, name, text = vals[key]
        paint_slider(p, ctrl_rect(r, key), frac, (name, text), active=key == active_key)
    spread = vals["spread_deg"][0] * 360.0
    if spread > 0.0:   # 成熟化轮⑥⑦：每灯相位点（MA3 黄点）
        phase_fan_dots(p, ctrl_rect(r, "spread_deg"), spread, fixture_count)
    paint_target_chips(p, node)


def ctrl_rect(r: QRectF, key: str) -> QRectF:
    dx, dy, w, h = dc.RULE_CONTROLS[key]
    return QRectF(r.left() + dx, r.top() + dy, w, h)


def slider_values(params: dict) -> dict:
    """滑杆键 → (填充比例, 名, 值文案)；比例映射与修前 _draw_effect_controls 逐位一致。"""
    depth = float(params.get("depth", 1.0))
    freq = float(params.get("freq", 2.0))
    atk = float(params.get("attack", 0.05))
    rel = float(params.get("release", 0.2))
    thr = min(1.0, max(0.0, float(params.get("threshold", 0.0))))
    smo = min(1.0, max(0.0, float(params.get("smoothing", 0.0))))
    spd = min(360.0, max(0.0, float(params.get("spread_deg", 0.0))))
    return {
        "depth": (depth, "深度", f"{depth:.0%}"),
        "freq": (max(0.0, (freq - 0.25) / 7.75), "频率", f"{freq:.1f}"),
        "attack": (min(1.0, atk / 2.0), "起应", f"{atk:.2f}s"),
        "release": (min(1.0, rel / 2.0), "落应", f"{rel:.2f}s"),
        "threshold": (thr, "阈值", f"{thr:.0%}"),
        "smoothing": (smo, "平滑", f"{smo:.0%}"),
        "spread_deg": (spd / 360.0, "展开", f"{spd:.0f}°"),
    }


# ── 内嵌控件 ────────────────────────────────────────────────────────────
def paint_dropdown(p: QPainter, box: QRectF, label: str) -> None:
    """波形下拉：输入框底（圆角 2 + 发丝线）+ 11px 文案 + 右侧三角。"""
    p.setPen(pen(BORDER_NORMAL, 1.0, cosmetic=True))
    p.setBrush(brush(BG_INPUT, 230))
    p.drawRoundedRect(box, CTRL_RADIUS, CTRL_RADIUS)
    p.setFont(cached_font(MICRO_PX))
    p.setPen(color(TEXT_PRIMARY))
    p.drawText(box.adjusted(PAD, 0.0, -20.0, 0.0), _ALIGN_L, label)
    cx, cy = box.right() - 10.0, box.center().y()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(brush(TEXT_SECONDARY))
    p.drawPolygon(QPointF(cx - 4.0, cy - 2.0), QPointF(cx + 4.0, cy - 2.0),
                  QPointF(cx, cy + 2.0))


def paint_slider(p: QPainter, box: QRectF, frac: float, texts: tuple, *,
                 active: bool = False) -> None:
    """极简数值滑杆：整槽为命中区，填充入槽，左名右值（11px，不再压字）。"""
    val = max(0.0, min(1.0, float(frac)))
    p.setPen(pen(FOCUS_BLUE, 1.0, cosmetic=True) if active else Qt.PenStyle.NoPen)
    p.setBrush(brush(BG_INPUT, 230))
    p.drawRoundedRect(box, CTRL_RADIUS, CTRL_RADIUS)
    if val > 0.0:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(brush(SELECT_BLUE, 200 if active else 150))
        p.drawRoundedRect(QRectF(box.left(), box.top(), max(2.0, box.width() * val),
                                 box.height()), CTRL_RADIUS, CTRL_RADIUS)
    name, value = texts
    inner = box.adjusted(4.0, 0.0, -4.0, 0.0)
    p.setFont(cached_font(MICRO_PX))
    p.setPen(color(TEXT_SECONDARY))
    p.drawText(inner, _ALIGN_L, str(name))
    p.setPen(color(TEXT_PRIMARY))
    p.drawText(inner, _ALIGN_R, str(value))


def paint_preview(p: QPainter, node, box: QRectF) -> None:
    """波形预览：折线按 (wave, 周期, 尺寸) 缓存为原点多边形，绘制时平移。"""
    p.setPen(pen(BORDER_NORMAL, 1.0, cosmetic=True))
    p.setBrush(brush(BG_DEEP, 235))
    p.drawRoundedRect(box, CTRL_RADIUS, CTRL_RADIUS)
    params = node.get("params") if isinstance(node.get("params"), dict) else {}
    wave = str(params.get("wave", node.get("wave", "")) or "")
    freq = max(1.0, min(8.0, float(params.get("freq", node.get("wave_freq", 2.0)) or 2.0)))
    key = (wave, int(freq), round(box.width(), 1), round(box.height(), 1))
    poly = _WAVE_PTS.get(key)
    if poly is None:
        poly = QPolygonF(wave_points(wave, int(freq),
                                     QRectF(0.0, 0.0, box.width(), box.height()), 40))
        if len(_WAVE_PTS) > 512:
            _WAVE_PTS.clear()
        _WAVE_PTS[key] = poly
    p.save()
    p.translate(box.left(), box.top())
    p.setPen(pen(BRIGHT_SUCCESS, 1.3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolyline(poly)
    p.restore()


def paint_target_chips(p: QPainter, node) -> None:
    """目标通道 chips（绿描边 + 淡绿底 + 11px 字）；截断溢出画 +N 徽标。"""
    types = [str(c) for c in (node.get("ch_types") or [])]
    chips = types[:dc.MAX_TARGET_CHIPS]
    p.setFont(cached_font(MICRO_PX))
    for i, ct in enumerate(chips):
        rect = dc.target_chip_rect(node, i)
        if rect is None:
            continue
        box = QRectF(QPointF(rect[0], rect[1]), QPointF(rect[2], rect[3]))
        p.setPen(pen(BRIGHT_SUCCESS, 1.0, cosmetic=True))
        p.setBrush(brush(BRIGHT_SUCCESS, 46))
        p.drawRoundedRect(box, CTRL_RADIUS, CTRL_RADIUS)
        p.setPen(color(TEXT_PRIMARY))
        p.drawText(box, _ALIGN_C, elide(ch_label(ct), MICRO_PX, False, box.width() - 4.0))
    overflow = len(types) - len(chips)
    tag = dc.overflow_tag_rect(node) if overflow > 0 else None
    if tag is not None:
        paint_overflow_tag(p, QRectF(QPointF(tag[0], tag[1]), QPointF(tag[2], tag[3])),
                           overflow)


def paint_overflow_tag(p: QPainter, box: QRectF, count: int) -> None:
    """+N 徽标（02 §3 徽标=全圆；与 draw_graph.overflow_tag_rect 命中区同源）。"""
    radius = box.height() / 2.0
    p.setPen(pen(BRIGHT_WARN, 1.0, cosmetic=True))
    p.setBrush(brush(BG_DEEP, 225))
    p.drawRoundedRect(box, radius, radius)
    p.setFont(cached_font(MICRO_PX, True))
    p.setPen(color(BRIGHT_WARN))
    p.drawText(box, _ALIGN_C, f"+{int(count)}")


def wave_points(wave: str, cycles: int, box: QRectF, n: int) -> list:
    """波形折线采样（effect_engine.WAVE_FUNCS 同源函数；缺键 → 斜线）。"""
    try:
        from ...ui.effect_engine import WAVE_FUNCS
        fn = WAVE_FUNCS.get(str(wave or ""))
    except Exception:
        fn = None
    pts = []
    mid = box.center().y()
    amp = max(2.0, box.height() * 0.32)
    for i in range(n + 1):
        s = i / float(n)
        v = s if fn is None else float(fn(s * cycles))
        pts.append(QPointF(box.left() + 3.0 + s * (box.width() - 6.0),
                           mid - (v - 0.5) * 2.0 * amp))
    return pts
