# -*- coding: utf-8 -*-
"""Clip Studio 卡片美术底图层（R172 设计 §0-B7）—— 纯 QPainter 函数 + 预构建路径缓存。

设计 B7：卡片不止文本框/控件，还要**相应效果的 UI 美术底图**（如左到右=箭头底图）。

纪律（本文件是唯一实现点，画布按 CardSpec.art 调用）：

* 功能图解使用暖米色画布的墨线/橙填充，清楚表达方向、批次、时序。
* 全部 QPainterPath / 抗锯齿；**预构建 QPainterPath 缓存到模块级 dict `_ART`**
  （键 = art_key，绘制时只做 save/translate/scale + stroke/fill，禁每帧重建路径）。
* 几何统一构建在 100x100 归一化坐标系，绘制时按 rect 等比缩放居中；
  LOD 低档（缩放过小）由画布自行不调用本模块。

`state` 参数保留在签名里（画布可传节点状态字符串），当前美术与状态无关。
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QTransform

from src.ui import art_asset_library as _lib
from src.ui import qascade_theme as _th
from src.ui.canvas_materials import tone

__all__ = ["paint_card_art", "has_art", "ART_KEYS", "paint_empty_canvas_art",
           "EMPTY_CANVAS_ART_ID", "EMPTY_CANVAS_TEXTURE_ID"]

_S = 100.0                     # 归一化坐标空间边长
_STROKE_ALPHA = 165            # Clear functional diagram on the cream card.
_FILL_ALPHA = 115
_PEN_W = 3.0                   # 归一化空间线宽（随卡片等比缩放）
_B0_ART_OPACITY = 0.35         # B0 卡面图绘制透明度（低对比不喧宾；R183-B1）

# 每个条目 = (QPainterPath, is_fill)；is_fill=True → 填充，False → 描边。
_ART: dict = {}


def _line(pts) -> QPainterPath:
    """开放折线路径。"""
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    return path


def _poly(pts) -> QPainterPath:
    """闭合多边形路径（填充用）。"""
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    path.closeSubpath()
    return path


def _circle(cx: float, cy: float, r: float) -> QPainterPath:
    return QPainterPath(QPointF(cx, cy)) if r <= 0 else _ellipse(cx, cy, r)


def _ellipse(cx: float, cy: float, rx: float, ry: float = 0.0) -> QPainterPath:
    ry = ry or rx
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), rx, ry)
    return path


def _arc(cx: float, cy: float, r: float, start_deg: float, sweep_deg: float) -> QPainterPath:
    """圆弧路径（Qt 角度：0=三点钟方向，正值逆时针；y 向下坐标里视觉相反）。"""
    path = QPainterPath()
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path.arcMoveTo(rect, start_deg)
    path.arcTo(rect, start_deg, sweep_deg)
    return path


def _mirrored(path: QPainterPath) -> QPainterPath:
    """关于 x=50 竖直轴镜像（rtl/ccw/edge_in 等由镜像派生，勿手写第二份几何）。"""
    tr = QTransform()
    tr.translate(_S, 0.0)
    tr.scale(-1.0, 1.0)
    return tr.map(path)


def _head(tip, ang_deg: float, size: float = 14.0) -> QPainterPath:
    """箭头三角（tip=尖端点，ang=指向角，弧度）。"""
    tx, ty = tip
    back = (tx - size * math.cos(ang_deg), ty - size * math.sin(ang_deg))
    perp = (math.cos(ang_deg + math.pi / 2), math.sin(ang_deg + math.pi / 2))
    return _poly([
        (tx, ty),
        (back[0] + perp[0] * size * 0.45, back[1] + perp[1] * size * 0.45),
        (back[0] - perp[0] * size * 0.45, back[1] - perp[1] * size * 0.45),
    ])


_R = math.radians


def _big_arrow(ang_deg: float) -> list:
    """大箭头（ shaft + 实心头），ang：0=右 / 180=左 / -90=上 / 90=下。"""
    a = _R(ang_deg)
    ux, uy = math.cos(a), math.sin(a)
    tip = (50 + 42 * ux, 50 + 42 * uy)
    tail = (50 - 30 * ux, 50 - 30 * uy)
    return [(_line([tail, (50 + 22 * ux, 50 + 22 * uy)]), False),
            (_head(tip, a, 20.0), True)]


def _rotate_arrow(cw: bool) -> list:
    """圆弧 + 箭头（cw=True 视觉顺时针）。

    Qt 弧参数：角 0=三点钟、正角逆时针（屏幕 y 向下 → P(a)=(cx+r·cos a, cy−r·sin a)）。
    顺时针 = 负 sweep；末端切线：cw → 90°−a，ccw → −90°−a（屏幕角度）。
    """
    start, sweep = (55.0, -250.0) if cw else (-235.0, 250.0)
    arc = _arc(50, 50, 30, start, sweep)
    end_deg = start + sweep
    ex = 50 + 30 * math.cos(_R(end_deg))
    ey = 50 - 30 * math.sin(_R(end_deg))
    tang = _R(90.0 - end_deg) if cw else _R(-90.0 - end_deg)
    return [(arc, False), (_head((ex, ey), tang, 16.0), True)]


def _sine_band() -> QPainterPath:
    pts = []
    for i in range(41):
        x = 8.0 + i * 84.0 / 40.0
        pts.append((x, 50 + 20 * math.sin(_R(i * 27.0))))
    return _line(pts)


def _stair(rev: bool) -> QPainterPath:
    pts = [(10, 25), (34, 25), (34, 50), (58, 50), (58, 75), (86, 75)]
    if rev:
        pts = [(x, _S - y) for x, y in pts]
    return _line(pts)


def _zigzag() -> QPainterPath:
    return _line([(8, 26), (38, 74), (56, 26), (92, 74)])


def _scatter_dots() -> list:
    """伪随机散点（坐标硬编码，确定性；不逐帧重算）。"""
    pts = [(18, 22), (46, 14), (78, 26), (30, 46), (62, 42), (86, 58),
           (16, 68), (44, 74), (72, 84), (58, 64)]
    return [(_circle(x, y, 4.5), True) for x, y in pts]


def _double_arrow(center_out: bool) -> list:
    """双向箭头：center_out=True 由中心向外，False 由两端向中心。"""
    s1 = _line([(38, 50), (16, 50)]) if center_out else _line([(16, 50), (38, 50)])
    s2 = _line([(62, 50), (84, 50)]) if center_out else _line([(84, 50), (62, 50)])
    h1 = _head((12, 50), math.pi, 15.0) if center_out else _head((42, 50), 0.0, 15.0)
    h2 = _head((88, 50), 0.0, 15.0) if center_out else _head((58, 50), math.pi, 15.0)
    return [(s1, False), (s2, False), (h1, True), (h2, True)]


def _alternate() -> list:
    return [(_line([(18, 32), (56, 32)]), False), (_head((66, 32), 0.0, 14.0), True),
            (_line([(82, 68), (44, 68)]), False), (_head((34, 68), math.pi, 14.0), True)]


def _blocks() -> list:
    """棋盘格块（alt_block 交错块面）。"""
    out = []
    for col in range(4):
        for row in range(2):
            if (col + row) % 2 == 0:
                x, y = 12 + col * 20.0, 26 + row * 24.0
                out.append((_poly([(x, y), (x + 16, y), (x + 16, y + 20), (x, y + 20)]), True))
    return out


def _curve_s() -> QPainterPath:
    path = QPainterPath(QPointF(10, 78))
    path.cubicTo(QPointF(42, 78), QPointF(58, 22), QPointF(90, 22))
    return path


def _branch() -> list:
    return [(_line([(50, 88), (50, 54)]), False),
            (_line([(50, 54), (24, 24)]), False), (_head((22, 21), _R(-132), 13.0), True),
            (_line([(50, 54), (76, 24)]), False), (_head((78, 21), _R(-48), 13.0), True)]


def _bulb() -> list:
    return [(_circle(50, 42, 22), False), (_line([(43, 68), (57, 68)]), False),
            (_line([(45, 76), (55, 76)]), False), (_line([(50, 42), (50, 56)]), False)]


def _gear() -> list:
    out = [(_circle(50, 50, 24), False), (_circle(50, 50, 9), False)]
    for i in range(8):
        a = _R(i * 45.0)
        out.append((_line([(50 + 24 * math.cos(a), 50 + 24 * math.sin(a)),
                           (50 + 34 * math.cos(a), 50 + 34 * math.sin(a))]), False))
    return out


def _percent() -> list:
    return [(_circle(30, 30, 10), False), (_circle(70, 70, 10), False),
            (_line([(76, 24), (24, 76)]), False)]


def _valve() -> list:
    return [(_poly([(16, 32), (16, 68), (50, 50)]), False),
            (_poly([(84, 32), (84, 68), (50, 50)]), False)]


def _pump() -> list:
    return [(_circle(44, 58, 22), False), (_line([(58, 44), (80, 22)]), False),
            (_poly([(74, 12), (88, 12), (88, 26), (74, 26)]), False)]


def _grid_art() -> list:
    out = []
    for col in range(3):
        for row in range(3):
            x, y = 20 + col * 24.0, 20 + row * 24.0
            out.append((_poly([(x, y), (x + 14, y), (x + 14, y + 14), (x, y + 14)]), False))
    return out


def _clock() -> list:
    return [(_circle(50, 50, 32), False), (_line([(50, 50), (50, 28)]), False),
            (_line([(50, 50), (66, 58)]), False)]


def _uniform() -> list:
    return [(_line([(14, 32), (86, 32)]), False), (_line([(14, 50), (86, 50)]), False),
            (_line([(14, 68), (86, 68)]), False)]


def _build_art() -> dict:
    """全量构建 → {art_key: [(path, is_fill), ...]}（模块导入时一次性执行）。"""
    out: dict = {}
    out["arrow_ltr"] = _big_arrow(0.0)
    out["arrow_rtl"] = _big_arrow(180.0)
    out["arrow_ftb"] = _big_arrow(-90.0)
    out["arrow_btf"] = _big_arrow(90.0)
    cw = _rotate_arrow(True)
    out["rotate_cw"] = cw
    out["rotate_ccw"] = _rotate_arrow(False)
    out["center_out"] = _double_arrow(True)
    out["edge_in"] = _double_arrow(False)
    out["alternate"] = _alternate()
    out["blocks"] = _blocks()
    out["wave"] = [(_sine_band(), False)]
    out["stair"] = [(_stair(False), False)]
    out["stair_rev"] = [(_stair(True), False)]
    out["zigzag"] = [(_zigzag(), False)]
    out["random"] = _scatter_dots()
    out["uniform"] = _uniform()
    out["curve"] = [(_curve_s(), False)]
    out["branch"] = _branch()
    out["bulb"] = _bulb()
    out["gear"] = _gear()
    out["percent"] = _percent()
    out["valve"] = _valve()
    out["pump"] = _pump()
    out["grid"] = _grid_art()
    out["clock"] = _clock()
    return out


_ART = _build_art()
ART_KEYS: frozenset = frozenset(_ART)


def has_art(art_key) -> bool:
    """art_key 是否有底图（未知 key → False，画布可安全跳过）。"""
    return str(art_key or "") in _ART


def _paint_b0_art(painter, rect, key: str) -> bool:
    """B0 卡面图命中路径：manifest 登记 → LRU 位图 opacity 0.35 绘制（未命中 False）。"""
    if _lib.art_meta(key) is None:
        return False
    pm = _lib.load_art_pixmap(key, max(4, int(rect.width())), max(4, int(rect.height())))
    if pm.isNull():
        return False
    painter.save()
    painter.setOpacity(_B0_ART_OPACITY)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawPixmap(rect, pm, QRectF(pm.rect()))
    painter.restore()
    return True


def _paint_programmatic(painter, rect, entries) -> None:
    """回退路径：预构建 `_ART` 路径低对比描边/填充（r172 美术守卫：不重建路径）。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scale = min(rect.width(), rect.height()) / _S
    center = rect.center()
    painter.translate(center.x() - _S * scale / 2.0, center.y() - _S * scale / 2.0)
    painter.scale(scale, scale)
    pen = QPen(tone(_th.CANVAS_MUTED, _STROKE_ALPHA), _PEN_W)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    brush = QBrush(tone(_th.BRAND_ORANGE_INK, _FILL_ALPHA))
    for path, is_fill in entries:
        if is_fill:
            painter.fillPath(path, brush)
        else:
            painter.strokePath(path, pen)
    painter.restore()


