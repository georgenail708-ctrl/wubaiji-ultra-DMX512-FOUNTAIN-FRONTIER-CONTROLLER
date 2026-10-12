# -*- coding: utf-8 -*-
from __future__ import annotations

import math
from typing import Dict, List, Tuple, Optional

from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QGradient, QMouseEvent, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF, QWheelEvent,
)
from PyQt6.QtWidgets import QMenu, QSizePolicy, QWidget

from ..qascade_theme import (
    ACTIVE_ORANGE,
    CANVAS_CARD_BOTTOM as BG_BOX,
    CANVAS_BORDER as BG_BUTTON,
    BG_DEEP,
    CANVAS_CARD as BG_PANEL,
    CANVAS_BG as BG_ROOT,
    BRIGHT_ERROR,
    BRIGHT_SUCCESS,
    BRIGHT_WARN,
    CANVAS_BORDER as BORDER_NORMAL,
    CANVAS_SELECTION as FOCUS_BLUE,
    SCROLLBAR_HANDLE,
    CANVAS_MUTED as TEXT_DISABLED,
    CANVAS_INK as TEXT_PRIMARY,
    CANVAS_MUTED as TEXT_SECONDARY,
)
from ..hidpi import hidpi_pixmap
from .. import qascade_theme as theme
from ..canvas_materials import paint_bed, selection_fill, tone

__all__ = ["RouteCanvas"]

# 精致度常量(D波): 发丝线统一 1.25px 抗锯齿; 数字/时间码用等宽字体。
_HAIRLINE_W = 1.25
# D2-D1：卡身垂直渐变/边框笔单例（ObjectBoundingMode 随目标包围盒取向，
# 修前每台每次静态重建各建一对）。
_CARD_BODY_BRUSH = None
_BASE_BORDER_PEN = None


def _card_body_brush():
    global _CARD_BODY_BRUSH
    if _CARD_BODY_BRUSH is None:
        g = QLinearGradient(0.0, 0.0, 0.0, 1.0)
        g.setCoordinateMode(QGradient.CoordinateMode.ObjectBoundingMode)
        g.setColorAt(0.0, QColor(BG_PANEL))
        g.setColorAt(1.0, QColor(BG_BOX))
        _CARD_BODY_BRUSH = QBrush(g)
    return _CARD_BODY_BRUSH


def _base_border_pen():
    global _BASE_BORDER_PEN
    if _BASE_BORDER_PEN is None:
        _BASE_BORDER_PEN = QPen(QColor(BG_BUTTON), _HAIRLINE_W)
        _BASE_BORDER_PEN.setCosmetic(True)
    return _BASE_BORDER_PEN
_MONO_FAMILIES = ["Consolas", "DejaVu Sans Mono"]

# 火车物理功能开关：置 False 时隐藏全部火车物理 UI 入口（创建火车/火车关键帧标记），
# 但底层信号/handler/引擎/模型字段全部保留，功能代码完整、随时可重新启用。
# 同名开关另见 timeline.py，两处需保持一致。
_TRAIN_PHYSICS_UI = True

_CIRCLED = [
    "①", "②", "③", "④", "⑤", "⑥",
    "⑦", "⑧", "⑨", "⑩", "⑪", "⑫",
    "⑬", "⑭", "⑮", "⑯", "⑰", "⑱",
    "⑲", "⑳",
]


def _circle_num(n: int) -> str:
    return _CIRCLED[n - 1] if 1 <= n <= 20 else str(n)


