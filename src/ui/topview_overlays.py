# -*- coding: utf-8 -*-
"""TopView 增强装配（R183/F2 · 注入式接线）。

`TopViewPanel` / `TopViewCanvas` 是冻结上帝类（方法数不增、行数不涨），故
标尺 / 参考线 / 吸附分级 / 小地图 四项全部在本模块闭环，只由面板源码里
**两次调用**接入（先例：`topview_track_draw.install_track_tool`）：

  1. `build_topview_ui_xy_ref_and_canvas` 末尾：`install_overlays(panel)`
     —— 画布已建好，注入标尺/覆盖层/小地图与工具条开关；
  2. 画布 `__init__` 的 resize/mouse 事件：由本模块安装的**事件过滤器**接管
     （画布自身 mouseMoveEvent / Resize 零改动）。

吸附接线（T6）：设备的实际平移由画布 `mouseMoveEvent` 直写
`panel.set_fixture_pos`，故吸附只能"事后修正"：过滤器在
`QEvent.Type.MouseMove` 的 **Post 阶段**补一次位移差 —— 与画布写入结果等价
（同一帧内二次写入同一坐标，最终值 = 吸附值），不需要改画布本体。
Alt 按下时 `_alt_down` 为真 → 整段跳过（任务书"Alt 临时关"）。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QWidget

from . import qascade_theme as _th
from .topview_minimap import MiniMap
from .topview_rulers import (GuideOverlay, GuideSet, LeftRuler, TopRuler)
from . import topview_snap as _snap

#: 吸附档位中文名（状态提示用）。
TIER_LABELS = {"guide": "参考线", "edge": "对象缘", "grid": "网格", "none": "关闭"}


def _canvas_of(panel):
    canvas = getattr(panel, "_canvas", None)
    return canvas


class _SnapEventFilter(QObject):
    """画布事件过滤器：拖动前改写拖动偏移量 → 画布写出即吸附位（无需二次写入）。"""

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self._alt_down = False
        self._last_tier = ""

    # ── 事件分发 ──
    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        et = ev.type()
        if et == QEvent.Type.Resize:
            _sync_overlay_geometry(self._canvas)
            return False
        if et == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Alt:
            self._alt_down = True
            return False
        if et == QEvent.Type.KeyRelease and ev.key() == Qt.Key.Key_Alt:
            self._alt_down = False
            return False
        if et == QEvent.Type.MouseMove:
            self._before_mouse_move(ev)
            return False
        if et == QEvent.Type.MouseButtonRelease:
            self._snap_indicator_clear()
            return False
        return False

    def _before_mouse_move(self, ev) -> None:
        """画布即将按光标写坐标 → 先把偏移量改到吸附位（写出即最终值）。"""
        canvas = self._canvas
        panel = getattr(canvas, "panel", None)
        if panel is None:
            return
        dragging = getattr(canvas, "_drag_fixture_ids", None) or getattr(canvas, "_drag_logical_id", "")
        if not dragging or getattr(canvas, "_panning", False):
            return
        if not getattr(canvas, "_snap_enabled", True):
            return
        mods = ev.modifiers() if hasattr(ev, "modifiers") else Qt.KeyboardModifier.NoModifier
        alt_off = self._alt_down or bool(mods & Qt.KeyboardModifier.AltModifier)
        apply_drag_snap(canvas, ev.position(), alt_off=alt_off)

    def _report_tier(self, tier: str, alt_off: bool) -> None:
        if tier == self._last_tier and not alt_off:
            return
        self._last_tier = tier
        panel = getattr(self._canvas, "panel", None)
        widget = getattr(panel, "_snap_status_label", None)
        if widget is None:
            return
        if alt_off:
            widget.setText("吸附: 已临时关闭 (Alt)")
        else:
            widget.setText(f"吸附: {TIER_LABELS.get(tier, tier)}")

    def _snap_indicator_clear(self) -> None:
        canvas = self._canvas
        if getattr(canvas, "_snap_indicator", None) is not None:
            canvas._snap_indicator = None
            overlay = getattr(canvas, "_guide_overlay", None)
            if overlay is not None:
                overlay.update()


def _drag_bounds(canvas, cx: float, cy: float, offsets: dict, ld_id: str):
    """本帧拖动包围盒（设备偏移量 + 包代表点偏移量；无拖动对象 → None）。"""
    bbox = _snap.compute_drag_bbox((cx, cy), offsets)
    if not ld_id:
        return bbox
    raw = getattr(canvas, "_drag_ld_offset", None)
    ld_off = (float(raw[0]), float(raw[1])) if raw else (0.0, 0.0)
    return _merge_box(bbox, _snap.compute_drag_bbox((cx, cy), {"__ld__": ld_off}))


def _drag_offsets(canvas) -> tuple:
    """本帧拖动上下文：`(offsets, ld_id)`（无拖动对象时两者皆空）。"""
    raw = getattr(canvas, "_drag_fixture_offsets", None)
    return (dict(raw) if raw else {},
            str(getattr(canvas, "_drag_logical_id", "") or ""))


def apply_drag_snap(canvas, cursor_screen_pos, *, alt_off: bool = False) -> str:
    """把本帧拖动偏移量改写为吸附位，返回命中的档位名。

    画布 `mouseMoveEvent` 的写入式是 `world(cursor) + offset`（绝对定位，不累积），
    故只需把 `offset` 平移 `(dx, dy)`，画布随后写出的坐标就是吸附位 —— 等价于
    "写出后再修正一次"，但避免了同一帧的二次写入与事件循环重入。
    纯几何、无 Qt 事件依赖，可直接被测试驱动。
    """
    panel = getattr(canvas, "panel", None)
    offsets, ld_id = _drag_offsets(canvas)
    if panel is None or (not offsets and not ld_id):
        return "none"
    cursor = canvas._screen_to_world(cursor_screen_pos)
    bbox = _drag_bounds(canvas, float(cursor.x()), float(cursor.y()), offsets, ld_id)
    if bbox is None:
        return "none"
    dx, dy, tier, line_x, line_y = _snap_for(canvas, panel, bbox, offsets, alt_off)
    filt = getattr(canvas, "_overlay_filter", None)
    if filt is not None:
        filt._report_tier(tier, alt_off)
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        _set_indicator(canvas, "")
        return tier
    _shift_offsets(canvas, offsets, dx, dy, ld_id)
    _set_indicator(canvas, tier, line_x, line_y)
    return tier


def _snap_for(canvas, panel, bbox, offsets: dict, alt_off: bool):
    """对 bbox 求吸附位移（候选源：参考线 ∪ 其他设备轴 ∪ 网格）。"""
    other_xs, other_ys = _snap.center_axes(_snap.fixture_centers(panel),
                                           set(offsets.keys()))
    guides: GuideSet = getattr(canvas, "_guides", None)
    scale = max(0.005, float(getattr(canvas, "_view_scale", 1.0) or 1.0))
    return _snap.snap_translation(
        bbox,
        guide_xs=guides.axis_values("x") if guides is not None else [],
        guide_ys=guides.axis_values("y") if guides is not None else [],
        other_xs=other_xs, other_ys=other_ys,
        grid_step=_grid_step(canvas), tol=_snap.SNAP_TOL_PX / scale, alt_off=alt_off)


def _shift_offsets(canvas, offsets: dict, dx: float, dy: float, ld_id: str) -> None:
    """把吸附位移累加进拖动偏移量（画布随后按 offset 写出的即吸附位）。"""
    for fid, off in list(offsets.items()):
        canvas._drag_fixture_offsets[fid] = (float(off[0]) + dx, float(off[1]) + dy)
    if ld_id:
        raw = getattr(canvas, "_drag_ld_offset", None)
        base = (float(raw[0]), float(raw[1])) if raw else (0.0, 0.0)
        canvas._drag_ld_offset = (base[0] + dx, base[1] + dy)


def _merge_box(a, b):
    """两个世界包围盒取并集（任一为 None 时返回另一个）。"""
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _set_indicator(canvas, tier: str, line_x=None, line_y=None) -> None:
    """吸附指示线：世界轴 → 屏幕坐标，挂到画布供覆盖层绘制。"""
    if tier and (line_x is not None or line_y is not None):
        try:
            sx = float(canvas._world_to_screen(QPointF(float(line_x), 0.0)).x()) \
                if line_x is not None else None
            sy = float(canvas._world_to_screen(QPointF(0.0, float(line_y))).y()) \
                if line_y is not None else None
        except Exception:
            sx = sy = None
        canvas._snap_indicator = (str(tier), sx, sy)
    else:
        canvas._snap_indicator = None
    overlay = getattr(canvas, "_guide_overlay", None)
    if overlay is not None:
        overlay.update()


def _grid_step(canvas) -> float:
    """网格档步长：与 `_draw_grid` 完全同式（缩放变化即跟随）。"""
    from .topview_rulers import grid_step_for
    return grid_step_for(getattr(canvas, "_view_scale", 1.0))


# ── 几何同步 ──────────────────────────────────────────────────────────────

def _sync_overlay_geometry(canvas) -> None:
    """画布 Resize：覆盖层铺满画布、小地图贴右下角（标尺由栅格布局管）。"""
    w, h = canvas.width(), canvas.height()
    overlay = getattr(canvas, "_guide_overlay", None)
    minimap = getattr(canvas, "_minimap", None)
    if overlay is not None:
        overlay.setGeometry(0, 0, max(0, w), max(0, h))
    if minimap is not None:
        from .topview_minimap import MINIMAP_MARGIN
        minimap.move(max(0, w - minimap.width() - MINIMAP_MARGIN),
                     max(0, h - minimap.height() - MINIMAP_MARGIN - 34))
        minimap.invalidate()
    for ruler in (getattr(canvas, "_top_ruler", None),
                  getattr(canvas, "_left_ruler", None)):
        if ruler is not None:
            ruler.update()




class _CornerBox(QWidget):
    """左上角标尺交汇处的空白填角（避免两条标尺互相压角）。"""

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        from .topview_rulers import RULER_THICK
        self.setFixedSize(RULER_THICK, RULER_THICK)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, ev) -> None:  # noqa: N802
        from PyQt6.QtGui import QColor, QPainter
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(_th.BG_DEEP))
        finally:
            p.end()
        ev.accept()


# ── 装配 ─────────────────────────────────────────────────────────────────

def install_overlays(panel) -> bool:
    """在画布建好后调用一次（`build_topview_ui_*` 尾部）。幂等。"""
    canvas = _canvas_of(panel)
    if canvas is None or getattr(canvas, "_overlays_installed", False):
        return False
    canvas._overlays_installed = True
    canvas._snap_enabled = True
    canvas._snap_indicator = None
    guides = GuideSet(lambda: _current_page(panel))
    canvas._guides = guides

    overlay = GuideOverlay(canvas, guides)
    canvas._guide_overlay = overlay
    canvas._overlay_corner = _CornerBox(canvas)
    canvas._top_ruler = TopRuler(canvas, guides)
    canvas._left_ruler = LeftRuler(canvas, guides)
    canvas._minimap = MiniMap(canvas)
    for w in (overlay, canvas._overlay_corner, canvas._top_ruler,
              canvas._left_ruler, canvas._minimap):
        w.raise_()
    _build_canvas_frame(panel, canvas)
    _sync_overlay_geometry(canvas)

    filt = _SnapEventFilter(canvas)
    canvas.installEventFilter(filt)
    canvas._overlay_filter = filt

    _inject_toolbar(panel)

    # 低频心跳：位图/视口变化时刷新小地图与指示线（隐藏时不做事）
    timer = QTimer(canvas)
    timer.setInterval(400)
    timer.timeout.connect(lambda: _tick_overlays(canvas))
    timer.start()
    canvas._overlay_timer = timer
    return True


def _build_canvas_frame(panel, canvas) -> None:
    """把「标尺 + 画布」装进 2×2 栅格（标尺不遮挡画布内容），替换原画布位置。

    只动布局、不改画布本体：原 `rv.addWidget(panel._canvas, 1)` 的伸缩因子由
    外层栅格承接（栅格 setStretch(1,1) 等价）。
    """
    from PyQt6.QtWidgets import QFrame, QGridLayout
    parent = canvas.parentWidget()
    lay = parent.layout() if parent is not None else None
    if lay is None:
        return
    frame = QFrame()
    frame.setObjectName("topviewCanvasFrame")
    grid = QGridLayout(frame)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(0)
    grid.addWidget(canvas._overlay_corner, 0, 0)
    grid.addWidget(canvas._top_ruler, 0, 1)
    grid.addWidget(canvas._left_ruler, 1, 0)
    try:
        lay.replaceWidget(canvas, frame)
    except Exception:
        return
    frame.setParent(parent)
    lay.addWidget(frame, 1)     # 原 canvas 的伸缩位
    grid.addWidget(canvas, 1, 1)
    grid.setRowStretch(1, 1)
    grid.setColumnStretch(1, 1)
    panel._canvas_frame = frame


def refresh_overlays(panel) -> None:
    """切页/载入后调用：小地图重算 bbox 并重贴。"""
    canvas = _canvas_of(panel)
    if canvas is None:
        return
    minimap = getattr(canvas, "_minimap", None)
    if minimap is not None:
        minimap.invalidate()
        minimap.update()
    overlay = getattr(canvas, "_guide_overlay", None)
    if overlay is not None:
        overlay.update()
    for ruler in (getattr(canvas, "_top_ruler", None), getattr(canvas, "_left_ruler", None)):
        if ruler is not None:
            ruler.update()


def _tick_overlays(canvas) -> None:
    """低频心跳：位图/视口变化时刷新（可见性由画布可见性门控，隐藏不做事）。"""
    if not canvas.isVisible():
        return
    minimap = getattr(canvas, "_minimap", None)
    if minimap is None:
        return
    minimap.update()
    overlay = getattr(canvas, "_guide_overlay", None)
    if overlay is not None and getattr(canvas, "_snap_indicator", None):
        overlay.update()


def _current_page(panel) -> Optional[dict]:
    try:
        return panel.current_page()
    except Exception:
        return None


def _inject_toolbar(panel) -> None:
    """画布下方注入一行「视图辅助」开关（吸附 / 小地图 / 参考线 / 导出 PNG）。

    落点是画布栅格（`_build_canvas_frame` 的 2×2 之外的第三行，跨两列）——
    画布重挂进栅格后 `canvas.parentWidget()` 已是 frame，故显式取 frame 的布局，
    避免插入目标漂移（也避免 QVBoxLayout 才有的 insertWidget 接口错用）。
    """
    canvas = _canvas_of(panel)
    frame = getattr(panel, "_canvas_frame", None)
    grid = frame.layout() if frame is not None else None
    if grid is None or getattr(panel, "_overlay_bar", None) is not None:
        return
    bar = QWidget()
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(4, 0, 4, 0)
    lay.setSpacing(8)

    snap_chk = QCheckBox("吸附")
    snap_chk.setChecked(True)
    snap_chk.setToolTip("拖动设备时吸附到参考线 → 其他设备轴线 → 网格（按住 Alt 临时关闭）")
    snap_chk.toggled.connect(lambda on, c=canvas: _set_snap_enabled(c, on))
    lay.addWidget(snap_chk)

    status = QLabel("吸附: 网格")
    status.setStyleSheet(f"color:{_th.TEXT_SECONDARY};font-size:11px;")
    lay.addWidget(status)
    panel._snap_status_label = status

    minimap_chk = QCheckBox("小地图")
    minimap_chk.setChecked(True)
    minimap_chk.setToolTip("右下角缩略图：拖动方框平移视图，双击适配整页")
    minimap_chk.toggled.connect(lambda on, c=canvas: _set_minimap_visible(c, on))
    lay.addWidget(minimap_chk)

    clear_btn = _mk_button("清参考线", lambda: _clear_guides(panel))
    clear_btn.setToolTip("删除当前页全部参考线")
    lay.addWidget(clear_btn)

    png_btn = _mk_button("导出 PNG", lambda: _png_slot(panel))
    png_btn.setToolTip("把当前画布另存为 PNG 图片（QPainter 抓图，零依赖）")
    lay.addWidget(png_btn)

    rulers_chk = QCheckBox("PNG 含标尺")
    rulers_chk.setChecked(True)
    rulers_chk.setToolTip("导出 PNG 时把顶部/左侧标尺一起拼进图片（关掉则只导画布本体）")
    rulers_chk.toggled.connect(lambda on, p=panel: setattr(p, "_png_include_rulers", bool(on)))
    lay.addWidget(rulers_chk)
    panel._png_include_rulers = True

    lay.addStretch()
    grid.addWidget(bar, 2, 0, 1, 2)
    panel._overlay_bar = bar


def _mk_button(text: str, slot):
    from PyQt6.QtWidgets import QPushButton
    btn = QPushButton(text)
    btn.setFixedHeight(22)
    btn.clicked.connect(lambda _checked=False: slot())
    return btn


def _set_snap_enabled(canvas, on: bool) -> None:
    canvas._snap_enabled = bool(on)
    panel = getattr(canvas, "panel", None)
    label = getattr(panel, "_snap_status_label", None)
    if label is not None:
        label.setText(f"吸附: {'网格' if on else '关闭'}")


def _set_minimap_visible(canvas, on: bool) -> None:
    minimap = getattr(canvas, "_minimap", None)
    if minimap is None:
        return
    minimap.setVisible(bool(on))
    if on:
        minimap.raise_()
        minimap.invalidate()
        minimap.update()


def _clear_guides(panel) -> None:
    canvas = _canvas_of(panel)
    guides = getattr(canvas, "_guides", None) if canvas is not None else None
    if guides is None:
        return
    removed = guides.clear()
    canvas.update()
    for ruler in (getattr(canvas, "_top_ruler", None), getattr(canvas, "_left_ruler", None)):
        if ruler is not None:
            ruler.update()
    overlay = getattr(canvas, "_guide_overlay", None)
    if overlay is not None:
        overlay.update()
    del removed




def _png_slot(panel) -> None:
    """导出 PNG 按钮回调：本体在 `topview_png.png_export_slot`（含标尺合成/裁剪）。"""
    from .topview_png import png_export_slot
    png_export_slot(panel)
