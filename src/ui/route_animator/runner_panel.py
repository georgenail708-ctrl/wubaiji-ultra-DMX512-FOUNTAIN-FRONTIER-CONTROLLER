# -*- coding: utf-8 -*-
"""RouteRunnerPanel — 「跑动编排」面板 v2（游戏化 + 完整交互）。

v2 重做(规格 ai-memory/_audit_r1/_route_runner_v2_spec.md): 轨道=设备点吸附链
+Chaikin 平滑; 火车=顶视角车厢块+覆盖圈; 道岔=节点式命令节点; 分页=每页
独立场景。引擎层直接用 RouteRunnerEngine v2(图模型+车厢物理+道岔语义), 面板只留
渲染元数据, 仿真/覆盖判定/自交绕圈标记全权交给真引擎。
"""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from bisect import bisect_left, bisect_right
from typing import Dict, List, Optional, Tuple

import numpy as np

from PyQt6.QtCore import Qt, QByteArray, QMimeData, QPointF, QRectF, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QFont, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
    QHBoxLayout, QLabel, QListWidgetItem, QSizePolicy, QTreeWidgetItem,
    QMenu, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ..hidpi import hidpi_pixmap, logical_size
from ..canvas_materials import tone
from .canvas import RouteCanvas
from .grid_build import SnapGrid
from .runner_bus_scope import build_route_nodes   # R141 缺陷⑩：逐列车覆盖集归集
from .runner_scene_store import (close_panel, drop_switch, drop_tracks,
                                export_scene_state, persist_saved_scene,
                                rebuild_page_combo, snapshot_route_undo,
                                sync_after_project_swap)
from .runner_lifecycle import discard_project_runtime, hide_view, refresh_frame_ui
from .runner_envelope import (advance_envelopes, finish_scene_restore,
                              envelopes_to_scene)
from .runner_audio_mod import (advance_audio_modulation, apply_scene_config,
                              config_to_scene, open_audio_mod_dialog)
from .runner_envelope_strip import attach_envelope_strip
from . import runner_audio_mod as _audio_mod
from .runner_chain_edit import inject_chain_edit_toggle, sync_chain_edit
from .runner_hud_overview import build_overview_widget
from .runner_fleet_panel import build_fleet_panel
from .runner_smooth_opts import (apply_smooth_profile, inject_smooth_row,
                                 remember_raw_chain, smooth_chain,
                                 smooth_strength_label)
from .runner_train_state import (card_param_specs, restore_train_state,
                                 snapshot_train_state)
from ..effect_engine import get_route_mask        # R130 上报门槛（无遮罩不上报）
from ..widget_kit import NumSlider, PanelSection, scope_qss
from ..qascade_theme import (
    ACTIVE_ORANGE,
    BG_BOX,
    BG_EDITOR,
    BG_PANEL,
    BG_PANEL_SUB,
    BRIGHT_ERROR,
    BRIGHT_SUCCESS,
    BORDER_NORMAL,
    CANVAS_OUTPUT_BG,
    CANVAS_OUTPUT_IDLE_BORDER,
    CANVAS_REVISION,
    FOCUS_BLUE,
    SELECT_BLUE,
    SEP_LINE,
    TEXT_DISABLED,
    TEXT_PRIMARY,
    TEXT_SELECTED_FG,
    TEXT_SECONDARY,
)
from ..output_surface_tokens import (
    OUTPUT_TEXT_PRIMARY as _OUTPUT_FG, OUTPUT_TEXT_SECONDARY as _OUTPUT_FG_DIM,
    OUTPUT_BG_EDITOR as _OUTPUT_PANEL, OUTPUT_BG_BOX as _OUTPUT_ELEV,
)
from ...engine.route_animator.runner_engine import FrameResult, RouteRunnerEngine

try:
    from ..main_icon_library import make_main_icon as _make_icon
except ImportError:
    _make_icon = None

logger = logging.getLogger(__name__)

#: 调度台用：本面板实例上报过的窗格身份键(id(panel) → set); 仅在窗格减少时删消失键, 不误删其它面板/实例。

# 页面色板短名(统一指向 qascade_theme 令牌, 减少全文改动面):
_BG = CANVAS_OUTPUT_BG     # 跑动场域轻暖底，普通工具区仍用浅色角色
_PANEL = BG_EDITOR         # 工具行/分区基底
_ELEV = BG_BOX             # 卡片/节点块面(节点式深面)
_BORDER = BORDER_NORMAL
_FG = TEXT_PRIMARY
_FG_DIM = TEXT_SECONDARY
_PRIMARY = SELECT_BLUE      # 选中/主操作填充
_GREEN = BRIGHT_SUCCESS     # 播放/成功语义(hue 保留)
_AMBER = "#E6B455"          # BRIGHT_WARN(hue 保留)
_RED = BRIGHT_ERROR

# 火车变体色(R54): 低饱和家族, hue 与旧六色一一对应; #FFA028=active_orange 令牌。
_TRAIN_PALETTE = ["#E36855", "#FFA028", "#56B65B", "#6FA8DC", "#9B6DD6", "#C97BA9"]
_TIMER_MS = 33
_STATIC_PAD = 48
_MAX_CACHE = 4096
_SMOOTH_K = 0.45  # 帧插值收敛系数

_SWITCH_LABELS = {"split": "分叉", "merge": "合并", "uncouple": "解挂", "couple": "连挂"}
# 道岔四类功能色: 蓝/绿/红对齐 主题令牌家族; couple 紫无对应令牌, 保 hue 降饱和(#C084FC → #9B6DD6)。
_SWITCH_COLORS = {"split": FOCUS_BLUE, "merge": BRIGHT_SUCCESS,
                  "uncouple": BRIGHT_ERROR, "couple": "#9B6DD6"}
_SWITCH_MIME = "application/x-qascade-switch"

_TRAIN_MODE_LABELS = {
    "loop_forward": "循环正跑", "loop_reverse": "循环反跑",
    "bounce": "返程", "one_way_forward": "单程正跑",
    "one_way_reverse": "单程反跑",
}

# 通用输入框/滑杆/下拉外观已由全局 GLOBAL_QSS 覆盖(restyle_spec §3), 仅保留布局特化与语义色。


# ── 几何工具(轨道折线) ──────────────────────────────────────────────
def _project_of(project_manager):
    """ProjectManager 上的当前工程对象（坏引用安全）。"""
    if project_manager is None:
        return None
    try:
        return (getattr(project_manager, "project", None)
                or getattr(project_manager, "_project", None))
    except Exception:
        return None


def _project_identity(project_manager) -> tuple:
    """工程身份指纹（换工程 → 调用方据此清空页面场景/存档槽）。

    ★ 为什么不用对象 id 或 ProjectManager id：
      · 新建工程（`MainWindow._new_project`）只替换 `pm._project`，Manager 是同一个；
      · undo/redo 的域合并（`ProjectManager._merged_project`）会**重建** Project 对象，
        用对象 id 判会把"同一工程"误判成换工程、清掉用户内存里还没保存的场景。
      `created_at` / `name` 随工程序列化往返（`to_dict` / `_from_dict` 都带），
      撤销重建时不变，只有真正新建/打开另一个工程才会变。
    """
    proj = _project_of(project_manager)
    if proj is None:
        return ()
    try:
        return (str(getattr(proj, "created_at", "") or ""),
                str(getattr(proj, "name", "") or ""))
    except Exception:
        return ()


def _polyline_cumlen(path: List[Tuple[float, float]]) -> List[float]:
    n = len(path)
    if n <= 128:
        cum = [0.0]
        for i in range(1, n):
            dx = path[i][0] - path[i - 1][0]
            dy = path[i][1] - path[i - 1][1]
            cum.append(cum[-1] + math.hypot(dx, dy))
        return cum
    arr = np.asarray(path, dtype=np.float64)
    dx = arr[1:, 0] - arr[:-1, 0]
    dy = arr[1:, 1] - arr[:-1, 1]
    seg = np.sqrt(dx * dx + dy * dy)
    return [0.0] + np.cumsum(seg).tolist()


def _point_at_arclen(s: float, path: List[Tuple[float, float]],
                     cum: List[float]) -> Tuple[float, float]:
    if not path:
        return (0.0, 0.0)
    if len(path) == 1 or cum[-1] <= 0.0:
        return path[0]
    s = max(0.0, min(s, cum[-1]))
    i = bisect_left(cum, s) - 1
    if i < 0:
        i = 0
    if i >= len(path) - 1:
        i = len(path) - 2
    seg = cum[i + 1] - cum[i]
    t = (s - cum[i]) / seg if seg > 1e-9 else 0.0
    ax, ay = path[i]
    bx, by = path[i + 1]
    return (ax + (bx - ax) * t, ay + (by - ay) * t)


def _tangent_at_arclen(s: float, path: List[Tuple[float, float]],
                       cum: List[float]) -> float:
    if not path or cum[-1] <= 0.0:
        return 0.0
    s = max(0.0, min(s, cum[-1]))
    i = bisect_left(cum, s) - 1
    if i < 0:
        i = 0
    if i >= len(path) - 1:
        i = len(path) - 2
    ax, ay = path[i]
    bx, by = path[i + 1]
    return math.atan2(by - ay, bx - ax)


def _offset_polyline(points: List[Tuple[float, float]],
                     gap: float) -> List[Tuple[float, float]]:
    """沿法向偏移折线(双轨渲染), 交角处取平均法向。"""
    n = len(points)
    if n < 2:
        return list(points)
    normals: List[Tuple[float, float]] = []
    for i in range(n - 1):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        length = math.hypot(dx, dy) or 1.0
        normals.append((-dy / length, dx / length))
    out: List[Tuple[float, float]] = [
        (points[0][0] + normals[0][0] * gap, points[0][1] + normals[0][1] * gap)]
    for i in range(1, n - 1):
        nx = normals[i - 1][0] + normals[i][0]
        ny = normals[i - 1][1] + normals[i][1]
        length = math.hypot(nx, ny) or 1.0
        out.append((points[i][0] + nx / length * gap, points[i][1] + ny / length * gap))
    out.append((points[-1][0] + normals[-1][0] * gap,
                points[-1][1] + normals[-1][1] * gap))
    return out


