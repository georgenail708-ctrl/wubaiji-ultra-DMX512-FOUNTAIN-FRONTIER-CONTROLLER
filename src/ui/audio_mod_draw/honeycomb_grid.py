# -*- coding: utf-8 -*-
"""通用蜂巢瓦片网格（R182）—— 卡/效果选择区的共享控件。

用户规格（2026-09-29）：效果选择区要"像一片蜂巢"，不再纵向长列表。
音频调制 Draw 的 `draw_effect_grid.EffectGrid` 与 Clip Studio 左栏共用本
控件的布局与交互纪律（单一布局真源 / 绘制命中同源 / 搜索过滤）。

用法：`set_items([(key, name, sub, icon, color)])`，`activated(key)` 点击、
`drag_started(key)` 拖动。`EffectGrid` 是它的薄包装（补组色带语义）。

R183-B1：行可选扩为 6 元组（尾元素 = B0 art_id，见 `art_asset_library`）；缺省时按
`<key>` → `eff_<key>` 在 manifest 里解析（Clip Studio 卡经 `studio_palette.catalog_rows`
显式传 `spec.art`；draw 效果 key 自动命中 `effects/eff_*`，两族 key 已核零碰撞）。
瓦片命中 B0 资产时把左上 16px 图标槽换成资产缩略（组色染色，布局零位移），
未命中照旧 tabler 图标 —— 双用户（`draw_effect_grid.EffectGrid` 与 Clip Studio
左栏）行为只增不改。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

try:
    from .. import art_asset_library as _art_lib
except Exception:                                   # pragma: no cover
    _art_lib = None                                 # 资产库不可用 → 全部回退 tabler 图标

from ..qascade_theme import (ACTIVE_ORANGE, BG_PANEL, BG_PANEL_SUB, BORDER_NORMAL,
                             BRIGHT_ERROR, BRIGHT_INFO, BRIGHT_SUCCESS, BRIGHT_WARN,
                             FOCUS_BLUE, SELECT_BLUE, TEXT_DISABLED, TEXT_PRIMARY,
                             TEXT_SECONDARY)

__all__ = ["HoneycombGrid", "grid_columns", "TILE_W", "TILE_H", "TILE_GAP", "PAD",
           "EMPTY_ART_ID", "EMPTY_ART_ID_ZERO", "EMPTY_ART_PX"]

TILE_W = 104.0
TILE_H = 54.0
TILE_GAP = 6.0
PAD = 6.0

#: B0 空态插画（Open Doodles，见 art_assets/manifest.json）：
#: 搜索无结果 → `EMPTY_ART_ID`；零条目（空目录）→ `EMPTY_ART_ID_ZERO`。
EMPTY_ART_ID = "empty_chilling"
EMPTY_ART_ID_ZERO = "empty_meditating"
EMPTY_ART_PX = 64


def grid_columns(avail_w: float) -> int:
    """可用宽 → 列数（至少 1；按瓦片宽 + 间距排）。"""
    if avail_w <= TILE_W + PAD * 2:
        return 1
    return max(1, int((avail_w - PAD * 2 + TILE_GAP) // (TILE_W + TILE_GAP)))


class HoneycombGrid(QWidget):
    """蜂巢瓦片网格（自绘；单一布局真源；绘制/命中同源）。

    items 行 = (key, name, sub, icon, color[, art_id])：color = 顶部色带（"" 用焦点蓝）；
    art_id = B0 资产 id（可省，省略时按 `<key>`/`eff_<key>` 自动解析）。
    """

    activated = pyqtSignal(str)            # 点击瓦片
    drag_started = pyqtSignal(str)         # 按住拖动瓦片

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: List[Tuple[str, str, str, str, str, str]] = []
        self._visible_keys: Optional[set] = None
        self._lay: List[dict] = []
        self._hover_key = ""
        self._press_pos = None
        self._face_prefix = "fx:"   # D1：瓦片画作 key 前缀（与画布卡 art_key 同源）
        self._press_key = ""
        self.setMouseTracking(True)
        self.setMinimumHeight(int(TILE_H + PAD * 2))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── 数据 ────────────────────────────────────────────────────────────
    def set_items(self, items) -> None:
        """items = [(key, name, sub, icon, color)]（可 6 元组尾加 art_id）；重排 + 重绘。

        B0 art_id 解析只在 set 时做一次（manifest 内存表查询，不逐帧查盘）。
        """
        rows: List[Tuple[str, str, str, str, str, str]] = []
        for row in (items or []):
            k, n, s, i, c = (tuple(row) + ("", "", "", "", ""))[:5]
            explicit = str(row[5]) if len(row) > 5 else ""
            rows.append((str(k), str(n), str(s), str(i), str(c),
                         _resolve_art_id(str(k), explicit)))
        self._items = rows
        self._relayout()
        self.update()

    def item_keys(self) -> List[str]:
        return [it[0] for it in self._items]

    def visible_keys(self) -> List[str]:
        if self._visible_keys is None:
            return self.item_keys()
        return [k for k in self.item_keys() if k in self._visible_keys]

    def tile_rect(self, key: str) -> Optional[QRectF]:
        for lay in self._lay:
            if lay["key"] == str(key):
                return QRectF(lay["rect"])
        return None

    def make_drag_pixmap(self, key: str, opacity: float = 0.65):
        """瓦片 → 半透明拖拽影像（R183 B3；失败/未命中 → None 走裸光标）。

        用 QWidget.grab 抓瓦片区再降透明度（所见即所得，无需第二套瓦片绘制）；
        调用方（studio_palette/draw_palette）setPixmap + 热点居中。
        """
        r = self.tile_rect(str(key))
        if r is None:
            return None
        try:
            from PyQt6.QtGui import QPainter, QPixmap
            pm = self.grab(r.toRect())
            if pm.isNull():
                return None
            out = QPixmap(pm.size())
            out.setDevicePixelRatio(pm.devicePixelRatio())   # 清查②②P2：DPR>1
            out.fill(Qt.GlobalColor.transparent)             # 缩放屏影像不缩 1/4
            p = QPainter(out)
            p.setOpacity(max(0.2, min(1.0, float(opacity))))
            p.drawPixmap(0, 0, pm)
            p.end()
            return out
        except Exception:
            return None

    def tile_at(self, pos) -> str:
        for lay in self._lay:
            if lay["rect"].contains(float(pos.x()), float(pos.y())):
                return str(lay["key"])
        return ""

    def layout_columns(self) -> int:
        return grid_columns(float(self.width()))

    def apply_filter(self, query: str) -> None:
        """搜索过滤（名称/子标题/key 子串；空 = 全部）。"""
        q = str(query or "").strip().lower()
        if not q:
            self._visible_keys = None
        else:
            self._visible_keys = {
                it[0] for it in self._items
                if q in f"{it[1]} {it[2]} {it[0]}".lower()}
        self._relayout()
        self.update()

    # ── 布局 ────────────────────────────────────────────────────────────
    def _relayout(self) -> None:
        cols = grid_columns(float(self.width()))
        keys = [it[0] for it in self._items
                if self._visible_keys is None or it[0] in self._visible_keys]
        by_key = {it[0]: it for it in self._items}
        self._lay = []
        for i, k in enumerate(keys):
            row, col = divmod(i, cols)
            x = PAD + col * (TILE_W + TILE_GAP)
            y = PAD + row * (TILE_H + TILE_GAP)
            key, name, sub, icon, color, art = by_key[k]
            self._lay.append({"key": key, "name": name, "sub": sub,
                              "icon": icon, "color": color, "art": art,
                              "rect": QRectF(x, y, TILE_W, TILE_H)})
        rows = (len(keys) + cols - 1) // cols
        self.setMinimumHeight(int(PAD * 2 + max(1, rows) * TILE_H
                                  + max(0, rows - 1) * TILE_GAP))

    def resizeEvent(self, ev):  # noqa: N802
        self._relayout()
        super().resizeEvent(ev)

    # ── 交互 ────────────────────────────────────────────────────────────
    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_pos = ev.position()
            self._press_key = self.tile_at(ev.position())
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):  # noqa: N802
        hover = self.tile_at(ev.position())
        if hover != self._hover_key:
            self._hover_key = hover
            self.update()
        if (self._press_pos is not None and self._press_key
                and ev.buttons() & Qt.MouseButton.LeftButton
                and (ev.position() - self._press_pos).manhattanLength() > 6):
            key, self._press_key, self._press_pos = self._press_key, "", None
            self.drag_started.emit(key)
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        pressed, self._press_pos = self._press_pos, None
        key, self._press_key = self._press_key, ""
        if (pressed is not None and key
                and ev.button() == Qt.MouseButton.LeftButton
                and self.tile_at(ev.position()) == key):
            self.activated.emit(key)
        super().mouseReleaseEvent(ev)

    def leaveEvent(self, ev):  # noqa: N802
        self._hover_key = ""
        self.update()
        super().leaveEvent(ev)

    # ── 绘制 ────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):  # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if not self._lay:
                self._paint_empty_state(p)
                return
            for lay in self._lay:
                self._paint_tile(p, lay)
        finally:
            p.end()

    def _paint_empty_state(self, p: QPainter) -> None:
        """R183-H：无可见瓦片时的 B0 插画空态（无 Doodles 资产时仅提示）。

        两种语义各配一图：搜索无结果 → `EMPTY_ART_ID`（chilling）；零条目
        （空目录）→ `EMPTY_ART_ID_ZERO`（meditating）。插画 64px 居中、muted 灰
        （与 `empty_state.EmptyState` 同色口径），下方一行提示；空间不足只画提示。
        """
        w, h = float(self.width()), float(self.height())
        if w < 80 or h < 40:
            return
        no_match = self._visible_keys is not None
        text = "没有匹配的条目" if no_match else "暂无条目"
        art_id = EMPTY_ART_ID if no_match else EMPTY_ART_ID_ZERO
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(QColor(TEXT_DISABLED))
        ty = h / 2.0 + 6.0
        if h >= EMPTY_ART_PX + 28:
            if _draw_art_thumb(p, art_id, (w - EMPTY_ART_PX) / 2.0,
                               h / 2.0 - EMPTY_ART_PX / 2.0 - 8.0,
                               size=EMPTY_ART_PX, color=TEXT_DISABLED):
                ty = h / 2.0 + EMPTY_ART_PX / 2.0 + 2.0
        p.drawText(QRectF(0, ty - 10.0, w, 20.0), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_tile(self, p: QPainter, lay: dict) -> None:
        r: QRectF = lay["rect"]
        hover = str(lay["key"]) == self._hover_key
        color = str(lay["color"]) or FOCUS_BLUE
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(BG_PANEL_SUB if hover else BG_PANEL)))
        p.drawRoundedRect(r, 5, 5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 5, 5)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), 3.0), QColor(color))
        # R183-H：art 缩略走同一裁剪域绘制（16px 槽位恒定，悬停不位移、不越圆角；
        # 先底后图保证不透明度层次：底图 0.9 / 悬停 1.0，不与色带/文字抢对比）。
        # 功能节点目录：标题、功能图标和分组色表达语义，避免无关画作干扰。
        _draw_icon(p, str(lay["icon"]), r.left() + 8, r.top() + 8, size=16, color=color)
        p.restore()
        p.setPen(QPen(QColor(FOCUS_BLUE if hover else BORDER_NORMAL), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 5, 5)
        f = QFont()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        text = fm.elidedText(str(lay["name"]), Qt.TextElideMode.ElideRight,
                             int(r.width()) - 34)
        p.setPen(QColor(TEXT_PRIMARY if hover else TEXT_SECONDARY))
        p.drawText(QRectF(r.left() + 28, r.top() + 8, r.width() - 34, 18),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        f2 = QFont()
        f2.setPixelSize(9)
        p.setFont(f2)
        p.setPen(QColor(TEXT_DISABLED))
        sub = fm.elidedText(str(lay["sub"]), Qt.TextElideMode.ElideRight,
                            int(r.width()) - 14)
        p.drawText(QRectF(r.left() + 7, r.bottom() - 18, r.width() - 14, 14),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sub)


_ICON_CACHE: dict = {}


def _resolve_art_id(key: str, explicit: str) -> str:
    """B0 art_id 解析（set 时一次）：显式 > `<key>` > `eff_<key>` > ""（无则回退图标）。

    两族用户 key 已核零碰撞（85 个效果 key 均不在 `cards/` id 里；M4 扩容后复核），
    解析次序稳定。
    """
    if explicit:
        return explicit
    if _art_lib is None or not key:
        return ""
    try:
        if _art_lib.art_meta(key) is not None:
            return key
        eff = "eff_" + key
        if _art_lib.art_meta(eff) is not None:
            return eff
    except Exception:                                   # pragma: no cover
        return ""
    return ""


_ART_PM_CACHE: dict = {}


def _draw_art_thumb(p: QPainter, art_id: str, x: float, y: float, *,
                    size: int = 16, color: str = TEXT_PRIMARY) -> bool:
    """B0 美术缩略（R183-B1）：命中画图返回 True；未命中/失败 False（调用方回退图标）。

    结果负缓存（同 key 只查一次）；绘制走 `art_asset_library` 的 LRU 位图缓存。
    """
    if not art_id or _art_lib is None:
        return False
    ck = (str(art_id), str(color), int(size))
    if ck not in _ART_PM_CACHE:
        try:
            pm = _art_lib.load_art_pixmap(art_id, size, size, str(color))
        except Exception:                               # pragma: no cover
            pm = None
        _ART_PM_CACHE[ck] = pm if (pm is not None and not pm.isNull()) else None
    pm = _ART_PM_CACHE[ck]
    if pm is None:
        return False
    p.drawPixmap(QPointF(float(x), float(y)), pm)
    return True


def _draw_icon(p: QPainter, name: str, x: float, y: float, *,
               size: int = 16, color: str = TEXT_PRIMARY) -> None:
    """tabler 小图标（懒建缓存；失败静默不阻塞绘制）。"""
    key = (str(name), str(color), int(size))
    if key not in _ICON_CACHE:
        try:
            from ..main_icon_library import load_tabler_icon
            pm = load_tabler_icon(key[0], size, color).pixmap(size, size)
        except Exception:
            pm = None
        _ICON_CACHE[key] = pm
    pm = _ICON_CACHE[key]
    if pm is not None and not pm.isNull():
        p.drawPixmap(int(x), int(y), pm)