def _catmull_rom_spline(
    points: List[Tuple[float, float]],
    num_segments: int = 20,
) -> List[Tuple[float, float]]:
    """Fit a Catmull-Rom spline through *points*. Returns smooth interpolated points."""
    if len(points) < 2:
        return list(points)
    pts = [points[0]] + points + [points[-1]]
    result: List[Tuple[float, float]] = []
    for i in range(len(pts) - 3):
        p0, p1, p2, p3 = pts[i], pts[i + 1], pts[i + 2], pts[i + 3]
        for t_idx in range(num_segments):
            t = t_idx / num_segments
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                (2 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                (2 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            result.append((x, y))
    result.append(pts[-2])
    return result


def _defer_refit(canvas) -> None:
    """R188-②：程序化 fit 记忆态下的延迟重拟合（防 Runner 设备截断）。

    Runner 打开顺序 = set_context/load_state（set_devices→zoom_to_fit）
    → show()；fit 在预布局尺寸（如 334x480）执行，show 后画布变 658x326
    且无任何重算 → 该轴设备被裁出视口（用户实报"当前页设备被截断"）。
    修法：zoom_to_fit 记录 `_fit_size`；show/resize 后下一拍若尺寸已变且
    视图仍是程序化 fit 态（用户未手调）→ 按新尺寸重拟合。用户滚轮/平移
    后 `_fit_size=None`（永不再自动动视图）。
    """
    from PyQt6.QtCore import QTimer

    def _run():
        try:
            if (canvas._fit_size is not None
                    and canvas._fit_size != (canvas.width(), canvas.height())
                    and canvas._dev_by_id):
                canvas.zoom_to_fit()
        except RuntimeError:
            pass   # C++ 对象已删（面板关闭）

    QTimer.singleShot(0, _run)


class RouteCanvas(QWidget):
    """TopView-style canvas for device layout with route animation overlay.

    Draws devices as circles on a dark grid and overlays route-order connections
    with Bezier curves, numbered badges, and a green/yellow/red color scheme.
    Supports trim handles, keyframe snapshots, device traces, and ghost preview.
    """
    selection_changed = pyqtSignal(list)
    route_order_changed = pyqtSignal(list)
    # Feature 1: Trim handle drag
    trim_handle_dragged = pyqtSignal(float, float)
    # Feature 2: Keyframe snapshot
    keyframe_snapshot = pyqtSignal(float, float, float, float, float, list)
    # Feature 6: Train drag interaction
    train_placed = pyqtSignal(str)  # device_id where train was placed
    track_drawn = pyqtSignal(object)  # List[Tuple[float, float]] - finalized track points
    car_path_drawn = pyqtSignal(object)  # List[Tuple[float, float]] - 车厢自由几何路径(不量化到设备)
    # Feature 2: Batch create trains
    trains_batch_created = pyqtSignal(object)  # List[str] device_ids
    # Frame stepping
    frame_step_requested = pyqtSignal(float)  # new_time

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 90)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._devices: List[Dict] = []
        self._dev_by_id: Dict[str, Dict] = {}
        self._ordered_ids: List[str] = []
        self._active_index: int = -1
        self._completed_count: int = 0
        self._view_scale: float = 1.0
        self._view_offset, self._fit_size = QPointF(0.0, 0.0), None  # R188-②
        self._panning: bool = False
        self._pan_start_screen: Optional[QPointF] = None
        self._pan_start_offset: Optional[QPointF] = None
        self._selected_ids: set = set()
        self._last_clicked_id: str = ""
        self._drag_start: Optional[QPointF] = None
        self._drag_pos: Optional[QPointF] = None
        self._drag_device_id: str = ""
        self._drag_reorder: bool = False
        self._drag_insert_index: int = -1
        self._hover_device_id: str = ""
        self._pulse_phase: float = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(50)
        self._pulse_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._pulse_timer.timeout.connect(self._advance_pulse)
        self._pulse_timer.start()
        self._static_cache: Optional[QPixmap] = None
        self._static_cache_key: Optional[tuple] = None
        self._static_cache_offset: Optional[QPointF] = None
        self._static_cache_margin: int = 200
        self._device_generation: int = 0
        self._ordered_index: Dict[str, int] = {}
        # 位置网格哈希索引: 近邻查询 O(1)(_device_at/_ins_idx), 设备集变化时重建
        self._spatial_grid: Dict[Tuple[int, int], List[str]] = {}
        self._spatial_cell: float = 1.0
        self._dev_seq: Dict[str, int] = {}

        # Feature 1: Trim handles
        self._trim_start: float = 0.0
        self._trim_end: float = 1.0
        self._drag_handle: str = ""  # "start", "end", or ""
        self._path_seg_paths: List[QPainterPath] = []
        self._path_seg_lengths: List[float] = []
        self._path_total_length: float = 0.0
        self._path_pts: List[QPointF] = []
        self._route_screen_pts: List[QPointF] = []  # 屏幕空间路线点(静态层重建时计算)

        # Feature 2: Keyframe snapshot
        self._active_cue_time: float = -1.0
        self._trim_offset: float = 0.0
        self._stagger_delay: float = 0.0
        self._snapshot_flash_pos: Optional[QPointF] = None
        self._snapshot_flash_alpha: float = 0.0
        self._snapshot_flash_timer = QTimer(self)
        self._snapshot_flash_timer.setInterval(50)
        self._snapshot_flash_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._snapshot_flash_timer.timeout.connect(self._fade_snapshot_flash)

        # Feature 3: Device trace
        self._trace_device_id: str = ""
        self._trace_data: List[Tuple[float, float, float, int]] = []

        # Feature 4: Ghost preview
        self._ghost_ordered_ids: List[str] = []
        self._ghost_time_until: float = -1.0

        # -- Train highlight effect -- #
        self._train_x: float = 0.0
        self._train_y: float = 0.0
        self._train_active_ids: set = set()
        self._train_width: int = 1
        self._train_color: str = ACTIVE_ORANGE
        # -- Spatial car bodies (空间化车厢可视化, 引擎返回位置) -- #
        self._train_car_positions: List[Tuple[float, float]] = []
        # -- Train group/chain structure -- #
        self._train_group_devices: List[List[str]] = []  # each inner list = one group's devices
        self._train_chain_length: int = 2
        # -- 波次3 统一可视化: 按 train_id 的多火车同帧绘制载荷(窗口每 tick 推送) -- #
        self._train_visuals: Dict[str, dict] = {}

        # -- Track path (visible rail line) -- #
        self._track_path_points: List[Tuple[float, float]] = []

        # -- Route number overlay -- #
        self._route_overlay_data: List[Tuple[str, float, float, int, str]] = []

        # -- Train drag interaction (Feature 6) -- #
        self._drawing_track: bool = False
        self._track_draw_points: List[Tuple[float, float]] = []
        # 车厢自由路径绘制模式：由「车厢+」工具开启。开启后在空白处按下即开始画车厢
        # 运行路径（自由几何、不吸附不量化到设备），松开发出 car_path_drawn。
        self._car_path_mode: bool = False
        self._train_device_id: str = ""
        self._quantum_preview_ids: set = set()
        self._spline_device_order: List[str] = []
        self._train_progress: float = 0.0

        # -- Smooth interpolation for train position -- #
        self._interp_train_pos: Tuple[float, float] = (0.0, 0.0)
        self._interp_train_target: Tuple[float, float] = (0.0, 0.0)
        self._interp_active_ids: set = set()
        self._interp_width: int = 1
        self._interp_color: str = ACTIVE_ORANGE
        self._interp_timer = QTimer(self)
        self._interp_timer.setInterval(50)
        self._interp_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._interp_timer.timeout.connect(self._interp_tick)
        self._interp_timer.start()

        # -- Snap-to-device (magnetic) -- #
        self._snap_enabled: bool = True
        self._snap_indicator_pos: Optional[QPointF] = None
        self._snap_radius: float = 15.0  # world units

        self._build_path_cache()

    # ------------------------------------------------------------------ #
    #  Visibility gating — UI-only repaint timers stop while hidden
    # ------------------------------------------------------------------ #

    def showEvent(self, ev):  # noqa: N802
        super().showEvent(ev)
        for t in (self._pulse_timer, self._interp_timer):
            if not t.isActive():
                t.start()
        _defer_refit(self)   # R188-②：show 前的 fit 在预布局尺寸执行 → 补一次

    def hideEvent(self, ev):  # noqa: N802
        self._pulse_timer.stop()
        self._interp_timer.stop()
        super().hideEvent(ev)

    # ------------------------------------------------------------------ #
    #  Smooth interpolation
    # ------------------------------------------------------------------ #

    def _mirror_legacy_visual(self) -> None:
        """把 `__legacy__` 条目镜像到旧单实例字段（组预览/旧调用方读取）。

        ★ 2026-09-20：从 `_interp_tick` 抽出（该函数受门禁 CC≤8 约束，内联
          这段会因多处 `or` 兜底把 CC 顶上去）。缺字段时宽容跳过。
        """
        legacy = self._train_visuals.get("__legacy__")
        if legacy is None:
            return
        head = legacy.get("head_pos")
        if head is None:
            return
        self._train_x, self._train_y = head
        self._train_active_ids = set(legacy.get("covered_ids") or [])
        self._train_width = int(legacy.get("width") or 1)
        self._train_color = legacy.get("color") or ACTIVE_ORANGE
        self._interp_train_pos = head

    def _interp_tick(self) -> None:
        """按 train_id 平滑插值各火车车头位置(波次3 多火车同帧绘制)。

        ★★ 2026-09-20 修复（用户实报 `KeyError: 'head_pos'`，画布不再重绘）：
          本方法由基类提供，而子类 `_GameCanvas` **覆写了 `set_train_visuals`**
          用另一套「车厢载荷」（car_positions/car_angles/radius/car_length，
          自带 `_smooth_cars` 平滑，**没有车头字段**）。基类这里硬下标
          `v["head_pos"]`/`v["head_target"]` → 子类载荷一进表，本 50ms 定时器
          每帧抛 KeyError，回调整个中断（跑动列车/轨道画不出来）。
          修：缺车头字段的条目**跳过插值**（其位置由所属实现自己平滑），
          两套契约共存；`__legacy__` 镜像同步改宽容取值。
        """
        changed = False
        for _tid, v in self._train_visuals.items():
            if not {"head_pos", "head_target"} <= v.keys():
                continue          # 车厢载荷（_GameCanvas）：无车头，不插值
            cx, cy = v["head_pos"]
            tx, ty = v["head_target"]
            nx = cx + (tx - cx) * 0.3
            ny = cy + (ty - cy) * 0.3
            if max(abs(nx - cx), abs(ny - cy)) > 0.01:
                v["head_pos"] = (nx, ny)
                changed = True
        self._mirror_legacy_visual()
        if changed:
            self.update()

    def set_train_visuals(self, visuals: List[dict]) -> None:
        """波次3 统一可视化入口: 窗口每 tick 推送全部火车的引擎碰撞后载荷。

        visuals: [{train_id, mode("spatial"|"quantum"), color, car_positions,
                   covered_ids, head_target, width, stalled, coupled_to,
                   chain_compression}, ...]
        按 train_id 重建 _train_visuals(同 id 保留旧插值头位置保证平滑,
        消失的 id 移除)。多火车同帧全部绘制(W2 遗留 1)。
        """
        new_visuals: Dict[str, dict] = {}
        for v in visuals or []:
            tid = str(v.get("train_id") or "")
            if not tid:
                continue
            prev = self._train_visuals.get(tid)
            head_target = tuple(v.get("head_target") or (0.0, 0.0))
            new_visuals[tid] = {
                "mode": str(v.get("mode") or "quantum"),
                "color": str(v.get("color") or ACTIVE_ORANGE),
                "car_positions": [tuple(p) for p in (v.get("car_positions") or [])],
                "covered_ids": set(v.get("covered_ids") or []),
                "head_target": head_target,
                "head_pos": prev["head_pos"] if prev else head_target,
                "width": max(1, int(v.get("width") or 1)),
                "stalled": bool(v.get("stalled")),
                "coupled_to": str(v.get("coupled_to") or ""),
                "chain_compression": int(v.get("chain_compression") or 0),
            }
        if new_visuals == self._train_visuals:
            return
        self._train_visuals = new_visuals
        self.update()

    def _set_legacy_visual(self, entry: dict) -> None:
        """旧单实例 API 兼容: 把单火车状态写入 __legacy__ 键统一绘制。"""
        prev = self._train_visuals.get("__legacy__")
        entry["head_pos"] = prev["head_pos"] if prev else entry["head_target"]
        self._train_visuals["__legacy__"] = entry
        self.update()

    def set_train_device_interpolated(
        self, device_id: str, active_device_ids: List[str],
        width: int, train_color: str = ACTIVE_ORANGE,
    ) -> None:
        """Set the train target position — smoothly lerp toward it each tick.

        波次3: 兼容旧调用, 路由进 __legacy__ 键的统一可视化(窗口已改用
        set_train_visuals 推送引擎载荷)。
        """
        dev = self._dev_by_id.get(device_id)
        if dev:
            self._interp_train_target = (
                float(dev.get("x", 0.0)), float(dev.get("y", 0.0)),
            )
        if not self._interp_active_ids:
            # First frame: snap instantly
            self._interp_train_pos = self._interp_train_target
            self._train_x, self._train_y = self._interp_train_target
        self._interp_active_ids = set(active_device_ids) if active_device_ids else set()
        self._interp_width = max(1, width)
        self._interp_color = train_color
        self._set_legacy_visual({
            "mode": "quantum",
            "color": train_color,
            "car_positions": [],
            "covered_ids": set(active_device_ids) if active_device_ids else set(),
            "head_target": self._interp_train_target,
            "width": max(1, width),
            "stalled": False,
            "coupled_to": "",
            "chain_compression": 0,
        })

    def set_train_car_positions(
        self, car_positions: List[Tuple[float, float]],
        covered_ids: List[str], width: int, train_color: str = ACTIVE_ORANGE,
    ) -> None:
        """空间化车厢可视化：按引擎返回的车厢位置重绘车厢体（不新增物理）。

        传入空列表仅清空车厢体；非空时同时复用平滑插值机制让车头标记与
        覆盖设备高亮跟随。波次3: 路由进 __legacy__ 键的统一可视化。
        """
        self._train_car_positions = [tuple(p) for p in (car_positions or [])]
        if not self._train_car_positions:
            self.update()
            return
        self._interp_train_target = self._train_car_positions[0]
        if not self._interp_active_ids:
            # 首次: 立即定位, 不等插值
            self._interp_train_pos = self._interp_train_target
            self._train_x, self._train_y = self._interp_train_target
        self._interp_active_ids = set(covered_ids) if covered_ids else set()
        self._interp_width = max(1, width)
        self._interp_color = train_color
        self._set_legacy_visual({
            "mode": "spatial",
            "color": train_color,
            "car_positions": list(self._train_car_positions),
            "covered_ids": set(covered_ids) if covered_ids else set(),
            "head_target": self._interp_train_target,
            "width": max(1, width),
            "stalled": False,
            "coupled_to": "",
            "chain_compression": 0,
        })

    # ------------------------------------------------------------------ #
    #  Snap-to-device helpers
    # ------------------------------------------------------------------ #

    def _snap_to_nearest_device(self, wp: QPointF) -> QPointF:
        """Snap *wp* to nearest device if within snap_radius. Returns snapped or original."""
        if not self._snap_enabled or not self._dev_by_id:
            self._snap_indicator_pos = None
            return wp
        nearest: Optional[str] = None
        nearest_dist = float("inf")
        for fid, dev in self._dev_by_id.items():
            dx = wp.x() - float(dev.get("x", 0.0))
            dy = wp.y() - float(dev.get("y", 0.0))
            dist = math.hypot(dx, dy)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = fid
        if nearest is not None and nearest_dist <= self._snap_radius:
            dev = self._dev_by_id[nearest]
            self._snap_indicator_pos = QPointF(
                float(dev.get("x", 0.0)), float(dev.get("y", 0.0)),
            )
            return QPointF(
                float(dev.get("x", 0.0)), float(dev.get("y", 0.0)),
            )
        self._snap_indicator_pos = None
        return wp

    # ------------------------------------------------------------------ #
    #  Zoom-to-fit
    # ------------------------------------------------------------------ #

    def zoom_to_fit(self) -> None:
        """Zoom and pan to fit all devices in the view with 50px margin."""
        if not self._dev_by_id:
            return
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for dev in self._dev_by_id.values():
            x = float(dev.get("x", 0.0))
            y = float(dev.get("y", 0.0))
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
        dx = max_x - min_x
        dy = max_y - min_y
        # 单轴退化时只补该轴，避免把长水平/垂直布局错误地缩放到 200 世界单位。
        if dx < 1.0:
            dx = 200.0
            min_x -= 100.0
            max_x += 100.0
        if dy < 1.0:
            dy = 200.0
            min_y -= 100.0
            max_y += 100.0
        margin = 50.0
        sx = (self.width() - 2 * margin) / dx
        sy = (self.height() - 2 * margin) / dy
        self._view_scale = max(0.005, min(8.0, min(sx, sy)))
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0
        self._view_offset = QPointF(
            self.width() / 2.0 - cx * self._view_scale,
            self.height() / 2.0 - cy * self._view_scale,
        )
        self._fit_size = (self.width(), self.height())   # R188-②
        self._static_cache = None
        self.update()

    # ------------------------------------------------------------------ #
    #  Public setters / data methods
    # ------------------------------------------------------------------ #

    def set_devices(self, devices: List[Dict]):
        self._devices = list(devices)
        self._dev_by_id = {str(d.get("id", "")): d for d in self._devices if d.get("id")}
        self._device_generation += 1
        self._build_spatial_grid()
        valid = set(self._dev_by_id.keys())
        self._selected_ids = {f for f in self._selected_ids if f in valid}
        if self._last_clicked_id not in valid:
            self._last_clicked_id = ""
        self._static_cache = None
        self._build_path_cache()
        self.update()

    def set_device_positions(
        self, pos_dict: Dict[str, Tuple[float, float]]
    ) -> None:
        """Accept device positions from topview when _devices not pre-populated."""
        if self._dev_by_id:
            # Update existing positions
            for did, (x, y) in pos_dict.items():
                dev = self._dev_by_id.get(str(did))
                if dev is not None:
                    dev["x"] = x
                    dev["y"] = y
        else:
            self._devices = [
                {"id": str(k), "x": float(v[0]), "y": float(v[1])}
                for k, v in pos_dict.items() if isinstance(v, (tuple, list)) and len(v) >= 2
            ]
            self._dev_by_id = {
                str(d.get("id", "")): d for d in self._devices if d.get("id")
            }
        self._device_generation += 1
        self._build_spatial_grid()
        self._static_cache = None
        self.update()

    def set_route_order(self, ordered_ids: List[str]):
        # 保留完整顺序（含当前页不可见的跨页设备）：过滤只影响渲染/交互可见性，
        # 状态设备集合绝不被过滤操作删除。渲染/命中处均按 _dev_by_id 过滤，
        # 因此回写信号（route_order_changed / keyframe_snapshot）携带全量 id。
        self._ordered_ids = [str(fid) for fid in (ordered_ids or [])]
        self._active_index = max(-1, min(self._active_index, len(self._ordered_ids) - 1))
        self._completed_count = max(0, min(self._completed_count, len(self._ordered_ids)))
        self._static_cache = None
        self._build_path_cache()
        self.update()

    def set_active_index(self, index: int):
        new_index = max(-1, min(int(index) if index is not None else -1,
                                len(self._ordered_ids) - 1))
        if new_index == self._active_index:
            return
        self._active_index = new_index
        self.update()

    def set_completed_count(self, count: int):
        new_count = max(0, min(int(count) if count is not None else 0,
                               len(self._ordered_ids)))
        if new_count == self._completed_count:
            return
        self._completed_count = new_count
        self.update()

    @property
    def route_order(self) -> List[str]:
        return list(self._ordered_ids)

    @property
    def devices(self) -> List[Dict]:
        return list(self._devices)

    @property
    def selected_ids(self) -> List[str]:
        return list(self._selected_ids)

    # -- Trim (Feature 1) -- #

    def set_trim(self, trim_start: float, trim_end: float):
        """Update trim boundary values (0.0-1.0)."""
        self._trim_start = max(0.0, min(1.0, trim_start))
        self._trim_end = max(0.0, min(1.0, trim_end))
        if self._trim_end < self._trim_start:
            self._trim_end = self._trim_start
        self.update()

    # -- Cue state (Feature 2) -- #

    def set_cue_state(self, time: float, trim_start: float, trim_end: float,
                      trim_offset: float, stagger_delay_ms: float):
        """Provide current cue playback state for keyframe snapshotting."""
        self._active_cue_time = time
        self._trim_start = max(0.0, min(1.0, trim_start))
        self._trim_end = max(0.0, min(1.0, trim_end))
        if self._trim_end < self._trim_start:
            self._trim_end = self._trim_start
        self._trim_offset = trim_offset
        self._stagger_delay = stagger_delay_ms
        self.update()

    # -- Car free-path draw mode (spatial car) -- #

    def set_car_path_mode(self, enabled: bool) -> None:
        """开/关车厢自由路径绘制模式。开启时光标变十字，空白处按下即画路径。"""
        self._car_path_mode = bool(enabled)
        # 退出时清理未完成的绘制
        if not self._car_path_mode and self._drawing_track:
            self._drawing_track = False
            self._track_draw_points = []
        self.setCursor(Qt.CursorShape.CrossCursor if self._car_path_mode
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    # -- Device trace (Feature 3) -- #

    def show_device_trace(self, device_id: str,
                          trace_data: List[Tuple[float, float, float, int]]):
        """Display a dashed trajectory for *device_id* across keyframes.

        ``trace_data`` = [(time_sec, x, y, rank_position), ...]
        """
        self._trace_device_id = device_id
        self._trace_data = list(trace_data) if trace_data else []
        self.update()

    def clear_device_trace(self):
        self._trace_device_id = ""
        self._trace_data = []
        self.update()

    # -- Ghost preview (Feature 4) -- #

    def show_ghost_preview(self, next_cue_ordered_ids: List[str], time_until: float):
        """Show a faint overlay of the next CUE's route path.

        *time_until* = seconds before the next CUE activates; opacity ramps
        from 0 % at 2 s to 30 % at 0.5 s.
        """
        self._ghost_ordered_ids = list(next_cue_ordered_ids) if next_cue_ordered_ids else []
        self._ghost_time_until = time_until
        self.update()

    # -- Train state (Feature 5) -- #

    def set_train_state(self, train_pos: Tuple[float, float],
                        active_device_ids: List[str],
                        width: int, train_color: str = ACTIVE_ORANGE):
        """Set the train's current position and active device IDs for visual feedback.

        Train is quantized to exactly one device tile — no "between" positions.
        Active devices glow brightest at the train position, fading outward.
        """
        self._train_x, self._train_y = train_pos
        self._train_active_ids = set(active_device_ids)
        self._train_width = max(1, width)
        self._train_color = train_color
        self.update()

    def set_train_device(self, device_id: str, active_device_ids: List[str],
                         width: int, train_color: str = ACTIVE_ORANGE):
        """Set the train on a specific device tile (quantized position).
        Called by the window each tick to reflect engine state.
        """
        dev = self._dev_by_id.get(device_id)
        if dev:
            self._train_x = float(dev.get("x", 0.0))
            self._train_y = float(dev.get("y", 0.0))
        self._train_device_id = device_id
        self._train_active_ids = set(active_device_ids) if active_device_ids else set()
        self._train_width = max(1, width)
        self._train_color = train_color
        self.update()

    def set_track_path(self, path_points: List[Tuple[float, float]]):
        """Set the visible track rail line waypoints (world coordinates)."""
        self._track_path_points = list(path_points) if path_points else []
        self._static_cache = None
        self.update()

    def set_train_progress(self, progress: float) -> None:
        """Set train progress (0-1) along the track for animated path rendering."""
        self._train_progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def cancel_track_drawing(self) -> None:
        """Cancel in-progress track drawing (called from Escape key shortcut)."""
        if self._drawing_track:
            self._drawing_track = False
            self._track_draw_points = []
            self._quantum_preview_ids.clear()
            self._drag_device_id = ""
            self._drag_start = None
            self._drag_pos = None
            self._drag_reorder = False
            self._drag_insert_index = -1
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    def set_train_groups(self, group_device_lists: List[List[str]],
                          chain_length: int = 2):
        """Set the group/chain structure for group-aware canvas drawing.

        Parameters
        ----------
        group_device_lists :
            Each inner list is the device IDs belonging to one group.
        chain_length :
            Number of devices in each coupling chain between groups.
        """
        self._train_group_devices = list(group_device_lists) if group_device_lists else []
        self._train_chain_length = max(0, chain_length)
        self.update()

    # -- Route number overlay -- #

    def set_route_overlay(self, overlay_data: List[Tuple[str, float, float, int, str]]):
        """Set route number overlay data: (fid, x, y, rank, color) per device."""
        self._route_overlay_data = list(overlay_data) if overlay_data else []
        self.update()

    # ------------------------------------------------------------------ #
    #  View helpers (unchanged)
    # ------------------------------------------------------------------ #

    def reset_view(self):
        self._fit_size = None   # R188-②：显式重置 → 退出自动重拟合
        self._view_scale = 1.0
        self._view_offset = QPointF(0.0, 0.0)
        self._static_cache = None
        self.update()

    def _s2w(self, pos: QPointF) -> QPointF:
        s = max(0.005, float(self._view_scale or 1.0))
        return QPointF((pos.x() - self._view_offset.x()) / s,
                       (pos.y() - self._view_offset.y()) / s)

    def _zoom_at(self, sp: QPointF, factor: float):
        self._fit_size = None   # R188-②：用户手缩放 → 退出自动重拟合
        before = self._s2w(sp)
        self._view_scale = max(0.005, min(8.00, self._view_scale * factor))
        self._view_offset = QPointF(sp.x() - before.x() * self._view_scale,
                                    sp.y() - before.y() * self._view_scale)
        self._static_cache = None
        self.update()

    def _vr(self) -> QRectF:
        tl = self._s2w(QPointF(0, 0))
        br = self._s2w(QPointF(float(self.width()), float(self.height())))
        return QRectF(tl, br).normalized()

    # -- Screen-constant sizing --------------------------------------- #
    # Device tiles and decorations are drawn under p.scale(view_scale), so a
    # fixed world size shrinks to nothing when zoomed out. These helpers keep
    # on-screen sizes roughly constant (with gentle scaling) so devices stay
    # visible and legible at ANY zoom level.
    _TILE_SCREEN_PX: float = 32.0   # natural on-screen tile size (at scale≈1)
    _TILE_MIN_PX: float = 16.0      # never smaller than this on screen
    _TILE_MAX_PX: float = 56.0      # never larger than this on screen

    def _px(self, screen_px: float) -> float:
        """Convert a screen-pixel length to world units at the current zoom.

        Use inside the scaled painter so decorations keep a constant on-screen
        size regardless of ``_view_scale``.
        """
        return float(screen_px) / max(0.005, float(self._view_scale or 1.0))

    def _lod(self) -> str:
        """Detail level for chrome/text (NOT tile visibility — tiles are always
        drawn at a visible screen size).

        ``minimal`` (dots) only kicks in when the layout would be genuinely too
        dense for cards, keyed off the *raw* (unclamped) on-screen size so that
        clamping to the floor never forces us into dot mode at mid zoom.
        """
        raw = self._TILE_SCREEN_PX * max(0.005, float(self._view_scale or 1.0))
        if raw < 11.0:
            return "minimal"
        px = self._tile_screen_px()
        return "low" if px < 20.0 else "medium" if px < 34.0 else "high"

    def _dwp(self, dev: Dict) -> QPointF:
        return QPointF(float(dev.get("x", 0.0)), float(dev.get("y", 0.0)))

    def _dname(self, dev: Dict) -> str:
        return str(dev.get("name", dev.get("id", "?")[-4:]))

    def _tile_screen_px(self) -> float:
        """Current tile size in *screen* pixels (clamped, gently zoom-aware)."""
        raw = self._TILE_SCREEN_PX * max(0.005, float(self._view_scale or 1.0))
        return max(self._TILE_MIN_PX, min(self._TILE_MAX_PX, raw))

    def _tile_size_for_lod(self, lod: str) -> float:
        """Return tile size in WORLD units so it renders at a constant on-screen
        size. ``lod`` is accepted for backward-compat but no longer changes size."""
        return self._px(self._tile_screen_px())

    def _build_spatial_grid(self) -> None:
        """重建位置网格哈希索引: 把设备按世界坐标分桶, 近邻查询 O(1)。

        桶边长取包围盒 / 64(约 64×64 桶), 平均每桶 ~3 台设备; 设备集或
        位置变化时(set_devices/set_device_positions)重建。
        """
        if not self._dev_by_id:
            self._spatial_grid = {}
            self._spatial_cell = 1.0
            self._dev_seq = {}
            return
        xs = [float(d.get("x", 0.0)) for d in self._dev_by_id.values()]
        ys = [float(d.get("y", 0.0)) for d in self._dev_by_id.values()]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        cell = span / 64.0
        self._spatial_cell = cell
        grid: Dict[Tuple[int, int], List[str]] = {}
        seq: Dict[str, int] = {}
        for i, (fid, d) in enumerate(self._dev_by_id.items()):
            seq[fid] = i
            k = (int(float(d.get("x", 0.0)) / cell), int(float(d.get("y", 0.0)) / cell))
            grid.setdefault(k, []).append(fid)
        self._spatial_grid = grid
        self._dev_seq = seq

    def _device_at(self, wp: QPointF) -> Optional[str]:
        tol = self._px(self._tile_screen_px() * 0.5 + 3.0)
        if self._spatial_grid:
            cell = self._spatial_cell
            cx0 = int((wp.x() - tol) / cell)
            cx1 = int((wp.x() + tol) / cell)
            cy0 = int((wp.y() - tol) / cell)
            cy1 = int((wp.y() + tol) / cell)
            best: Optional[str] = None
            best_seq = -1
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    for fid in self._spatial_grid.get((cx, cy), ()):
                        d = self._dwp(self._dev_by_id[fid])
                        if abs(wp.x() - d.x()) <= tol and abs(wp.y() - d.y()) <= tol:
                            s = self._dev_seq.get(fid, -1)
                            if s > best_seq:
                                best_seq = s
                                best = fid
            return best
        for fid in reversed(list(self._dev_by_id.keys())):
            d = self._dwp(self._dev_by_id[fid])
            if abs(wp.x() - d.x()) <= tol and abs(wp.y() - d.y()) <= tol:
                return fid
        return None

    def _badge_offset(self) -> float:
        """World-space vertical offset of the route badge above a tile."""
        return self._px(self._tile_screen_px() * 0.5 + 11.0)

    def _visible_device_ids(self, vr: QRectF, margin: float) -> List[str]:
        """Return device ids inside the world viewport, using the spatial grid."""
        if not self._spatial_grid:
            out = []
            for dev in self._devices:
                if vr.adjusted(-margin, -margin, margin, margin).contains(self._dwp(dev)):
                    fid = str(dev.get("id", ""))
                    if fid:
                        out.append(fid)
            return out
        cell = self._spatial_cell
        x0 = int(math.floor((vr.left() - margin) / cell))
        x1 = int(math.floor((vr.right() + margin) / cell))
        y0 = int(math.floor((vr.top() - margin) / cell))
        y1 = int(math.floor((vr.bottom() + margin) / cell))
        out: List[str] = []
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                for fid in self._spatial_grid.get((cx, cy), ()):
                    dev = self._dev_by_id.get(fid)
                    if dev is None:
                        continue
                    if vr.adjusted(-margin, -margin, margin, margin).contains(self._dwp(dev)):
                        out.append(fid)
        if len(out) > 1:
            out.sort(key=lambda fid: self._dev_seq.get(fid, 0))
        return out

    def _badge_hit(self, dev: Dict, wp: QPointF) -> bool:
        fid = str(dev.get("id", ""))
        if fid not in self._ordered_ids:
            return False
        d = self._dwp(dev)
        r = self._px(11.0)
        by = d.y() - self._badge_offset()
        return abs(wp.x() - d.x()) <= r and abs(wp.y() - by) <= r

    def _advance_pulse(self):
        self._pulse_phase = (self._pulse_phase + 0.15) % 100.0
        if self._active_index >= 0:
            self.update()

    def _pulse(self) -> float:
        return 0.5 + 0.5 * math.sin(self._pulse_phase * math.tau / 20.0)

    # ------------------------------------------------------------------ #
    #  Path cache (for trim handles & ghost)
    # ------------------------------------------------------------------ #

    def _build_path_cache(self):
        """Rebuild cached segment list from the current *ordered_ids*."""
        self._ordered_index = {fid: i for i, fid in enumerate(self._ordered_ids)}
        self._path_seg_paths.clear()
        self._path_seg_lengths.clear()
        self._path_total_length = 0.0
        self._path_pts = [self._dwp(self._dev_by_id[fid])
                          for fid in self._ordered_ids
                          if self._dev_by_id.get(fid) is not None]
        pts = self._path_pts
        if len(pts) < 2:
            return
        for i in range(len(pts) - 1):
            seg = self._seg_bezier(pts[i], pts[i + 1])
            self._path_seg_paths.append(seg)
            seg_len = seg.length()
            if seg_len <= 0.0:
                seg_len = 1.0
            self._path_seg_lengths.append(seg_len)
            self._path_total_length += seg_len
        if self._path_total_length <= 0.0:
            self._path_total_length = 1.0

    def _path_point(self, t: float) -> QPointF:
        """Return a point on the route path at arclength fraction *t* (0-1)."""
        if not self._path_seg_paths or self._path_total_length <= 0.0:
            return QPointF(0.0, 0.0)
        t = max(0.0, min(1.0, t))
        target = t * self._path_total_length
        cum = 0.0
        for i, seg in enumerate(self._path_seg_paths):
            seg_len = self._path_seg_lengths[i]
            if target <= cum + seg_len or i == len(self._path_seg_paths) - 1:
                seg_t = (target - cum) / seg_len if seg_len > 0 else 0.0
                seg_t = max(0.0, min(1.0, seg_t))
                return seg.pointAtPercent(seg_t)
            cum += seg_len
        return self._path_seg_paths[-1].pointAtPercent(1.0)

    def _path_nearest(self, wp: QPointF) -> float:
        """Find the arclength fraction (0-1) on the path nearest to *wp*."""
        if not self._path_seg_paths or self._path_total_length <= 0.0:
            return 0.0
        best_t = 0.0
        best_dist = 1e9
        num = 100
        for i in range(num + 1):
            t = i / num
            pt = self._path_point(t)
            d = math.hypot(wp.x() - pt.x(), wp.y() - pt.y())
            if d < best_dist:
                best_dist = d
                best_t = t
        # Refine around best guess
        for _ in range(3):
            step = 1.0 / num
            left = max(0.0, best_t - step)
            right = min(1.0, best_t + step)
            for j in range(5):
                t = left + (right - left) * j / 4.0
                pt = self._path_point(t)
                d = math.hypot(wp.x() - pt.x(), wp.y() - pt.y())
                if d < best_dist:
                    best_dist = d
                    best_t = t
        return best_t

    # ------------------------------------------------------------------ #
    #  paintEvent & drawing
    # ------------------------------------------------------------------ #

    def paintEvent(self, event):
        # ── Empty state when no devices loaded ──────────────────────────
        if not self._devices and not self._dev_by_id:
            p = QPainter(self)
            paint_bed(p, self.rect())
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._draw_empty_state(p)
            p.end()
            return

        # 静态层缓存: 网格 + 轨道线 + 设备基块(无路线状态/火车/选中高亮),
        # 按视口尺寸/缩放失效；小范围平移在缓存 margin 内直接位移复用，
        # 避免每次拖动都全量重建万级设备静态层。
        margin = self._static_cache_margin
        w, h = self.width(), self.height()
        key = (w, h, float(self._view_scale), self.devicePixelRatioF(), theme.CANVAS_REVISION)
        cache_ok = (
            self._static_cache is not None
            and self._static_cache_key == key
            and self._static_cache_offset is not None
        )
        dx = dy = 0.0
        if cache_ok:
            dx = float(self._view_offset.x()) - float(self._static_cache_offset.x())
            dy = float(self._view_offset.y()) - float(self._static_cache_offset.y())
            if abs(dx) > margin or abs(dy) > margin:
                cache_ok = False
        if not cache_ok:
            self._rebuild_static_cache()
            dx = dy = 0.0

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._static_cache is not None:
            p.drawPixmap(QPointF(-margin + dx, -margin + dy), self._static_cache)
        else:
            paint_bed(p, self.rect())
            self._draw_grid(p)

        p.save()
        p.translate(self._view_offset)
        p.scale(self._view_scale, self._view_scale)
        self._draw_ghost_preview(p)         # Feature 4 – behind route path
        self._draw_route_path(p)
        self._draw_trim_range_highlight(p)  # Feature 1 – path recolor
        self._draw_trim_handles(p)          # Feature 1 – drag handles
        self._draw_device_trace(p)          # Feature 3 – timeline trajectory
        self._draw_devices(p)               # 动态设备覆盖层(路线状态/选中/火车高亮)
        self._draw_train_visuals(p)         # 统一火车可视化(引擎碰撞后载荷, 多火车同帧)
        self._draw_snap_indicator(p)        # Snap-to-device indicator
        self._draw_track_being_drawn(p)     # Feature 6 – track being drawn
        self._draw_snapshot_flash(p)        # Feature 2 – keyframe flash
        self._draw_route_numbers(p)         # Feature 5 – route position badges
        if self._drag_start and self._drag_pos and not self._drag_reorder and not self._drag_device_id:
            r = QRectF(self._drag_start, self._drag_pos).normalized()
            if r.width() > 3 or r.height() > 3:
                p.setPen(QPen(QColor(FOCUS_BLUE), 1, Qt.PenStyle.DashLine))
                p.setBrush(QBrush(selection_fill()))
                p.drawRect(r)
        p.restore()
        self._draw_hud(p)
        p.end()

    def _rebuild_static_cache(self) -> None:
        """Render static layers (grid + track rail + device base tiles) into
        an offscreen pixmap. Invalidate on viewport/scale/device-set change;
        paintEvent then only redraws the dynamic overlays."""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            self._static_cache = None
            self._static_cache_key = None
            self._static_cache_offset = None
            return
        margin = self._static_cache_margin
        pm = hidpi_pixmap(w + margin * 2, h + margin * 2, self)   # C7：按 DPR 栅格化静态层
        pm.fill(QColor(BG_ROOT))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        paint_bed(p, QRectF(0, 0, w + margin * 2, h + margin * 2))
        # 缓存左上角对应 widget 坐标 (-margin, -margin)：先平移到 widget 坐标系。
        p.translate(margin, margin)
        self._draw_grid(p, margin=margin)
        p.save()
        p.translate(self._view_offset)
        p.scale(self._view_scale, self._view_scale)
        self._draw_track_path(p)
        self._draw_route_path_static(p)
        self._draw_device_bases(p)
        p.restore()
        p.end()
        self._static_cache = pm
        self._static_cache_key = (w, h, float(self._view_scale), self.devicePixelRatioF(),
                                  theme.CANVAS_REVISION)
        self._static_cache_offset = QPointF(self._view_offset)

    def resizeEvent(self, event):
        self._static_cache = None
        _defer_refit(self)   # R188-②：尺寸变化后按新尺寸重拟合（仅程序化 fit 态）
        super().resizeEvent(event)

    def _draw_hud(self, p: QPainter) -> None:
        """Draw the bottom-left zoom pill + control hint overlay."""
        if self.width() < 160 or self.height() < 30:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # -- Zoom pill --
        zoom_txt = f"{self._view_scale:.0%}"
        pill_font = QFont(_MONO_FAMILIES)
        pill_font.setPixelSize(11)
        p.setFont(pill_font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(zoom_txt)
        pad = 9
        pw = tw + pad * 2 + 16  # room for the magnifier glyph
        ph = 22
        px0 = 10
        py0 = self.height() - ph - 10
        pill = QRectF(px0, py0, pw, ph)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tone(theme.CANVAS_CARD, 235))
        p.drawRoundedRect(pill, 11, 11)
        p.setPen(QPen(tone(theme.CANVAS_BORDER, 180), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(pill, 11, 11)
        # magnifier glyph
        p.setPen(QColor(FOCUS_BLUE))
        gx = px0 + 11
        gy = py0 + ph / 2.0
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(gx - 1, gy - 1), 4.0, 4.0)
        p.drawLine(QPointF(gx + 2, gy + 2), QPointF(gx + 5, gy + 5))
        # zoom text
        p.setPen(QColor(TEXT_PRIMARY))
        p.drawText(QRectF(px0 + 18, py0, pw - 18, ph),
                   Qt.AlignmentFlag.AlignCenter, zoom_txt)

    def _draw_empty_state(self, p: QPainter) -> None:
        """Draw centered empty-state placeholder when no devices are loaded."""
        w, h = self.width(), self.height()
        if w < 100 or h < 60:
            return
        # Subtle dashed border
        margin = 20
        border_rect = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)
        p.setPen(QPen(QColor(BORDER_NORMAL), _HAIRLINE_W, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(border_rect, 6, 6)

        # Icon (simulated with text since we don't import icon lib here)
        p.setPen(QColor(FOCUS_BLUE))
        icon_font = QFont("Segoe UI", 32)
        p.setFont(icon_font)
        p.drawText(QRectF(0, h * 0.36, w, 48), Qt.AlignmentFlag.AlignCenter, "◎")

        # R182：空态两句说明压成一行短句（原双行引导已冗余）
        p.setPen(QColor(TEXT_SECONDARY))
        title_font = QFont("Microsoft YaHei", 13)
        p.setFont(title_font)
        p.drawText(QRectF(0, h * 0.50, w, 28), Qt.AlignmentFlag.AlignCenter,
                   "从 TopView 选择设备")

    def _draw_grid(self, p: QPainter, *, margin=0):
        s = max(0.005, float(self._view_scale or 1.0))
        step = 25.0
        while step * s < 16.0:
            step *= 2.0
        while step * s > 120.0 and step > 6.25:
            step /= 2.0
        tl = self._s2w(QPointF(-margin, -margin))
        br = self._s2w(QPointF(float(self.width()) + margin, float(self.height()) + margin))
        min_x = math.floor(min(tl.x(), br.x()) / step) * step
        max_x = math.ceil(max(tl.x(), br.x()) / step) * step
        min_y = math.floor(min(tl.y(), br.y()) / step) * step
        max_y = math.ceil(max(tl.y(), br.y()) / step) * step
        p.save()
        p.translate(self._view_offset)
        p.scale(self._view_scale, self._view_scale)
        p.setPen(QPen(QColor(theme.CANVAS_GRID_MINOR), 0.75 / s))
        x = min_x
        while x <= max_x:
            p.drawLine(QPointF(x, min_y), QPointF(x, max_y))
            x += step
        y = min_y
        while y <= max_y:
            p.drawLine(QPointF(min_x, y), QPointF(max_x, y))
            y += step
        p.setPen(QPen(QColor(theme.CANVAS_AXIS), 1.25 / s))
        p.drawLine(QPointF(min_x, 0), QPointF(max_x, 0))
        p.drawLine(QPointF(0, min_y), QPointF(0, max_y))
        p.restore()

    def _seg_bezier(self, p1: QPointF, p2: QPointF) -> QPainterPath:
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        path = QPainterPath(p1)
        if dist < 1.0:
            path.lineTo(p2)
            return path
        perp_x, perp_y = -dy / dist, dx / dist
        off = dist * 0.22
        cp1 = QPointF(p1.x() + dx * 0.33 + perp_x * off, p1.y() + dy * 0.33 + perp_y * off)
        cp2 = QPointF(p1.x() + dx * 0.67 + perp_x * off, p1.y() + dy * 0.67 + perp_y * off)
        path.cubicTo(cp1, cp2, p2)
        return path

    def _bezier_tan(self, p1: QPointF, p2: QPointF, t: float) -> Tuple[float, float]:
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            return dx, dy
        perp_x, perp_y = -dy / dist, dx / dist
        off = dist * 0.22
        c1x, c1y = p1.x() + dx * 0.33 + perp_x * off, p1.y() + dy * 0.33 + perp_y * off
        c2x, c2y = p1.x() + dx * 0.67 + perp_x * off, p1.y() + dy * 0.67 + perp_y * off
        u = 1.0 - t
        tx = 3.0 * u * u * (c1x - p1.x()) + 6.0 * u * t * (c2x - c1x) + 3.0 * t * t * (p2.x() - c2x)
        ty = 3.0 * u * u * (c1y - p1.y()) + 6.0 * u * t * (c2y - c1y) + 3.0 * t * t * (p2.y() - c2y)
        n = math.hypot(tx, ty)
        return (dx, dy) if n < 1e-8 else (tx / n, ty / n)

    def _arrow(self, p: QPainter, tip: QPointF, tx: float, ty: float, size: float, color: QColor):
        px, py = tip.x(), tip.y()
        s = size
        ax = px - tx * s + ty * s * 0.45
        ay = py - ty * s - tx * s * 0.45
        bx = px - tx * s - ty * s * 0.45
        by = py - ty * s + tx * s * 0.45
        ap = QPainterPath()
        ap.moveTo(px, py)
        ap.lineTo(ax, ay)
        ap.lineTo(bx, by)
        ap.closeSubpath()
        p.fillPath(ap, QBrush(color))
        p.setPen(QPen(color, 1))
        p.drawPath(ap)

    def _draw_route_path(self, p: QPainter):
        pts = self._path_pts
        n = len(pts)
        if n < 2:
            return
        comp = max(0, min(self._completed_count, n - 1))
        # 大路线(万级设备)用折线近似: 相邻设备间距远小于可视曲率, 直线与贝塞尔
        # 视觉无差, 但描边快 1~2 个数量级; 小/中路线保留贝塞尔弧线 + 方向箭头。
        if n > 500:
            self._draw_route_path_polyline(p, comp)
            return
        for i in range(n - 1):
            seg = self._path_seg_paths[i]
            if i < comp:
                color = QColor(BRIGHT_SUCCESS)
                pen = QPen(color, 2.0)
            elif i == comp and comp < n - 1:
                color = QColor(ACTIVE_ORANGE)
                pen = QPen(color, 3.0)
            else:
                color = QColor(BRIGHT_ERROR)
                pen = QPen(color, 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)  # constant screen width at any zoom
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(seg)
            if i >= comp:
                tx, ty = self._bezier_tan(pts[i], pts[i + 1], 0.9)
                ac = QColor(color)
                ac.setAlpha(200)
                # Arrow scaled to screen pixels
                self._arrow(p, pts[i + 1], tx, ty, self._px(7.0), ac)
        if 0 <= self._active_index < n - 1:
            pul = self._pulse()
            glow_pen = QPen(QColor(255, 160, 40, int(90 * pul)), 7.0)
            glow_pen.setCosmetic(True)
            p.setPen(glow_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._path_seg_paths[self._active_index])

    def _draw_route_path_static(self, p: QPainter) -> None:
        """静态层: 计算屏幕空间路线点并绘制整条"未完成红段"折线。进度高亮
        (绿/琥珀)由动态层 _draw_route_path_polyline 叠加, 逐帧不再重画 N 段。

        仅 n>500 折线路径入缓存; n<=500 的贝塞尔路径量小, 仍走动态层逐段。
        屏幕空间 + 关 AA: cosmetic 笔在缩放变换下按世界单位反算笔宽, 与密集
        几何叠加描边开销陡增; 屏幕空间笔宽即像素宽, 视觉等价且快 1~2 个数量级。
        """
        pts = self._path_pts
        n = len(pts)
        if n <= 500:
            self._route_screen_pts = []
            return
        s = float(self._view_scale or 1.0)
        ox = float(self._view_offset.x())
        oy = float(self._view_offset.y())
        spts = [QPointF(ox + pt.x() * s, oy + pt.y() * s) for pt in pts]
        self._route_screen_pts = spts
        p.save()
        p.resetTransform()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(QColor(BRIGHT_ERROR), 1.5, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF(spts))
        p.restore()

    def _draw_route_path_polyline(self, p: QPainter, comp: int) -> None:
        """大路线(>500)进度高亮叠加: 绿(已完成)/琥珀(激活)叠在静态红段之上。
        用静态层已算好的 _route_screen_pts, 逐帧仅画绿+琥珀+脉冲, 不再画红段。"""
        spts = self._route_screen_pts
        n = len(spts)
        if n < 2:
            return
        p.save()
        p.resetTransform()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if comp > 0:
            p.setPen(QPen(QColor(BRIGHT_SUCCESS), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(QPolygonF(spts[0:comp + 1]))
        if comp < n - 1:
            p.setPen(QPen(QColor(ACTIVE_ORANGE), 3.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(spts[comp], spts[comp + 1])
        if 0 <= self._active_index < n - 1:
            pul = self._pulse()
            p.setPen(QPen(QColor(255, 160, 40, int(90 * pul)), 7.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(spts[self._active_index], spts[self._active_index + 1])
        p.restore()

    # ------------------------------------------------------------------ #
    #  Feature 1 – Trim handles
    # ------------------------------------------------------------------ #

    def _draw_trim_range_highlight(self, p: QPainter):
        """Draw a subtle highlight along the trimmed portion of the path."""
        if not self._path_seg_paths or self._path_total_length <= 0.0:
            return
        if self._trim_start <= 0.0 and self._trim_end >= 1.0 and not self._drag_handle:
            return
        steps = max(10, int(30 * (self._trim_end - self._trim_start)))
        hl = QPainterPath()
        first = True
        for i in range(steps + 1):
            t = self._trim_start + (self._trim_end - self._trim_start) * i / steps
            pt = self._path_point(t)
            if first:
                hl.moveTo(pt)
                first = False
            else:
                hl.lineTo(pt)
        # End point
        pt = self._path_point(self._trim_end)
        hl.lineTo(pt)
        p.setPen(QPen(QColor(86, 182, 91, 50), 5.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(hl)

    def _draw_trim_handles(self, p: QPainter):
        """Draw the green start and red end drag handles on the route path."""
        if not self._path_seg_paths or self._path_total_length <= 0.0:
            return
        if len(self._ordered_ids) < 2:
            return
        start_pt = self._path_point(self._trim_start)
        end_pt = self._path_point(self._trim_end)
        r = 6.0
        # Start handle – green (bright_success)
        p.setPen(QPen(QColor(BRIGHT_SUCCESS), 2))
        p.setBrush(QBrush(QColor(BRIGHT_SUCCESS)))
        p.drawEllipse(start_pt, r, r)
        # End handle – red (bright_error)
        p.setPen(QPen(QColor(BRIGHT_ERROR), 2))
        p.setBrush(QBrush(QColor(BRIGHT_ERROR)))
        p.drawEllipse(end_pt, r, r)

    # ------------------------------------------------------------------ #
    #  Feature 2 – Keyframe snapshot flash
    # ------------------------------------------------------------------ #

    def _fade_snapshot_flash(self):
        self._snapshot_flash_alpha -= 0.1
        if self._snapshot_flash_alpha <= 0.0:
            self._snapshot_flash_alpha = 0.0
            self._snapshot_flash_timer.stop()
            self._snapshot_flash_pos = None
        self.update()

    def _draw_snapshot_flash(self, p: QPainter):
        """Draw a fading diamond at the snapshot position."""
        if self._snapshot_flash_pos is None or self._snapshot_flash_alpha <= 0.0:
            return
        x, y = self._snapshot_flash_pos.x(), self._snapshot_flash_pos.y()
        s = 10.0
        diamond = QPainterPath()
        diamond.moveTo(x, y - s)
        diamond.lineTo(x + s, y)
        diamond.lineTo(x, y + s)
        diamond.lineTo(x - s, y)
        diamond.closeSubpath()
        c = QColor(ACTIVE_ORANGE)
        c.setAlphaF(self._snapshot_flash_alpha)
        p.setPen(QPen(c, 2))
        p.setBrush(QBrush(c))
        p.drawPath(diamond)

    # ------------------------------------------------------------------ #
    #  Feature 3 – Device trace
    # ------------------------------------------------------------------ #

    def _draw_device_trace(self, p: QPainter):
        """Draw a dashed trajectory connecting keyframe positions of a device."""
        if not self._trace_device_id or not self._trace_data or len(self._trace_data) < 2:
            return
        # Dashed connecting lines
        pen = QPen(QColor(ACTIVE_ORANGE), _HAIRLINE_W, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        points = [QPointF(x, y) for _, x, y, _ in self._trace_data]
        path = QPainterPath()
        path.moveTo(points[0])
        for pt in points[1:]:
            path.lineTo(pt)
        p.drawPath(path)
        # Labels and markers at each keyframe point
        old_font = p.font()
        lf = QFont(old_font)
        lf.setPointSizeF(max(7.0, old_font.pointSizeF() - 2.0))
        p.setFont(lf)
        for time_sec, x, y, rank in self._trace_data:
            pt = QPointF(x, y)
            # Dot
            p.setPen(QPen(QColor(ACTIVE_ORANGE), 2))
            p.setBrush(QBrush(QColor(BG_DEEP)))
            p.drawEllipse(pt, 3.0, 3.0)
            # Label
            p.setPen(QColor(TEXT_PRIMARY))
            p.drawText(QRectF(pt.x() + 5, pt.y() - 10, 120, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"t={time_sec:.0f}s #{rank}")
        p.setFont(old_font)

    # ------------------------------------------------------------------ #
    #  Feature 4 – Ghost preview
    # ------------------------------------------------------------------ #

    def _draw_ghost_preview(self, p: QPainter):
        """Draw next cue's path as a faint dashed overlay that fades in."""
        if self._ghost_time_until < 0.0 or not self._ghost_ordered_ids:
            return
        if self._ghost_time_until >= 2.0:
            return
        if self._ghost_time_until <= 0.5:
            alpha = 0.30
        else:
            alpha = 0.30 * (2.0 - self._ghost_time_until) / 1.5
        if alpha <= 0.0:
            return
        pts = [self._dwp(self._dev_by_id[fid]) for fid in self._ghost_ordered_ids
               if self._dev_by_id.get(fid) is not None]
        if len(pts) < 2:
            return
        c = QColor(FOCUS_BLUE)
        c.setAlphaF(alpha)
        for i in range(len(pts) - 1):
            seg = self._seg_bezier(pts[i], pts[i + 1])
            pen = QPen(c, 2.0, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(seg)

    # ------------------------------------------------------------------ #
    #  Devices
    # ------------------------------------------------------------------ #

    def _draw_device_bases(self, p: QPainter, margin_override: Optional[float] = None) -> None:
        """Draw device base tiles (chrome/text, no route-state accent) — static."""
        lod = self._lod()
        vr = self._vr()
        margin = (
            self._static_cache_margin / max(0.005, self._view_scale)
            if margin_override is None else margin_override
        )
        for fid in self._visible_device_ids(vr, margin):
            dev = self._dev_by_id.get(fid)
            if dev is not None:
                self._draw_device_base(p, dev, lod)

    def _draw_devices(self, p: QPainter) -> None:
        """Draw device overlays (route-state accent/badge/selection/rings) — dynamic."""
        lod = self._lod()
        vr = self._vr()
        margin = 50.0 / max(0.005, self._view_scale)
        for fid in self._visible_device_ids(vr, margin):
            dev = self._dev_by_id.get(fid)
            if dev is not None:
                self._draw_device_overlay(p, dev, lod)

    def _draw_snap_indicator(self, p: QPainter) -> None:
        """Draw a small green diamond at the current snap target position."""
        snap_pos = self._snap_indicator_pos
        if snap_pos is None:
            return
        s = 5.0
        x, y = snap_pos.x(), snap_pos.y()
        diamond = QPainterPath()
        diamond.moveTo(x, y - s)
        diamond.lineTo(x + s, y)
        diamond.lineTo(x, y + s)
        diamond.lineTo(x - s, y)
        diamond.closeSubpath()
        c = QColor(BRIGHT_SUCCESS)
        c.setAlpha(200)
        p.setPen(QPen(c, _HAIRLINE_W))
        p.setBrush(QBrush(c))
        p.drawPath(diamond)

    def _route_state_accent(self, fid: str) -> Optional[QColor]:
        """Return the accent color for a device's route state, or None."""
        idx = self._ordered_index.get(fid)
        if idx is None:
            return None
        if idx < self._completed_count:
            return QColor(BRIGHT_SUCCESS)   # done – green
        if idx == self._active_index:
            return QColor(ACTIVE_ORANGE)   # active – orange
        return QColor(TEXT_DISABLED)      # pending – dim

    def _draw_device_base(self, p: QPainter, dev: Dict, lod: str) -> None:
        """Draw the static device chrome: card gradient/border/name or minimal dot.

        Route-state accent, selection, hover, trace and active rings are drawn
        separately by :meth:`_draw_device_overlay` (dynamic layer)."""
        d = self._dwp(dev)
        cx, cy = d.x(), d.y()
        size = self._tile_size_for_lod(lod)
        half = size / 2.0
        rect = QRectF(cx - half, cy - half, size, size)
        radius = self._px(5.0)

        # ── Minimal LOD: a clean filled dot ─────────────────────────────
        if lod == "minimal":
            dot = self._px(self._tile_screen_px() * 0.42)
            p.setPen(QPen(QColor(BORDER_NORMAL), self._px(0.8)))
            p.setBrush(QBrush(QColor(BG_PANEL)))
            p.drawEllipse(d, dot, dot)
            return

        # ── Card body: vertical gradient（D2-D1 单例）────────────────────
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_card_body_brush())
        p.drawRoundedRect(rect, radius, radius)

        # ── Border (soft; selection border drawn in overlay) ────────────
        p.setPen(_base_border_pen())
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, radius, radius)

        # ── Device name centered in the card ────────────────────────────
        if lod in ("medium", "high"):
            name = self._dname(dev)[:5]
            old_font = p.font()
            nf = QFont(old_font)
            nf.setPixelSize(max(10, int(self._tile_screen_px() * 0.30)))
            nf.setBold(True)
            p.setFont(nf)
            # Render text at constant screen size by temporarily un-scaling.
            self._draw_screen_text(p, cx, cy, rect, name, QColor(TEXT_PRIMARY))
            p.setFont(old_font)

    def _draw_device_overlay(self, p: QPainter, dev: Dict, lod: str) -> None:
        """Draw dynamic device overlays: route-state accent strip/badge,
        selection border, active pulse, hover/trace/quantum rings."""
        fid = str(dev.get("id", ""))
        d = self._dwp(dev)
        sel = fid in self._selected_ids

        # ── Minimal LOD: 只画选中高亮; 路线进度由路由路径(绿/琥珀/红段)表达,
        # 避免万级设备下逐点 route-state 着色(QColor 创建 + drawEllipse 热点)。
        if lod == "minimal":
            if sel:
                dot = self._px(self._tile_screen_px() * 0.42)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(BRIGHT_WARN)))
                p.drawEllipse(d, dot, dot)
            return

        cx, cy = d.x(), d.y()
        active = (self._active_index >= 0 and self._active_index < len(self._ordered_ids)
                  and self._ordered_ids[self._active_index] == fid)
        accent = self._route_state_accent(fid)
        size = self._tile_size_for_lod(lod)
        half = size / 2.0
        rect = QRectF(cx - half, cy - half, size, size)
        radius = self._px(5.0)

        # ── Route-state accent strip along the left edge ────────────────
        if accent is not None:
            strip_w = self._px(4.0)
            strip = QRectF(rect.left(), rect.top(), strip_w, rect.height())
            sp = QPainterPath()
            sp.addRoundedRect(rect, radius, radius)
            p.save()
            p.setClipPath(sp)
            p.fillRect(strip, QBrush(accent))
            p.restore()

        # ── Border (selected = bright gold) ─────────────────────────────
        if sel:
            bpen = QPen(QColor(BRIGHT_WARN), 2.0)
            bpen.setCosmetic(True)
            p.setPen(bpen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, radius, radius)

        # ── Route badge (above the tile) ────────────────────────────────
        if lod == "high" or (lod == "medium" and len(self._ordered_ids) <= 30):
            self._draw_badge(p, dev)

        # ── Active pulse ring (screen-constant) ─────────────────────────
        if active:
            pul = self._pulse()
            gr = half + self._px(3.0 + 4.0 * pul)
            rpen = QPen(QColor(255, 160, 40, int(200 * pul)), 2.0 + pul * 1.5)
            rpen.setCosmetic(True)
            p.setPen(rpen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(cx - gr, cy - gr, gr * 2, gr * 2), radius, radius)

        # ── Hover ring (focus_blue outer halo, semi-transparent fill) ───
        if fid == self._hover_device_id:
            hr = half + self._px(4.0)
            hrect = QRectF(cx - hr, cy - hr, hr * 2, hr * 2)
            hpath = QPainterPath()
            hpath.addRoundedRect(hrect, radius, radius)
            p.fillPath(hpath, selection_fill(26))
            hpen = QPen(QColor(FOCUS_BLUE), _HAIRLINE_W)
            hpen.setCosmetic(True)
            p.setPen(hpen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(hrect, radius, radius)

        # ── Trace ring (Feature 3) ──────────────────────────────────────
        if fid == self._trace_device_id:
            tr = half + self._px(5.0)
            tpen = QPen(QColor(ACTIVE_ORANGE), 2, Qt.PenStyle.DashLine)
            tpen.setCosmetic(True)
            p.setPen(tpen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(cx - tr, cy - tr, tr * 2, tr * 2), radius, radius)

        # ── Quantum preview ring (Feature 6) ────────────────────────────
        if fid in self._quantum_preview_ids:
            qr = half + self._px(5.0)
            qc = QColor(BRIGHT_SUCCESS)
            qc.setAlpha(200)
            qpen = QPen(qc, 2.5, Qt.PenStyle.DashLine)
            qpen.setCosmetic(True)
            p.setPen(qpen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(cx - qr, cy - qr, qr * 2, qr * 2), radius, radius)

    def _draw_screen_text(self, p: QPainter, wx: float, wy: float,
                          world_rect: QRectF, text: str, color: QColor) -> None:
        """Draw *text* centered at world point (wx, wy) at a constant on-screen
        size by painting in device space (immune to view scale)."""
        p.save()
        p.resetTransform()
        s = max(0.005, float(self._view_scale or 1.0))
        sx = self._view_offset.x() + wx * s
        sy = self._view_offset.y() + wy * s
        w_px = world_rect.width() * s
        h_px = world_rect.height() * s
        box = QRectF(sx - w_px / 2.0, sy - h_px / 2.0, w_px, h_px)
        p.setPen(color)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    # ------------------------------------------------------------------ #
    #  Feature 5 – Track path, train highlight, route numbers
    # ------------------------------------------------------------------ #

    def _draw_track_path(self, p: QPainter):
        """Draw the track rail. Progress > 0 splits into traveled (solid)
        and untraveled (dashed dim) portions with direction arrows."""
        if len(self._track_path_points) < 2:
            return
        pts = [QPointF(*pt) for pt in self._track_path_points]
        total = len(pts)
        progress = max(0.0, min(1.0, self._train_progress))
        split_idx = int(progress * (total - 1)) if total > 1 else 0
        split_idx = max(0, min(split_idx, total - 2))

        # -- Traveled portion (solid) --
        if split_idx > 0:
            p.save()
            pen = QPen(QColor(SCROLLBAR_HANDLE), 3.0, Qt.PenStyle.SolidLine)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(pts[0])
            for i in range(1, split_idx + 1):
                path.lineTo(pts[i])
            p.drawPath(path)
            p.restore()

        # -- Untraveled portion (dashed, dim) --
        if split_idx < total - 1:
            p.save()
            c = QColor(SCROLLBAR_HANDLE)
            c.setAlphaF(0.35)
            pen = QPen(c, 2.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            path = QPainterPath(pts[split_idx])
            for i in range(split_idx + 1, total):
                path.lineTo(pts[i])
            p.drawPath(path)
            p.restore()

        # -- Direction arrows on untraveled portion --
        self._draw_track_arrows(p, pts, split_idx)

    def _draw_track_arrows(self, p: QPainter, pts: List[QPointF],
                           start_idx: int) -> None:
        """Draw small direction arrows along the track path."""
        step = 5
        color = QColor(TEXT_DISABLED)
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(start_idx, len(pts) - 1, step):
            p1, p2 = pts[i], pts[i + 1]
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            dist = math.hypot(dx, dy)
            if dist < 1.0:
                continue
            tx, ty = dx / dist, dy / dist
            mx = (p1.x() + p2.x()) * 0.5
            my = (p1.y() + p2.y()) * 0.5
            s = 6.0
            ax = mx - tx * s + ty * s * 0.45
            ay = my - ty * s - tx * s * 0.45
            bx = mx - tx * s - ty * s * 0.45
            by = my - ty * s + tx * s * 0.45
            path = QPainterPath()
            path.moveTo(mx, my)
            path.lineTo(ax, ay)
            path.lineTo(bx, by)
            path.closeSubpath()
            p.fillPath(path, QBrush(color))
        p.restore()

    def _draw_train_visuals(self, p: QPainter) -> None:
        """波次3 统一火车可视化: 按 train_id 同帧绘制全部火车(引擎碰撞后载荷)。

        组结构预览(_train_group_devices)与播放可视化并存; 播放可视化来自
        set_train_visuals 推送: spatial 画车厢链, quantum 画设备高亮,
        均带车头标记与作用半径圆。旧单实例字段直接写入时走平面高亮回退。
        """
        if self._train_active_ids and self._train_group_devices:
            self._draw_train_groups(p)
        elif self._train_active_ids and not self._train_visuals:
            self._draw_flat_highlight(p, self._train_active_ids, self._train_color)
            self._draw_train_head_marker(
                p, (self._train_x, self._train_y), self._train_color,
                self._train_width,
            )
        for _tid, v in self._train_visuals.items():
            color = str(v.get("color") or ACTIVE_ORANGE)
            width = max(1, int(v.get("width") or 1))
            if v.get("mode") == "spatial" and v.get("car_positions"):
                self._draw_car_bodies_at(p, v["car_positions"], color)
            elif v.get("covered_ids"):
                self._draw_flat_highlight(p, v["covered_ids"], color)
            head = v.get("head_pos") or v.get("head_target") or (0.0, 0.0)
            self._draw_train_head_marker(p, head, color, width)

    def _draw_flat_highlight(self, p: QPainter, active_ids, color: str) -> None:
        """平面高亮(无编组): 活动设备发光瓦片(波次3 提取自旧 _draw_train_highlight)。"""
        if not active_ids:
            return
        size = self._tile_size_for_lod(self._lod())
        half = size / 2.0
        radius_c = self._px(5.0)
        for fid in active_ids:
            dev = self._dev_by_id.get(str(fid))
            if dev is None:
                continue
            d = self._dwp(dev)
            rect = QRectF(d.x() - half, d.y() - half, size, size)
            # Glow fill — bright rounded tile
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color).lighter(180))
            p.drawRoundedRect(rect, radius_c, radius_c)
            # Thick colored border
            bpen = QPen(QColor(color), 3)
            bpen.setCosmetic(True)
            p.setPen(bpen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, radius_c, radius_c)

    def _draw_train_head_marker(
        self, p: QPainter, head: Tuple[float, float], color: str, width: int,
    ) -> None:
        """车头标记圆 + 作用半径虚线圆(波次3 提取, 每火车各画一份)。"""
        tx, ty = head
        mr = self._px(6.0)
        p.setBrush(QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(tx, ty), mr, mr)
        # Draw reach-radius circle (world-space physical reach)
        rpen = QPen(QColor(color), 1, Qt.PenStyle.DashLine)
        rpen.setCosmetic(True)
        p.setPen(rpen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        radius = float(max(1, width)) * 20.0
        p.drawEllipse(QPointF(tx, ty), radius, radius)

    def _draw_train_groups(self, p: QPainter):
        """Draw train with group/chain visualization.

        Cars: highlighted tiles with bright fill.
        Chains: dim dashed lines between groups, NO highlight on chain devices.
        Group boundary: thin gray line between adjacent groups.
        """
        lod = self._lod()
        size = self._tile_size_for_lod(lod)
        half = size / 2.0

        # -- Draw each group's cars using _draw_car_highlight --
        for gi, group_ids in enumerate(self._train_group_devices):
            for dev_id in group_ids:
                dev = self._dev_by_id.get(str(dev_id))
                if dev is None:
                    continue
                d = self._dwp(dev)
                self._draw_car_highlight(p, d.x(), d.y(), self._train_color)

            # -- Group boundary separator --
            if gi > 0 and group_ids:
                prev_last = self._train_group_devices[gi - 1][-1]
                first_this = group_ids[0]
                prev_dev = self._dev_by_id.get(str(prev_last))
                this_dev = self._dev_by_id.get(str(first_this))
                if prev_dev is not None and this_dev is not None:
                    p1 = self._dwp(prev_dev)
                    p2 = self._dwp(this_dev)
                    # Thin gray separator between groups
                    p.setPen(QPen(QColor(TEXT_DISABLED), 1, Qt.PenStyle.DotLine))
                    p.drawLine(QPointF(p1.x() + half, p1.y()),
                               QPointF(p2.x() - half, p2.y()))

        # -- Draw coupling chains between groups (dim dashed lines) --
        if len(self._train_group_devices) >= 2:
            for gi in range(len(self._train_group_devices) - 1):
                last_car = self._train_group_devices[gi][-1]
                first_next = self._train_group_devices[gi + 1][0]
                if self._spline_device_order:
                    self._draw_chain_curve(p, last_car, first_next)
                else:
                    self._draw_chain(p, last_car, first_next)

        # -- Train position marker --
        tx, ty = self._train_x, self._train_y
        mr = self._px(6.0)
        p.setBrush(QColor(self._train_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(tx, ty), mr, mr)

        # -- Reach-radius circle --
        rpen = QPen(QColor(self._train_color), 1, Qt.PenStyle.DashLine)
        rpen.setCosmetic(True)
        p.setPen(rpen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        radius = float(self._train_width) * 20.0
        p.drawEllipse(QPointF(tx, ty), radius, radius)

    def _draw_car_highlight(self, p: QPainter, x: float, y: float, color: str):
        """Draw a single car highlight tile at *(x, y)* in world space."""
        lod = self._lod()
        size = self._tile_size_for_lod(lod)
        half = size / 2.0
        radius_c = self._px(5.0)
        rect = QRectF(x - half, y - half, size, size)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color).lighter(175))
        p.drawRoundedRect(rect, radius_c, radius_c)
        bpen = QPen(QColor(color), 3)
        bpen.setCosmetic(True)
        p.setPen(bpen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, radius_c, radius_c)

    def _draw_car_bodies_at(
        self, p: QPainter, car_positions, color: str,
    ) -> None:
        """空间化车厢链绘制(波次3 提取自旧 _draw_train_car_bodies, 按火车着色)。"""
        if not car_positions:
            return
        qc = QColor(color)
        car_half = self._px(self._tile_screen_px() * 0.35)
        radius_c = self._px(4.0)
        for x, y in car_positions:
            rect = QRectF(x - car_half, y - car_half, car_half * 2.0, car_half * 2.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qc.lighter(175))
            p.drawRoundedRect(rect, radius_c, radius_c)
            bpen = QPen(qc, 2)
            bpen.setCosmetic(True)
            p.setPen(bpen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, radius_c, radius_c)

    def _draw_chain(self, p: QPainter, last_car_id: str, first_next_id: str):
        """Draw a dim dashed line representing the coupling chain.

        The chain spans from *last_car_id* (last device in previous group)
        to *first_next_id* (first device in next group).  Chain devices
        receive NO highlight — they are visual-only spacers.
        """
        dev_a = self._dev_by_id.get(str(last_car_id))
        dev_b = self._dev_by_id.get(str(first_next_id))
        if dev_a is None or dev_b is None:
            return
        p1 = self._dwp(dev_a)
        p2 = self._dwp(dev_b)
        pen = QPen(QColor(SCROLLBAR_HANDLE), _HAIRLINE_W, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(p1.x(), p1.y()), QPointF(p2.x(), p2.y()))

    def _draw_chain_curve(self, p: QPainter, from_dev: str,
                          to_dev: str) -> None:
        """Draw a curved chain following the spline between two groups.
        Falls back to straight chain when no spline path is found."""
        chain_devs = self._get_spline_chain_devices(from_dev, to_dev)
        if not chain_devs:
            self._draw_chain(p, from_dev, to_dev)
            return
        pts = []
        for d in [from_dev] + chain_devs + [to_dev]:
            dev = self._dev_by_id.get(str(d))
            if dev is None:
                continue
            pts.append(self._dwp(dev))
        if len(pts) < 2:
            return
        pen = QPen(QColor(SCROLLBAR_HANDLE), _HAIRLINE_W, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(len(pts) - 1):
            p.drawLine(pts[i], pts[i + 1])

    def _get_spline_chain_devices(self, from_dev: str,
                                  to_dev: str) -> List[str]:
        """Extract intermediate devices between *from_dev* and *to_dev*
        from the spline device order."""
        if not self._spline_device_order:
            return []
        try:
            start = self._spline_device_order.index(from_dev)
            end = self._spline_device_order.index(to_dev)
            if end > start + 1:
                return self._spline_device_order[start + 1:end]
        except ValueError:
            pass
        return []

    def _draw_route_numbers(self, p: QPainter):
        """Draw position number badges on device tiles (top-right corner).
        Badge size is screen-constant so it remains readable at any zoom.
        """
        if not self._route_overlay_data:
            return
        lod = self._lod()
        size = self._tile_size_for_lod(lod)
        half = size / 2.0
        badge_size = self._px(max(11.0, self._tile_screen_px() * 0.38))
        old_font = p.font()
        nf = QFont(_MONO_FAMILIES)
        # 路线序号: 等宽 10~11px(D波排版契约)
        nf.setPixelSize(max(10, min(11, int(self._tile_screen_px() * 0.28))))
        nf.setBold(True)
        p.setFont(nf)
        for fid, x, y, rank, color in self._route_overlay_data:
            bw = badge_size
            badge_rect = QRectF(x + half - bw, y - half, bw, bw)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(badge_rect, self._px(3.0), self._px(3.0))
            bpen = QPen(QColor(BG_DEEP), 1)
            bpen.setCosmetic(True)
            p.setPen(bpen)
            p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(rank + 1))
        p.setFont(old_font)

    # ------------------------------------------------------------------ #
    #  Feature 6 – Train drag interaction
    # ------------------------------------------------------------------ #

    def _draw_track_being_drawn(self, p: QPainter):
        """Draw the path as the user drags — a smooth, thick, glowing ribbon.
        Painter is in world-space (transform already applied in paintEvent).
        Cosmetic pens keep the stroke a constant screen width at any zoom.
        """
        if not self._drawing_track or len(self._track_draw_points) < 2:
            return
        # Catmull-Rom 平滑成连续曲线，而非粗糙折线
        smooth = _catmull_rom_spline(self._track_draw_points, num_segments=16)
        if len(smooth) < 2:
            smooth = self._track_draw_points
        path = QPainterPath(QPointF(smooth[0][0], smooth[0][1]))
        for x, y in smooth[1:]:
            path.lineTo(QPointF(x, y))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # 外发光底衬（半透明宽笔）
        glow = QPen(QColor(255, 160, 40, 70), 9.0)
        glow.setCosmetic(True)
        glow.setCapStyle(Qt.PenCapStyle.RoundCap)
        glow.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(glow)
        p.drawPath(path)
        # 主笔：加粗、圆头、恒定屏幕宽度
        main = QPen(QColor(ACTIVE_ORANGE).lighter(115), 4.0)
        main.setCosmetic(True)
        main.setCapStyle(Qt.PenCapStyle.RoundCap)
        main.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(main)
        p.drawPath(path)

    def _preview_quantum_path(self, track_points: List[Tuple[float, float]],
                               dev_by_id: Dict[str, Dict]) -> List[str]:
        """Given a partial track, predict which devices the train will activate.
        Quantum: never between devices, always exactly on one.
        Returns an ordered list of device IDs the train would visit.
        """
        result: List[str] = []
        last_device: Optional[str] = None
        for wx, wy in track_points:
            nearest: Optional[str] = None
            nearest_dist = float("inf")
            for fid, dev in dev_by_id.items():
                dx = wx - float(dev.get("x", 0.0))
                dy = wy - float(dev.get("y", 0.0))
                dist = math.hypot(dx, dy)
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest = fid
            if nearest is not None and nearest != last_device:
                result.append(nearest)
                last_device = nearest
        return result

    def _snap_spline_to_devices(
        self,
        spline_points: List[Tuple[float, float]],
        positions: Dict[str, Tuple[float, float]],
        device_ids: List[str],
    ) -> List[str]:
        """Snap each spline point to nearest device, deduplicate consecutive hits.
        Returns ordered list of device IDs forming the track."""
        device_order: List[str] = []
        last_dev: Optional[str] = None
        for px, py in spline_points:
            nearest: Optional[str] = None
            nearest_dist = float("inf")
            for dev_id in device_ids:
                pos = positions.get(dev_id)
                if pos is None:
                    continue
                dx = px - pos[0]
                dy = py - pos[1]
                dist = dx * dx + dy * dy
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest = dev_id
            if nearest is not None and nearest != last_dev:
                device_order.append(nearest)
                last_dev = nearest
        return device_order

    # ------------------------------------------------------------------ #
    #  Feature 3 – Multi-train track assignment helpers
    # ------------------------------------------------------------------ #

    def get_drawn_track_relative(self) -> List[Tuple[float, float]]:
        """Return the drawn track as relative offsets from the starting point.

        This allows applying the same track shape to multiple trains at different
        positions.  Returns an empty list when fewer than 2 points were drawn.
        """
        if not self._track_draw_points or len(self._track_draw_points) < 2:
            return []
        start_x, start_y = self._track_draw_points[0]
        return [(x - start_x, y - start_y) for x, y in self._track_draw_points]

    def apply_track_relative(
        self, offsets: List[Tuple[float, float]], origin_device: str,
    ) -> List[Tuple[float, float]]:
        """Apply a relative track to a train starting at **origin_device**.

        Parameters
        ----------
        offsets :
            Relative offset vectors from :meth:`get_drawn_track_relative`.
        origin_device :
            Device ID whose canvas position is used as the translation origin.

        Returns
        -------
        list[tuple[float, float]]
            Absolute world-coordinate points for the translated track.
        """
        dev = self._dev_by_id.get(origin_device)
        if dev is None:
            return list(offsets)  # fallback: return offsets as absolute
        ox = float(dev.get("x", 0.0))
        oy = float(dev.get("y", 0.0))
        return [(ox + dx, oy + dy) for dx, dy in offsets]

    def _draw_badge(self, p: QPainter, dev: Dict):
        fid = str(dev.get("id", ""))
        idx = self._ordered_index.get(fid)
        if idx is None:
            return
        num = idx + 1
        d = self._dwp(dev)
        cx, cy = d.x(), d.y() - self._badge_offset()
        br = self._px(8.5)
        # 与 _route_state_accent 同源: done 绿 / active 橙 / pending 灰
        if idx < self._completed_count:
            bg, fg = QColor(BRIGHT_SUCCESS), QColor(BG_DEEP)
        elif idx == self._active_index:
            bg, fg = QColor(ACTIVE_ORANGE), QColor(BG_DEEP)
        else:
            bg, fg = QColor(TEXT_DISABLED), QColor(BG_DEEP)
        bpen = QPen(QColor(BG_DEEP), 1.2)
        bpen.setCosmetic(True)
        p.setPen(bpen)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(QRectF(cx - br, cy - br, br * 2, br * 2), self._px(3.0), self._px(3.0))
        old = p.font()
        f = QFont(_MONO_FAMILIES)
        # 数字徽标: 等宽 10~11px(D波排版契约)
        f.setPixelSize(max(10, min(11, int(self._tile_screen_px() * 0.34))))
        f.setBold(True)
        p.setFont(f)
        self._draw_screen_text(
            p, cx, cy, QRectF(cx - br, cy - br, br * 2, br * 2),
            _circle_num(num), fg,
        )
        p.setFont(old)

    def _ins_idx(self, wp: QPointF) -> int:
        if self._spatial_grid:
            cell = self._spatial_cell
            cx = int(wp.x() / cell)
            cy = int(wp.y() / cell)
            oi = self._ordered_index
            best = 0
            bd = float("inf")
            # 扩展环搜索: 从 wp 所在桶向外找最近已排序设备。
            # 第 r 环的最小可能距离 = (r-1)*cell, 故搜完第 radius 环后, 若下一环
            # (radius+1) 最小距离 radius*cell 已 >= sqrt(bd), 更外环不可能更近 → 停。
            for radius in range(0, 64):
                for dx in range(-radius, radius + 1):
                    for dy in range(-radius, radius + 1):
                        if max(abs(dx), abs(dy)) != radius:
                            continue
                        for fid in self._spatial_grid.get((cx + dx, cy + dy), ()):
                            idx = oi.get(fid)
                            if idx is None:
                                continue
                            dev = self._dev_by_id.get(fid)
                            if dev is None:
                                continue
                            d = self._dwp(dev)
                            dist = (wp.x() - d.x()) ** 2 + (wp.y() - d.y()) ** 2
                            if dist < bd:
                                bd = dist
                                best = idx
                if bd != float("inf") and (radius * cell) ** 2 >= bd:
                    break
            if bd != float("inf"):
                return best
        best, bd = 0, 1e9
        for i, fid in enumerate(self._ordered_ids):
            dev = self._dev_by_id.get(fid)
            if dev is not None:
                d = self._dwp(dev)
                dist = math.hypot(wp.x() - d.x(), wp.y() - d.y())
                if dist < bd:
                    bd, best = dist, i
        return best

    def _adj_active(self, old: int, new_: int):
        if self._active_index == old:
            self._active_index = new_
        elif old < self._active_index < new_:
            self._active_index -= 1
        elif new_ <= self._active_index < old:
            self._active_index += 1

    # ------------------------------------------------------------------ #
    #  Mouse events
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, ev: QMouseEvent):
        sp = ev.position()
        wp = self._s2w(sp)
        if ev.button() == Qt.MouseButton.MiddleButton or (
            ev.button() == Qt.MouseButton.LeftButton and ev.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self._panning, self._fit_size = True, None   # R188-②：手动平移退出自动重拟合
            self._pan_start_screen = sp
            self._pan_start_offset = QPointF(self._view_offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            ev.accept()
            return
        if ev.button() == Qt.MouseButton.RightButton:
            self._show_cmenu(ev.globalPosition().toPoint())
            ev.accept()
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return

        # 车厢自由路径模式：按下即开始画（不吸附设备、不量化），优先于其它交互
        if self._car_path_mode:
            self._drawing_track = True
            self._track_draw_points = [(wp.x(), wp.y())]
            self.update()
            ev.accept()
            return

        # Feature 1: Trim handle hit test
        if self._path_seg_paths and self._path_total_length > 0.0:
            tol = 15.0 / max(0.005, self._view_scale)
            spt = self._path_point(self._trim_start)
            ept = self._path_point(self._trim_end)
            if math.hypot(wp.x() - spt.x(), wp.y() - spt.y()) <= tol:
                self._drag_handle = "start"
                self._drag_start = wp
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                ev.accept()
                return
            if math.hypot(wp.x() - ept.x(), wp.y() - ept.y()) <= tol:
                self._drag_handle = "end"
                self._drag_start = wp
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                ev.accept()
                return

        for fid in reversed(self._ordered_ids):
            dev = self._dev_by_id.get(fid)
            if dev is not None and self._badge_hit(dev, wp):
                self._drag_device_id = fid
                self._drag_reorder = True
                self._drag_start = wp
                self._drag_pos = wp
                self._drag_insert_index = self._ordered_ids.index(fid)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                return

        fid = self._device_at(wp)
        if fid:
            mods = ev.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                self._selected_ids.add(fid) if fid not in self._selected_ids else self._selected_ids.discard(fid)
                self._last_clicked_id = fid
                self.selection_changed.emit(list(self._selected_ids))
                self.update()
                return
            if mods & Qt.KeyboardModifier.ShiftModifier and self._last_clicked_id:
                o = [f for f in self._ordered_ids if f in self._dev_by_id]
                if self._last_clicked_id in o and fid in o:
                    a, b = o.index(self._last_clicked_id), o.index(fid)
                    self._selected_ids = set(o[min(a, b):max(a, b) + 1])
                    self.selection_changed.emit(list(self._selected_ids))
                    self.update()
                    return
            # Feature 3: Toggle device trace on single click of traced device
            if fid == self._trace_device_id:
                self._trace_device_id = ""
                self._trace_data = []
            self._selected_ids = {fid}
            self._last_clicked_id = fid
            self._drag_device_id = fid
            self._drag_start = wp
            self._drag_pos = wp
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.selection_changed.emit(list(self._selected_ids))
            # 单击=仅选中；画轨改为「拖拽已选设备超过阈值才启动」（见 mouseMoveEvent）
            self.update()
            return

        # Click on empty space
        # Feature 6: Cancel any in-progress track drawing
        if self._drawing_track:
            self._drawing_track = False
            self._track_draw_points = []
            self._quantum_preview_ids.clear()
        self._selected_ids.clear()
        self._last_clicked_id = ""
        # Feature 3: Clear device trace
        self._trace_device_id = ""
        self._trace_data = []
        self._drag_start = wp
        self._drag_pos = wp
        self.selection_changed.emit([])
        self.update()

    def mouseMoveEvent(self, ev: QMouseEvent):
        sp, wp = ev.position(), self._s2w(ev.position())
        if self._panning and self._pan_start_screen is not None:
            d = sp - self._pan_start_screen
            self._view_offset = QPointF(self._pan_start_offset.x() + d.x(), self._pan_start_offset.y() + d.y())
            self._static_cache = None
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            ev.accept()
            return

        # Feature 1: Trim handle drag
        if self._drag_handle:
            fra = self._path_nearest(wp)
            if self._drag_handle == "start":
                self._trim_start = max(0.0, min(float(self._trim_end), fra))
            else:
                self._trim_end = max(float(self._trim_start), min(1.0, fra))
            self.trim_handle_dragged.emit(self._trim_start, self._trim_end)
            self.update()
            ev.accept()
            return

        # Feature 6: Track drawing in progress
        if self._drawing_track:
            # 车厢自由路径模式：直接用鼠标世界坐标，不吸附、不做设备量化预览
            if self._car_path_mode:
                self._track_draw_points.append((wp.x(), wp.y()))
                self.update()
                ev.accept()
                return
            # Alt temporarily disables snap
            alt_held = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
            old_snap = self._snap_enabled
            if alt_held:
                self._snap_enabled = False
            raw_wp = self._s2w(ev.position())
            snapped_wp = self._snap_to_nearest_device(raw_wp)
            self._snap_enabled = old_snap
            self._track_draw_points.append((snapped_wp.x(), snapped_wp.y()))
            # Update quantum preview while drawing
            self._quantum_preview_ids = set(
                self._preview_quantum_path(self._track_draw_points, self._dev_by_id)
            )
            self.update()
            ev.accept()
            return

        if self._drag_reorder and self._drag_device_id:
            self._drag_pos = wp
            self._drag_insert_index = self._ins_idx(wp)
            self.update()
            return
        if self._drag_device_id and not self._drag_reorder and self._drag_start:
            self._drag_pos = wp
            # Feature 6: 拖拽已选设备超过阈值 → 从该设备开始画轨（单击只选中不画）
            if (not self._drawing_track
                    and math.hypot(wp.x() - self._drag_start.x(),
                                   wp.y() - self._drag_start.y()) > self._px(5.0)):
                dev = self._dev_by_id.get(self._drag_device_id)
                if dev:
                    self._drawing_track = True
                    self._train_device_id = self._drag_device_id
                    # 起点=设备中心，并立刻补上当前(吸附)点，保持「按下+一次移动」≥2点的旧行为
                    alt_held = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
                    old_snap = self._snap_enabled
                    if alt_held:
                        self._snap_enabled = False
                    snapped_wp = self._snap_to_nearest_device(wp)
                    self._snap_enabled = old_snap
                    self._track_draw_points = [
                        (float(dev.get("x", 0.0)), float(dev.get("y", 0.0))),
                        (snapped_wp.x(), snapped_wp.y()),
                    ]
                    self.train_placed.emit(self._drag_device_id)
            self.update()
            return
        if self._drag_start and not self._drag_device_id:
            self._drag_pos = wp
            self.update()
            return
        # 悬停光标(D波交互成熟化): 裁切手柄=手型; 设备=张手; 其余=箭头。
        if self._path_seg_paths and self._path_total_length > 0.0:
            tol = 15.0 / max(0.005, self._view_scale)
            spt = self._path_point(self._trim_start)
            ept = self._path_point(self._trim_end)
            if (math.hypot(wp.x() - spt.x(), wp.y() - spt.y()) <= tol
                    or math.hypot(wp.x() - ept.x(), wp.y() - ept.y()) <= tol):
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                return
        fid = self._device_at(wp)
        if fid != self._hover_device_id:
            self._hover_device_id = fid if fid else ""
            self.update()
        self.setCursor(Qt.CursorShape.OpenHandCursor if fid else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if self._panning:
            self._panning = False
            self._pan_start_screen = self._pan_start_offset = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            ev.accept()
            return

        # Feature 1: Trim handle release
        if self._drag_handle:
            self._drag_handle = ""
            self._drag_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.trim_handle_dragged.emit(self._trim_start, self._trim_end)
            self.update()
            ev.accept()
            return

        # 车厢自由路径模式：完成绘制 → 平滑后发出自由几何路径（不量化到设备）
        if self._drawing_track and self._car_path_mode:
            self._drawing_track = False
            pts = list(self._track_draw_points)
            self._track_draw_points = []
            self._car_path_mode = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if len(pts) >= 2:
                smooth = _catmull_rom_spline(pts, num_segments=16)
                self._track_path_points = list(smooth)
                self.car_path_drawn.emit(smooth)
            self.update()
            ev.accept()
            return

        # Feature 6: Finalize track drawing
        if self._drawing_track:
            self._drawing_track = False
            points = list(self._track_draw_points)
            self._track_draw_points = []
            self._quantum_preview_ids.clear()
            # Clean up any associated drag state
            self._drag_device_id = ""
            self._drag_start = None
            self._drag_pos = None
            self._drag_reorder = False
            self._drag_insert_index = -1
            if len(points) >= 2:
                # Feature 1: Fit Catmull-Rom spline
                spline_pts = _catmull_rom_spline(points)
                self._track_path_points = list(spline_pts)
                # Feature 2: Snap spline to devices for curved drawing
                self._spline_device_order = self._snap_spline_to_devices(
                    spline_pts,
                    {fid: (float(d.get("x", 0.0)), float(d.get("y", 0.0)))
                     for fid, d in self._dev_by_id.items()},
                    list(self._dev_by_id.keys()),
                )
                self.track_drawn.emit(spline_pts)
                # Keyframe snapshot at this position
                if self._active_cue_time >= 0.0:
                    self.keyframe_snapshot.emit(
                        self._active_cue_time,
                        self._trim_start,
                        self._trim_end,
                        self._trim_offset,
                        self._stagger_delay,
                        list(self._ordered_ids),
                    )
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            ev.accept()
            return

        if self._drag_reorder and self._drag_device_id:
            tgt = self._drag_insert_index
            if tgt >= 0 and self._drag_device_id in self._ordered_ids:
                old = self._ordered_ids.index(self._drag_device_id)
                if tgt != old and tgt != old + 1:
                    ids = list(self._ordered_ids)
                    ids.remove(self._drag_device_id)
                    ins = max(0, min(tgt if tgt <= old else tgt - 1, len(ids)))
                    ids.insert(ins, self._drag_device_id)
                    self._ordered_ids = ids
                    self._adj_active(old, ins)
                    self._build_path_cache()
                    self.route_order_changed.emit(list(self._ordered_ids))
            self._drag_reorder = False
            self._drag_device_id = ""
            self._drag_start = self._drag_pos = None
            self._drag_insert_index = -1
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return
        if self._drag_device_id:
            self._drag_device_id = ""
            self._drag_start = self._drag_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            return
        if self._drag_start and self._drag_pos and not self._drag_device_id:
            r = QRectF(self._drag_start, self._drag_pos).normalized()
            if r.width() > 4 and r.height() > 4:
                sel = [str(d.get("id", "")) for d in self._devices if r.contains(self._dwp(d))]
                self._selected_ids = set(sel) if sel else set()
            self._drag_start = self._drag_pos = None
            self.selection_changed.emit(list(self._selected_ids))
            self.update()
            return
        self._drag_start = self._drag_pos = None
        self.update()

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        """Double-click on empty canvas -> snapshot keyframe at current cue time."""
        if ev.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(ev)
            return
        wp = self._s2w(ev.position())
        # Pass through if on a device or trim handle
        if self._device_at(wp):
            super().mouseDoubleClickEvent(ev)
            return
        if self._path_seg_paths and self._path_total_length > 0.0:
            tol = 15.0 / max(0.005, self._view_scale)
            spt = self._path_point(self._trim_start)
            ept = self._path_point(self._trim_end)
            if (math.hypot(wp.x() - spt.x(), wp.y() - spt.y()) <= tol or
                    math.hypot(wp.x() - ept.x(), wp.y() - ept.y()) <= tol):
                super().mouseDoubleClickEvent(ev)
                return
        # Snapshot if we have an active cue
        if self._active_cue_time >= 0.0:
            self.keyframe_snapshot.emit(
                self._active_cue_time,
                self._trim_start,
                self._trim_end,
                self._trim_offset,
                self._stagger_delay,
                list(self._ordered_ids),
            )
            # Visual flash
            self._snapshot_flash_pos = wp
            self._snapshot_flash_alpha = 1.0
            self._snapshot_flash_timer.start()
        ev.accept()

    def wheelEvent(self, ev: QWheelEvent):
        d = ev.angleDelta().y()
        if d:
            self._zoom_at(ev.position(), 1.25 if d > 0 else 1.0 / 1.25)
            ev.accept()
            return
        super().wheelEvent(ev)

    def keyPressEvent(self, ev):
        # -- Zoom-to-fit: Ctrl+Shift+0 --
        if ev.key() == Qt.Key.Key_0:
            mods = ev.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier and mods & Qt.KeyboardModifier.ShiftModifier:
                self.zoom_to_fit()
                ev.accept()
                return
            if mods & Qt.KeyboardModifier.ControlModifier and not (mods & Qt.KeyboardModifier.ShiftModifier):
                self.reset_view()
                ev.accept()
                return

        # -- Frame stepping: Left/Right arrows --
        k = ev.key()
        mods = ev.modifiers()
        frame_dur = 0.04  # 1 frame at 25 fps
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = frame_dur * (10 if mods & Qt.KeyboardModifier.ShiftModifier else 1)
            direction = 1 if k == Qt.Key.Key_Right else -1
            self.frame_step_requested.emit(direction * step)
            ev.accept()
            return

        # -- View pan via arrow keys (only when Shift not held for frame stepping) --
        pan = 80.0
        if k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            dy = pan if k == Qt.Key.Key_Down else -pan
            self._view_offset = QPointF(self._view_offset.x(), self._view_offset.y() - dy)
            self._static_cache = None
            self.update()
            ev.accept()
            return
        if k == Qt.Key.Key_Home:
            self.frame_step_requested.emit(-999999.0)  # go to start
            ev.accept()
            return
        if k == Qt.Key.Key_End:
            self.frame_step_requested.emit(999999.0)  # go to end
            ev.accept()
            return

        super().keyPressEvent(ev)

    def leaveEvent(self, ev):
        self._hover_device_id = ""
        self.update()
        super().leaveEvent(ev)

    def _show_cmenu(self, gp):
        if not self._ordered_ids and not self._selected_ids:
            return
        menu = QMenu(self)
        # 本地 QMenu 通用外观已由全局主题(GLOBAL_QSS)覆盖, 按规范删除页面级重复定义。

        a = menu.addAction
        if self._ordered_ids and self._selected_ids:
            a("移到最前").triggered.connect(lambda: self._reorder("front"))
            a("移到最后").triggered.connect(lambda: self._reorder("back"))
            a("反向所选").triggered.connect(lambda: self._reorder("reverse"))
            a("按X轴排序所选").triggered.connect(lambda: self._reorder("sort_x"))
            menu.addSeparator()
            a("反向整体路线").triggered.connect(lambda: self._reorder("reverse_all"))
            a("清除选择").triggered.connect(lambda: self._reorder("clear"))
            menu.addSeparator()
        # Feature 2: Batch train creation（火车物理 UI 关闭时不提供入口）
        sel_ids = list(self._selected_ids)
        if _TRAIN_PHYSICS_UI and len(sel_ids) >= 2:
            a("创建火车(每设备一辆)").triggered.connect(
                lambda checked=False, ids=sel_ids: self.trains_batch_created.emit(ids)
            )
        elif _TRAIN_PHYSICS_UI and len(sel_ids) == 1:
            a("创建火车").triggered.connect(
                lambda checked=False, ids=sel_ids: self.trains_batch_created.emit(ids)
            )
        menu.exec(gp)

    def _reorder(self, mode: str):
        if mode == "clear":
            self._selected_ids.clear()
            self.selection_changed.emit([])
            self.update()
            return
        if mode == "reverse_all":
            self._ordered_ids = list(reversed(self._ordered_ids))
            self._build_path_cache()
            self.route_order_changed.emit(list(self._ordered_ids))
            self.update()
            return
        if not self._selected_ids:
            return
        sel = [f for f in self._ordered_ids if f in self._selected_ids]
        rem = [f for f in self._ordered_ids if f not in self._selected_ids]
        if mode == "front":
            self._ordered_ids = sel + rem
        elif mode == "back":
            self._ordered_ids = rem + sel
        elif mode == "reverse":
            if len(sel) < 2:
                return
            rev = list(reversed(sel))
            self._ordered_ids = [rev.pop(0) if f in self._selected_ids else f for f in self._ordered_ids]
        elif mode == "sort_x":
            sel.sort(key=lambda fid: float(self._dev_by_id.get(fid, {}).get("x", 0.0)))
            self._ordered_ids = [sel.pop(0) if f in self._selected_ids else f for f in self._ordered_ids]
        self._build_path_cache()
        self.route_order_changed.emit(list(self._ordered_ids))
        self.update()