def paint_card_art(painter, rect, art_key, state=None) -> bool:
    """在 rect 内绘制卡片美术底图。

    绘制顺序（R183-B1：B0 资产落地，程序化底图为回退）：

    1. 已有 `_ART` 预构建路径优先：完整方向箭头、分组、时钟等功能图解，
       避免旧缩略标记放大成为模糊横线/字形。缓存路径不随 paint 重建。
    2. 无路径时保 B0 manifest/LRU 资产入口与透明度；旧资产测试清空路径表
       仍可绘制。`spec.icon`、art_key 与卡片字段全部保持。

    * `rect`：QRectF（画布世界坐标或屏幕坐标均可，调用方负责上下文）。
    * `art_key`：CardSpec.art（见 studio_cards 的 _DIR_ART / 各卡 art 字段）。
    * `state`：预留（节点状态；当前美术与状态无关）。
    * 返回是否实际绘制（未知 key / rect 过小 → False）。
    """
    key = str(art_key or "")
    if rect is None or rect.width() <= 4 or rect.height() <= 4:
        return False
    entries = _ART.get(key)
    if not entries:
        return _paint_b0_art(painter, rect, key)
    _paint_programmatic(painter, rect, entries)
    return True


# ── 空画布美术（R183-B2；从 studio_canvas_paint 移入避免超 500 行门禁）──────

