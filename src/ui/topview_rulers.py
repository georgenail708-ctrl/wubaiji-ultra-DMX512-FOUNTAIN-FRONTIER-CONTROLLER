# -*- coding: utf-8 -*-
"""TopView 标尺 + 参考线（R183/F2 · 深查③ T1/T2）。

从 `timeline/timeline_ruler.py` 借来「自适应刻度 + 循环上界截断」的绘制先例，
但量纲从"秒"换成"画布世界单位"（`core.constants.CANVAS_UNITS_PER_CM = 1.0`
→ 1 世界单位 = 1 cm，故刻度按 cm/m 双档标注）。

本模块只负责：刻度数学（纯函数，可 headless 单测）+ 两条标尺控件 + 参考线
模型/覆盖层。吸附判定在 `topview_snap.py`，装配与工具条注入在
`topview_overlays.py`（`TopViewPanel` 是冻结上帝类，新功能一律注入式接线）。

参考线持久化：列表存在**当前页 dict** 的自由键 `guides` 里 —— `save_state`
把 `_pages` 整棵子树落盘、`load_state` 整棵灌回，故零改动即随工程保存。
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from . import qascade_theme as _th
from .topview_guides import GuideSet, guide_draw_color

#: 标尺厚度（屏幕像素）：两条标尺各占画布上/左边缘一条。
RULER_THICK = 22
#: 主刻度最小屏幕间距（沿用 timeline_ruler 的 55px 观感）。
MIN_TICK_PX = 55.0
#: 1-2-5 阶梯候选（世界单位 = cm）；覆盖 ±5000 世界跨度的真工程（x∈[-3858,247]）。
TICK_STEPS: Tuple[float, ...] = (
    0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0,
    200.0, 250.0, 500.0, 1000.0, 2000.0, 2500.0, 5000.0, 10000.0, 20000.0,
)
#: 网格吸附基线步长（与 `TopViewCanvas._draw_grid` 同源常量）。
GRID_BASE_STEP = 25.0


# ── 刻度纯函数（headless 可测）────────────────────────────────────────────

def choose_tick_step(view_scale: float, min_px: float = MIN_TICK_PX) -> float:
    """选择主刻度世界步长：阶梯中第一个屏幕间距 ≥ min_px 的档位。"""
    scale = max(1e-6, float(view_scale or 1.0))
    for step in TICK_STEPS:
        if step * scale >= float(min_px):
            return float(step)
    return float(TICK_STEPS[-1])


def grid_step_for(view_scale: float, base: float = GRID_BASE_STEP) -> float:
    """画布网格步长（与 `_draw_grid` 的两段 while 完全同式）→ 吸附用同一量化。"""
    scale = max(1e-6, float(view_scale or 1.0))
    step = float(base)
    while step * scale < 16.0:
        step *= 2.0
    while step * scale > 120.0 and step > 6.25:
        step /= 2.0
    return step


def snap_to_step(value: float, step: float) -> float:
    """最近网格点（step 非法/为 0 时原值返回）。"""
    s = float(step or 0.0)
    if s <= 0.0:
        return float(value)
    return round(float(value) / s) * s


def format_tick_label(value: float, step: float) -> str:
    """刻度文案：步长 ≥100 世界单位（=1 m）走米制，否则厘米制。"""
    v = float(value)
    if float(step) >= 100.0:
        text = f"{v / 100.0:.2f}".rstrip("0").rstrip(".")
        return f"{text or '0'}m"
    text = f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{text or '0'}"


def ticks_for(view_scale: float, offset_px: float, length_px: float,
              min_px: float = MIN_TICK_PX) -> List[Tuple[float, float, str]]:
    """返回 [(屏幕像素位置, 世界值, 文案)]。

    与 `TimelineRuler.paintEvent` 同构：可见区起点向下取整到步长倍数、终点
    (可见区 + 80px 缓冲) 上取整 —— 超出可见区的刻度本就会被过滤掉，提前截断
    循环次数不改变任何绘制输出。
    """
    scale = max(1e-6, float(view_scale or 1.0))
    step = choose_tick_step(scale, min_px)
    off = float(offset_px)
    length = max(0.0, float(length_px))
    world_start = (0.0 - off) / scale
    world_end = (length + 80.0 - off) / scale
    first = math.floor(world_start / step) * step
    last = math.ceil(world_end / step) * step
    out: List[Tuple[float, float, str]] = []
    v = first
    guard = 0
    while v <= last + 1e-9 and guard < 4096:
        out.append((v * scale + off, v, format_tick_label(v, step)))
        v += step
        guard += 1
    return out


# ── 标尺控件 ──────────────────────────────────────────────────────────────

class _BaseRuler(QWidget):
    """标尺基类：世界↔屏幕换算、刻度绘制、参考线拖出/移动/锁定/删除。"""

    HORIZONTAL = True

    def __init__(self, canvas, guides: GuideSet, parent=None) -> None:
        super().__init__(parent if parent is not None else canvas)
        self._canvas = canvas
        self._guides = guides
        self._drag_guide: Optional[dict] = None
        self._drag_moved = False
        self._pending_axis: str = ""   # 尚未落盘的待建参考线轴（首次拖动才创建）
        self._hover_guide: Optional[dict] = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SplitHCursor if self.HORIZONTAL
                       else Qt.CursorShape.SplitVCursor)
        self.setToolTip("从标尺拖出参考线；拖动参考线可移动；右键可锁定/删除/清空；"
                        "拖动设备时按住 Alt 可临时关闭吸附")

    # ── 视图同步 ──
    def _scale(self) -> float:
        return max(0.005, float(getattr(self._canvas, "_view_scale", 1.0) or 1.0))

    def _offset(self) -> float:
        off = getattr(self._canvas, "_view_offset", QPointF(0.0, 0.0))
        try:
            return float(off.x() if self.HORIZONTAL else off.y())
        except Exception:
            return 0.0

    def _length_px(self) -> float:
        return float(self.width() if self.HORIZONTAL else self.height())

    def screen_to_world(self, px: float) -> float:
        return (float(px) - self._offset()) / self._scale()

    def world_to_screen(self, world: float) -> float:
        return float(world) * self._scale() + self._offset()

    def _snapshot(self) -> None:
        """参考线变更前登记撤销域（参考线存在页 dict 里 → 属 DOMAIN_TOPVIEW）。

        `TopViewPanel._snapshot_for_undo` 会把面板状态写回工程再压快照，故与
        页面/坐标/图形的撤销语义完全一致；取不到 PM 时静默（无撤销环境）。
        """
        panel = getattr(self._canvas, "panel", None)
        fn = getattr(panel, "_snapshot_for_undo", None) if panel is not None else None
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    # ── 绘制 ──
    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.fillRect(self.rect(), QColor(_th.CANVAS_CARD))
            p.setFont(_th.cached_font(10, families=_th.MONO_FONT_FAMILIES))
            scale = self._scale()
            for pos_px, world, label in ticks_for(scale, self._offset(), self._length_px()):
                self._paint_tick(p, pos_px, label)
            for guide in self._guides.items():
                self._paint_guide_marker(p, guide)
            self._paint_edge(p)
        finally:
            p.end()

    def _paint_tick(self, p: QPainter, pos_px: float, label: str) -> None:
        h, w = self.height(), self.width()
        if self.HORIZONTAL:
            if not (-80 <= pos_px <= w + 80):
                return
            p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
            p.drawLine(int(pos_px), h - 8, int(pos_px), h)
            p.setPen(QColor(_th.TEXT_SECONDARY))
            p.drawText(int(pos_px) + 3, 0, 90, h - 9,
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       label)
            return
        if not (-80 <= pos_px <= h + 80):
            return
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        p.drawLine(w - 8, int(pos_px), w, int(pos_px))
        p.save()
        p.translate(2, int(pos_px) + 3)
        p.rotate(90)
        p.setPen(QColor(_th.TEXT_SECONDARY))
        p.drawText(0, 0, 90, w - 9,
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), label)
        p.restore()

    def _paint_guide_marker(self, p: QPainter, guide: dict) -> None:
        axis = str(guide.get("axis", ""))
        if axis != ("x" if not self.HORIZONTAL else "y"):
            return
        pos_px = self.world_to_screen(float(guide.get("pos", 0.0)))
        color = guide_draw_color(guide)
        p.setPen(QPen(color, 2))
        p.setBrush(QBrush(color))
        if self.HORIZONTAL:
            if -4 <= pos_px <= self.width() + 4:
                p.drawLine(int(pos_px), self.height() - 12, int(pos_px), self.height())
                p.drawRect(QRectF(pos_px - 3, 2, 6, 6))
        else:
            if -4 <= pos_px <= self.height() + 4:
                p.drawLine(self.width() - 12, int(pos_px), self.width(), int(pos_px))
                p.drawRect(QRectF(2, pos_px - 3, 6, 6))

    def _paint_edge(self, p: QPainter) -> None:
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        if self.HORIZONTAL:
            p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        else:
            p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

    # ── 交互 ──
    def _guide_axis(self) -> str:
        return "y" if self.HORIZONTAL else "x"

    def _canvas_point(self, ev) -> QPointF:
        """事件点 → 画布本地坐标（标尺是画布的兄弟控件，需跨控件换算）。

        走「全局坐标中转」而非 `mapTo`：实测 `mapTo(canvas, …)` 在画布父链被
        重排进 2×2 栅格之后返回错值并报
        `QWidget::mapTo(): parent must be in parent hierarchy`；全局中转
        （`mapToGlobal` → `mapFromGlobal`）与之等价且无层级假设。
        """
        try:
            pt = self.mapToGlobal(ev.position().toPoint())
            local = self._canvas.mapFromGlobal(pt)
            return QPointF(float(local.x()), float(local.y()))
        except Exception:
            return QPointF(ev.position())

    def _pos_from_event(self, ev) -> float:
        """事件 → 参考线落点世界值（垂直于标尺的那一轴）。"""
        pt = self._canvas_point(ev)
        return float(self._canvas._screen_to_world(pt).y() if self.HORIZONTAL
                     else self._canvas._screen_to_world(pt).x())

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.RightButton:
            self._show_menu(ev)
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        axis = self._guide_axis()
        world = self._pos_from_event(ev)
        hit = self._guides.nearest(axis, world, 4.0 / self._scale())
        self._drag_moved = False
        if hit is not None:
            # 命中已有线：拖动=改位置 → 先登记撤销
            self._snapshot()
            if hit.get("locked"):
                self._guides.set_locked(hit, False)   # 锁定线单击=解锁后再拖（可预期）
            self._drag_guide = hit
        else:
            # 新线**延到真正拖动时才创建**：单击不产生参考线，也不留空撤销条目
            self._drag_guide = None
            self._pending_axis = axis
        self._canvas.update()
        ev.accept()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        world = self._pos_from_event(ev)
        if self._drag_guide is None:
            if getattr(self, "_pending_axis", ""):
                self._snapshot()     # 首次拖动：此刻才落盘（撤销域取变更前状态）
                self._drag_guide = self._guides.add(self._pending_axis, world)
                self._pending_axis = ""
            else:
                self._hover_guide = self._guides.nearest(
                    self._guide_axis(), world, 4.0 / self._scale())
                return
        self._drag_moved = True
        self._guides.move(self._drag_guide, world)
        self._canvas.update()
        self.update()
        ev.accept()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        self._drag_guide = None
        self._pending_axis = ""
        self._canvas.update()
        self.update()
        ev.accept()

    def _show_menu(self, ev) -> None:
        from PyQt6.QtWidgets import QMenu
        axis = self._guide_axis()
        world = self._pos_from_event(ev)
        near = self._guides.nearest(axis, world, 6.0 / self._scale())
        menu = QMenu(self)
        if near is not None:
            lock_txt = "解锁参考线" if near.get("locked") else "锁定参考线"
            menu.addAction(lock_txt, lambda g=near: self._toggle_lock(g))
            menu.addAction("删除参考线", lambda g=near: self._delete(g))
            menu.addSeparator()
        menu.addAction("在此处新增参考线",
                       lambda: self._add_at(world))
        menu.addAction("清除全部参考线", self._clear_all)
        menu.exec(ev.globalPosition().toPoint())

    def _toggle_lock(self, guide: dict) -> None:
        self._snapshot()
        self._guides.set_locked(guide, not guide.get("locked"))
        self._canvas.update()
        self.update()

    def _delete(self, guide: dict) -> None:
        self._snapshot()
        self._guides.remove(guide)
        self._canvas.update()
        self.update()

    def _add_at(self, world: float) -> None:
        self._snapshot()
        self._guides.add(self._guide_axis(), world)
        self._canvas.update()
        self.update()

    def _clear_all(self) -> None:
        self._snapshot()
        self._guides.clear()
        self._canvas.update()
        self.update()


class TopRuler(_BaseRuler):
    """画布顶部标尺：横轴世界 x（拖出竖直参考线）。"""

    HORIZONTAL = True

    def __init__(self, canvas, guides: GuideSet, parent=None) -> None:
        super().__init__(canvas, guides, parent)
        from PyQt6.QtWidgets import QSizePolicy
        self.setFixedHeight(RULER_THICK)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class LeftRuler(_BaseRuler):
    """画布左侧标尺：纵轴世界 y（拖出水平参考线）。"""

    HORIZONTAL = False

    def __init__(self, canvas, guides: GuideSet, parent=None) -> None:
        super().__init__(canvas, guides, parent)
        from PyQt6.QtWidgets import QSizePolicy
        self.setFixedWidth(RULER_THICK)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)


# ── 参考线 / 吸附指示覆盖层 ───────────────────────────────────────────────

class GuideOverlay(QWidget):
    """透明覆盖层：画参考线 + 拖动中的吸附指示（不拦鼠标）。"""

    def __init__(self, canvas, guides: GuideSet, parent=None) -> None:
        super().__init__(parent if parent is not None else canvas)
        self._canvas = canvas
        self._guides = guides
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def paintEvent(self, ev) -> None:  # noqa: N802
        canvas = self._canvas
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_guides(p)
            self._paint_snap(p)
        finally:
            p.end()
        del canvas

    def _paint_guides(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        for guide in self._guides.items():
            color = guide_draw_color(guide)
            locked = bool(guide.get("locked"))
            pen = QPen(color, 1.6 if locked else 1.2,
                       Qt.PenStyle.SolidLine if locked else Qt.PenStyle.DashLine)
            p.setPen(pen)
            pos = self._world_to_screen(guide)
            if pos is None:
                continue
            if guide.get("axis") == "x":
                p.drawLine(QPointF(pos, 0.0), QPointF(pos, float(h)))
            else:
                p.drawLine(QPointF(0.0, pos), QPointF(float(w), pos))

    def _world_to_screen(self, guide: dict) -> Optional[float]:
        canvas = self._canvas
        try:
            if guide.get("axis") == "x":
                return float(canvas._world_to_screen(QPointF(float(guide["pos"]), 0.0)).x())
            return float(canvas._world_to_screen(QPointF(0.0, float(guide["pos"]))).y())
        except Exception:
            return None

    def _paint_snap(self, p: QPainter) -> None:
        """拖动中的吸附指示：命中的轴画一条警示色线（tier 文案走状态提示）。"""
        info = getattr(self._canvas, "_snap_indicator", None)
        if not info:
            return
        w, h = self.width(), self.height()
        color = QColor(_th.BRIGHT_WARN if info[0] != "grid" else _th.FOCUS_BLUE)
        p.setPen(QPen(color, 1.0, Qt.PenStyle.DotLine))
        x_line, y_line = info[1], info[2]
        if x_line is not None:
            p.drawLine(QPointF(x_line, 0.0), QPointF(x_line, float(h)))
        if y_line is not None:
            p.drawLine(QPointF(0.0, y_line), QPointF(float(w), y_line))
