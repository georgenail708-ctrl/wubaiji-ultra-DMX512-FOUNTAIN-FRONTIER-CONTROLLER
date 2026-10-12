# -*- coding: utf-8 -*-
"""存储区：场景槽位网格（自绘编号格 + 拖拽换位 + 空槽新建 + 右键菜单）。

设计取舍：本模块只管原画与交互，数据一律经 store（EffectCacheStore），
      动作语义（存/调用/清空）经注入的回调 —— 与 `table_view` 同样的零耦合纪律。
    · 绘制/命中/拖拽/右键共用同一份布局真源（`_relayout` 缓存的 `_lay_items`）。
    · 卡背景复用显式纯背景缓存，前景墨字独立保持清晰。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFontMetrics, QLinearGradient, QPainter,
                         QPainterPath, QPen)
from PyQt6.QtWidgets import QWidget

from .. import qascade_theme as _th
from ..editor_backdrop import paint_background
from .scene_store_band import (_finish_band, _slice_slots,
                              ensure_growth_for_scroll, on_hover_or_band,
                              SceneStoreBandMixin)
from .table_view import ui_font

# ── 造型（4px 基准；与表格视图同一套语言）──────────────────────────────────
PAD = 8
SLOT_MIN = 62            # 槽位最小边长
SLOT_MAX = 108           # 槽位最大边长
SLOT_GAP = 6             # 槽位间距（存储卡要呼吸感，不像表格那样密排）
CARD_R = 7.0             # 卡片圆角（液态玻璃主视觉）
#: ★ 2026-09-14 用户定案（无限列表）：空槽**始终**多铺这么多行，方便往任意位置放
#:   场景（原实现只多铺一格 = "多余的一个格子"，位置不够用）。用户要求
#:   "自动渲染一个无限多的列表（注意前端性能优化）" —— 真正的无限不可行（会无限
#:   占内存/绘制），故取"多铺 N 行空槽 + 滚动到底自动续铺"：观感等同无限，
#:   而绘制量始终 ∝ 可见槽数（性能纪律：复杂度不得 ∝ 场景数）。
EXTRA_ROWS = 6
#: 滚动到距底部多少像素时再续铺一行空槽（"无限"滚动的触发余量）。
GROW_MARGIN_PX = 160


class SceneStoreView(SceneStoreBandMixin, QWidget):
    """存储区场景槽位视图（自绘；单一布局真源）。

    公开接口：set_scenes / count / items / selected_ids / active_ids /
        set_active / set_selected / clear_selection / content_height /
        empty-slot·item 命中经 `_hit`。
    信号：slot_activated(id) / empty_slot_clicked(position) /
          context_menu(kind, key, global_pos) / reordered()
    """

    slot_activated = pyqtSignal(object)          # 条目 id（左键点=调用/触发）
    empty_slot_clicked = pyqtSignal(object)      # 空槽位置（点空槽=新建）
    context_menu = pyqtSignal(object, object, object)   # (kind, key, global_pos)
    reordered = pyqtSignal(str, int, int)        # (entry_id, old_pos, new_pos)
    #: ★ 2026-09-14 flash 模式需要"按住才应用、松手即释放"：这两个是鼠标左键
    #:   按下/松开的**原始事件**（携带条目 id）。非 flash 模式的面板可忽略它们。
    slot_pressed = pyqtSignal(object)            # 左键在占用槽上按下
    slot_released = pyqtSignal(object)           # 左键松开（未发生拖拽换位）
    #: 空白/框选处右键 → 作用于"框选命中或已选中"的批量操作（用户 2026-09-14 要求
    #: "框选可以一键开启多个保存的 scene，也能一键改变多个 scene 的触发模式"）。
    area_context_menu = pyqtSignal(object)       # global_pos

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scenes: List[Any] = []
        self._selected: set = set()
        #: 上次点击的条目 id（Shift 范围选择锚点）
        self._last_click_iid: Optional[str] = None
        #: **已激活**（正在输出）的场景 id 集 —— cue 高亮语义（用户 2026-09-14）：
        #  latch/hold 点击后保持在此集合；flash 只在按住期间在内。
        self._active: set = set()
        #: 按下但尚未松开的条目 id（flash 判定"松手释放"用）
        self._pressed_iid: Optional[str] = None
        #: 框选状态（空白处起框；用户 2026-09-14 要求"保存区也支持框选"）
        self._banding: bool = False
        self._band_origin = None
        self._band_current = None
        self._band_base: set = set()
        self._hover: Optional[str] = None
        # R194/S2-01：悬停身份另一半（槽位序 + 上次悬停矩形）。空槽之间 id 恒为
        # None 但"＋"按槽位画；`_hover_rect` 供 hover 变化时 update(旧∪新) 收窄。
        self._hover_slot: Optional[int] = None
        self._hover_rect: Optional[QRect] = None
        self._lay_items: List[dict] = []
        self._content_h = PAD * 2
        self._in_relayout = False      # 重排重入守卫（见 _relayout）
        self._drag_id: Optional[str] = None
        self._drag_pos: Optional[int] = None
        self._hover_pos = None      # 鼠标当前位置（空槽画"＋"用，避免 cursor() 跨屏取错）
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(PAD * 2)

    # ── 数据 ────────────────────────────────────────────────────────────
    def set_scenes(self, scenes: List[Any]) -> None:
        self._scenes = list(scenes or [])
        self._relayout()
        self.update()

    def count(self) -> int:
        return len(self.items())

    def items(self) -> List[Any]:
        """全部场景条目（顺序 = 槽位序）。"""
        return sorted(self._scenes, key=self._pos_of)

    def selected_ids(self) -> List[str]:
        return [str(getattr(i, "id", "")) for i in self.items()
                if str(getattr(i, "id", "")) in self._selected]

    def active_ids(self) -> List[str]:
        """已激活（正在输出）的场景 id 集 —— cue 高亮用。

        返回**原始激活集**（不按已加载条目过滤）：激活是"哪个场景在输出"的
        权威状态，条目列表刷新（搜索/重载）不得让它凭空消失。
        """
        return sorted(self._active)

    def set_active(self, ids) -> None:
        """设置激活集（点击触发后由面板同步；只影响高亮，不改数据）。"""
        new = {str(x) for x in (ids or set())}
        if new != self._active:
            self._active = new
            self.update()

    def clear_active(self) -> None:
        if self._active:
            self._active.clear()
            self.update()

    def set_selected(self, ids) -> None:
        self._selected = {str(x) for x in (ids or set())}
        self.update()

    def select_all(self) -> None:
        self._selected = {str(getattr(i, "id", "")) for i in self.items()}
        self.update()

    def clear_selection(self) -> None:
        self._selected.clear()
        self.update()

    def content_height(self) -> int:
        return int(self._content_h)

    # ── 布局（唯一真源）─────────────────────────────────────────────────
    @staticmethod
    def _pos_of(item: Any) -> int:
        try:
            return int(getattr(item, "position", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _slot_metrics(self, width: int) -> Tuple[int, int]:
        """列数 + 槽位边长（可用宽均摊，夹在 [SLOT_MIN, SLOT_MAX]）。"""
        usable = max(10, int(width) - PAD * 2)
        cols = max(1, (usable + SLOT_GAP) // (SLOT_MIN + SLOT_GAP))
        side = (usable - (cols - 1) * SLOT_GAP) // cols
        side = max(SLOT_MIN, min(SLOT_MAX, side))
        return cols, side

    def _compute_layout(self) -> Tuple[List[dict], int]:
        """产出全部布局项（唯一真源：绘制/命中/拖拽共用）。"""
        items: List[dict] = []
        w = max(self.width(), SLOT_MIN + PAD * 2)
        cols, side = self._slot_metrics(w)
        y = self._emit_slot_group(items, self._scenes, PAD, cols, side)
        return items, y + PAD

    def _emit_slot_group(self, items: List[dict], lst: List[Any],
                         y: int, cols: int, side: int) -> int:
        """铺一组槽位（**空槽也画**：点空槽=新建，是 的核心交互）。

        空槽数量 = max(至少一屏, 覆盖最大位置) + EXTRA_ROWS 行（见 EXTRA_ROWS
        注释的"无限列表"定案）；滚到底自动续铺走 `ensure_growth_for_scroll`。
        """
        items_sorted = sorted(lst or [], key=self._pos_of)
        by_pos: Dict[int, Any] = {}
        for it in items_sorted:
            by_pos[self._pos_of(it)] = it
        # 该组占用的最大槽位（决定至少要铺到哪）
        max_pos = max(by_pos) if by_pos else -1
        # 用户手工续铺的额外行数（滚动到底递增；见 ensure_growth_for_scroll）
        # ★ 必须与 EXTRA_ROWS **相加**（不是取 max）—— 取 max 会让前 6 次续铺全是
        #   空操作（`max(1,6)==6`），表现为"滚到底也不涨"（自测抓到）。
        extra = EXTRA_ROWS + max(0, int(getattr(self, "_extra_rows", 0) or 0))
        # 覆盖最大位置所需行数 + 额外空行
        need_rows = max(1, ((max_pos + 1) + cols - 1) // cols) + extra
        total = max(cols, need_rows * cols)
        for idx in range(total):
            col = idx % cols
            row = idx // cols
            r = QRect(PAD + col * (side + SLOT_GAP),
                      y + row * (side + SLOT_GAP), side, side)
            items.append({"kind": "slot", "rect": r, "position": idx,
                          "item": by_pos.get(idx)})
        rows = (total + cols - 1) // cols
        return y + rows * (side + SLOT_GAP) + SLOT_GAP

    def ensure_growth_for_scroll(self, viewport_bottom: int) -> bool:
        """滚动接近底部时续铺一行空槽（"无限列表"；实现见 scene_store_band）。"""
        return ensure_growth_for_scroll(self, viewport_bottom)

    def _relayout(self) -> None:
        """重排并设最小高度（唯一布局真源；paint/命中/拖拽只读 `_lay_items`）。

        重入守卫（必需）：`resizeEvent → _relayout → setMinimumHeight →
        resizeEvent → …` 会无界递归、栈溢出硬崩；守卫让二次重排直接返回。
        """
        if self._in_relayout:
            return
        self._in_relayout = True
        try:
            items, h = self._compute_layout()
            self._lay_items = items
            self._content_h = h
            self.setMinimumHeight(int(h))
        finally:
            self._in_relayout = False

    # ── 命中 ────────────────────────────────────────────────────────────
    def _hit(self, pos) -> Optional[dict]:
        for it in self._lay_items:
            if it["kind"] == "slot" and it["rect"].contains(pos):
                return it
        return None

    # ── 绘制 ────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dirty = _ev.rect()
        paint_background(p, self, QColor(_th.BG_ROOT), dirty)
        # R194/S2-01（照 table_view.paintEvent 先例）：只读 `_relayout` 缓存的
        # 布局真源，不再每 paint 全量重算；按 y 递增 bisect 只绘可视段。
        for it in _slice_slots(self._lay_items, dirty):
            if it["kind"] == "slot":
                self._draw_slot(p, it)
        # 框选矩形（与调度台/监视器同款虚线语义）
        rect = self._band_rect()
        if rect is not None and not rect.isNull():
            p.setPen(QPen(QColor(_th.FOCUS_BLUE), 1, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(113, 168, 255, 35)))
            p.drawRect(rect)
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.end()

    def _draw_slot(self, p: QPainter, it: dict) -> None:
        """槽位：空槽 = 虚线玻璃底 + 编号 + "＋"；占用 = 满玻璃卡片。"""
        r = it["rect"]
        item = it.get("item")
        if item is None:
            self._draw_empty_slot(p, r, int(it.get("position", 0)))
            return
        self._draw_card(p, r, item, int(it.get("position", 0)))

    def _draw_empty_slot(self, p: QPainter, r: QRect, position: int) -> None:
        """空槽：极淡虚线 + 中心编号 + 半透明 ＋（可读但不抢眼）。"""
        rr = r.adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rr), CARD_R, CARD_R)
        p.fillPath(path, QBrush(QColor(_th.BG_INPUT)))
        pen = QPen(QColor(_th.BORDER_NORMAL), 1, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawPath(path)
        f = ui_font(9)
        p.setFont(f)
        p.setPen(QColor(_th.TEXT_DISABLED))
        p.drawText(r.adjusted(6, 4, -6, 0),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                   str(position + 1))
        if self._hover_pos is not None and r.contains(self._hover_pos):
            f2 = ui_font(14)
            p.setFont(f2)
            p.setPen(QColor(_th.FOCUS_BLUE))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "＋")

    def _draw_card(self, p: QPainter, r: QRect, item: Any, position: int) -> None:
        """占用槽位：液态玻璃卡（编号徽章 + 名称 + 副行 + 分类色渐变）。

        已激活（cue 高亮）：整卡点亮，与"仅选中"（蓝）区分开。
        """
        rr = r.adjusted(0, 0, -1, -1)
        iid = str(getattr(item, "id", ""))
        active = iid in self._active
        accent = QColor(str(getattr(item, "color", "") or "") or _th.BRIGHT_INFO)
        if not accent.isValid():
            accent = QColor(_th.BRIGHT_INFO)
        self._draw_card_body(p, rr, accent, selected=(iid in self._selected),
                             active=active, hovered=(iid == self._hover))
        by, bh = rr.top() + 3, 12
        self._draw_badge(p, QRect(rr.left() + 4, by, 16, bh), str(position + 1),
                         _th.FOCUS_BLUE)
        # 模式角标（右上）：L = latch 自锁 / F = flash 点动。
        # ★ 2026-09-18 用户实报：flash 显示成 H（hold 时代遗留的三元旧值）→ 取真模式，
        #   未知回落 flash（非自锁）。
        try:
            _is_latch = str(item.effective_mode()) == "latch"
        except Exception:
            _is_latch = bool(getattr(item, "is_latch", lambda: False)())
        self._draw_badge(p, QRect(rr.right() - 16, by, 13, bh), "L" if _is_latch else "F",
                         _th.FOCUS_BLUE if _is_latch else _th.BRIGHT_WARN)
        self._draw_card_text(p, rr, item, by + bh + 3)

    def _draw_card_body(self, p, rr: QRect, accent: QColor, *, selected, active,
                        hovered) -> None:
        """卡片玻璃底：按 激活 > 选中 > 悬停 > 常态 的优先级取渐变与描边。"""
        path = QPainterPath()
        path.addRoundedRect(QRectF(rr), CARD_R, CARD_R)
        if active:
            # 激活态优先级最高：无论是否选中/悬停，都点亮为"正在输出"
            top = QColor(_th.EDITOR_IVORY["selected"])
            bot = QColor(_th.EDITOR_IVORY["panel"])
            border = QColor(accent)
        elif selected:
            top = QColor(_th.EDITOR_IVORY["selected"])
            bot = QColor(_th.EDITOR_IVORY["surface"])
            border = QColor(_th.EDITOR_IVORY["focus"])
        elif hovered:
            top = QColor(_th.EDITOR_IVORY["surface"])
            bot = QColor(_th.EDITOR_IVORY["hover"])
            border = QColor(_th.BORDER_NORMAL)
        else:
            top = QColor(_th.EDITOR_IVORY["surface"]); top.setAlpha(176)
            bot = QColor(_th.EDITOR_IVORY["panel"]); bot.setAlpha(144)
            border = QColor(_th.BORDER_NORMAL)
        grad = QLinearGradient(0, rr.top(), 0, rr.bottom())
        grad.setColorAt(0.0, top); grad.setColorAt(1.0, bot)
        p.fillPath(path, QBrush(grad))
        # 顶部内高光（满强度——存储卡不是数字表，可承担浓玻璃）
        hl = QPainterPath()
        hl.addRoundedRect(QRectF(rr.left() + 2, rr.top() + 1.5,
                                 rr.width() - 4, 5.5), 4.0, 4.0)
        p.fillPath(hl, QBrush(QColor(255, 255, 255, 34)))
        p.setPen(QPen(border, 2 if active else 1))
        p.drawPath(path)
        p.setPen(QPen(accent, 3 if active else 2))
        p.drawLine(rr.left() + 6, rr.bottom() - 3, rr.right() - 6, rr.bottom() - 3)

    @staticmethod
    def _draw_badge(p, rect: QRect, text: str, color) -> None:
        """小角标（编号 / 模式）：黑底圆角 + 彩色字。"""
        bp = QPainterPath()
        bp.addRoundedRect(QRectF(rect), 3.0, 3.0)
        p.fillPath(bp, QBrush(QColor(_th.BG_INPUT)))
        p.setFont(ui_font(8, True))
        p.setPen(QColor(_th.TEXT_PRIMARY))
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)

    @staticmethod
    def _draw_card_text(p, rr: QRect, item: Any, text_top: int) -> None:
        """名称（粗体）+ 副行（灰）——均右省略。"""
        fm = QFontMetrics(ui_font(10, True))
        p.setFont(ui_font(10, True))
        p.setPen(QColor(_th.TEXT_PRIMARY))
        p.drawText(QRect(rr.left() + 5, text_top, rr.width() - 10, 14),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   fm.elidedText(str(getattr(item, "name", "") or ""),
                                 Qt.TextElideMode.ElideRight, rr.width() - 10))
        try:
            sub = str(item.subtitle())
        except Exception:
            sub = f"{len(getattr(item, 'values', {}) or {})} 台"
        f2 = ui_font(9)
        p.setFont(f2)
        p.setPen(QColor(_th.TEXT_SECONDARY))
        p.drawText(QRect(rr.left() + 5, rr.bottom() - 17, rr.width() - 10, 13),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   QFontMetrics(f2).elidedText(sub, Qt.TextElideMode.ElideRight,
                                               rr.width() - 10))

    # ── 鼠标 ────────────────────────────────────────────────────────────
    def mouseMoveEvent(self, e):  # noqa: N802
        """悬停高亮 + **框选推进**（原实现没有本方法 → 框选永远起不来，见 band 模块）。"""
        on_hover_or_band(self, e.position().toPoint())

    def mousePressEvent(self, e):  # noqa: N802
        """按下：占用卡 → 点选/拖拽换位；**空槽/空白 → 起框选**（见 _maybe_begin_band）。

        ★ 2026-09-14 用户实报"框选调不出来"：原实现在空槽上按下会**立即**发
          `empty_slot_clicked`（新建场景），而空槽又占满全屏 → 既起不了框选、又到处
          误建场景。现改为：空槽按下先起框选；**松开时若没拖动**（原地按）才算
          "点空槽新建"（见 mouseReleaseEvent），两种意图不再互相打架。
        """
        pos = e.position().toPoint()
        self._hover_pos = pos
        if e.button() != Qt.MouseButton.LeftButton:
            return
        _shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._maybe_begin_band(pos, _shift)
        it = self._hit(pos)
        if it is None:
            self._clear_selection_on_blank(e)
            return
        if it["kind"] != "slot":
            return
        item = it.get("item")
        if item is None:
            return                  # 空槽：按下不起动作，等松开判定（框选 or 新建）
        self._press_on_item(item, int(it.get("position", 0)), e.modifiers())

    def _clear_selection_on_blank(self, e) -> None:
        """点空白（无修饰键）→ 清空选中；Shift/Ctrl 时保留（便于增量框选）。"""
        if e.modifiers() & (Qt.KeyboardModifier.ShiftModifier |
                            Qt.KeyboardModifier.ControlModifier):
            return
        self._selected.clear()
        self.update()

    def _press_on_item(self, item, position: int, mods) -> None:
        """在占用槽上按下：多选（Ctrl/Shift）→ 记录 → 发按下/激活事件。

        ★ 多选语义（用户 2026-09-14 实报"选中一个自动释放其它"）：
          Ctrl 切换该格、Shift 从上次点击范围追加、无修饰键单选。
        ★ flash 需要"按住才应用"：先发 `slot_pressed`（原始按下），再发
          `slot_activated`（latch/hold 的点击切换用它）。
        """
        iid = str(getattr(item, "id", ""))
        if mods & Qt.KeyboardModifier.ShiftModifier:
            self._select_range(iid, int(position))
        elif mods & Qt.KeyboardModifier.ControlModifier:
            self._selected.symmetric_difference_update({iid})
        else:
            self._selected = {iid}
        self._last_click_iid = iid
        self.update()
        self._pressed_iid = iid
        self.slot_pressed.emit(iid)
        self.slot_activated.emit(iid)
        self._drag_id, self._drag_pos = iid, int(position)

    def mouseReleaseEvent(self, e):  # noqa: N802
        """左键松开：结束框选 / 空槽单击新建 / flash 释放 / 槽位换位。

        ★ 2026-09-14：空槽按下时既可能想**框选**、也可能想**新建场景**，靠"松开时
          有没有拖动"区分 —— 拖出了矩形（或位移超过阈值）= 框选；原地按下松开
          = 点空槽新建（保留原"点空格即存"的核心交互）。判定抽到 `scene_store_band`。
        """
        if getattr(self, "_banding", False):
            _finish_band(self, e.position().toPoint())
            return
        pressed = getattr(self, "_pressed_iid", None)
        self._pressed_iid = None
        if pressed:
            # ★★ 2026-09-14 深查修正（flash 拖拽后卡死）：原实现在"拖拽换位成功"时
            #   直接 return，**不发 `slot_released`** → flash 场景靠该信号松手释放，
            #   于是"按住 flash 卡拖到别的槽再松手"效果一直亮着（且卡片保持高亮）。
            #   换位与释放是两件独立的事：先发释放，再处理换位。
            self.slot_released.emit(pressed)   # ★ flash：松手即释放（无论是否换位）
            if self._finish_drag(e.position().toPoint()):
                return                      # 拖拽换位（已发 reordered）
            self.update()
            return
        if self._drag_id:
            self._finish_drag(e.position().toPoint())

    def _finish_drag(self, pos) -> bool:
        """若发生了跨槽拖拽 → 发 reordered 并返回 True；否则 False。"""
        if not self._drag_id:
            return False
        it = self._hit(pos)
        src_id, src_pos = self._drag_id, self._drag_pos
        self._drag_id, self._drag_pos = None, None
        if it is not None and it["kind"] == "slot":
            dst = int(it.get("position", -1))
            if dst >= 0 and src_pos is not None and dst != src_pos:
                self.reordered.emit(str(src_id), int(src_pos), dst)
                return True
        self.update()
        return False

    def contextMenuEvent(self, e):  # noqa: N802
        it = self._hit(e.pos())
        if it is None:
            # 空白处右键 → 作用于"框选命中或已选中"的批量菜单（一键开启/关闭/改模式）
            self.area_context_menu.emit(e.globalPos())
            return
        if it["kind"] == "slot":
            item = it.get("item")
            if item is None:
                self.context_menu.emit("empty", int(it.get("position", 0)), e.globalPos())
            else:
                key = {"id": str(getattr(item, "id", "")),
                       "position": int(it.get("position", 0))}
                self.context_menu.emit("slot", key, e.globalPos())

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._relayout()

    def leaveEvent(self, e):  # noqa: N802
        # R194/S2-01：清完整悬停身份（id+槽位）——原只看 id，悬在空槽离开会残留"＋"。
        if self._hover is not None or self._hover_slot is not None:
            self._hover = None
            self._hover_slot = None
            self._hover_rect = None
            self.update()

__all__ = ["SceneStoreView", "SLOT_MIN"]