#: B0 空态插画（文案上方，muted 灰）/ 纹理试点 id / 低透明（0.04-0.06 区间）。
EMPTY_CANVAS_ART_ID = "empty_float"
EMPTY_CANVAS_ART_PX = 96
EMPTY_CANVAS_ART_MIN_H = 240          # 画布过矮不放插画（防越顶）
EMPTY_CANVAS_TEXTURE_ID = "pat_floating_cogs"
EMPTY_CANVAS_TEXTURE_PX = 256
EMPTY_CANVAS_TEXTURE_ALPHA = 0.05


def paint_empty_canvas_art(painter, w: float, h: float, texture_on: bool = True,
                           text_color: str = "") -> None:
    """空画布 B0 底纹（tiled，低透明可关）+ 文案上方插画（muted 灰）。

    画在屏幕坐标系（调用方 = `_draw_empty_state`，paintEvent restore 之后）；
    既有文案/虚框仍由画布绘制，本函数只补美术两件。
    """
    from src.ui import qascade_theme as _th
    tint = text_color or _th.TEXT_DISABLED
    if texture_on:
        tex = _lib.load_art_pixmap(EMPTY_CANVAS_TEXTURE_ID,
                                   EMPTY_CANVAS_TEXTURE_PX, EMPTY_CANVAS_TEXTURE_PX, tint)
        if not tex.isNull():
            painter.save()
            painter.setOpacity(EMPTY_CANVAS_TEXTURE_ALPHA)
            painter.drawTiledPixmap(QRectF(0, 0, float(w), float(h)), tex)
            painter.restore()
    if h >= EMPTY_CANVAS_ART_MIN_H:
        art = _lib.load_art_pixmap(EMPTY_CANVAS_ART_ID, EMPTY_CANVAS_ART_PX,
                                   EMPTY_CANVAS_ART_PX, tint)
        if not art.isNull():
            painter.drawPixmap(QPointF((w - EMPTY_CANVAS_ART_PX) / 2.0,
                                       h * 0.45 - EMPTY_CANVAS_ART_PX - 10.0), art)
