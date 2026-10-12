# -*- coding: utf-8 -*-
"""TopView「轨迹选择」工具（track）：画一条轨道捕获沿途设备并按弧长编号。

R178/topview 从 topview_panel（五上帝类之一，方法数冻结）拆出的新逻辑模块：
面板/画布侧只做事件转发（收点/预览/收笔/取消/调宽），捕获、弧长排序、
预览绘制、选择写回全部在本模块内闭环。

交互（polyline 同风格）：
  - 左键按下收点；按住拖动沿途按最小间距持续采样（自由笔）；
  - 松开时已收 ≥2 点 → finish（捕获 + 弧长编号 + `set_route_order_selection`
    写回选择序——跑动顺序成为有效选择序——再把有序中心点挂浮条桥，
    「应用为跑动路线」直通 runner 面板；R180#12）；
  - 右键 / Esc 取消（同按键也会收起浮条报价）；绘制中滚轮或 +/- 键
    临时调捕获宽度；工具栏 QSpinBox 持久调、QCheckBox 开关首尾吸附。

捕获判定：设备 hitbox 中心（= `panel.fixture_pos`，`_fixture_rect` 为其 ±7 的
14×14 方块）到路径折线段的最近距离 ≤ 捕获宽度/2（宽度为屏幕像素，按
`canvas._view_scale` 换算世界单位）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QEvent, QObject, QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from . import qascade_theme as _th

DEFAULT_TRACK_WIDTH_PX = 24
MIN_TRACK_WIDTH_PX = 8
MAX_TRACK_WIDTH_PX = 120
# 拖动采样最小世界间距：再小只是堆点，捕获/排序结果不变。
DRAG_SAMPLE_MIN_STEP = 6.0
# 首尾点吸附半径（屏幕像素，按 _view_scale 换算世界单位）：端点该半径内
# 的最近设备中心会被并入轨迹（减少起笔误差；工具栏「首尾吸附」可关）。
ENDPOINT_SNAP_PX = 28


class TrackDrawState:
    """一次轨迹绘制会话：世界坐标点序列 + 捕获宽度（屏幕像素）。"""

    def __init__(self) -> None:
        self.points: List[Tuple[float, float]] = []
        self.width_px: int = DEFAULT_TRACK_WIDTH_PX
        self.snap_endpoints: bool = True

    def reset(self) -> None:
        self.points = []

    def is_drawing(self) -> bool:
        return bool(self.points)

    def try_append(self, x: float, y: float, min_step: float = DRAG_SAMPLE_MIN_STEP) -> bool:
        """收一点：与上一点距离 ≥ min_step 才追加（首点恒收）。返回是否追加。"""
        if not self.points:
            self.points.append((float(x), float(y)))
            return True
        lx, ly = self.points[-1]
        if math.hypot(float(x) - lx, float(y) - ly) >= min_step:
            self.points.append((float(x), float(y)))
            return True
        return False

    def nudge_width(self, delta_px: int) -> int:
        """滚轮/±键临时调宽：clamp 到 [MIN, MAX]，返回新宽度。"""
        self.width_px = int(max(MIN_TRACK_WIDTH_PX, min(MAX_TRACK_WIDTH_PX, self.width_px + delta_px)))
        return self.width_px

    def preview_points(self, cursor: Optional[object]) -> List[Tuple[float, float]]:
        """已收点 + 当前光标（QPointF 或 tuple），供预览带延伸到光标处。"""
        pts = list(self.points)
        if cursor is not None:
            if isinstance(cursor, QPointF):
                pts.append((cursor.x(), cursor.y()))
            else:
                pts.append((float(cursor[0]), float(cursor[1])))
        return pts


def draw_preview(painter: QPainter, points: List[Tuple[float, float]],
                 width_px: int, view_scale: float) -> None:
    """半透明高亮带 + 中心虚线。painter 需已处于世界坐标系（随画布变换）。"""
    if not points:
        return
    scale = max(0.005, float(view_scale or 1.0))
    band_w = max(1.0, float(width_px) / scale)   # 屏幕 width_px 像素宽的世界等值
    path = QPainterPath(QPointF(points[0][0], points[0][1]))
    for px, py in points[1:]:
        path.lineTo(QPointF(float(px), float(py)))
    # 高亮带：粗半透明圆帽笔画出捕获走廊（即"宽度覆盖"的可视化）
    band = QPen(QColor(90, 190, 255, 40))
    band.setWidthF(band_w)
    band.setCapStyle(Qt.PenCapStyle.RoundCap)
    band.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(band)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    # 中心线：屏幕恒定 2px 虚线（与 line/circle 预览同观感）
    center = QPen(QColor(_th.ACTIVE_ORANGE), 2.0 / scale, Qt.PenStyle.DashLine)
    center.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(center)
    painter.drawPath(path)


def _project_on_polyline(points: List[Tuple[float, float]],
                         pt: Tuple[float, float]) -> Tuple[float, float]:
    """点到折线的投影：返回 (弧长 s, 距离 d)。

    弧长 = 最近段起点累积弧长 + 段内参数 × 段长；多段等距时取距离最近者
    （与 `topview_panel._nearest_on_polyline` 同判据语义，独立实现避免循环 import）。
    """
    best_d = math.inf
    best_s = 0.0
    acc = 0.0
    px, py = float(pt[0]), float(pt[1])
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        vx, vy = bx - ax, by - ay
        seg2 = vx * vx + vy * vy
        if seg2 <= 1e-12:
            t = 0.0
            seg_len = 0.0
        else:
            seg_len = math.sqrt(seg2)
            t = ((px - ax) * vx + (py - ay) * vy) / seg2
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        d = math.hypot(px - (ax + t * vx), py - (ay + t * vy))
        if d < best_d:
            best_d = d
            best_s = acc + t * seg_len
        acc += seg_len
    return best_s, best_d


def _world_half_width(canvas: object, width_px: int) -> float:
    """捕获宽度（屏幕像素）→ 世界半宽（按画布当前缩放换算）。"""
    scale = max(0.005, float(getattr(canvas, "_view_scale", 1.0) or 1.0))
    return float(width_px) / scale / 2.0


def _fixture_centers(panel: object) -> Dict[str, Tuple[float, float]]:
    """当前页全部设备 hitbox 中心（= fixture_pos，_fixture_rect 的几何中心）。"""
    centers: Dict[str, Tuple[float, float]] = {}
    for fid in panel.fixture_ids():
        try:
            cx, cy = panel.fixture_pos(fid)
            centers[str(fid)] = (float(cx), float(cy))
        except Exception:
            continue
    return centers


def capture_along_path(panel: object, canvas: object,
                       points: List[Tuple[float, float]], width_px: int) -> List[str]:
    """捕获：设备中心到路径折线段最近距离 ≤ width/2（世界单位）的设备集合。"""
    if len(points) < 2:
        return []
    half = _world_half_width(canvas, width_px)
    hits: List[str] = []
    for fid, center in _fixture_centers(panel).items():
        _, dist = _project_on_polyline(points, center)
        if dist <= half:
            hits.append(fid)
    return hits


def number_by_arclength(points: List[Tuple[float, float]], ids: List[str],
                        centers: Dict[str, Tuple[float, float]]) -> List[str]:
    """编号：命中设备投影到路径弧长参数 s 排序（视觉起点 → 终点）。"""
    if len(points) < 2 or not ids:
        return []
    keyed = []
    for fid in ids:
        center = centers.get(str(fid))
        if center is None:
            continue
        arc_s, _ = _project_on_polyline(points, center)
        keyed.append((arc_s, str(fid)))
    keyed.sort(key=lambda item: item[0])
    return [fid for _, fid in keyed]


def _nearest_center(centers: Dict[str, Tuple[float, float]],
                    pt: Tuple[float, float], radius: float):
    """半径内的最近设备中心（无命中返回 None）。"""
    best = None
    best_d = float(radius)
    px, py = float(pt[0]), float(pt[1])
    for cx, cy in centers.values():
        d = math.hypot(cx - px, cy - py)
        if d <= best_d:
            best_d = d
            best = (cx, cy)
    return best


def snap_stroke_endpoints(points: List[Tuple[float, float]],
                          centers: Dict[str, Tuple[float, float]],
                          radius: float) -> List[Tuple[float, float]]:
    """首尾点吸附：端点 radius 内有设备中心 → 前/后追加该中心。

    追加而非替换：扩展后的折线是原折线的超集，捕获只增不减（既有捕获
    结果不会被吸附破坏）；吸附到的端点设备弧长≈0/末尾，天然排首/尾。
    """
    if len(points) < 2 or not centers or radius <= 0.0:
        return list(points)
    out = list(points)
    first = _nearest_center(centers, points[0], radius)
    if first is not None and math.hypot(first[0] - points[0][0],
                                        first[1] - points[0][1]) > 1e-6:
        out.insert(0, first)
    last = _nearest_center(centers, points[-1], radius)
    if last is not None and math.hypot(last[0] - points[-1][0],
                                       last[1] - points[-1][1]) > 1e-6:
        out.append(last)
    return out


def finish(panel: object, canvas: object) -> List[str]:
    """收笔：捕获 → 弧长编号 → `set_route_order_selection` 写回 → 浮条报价 → 清状态。

    R180#12 断链修复：选择写回从 `_apply_box_selection`（框选链语义，奇偶/间隔
    过滤会退回页面设备序丢轨迹序）改走 `set_route_order_selection`——顺序成为
    有效选择序（live_base=轨迹序、box 链复位、established=True），下游
    奇偶/间隔/设备组保存/调度台名全部继承"跑动顺序"。
    force_visible=False：捕获集本就全部在当前页可见，不重排页面设备序。
    `group_id` 透传当前激活设备组，避免画一次轨道把组上下文清掉。
    写回成功后把有序设备中心点序列挂给 `_route_bridge` 浮条 → 一键应用为跑动路线。
    """
    state: Optional[TrackDrawState] = getattr(canvas, "_track_state", None)
    points = list(state.points) if state is not None else []
    width_px = state.width_px if state is not None else DEFAULT_TRACK_WIDTH_PX
    snap_on = bool(getattr(state, "snap_endpoints", True))
    if state is not None:
        state.reset()
    if len(points) < 2:
        return []
    centers = _fixture_centers(panel)
    if snap_on:
        scale = max(0.005, float(getattr(canvas, "_view_scale", 1.0) or 1.0))
        points = snap_stroke_endpoints(points, centers, ENDPOINT_SNAP_PX / scale)
    hits = [fid for fid in capture_along_path(panel, canvas, points, width_px) if fid in centers]
    ordered = number_by_arclength(points, hits, centers)
    if ordered:
        group_id = str(getattr(panel, "_active_group_id", "") or "")
        panel.set_route_order_selection(ordered, group_id=group_id,
                                        force_visible=False)
        from .route_animator.topview_route_bridge import present_route_offer
        present_route_offer(panel, canvas, ordered, centers)
    return ordered


def set_endpoint_snap(panel: object, on: bool) -> None:
    """工具栏「首尾吸附」勾选 → 会话标志（画布可能尚未创建则忽略）。"""
    canvas = getattr(panel, "_canvas", None)
    state = getattr(canvas, "_track_state", None) if canvas is not None else None
    if state is not None:
        state.snap_endpoints = bool(on)


def cancel(canvas: object) -> None:
    """右键 / Esc 取消：丢弃半成品轨迹并重绘。"""
    state: Optional[TrackDrawState] = getattr(canvas, "_track_state", None)
    if state is not None and state.is_drawing():
        state.reset()
        canvas.update()


def nudge_and_sync(canvas: object, delta_px: int) -> int:
    """绘制中滚轮/±键调宽：同步会话状态、工具栏 QSpinBox 显示并重绘。"""
    state: Optional[TrackDrawState] = getattr(canvas, "_track_state", None)
    if state is None:
        return DEFAULT_TRACK_WIDTH_PX
    new_w = state.nudge_width(delta_px)
    spin = getattr(canvas.panel, "_track_width_spin", None)
    if spin is not None and int(spin.value()) != new_w:
        spin.blockSignals(True)
        spin.setValue(new_w)
        spin.blockSignals(False)
    canvas.update()
    return new_w


def set_width(panel: object, width_px: int) -> None:
    """工具栏 QSpinBox 值变化 → 写回画布会话宽度（画布可能尚未创建则忽略）。"""
    canvas = getattr(panel, "_canvas", None)
    state = getattr(canvas, "_track_state", None) if canvas is not None else None
    if state is not None:
        state.width_px = int(max(MIN_TRACK_WIDTH_PX, min(MAX_TRACK_WIDTH_PX, width_px)))


def sync_tool_ui(panel: object, tool: str) -> None:
    """工具切换：离开 track 清半成品轨迹；宽度 QSpinBox/吸附 QCheckBox 仅 track 可见。"""
    canvas = getattr(panel, "_canvas", None)
    if canvas is not None and tool != "track":
        cancel(canvas)
    spin = getattr(panel, "_track_width_spin", None)
    if spin is not None:
        spin.setVisible(tool == "track")
    snap_chk = getattr(panel, "_track_snap_check", None)
    if snap_chk is not None:
        snap_chk.setVisible(tool == "track")


# ── 事件过滤器接线（R178：上帝类零增长——responsive_tiers 先例）──────────
#
# 挂载方式：canvas.__init__ 调 install_track_tool(self)（面板源码仅改 2 行：
# 工具下拉 items 加 "track" + 这一行安装调用）。鼠标/键盘/滚轮在 tool=="track"
# 时被过滤器整段消费（画布自身的 mousePress/Move/Release 处理器零改动，基线
# 不动）；预览走透明 overlay 子控件（不拦鼠标）。

class _TrackOverlay(QWidget):
    """轨迹预览透明覆盖层（画布子控件；尺寸随画布 Resize 同步）。"""

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._canvas = canvas

    def paintEvent(self, ev) -> None:  # noqa: N802
        state = getattr(self._canvas, "_track_state", None)
        if state is None or not state.is_drawing():
            return
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # ★ R180 修复：draw_preview 契约要求世界坐标系——原先漏了
            #   _apply_world_transform，缩放/平移视图下预览带错位整屏。
            xf = getattr(self._canvas, "_apply_world_transform", None)
            if callable(xf):
                xf(p)
            draw_preview(p, list(state.points), state.width_px,
                         getattr(self._canvas, "_view_scale", 1.0))
        finally:
            p.end()


class TrackToolFilter(QObject):
    """tool=="track" 时消费画布鼠标/键盘/滚轮，其余原样放行。"""

    def __init__(self, canvas) -> None:
        super().__init__(canvas)
        self._canvas, self._overlay = canvas, _TrackOverlay(canvas)
        self._overlay.raise_()
        # R186-UP03①：清屏幂等标志（修前每事件无条件 update→自激千次/s 空转）。
        self._overlay_clean_pending = True

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        canvas = self._canvas
        panel = getattr(canvas, "panel", None)
        if ev.type() == QEvent.Type.Resize:
            self._overlay.setGeometry(canvas.rect())
            bridge = getattr(canvas, "_route_bridge", None)
            if bridge is not None:
                bridge.relayout()
            return False
        if getattr(panel, "current_tool", "") != "track":
            if self._overlay_clean_pending:
                self._overlay_clean_pending = False
                self._overlay.update()
            return False
        self._overlay_clean_pending = True
        return self._dispatch(canvas, panel, ev)

    def _dispatch(self, canvas, panel, ev) -> bool:
        """按事件类型分发（track 工具已激活；各 handler 拆出降 CC）。"""
        et = ev.type()
        if et == QEvent.Type.MouseButtonPress:
            return self._on_press(canvas, ev)
        if et == QEvent.Type.MouseMove:
            return self._on_move(canvas, ev)
        if et == QEvent.Type.MouseButtonRelease:
            return self._on_release(canvas, panel, ev)
        if et == QEvent.Type.Wheel:
            return self._on_wheel(canvas, ev)
        if et == QEvent.Type.KeyPress:
            return self._on_key(canvas, ev)
        return False

    def _on_press(self, canvas, ev) -> bool:
        if ev.button() == Qt.MouseButton.RightButton:
            cancel(canvas)
            return True
        if ev.button() == Qt.MouseButton.LeftButton:
            bridge = getattr(canvas, "_route_bridge", None)
            if bridge is not None:
                bridge.dismiss()   # 新笔画起笔 → 上一笔的浮条报价作废
            w = canvas._screen_to_world(ev.position())
            canvas._track_state.try_append(w.x(), w.y(), 0.0)
            canvas.setCursor(Qt.CursorShape.CrossCursor)
            self._overlay.update()
            return True
        return False

    def _on_move(self, canvas, ev) -> bool:
        if getattr(canvas, "_panning", False):
            return False   # 中键/Alt+左键平移中放行给画布（grid_build 同款守卫）
        w = canvas._screen_to_world(ev.position())
        if ev.buttons() & Qt.MouseButton.LeftButton:
            canvas._track_state.try_append(w.x(), w.y())
        self._overlay.update()
        return True

    def _on_release(self, canvas, panel, ev) -> bool:
        if ev.button() == Qt.MouseButton.LeftButton:
            finish(panel, canvas)
            self._overlay.update()
            return True
        return False

    def _on_wheel(self, canvas, ev) -> bool:
        delta = 1 if ev.angleDelta().y() > 0 else -1
        nudge_and_sync(canvas, delta)
        self._overlay.update()
        return True

    def _on_key(self, canvas, ev) -> bool:
        key = ev.key()
        if key == Qt.Key.Key_Escape:
            cancel(canvas)
            bridge = getattr(canvas, "_route_bridge", None)
            if bridge is not None:
                bridge.dismiss()   # Esc 取消不残留：连浮条报价一起收
            return True
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            nudge_and_sync(canvas, 1)
            return True
        if key == Qt.Key.Key_Minus:
            nudge_and_sync(canvas, -1)
            return True
        return False


def install_track_tool(canvas) -> None:
    """画布 __init__ 调一次：挂会话状态 + 过滤器 + 跑动报价桥 + 宽度/吸附控件。"""
    if getattr(canvas, "_track_installed", False):
        return
    canvas._track_installed = True
    canvas._track_state = TrackDrawState()
    filt = TrackToolFilter(canvas)
    canvas.installEventFilter(filt)
    canvas._track_filter = filt
    try:   # R180#12：桥接对象挂在画布上（main_window/测试经 canvas._route_bridge 接线）
        from .route_animator.topview_route_bridge import TopViewRouteBridge
        canvas._route_bridge = TopViewRouteBridge(canvas)
    except Exception:
        canvas._route_bridge = None
    panel = getattr(canvas, "panel", None)
    combo = getattr(panel, "_tool_combo", None)
    if combo is not None:
        _inject_width_spin(panel, combo)


def _inject_width_spin(panel, combo) -> None:
    """把捕获宽度 QSpinBox + 首尾吸附 QCheckBox 插到工具下拉旁（运行时注入）。"""
    from PyQt6.QtWidgets import QCheckBox, QSpinBox
    lay = combo.parentWidget().layout()
    if lay is None:
        return
    spin = QSpinBox()
    spin.setRange(MIN_TRACK_WIDTH_PX, MAX_TRACK_WIDTH_PX)
    spin.setValue(DEFAULT_TRACK_WIDTH_PX)
    spin.setToolTip("轨迹捕获宽度（像素）：绘制中滚轮或 +/- 可临时调整")
    idx = lay.indexOf(combo)
    lay.insertWidget(idx + 1, spin)
    spin.setVisible(False)
    spin.valueChanged.connect(lambda v, p=panel: set_width(p, v))
    panel._track_width_spin = spin
    snap_chk = QCheckBox("首尾吸附")
    snap_chk.setChecked(True)
    snap_chk.setToolTip("轨迹起/终点自动吸附最近设备中心（减少起笔误差，默认开）")
    lay.insertWidget(idx + 2, snap_chk)
    snap_chk.setVisible(False)
    snap_chk.toggled.connect(lambda on, p=panel: set_endpoint_snap(p, on))
    panel._track_snap_check = snap_chk
    # ★ R180 修复：中文化后 currentTextChanged 拿到的是中文标签而非内部键
    #   → sync_tool_ui 的 tool=="track" 恒假、宽度控件永不显示；改走
    #   currentIndexChanged + itemData（与 build_tool_combo 的 current_tool 同源）。
    combo.currentIndexChanged.connect(
        lambda i, p=panel, c=combo: sync_tool_ui(p, str(c.itemData(i) or "select")))


def build_tool_combo(panel):
    """TopView 工具下拉（R178 中文化：用户可见名与内部键分离；track=画轨道选设备）。"""
    from PyQt6.QtWidgets import QComboBox
    combo = QComboBox()
    for key, label in (("select", "选择"), ("line", "直线"), ("circle", "圆"),
                       ("ellipse", "椭圆"), ("polyline", "折线"),
                       ("track", "画轨道选设备")):
        combo.addItem(label, key)
    combo.setItemData(5, "画一条轨迹，吸附线经过的设备按先后顺序选中并编号",
                      Qt.ItemDataRole.ToolTipRole)
    combo.currentIndexChanged.connect(
        lambda idx, p=panel, c=combo: setattr(
            p, "current_tool", c.itemData(idx) or "select"))
    return combo