def _median_nn_distance(pts: List[Tuple[float, float]]) -> float:
    """设备最近邻间距中位数(车长=0 时车厢间距回退)。"""
    if len(pts) < 2:
        return 40.0
    if len(pts) > 500:
        pts = pts[::max(1, len(pts) // 500)][:500]
    nn: List[float] = []
    for i, (x, y) in enumerate(pts):
        best: Optional[float] = None
        for j, (ox, oy) in enumerate(pts):
            if i == j:
                continue
            d = math.hypot(x - ox, y - oy)
            if best is None or d < best:
                best = d
        if best is not None and best > 1e-6:
            nn.append(best)
    if not nn:
        return 40.0
    nn.sort()
    return nn[len(nn) // 2]


# ── 游戏画布(叠加层) ────────────────────────────────────────────────
class _SwitchBlock(QFrame):
    """道岔条上可拖拽的命令块(节点式方框外观)。"""

    _KIND_TIPS = {
        "split": "分叉: 左键点击道岔实时切换方向, 决定从分道方向来的火车走哪条路",
        "merge": "合并: 两支车列汇入同一轨道(保序)",
        "uncouple": "解挂: 检查点, 火车通过后自动平分车厢, 前半走主线/选定方向, 后半走支线",
        "couple": "连挂: 检查点, 圆形半径内同轨道同方向最近两列自动追尾链接",
    }

    def __init__(self, kind: str) -> None:
        super().__init__()
        self._kind = kind
        self._drag_start: Optional[QPointF] = None
        label = _SWITCH_LABELS.get(kind, kind)
        color = _SWITCH_COLORS.get(kind, TEXT_SECONDARY)
        hover_bg = QColor(_ELEV).lighter(115).name()
        self.setFixedSize(92, 34)
        self.setToolTip(self._KIND_TIPS.get(kind, f"拖到轨道附近放置「{label}」道岔"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 节点式骨骼: 深色方框 + 左侧 4px 功能色竖条 + 圆角; 边框中性细线(§10-5), hover 仅提亮面。
        self.setStyleSheet(
            f"QFrame{{background:{_ELEV};border:1px solid rgba(255,255,255,26);"
            f"border-left:4px solid {color};border-radius:4px;}}"
            f"QFrame:hover{{background:{hover_bg};}}"
            f"QLabel{{color:{_FG};font-size:11px;background:transparent;}}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(5)
        lay.addWidget(QLabel(label))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_start is not None:
            if (event.position() - self._drag_start).manhattanLength() > QApplication.startDragDistance():
                self._start_drag()
                self._drag_start = None
                return
        super().mouseMoveEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_SWITCH_MIME, QByteArray(self._kind.encode("utf-8")))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class _GameCanvas(RouteCanvas):
    """游戏化动态层: 双轨+枕木+方向箭头(静态层) / 火车块+覆盖圈(动态层) /
    道岔节点(屏幕层) / 点设备吸附链绘制 / 道岔拖放吸附。"""

    track_chain_drawn = pyqtSignal(object)   # List[Tuple[float,float]] 吸附链
    switch_dropped = pyqtSignal(str, str, float, dict)  # kind, track_id, s, params
    switch_direction_requested = pyqtSignal(str, int)  # node_id, direction
    switch_delete_requested = pyqtSignal(str)
    chain_hint = pyqtSignal(str)               # 画轨道时的状态栏提示(闭合/吸附)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks: Dict[str, dict] = {}
        self._switches: Dict[str, dict] = {}
        self._coverage_ids: set = set()
        self._smooth_cars: Dict[str, List[Tuple[float, float]]] = {}
        self._track_draw_mode = False
        self._chain_points: List[Tuple[float, float]] = []
        self._switch_preview: Optional[dict] = None
        self._draw_snap_fid: Optional[str] = None
        self._near_start = False
        self._draw_dragging = False   # 画轨道: 左键按住拖扫中
        self._chain_anchor_node: Optional[str] = None  # 链起点=道岔节点(从节点出发分岔)
        self._chain_end_node: Optional[str] = None     # 链终点=道岔节点(画进节点)
        self._chain_fids: set = set()  # 链上设备 id(拖扫去重)
        self._device_path_cache: Optional[QPainterPath] = None
        self._device_path_key: Optional[tuple] = None
        self._world_bbox_cache: Optional[tuple] = None
        self._static_world_scale = 1.0
        self._static_rebuild_pending = False
        self._static_rebuild_timer = QTimer(self)
        self._static_rebuild_timer.setSingleShot(True)
        self._static_rebuild_timer.setInterval(50)
        self._static_rebuild_timer.timeout.connect(self._rebuild_static_now)
        self.setAcceptDrops(True)
        self._snap = SnapGrid()   # 画轨道网格吸附辅助(非模态, 关闭零开销)
        for tmr in (self._pulse_timer, self._interp_timer, self._snapshot_flash_timer):
            try:
                tmr.stop()
            except Exception:
                pass

    # ── 放大镜渲染(世界尺寸: 缩放即整幅同步放大) ──────────────────────
    def set_devices(self, devices) -> None:
        super().set_devices(devices)
        self._world_bbox_cache = None
        # 世界基准单位 = 设备最近邻间距中位数, 一切装饰尺寸按它等比放大
        self._world_tile = _median_nn_distance(
            [(float(d.get("x", 0.0)), float(d.get("y", 0.0))) for d in (devices or [])])

    def set_device_positions(self, positions) -> None:
        super().set_device_positions(positions)
        self._world_bbox_cache = None

    def _wpen(self, base_world: float) -> float:
        """笔宽(世界单位): 随缩放同步缩放, 屏幕下限 0.8px 保可读。"""
        return max(float(base_world), 0.8 / max(0.005, float(self._view_scale)))

    def _wfont(self, frac: float) -> QFont:
        return QFont("Microsoft YaHei", int(max(6.0, self._world_tile * frac)))

    # ── 场景推入 ────────────────────────────────────────────────────────
    @staticmethod
    def _simplify_loop_display(points: List[Tuple[float, float]]
                               ) -> Tuple[List[Tuple[float, float]], int]:
        """检测完全重复的闭环周期，显示只保留一个基圈 + ×N，避免绕很多圈时
        同一圆上叠满杂乱线段。仅影响显示；引擎仍用完整 points 做激活判定。"""
        pts = [tuple(p) for p in (points or [])]
        if len(pts) < 6 or pts[0] != pts[-1]:
            return pts, 1
        core = pts[:-1]
        n = len(core)
        for p in range(1, n // 2 + 1):
            if n % p != 0:
                continue
            ok = True
            for i in range(p, n):
                if (abs(core[i][0] - core[i - p][0]) > 1e-3
                        or abs(core[i][1] - core[i - p][1]) > 1e-3):
                    ok = False
                    break
            if ok:
                return core[:p] + [core[0]], n // p
        return pts, 1

    def set_tracks(self, tracks: Dict[str, dict]) -> None:
        """登记全部可见轨道(静态层); dict: track_id -> {points, color, loop_markers}。"""
        new: Dict[str, dict] = {}
        for tid, t in (tracks or {}).items():
            pts = [tuple(p) for p in (t.get("points") or [])]
            if len(pts) < 2:
                continue
            cum = _polyline_cumlen(pts)
            display_pts, display_lap = self._simplify_loop_display(pts)
            new[str(tid)] = {
                "id": str(tid), "points": pts, "cum": cum,
                "display_points": display_pts,
                "display_lap_count": display_lap,
                "color": str(t.get("color") or "#8fd0ff"),
                "loop_markers": [dict(m) for m in (t.get("loop_markers") or [])],
                "lap_count": max(1, int(t.get("lap_count") or 1)),
                "gaps": [tuple(g) for g in (t.get("gaps") or [])],
            }
        self._tracks = new
        self._static_cache = None
        self.update()

    def set_switches(self, switches: Dict[str, dict]) -> None:
        """登记已放置道岔节点(屏幕层); dict: node_id -> {kind, pos, track_id, s}。"""
        self._switches = {str(k): dict(v) for k, v in (switches or {}).items()}
        self.update()

    def set_coverage_ids(self, ids) -> None:
        s = {str(i) for i in (ids or [])}
        if s != self._coverage_ids:
            self._coverage_ids = s
            self.update()

    def set_train_visuals(self, visuals: List[dict]) -> None:
        """统一 _train_visuals 契约(单一来源, 删除 _game_visuals 冗余)。"""
        new_visuals: Dict[str, dict] = {}
        new_smooth: Dict[str, List[Tuple[float, float]]] = {}
        for v in visuals or []:
            tid = str(v.get("train_id") or "")
            if not tid:
                continue
            raw_cars = v.get("car_positions") or []
            cars = [c if isinstance(c, tuple) else tuple(c) for c in raw_cars]
            prev = self._smooth_cars.get(tid)
            if prev is not None and len(prev) == len(cars):
                cars = [self._lerp(prev[i], cars[i], _SMOOTH_K) for i in range(len(cars))]
            new_smooth[tid] = cars
            raw_angles = v.get("car_angles") or []
            new_visuals[tid] = {
                "color": str(v.get("color") or ACTIVE_ORANGE),
                "car_positions": cars,
                "car_angles": [a if isinstance(a, float) else float(a) for a in raw_angles],
                "radius": float(v.get("radius") or 0.0),
                "show_radius": bool(v.get("show_radius", True)),
                "car_length": float(v.get("car_length") or 0.0),
            }
        self._train_visuals = new_visuals
        self._smooth_cars = new_smooth
        self._pulse_phase = (self._pulse_phase + 0.18) % 100.0
        self.update()

    @staticmethod
    def _lerp(a, b, t: float) -> Tuple[float, float]:
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    # ── 点设备吸附链绘制 ───────────────────────────────────────────────
    def set_track_draw_mode(self, enabled: bool) -> None:
        self._track_draw_mode = bool(enabled)
        self._chain_points = []
        self._chain_fids = set()
        self._draw_dragging = False
        self._chain_anchor_node = None
        self._chain_end_node = None
        self._near_start = False
        self._snap.clear_pointer()
        self.setCursor(Qt.CursorShape.CrossCursor if self._track_draw_mode
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    def _reset_chain_state(self) -> None:
        self._chain_points = []
        self._chain_fids = set()
        self._draw_dragging = False
        self._chain_anchor_node = None
        self._chain_end_node = None
        self._near_start = False
        self._track_draw_mode = False
        self._snap.clear_pointer()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _finish_chain(self) -> None:
        pts = list(self._chain_points)
        if (self._near_start and len(pts) >= 3
                and pts[-1] != pts[0]):
            # 拖扫回到起点附近再双击/回车 = 封口成环(闭环可无限绕圈)
            pts.append(pts[0])
        start_node = self._chain_anchor_node
        end_node = self._chain_end_node
        self._reset_chain_state()
        if len(pts) >= 2:
            self.track_chain_drawn.emit(
                {"points": pts, "start_node": start_node, "end_node": end_node})

    def _cancel_chain(self) -> None:
        self._reset_chain_state()

    def mousePressEvent(self, event) -> None:
        if (not self._track_draw_mode and event.button() == Qt.MouseButton.LeftButton):
            # 火车游戏式道岔: 左键点击分叉节点 = 实时切换下一方向
            wp = self._s2w(event.position())
            node_hit = self._switch_at(wp)
            if node_hit is not None:
                node = self._switches.get(node_hit) or {}
                if (str(node.get("kind", "")) == "split"
                        and int(node.get("out_count", 1)) > 1):
                    cur = int(node.get("direction", 0))
                    nxt = (cur + 1) % int(node.get("out_count", 1))
                    self.switch_direction_requested.emit(node_hit, nxt)
                    event.accept()
                    return
        if self._track_draw_mode and event.button() == Qt.MouseButton.LeftButton:
            # 按下即拖扫: 按住扫过设备自动入链(快速画法); 单击点选成链并存。
            self._draw_dragging = True
            wp = self._s2w(event.position())
            node_hit = self._switch_at(wp)
            if not self._chain_points and node_hit is not None:
                # 链起点 = 道岔节点: 从分叉/解挂节点出发画支线
                node = self._switches.get(node_hit) or {}
                pos = node.get("pos", (0.0, 0.0))
                self._chain_points.append((float(pos[0]), float(pos[1])))
                self._chain_anchor_node = node_hit
                self.chain_hint.emit("已从道岔节点出发, 继续点/扫设备画支线, 双击完成")
                self.update()
                event.accept()
                return
            if self._chain_points and node_hit is not None:
                # 链画进道岔节点: 支线汇入/连挂接线, 立即完成
                node = self._switches.get(node_hit) or {}
                pos = node.get("pos", (0.0, 0.0))
                if self._chain_points[-1] != (float(pos[0]), float(pos[1])):
                    self._chain_points.append((float(pos[0]), float(pos[1])))
                self._chain_end_node = node_hit
                self._finish_chain()
                event.accept()
                return
            fid = self._device_at(self._snap_wp(wp))
            if fid is not None:
                self._chain_fids.add(fid)
                dev = self._dev_by_id[fid]
                pt = (float(dev.get("x", 0.0)), float(dev.get("y", 0.0)))
                if not self._chain_points or self._chain_points[-1] != pt:
                    self._chain_points.append(pt)
                self.update()
            event.accept()
            return
        # 右键不转发给基类：让 contextMenuEvent 统一处理道岔菜单，避免基类先弹出通用菜单。
        if event.button() == Qt.MouseButton.RightButton and not self._track_draw_mode:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._track_draw_mode:
            wp = self._s2w(event.position())
            node_hit = self._switch_at(wp) if self._chain_points else None
            if node_hit is not None:
                node = self._switches.get(node_hit) or {}
                pos = node.get("pos", (0.0, 0.0))
                if self._chain_points[-1] != (float(pos[0]), float(pos[1])):
                    self._chain_points.append((float(pos[0]), float(pos[1])))
                self._chain_end_node = node_hit
            self._finish_chain()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._track_draw_mode and event.button() == Qt.MouseButton.LeftButton:
            self._draw_dragging = False   # 结束拖扫, 双击/回车/右键仍完成链
            event.accept()
            return
        if self._track_draw_mode and event.button() == Qt.MouseButton.RightButton:
            self._finish_chain()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _snap_hover_update(self, wp, shift_down) -> None:
        """画轨光标移动：刷新吸附落点/Shift 挂起态（变化才重绘）。"""
        if self._snap.update_pointer(wp.x(), wp.y(), bool(shift_down)):
            self.update()

    def _snap_wp(self, wp):
        """画轨命中点按吸附网格对齐（辅助关闭/Shift 挂起时原样返回）。"""
        if self._snap.snapping(self):
            sp = self._snap.snap_point(wp.x(), wp.y())
            return QPointF(sp[0], sp[1])
        return wp

    def mouseMoveEvent(self, event) -> None:
        if self._track_draw_mode:
            wp = self._s2w(event.position())
            self._snap_hover_update(wp, event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            fid = self._device_at(self._snap_wp(wp))
            if fid != self._draw_snap_fid:
                self._draw_snap_fid = fid
                self.update()
            # 拖扫快速画法: 按住扫过的设备自动追加进链; 可重复路过(绕圈), 仅防原地重复追加。
            if (self._draw_dragging
                    and (event.buttons() & Qt.MouseButton.LeftButton)
                    and fid is not None):
                dev = self._dev_by_id[fid]
                pt = (float(dev.get("x", 0.0)), float(dev.get("y", 0.0)))
                if not self._chain_points or self._chain_points[-1] != pt:
                    self._chain_points.append(pt)
                    self.update()
            near_start = False
            if len(self._chain_points) >= 3:
                sx, sy = self._chain_points[0]
                d = math.hypot(wp.x() - sx, wp.y() - sy)
                near_start = d < self._px(12.0)
            if near_start != self._near_start:
                self._near_start = near_start
                self.chain_hint.emit(
                    "松开/双击闭合: 轨道首尾相连成环" if near_start
                    else "点击或按住扫过设备连成链, 双击/回车完成")
                self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._track_draw_mode and event.key() == Qt.Key.Key_Shift:
            self._snap.set_suspended(True)   # Shift 临时禁用吸附(画自由点)
            self.update()
        if self._track_draw_mode and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish_chain()
            event.accept()
            return
        if self._track_draw_mode and event.key() == Qt.Key.Key_Escape:
            self._cancel_chain()
            self.chain_hint.emit("已取消画轨道")
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if (self._track_draw_mode and event.key() == Qt.Key.Key_Shift):
            self._snap.set_suspended(False)   # Shift 松开：按最近悬停位恢复吸附
            self.update()
        super().keyReleaseEvent(event)

    # ── 道岔拖放 ───────────────────────────────────────────────────────
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_SWITCH_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(_SWITCH_MIME):
            kind = bytes(event.mimeData().data(_SWITCH_MIME)).decode("utf-8", "ignore")
            wp = self._s2w(event.position())
            self._switch_preview = self._snap_switch_target(wp, kind)
            self.update()
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(_SWITCH_MIME):
            kind = bytes(event.mimeData().data(_SWITCH_MIME)).decode("utf-8", "ignore")
            wp = self._s2w(event.position())
            hit = self._snap_switch_target(wp, kind)
            self._switch_preview = None
            self.update()
            if hit is not None:
                self.switch_dropped.emit(
                    kind, str(hit.get("track_id", "")), float(hit.get("s", 0.0)),
                    dict(hit.get("params") or {}))
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._switch_preview = None
        self.update()
        super().dragLeaveEvent(event)

    def contextMenuEvent(self, event) -> None:
        wp = self._s2w(event.pos())
        node_id = self._switch_at(wp)
        if node_id is not None:
            menu = QMenu(self)
            dir_acts = []
            node = self._switches.get(node_id) or {}
            if str(node.get("kind", "")) == "split" and int(node.get("out_count", 1)) > 1:
                labels = ["方向→主干"]
                labels += [f"方向→支线{i}" for i in range(1, int(node.get("out_count", 1)))]
                for i, lbl in enumerate(labels):
                    a = menu.addAction(lbl)
                    a.setCheckable(True)
                    a.setChecked(int(node.get("direction", 0)) == i)
                    dir_acts.append((a, i))
            del_act = menu.addAction("删除道岔")
            chosen = menu.exec(event.globalPosition().toPoint())
            for a, i in dir_acts:
                if chosen == a:
                    self.switch_direction_requested.emit(node_id, i)
                    return
            if chosen == del_act:
                self.switch_delete_requested.emit(node_id)
            return
        super().contextMenuEvent(event)

    def _nearest_track_point(self, wp) -> Optional[Tuple[str, float]]:
        tol = self._px(self._tile_screen_px() * 0.6 + 6.0)
        best: Optional[Tuple[str, float]] = None
        best_d = tol
        for track in self._tracks.values():
            pts = track.get("points") or []
            cum = track.get("cum") or []
            if len(pts) < 2:
                continue
            for i in range(len(pts) - 1):
                ax, ay = pts[i]
                bx, by = pts[i + 1]
                dx, dy = bx - ax, by - ay
                seg2 = dx * dx + dy * dy
                if seg2 <= 0.0:
                    t = 0.0
                else:
                    t = max(0.0, min(1.0, ((wp.x() - ax) * dx + (wp.y() - ay) * dy) / seg2))
                qx = ax + t * dx
                qy = ay + t * dy
                d = math.hypot(wp.x() - qx, wp.y() - qy)
                if d <= best_d:
                    best_d = d
                    seg_len = math.sqrt(seg2) if seg2 > 0.0 else 0.0
                    s = (cum[i] if i < len(cum) else 0.0) + t * seg_len
                    best = (str(track.get("id", "")), s)
        return best

    def _snap_switch_target(self, wp, kind: str) -> Optional[dict]:
        """道岔吸附分级(优先级从高到低):

        1 已存在的道岔节点(换类型/顶替)
        2 车厢边界(nose_s - j*car_length, j=1..n-1) — uncouple/couple 用
        3 轨道端点 / 自交点(绕圈口)
        4 轨道任意位置(原 _nearest_track_point 语义)
        """
        node_id = self._switch_at(wp)
        if node_id is not None:
            node = self._switches.get(node_id) or {}
            node_kind = str(node.get("kind", ""))
            stacked = node.get("stacked") or []
            if node_kind != kind and kind not in stacked:
                label = f"叠加「{_SWITCH_LABELS.get(kind, kind)}」"
            else:
                label = "替换道岔"
            return {"track_id": str(node.get("track_id", "")),
                    "s": float(node.get("s", 0.0)), "kind": kind,
                    "snap": "node", "label": label,
                    "params": {"replace_node": node_id}}
        if kind in ("uncouple", "couple"):
            hit = self._snap_car_boundary(wp)
            if hit is not None:
                track_id, s, j, label = hit
                return {"track_id": track_id, "s": s, "kind": kind,
                        "snap": "car", "label": label,
                        "params": {"car_index": j}}
        hit = self._snap_track_feature(wp)
        if hit is not None:
            track_id, s, label = hit
            return {"track_id": track_id, "s": s, "kind": kind,
                    "snap": "end", "label": label, "params": {}}
        hit = self._nearest_track_point(wp)
        if hit is not None:
            track_id, s = hit
            return {"track_id": track_id, "s": s, "kind": kind,
                    "snap": "free", "label": "放置道岔", "params": {}}
        return None

    def _project_to_track(self, pt) -> Optional[Tuple[str, float]]:
        """投影点到各轨道, 返回 (track_id, s)。

        与 _nearest_track_point 的区别: 距离平局时优先「投影落在轨道内部」的轨道
        (交岔点同时落在主干内部与支线端点, 应归主干 = 火车实际所在的轨道)。
        """
        tol = self._px(12.0)
        best: Optional[Tuple[str, float]] = None
        best_d = tol
        best_endpoint = True
        for track in self._tracks.values():
            pts = track.get("points") or []
            cum = track.get("cum") or []
            if len(pts) < 2:
                continue
            total = cum[-1] if cum else 0.0
            for i in range(len(pts) - 1):
                ax, ay = pts[i]
                bx, by = pts[i + 1]
                dx, dy = bx - ax, by - ay
                seg2 = dx * dx + dy * dy
                if seg2 <= 0.0:
                    t = 0.0
                else:
                    t = max(0.0, min(1.0, ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / seg2))
                qx = ax + t * dx
                qy = ay + t * dy
                d = math.hypot(pt[0] - qx, pt[1] - qy)
                seg_len = math.sqrt(seg2) if seg2 > 0.0 else 0.0
                s = (cum[i] if i < len(cum) else 0.0) + t * seg_len
                endpoint = (s <= 1e-9 or s >= total - 1e-9)
                better = (d < best_d - 1e-12 or
                          (abs(d - best_d) <= 1e-12 and best_endpoint and not endpoint))
                if d <= best_d and better:
                    best_d = d
                    best = (str(track.get("id", "")), s)
                    best_endpoint = endpoint
        return best

    def _snap_car_boundary(self, wp) -> Optional[Tuple[str, float, int, str]]:
        """车厢边界吸附: 边界 j = 车 j-1 与车 j 中心的中点(= nose_s - j*car_length)。"""
        tol = self._px(10.0)
        best: Optional[Tuple[str, float, int, str]] = None
        best_d = tol
        for v in self._train_visuals.values():
            cars = [tuple(c) for c in (v.get("car_positions") or [])]
            for j in range(1, len(cars)):
                mx = (cars[j - 1][0] + cars[j][0]) / 2.0
                my = (cars[j - 1][1] + cars[j][1]) / 2.0
                d = math.hypot(wp.x() - mx, wp.y() - my)
                if d > best_d:
                    continue
                hit = self._project_to_track((mx, my))
                if hit is None:
                    continue
                best_d = d
                best = (hit[0], hit[1], j, f"解挂点 #{j}")
        return best

    def _snap_track_feature(self, wp) -> Optional[Tuple[str, float, str]]:
        """轨道端点 / 自交点(绕圈口)吸附。"""
        tol = self._px(12.0)
        best: Optional[Tuple[str, float, str]] = None
        best_d = tol
        for track in self._tracks.values():
            pts = track.get("points") or []
            cum = track.get("cum") or []
            if len(pts) < 2:
                continue
            for cand_s, cand_pt in ((0.0, pts[0]), (cum[-1] if cum else 0.0, pts[-1])):
                d = math.hypot(wp.x() - cand_pt[0], wp.y() - cand_pt[1])
                if d <= best_d:
                    best_d = d
                    best = (str(track.get("id", "")), cand_s, "端点")
            for m in track.get("loop_markers") or []:
                mx = float(m.get("x", 0.0))
                my = float(m.get("y", 0.0))
                d = math.hypot(wp.x() - mx, wp.y() - my)
                if d <= best_d:
                    best_d = d
                    best = (str(track.get("id", "")), float(m.get("s_a", 0.0)), "绕圈口")
        return best

    def _switch_at(self, wp) -> Optional[str]:
        tol = max(self._px(10.0), self._world_tile * 0.75)
        best: Optional[str] = None
        best_d = tol
        for node_id, node in self._switches.items():
            x, y = node.get("pos", (0.0, 0.0))
            d = math.hypot(wp.x() - x, wp.y() - (y - self._world_tile * 0.55))
            if d <= best_d:
                best_d = d
                best = str(node_id)
        return best

    # ── 视图辅助 ────────────────────────────────────────────────────────
    def _w2s(self, x: float, y: float) -> Tuple[float, float]:
        s = max(0.005, self._view_scale)
        return (self._view_offset.x() + x * s, self._view_offset.y() + y * s)

    # ── paintEvent(R2: 无设备且无视觉物才早退) ─────────────────────────
    def paintEvent(self, event) -> None:
        has_content = (bool(self._dev_by_id) or bool(self._train_visuals)
                       or bool(self._tracks) or bool(self._switches)
                       or self._snap.enabled)
        if not has_content:
            p = QPainter(self)
            p.fillRect(self.rect(), tone(_BG))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._draw_empty_state(p)
            p.end()
            return
        self._ensure_static_cache()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setClipRect(event.region().boundingRect())
        p.fillRect(self.rect(), tone(_BG))
        p.save()
        p.setOpacity(0.22)
        self._draw_grid(p)
        p.restore()
        if self._static_cache is not None:
            p.save()
            if self._static_is_world:
                w0, h0 = self._static_world_origin
                s = max(0.005, self._view_scale)
                p.translate(-w0 * s, -h0 * s)
                p.scale(s, s)
            else:
                p.translate(self._view_offset)
                p.scale(self._view_scale, self._view_scale)
            self._draw_host_glow(p)
            p.restore()
        if self._static_cache is not None:
            if self._static_is_world:
                w0, h0 = self._static_world_origin
                old_s = float(getattr(self, "_static_world_scale", 1.0) or 1.0)
                new_s = max(0.005, self._view_scale)
                _lsz = logical_size(self._static_cache)   # D1-1：逻辑尺寸
                pw, ph = _lsz.width(), _lsz.height()
                ox = self._view_offset.x() + w0 * new_s
                oy = self._view_offset.y() + h0 * new_s
                # 防抖期间旧缓存按当前视图缩放占位，避免连续缩放时静态层闪烁/消失。
                p.drawPixmap(QRectF(ox, oy, pw * new_s / old_s, ph * new_s / old_s),
                             self._static_cache, QRectF(self._static_cache.rect()))
            else:
                p.drawPixmap(0, 0, self._static_cache)
        p.save()
        p.translate(self._view_offset)
        p.scale(self._view_scale, self._view_scale)
        self._snap.draw_lattice(p, self)
        self._snap.draw_highlight(p, self)
        self._draw_train_visuals(p)
        self._draw_switch_nodes(p)
        self._draw_switch_preview(p)
        self._draw_chain_preview(p)
        self._draw_lap_labels(p)
        p.restore()
        self._draw_track_guide(p)
        self._draw_hud(p)
        p.end()

    # ── 静态层缓存 ──────────────────────────────────────────────────────
    def _ensure_static_cache(self) -> None:
        """防抖入口：首次/无旧缓存立即重建；缩放/平移导致的重建延后 50ms，
        期间用旧缓存按当前视图缩放占位，连续操作不阻塞 UI 线程。"""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            self._static_cache = None
            self._static_cache_key = None
            return
        s = max(0.005, self._view_scale)
        track_sig = tuple(sorted((tid, id(t["points"])) for tid, t in self._tracks.items()))
        bbox = self._world_bbox()
        if bbox is not None:
            pw = int((bbox[2] - bbox[0]) * s) + 2 * _STATIC_PAD
            ph = int((bbox[3] - bbox[1]) * s) + 2 * _STATIC_PAD
        else:
            pw = ph = 0
        if 0 < pw <= _MAX_CACHE and 0 < ph <= _MAX_CACHE:
            key = ("w", w, h, s, self._device_generation, track_sig, self.devicePixelRatioF(), CANVAS_REVISION)
        else:
            key = ("v", w, h, s, self._view_offset.x(), self._view_offset.y(),
                   self._device_generation, track_sig, self.devicePixelRatioF(), CANVAS_REVISION)
        if self._static_cache is not None and self._static_cache_key == key:
            return
        if self._static_cache is not None and not self._static_rebuild_pending:
            # 已有旧缓存：延后重建，连续缩放/平移不卡主线程。
            self._static_rebuild_pending = True
            self._static_rebuild_timer.start()
            return
        if self._static_rebuild_pending:
            return
        self._ensure_static_cache_impl(key, bbox, w, h, s)

    def _rebuild_static_now(self) -> None:
        if not self._static_rebuild_pending:
            return
        self._static_rebuild_pending = False
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        s = max(0.005, self._view_scale)
        track_sig = tuple(sorted((tid, id(t["points"])) for tid, t in self._tracks.items()))
        bbox = self._world_bbox()
        if bbox is not None:
            pw = int((bbox[2] - bbox[0]) * s) + 2 * _STATIC_PAD
            ph = int((bbox[3] - bbox[1]) * s) + 2 * _STATIC_PAD
        else:
            pw = ph = 0
        if 0 < pw <= _MAX_CACHE and 0 < ph <= _MAX_CACHE:
            key = ("w", w, h, s, self._device_generation, track_sig, self.devicePixelRatioF(), CANVAS_REVISION)
        else:
            key = ("v", w, h, s, self._view_offset.x(), self._view_offset.y(),
                   self._device_generation, track_sig, self.devicePixelRatioF(), CANVAS_REVISION)
        if self._static_cache is not None and self._static_cache_key == key:
            return
        self._ensure_static_cache_impl(key, bbox, w, h, s)
        self.update()

    def _ensure_static_cache_impl(self, key, bbox, w, h, s) -> None:
        if 0 < (int((bbox[2] - bbox[0]) * s) + 2 * _STATIC_PAD if bbox is not None else 0) <= _MAX_CACHE \
                and 0 < (int((bbox[3] - bbox[1]) * s) + 2 * _STATIC_PAD if bbox is not None else 0) <= _MAX_CACHE:
            self._build_world_cache(bbox, s)
        else:
            self._build_view_cache(w, h)
        self._static_cache_key = key

    def _world_bbox(self):
        if self._world_bbox_cache is not None:
            return self._world_bbox_cache
        if not self._dev_by_id:
            self._world_bbox_cache = None
            return None
        xs = [float(d.get("x", 0.0)) for d in self._dev_by_id.values()]
        ys = [float(d.get("y", 0.0)) for d in self._dev_by_id.values()]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        self._world_bbox_cache = bbox
        return bbox

    def _build_world_cache(self, bbox, s: float) -> None:
        min_x, min_y, max_x, max_y = bbox
        pad_w = _STATIC_PAD / s
        pad_h = _STATIC_PAD / s
        w0, h0 = min_x - pad_w, min_y - pad_h
        pw = max(1, int((max_x - min_x + 2 * pad_w) * s))
        ph = max(1, int((max_y - min_y + 2 * pad_h) * s))
        pm = hidpi_pixmap(pw, ph, self)   # D1-1：世界缓存按 DPR 栅格化
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.translate(-w0 * s, -h0 * s)
        p.scale(s, s)
        self._draw_track_path(p)
        self._draw_device_bases(p)
        p.end()
        self._static_cache = pm
        self._static_world_origin = (w0, h0)
        self._static_world_scale = s
        self._static_is_world = True

    def _build_view_cache(self, w: int, h: int) -> None:
        pm = hidpi_pixmap(w, h, self)   # C7：视图缓存按 DPR 栅格化
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.save()
        p.translate(self._view_offset)
        p.scale(self._view_scale, self._view_scale)
        self._draw_track_path(p)
        self._draw_device_bases(p)
        p.restore()
        p.end()
        self._static_cache = pm
        self._static_world_origin = (0.0, 0.0)
        self._static_is_world = False

    # ── 设备点 ──────────────────────────────────────────────────────────
    def _draw_device_bases(self, p: QPainter) -> None:
        if not self._devices:
            return
        key = (self._device_generation, self._world_tile)
        if self._device_path_key != key or self._device_path_cache is None:
            r = max(2.0, self._world_tile * 0.32)
            path = QPainterPath()
            for dev in self._devices:
                path.addEllipse(
                    QPointF(float(dev.get("x", 0.0)), float(dev.get("y", 0.0))), r, r)
            self._device_path_cache = path
            self._device_path_key = key
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tone(CANVAS_OUTPUT_IDLE_BORDER))
        p.drawPath(self._device_path_cache)

    def _draw_devices(self, p: QPainter) -> None:
        return  # 设备点已在静态层画完

    # ── 轨道渲染(双轨+枕木+方向箭头+自交绕圈标记) ─────────────────────
    def _draw_track_path(self, p: QPainter) -> None:
        for track in self._tracks.values():
            pts = track.get("points") or []
            if len(pts) < 2:
                continue
            color = QColor(str(track.get("color") or "#8fd0ff"))
            # 显示简化：完全重复的闭环只画一个基圈 + ×N，避免绕圈叠满杂乱线段。
            draw_track = dict(track)
            display_pts = track.get("display_points") or pts
            draw_track["points"] = display_pts
            draw_track["cum"] = _polyline_cumlen(display_pts)
            draw_track["lap_count"] = int(track.get("display_lap_count")
                                         or track.get("lap_count", 1))
            self._draw_rails(p, draw_track, color)
            self._draw_sleepers(p, draw_track, color)
            self._draw_direction_arrows(p, draw_track, color)
            self._draw_gap_spans(p, draw_track["points"], draw_track["cum"], draw_track)
            self._draw_self_intersections(p, track.get("loop_markers") or [], color)

    def _render_points(self, track: dict) -> tuple:
        """按当前缩放返回适合静态层绘制的轨道点（每约 0.5 像素一个点）。

        低倍率下 4000+ 全量点会拖垮静态缓存重建；这里按屏幕分辨率抽稀，
        视觉几乎不变但重建耗时大幅下降。缓存 key 绑定 points 身份与缩放。
        """
        pts = track.get("points") or []
        cum = track.get("cum") or []
        if len(pts) < 2 or not cum or cum[-1] <= 0.0:
            return pts, cum
        s = max(0.005, self._view_scale)
        key = ("render", id(pts), round(s, 6))
        cached = track.get("_render")
        if cached is not None and cached[0] == key:
            return cached[1], cached[2]
        total = float(cum[-1])
        # 目标点数：每 0.5 屏像素一个点，最多不超过原始点数。
        count = max(2, min(len(pts), int(math.ceil(total * s / 0.5)) + 2))
        if count >= len(pts):
            track["_render"] = (key, pts, cum)
            return pts, cum
        pts_np = np.asarray(pts, dtype=np.float64)
        cum_np = np.asarray(cum, dtype=np.float64)
        s_vals = np.linspace(0.0, total, count)
        idx = np.searchsorted(cum_np, s_vals, side="right") - 1
        idx = np.clip(idx, 0, len(pts_np) - 2)
        seg = cum_np[idx + 1] - cum_np[idx]
        t = np.divide(s_vals - cum_np[idx], seg,
                      out=np.zeros_like(s_vals), where=seg > 1e-12)
        x = pts_np[idx, 0] + t * (pts_np[idx + 1, 0] - pts_np[idx, 0])
        y = pts_np[idx, 1] + t * (pts_np[idx + 1, 1] - pts_np[idx, 1])
        out_pts = [(float(xi), float(yi)) for xi, yi in zip(x, y, strict=True)]
        out_cum = _polyline_cumlen(out_pts)
        track["_render"] = (key, out_pts, out_cum)
        return out_pts, out_cum

    def _track_rails(self, track: dict) -> tuple:
        """缓存双轨偏移折线；key 随几何/设备间距/缩放失效。"""
        pts, _cum = self._render_points(track)
        key = ("rails", id(track.get("points")), self._world_tile, round(self._view_scale, 6))
        cached = track.get("_rails")
        if cached is not None and track.get("_rails_key") == key:
            return cached
        gap = max(1.5, self._world_tile * 0.09)
        result = (_offset_polyline(pts, -gap), _offset_polyline(pts, gap))
        track["_rails"] = result
        track["_rails_key"] = key
        return result

    def _draw_rails(self, p: QPainter, track: dict, color: QColor) -> None:
        left, right = self._track_rails(track)
        c = QColor(color)
        c.setAlpha(210)
        pen = QPen(c, self._wpen(self._world_tile * 0.06))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for line in (left, right):
            if len(line) >= 2:
                p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in line]))

    def _track_decor(self, track: dict) -> dict:
        """预计算枕木/方向箭头装饰点，避免每次静态缓存重建都 O(点数×装饰数)。

        缓存挂在 track dict 上，key 绑定 points 对象身份与 world_tile；几何/页面
        变化时 points 列表重建或 _world_tile 改变都会自动失效。
        """
        s_scale = max(0.005, self._view_scale)
        # 装饰密度按屏幕像素自适应：缩放越小时越稀疏，避免低倍率下画数千个亚像素枕木/箭头。
        key = ("decor", id(track.get("points")), self._world_tile, round(s_scale, 6))
        cached = track.get("_decor")
        if cached is not None and cached.get("key") == key:
            return cached
        pts, cum = self._render_points(track)
        if len(pts) < 2 or not cum or cum[-1] <= 0.0:
            return {"key": key, "sleepers": [], "arrows": []}
        pts_np = np.asarray(pts, dtype=np.float64)
        cum_np = np.asarray(cum, dtype=np.float64)
        total = float(cum_np[-1])

        def _sample(step_w: float, include_end: bool) -> np.ndarray:
            if step_w <= 0.0:
                return np.array([], dtype=np.float64)
            count = int(math.ceil(total / step_w))
            s = np.arange(count, dtype=np.float64) * step_w
            if include_end:
                s = np.append(s, total)
            return s

        # 枕木：世界间距与“至少 8 像素一个”取大，低倍率不再画满全程。
        sleepers = []
        step_w = max(self._world_tile * 0.5, 8.0 / s_scale)
        half = self._world_tile * 0.20
        if step_w > 0.0 and total > 0.0:
            s_vals = _sample(step_w, True)
            idx = np.searchsorted(cum_np, s_vals, side="right") - 1
            idx = np.clip(idx, 0, len(pts_np) - 2)
            seg = cum_np[idx + 1] - cum_np[idx]
            t = np.divide(s_vals - cum_np[idx], seg,
                          out=np.zeros_like(s_vals), where=seg > 1e-12)
            x = pts_np[idx, 0] + t * (pts_np[idx + 1, 0] - pts_np[idx, 0])
            y = pts_np[idx, 1] + t * (pts_np[idx + 1, 1] - pts_np[idx, 1])
            dx = pts_np[idx + 1, 0] - pts_np[idx, 0]
            dy = pts_np[idx + 1, 1] - pts_np[idx, 1]
            norm = np.hypot(dx, dy)
            norm = np.where(norm > 1e-12, norm, 1.0)
            nx = -dy / norm
            ny = dx / norm
            for i in range(len(s_vals)):
                sleepers.append((
                    float(x[i] - nx[i] * half), float(y[i] - ny[i] * half),
                    float(x[i] + nx[i] * half), float(y[i] + ny[i] * half)))

        # 方向箭头：至少 28 像素一个，低倍率只保留主要方向提示。
        arrows = []
        step_w = max(self._world_tile * 1.8, 28.0 / s_scale)
        if step_w > 0.0 and total > 0.0:
            s_vals = _sample(step_w, False)
            idx = np.searchsorted(cum_np, s_vals, side="right") - 1
            idx = np.clip(idx, 0, len(pts_np) - 2)
            seg = cum_np[idx + 1] - cum_np[idx]
            t = np.divide(s_vals - cum_np[idx], seg,
                          out=np.zeros_like(s_vals), where=seg > 1e-12)
            x = pts_np[idx, 0] + t * (pts_np[idx + 1, 0] - pts_np[idx, 0])
            y = pts_np[idx, 1] + t * (pts_np[idx + 1, 1] - pts_np[idx, 1])
            dx = pts_np[idx + 1, 0] - pts_np[idx, 0]
            dy = pts_np[idx + 1, 1] - pts_np[idx, 1]
            ang = np.arctan2(dy, dx)
            for i in range(len(s_vals)):
                arrows.append((float(x[i]), float(y[i]), float(ang[i])))

        result = {"key": key, "sleepers": sleepers, "arrows": arrows}
        track["_decor"] = result
        return result

    def _draw_sleepers(self, p: QPainter, track: dict, color: QColor) -> None:
        decor = self._track_decor(track)
        if not decor["sleepers"]:
            return
        c = QColor(color)
        c.setAlpha(115)
        pen = QPen(c, self._wpen(self._world_tile * 0.04))
        p.setPen(pen)
        for x1, y1, x2, y2 in decor["sleepers"]:
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _draw_direction_arrows(self, p: QPainter, track: dict, color: QColor) -> None:
        decor = self._track_decor(track)
        if not decor["arrows"]:
            return
        length = self._world_tile * 0.34
        width = self._world_tile * 0.20
        c = QColor("#FFE14D")  # 方向箭头固定亮黄, 与轨道色解耦(清晰+可测)
        c.setAlpha(215)
        for x, y, a in decor["arrows"]:
            self._draw_arrow(p, x, y, a, length, width, c)

    @staticmethod
    def _draw_arrow(p: QPainter, x: float, y: float, angle: float,
                    length: float, width: float, color: QColor) -> None:
        p.save()
        p.translate(x, y)
        p.rotate(math.degrees(angle))
        path = QPainterPath()
        path.moveTo(length / 2.0, 0.0)
        path.lineTo(-length / 2.0, -width / 2.0)
        path.lineTo(-length / 2.0, width / 2.0)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)
        p.restore()

    def _draw_gap_spans(self, p: QPainter, pts, cum, track) -> None:
        """连接处(设备空余段)标记: 用暗色虚线覆盖该弧段, 提示可跳过。"""
        gaps = track.get("gaps") or []
        if not gaps:
            return
        pts, cum = self._render_points(track)
        s_scale = max(0.005, self._view_scale)
        # 只画屏幕长度 ≥8px 的连接处；低倍率下大量亚像素 gap 不画，显著减少静态层耗时。
        visible = [(a, b) for a, b in gaps if (b - a) * s_scale >= 8.0]
        if len(visible) > 32:
            step = len(visible) / 32.0
            visible = [visible[int(i * step)] for i in range(32)]
        if not visible:
            return
        gc = QColor(TEXT_DISABLED)
        gc.setAlpha(130)
        pen = QPen(gc, self._wpen(self._world_tile * 0.09), Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        for a, b in visible:
            # 只取弧段 [a,b] 内的顶点，避免每次 gap 全量扫一遍轨道点。
            lo = max(0, bisect_right(cum, a) - 1)
            hi = min(len(pts) - 1, bisect_left(cum, b) + 1)
            sub = [_point_at_arclen(a, pts, cum)]
            if hi > lo + 1:
                sub.extend(tuple(pts[i]) for i in range(lo + 1, hi))
            sub.append(_point_at_arclen(b, pts, cum))
            if len(sub) >= 2:
                path.moveTo(QPointF(sub[0][0], sub[0][1]))
                for x, y in sub[1:]:
                    path.lineTo(QPointF(x, y))
        if not path.isEmpty():
            p.drawPath(path)

    def _draw_self_intersections(self, p: QPainter, markers, color: QColor) -> None:
        if not markers:
            return
        r = self._world_tile * 0.28
        c = QColor(color)
        c.setAlpha(200)
        pen = QPen(c, self._wpen(self._world_tile * 0.05))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for m in markers:
            p.drawEllipse(QPointF(float(m.get("x", 0.0)), float(m.get("y", 0.0))), r, r)

    # ── 游戏层: 车厢链 / 覆盖圈 / 覆盖设备点亮 ─────────────────────────
    def _draw_train_visuals(self, p: QPainter) -> None:
        for v in self._train_visuals.values():
            color = str(v.get("color") or ACTIVE_ORANGE)
            cars = [tuple(c) for c in (v.get("car_positions") or [])]
            radius = float(v.get("radius") or 0.0)
            show_radius = bool(v.get("show_radius", True))
            if show_radius and radius > 0.0:
                for car in cars:
                    self._draw_coverage_circle(p, car, radius, color)
            self._draw_carriages(p, cars, color,
                                  float(v.get("car_length") or 0.0))
        if self._coverage_ids:
            self._draw_coverage_highlight(p, self._coverage_ids)

    def _draw_coverage_circle(self, p: QPainter, head, radius: float, color: str) -> None:
        x, y = head
        pul = self._pulse()
        rc = QColor(color)
        rc.setAlpha(int(90 + 90 * pul))
        rpen = QPen(rc, self._wpen(self._world_tile * 0.05), Qt.PenStyle.DashLine)
        p.setPen(rpen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(x, y), radius, radius)

    def _draw_carriages(self, p: QPainter, cars, color: str,
                        car_length: float = 0.0) -> None:
        """珠串渲染: 每节车厢 = 对应火车颜色的圆点, 相邻珠子间画锁链。

        解挂/连挂的表现 = 锁链断开/相连: 每支车队内部连链, 分裂后的两支
        车队是独立 visuals, 断点处自然没有链; 连挂合并后恢复单链。
        """
        if not cars:
            return
        clen = max(self._world_tile * 0.26, car_length * 0.5) if car_length > 0.0 else self._world_tile * 0.30
        # 锁链: 相邻珠子之间连线
        chain_c = QColor(color)
        chain_c.setAlpha(170)
        pen = QPen(chain_c, self._wpen(self._world_tile * 0.07))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(1, len(cars)):
            p.drawLine(QPointF(cars[i - 1][0], cars[i - 1][1]),
                       QPointF(cars[i][0], cars[i][1]))
        # 珠子
        for i, (x, y) in enumerate(cars):
            if i == 0:
                pul = self._pulse()
                glow = QColor(color)
                glow.setAlpha(70)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(glow)
                p.drawEllipse(QPointF(x, y),
                              self._world_tile * 0.32 + self._world_tile * 0.12 * pul,
                              self._world_tile * 0.32 + self._world_tile * 0.12 * pul)
            p.setPen(QPen(QColor(10, 12, 16, 180), 1.0))
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(x, y), clen, clen)

    def _draw_coverage_highlight(self, p: QPainter, ids: set) -> None:
        r = max(3.0, self._world_tile * 0.42)
        vr = self._vr()
        margin = max(r, 50.0 / max(0.005, self._view_scale))
        visible = set(self._visible_device_ids(vr, margin))
        path = QPainterPath()
        for fid in ids:
            if fid not in visible:
                continue
            dev = self._dev_by_id.get(str(fid))
            if dev is None:
                continue
            d = self._dwp(dev)
            path.addEllipse(QPointF(d.x(), d.y()), r, r)
        c = QColor(BRIGHT_SUCCESS)
        c.setAlpha(150)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPath(path)

    # ── 屏幕层: 道岔节点 / 预览 / 吸附链 ───────────────────────────────
    def _draw_switch_nodes(self, p: QPainter) -> None:
        for node in self._switches.values():
            x, y = node.get("pos", (0.0, 0.0))
            kind = str(node.get("kind", "split"))
            label = _SWITCH_LABELS.get(kind, kind)
            if kind == "split" and int(node.get("out_count", 1)) > 1:
                d = int(node.get("direction", 0))
                names = ["主干"] + [f"支线{i}" for i in range(1, int(node.get("out_count", 1)))]
                if d < len(names):
                    label += f"→{names[d]}"
            stacked = node.get("stacked") or []
            if stacked:
                label += "＋" + "＋".join(_SWITCH_LABELS.get(t, t) for t in stacked)
            self._draw_switch_box(p, x, y - self._world_tile * 0.55, label,
                                  _SWITCH_COLORS.get(kind, TEXT_SECONDARY))

    def _draw_track_guide(self, p: QPainter) -> None:
        """无轨道时的空状态引导(T8): 画布中央一行灰字教用户画轨道。"""
        if self._tracks:
            return
        w, h = self.width(), self.height()
        if w < 200 or h < 100:
            return
        p.save()
        p.resetTransform()
        p.setFont(QFont("Microsoft YaHei", 11))
        p.setPen(QColor(_OUTPUT_FG_DIM))
        p.drawText(QRectF(0.0, h * 0.46, float(w), 24.0),
                   Qt.AlignmentFlag.AlignCenter,
                   "点「画轨道」→ 点击或按住扫过设备连成链 → 双击完成")
        p.restore()

    def _draw_lap_labels(self, p: QPainter) -> None:
        """绕圈标记上叠 ×N 文字标签(N=lap_count, 世界字体随缩放放大)。"""
        p.setFont(self._wfont(0.22))
        p.setPen(QColor("#FFE14D"))
        for track in self._tracks.values():
            lap = int(track.get("lap_count") or 1)
            if lap <= 1:
                continue
            for m in track.get("loop_markers") or []:
                p.drawText(QPointF(float(m.get("x", 0.0)) + self._world_tile * 0.2,
                                   float(m.get("y", 0.0)) - self._world_tile * 0.2),
                           f"×{lap}")

    def _draw_switch_preview(self, p: QPainter) -> None:
        if not isinstance(self._switch_preview, dict):
            return
        track_id = str(self._switch_preview.get("track_id") or "")
        s = float(self._switch_preview.get("s") or 0.0)
        track = self._tracks.get(track_id)
        if track is None:
            return
        cum = track.get("cum") or []
        x, y = _point_at_arclen(s, track.get("points") or [], cum)
        label = str(self._switch_preview.get("label") or "放置道岔")
        self._draw_switch_box(p, x, y - self._world_tile * 0.55, label, _OUTPUT_FG,
                              ghost=True)

    def _draw_host_glow(self, p: QPainter) -> None:
        """吸附悬停时, 宿主轨道整条泛光(T6: 拖到周围就高亮)。"""
        if not isinstance(self._switch_preview, dict):
            return
        host_id = str(self._switch_preview.get("track_id") or "")
        if not host_id:
            return
        track = self._tracks.get(host_id)
        pts = (track or {}).get("points") or []
        if len(pts) < 2:
            return
        glow = QColor(str(track.get("color") or "#8fd0ff"))
        glow.setAlpha(70)
        pen = QPen(glow, self._wpen(self._world_tile * 0.22))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))

    def _draw_switch_box(self, p: QPainter, wx: float, wy: float, label: str,
                         color: str, ghost: bool = False) -> None:
        """放大镜语义: 道岔方框用世界尺寸, 随缩放同步放大。"""
        w = self._world_tile * 1.7
        h = self._world_tile * 0.55
        rect = QRectF(wx - w / 2.0, wy - h / 2.0, w, h)
        c = QColor(color)
        if ghost:
            c.setAlpha(140)
            p.setBrush(QColor(24, 24, 24, 200))
        else:
            p.setBrush(QColor(24, 24, 24, 235))
        pen = QPen(c, self._wpen(self._world_tile * 0.05))
        pen.setStyle(Qt.PenStyle.DashLine if ghost else Qt.PenStyle.SolidLine)
        p.setPen(pen)
        p.drawRoundedRect(rect, self._world_tile * 0.20, self._world_tile * 0.20)
        p.setPen(QColor(color))
        p.setFont(self._wfont(0.22))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_chain_preview(self, p: QPainter) -> None:
        if not self._chain_points:
            return
        poly = QPolygonF([QPointF(x, y) for x, y in self._chain_points])
        pen = QPen(QColor(_OUTPUT_FG), self._wpen(self._world_tile * 0.08),
                   Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if len(poly) >= 2:
            p.drawPolyline(poly)
        if self._chain_anchor_node:
            node = self._switches.get(self._chain_anchor_node) or {}
            nx, ny = node.get("pos", (0.0, 0.0))
            c = QColor(BRIGHT_SUCCESS)
            c.setAlpha(180)
            p.setPen(QPen(c, self._wpen(self._world_tile * 0.06)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(nx, ny - self._world_tile * 0.55),
                          self._world_tile * 0.36, self._world_tile * 0.36)
        if self._chain_points:
            x, y = self._chain_points[-1]
            p.setBrush(QColor(_OUTPUT_FG))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x, y), self._world_tile * 0.16,
                          self._world_tile * 0.16)
        if self._near_start and self._chain_points:
            sx, sy = self._chain_points[0]
            pul = self._pulse()
            c = QColor(BRIGHT_SUCCESS)
            c.setAlpha(160)
            p.setPen(QPen(c, self._wpen(self._world_tile * 0.06)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(sx, sy),
                          self._world_tile * 0.30 + self._world_tile * 0.12 * pul,
                          self._world_tile * 0.30 + self._world_tile * 0.12 * pul)
        # 画轨道吸附反馈: 光标附近设备金色描边圈
        if self._track_draw_mode and self._draw_snap_fid:
            dev = self._dev_by_id.get(str(self._draw_snap_fid))
            if dev is not None:
                d = self._dwp(dev)
                r = self._world_tile * 0.45
                c = QColor(_AMBER)
                c.setAlpha(220)
                p.setPen(QPen(c, self._wpen(self._world_tile * 0.06)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(d.x(), d.y()), r, r)


class RouteRunnerPanel(QWidget):
    """「跑动编排」面板 v2。旧 RouteAnimatorWindow 保留不删(下轮清理)。"""

    closed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("跑动编排 — Route Runner")
        self.setMinimumSize(980, 600)
        self._project_manager = None
        self._topview_widget = None
        self._audio_path = ""
        self._engine = RouteRunnerEngine()
        self._tracks: Dict[str, dict] = {}
        self._track_numbers: Dict[str, int] = {}
        self._track_list_sig: tuple = ()
        self._switches: Dict[str, dict] = {}
        self._device_positions: Dict[str, Tuple[float, float]] = {}
        self._fallback_car_unit = 40.0
        self._train_names: Dict[str, str] = {}
        self._cards: Dict[str, QFrame] = {}
        self._throttle_sliders: Dict[str, QSlider] = {}
        self._throttle_labels: Dict[str, QLabel] = {}
        self._throttle_spins: Dict[str, QSpinBox] = {}
        self._param_sliders: Dict[str, Dict[str, QSlider]] = {}
        self._param_spins: Dict[str, Dict[str, object]] = {}
        self._param_value_labels: Dict[str, Dict[str, QLabel]] = {}
        self._param_specs: Dict[str, Dict[str, tuple]] = {}
        self._mode_combos: Dict[str, QComboBox] = {}
        self._track_combos: Dict[str, QComboBox] = {}
        self._enabled_checkboxes: Dict[str, QCheckBox] = {}
        self._selected_chains: Dict[str, set] = {}
        self._train_manager_dlg = None
        self._fleet_sig: tuple = ()
        self._fleet_tick = 0
        self._pending_throttle: Dict[str, int] = {}
        self._playing = False
        self._selected_train_id: Optional[str] = None
        self._show_radius = True
        self._last_active_ids: List[str] = []
        self._last_intensity: Dict[str, float] = {}
        self._last_tick = time.monotonic()
        self._frame_ms_ema = 0.0
        self._train_color_idx = 0; self._throttle_envelopes: Dict[str, dict] = {}
        self._jb_wired = False
        self._page_combo = None
        self._switch_bar = None
        self._current_page_id: Optional[str] = None
        self._page_scenes: Dict[str, dict] = {}
        self._save_slots: Dict[str, Dict[str, dict]] = {}
        self._pages_fingerprint = ""
        #: 工程身份指纹（`created_at + name`）：换工程才清页面场景/存档槽（见 set_context）。
        self._project_identity: tuple = ()
        self._cur_pos_fp = ""
        self._topview_wired = False
        self._min_radius = 25.0      # 轨道平滑最小转弯半径(T5, HUD 可调)
        self._snap_tol_value = 12.0   # 吸附容差(T5/T6, HUD 可调)
        self._smooth_thread: Optional[threading.Thread] = None
        self._smooth_pending: Optional[dict] = None
        self._smooth_poll = QTimer(self)
        self._smooth_poll.setInterval(16)
        self._smooth_poll.timeout.connect(self._poll_smooth_thread)
        self._gap_thread: Optional[threading.Thread] = None
        self._gap_pending: Optional[dict] = None
        self._gap_poll = QTimer(self)
        self._gap_poll.setInterval(16)
        self._gap_poll.timeout.connect(self._poll_gap_thread)
        self._canvas = _GameCanvas(self)
        self._build_ui()
        self._canvas.track_chain_drawn.connect(self._on_track_drawn)
        self._canvas.switch_dropped.connect(self._on_switch_dropped)
        self._canvas.switch_delete_requested.connect(self._on_switch_delete)
        self._canvas.switch_direction_requested.connect(self._on_switch_direction)
        self._canvas.chain_hint.connect(lambda t: self._set_status(t))
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(_TIMER_MS)
        self._frame_timer.timeout.connect(self._on_frame)
        self._frame_timer.start()
        self._page_poll_timer = QTimer(self)
        self._page_poll_timer.setInterval(1000)
        self._page_poll_timer.timeout.connect(self._on_page_poll)
        self._page_poll_timer.start()

    # ── 公开 API ────────────────────────────────────────────────────────
    @property
    def engine(self) -> RouteRunnerEngine:
        return self._engine

    @property
    def active_ordered_ids(self) -> List[str]:
        return list(self._last_active_ids)

    def set_devices(self, devices: List[dict]) -> None:
        self._canvas.set_devices(list(devices or []))
        positions = {
            str(d.get("id", "")): (float(d.get("x", 0.0)), float(d.get("y", 0.0)))
            for d in (devices or []) if d.get("id")
        }
        self._device_positions = positions
        self._fallback_car_unit = (
            _median_nn_distance(list(positions.values())) if positions else 40.0)
        self._engine.set_page_devices(positions)
        if positions:
            self._canvas.zoom_to_fit()

    def add_train(self, name: str = "", color: str = "") -> str:
        if not color:
            color = _TRAIN_PALETTE[self._train_color_idx % len(_TRAIN_PALETTE)]
            self._train_color_idx += 1
        tid = str(uuid.uuid4())
        name = name or f"火车 {len(self._train_names) + 1}"
        self._train_names[tid] = name
        self._selected_train_id = tid
        self._engine.add_train(tid, 4, self._fallback_car_unit,
                               self._fallback_car_unit * 0.6,
                               self._default_base_speed(), color)
        # 默认不自动挂到任何轨道: 由用户通过轨道选择器把火车放到目标轨道
        self._build_train_card(tid)
        self._refresh_card_selection()
        return tid

    def tick(self, dt: float) -> None:
        self._tick_impl(dt)

    def set_context(self, project_manager=None, topview=None, audio_path: str = "") -> None:
        """换绑上下文（打开跑动窗口 / 切换工程 / undo 回灌都会调）。

        ★★ 2026-09-21 用户实报（工程串台）：页面场景与存档槽按**页 id**（TopView 新
          工程的首页 id 恒为 `"page-1"`）索引，而 `set_context` 只重置指纹、刷新页面
          列表 → 切到另一个工程后键正好命中 → 跑动的存档点仍是上一个工程的（页 id
          一样时，上一个工程的场景会被直接写进新工程）。
          这里按**工程身份**变化清空两份内存字典；`_current_page_id` 一并归零，
          否则 `_switch_page` 见"页 id 未变"会早退、旧火车/轨道留在画布上继续被
          下一次保存写进新工程。
        """
        identity = _project_identity(project_manager)
        if identity != getattr(self, "_project_identity", ()):
            self._project_identity = identity
            discard_project_runtime(self)
        self._project_manager = project_manager
        self._topview_widget = topview
        self._audio_path = audio_path or ""
        self._pages_fingerprint = ""
        self._cur_pos_fp = ""
        sync_after_project_swap(self)   # 第 7 项：换工程/undo 后内存场景对齐工程
        self._clear_route_mask()
        self._wire_topview()
        self._reload_topview_pages()

    def save_state(self) -> dict:
        self._save_scene(self._current_page_id)
        return export_scene_state(self)

    def load_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        pages = state.get("runner_pages")
        if isinstance(pages, dict):
            for pid, scene in pages.items():
                if isinstance(scene, dict):
                    self._page_scenes[str(pid)] = scene
        slots = state.get("runner_slots")
        if isinstance(slots, dict):
            for pid, slot_map in slots.items():
                if isinstance(slot_map, dict):
                    self._save_slots[str(pid)] = {
                        str(k): dict(v) for k, v in slot_map.items()
                        if isinstance(v, dict)
                    }
        cur = state.get("runner_current_page")
        if cur:
            pages = self._topview_pages()
            if any(str(p.get("id", "")) == str(cur) for p in pages):
                self._current_page_id = None
                self._switch_page(str(cur), pages)
            else:
                # 页面列表暂不可用(独立面板/测试)或页面已删: 直接恢复已存场景防丢数据。
                self._current_page_id = str(cur)
                self._restore_scene(str(cur))
                self._clear_route_mask()

    # ══ 调度台: 跑动编排总线上报(唯一出口) ═════════════════════════════
    # 一列火车覆盖设备集=一个效果节点; 身份契约: 一窗格=(panel, 火车id, 覆盖设备id内容寻址), 绝不按名称合并。

    #: 上报节流（秒）：帧循环可 60Hz，总线只需人眼刷新率级别
    _BUS_REPORT_INTERVAL = 0.25

    @staticmethod
    def _dispatch_group_key(ids) -> str:
        """设备 id 内容寻址的设备集身份（与 EffectBrowser._group_key_from_ids 同规则）。"""
        out, seen = [], set()
        for fid in (ids or []):
            sfid = str(fid)
            if sfid and sfid not in seen:
                seen.add(sfid)
                out.append(sfid)
        return "grp:" + "|".join(out) if out else "__all__"

    def _bus_nodes(self) -> list:
        """把当前车列覆盖转成调度台窗格（一列火车一节点，不去重）。

        节点构造与逐列作用域归集在 `runner_bus_scope`（本文件只许减行，见门禁）；
        R130 的"无遮罩不上报"门槛保留在这里（保持原语义不动）。
        """
        try:
            if not get_route_mask():          # R130：只有车列定义而无遮罩 → 不上报
                return []
            engine = getattr(self, "_engine", None)
            return build_route_nodes({
                "engine": engine,
                "trains": dict(getattr(engine, "_trains", {}) or {}),
                "names": dict(getattr(self, "_train_names", {}) or {}),
                "last": list(getattr(self, "_last_active_ids", []) or []),
                "playing": bool(getattr(self, "_playing", False)),
                "group_key": self._dispatch_group_key,
            })
        except Exception:
            logger.debug("route: _bus_nodes failed", exc_info=True)
            return []

    def report_to_bus(self, force: bool = False) -> None:
        """上报当前跑动窗格到总线（节流；失败绝不影响出光）。"""
        try:
            now = time.monotonic()
            if not force and (now - getattr(self, "_bus_last_report", 0.0)) < self._BUS_REPORT_INTERVAL:
                return
            self._bus_last_report = now
            from ...processing.dispatch import EffectBus
            bus = EffectBus.instance()
            nodes = self._bus_nodes()
            # 逐键 upsert + 差集删除：删车/清空覆盖时只删它自己的格，不整体 clear_panel
            live_keys = {(n.panel, n.track_type, n.group_key) for n in nodes}
            for n in nodes:
                bus.report(n)
            for key in list(getattr(self, "_bus_seen_keys", ())):
                if key not in live_keys:
                    bus.remove(*key)
            self._bus_seen_keys = live_keys
        except Exception:
            pass

    def dispatch_capture_state(self) -> dict:
        """给调度台捕获可完整还原的面板状态（复用面板既有 save_state）。"""
        try:
            return dict(self.save_state())
        except Exception:
            return {}

    def dispatch_release_state(self, node: dict, state: Optional[dict] = None) -> bool:
        """**释放**跑动编排（关闭场景时调用）：停播放并清遮罩。

        ★★ 2026-09-14 深查修正：本面板只有 capture/recall、**没有 release**，
          而释放路由对"无 release 回调"静默算成功 → 关闭含编排的场景后，
          `_frame_timer` 仍在跑、遮罩仍在写 → 输出不会停（"关不掉"）。
          复用既有停止路径（停帧定时器 + 清遮罩），不新增第二套逻辑。
        """
        try:
            self._playing = False
            try:
                self._frame_timer.stop()
            except Exception:
                pass
            try:
                self._clear_route_mask()
            except Exception:
                logger.debug("route: clear mask on release failed", exc_info=True)
            try:
                self.report_to_bus(force=True)
            except Exception:
                pass
            return True
        except Exception:
            logger.debug("route: dispatch_release_state failed", exc_info=True)
            return False

    def dispatch_recall_state(self, node: dict, state: Optional[dict] = None) -> bool:
        """按调度台节点还原跑动编排（复用 load_state 的页面/场景往返）。"""
        try:
            if state:
                self.load_state(dict(state))
            self.report_to_bus(force=True)     # 立即反映到调度台
            return True
        except Exception:
            logger.debug("route: dispatch_recall_state failed", exc_info=True)
            return False

    # ── 分页(R1: 空页/指纹未变/空页id 直接 return, 不重建不清火车) ─────
    # ★★ R194/HF-03 两级指纹（1s 轮询性能）：
    #   1s 轮询的大多数拍次里页面**根本没变**，而全路径 `_topview_pages()` 是
    #   O(页×设备) 的 `_pages_from_raw` 投影 + repr 全量指纹（4000 台 ≈ 每秒
    #   一次全投影）。现在每拍先走 **L1 快筛**（`_pages_l1_hit`，零投影）：
    #   命中直接返回；L1 未命中**或**距上次全量检查 ≥10s 才走全路径（L2 兜底，
    #   非当前页变化的陈旧上界 = 10s —— L1 只盯当前页，其余页交给 L2）。
    def _reload_topview_pages(self) -> None:
        pc = self._page_combo
        if pc is None:
            return
        if self._pages_l1_hit():
            return  # L1 快筛命中：跳过 O(页×设备) 投影（见上方两级指纹说明）
        pages = self._topview_pages()
        if not pages:
            self._clear_route_mask()
            return  # R1: 空页列表不重建、不切页、不清空火车
        list_fp = self._pages_list_fingerprint(pages)
        if list_fp != self._pages_fingerprint:
            self._pages_fingerprint = list_fp
            self._rebuild_combo(pages)
            cur = str(pc.currentData() or "")
            if cur:
                self._switch_page(cur, pages)
        else:
            self._refresh_current_page_devices(pages)

    def _pages_l1_hit(self) -> bool:
        """L1 快筛（R194/HF-03）：大概率没变即 True（零投影，不调 `_pages_from_raw`）。

        指纹构成见 `_pages_l1_fingerprint`；TopView `_pages` 是普通 dict 列表且
        **就地变异**（id 不变），id()/len() 抓不住"同对象改 x/y"——当前页 pos
        投影 repr 补这一格（拖动 ≤1 拍反映）；其余页的变化 L1 故意不查，由 L2
        （每 ≤10s 的全量 `_pages_list_fingerprint`）兜底。持有 raw 列表与当前
        页 dict 的引用防 id() 复用假命中。空列表不快筛（本就近零成本，且保住
        R1"每拍清遮罩"的旧行为）。
        """
        tv = self._topview_widget
        raw = getattr(tv, "_pages", None) if tv is not None else None
        if not isinstance(raw, list) or not raw:
            return False          # 工程态回退路径/空页：每拍走全路径（廉价且保语义）
        now = time.monotonic()
        fp = self._pages_l1_fingerprint(raw)
        self._pages_l1_raw_ref = raw          # 持引用：占住 id，防地址复用假命中
        if fp == getattr(self, "_pages_l1_fp", None) and \
                now < getattr(self, "_pages_l2_due", 0.0):
            return True
        self._pages_l1_fp = fp
        self._pages_l2_due = now + 10.0       # 本拍已（或即将）做全量 L2 检查
        return False

    def _pages_l1_fingerprint(self, raw: list) -> tuple:
        """L1 指纹（零投影）：工程身份 + id/长度/dict 页数 + 当前页 pos/可见集 repr。"""
        cur_pid = str(getattr(self, "_current_page_id", "") or "")
        cur_page = None
        ndict = 0
        for pg in raw:
            if isinstance(pg, dict):
                ndict += 1
                if cur_page is None and str(pg.get("id", "") or "") == cur_pid:
                    cur_page = pg
        pos_repr, vis_repr = self._cur_page_reprs(cur_page)
        self._pages_l1_cur_ref = cur_page     # 持引用：占住 id，防地址复用假命中
        return (getattr(self, "_project_identity", ()), id(raw), len(raw), ndict,
                cur_pid, id(cur_page) if cur_page is not None else -1,
                pos_repr, vis_repr)

    def _cur_page_reprs(self, cur_page) -> tuple:
        """当前页 raw 的 (设备 pos 投影 repr, 可见集 repr)（零投影判变，畸形容忍）。"""
        if cur_page is None:
            return "", ""
        fpos = cur_page.get("fixtures") or {}
        pos = ""
        if isinstance(fpos, dict):
            pos = repr([(str(fid), self._raw_xy(v), self._raw_xy(v, "y"))
                        for fid, v in fpos.items() if isinstance(v, dict)])
        return pos, repr(cur_page.get("visible_fixture_ids"))

    @staticmethod
    def _raw_xy(v, axis: str = "x") -> float:
        """raw fixtures 值的宽容坐标读取（与 `_pages_from_raw` 的 float 化同口径）。

        L1 快筛自用：轮询路径绝不许抛（畸形坐标回落 0.0，与投影侧的
        `float(v.get(axis, 0) or 0)` 数值口径一致，repr 判变不受影响）。
        """
        try:
            return float(v.get(axis, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _topview_pages(self) -> List[dict]:
        tv = self._topview_widget
        if tv is not None:
            raw = getattr(tv, "_pages", None)
            if isinstance(raw, list):
                return self._pages_from_raw(raw)
        proj = self._project()
        tv_state = getattr(proj, "topview_state", {}) or {} if proj is not None else {}
        return self._pages_from_raw(list((tv_state or {}).get("pages", []) or []))

    @staticmethod
    def _pages_from_raw(raw_pages: list) -> List[dict]:
        out: List[dict] = []
        for i, pg in enumerate(raw_pages):
            if not isinstance(pg, dict):
                continue
            fpos = pg.get("fixtures", {}) or {}
            has_vis = "visible_fixture_ids" in pg
            visible = {str(f) for f in (pg.get("visible_fixture_ids") or [])}
            devs: List[dict] = []
            for fid, v in fpos.items():
                if not isinstance(v, dict):
                    continue
                if has_vis and str(fid) not in visible:
                    continue
                if "x" not in v and "y" not in v:
                    continue
                x = float(v.get("x", 0) or 0)
                y = float(v.get("y", 0) or 0)
                devs.append({"id": str(fid), "x": x, "y": y})
            out.append({
                "id": str(pg.get("id", "") or f"page-{i + 1}"),
                "name": str(pg.get("name", "") or f"Sheet{i + 1}"),
                "device_count": len(devs),
                "devices": devs,
            })
        return out

    @staticmethod
    def _pages_list_fingerprint(pages: List[dict]) -> str:
        """L2 全量指纹（R194/HF-03）：页 id/name/每页设备 id 的 repr。

        O(页×设备) + 依赖 `_topview_pages()` 的全投影，**只**在 L1 未命中或
        每 ≤10s 兜底时走（见 `_reload_topview_pages` 头注释）——它对"就地变异"
        的 dict 逐页投影，是正确性的最后防线；非当前页的变化陈旧上界 = 10s。
        """
        parts = [(pg["id"], pg["name"], tuple(d["id"] for d in pg["devices"])) for pg in pages]
        return repr(parts)

    @staticmethod
    def _page_pos_fingerprint(pg: dict) -> str:
        return repr([(d["id"], d["x"], d["y"]) for d in pg.get("devices", [])])

    def _rebuild_combo(self, pages: List[dict]) -> None:
        rebuild_page_combo(self, pages)

    def _on_page_combo_changed(self, idx: int) -> None:
        pc = self._page_combo
        if pc is None or idx < 0:
            return
        page_id = str(pc.itemData(idx) or "")
        self._switch_page(page_id, self._topview_pages())

    def _switch_page(self, page_id: str, pages: List[dict]) -> None:
        if not page_id:
            return  # R1: 空页 id 不切页、不清火车
        if not any(str(p.get("id", "")) == str(page_id) for p in pages):
            return  # 页面已被删除/不存在: 不进入无效页, 避免刷新脏状态
        if page_id == self._current_page_id:
            self._refresh_current_page_devices(pages, force=True)
            return
        self._save_scene(self._current_page_id)
        self._current_page_id = page_id
        devs: List[dict] = next(
            (list(p["devices"]) for p in pages if p["id"] == page_id), [])
        self._apply_page_devices(devs)
        self._restore_scene(page_id)
        self._clear_route_mask()

    @staticmethod
    def _parse_route_points(points) -> List[Tuple[float, float]]:
        """浮条负载点序宽松解析（畸形项丢弃不崩）。"""
        out: List[Tuple[float, float]] = []
        for p in (points or []):
            try:
                out.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def apply_topview_route(self, page_id: str = "", points=None) -> None:
        """TopView「画轨道选设备」浮条投递端（R180#12 桥契约）：

        切到来源页 → 把点序喂给画布 → 走既有 ``track_chain_drawn`` →
        ``_on_track_drawn`` → 平滑 → engine.add_track（与手画同一契约，
        引擎零改动）。页不存在/点序不足/窗口未 set_context 时静默降级。
        """
        pts = self._parse_route_points(points)
        if len(pts) < 2:
            return
        try:
            pid = str(page_id or "")
            if pid and pid != self._current_page_id:
                self._switch_page(pid, self._topview_pages())
            self._canvas.track_chain_drawn.emit(
                {"points": pts, "start_node": None, "end_node": None})
            self._set_status(f"已从 TopView 应用路线（{len(pts)} 点）")
        except Exception:
            logger.debug("apply_topview_route failed", exc_info=True)

    def _refresh_current_page_devices(self, pages: List[dict], force: bool = False) -> None:
        if self._current_page_id is None:
            return
        for pg in pages:
            if pg["id"] == self._current_page_id:
                pos_fp = self._page_pos_fingerprint(pg)
                if force or pos_fp != self._cur_pos_fp:
                    self._apply_page_devices(list(pg["devices"]))
                return

    def _apply_page_devices(self, devices: List[dict]) -> None:
        self.set_devices(list(devices))
        self._cur_pos_fp = repr([(d["id"], d["x"], d["y"]) for d in devices])

    def _wire_topview(self) -> None:
        tv = self._topview_widget
        if tv is None or self._topview_wired:
            return
        try:
            tv.layout_changed.connect(self._on_topview_layout_changed)
            self._topview_wired = True
        except Exception:
            logger.debug("topview layout_changed wiring failed", exc_info=True)

    def _on_topview_layout_changed(self) -> None:
        self._reload_topview_pages()

    def _on_page_poll(self) -> None:
        self._reload_topview_pages()

    # ── 场景保存/恢复(每页独立) ────────────────────────────────────────
    def _switch_pos(self, node_id: str) -> List[float]:
        node = self._switches.get(str(node_id))
        if node is None:
            return [0.0, 0.0]
        track = self._tracks.get(str(node.get("track_id", "")))
        if track is None:
            return [0.0, 0.0]
        x, y = _point_at_arclen(float(node.get("s", 0.0)), track["points"], track["cum"])
        return [float(x), float(y)]

    def _snap_tol(self) -> float:
        """吸附容差(世界单位); HUD 数值是屏幕像素, 需按当前缩放转成世界单位。"""
        raw = float(self._snap_tol_value)
        try:
            return max(raw, self._canvas._px(raw))
        except Exception:
            return raw

    def _default_base_speed(self) -> float:
        """基速默认值(世界单位/秒): 约 2 个设备间距/秒, 与旧 v1 视觉手感一致。"""
        return max(20.0, self._fallback_car_unit * 2.0)

    def _materialize_train(self, tr) -> None:
        """车长/半径 0 = 自动 → 用设备间距回退, 落为真引擎每车厢定距。"""
        tr.car_length = tr.car_length if tr.car_length > 0.0 else self._fallback_car_unit
        tr.radius = tr.radius if tr.radius > 0.0 else self._fallback_car_unit * 0.6

    def _reattach_train(self, tid: str) -> None:
        tr = self._engine._trains.get(str(tid))
        if tr is None:
            return
        track_id = tr._home_edge_id
        if not track_id or track_id not in self._tracks:
            return
        self._engine.attach_train_to_track(str(tid), track_id, tr.nose_s)

    def _attach_train(self, tid: str, track_id: str, s0: float) -> None:
        tr = self._engine._trains.get(str(tid))
        if tr is None:
            return
        self._materialize_train(tr)
        s0 = float(s0)
        # 起始弧长 0 时把车头前移到整列长度，避免所有车厢在起点叠成一个点
        if s0 <= 0.0 and tr.car_count > 1:
            s0 = (tr.car_count - 1) * tr.chain_length
        self._engine.attach_train_to_track(str(tid), str(track_id), s0)

    def _add_switch(self, node_id: str, kind: str, track_id: str,
                    s: float, branch_params: Optional[dict] = None) -> None:
        """放置道岔: 真实调用引擎 add_switch(四类型) + 记录渲染元数据。"""
        kind = kind if kind in _SWITCH_LABELS else "split"
        params = dict(branch_params or {})
        self._engine.add_switch(str(node_id), kind, str(track_id), float(s), params)
        self._switches[str(node_id)] = {
            "kind": kind, "track_id": str(track_id), "s": float(s),
            "branch_params": params,
        }

    def _rebuild_engine(self) -> None:
        """引擎无 remove_track/remove_switch → 结构变更后从面板场景重放 add_*。

        保留每列火车元数据(车厢/车长/半径/基速/颜色/油门)、车头弧长(nose_s)
        与 R1/R6/R8 运行态；快照/回填逻辑在 runner_train_state（门禁行数口径）。
        """
        snap: List[dict] = [
            snapshot_train_state(tr)
            for tid, tr in self._engine._trains.items() if tid in self._train_names
        ]
        eng = RouteRunnerEngine()
        eng.set_page_devices(self._device_positions)
        for tid, track in self._tracks.items():
            eng.add_track(tid, [tuple(p) for p in track["points"]])
        for nid, node in self._switches.items():
            eng.add_switch(nid, node["kind"], node["track_id"], float(node["s"]),
                            dict(node.get("branch_params", {})))
            for t in node.get("stacked") or []:
                eng.stack_switch(nid, t)
            eng.set_switch_direction(nid, int(node.get("direction", 0)))
        for item in snap:
            eng.add_train(item["tid"], item["car_count"], item["car_length"],
                          item["radius"], item["base_speed"], item["color"])
            tr2 = eng._trains[item["tid"]]
            tr2.chain_length = float(item["chain_length"]) if float(item["chain_length"]) > 0.0 else tr2.car_length
            if item["track_id"] and item["track_id"] in self._tracks:
                eng.attach_train_to_track(item["tid"], item["track_id"], item["nose_s"])
            eng.set_throttle(item["tid"], item["throttle"])
            restore_train_state(eng, item)
        self._engine = eng

    def _reset_trains(self) -> None:
        for tid, tr in list(self._engine._trains.items()):
            track_id = tr._home_edge_id
            if track_id and track_id in self._tracks:
                self._attach_train(tid, track_id, 0.0)

    def _save_scene(self, page_id: Optional[str]) -> None:
        if not page_id:
            return
        # id 含 '#' 的分队是运行态(解挂产物), 不持久化; 载入后由引擎重新分裂
        trains = {
            tid: {
                "car_count": int(tr.car_count), "car_length": float(tr.car_length),
                "radius": float(tr.radius), "base_speed": float(tr.base_speed),
                "color": tr.color, "throttle": float(tr.throttle),
                "track_id": str(tr._home_edge_id or ""), "nose_s": float(tr.nose_s),
                "chain_length": float(tr.chain_length), "mode": tr.mode,
                "skip_gaps": bool(tr.skip_gaps),
                "enabled": not bool(tr.ended),
            }
            for tid, tr in self._engine._trains.items()
            if "#" not in tid
        }
        tracks = {
            tid: {"points": [list(p) for p in track["points"]], "color": track["color"],
                   "lap_count": int(track.get("lap_count", 1)),
                   "owner_train_id": str(track.get("owner_train_id", "") or ""),
                   "number": int(track.get("number", 0) or 0)}
            for tid, track in self._tracks.items()
        }
        switches = {
            nid: {"kind": node["kind"], "track_id": node["track_id"], "s": float(node["s"]),
                  "branch_params": dict(node.get("branch_params", {})),
                  "pos": self._switch_pos(nid),
                  "stacked": list(node.get("stacked", [])),
                  "direction": int(node.get("direction", 0))}
            for nid, node in self._switches.items()
        }
        selected = self._selected_train_id
        if selected and "#uncouple#" in str(selected):
            selected = self._base_train_key(str(selected))
        self._page_scenes[page_id] = {
            "trains": trains,
            "names": {k: v for k, v in self._train_names.items()
                      if "#uncouple#" not in str(k)},
            "tracks": tracks,
            "switches": switches, "selected_train_id": selected,
            "train_color_idx": self._train_color_idx,
            "envelopes": envelopes_to_scene(self),   # R3：每车油门包络
            "audio_mod": config_to_scene(self),      # R7：音频调制配置
        }
        # 第 7 项：写穿 project.route_animator_state（修前场景只活在内存，另存即丢）
        persist_saved_scene(self)

    def _restore_scene(self, page_id: str) -> None:
        self._clear_all_trains()
        self._clear_all_tracks()
        self._clear_all_switches()
        self._rebuild_engine()
        scene = self._page_scenes.get(page_id)
        if not scene:
            finish_scene_restore(self)
            return
        apply_scene_config(self, scene.get(_audio_mod.SCENE_KEY))  # R7+P2-2 复位
        for tid, track in (scene.get("tracks") or {}).items():
            pts = [tuple(p) for p in track.get("points", [])]
            if len(pts) < 2:
                continue
            lap = int(track.get("lap_count", 1))
            num = int(track.get("number", 0) or 0)
            if num <= 0:
                num = self._next_track_number()
            self._track_numbers[str(tid)] = num
            self._tracks[tid] = {"points": pts, "color": str(track.get("color", "#8fd0ff")),
                                 "cum": _polyline_cumlen(pts), "lap_count": lap,
                                 "owner_train_id": str(track.get("owner_train_id", "") or ""),
                                 "number": num}
            self._engine.add_track(tid, pts, lap_count=lap)
        for nid, node in (scene.get("switches") or {}).items():
            self._add_switch(nid, node.get("kind", "split"),
                              node.get("track_id", ""), float(node.get("s", 0.0)),
                              node.get("branch_params", {}))
            for t in node.get("stacked") or []:
                self._engine.stack_switch(nid, t)
            self._engine.set_switch_direction(nid, int(node.get("direction", 0)))
        for tid, data in (scene.get("trains") or {}).items():
            self._engine.add_train(
                tid, int(data.get("car_count", 4)), float(data.get("car_length", 0.0)),
                float(data.get("radius", 0.0)), float(data.get("base_speed", 80.0)),
                str(data.get("color", ACTIVE_ORANGE)))
            tr = self._engine._trains[tid]
            self._materialize_train(tr)
            chain = float(data.get("chain_length") or data.get("car_length") or 0.0)
            if chain > 0.0:
                tr.chain_length = chain
            track_id = str(data.get("track_id", "") or "")
            if track_id and track_id in self._tracks:
                # 初始状态: 火车停在轨道起点(运行态弧长不持久化)
                self._attach_train(tid, track_id, 0.0)
            self._engine.set_throttle(tid, float(data.get("throttle", 1.0)))
            self._engine.set_train_mode(tid, str(data.get("mode", "loop_forward")))
            self._engine.set_train_skip_gaps(tid, bool(data.get("skip_gaps", False)))
            self._engine._trains[tid].ended = not bool(data.get("enabled", True))
            self._train_names[tid] = str(scene.get("names", {}).get(tid, tid))
            self._build_train_card(tid)
        self._selected_train_id = scene.get("selected_train_id")
        self._train_color_idx = int(scene.get("train_color_idx", 0))
        finish_scene_restore(self)      # 尾段：包络重建 → 画布/火车刷新
        self._refresh_card_selection()

    def _clear_all_trains(self) -> None:
        for tid in list(self._engine._trains.keys()):
            self._engine.remove_train(tid)
        self._train_names.clear()
        for card in list(self._cards.values()):
            self._deck_lay.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._throttle_sliders.clear()
        self._throttle_spins.clear()
        self._throttle_labels.clear()
        self._param_sliders.clear()
        self._param_spins.clear()
        self._param_value_labels.clear()
        self._param_specs.clear()
        self._pending_throttle.clear()
        self._mode_combos.clear()
        self._track_combos.clear()
        self._enabled_checkboxes.clear()
        self._selected_chains.clear()
        self._selected_train_id = None

    def _clear_all_tracks(self) -> None:
        self._tracks = {}
        self._track_numbers = {}

    def _clear_all_switches(self) -> None:
        self._switches = {}

    def _refresh_canvas_scene(self) -> None:
        gap_radius = self._fallback_car_unit * 0.6
        for tr in self._engine._trains.values():
            if tr.radius > 0.0:
                gap_radius = tr.radius
                break
        # 长轨道 gap 计算触发投影(4248 点约 100ms), 放后台线程; 首帧空 gaps 渲染防卡顿。
        pending_gaps: Dict[str, float] = {}
        tracks: Dict[str, dict] = {}
        for tid, t in self._tracks.items():
            pts_count = len(t.get("points") or [])
            if pts_count > 600:
                gaps: list = []
                pending_gaps[tid] = gap_radius
            else:
                gaps = self._engine.get_track_gaps(tid, gap_radius)
            tracks[tid] = {"points": [tuple(p) for p in t["points"]],
                           "color": t["color"],
                           "loop_markers": self._engine.get_loop_markers(tid),
                           "lap_count": int(t.get("lap_count", 1)),
                           "gaps": gaps}
        self._canvas.set_tracks(tracks)
        switches = {}
        for nid in self._switches:
            pos = self._switch_pos(nid)
            node = self._switches[nid]
            eng_node = self._engine._nodes.get(nid)
            switches[nid] = {"kind": node["kind"], "pos": (pos[0], pos[1]),
                             "track_id": node["track_id"], "s": node["s"],
                             "out_count": len(eng_node.out_edges) if eng_node else 1,
                             "direction": int((eng_node.params or {}).get("direction", 0))
                             if eng_node else 0,
                             "stacked": list(node.get("stacked", []))}
        self._canvas.set_switches(switches)
        if pending_gaps:
            self._start_gap_fill(pending_gaps)

    def _start_gap_fill(self, pending: Dict[str, float]) -> None:
        if self._gap_thread is not None and self._gap_thread.is_alive():
            # 已有线程在跑，暂存最新需求，跑完后再补。
            self._gap_pending = pending
            return
        holder: list = []
        def worker():
            try:
                result = {}
                for tid, radius in pending.items():
                    result[tid] = self._engine.get_track_gaps(tid, radius)
                holder.append(result)
            except Exception:
                holder.append(None)
        self._gap_holder = holder
        self._gap_thread = threading.Thread(target=worker, daemon=True)
        self._gap_thread.start()
        if not self._gap_poll.isActive():
            self._gap_poll.start()

    def _poll_gap_thread(self) -> None:
        if self._gap_thread is None:
            self._gap_poll.stop()
            return
        if self._gap_thread.is_alive():
            return
        self._gap_thread.join(timeout=0.05)
        holder = getattr(self, "_gap_holder", None) or []
        self._gap_thread = None
        self._gap_holder = None
        self._gap_poll.stop()
        if holder and holder[0] is not None:
            result = holder[0]
            for tid, gaps in result.items():
                if tid in self._canvas._tracks:
                    self._canvas._tracks[tid]["gaps"] = list(gaps)
            self._canvas._static_cache = None
            self._canvas.update()
        pending = self._gap_pending
        self._gap_pending = None
        if pending:
            self._start_gap_fill(pending)

    def _project(self):
        if self._project_manager is None:
            return None
        return getattr(self._project_manager, "project", None) or getattr(
            self._project_manager, "_project", None)

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        from .runner_workspace_ui import build
        build(self)

    # ── 工具/参数双行头(D波解剖: 26px 头行 + prop_row 参数行) ──────────
    def _build_hud(self) -> QWidget:
        """上行 = 全局工具/开关(26px 头行); 下行 = 参数行(prop_row 版式)。"""
        wrap = QWidget()
        scope_qss(wrap, f"background:{_PANEL};border-bottom:1px solid {SEP_LINE};")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        v.addWidget(self._build_hud_tools())
        v.addWidget(self._build_hud_params())
        # R15：HUD 全局总览 mini 时间线（自绘条，逻辑在 runner_hud_overview）
        v.addWidget(build_overview_widget(self))
        return wrap

    def _build_hud_tools(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(30)   # 26px 按钮 + 上下 2px 呼吸
        r = QHBoxLayout(w)
        r.setContentsMargins(8, 2, 8, 2)
        r.setSpacing(6)
        self._add_btn = self._hud_btn("添加火车", _PRIMARY)
        self._add_btn.clicked.connect(self._on_add_train)
        r.addWidget(self._add_btn)
        self._train_manager_btn = self._hud_btn("火车管理", _PRIMARY)
        self._train_manager_btn.setToolTip("打开火车管理子页面：竖排卡片 + 滚动浏览")
        self._train_manager_btn.clicked.connect(self._open_train_manager)
        r.addWidget(self._train_manager_btn)
        sep = self._tool_sep()
        r.addWidget(sep)
        self._draw_btn = self._hud_btn("画轨道", _AMBER)
        self._draw_btn.setCheckable(True)
        self._draw_btn.setToolTip("画轨道模式：点击或按住扫过设备连成链，双击/回车完成，Esc 取消")
        self._draw_btn.toggled.connect(self._on_draw_toggle)
        r.addWidget(self._draw_btn)
        self._grid_btn = self._hud_btn("网格吸附", _AMBER)
        self._grid_btn.setCheckable(True)
        self._grid_btn.setToolTip("画轨道时吸附到六边形/方形网格(对齐辅助, 非独立建造)。\n"
                                  "按住 Shift 临时禁用; 配合格距/形状画出参考格线")
        self._grid_btn.toggled.connect(self._on_grid_toggle)
        r.addWidget(self._grid_btn)
        self._grid_size_spin = QSpinBox()
        self._grid_size_spin.setRange(10, 2000)
        self._grid_size_spin.setValue(50)
        self._grid_size_spin.setFixedWidth(64)
        self._grid_size_spin.setFixedHeight(22)
        self._grid_size_spin.setToolTip("格距(世界单位): 六边形=外接圆半径, 方形=边长")
        self._grid_size_spin.setVisible(False)
        r.addWidget(self._grid_size_spin)
        self._grid_mode_combo = QComboBox()
        self._grid_mode_combo.addItem("六边形", "hex")
        self._grid_mode_combo.addItem("方形", "square")
        self._grid_mode_combo.setFixedWidth(78)
        self._grid_mode_combo.setFixedHeight(22)
        self._grid_mode_combo.setVisible(False)
        r.addWidget(self._grid_mode_combo)
        self._grid_size_spin.valueChanged.connect(self._sync_grid_params)
        self._grid_mode_combo.currentIndexChanged.connect(self._sync_grid_params)
        r.addWidget(self._tool_sep())          # 绘制组 | 显示组
        self._switch_btn = self._hud_btn("道岔条", _PRIMARY)
        self._switch_btn.setCheckable(True)
        self._switch_btn.setChecked(True)
        self._switch_btn.toggled.connect(self._on_switch_bar_toggle)
        r.addWidget(self._switch_btn)
        self._radius_btn = self._hud_btn("覆盖圈", _GREEN)
        self._radius_btn.setCheckable(True)
        self._radius_btn.setChecked(True)
        self._radius_btn.toggled.connect(self._on_radius_toggle)
        r.addWidget(self._radius_btn)
        r.addWidget(self._tool_sep())
        self._play_btn = self._hud_btn("播放", _GREEN)
        self._play_btn.setCheckable(True)
        self._play_btn.toggled.connect(self._on_play_toggle)
        r.addWidget(self._play_btn)
        self._reset_btn = self._hud_btn("复位", _FG_DIM)
        self._reset_btn.clicked.connect(self._on_reset)
        r.addWidget(self._reset_btn)
        r.addStretch()
        return w

    def _build_hud_params(self) -> QWidget:
        """参数行: 存档槽 / 页面 / 最小半径 / 吸附 — 标签右对齐 11px 次级色
        (widget_kit.prop_row 同一版式体系, 横排紧凑变体)。"""
        w = QWidget()
        w.setFixedHeight(28)
        r = QHBoxLayout(w)
        r.setContentsMargins(8, 3, 8, 3)
        r.setSpacing(6)
        r.addWidget(QLabel("存档点"), 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._slot_combo = QComboBox()
        self._slot_combo.setEditable(True)
        self._slot_combo.setMinimumWidth(90)
        self._slot_combo.addItem("默认")
        self._slot_combo.setToolTip("存档点名称：选择/输入一个存档点，保存或加载该存档点")
        r.addWidget(self._slot_combo)
        self._save_page_btn = self._hud_btn("保存到槽", _GREEN, small=True)
        self._save_page_btn.setToolTip("把当前页所有火车/轨道/道岔保存到选中的存档点(随项目持久化)")
        self._save_page_btn.clicked.connect(self._on_save_page)
        r.addWidget(self._save_page_btn)
        self._load_page_btn = self._hud_btn("加载槽", _AMBER, small=True)
        self._load_page_btn.setToolTip("从选中的存档点加载当前页的火车/轨道/道岔设置")
        self._load_page_btn.clicked.connect(self._on_load_page)
        r.addWidget(self._load_page_btn)
        r.addWidget(self._tool_sep())
        page_lab = QLabel("页面")
        page_lab.setStyleSheet(f"color:{_FG_DIM};font-size:11px;background:transparent;")
        page_lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        page_lab.setMinimumWidth(40)
        r.addWidget(page_lab)
        self._page_combo = QComboBox()
        self._page_combo.setMinimumWidth(160)
        self._page_combo.setToolTip("TopView 页面 — 每页独立场景(轨道/火车/道岔)")
        self._page_combo.currentIndexChanged.connect(self._on_page_combo_changed)
        r.addWidget(self._page_combo)
        r.addWidget(self._tool_sep())
        r.addWidget(QLabel("最小半径"), 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        r.addWidget(self._make_min_radius_spin())
        r.addSpacing(4)
        r.addWidget(QLabel("吸附"), 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        r.addWidget(self._make_snap_tol_spin())
        # R12 平滑强度下拉 + 平滑预览按钮（逻辑在 runner_smooth_opts）
        inject_smooth_row(self, r)
        # R7 音频调制入口（逻辑在 runner_audio_mod）
        audio_btn = self._hud_btn("音频调制", _PRIMARY, small=True)
        audio_btn.setToolTip("音频特征（音量/onset）经曲线映射到油门（R7）")
        audio_btn.clicked.connect(lambda: open_audio_mod_dialog(self))
        r.addWidget(audio_btn)
        r.addStretch()
        # 行内标签统一样式(widget_kit.prop_row 版式: 11px text_secondary)
        for i in range(r.count()):
            it = r.itemAt(i)
            wid = it.widget() if it is not None else None
            if isinstance(wid, QLabel) and wid.styleSheet() == "":
                wid.setStyleSheet(
                    f"color:{_FG_DIM};font-size:11px;background:transparent;")
                wid.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
                wid.setMinimumWidth(wid.minimumWidth() or 48)
        return w

    @staticmethod
    def _tool_sep() -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 16)
        line.setStyleSheet(f"background:{SEP_LINE};border:none;")
        return line

    def _build_switch_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(42)
        scope_qss(bar, f"background:{_PANEL};border-bottom:1px solid {SEP_LINE};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        lbl = QLabel("道岔条")
        lbl.setStyleSheet(f"color:{_FG_DIM};font-size:11px;background:transparent;")
        lay.addWidget(lbl)
        # 用户定案: 分叉/合并同为轨道节点, 仅保留分叉作道岔入口; 合并兼容旧场景读取。
        for kind in ("split", "uncouple", "couple"):
            lay.addWidget(_SwitchBlock(kind))
        lay.addStretch()
        tip = QLabel("拖到轨道上放置 · 右击画布上的道岔可删除/换向")
        tip.setStyleSheet(f"color:{TEXT_DISABLED};font-size:11px;background:transparent;")
        lay.addWidget(tip)
        self._switch_bar = bar
        return bar

    def _build_status_bar(self) -> QWidget:
        """三段状态栏: 左=操作提示 / 中=已激活 N 台·本页 M 台 / 右=CE|PY·帧耗时。"""
        bar = QWidget()
        bar.setFixedHeight(24)
        bar.setStyleSheet(f"background:{BG_PANEL_SUB};border-top:1px solid {SEP_LINE};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 2, 10, 2)
        lay.setSpacing(8)
        self._status_left = QLabel("就绪")
        self._status_left.setStyleSheet(
            f"color:{_FG_DIM};font-size:11px;background:transparent;")
        lay.addWidget(self._status_left, 1)
        self._status_mid = QLabel("已激活 0 台 / 本页 0 台")
        self._status_mid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_mid.setStyleSheet(
            f"color:{_GREEN};font-size:11px;background:transparent;")
        lay.addWidget(self._status_mid, 2)
        self._status_right = QLabel("PY · 0.0ms/帧")
        mono = QFont()
        mono.setFamilies(["Consolas", "DejaVu Sans Mono"])
        mono.setPixelSize(10)
        self._status_right.setFont(mono)
        self._status_right.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_right.setStyleSheet(
            f"color:{_FG_DIM};background:transparent;")
        lay.addWidget(self._status_right, 1)
        return bar

    # ── 车队/车厢状态栏(借鉴 树形列表行解剖) ─────────
    def _build_fleet_panel(self) -> QWidget:
        """右侧栏: PanelSection 分区视觉分组(不动任何信号)。

        构建逻辑在 runner_fleet_panel（W2 腾行数：本文件超基线 4084 行）；本方法
        只保留旧住址与文档，行为逐行等价（行解剖对齐专业时间轴行）。
        """
        return build_fleet_panel(self)

    def _track_row_widget(self, tid: str, owner_name: str, pts: int) -> QWidget:
        """Dope-sheet 轨道行: 名称 12px + 元数据 11px 次级色右对齐。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 0, 8, 0)
        h.setSpacing(8)
        name = QLabel(self._track_label(tid))
        name.setStyleSheet(
            f"color:{_FG};font-size:12px;background:transparent;")
        meta = QLabel(f"{owner_name} · {pts}点")
        meta.setStyleSheet(
            f"color:{_FG_DIM};font-size:11px;background:transparent;")
        meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(name)
        h.addStretch(1)
        h.addWidget(meta)
        return row

    def _fleet_update_sel_label(self) -> None:
        n = len(self._fleet_tree.selectedItems())
        if self._fleet_sel_lbl is not None:
            self._fleet_sel_lbl.setText(f"已选 {n}")

    @staticmethod
    def _style_meta_item(item) -> None:
        """Dope-sheet 元数据列: 11px Consolas、text_secondary、右对齐。"""
        item.setTextAlignment(
            1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        item.setForeground(1, QColor(TEXT_SECONDARY))
        f = QFont()
        f.setFamilies(["Consolas", "DejaVu Sans Mono"])
        f.setPixelSize(11)
        item.setFont(1, f)

    def _on_fleet_item_double_clicked(self, item, _column) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        tid = data.get("tid")
        if not tid:
            return
        self._selected_train_id = str(tid)
        self._refresh_card_selection()
        name = self._train_names.get(str(tid), str(tid))
        self._set_status(f"已选中 {name}: 可用油门/速度/方向控件实时调整")

    @staticmethod
    def _base_train_key(tid: str) -> str:
        return str(tid).split("#uncouple#", 1)[0]

    def _fleet_selected_train_ids(self) -> list:
        out: list = []
        for item in self._fleet_tree.selectedItems():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "train":
                tid = data.get("tid")
                if tid is not None and tid not in out:
                    out.append(tid)
        return out

    def _fleet_apply_mode(self, mode: str) -> None:
        if not mode:
            return
        tids = self._fleet_selected_train_ids()
        if not tids:
            return
        for tid in tids:
            self._engine.set_train_mode(tid, mode)
            combo = self._mode_combos.get(tid)
            if combo is not None:
                idx = combo.findData(mode)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        self._refresh_fleet_tree()
        self._set_status(f"已批量切换 {len(tids)} 列火车 → "
                         f"{_TRAIN_MODE_LABELS.get(mode, mode)}(实时生效)")

    def _fleet_apply_throttle(self, v: int) -> None:
        tids = self._fleet_selected_train_ids()
        if not tids:
            return
        from .runner_fleet_motion import set_throttle_with_panel_motion
        for tid in tids:
            set_throttle_with_panel_motion(self._engine, self, tid, v / 100.0)
            slider = self._throttle_sliders.get(tid)
            if slider is not None:
                slider.setValue(int(v))
            label = self._throttle_labels.get(tid)
            if label is not None:
                label.setText(f"{v}%")
        self._refresh_fleet_tree()

    def _fleet_detach_selected(self) -> None:
        picks: dict = {}
        for item in self._fleet_tree.selectedItems():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "car":
                tid = data.get("tid")
                idx = int(data.get("idx", -1))
                if tid is not None and idx > 0 and (
                        tid not in picks or idx < picks[tid]):
                    picks[tid] = idx
        if not picks:
            self._set_status("请先在状态栏选中要解挂的车厢(Ctrl/Shift 多选)")
            return
        for tid, idx in picks.items():
            self._engine.detach_train_cars(tid, idx)
        self._tick_impl(0.0)
        self._refresh_fleet_tree()
        self._set_status(f"已在 {len(picks)} 列火车处解挂车厢 → 后段成为独立子列车")

    def _fleet_merge_selected(self) -> None:
        """R13 回并：把选中的分队并回其主列车（尾部对齐；逻辑在 runner_merge_ops）。"""
        from .runner_merge_ops import merge_selected_units
        n = merge_selected_units(self)
        if n:
            self._tick_impl(0.0)
            self._refresh_fleet_tree()
        self._set_status(f"已回并 {n} 个分队（尾部对齐，链长表顺接）" if n
                         else "请先选中要回并的分队（车队树里带「子列」标记的行）")

    def _clear_selected_train_tracks(self) -> None:
        """清除选中火车被画的全部轨道(多选=车队状态树 Ctrl/Shift 选中)。"""
        tids = self._fleet_selected_train_ids()
        if not tids and self._selected_train_id:
            tids = [self._selected_train_id]
        tids = [str(t) for t in tids]
        if not tids:
            self._set_status("请先选中要清除轨道的火车(车队状态树 Ctrl/Shift 多选)")
            return
        tid_set = set(tids)
        home_ids = {str(self._engine._trains[t]._home_edge_id or "")
                    for t in tid_set if t in self._engine._trains}
        removed = [tid for tid, track in self._tracks.items()
                   if str(track.get("owner_train_id", "") or "") in tid_set
                   or tid in home_ids]
        if not removed:
            self._set_status("选中的火车没有可清除的轨道")
            return
        drop_tracks(self, removed)
        self._rebuild_engine()
        self._refresh_canvas_scene()
        self._preview_train_visuals()
        self._save_scene(self._current_page_id)
        self._set_status(f"已清除 {len(removed)} 条选中火车轨道")

    def _current_slot_name(self) -> str:
        """读取/创建当前存档点名(默认“默认”)。"""
        if self._slot_combo is None:
            return "默认"
        txt = str(self._slot_combo.currentText() or "").strip() or "默认"
        if self._slot_combo.findText(txt) < 0:
            self._slot_combo.addItem(txt)
            self._slot_combo.setCurrentText(txt)
        return txt

    def _refresh_track_list(self) -> None:
        if not hasattr(self, "_track_list") or self._track_list is None:
            return
        sig = tuple(
            (str(tid), int(t.get("number", 0) or 0),
             str(t.get("owner_train_id", "") or ""),
             len(t.get("points") or []))
            for tid, t in self._tracks.items()
        )
        if sig == self._track_list_sig:
            return  # 轨道没变化时不要反复重建, 避免干扰用户正在操作的下拉框
        self._track_list_sig = sig
        self._track_list.clear()
        for tid, t in self._tracks.items():
            owner = str(t.get("owner_train_id", "") or "")
            owner_name = self._train_names.get(owner, owner) if owner else "公共"
            pts = len(t.get("points") or [])
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(tid))
            row = self._track_row_widget(str(tid), owner_name, pts)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            item.setSizeHint(QSize(10, 24))
            self._track_list.addItem(item)
            self._track_list.setItemWidget(item, row)
        self._refresh_train_track_combos()

    def _refresh_train_track_combos(self) -> None:
        for tid, combo in list(self._track_combos.items()):
            current = str(combo.currentData() or "")
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("未分配", "")
            for t_id in self._tracks:
                combo.addItem(self._track_label(t_id), str(t_id))
            tr = self._engine._trains.get(tid)
            target = current if current and current in self._tracks else (
                str(tr._home_edge_id or "") if tr and tr._home_edge_id in self._tracks else "")
            if target:
                idx = combo.findData(target)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                else:
                    combo.setCurrentIndex(0)
            else:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)

    def _delete_selected_tracks(self) -> None:
        if not hasattr(self, "_track_list") or self._track_list is None:
            return
        removed = {
            str(self._track_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self._track_list.count())
            if self._track_list.item(i).isSelected()
        }
        removed = {r for r in removed if r}
        if not removed:
            self._set_status("请先在轨道列表选择要删除的轨道(Ctrl/Shift 多选)")
            return
        drop_tracks(self, removed)      # R9 快照 + 删轨 + 剪道岔
        self._rebuild_engine()
        self._refresh_canvas_scene()
        self._preview_train_visuals()
        self._save_scene(self._current_page_id)
        self._refresh_track_list()
        self._set_status(f"已删除 {len(removed)} 条轨道")

    def _delete_selected_trains(self) -> None:
        tids = self._fleet_selected_train_ids()
        if not tids and self._selected_train_id:
            tids = [self._selected_train_id]
        tids = [str(t) for t in tids]
        if not tids:
            self._set_status("请先在车队状态树选择要删除的火车(Ctrl/Shift 多选)")
            return
        snapshot_route_undo(self)   # R9：删车前压 DOMAIN_ROUTE 快照
        for tid in list(tids):
            if tid in self._train_names:
                self._on_remove_train(tid)
        self._tick_impl(0.0)
        self._refresh_fleet_tree()
        self._refresh_canvas_scene()
        self._save_scene(self._current_page_id)
        self._set_status(f"已删除 {len(tids)} 列火车")

    def _on_save_page(self) -> None:
        """把当前页保存到选中的存档点(可多个存档点, 类似 Excel 不同 sheet)。"""
        if not self._current_page_id:
            self._set_status("当前没有可保存的页面")
            return
        slot = self._current_slot_name()
        self._save_scene(self._current_page_id)
        self._save_slots.setdefault(self._current_page_id, {})[slot] = dict(
            self._page_scenes.get(self._current_page_id, {}))
        proj = self._project()
        if proj is not None:
            try:
                proj.route_animator_state = self.save_state()
            except Exception:
                logger.debug("route_animator: save_state to project failed", exc_info=True)
            try:
                proj.project_changed.emit()
            except Exception:
                logger.debug("route_animator: project_changed emit failed", exc_info=True)
        self._set_status(f"本页已保存到存档点「{slot}」(火车 {len(self._engine._trains)} 列, 轨道 {len(self._tracks)} 条)")

    def _on_load_page(self) -> None:
        """从选中的存档点加载当前页的火车/轨道/道岔设置。"""
        if not self._current_page_id:
            self._set_status("当前没有可加载的页面")
            return
        slot = self._current_slot_name()
        slots = self._save_slots.get(self._current_page_id, {})
        scene = slots.get(slot)
        if scene is None and slot == "默认":
            scene = self._page_scenes.get(self._current_page_id)
        if scene is None:
            self._set_status(f"当前页还没有存档点「{slot}」，先点「保存到槽」")
            return
        self._page_scenes[self._current_page_id] = scene
        self._restore_scene(self._current_page_id)
        self._set_status(f"已从存档点「{slot}」加载本页(火车/轨道/道岔已恢复)")

    def _refresh_fleet_tree(self) -> None:
        """每 5 帧刷新一次: 按解挂单元分组, 显示实时解连挂/速度/方向/油门仪表。"""
        trains = self._engine._trains
        sig = tuple((tid, len(tr.cars), bool(tr.held_node), bool(tr._attracted))
                    for tid, tr in trains.items())
        selected = self._fleet_selected_train_ids() if self._fleet_tree else []
        changed = False
        if sig != self._fleet_sig:
            changed = True
            self._fleet_sig = sig
            self._fleet_tree.clear()
            groups: dict[str, list[str]] = {}
            for tid in trains:
                groups.setdefault(self._base_train_key(str(tid)), []).append(str(tid))
            for base, tids in groups.items():
                base_name = self._train_names.get(base, base)
                parent = QTreeWidgetItem([f"{base_name} · {len(tids)}单元", ""])
                parent.setData(0, Qt.ItemDataRole.UserRole,
                               {"kind": "group", "base": base})
                parent.setExpanded(True)
                self._fleet_tree.addTopLevelItem(parent)
                for tid in tids:
                    tr = trains.get(tid)
                    if tr is None:
                        continue
                    tag = "主列" if tid == base else "子列"
                    name = self._train_names.get(tid, tid)
                    titem = QTreeWidgetItem([f"{name} ({tag})", ""])
                    titem.setData(0, Qt.ItemDataRole.UserRole,
                                  {"kind": "train", "tid": tid})
                    parent.addChild(titem)
                    for j in range(len(tr.cars)):
                        citem = QTreeWidgetItem([f"车厢 #{j}", ""])
                        citem.setData(0, Qt.ItemDataRole.UserRole,
                                      {"kind": "car", "tid": tid, "idx": j})
                        titem.addChild(citem)
            if selected:
                def _reselect(item):
                    data = item.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict) and data.get("tid") in selected:
                        item.setSelected(True)
                    for k in range(item.childCount()):
                        _reselect(item.child(k))
                for i in range(self._fleet_tree.topLevelItemCount()):
                    _reselect(self._fleet_tree.topLevelItem(i))
        for i in range(self._fleet_tree.topLevelItemCount()):
            parent = self._fleet_tree.topLevelItem(i)
            pdata = parent.data(0, Qt.ItemDataRole.UserRole) or {}
            base = str(pdata.get("base", "") or "")
            unit_count = 0
            for j in range(parent.childCount()):
                titem = parent.child(j)
                data = titem.data(0, Qt.ItemDataRole.UserRole) or {}
                tid = str(data.get("tid", "") or "")
                tr = trains.get(tid)
                if tr is None:
                    continue
                unit_count += 1
                tag = "主列" if tid == self._base_train_key(tid) else "子列"
                state = ("连挂吸附中" if tr._attracted else
                         "停用" if tr.ended else
                         "等待连挂" if tr.held_node is not None else "运行中")
                if "#uncouple#" in tid:
                    state = "解挂单元 · " + state
                track_txt = (self._track_label(tr._home_edge_id)
                             if tr._home_edge_id and tr._home_edge_id in self._tracks
                             else "未分配")
                titem.setText(
                    0, f"{self._train_names.get(tid, tid)} ({tag}) · {len(tr.cars)}节")
                titem.setText(
                    1, f"{track_txt} · {_TRAIN_MODE_LABELS.get(tr.mode, tr.mode)} · "
                       f"{round(tr.base_speed * tr.throttle * tr.dir_sign, 1)}u/s · "
                       f"{int(round(tr.throttle * 100))}% · {state}")
                self._style_meta_item(titem)
                for k in range(titem.childCount()):
                    citem = titem.child(k)
                    if k < len(tr.cars):
                        c = tr.cars[k]
                        citem.setText(
                            1, f"s={c.s:.1f} · "
                               f"{round(tr.base_speed * tr.throttle * tr.dir_sign, 1)}u/s")
                        self._style_meta_item(citem)
            if base:
                parent.setText(0, f"{self._train_names.get(base, base)} · {unit_count}单元")
        self._refresh_track_list()
        if changed:
            self._refresh_train_manager_if_open()

    def _build_train_deck(self) -> QWidget:
        deck = QWidget()
        scope_qss(deck, f"background:{BG_PANEL_SUB};border-top:1px solid {SEP_LINE};")
        self._deck_lay = QHBoxLayout(deck)
        self._deck_lay.setContentsMargins(8, 6, 8, 6)
        self._deck_lay.setSpacing(8)
        self._deck_lay.addStretch()
        return deck

    def _make_min_radius_spin(self) -> NumSlider:
        """轨道平滑最小转弯半径(T5); 每个设置必有读取方(平滑管线)。

        D波: QDoubleSpinBox → widget_kit.NumSlider(值绘在滑槽上)。
        NumSlider 内部以 10^decimals 整数刻度工作, 回调按小数位换算回浮点。
        """
        s = NumSlider(5.0, 200.0, float(self._min_radius), decimals=1)
        s.setFixedWidth(116)
        s.setToolTip("画轨道自动平滑的最小转弯半径(世界单位)")
        s.valueChanged.connect(lambda v: setattr(self, "_min_radius", v / 10.0))
        return s

    def _make_snap_tol_spin(self) -> NumSlider:
        """吸附容差(T5 闭合判定 / T6 吸附接线); 每个设置必有读取方。

        D波: 同上改 NumSlider(2~60 世界单位, 1 位小数)。
        """
        s = NumSlider(2.0, 60.0, float(self._snap_tol_value), decimals=1)
        s.setFixedWidth(100)
        s.setToolTip("轨道闭合判定 / 道岔接线吸附容差(世界单位)")
        s.valueChanged.connect(lambda v: setattr(self, "_snap_tol_value", v / 10.0))
        return s

    def _hud_btn(self, text: str, accent: str, small: bool = False) -> QPushButton:
        """头行按钮(26px) / 参数行小按钮(22px): 扁平 + 语义色 checked。

        checked 用 accent 填充仅作开关状态强调(语义色容器, §3 允许);
        默认面交给全局主题——这里只做透明化与 hover 特化的布局特化。
        """
        b = QPushButton(text)
        h = 22 if small else 26
        b.setFixedHeight(h)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_FG};"
            f"border:1px solid rgba(255,255,255,20);border-radius:4px;"
            f"padding:0 12px;}}"
            f"QPushButton:hover{{background:{BG_PANEL};border-color:rgba(255,255,255,44);}}"
            f"QPushButton:pressed{{background:{_PRIMARY};border-color:{_PRIMARY};"
            f"color:{TEXT_SELECTED_FG};}}"
            f"QPushButton:checked{{background:{accent};color:{TEXT_SELECTED_FG};"
            f"border-color:{accent};}}"
            f"QPushButton:checked:hover{{background:{accent};}}")
        key = {"添加火车": "track_add", "播放": "play", "复位": "layout_reset"}.get(text)
        if _make_icon and key:
            try:
                b.setIcon(_make_icon(key, 15))
            except Exception:
                pass
        return b

    # ── 火车卡片 ────────────────────────────────────────────────────────
    def _build_train_card(self, tid: str, derived: bool = False) -> None:
        tr = self._engine._trains.get(tid)
        if tr is None:
            return
        name = self._train_names.get(tid, tid)
        if derived:
            name = f"⇠解挂 {name}"  # 引擎分裂出的分队, 生命周期由引擎管理(禁手删)
        card = _TrainCard(tid)
        card.clicked.connect(self._on_select_train)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # 扁平卡片: BG_BOX 面 + 中性细边 + 左侧火车色条(用户资产色, 不动)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setStyleSheet(
            f"QFrame{{background:{_ELEV};border:1px solid rgba(255,255,255,20);"
            f"border-left:3px solid {tr.color};border-radius:4px;}}"
            f"QLabel{{color:{_FG};font-size:11px;background:transparent;}}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        swatch = QLabel("")
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(f"background:{tr.color};border-radius:6px;")
        top.addWidget(swatch)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(f"color:{tr.color};font-weight:bold;")
        top.addWidget(name_lbl)
        top.addStretch()
        bind_btn = self._card_btn("摇杆", _PRIMARY)
        bind_btn.clicked.connect(lambda _=False, t=tid: self._on_bind_joystick(t))
        top.addWidget(bind_btn)
        if not derived:
            del_btn = self._card_btn("删除", _RED)
            del_btn.clicked.connect(lambda _=False, t=tid: self._on_remove_train(t))
            top.addWidget(del_btn)
        lay.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(6)
        for label, lo, hi, val, dec, field in card_param_specs(
                tr, self._fallback_car_unit):
            self._add_dual_spec(row, label, lo, hi, val, dec, field, tid)
        row.addStretch()
        lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(QLabel("模式"))
        mode_combo = QComboBox()
        for key, label in _TRAIN_MODE_LABELS.items():
            mode_combo.addItem(label, key)
        idx = mode_combo.findData(tr.mode)
        mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        mode_combo.setToolTip("循环正跑/循环反跑/返程/单程正跑/单程反跑 — 实时生效")
        mode_combo.currentIndexChanged.connect(
            lambda _i, t=tid, c=mode_combo: self._on_train_mode(
                t, str(c.itemData(c.currentIndex()))))
        row2.addWidget(mode_combo)
        skip_cb = QCheckBox("跳过连接处")
        skip_cb.setChecked(bool(tr.skip_gaps))
        skip_cb.setToolTip("设备组之间的空余段瞬移跳过, 激活不中断")
        skip_cb.toggled.connect(lambda v, t=tid: self._on_train_skip(t, bool(v)))
        row2.addWidget(skip_cb)
        row2.addStretch()
        lay.addLayout(row2)
        row3 = QHBoxLayout()
        row3.setSpacing(6)
        row3.addWidget(QLabel("轨道"))
        track_combo = QComboBox()
        track_combo.addItem("未分配", "")
        for t_id, t_data in self._tracks.items():
            track_combo.addItem(self._track_label(t_id), str(t_id))
        cur_track = str(tr._home_edge_id or "")
        if cur_track and cur_track in self._tracks:
            track_combo.setCurrentIndex(track_combo.findData(cur_track))
        else:
            track_combo.setCurrentIndex(0)
        track_combo.currentIndexChanged.connect(
            lambda _i, t=tid, c=track_combo: self._on_train_track_changed(
                t, str(c.itemData(c.currentIndex()) or "")))
        track_combo.setToolTip("选择这列火车所在的轨道；选择“未分配”可临时停用轨道归属")
        row3.addWidget(track_combo, 1)
        enable_cb = QCheckBox("启用")
        enable_cb.setChecked(not bool(tr.ended))
        enable_cb.setToolTip("停用后这列火车暂停运行，启用后继续")
        enable_cb.toggled.connect(lambda v, t=tid: self._on_train_enabled(t, bool(v)))
        row3.addWidget(enable_cb)
        row3.addStretch()
        lay.addLayout(row3)
        trow = QHBoxLayout()
        trow.setSpacing(6)
        trow.addWidget(QLabel("油门"))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(int(round(tr.throttle * 100.0)))
        slider.valueChanged.connect(lambda v, t=tid: self._on_throttle(t, v))
        trow.addWidget(slider, 1)
        tspin = self._spin(0, 100, int(round(tr.throttle * 100.0)))
        tspin.setToolTip("手动输入油门百分比")
        tspin.valueChanged.connect(lambda v, t=tid, sl=slider: (
            sl.blockSignals(True),
            sl.setValue(v),
            sl.blockSignals(False),
            self._on_throttle(t, v),
        ))
        trow.addWidget(tspin)
        val_lbl = QLabel(f"{int(round(tr.throttle * 100.0))}%")
        val_lbl.setFixedWidth(38)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        trow.addWidget(val_lbl)
        lay.addLayout(trow)
        # R3 补遗：卡片内嵌常驻油门包络缩略（点击展开编辑器；逻辑在 runner_envelope_strip）
        attach_envelope_strip(self, card, lay, tid)
        self._cards[tid] = card
        self._throttle_sliders[tid] = slider
        self._throttle_spins[tid] = tspin
        self._throttle_labels[tid] = val_lbl
        self._mode_combos[tid] = mode_combo
        self._track_combos[tid] = track_combo
        self._enabled_checkboxes[tid] = enable_cb

    def _card_btn(self, text: str, accent: str) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(22)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{background:transparent;color:{accent};"
            f"border:1px solid rgba(255,255,255,24);border-radius:4px;"
            f"padding:0 8px;font-size:11px;}}"
            f"QPushButton:hover{{background:{BG_PANEL};border-color:{accent};}}"
            f"QPushButton:pressed{{background:{accent};color:{TEXT_SELECTED_FG};}}")
        return b

    def _spin(self, lo: int, hi: int, val: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setFixedWidth(80)
        return s

    def _dspin(self, lo: float, hi: float, val: float, dec: int) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setDecimals(dec)
        s.setFixedWidth(110)
        return s

    def _add_dual_spec(self, layout, label: str, lo, hi, val, dec: int,
                       field: str, tid: str) -> None:
        """参数行：数值输入=实际值（可无上限自定义）；滑块=该值的快捷调整。

        ★ 2026-09-12：旧实现把数值框当"可调上限"（setRange(lo,hi)、初值=hi），
        用户无法输入超过页面默认量程的值（用户反馈"设置火车各参数没法无上限
        自定义输入值"）。现改为：数值框 = 实际值本身，上限放到类型允许的最大
        （int 2147483647 / double 1e12）→ 可任意输入；滑块按 hi 作为默认量程做
        快捷调整，实际值超出默认量程时自动扩展滑块量程，保证滑块仍代表当前值。
        """
        layout.addWidget(QLabel(label))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 1000)
        slider.setFixedWidth(70)

        if dec > 0:
            spin = self._dspin(float(lo), 1e12, float(val), dec)
        else:
            spin = self._spin(int(lo), 2147483647, int(val))
        spin.setFixedWidth(90)
        spin.setToolTip("实际值：可直接输入任意数值（无上限）")

        value_lbl = QLabel("")
        value_lbl.setFixedWidth(64)
        value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_lbl.setStyleSheet(f"color:{TEXT_PRIMARY};font-size:11px;background:transparent;")

        def _fmt(v):
            return f"{float(v):.{dec}f}" if dec > 0 else str(int(round(v)))

        def _slider_hi():
            """滑块量程上限 = 默认量程 hi 与实际值的较大者（值超出时自动扩展）。"""
            return max(float(hi), float(spin.value()), float(lo) + 1e-9)

        def _to_slider(v):
            span = _slider_hi() - float(lo)
            return 0 if span <= 0 else int(round((float(v) - float(lo)) / span * 1000.0))

        def _from_slider(sv):
            span = _slider_hi() - float(lo)
            return float(lo) + span * float(sv) / 1000.0

        slider.setValue(_to_slider(val))
        value_lbl.setText(_fmt(val))

        def _on_slider(sv, _t=tid, _f=field, _lbl=value_lbl, _sp=spin):
            v = _from_slider(sv)
            _lbl.setText(_fmt(v))
            # 滑块拖动 = 直接设定实际值（数值框同步，不触发二次写入）
            _sp.blockSignals(True)
            _sp.setValue(v)
            _sp.blockSignals(False)
            self._set_train_field(_t, _f, v)

        def _on_spin(_v, _t=tid, _f=field, _sl=slider, _lbl=value_lbl):
            # 数值框 = 实际值本身；同步滑块位置（必要时自动扩展量程）
            v = float(_v)
            _lbl.setText(_fmt(v))
            _sl.blockSignals(True)
            _sl.setValue(_to_slider(v))
            _sl.blockSignals(False)
            self._set_train_field(_t, _f, v)

        slider.valueChanged.connect(_on_slider)
        spin.valueChanged.connect(_on_spin)
        layout.addWidget(slider)
        layout.addWidget(value_lbl)
        layout.addWidget(spin)
        bind_btn = QPushButton("绑")
        bind_btn.setFixedWidth(24)
        bind_btn.setToolTip(f"将「{label}」拖动条绑定到摇杆轴")
        bind_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_PRIMARY};"
            f"border:1px solid {_BORDER};border-radius:3px;font-size:10px;}}"
            f"QPushButton:hover{{border-color:{_PRIMARY};}}")
        bind_btn.clicked.connect(
            lambda _=False, t=tid, f=field, l=label: self._bind_param_to_joystick(t, f, l))
        layout.addWidget(bind_btn)
        self._param_sliders.setdefault(tid, {})[field] = slider
        self._param_spins.setdefault(tid, {})[field] = spin
        self._param_value_labels.setdefault(tid, {})[field] = value_lbl
        self._param_specs.setdefault(tid, {})[field] = (float(lo), float(hi), int(dec))

    # ── 交互 ────────────────────────────────────────────────────────────
    def _open_train_manager(self) -> None:
        if self._train_manager_dlg is None:
            from ..editor_workspace_regions import popup
            self._train_manager_dlg = popup(TrainManagerDialog(self, self))
            self._train_manager_dlg.destroyed.connect(
                lambda: setattr(self, "_train_manager_dlg", None))
        self._train_manager_dlg.show()
        self._train_manager_dlg.raise_()
        self._train_manager_dlg.activateWindow()
        self._train_manager_dlg.rebuild()
        self._train_manager_dlg._refresh_train_combo()

    def _refresh_train_manager_if_open(self) -> None:
        dlg = self._train_manager_dlg
        if dlg is not None:
            dlg.rebuild()
            dlg._refresh_train_combo()

    def _on_add_train(self) -> None:
        tid = self.add_train()
        name = self._train_names.get(tid, "火车")
        self._set_status(f"已添加 {name}")

    def _on_remove_train(self, tid: str) -> None:
        self._engine.remove_train(tid)
        self._train_names.pop(tid, None)
        card = self._cards.pop(tid, None)
        if card is not None:
            self._deck_lay.removeWidget(card)
            card.deleteLater()
        self._throttle_sliders.pop(tid, None)
        self._throttle_spins.pop(tid, None)
        self._throttle_labels.pop(tid, None)
        self._param_sliders.pop(tid, None)
        self._param_spins.pop(tid, None)
        self._param_specs.pop(tid, None)
        self._param_value_labels.pop(tid, None)
        self._pending_throttle.pop(tid, None)
        self._mode_combos.pop(tid, None)
        self._track_combos.pop(tid, None)
        self._enabled_checkboxes.pop(tid, None)
        self._selected_chains.pop(tid, None)
        if self._selected_train_id == tid:
            self._selected_train_id = next(reversed(self._train_names), None)
        self._refresh_card_selection()
        self._tick_impl(0.0)
        self._refresh_fleet_tree()
        if not self._engine._trains:
            self._clear_route_mask()

    def _on_draw_toggle(self, checked: bool) -> None:
        if checked and self._grid_btn.isChecked():
            self._grid_btn.setChecked(False)   # 画轨道/网格建造互斥
        self._canvas.set_track_draw_mode(bool(checked))
        if checked:
            target = self._selected_train_id or (next(reversed(self._train_names), None))
            name = self._train_names.get(target, "火车") if target else "火车"
            self._set_status(f"为 {name} 画轨道：点击或按住扫过设备连成链，双击/回车完成")

    def _on_grid_toggle(self, checked: bool) -> None:
        self._grid_size_spin.setVisible(checked)
        self._grid_mode_combo.setVisible(checked)
        self._canvas._snap.configure(checked, self._grid_mode_combo.currentData() or "hex",
                                     float(self._grid_size_spin.value()))
        self._canvas.update()

    def _sync_grid_params(self) -> None:
        if self._canvas._snap.enabled:   # 激活中改格距/形状即时生效
            self._on_grid_toggle(True)

    def _on_track_drawn(self, payload) -> None:
        if isinstance(payload, dict):
            raw_points = payload.get("points") or []
            start_node = payload.get("start_node")
            end_node = payload.get("end_node")
        else:  # 兼容旧列表调用(测试)
            raw_points = payload or []
            start_node = end_node = None
        chain = [tuple(p) for p in raw_points]
        if len(chain) < 2:
            self._draw_btn.setChecked(False)
            return
        snapshot_route_undo(self)   # R9：画轨手势起点压跑动域快照（可撤销）
        self._start_async_smooth(chain, start_node, end_node)

    def _start_async_smooth(self, chain, start_node, end_node) -> None:
        """长链平滑放到后台线程，UI 不冻结；完成后回到主线程建轨/刷新。

        R12：入参（段内插值密度/最小半径）由 HUD「平滑强度」档决定，见
        `runner_smooth_opts.apply_smooth_profile`；原始链一并记下供预览重算。
        """
        remember_raw_chain(self, chain)
        if len(chain) < 80:
            per_seg, rmin, extra = apply_smooth_profile(
                self, chain, self._snap_tol(), self._min_radius, len(chain))
            pts, closed, lap_count = smooth_chain(
                chain, self._snap_tol(), rmin, per_seg, extra)
            self._apply_smooth_result(
                {"start_node": start_node, "end_node": end_node},
                pts, closed, lap_count)
            return
        self._set_status(f"轨道平滑中…（强度 {smooth_strength_label(self)}）")
        self._draw_btn.setEnabled(False)
        snap_tol = self._snap_tol()
        per_seg, min_radius, extra = apply_smooth_profile(
            self, chain, snap_tol, self._min_radius, len(chain))
        holder: list = []
        def worker():
            try:
                holder.append(smooth_chain(chain, snap_tol, min_radius,
                                           per_seg, extra))
            except Exception:
                holder.append(None)
        self._smooth_pending = {"start_node": start_node, "end_node": end_node}
        self._smooth_holder = holder
        self._smooth_thread = threading.Thread(target=worker, daemon=True)
        self._smooth_thread.start()
        if not self._smooth_poll.isActive():
            self._smooth_poll.start()

    def _poll_smooth_thread(self) -> None:
        if self._smooth_thread is None:
            self._smooth_poll.stop()
            return
        if self._smooth_thread.is_alive():
            return
        self._smooth_thread.join(timeout=0.05)
        pending = self._smooth_pending or {}
        holder = getattr(self, "_smooth_holder", None) or []
        self._smooth_thread = None
        self._smooth_pending = None
        self._smooth_holder = None
        self._smooth_poll.stop()
        self._draw_btn.setEnabled(True)
        if holder and holder[0] is not None:
            pts, closed, lap_count = holder[0]
            self._apply_smooth_result(pending, pts, closed, lap_count)
        else:
            self._set_status("轨道平滑失败，请重试")

    def _next_track_number(self) -> int:
        return max(self._track_numbers.values(), default=0) + 1

    def _track_label(self, track_id: str) -> str:
        num = self._track_numbers.get(str(track_id))
        if num is None:
            return f"轨道 {str(track_id)[:8]}"
        return f"轨道 {num}"

    def _apply_smooth_result(self, pending: dict, pts, closed, lap_count) -> None:
        start_node = pending.get("start_node")
        end_node = pending.get("end_node")
        # 锚点端点: 平滑可能微移端点, 强制钉回节点位置(分岔必须精确衔接)
        if start_node:
            node = self._switches.get(str(start_node)) or {}
            pos = node.get("pos")
            if pos is not None and pts:
                pts[0] = (float(pos[0]), float(pos[1]))
        if end_node:
            node = self._switches.get(str(end_node)) or {}
            pos = node.get("pos")
            if pos is not None and pts:
                pts[-1] = (float(pos[0]), float(pos[1]))
        track_id = str(uuid.uuid4())
        self._engine.add_track(track_id, pts, lap_count=lap_count)
        num = self._next_track_number()
        self._track_numbers[track_id] = num
        self._tracks[track_id] = {"points": pts, "color": "#8fd0ff",
                                  "cum": _polyline_cumlen(pts),
                                  "closed": closed, "lap_count": lap_count,
                                  "number": num}
        if start_node or end_node:
            # 道岔节点接线: 从节点出发(分叉/解挂)或画进节点(合并/连挂)
            if start_node:
                self._engine.add_node_branch(str(start_node), track_id)
                self._record_branch(str(start_node), track_id)
            if end_node:
                self._engine.add_node_branch(str(end_node), track_id)
                self._record_branch(str(end_node), track_id)
            self._refresh_canvas_scene()
            self._draw_btn.setChecked(False)
            self._preview_train_visuals()
            self._set_status(f"支线已接入道岔节点 ({len(pts)} 点, 火车经节点自动分流)")
            return
        tid = self._selected_train_id or (next(reversed(self._train_names), None))
        if tid is None or tid not in self._train_names:
            self._draw_btn.setChecked(False)
            self._set_status("请先添加火车，再画轨道")
            return
        self._attach_train(tid, track_id, 0.0)
        self._tracks[track_id]["owner_train_id"] = tid
        self._refresh_canvas_scene()
        self._draw_btn.setChecked(False)
        self._preview_train_visuals()   # 火车立即停在轨道起点(播放前即可见, 不触发投影)
        name = self._train_names.get(tid, "火车")
        loop_txt = f", 闭环×{lap_count}" if closed and lap_count > 1 else (
            ", 闭环" if closed else "")
        self._set_status(f"{name} 轨道已绘制并平滑 ({len(pts)} 点{loop_txt})")

    def _record_branch(self, node_id: str, track_id: str) -> None:
        """把画出的支线记进道岔节点的 branch_params(存档/重建走既有 branches 路径)。"""
        node = self._switches.get(str(node_id))
        if node is None:
            return
        bp = node.setdefault("branch_params", {})
        merged: list = []
        if bp.get("branch"):
            merged.append(bp["branch"])
        if isinstance(bp.get("branches"), list):
            merged.extend(bp["branches"])
        seen: list = []
        for b in merged:
            if b not in seen:
                seen.append(b)
        if track_id not in seen:
            seen.append(track_id)
        bp.pop("branch", None)
        bp["branches"] = seen

    def _on_switch_bar_toggle(self, checked: bool) -> None:
        if self._switch_bar is not None:
            self._switch_bar.setVisible(bool(checked))

    def _on_radius_toggle(self, checked: bool) -> None:
        self._show_radius = bool(checked)
        self._tick_impl(0.0)

    def _on_switch_dropped(self, kind: str, track_id: str, s: float,
                           params: Optional[dict] = None) -> None:
        params = dict(params or {})
        replace_id = params.pop("replace_node", None)
        if replace_id and replace_id in self._switches:
            node = self._switches[replace_id]
            label = _SWITCH_LABELS.get(kind, kind)
            stacked = node.setdefault("stacked", [])
            if kind == node.get("kind") or kind in stacked:
                self._set_status(f"该点已放置「{label}」")
                return
            # 功能标牌叠加(如 分叉+解挂): 效果组合, 不再替换
            self._engine.stack_switch(replace_id, kind)
            stacked.append(kind)
            self._refresh_canvas_scene()
            combo = "＋".join(_SWITCH_LABELS.get(k, k)
                              for k in ([node["kind"]] + list(stacked)))
            self._set_status(f"已叠加「{label}」→ 组合效果: {combo}")
            return
        if ("branch" not in params and "branches" not in params):
            branch = self._auto_branch(kind, track_id, s)
            if branch is not None:
                params["branch"] = branch
        node_id = str(uuid.uuid4())
        self._add_switch(node_id, kind, track_id, s, params)
        self._refresh_canvas_scene()
        label = _SWITCH_LABELS.get(kind, kind)
        extra = ""
        if kind == "uncouple" and params.get("car_index"):
            extra = f" (解挂点 #{params['car_index']})"
        self._set_status(f"已放置「{label}」道岔{extra} (右击可删除)")

    def _auto_branch(self, kind: str, track_id: str, s: float) -> Optional[str]:
        """自动接分支: 分叉/解挂找「起点」贴着落点的轨道, 合并/连挂找「终点」贴着落点的轨道。

        这样用户只需把第二条轨道画到交汇处, 拖道岔时自动接线(命令节点语义)。
        """
        host = self._tracks.get(str(track_id))
        if host is None:
            return None
        x, y = _point_at_arclen(float(s), host["points"], host["cum"])
        tol = self._snap_tol()
        want_start = kind in ("split", "uncouple")
        best: Optional[str] = None
        best_d = tol
        for tid, t in self._tracks.items():
            if tid == str(track_id):
                continue
            pts = t.get("points") or []
            if len(pts) < 2:
                continue
            cand = pts[0] if want_start else pts[-1]
            d = math.hypot(x - cand[0], y - cand[1])
            if d <= best_d:
                best_d = d
                best = tid
        return best

    def _on_switch_direction(self, node_id: str, direction: int) -> None:
        """人工道岔: 切换分叉节点指向, 实时生效。"""
        self._engine.set_switch_direction(node_id, int(direction))
        node = self._switches.get(str(node_id))
        if node is not None:
            node["direction"] = int(direction)
        self._refresh_canvas_scene()
        names = ["主干"] + [f"支线{i}" for i in range(1, 8)]
        d = int(direction)
        self._set_status(f"道岔方向已切换: {names[d] if d < len(names) else d}")

    def _on_switch_delete(self, node_id: str) -> None:
        if str(node_id) not in self._switches:
            return
        drop_switch(self, node_id)               # R9 快照 + 摘除道岔（逻辑在同名模块）
        self._rebuild_engine()
        self._refresh_canvas_scene()
        self._tick_impl(0.0)
        self._refresh_fleet_tree()
        self._set_status("已删除道岔")

    def _on_play_toggle(self, checked: bool) -> None:
        self._playing = bool(checked)
        self._play_btn.setText("暂停" if checked else "播放")
        if checked:
            self._last_tick = time.monotonic()
            # ★★ 2026-09-21 实报(释放后播放失灵): 定时器只在 showEvent 重启, 已可见时点播放只置文案; 播放须确保定时器跑并复位 dt 基准。
            try:
                if not self._frame_timer.isActive():
                    self._frame_timer.start()
            except Exception:
                logger.debug("route_animator: start frame timer failed", exc_info=True)
            self._set_status("播放中")
        else:
            # 暂停=冻结: 遮罩保持最后一帧(与画布高亮一致); 复位/切页/关闭才清除
            self._set_status("已暂停")
        # 调度台：播放/暂停是离散状态变化点 → 立即上报
        self.report_to_bus(force=True)

    def _on_reset(self) -> None:
        self._playing = False
        self._play_btn.setChecked(False)
        self._play_btn.setText("播放")
        self._reset_trains()
        self._tick_impl(0.0)
        self._clear_route_mask()
        self._set_status("已复位")
        # 调度台：复位清空覆盖 → 立即上报（清掉旧格）
        self.report_to_bus(force=True)

    def _on_throttle(self, tid: str, v: int) -> None:
        self._pending_throttle[tid] = int(v)
        lbl = self._throttle_labels.get(tid)
        if lbl is not None:
            lbl.setText(f"{v}%")
        spin = self._throttle_spins.get(tid)
        if spin is not None and spin.value() != int(v):
            spin.blockSignals(True)
            spin.setValue(int(v))
            spin.blockSignals(False)

    def _set_train_field(self, tid: str, field: str, v) -> None:
        tr = self._engine._trains.get(tid)
        if tr is None:
            return
        if field == "car_count":
            tr.car_count = max(1, int(v))
            self._selected_chains.pop(tid, None)
        elif field == "chain_length":
            selected = self._selected_chains.get(tid)
            if selected:
                for idx in sorted(selected):
                    self._engine.set_chain_length(tid, idx, float(v))
            else:
                self._engine.set_chain_length_all(tid, float(v))
            self._materialize_train(tr)
            self._reattach_train(tid)
            sync_chain_edit(self, tid, field, v)     # R10：锁链也随链式编辑同步
            return
        else:
            setattr(tr, field, float(v))
        self._materialize_train(tr)
        self._reattach_train(tid)
        # R10 链式编辑锁：同步全部选中卡（未开锁/无选中 → 0，零行为变化）
        sync_chain_edit(self, tid, field, v)

    def _on_train_mode(self, tid: str, mode: str) -> None:
        self._engine.set_train_mode(tid, mode)
        name = self._train_names.get(tid, "火车")
        self._set_status(f"{name} 运行模式: {_TRAIN_MODE_LABELS.get(mode, mode)}(实时生效)")

    def _on_train_skip(self, tid: str, on: bool) -> None:
        self._engine.set_train_skip_gaps(tid, on)
        self._set_status("已开启跳过连接处(激活不中断)" if on else "已关闭跳过连接处")

    def _on_train_track_changed(self, tid: str, track_id: str) -> None:
        tr = self._engine._trains.get(tid)
        if tr is None:
            return
        if not track_id or track_id not in self._tracks:
            # 选回“未分配”: 如果原本有轨道则忽略, 保持原轨道; 新火车保持未分配
            combo = self._track_combos.get(tid)
            if combo is not None and tr._home_edge_id and tr._home_edge_id in self._tracks:
                combo.blockSignals(True)
                combo.setCurrentIndex(combo.findData(tr._home_edge_id))
                combo.blockSignals(False)
            return
        self._attach_train(tid, track_id, 0.0)
        self._tracks[track_id]["owner_train_id"] = tid
        self._refresh_canvas_scene()
        self._preview_train_visuals()
        name = self._train_names.get(tid, "火车")
        self._set_status(f"{name} 已移动到 {self._track_label(track_id)}")

    def _on_train_enabled(self, tid: str, enabled: bool) -> None:
        tr = self._engine._trains.get(tid)
        if tr is None:
            return
        tr.ended = not enabled
        if enabled:
            tr.ended = False
        name = self._train_names.get(tid, "火车")
        self._set_status(f"{name} 已启用" if enabled else f"{name} 已停用")

    def _apply_chain_selection(self, tid: str, count: int, interval: int, start: int) -> None:
        """按 选X个/间隔Y个/从第N个锁链开始选 生成锁链选择集合。

        count=选几根, interval=每选一根后跳过几根, start=从第几根开始(1-based)。
        """
        tr = self._engine._trains.get(tid)
        if tr is None:
            return
        total = max(0, tr.car_count - 1)
        if total <= 0:
            self._selected_chains[tid] = set()
            self._set_status("当前火车没有锁链(至少需要 2 节车厢)")
            return
        count = max(1, int(count))
        interval = max(0, int(interval))
        start = max(1, int(start))
        selected: set = set()
        idx = start - 1
        step = max(1, interval + 1)
        while len(selected) < count and idx < total:
            selected.add(idx)
            idx += step
        self._selected_chains[tid] = selected
        show = ", ".join(str(i + 1) for i in sorted(selected))
        self._set_status(f"已选中锁链: {show} (共 {len(selected)} 根)")

    def _clear_chain_selection(self, tid: str) -> None:
        self._selected_chains.pop(tid, None)
        name = self._train_names.get(tid, "火车")
        self._set_status(f"已清除 {name} 的锁链选择(锁链长度恢复统一调整)")

    # ── 摇杆绑定 ────────────────────────────────────────────────────────
    def _on_bind_joystick(self, tid: str) -> None:
        name = self._train_names.get(tid, "火车")
        try:
            from ..joystick_control_binding import (
                AxisDetectPopup, get_joystick_bind_manager,
            )
        except Exception:
            self._set_status("摇杆模块不可用")
            return
        popup = AxisDetectPopup(name, self)
        popup.exec()
        axis = getattr(popup, "detected_axis", None)
        if not axis:
            self._set_status("未检测到摇杆轴")
            return
        mgr = get_joystick_bind_manager()
        mgr.add_effect_param_binding(
            channel=tid, param_key="throttle", param_label="油门",
            physical_axis=axis, range_lo=0, range_hi=100)
        if not self._jb_wired:
            mgr.effect_param_changed.connect(self._on_joystick_param)
            self._jb_wired = True
        self._set_status(f"{name} 油门已绑定 {axis.upper()} 轴")

    def _bind_param_to_joystick(self, tid: str, field: str, label: str) -> None:
        name = self._train_names.get(tid, "火车")
        try:
            from ..joystick_control_binding import (
                AxisDetectPopup, get_joystick_bind_manager,
            )
        except Exception:
            self._set_status("摇杆模块不可用")
            return
        popup = AxisDetectPopup(f"{name}·{label}", self)
        popup.exec()
        axis = getattr(popup, "detected_axis", None)
        if not axis:
            self._set_status("未检测到摇杆轴")
            return
        mgr = get_joystick_bind_manager()
        mgr.add_effect_param_binding(
            channel=tid, param_key=field, param_label=label,
            physical_axis=axis, range_lo=0, range_hi=1000)
        if not self._jb_wired:
            mgr.effect_param_changed.connect(self._on_joystick_param)
            self._jb_wired = True
        self._set_status(f"{name}·{label} 已绑定 {axis.upper()} 轴")

    def _on_joystick_param(self, channel: str, param_key: str, value: int) -> None:
        if channel not in self._train_names:
            return
        raw = int(value)
        if param_key == "throttle":
            # 油门绑定范围就是 0-100(与油门滑块一致), 不再除以 10
            pct = max(0, min(100, raw))
            self._pending_throttle[channel] = pct
            slider = self._throttle_sliders.get(channel)
            if slider is not None and slider.value() != pct:
                slider.blockSignals(True)
                slider.setValue(pct)
                slider.blockSignals(False)
            lbl = self._throttle_labels.get(channel)
            if lbl is not None:
                lbl.setText(f"{pct}%")
            spin = self._throttle_spins.get(channel)
            if spin is not None and spin.value() != pct:
                spin.blockSignals(True)
                spin.setValue(pct)
                spin.blockSignals(False)
            return

        v = max(0, min(1000, raw))
        spec = self._param_specs.get(channel, {}).get(param_key)
        if spec is None:
            return
        # 默认量程 hi(非 spin.maximum(), 数值框已放开无上限); 摇杆按页面默认量程映射。
        lo, default_hi, dec = spec
        upper = float(default_hi)
        val = float(lo) + (upper - float(lo)) * v / 1000.0
        self._set_train_field(channel, param_key, val)
        slider = self._param_sliders.get(channel, {}).get(param_key)
        if slider is not None and slider.value() != v:
            slider.blockSignals(True)
            slider.setValue(v)
            slider.blockSignals(False)
        lbl = self._param_value_labels.get(channel, {}).get(param_key)
        if lbl is not None:
            lbl.setText(f"{val:.{dec}f}" if dec > 0 else str(int(round(val))))

    def _flush_throttle(self) -> None:
        if not self._pending_throttle:
            return
        pending = self._pending_throttle
        self._pending_throttle = {}
        from .runner_fleet_motion import set_throttle_with_panel_motion
        for tid, v in pending.items():
            set_throttle_with_panel_motion(self._engine, self, tid, v / 100.0)

    # ── 帧循环 / 契约发布 ───────────────────────────────────────────────
    def _on_frame(self) -> None:
        self._flush_throttle()
        if not self._playing:
            return
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        self._tick_impl(dt)

    def _tick_impl(self, dt: float) -> None:
        t0 = time.perf_counter()
        # R7：音频调制先写 target（未启用零开销），显式包络(R3)随后覆盖
        advance_audio_modulation(self, dt)
        advance_envelopes(self, dt)   # R3：包络先写油门，当帧即参与积分
        frame = self._engine.tick(dt)
        self._sync_train_cards(frame)
        self._publish_state(frame)
        self._register_route_mask(frame)
        # 调度台上报（节流在 report_to_bus 内部；失败不影响出光）
        self.report_to_bus()
        self._frame_ms_ema = (0.9 * self._frame_ms_ema
                              + 0.1 * ((time.perf_counter() - t0) * 1000.0))
        refresh_frame_ui(self, frame)

    def _sync_train_cards(self, frame: FrameResult) -> None:
        """引擎自动分裂/合并的车队 → 自动增删 HUD 卡片(用户拍板: 独立车队·可控)。"""
        live = list(frame.train_ids)
        for tid in live:
            if tid in self._train_names:
                continue
            base = tid.split("#", 1)[0]
            n = len([k for k in self._train_names if k.split("#", 1)[0] == base]) + 1
            self._train_names[tid] = f"{self._train_names.get(base, '火车')}·分队{n}"
            self._build_train_card(tid, derived=True)
        for tid in [k for k in self._train_names if k not in live]:
            if "#" not in tid:
                continue          # 用户手建的车队不因引擎合并而消失
            self._remove_card_widgets(tid)
            self._train_names.pop(tid, None)

    def _remove_card_widgets(self, tid: str) -> None:
        """卸载某车队的 HUD 卡片与滑块(引擎侧车列由引擎生命周期管理)。"""
        card = self._cards.pop(tid, None)
        if card is not None:
            self._deck_lay.removeWidget(card)
            card.deleteLater()
        self._throttle_sliders.pop(tid, None)
        self._throttle_spins.pop(tid, None)
        self._throttle_labels.pop(tid, None)
        self._param_sliders.pop(tid, None)
        self._param_spins.pop(tid, None)
        self._param_specs.pop(tid, None)
        self._param_value_labels.pop(tid, None)
        self._pending_throttle.pop(tid, None)
        self._mode_combos.pop(tid, None)
        self._track_combos.pop(tid, None)
        self._enabled_checkboxes.pop(tid, None)
        self._selected_chains.pop(tid, None)
        if self._selected_train_id == tid:
            self._selected_train_id = next(reversed(self._train_names), None)

    def _frame_to_visuals(self, frame: FrameResult) -> List[dict]:
        visuals: List[dict] = []
        for i, tid in enumerate(frame.train_ids):
            tr = self._engine._trains.get(tid)
            if tr is None:
                continue
            cars = list(frame.car_positions[i]) if i < len(frame.car_positions) else []
            angles = list(frame.car_angles[i] if i < len(frame.car_angles) else [])
            radius_list = (frame.coverage_radius_list[i]
                           if i < len(frame.coverage_radius_list) else [])
            radius = float(radius_list[0]) if radius_list else 0.0
            color = tr.color
            if "#" in tid:
                color = QColor(color).lighter(120).name()  # 分队: 母色提亮一档
            visuals.append({
                "train_id": tid, "color": color, "car_positions": cars,
                "car_angles": angles, "radius": radius, "show_radius": self._show_radius,
                "car_length": float(tr.car_length),
            })
        return visuals

    def _preview_train_visuals(self) -> None:
        """轻量预览：只更新火车位置/角度，不计算覆盖/遮罩（播放前零投影开销）。"""
        visuals: List[dict] = []
        for tid, tr in self._engine._trains.items():
            if not tr.cars:
                continue
            pos, ang, rad = self._engine._car_outputs(tr)
            color = tr.color
            if "#" in tid:
                color = QColor(color).lighter(120).name()
            radius = float(rad[0]) if rad else 0.0
            visuals.append({
                "train_id": tid, "color": color,
                "car_positions": [tuple(c) for c in pos],
                "car_angles": list(ang),
                "radius": radius,
                "show_radius": self._show_radius,
                "car_length": float(tr.car_length),
            })
        self._canvas.set_train_visuals(visuals)
        self._canvas.set_coverage_ids([])
        self._refresh_fleet_tree()

    def _register_route_mask(self, frame: FrameResult) -> None:
        try:
            from ..effect_engine import set_route_mask
            if self._engine._trains:
                set_route_mask(self._engine.route_mask_map())
            else:
                set_route_mask(None)
        except Exception:
            logger.debug("route mask registration failed", exc_info=True)

    def _clear_route_mask(self) -> None:
        try:
            from ..effect_engine import set_route_mask
            set_route_mask(None)
        except Exception:
            logger.debug("route mask clear failed", exc_info=True)

    def _publish_state(self, frame: FrameResult) -> None:
        proj = self._project()
        active_ids = list(frame.active_ordered_ids)
        intensity = dict(frame.coverage_strength)
        if active_ids == self._last_active_ids and intensity == self._last_intensity:
            return
        self._last_active_ids = active_ids
        self._last_intensity = intensity
        if proj is None:
            return
        st = getattr(proj, "route_animator_state", None)
        if not isinstance(st, dict):
            st = {}
            try:
                proj.route_animator_state = st
            except Exception:
                pass
        st["active_ordered_ids"] = list(frame.active_ordered_ids)
        st["active_intensity"] = dict(frame.coverage_strength)

    def _on_select_train(self, tid: str) -> None:
        if tid not in self._train_names:
            return
        self._selected_train_id = tid
        self._refresh_card_selection()
        name = self._train_names[tid]
        self._set_status(f"当前选中 {name}（画轨道将绘制其轨道）")

    def _refresh_card_selection(self) -> None:
        for tid, card in self._cards.items():
            tr = self._engine._trains.get(tid)
            accent = tr.color if tr is not None else TEXT_SECONDARY
            border = SELECT_BLUE if tid == self._selected_train_id else "rgba(255,255,255,20)"
            width = 2 if tid == self._selected_train_id else 1
            card.setStyleSheet(
                f"QFrame{{background:{_ELEV};border:{width}px solid {border};"
                f"border-left:3px solid {accent};border-radius:4px;}}"
                f"QLabel{{color:{_FG};font-size:11px;background:transparent;}}")

    def _set_status(self, text: str) -> None:
        left = getattr(self, "_status_left", None)
        if left is not None:
            left.setText(text)

    def hideEvent(self, event) -> None:
        """隐藏只停止 UI 轮询；播放中的原计时器继续计算和发布遮罩。"""
        super().hideEvent(event)
        hide_view(self)

    def showEvent(self, event) -> None:
        """恢复原计时器与 UI 轮询；不改变 playing，不复活已停止输出。"""
        try:
            super().showEvent(event)
        except Exception:
            logger.debug("route_animator: showEvent super failed", exc_info=True)
        try:
            if not self._frame_timer.isActive():
                self._frame_timer.start()
            if not self._page_poll_timer.isActive():
                self._page_poll_timer.start()
        except Exception:
            logger.debug("route_animator: showEvent restart timers failed", exc_info=True)

    def closeEvent(self, event) -> None:
        """关闭视图保存原场景，运行中的输出继续；显式停止由释放入口负责。

        容错序列搬到 `runner_scene_store.close_panel`（本函数受门禁 CC≤8 约束）。
        """
        close_panel(self)
        try:
            super().closeEvent(event)
        except Exception:
            logger.debug("route_animator: closeEvent super failed", exc_info=True)


# 卡片/火车管理子页面已搬到 runner_cards（W2 腾行数）；本行保持旧导入面可用。
from .runner_cards import TrainManagerDialog, _TrainCard  # noqa: E402,F401
