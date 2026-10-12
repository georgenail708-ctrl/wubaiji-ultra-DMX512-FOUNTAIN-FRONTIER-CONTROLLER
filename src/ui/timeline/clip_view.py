"""
Qascade V1.0 — Clip View  [FIXED v8]

修复跨轨拖动（调试版）：
  1. ClipItem.mouseReleaseEvent：把 releaseMouse() 移到 emit 信号之后
  2. ClipItem.mouseMoveEvent：乐观本地更新（只动 geometry，不改 model）
  3. ClipView：cross_track_drag_end 改为通过中转槽 _on_item_cross_track_drag_end
     传递，方便调试确认信号链路是否断开
  4. DEBUG = False 时在控制台打印完整的信号链路日志
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Optional

import numpy as np

import time
from PyQt6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QSize, QEvent, QObject, pyqtSignal
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont,
    QLinearGradient, QPainterPath, QPaintEvent,
    QMouseEvent, QContextMenuEvent, QCursor, QPolygonF,
)
from PyQt6.QtWidgets import (
    QWidget, QMenu, QSizePolicy, QMessageBox, QToolTip,
)
try:
    from ..main_icon_library import make_main_icon as _make_icon
except Exception:
    _make_icon = None
try:
    from ...engine.output_smoother import BUILTIN_SMOOTH_PRESETS
except Exception:  # pragma: no cover - UI fallback only
    BUILTIN_SMOOTH_PRESETS = {}

from ...core.models import (
    Clip, Track,
    normalize_group_pauses,
)
from .. import qascade_theme as _th
from .clip_context_menu import build_clip_context_menu
from .timeline_grid import second_grid_positions
from . import timeline_group_gesture as _group_edit
from . import timeline_drag_tip as _drag_tip
from .timeline_snap import (snap_guide_time, snap_move_start,  # T1 网格吸附
                            snap_resize_end)


# ── 调试开关 ──────────────────────────────────────────────────────────────────
DEBUG = False


def _alt_down(mods) -> bool:
    """Alt 是否按住（吸附临时旁路手势；修饰键对象缺失时视为未按）。"""
    try:
        return bool(mods is not None and (mods & Qt.KeyboardModifier.AltModifier))
    except Exception:
        return False


def _show_snap_guide(item, raw, snapped) -> None:
    """把吸附参考线位置（秒；`raw/snapped` 任一为 None = 收线）交给覆盖层。

    沿父链找**最先**实现 `set_snap_guide` 的控件（ClipItem → ClipView →
    _TrackContainer，参考线由后者转发给播放头覆盖层跨轨绘制）。取不到时静默：
    参考线是可视辅助，任何缺失都不该影响拖动本身。
    """
    t = None
    if raw is not None and snapped is not None:
        t = snap_guide_time(item, raw, snapped)
    p = item.parent()
    for _ in range(4):
        if p is None:
            return
        fn = getattr(p, "set_snap_guide", None)
        if callable(fn):
            try:
                fn(t)
            except Exception:
                pass
            return
        p = p.parent()


# ── 字体助手（D5 解剖学：正文 11px / 数值与时间码 Consolas）──────────────────
# R183 横向性能批：paint 路径逐帧 QFont() 构造 → _th.cached_font 模块级缓存
# （共享只读实例，调用方只 setFont，不得 set* 变异）。
def _font_ui(px: int, bold: bool = False) -> QFont:
    return _th.cached_font(px, bold)


def _font_mono(px: int) -> QFont:
    return _th.cached_font(px, families=_th.MONO_FONT_FAMILIES)


# ── FX 徽标色（§4.C 十一色编码：hue 全部保留，明度对齐 bright 族可读档；
#     对应关系见类内 _draw_fx_badges 注释。TRACK_COLORS 等数据色不在此列。）──
def _fx_badge_color(kind: str) -> QColor:
    hues = {
        "fade":   "#6FA8DC",   # 信息蓝族（原 #4aa3ff，hue≈211°）
        "dir":    "#BE9BEA",   # 紫（原 #b16cff，同 hue 提亮）
        "smooth": "#5BCD9E",   # 青绿（原 #36c98e，同 hue 提亮）
        "track":  "#E6B455",   # bright_warn
        "ph":     "#E6B455",   # bright_warn（原金，前波已定档）
        "rec":    "#E36855",   # bright_error（§2 红→bright_error）
        "phase":  "#F58FB4",   # 玫红（原 #ff6b9d，同 hue 提亮）
        "tr":     "#63ABF2",   # 蓝（原 #30a0f0，同 hue 提亮）
        "mf":     "#DD8080",   # 玫瑰红（原 #c46b6b，同 hue 提亮）
        "speed":  "#E8C565",   # 黄（原 #f4d35e，同 hue 提亮对齐 warn 档）
    }
    return QColor(hues.get(kind, _th.TEXT_SECONDARY))


# ── 工具模式 ──────────────────────────────────────────────────────────────────
class ToolMode(Enum):
    SELECT = auto()
    RAZOR  = auto()
    PEN    = auto()   # R179 画笔：在音频调制轨上拖动绘制调制 clip（用户规格 ②）


_current_tool: ToolMode = ToolMode.SELECT


def set_tool_mode(mode: ToolMode) -> None:
    global _current_tool
    _current_tool = mode


def get_tool_mode() -> ToolMode:
    return _current_tool


# ── R179 画笔工具门（用户规格 ②：PEN 在普通轨=无效+光标禁用态）──────────────
# 只拦事件不改 ClipItem（其 mouse*Event 行数基线冻结）：工具切换时对全部
# ClipItem 装/拆事件过滤器。调制轨行画布（AudioModLaneView._items={}）天然
# 不受影响——它自己按 get_tool_mode() 决定画笔手势。
class _PenToolGateFilter(QObject):
    """PEN 激活期拦截 ClipItem 的鼠标事件（按下/移动/释放/双击/右键菜单全无效）。

    ★ R182 四轮审计修：补 ContextMenu——修前画笔模式下普通轨 clip 右键菜单
      仍可用（剪切/删除等编辑动作未被禁用）。
    """

    _BLOCKED = frozenset((
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseMove,
        QEvent.Type.ContextMenu,
    ))

    def eventFilter(self, obj, ev):
        if ev.type() in self._BLOCKED:
            return True   # PEN 在普通轨 clip 上无效（禁用态）
        return super().eventFilter(obj, ev)


_pen_gate_filter: Optional[_PenToolGateFilter] = None   # QObject 须常驻防 GC


def apply_pen_tool_gate(views, enabled: bool) -> None:
    """把 PEN 禁用态装/拆到全部普通轨 ClipItem 上（工具切换与轨道重建共用）。

    `views` = _TrackContainer._views（鸭子类型：只读 `_items`，调制行画布为
    空字典自然跳过）。enabled=True 时同时设 ForbiddenCursor（光标禁用态）。
    """
    global _pen_gate_filter
    if _pen_gate_filter is None:
        _pen_gate_filter = _PenToolGateFilter()
    for view in views or ():
        for item in (getattr(view, "_items", None) or {}).values():
            _apply_pen_gate_to_item(item, enabled)


def _apply_pen_gate_to_item(item, enabled: bool) -> None:
    """单 ClipItem 的门装/拆：PEN 拦事件+禁用光标；退出恢复自由交互。"""
    if enabled:
        item.installEventFilter(_pen_gate_filter)
        item.setCursor(Qt.CursorShape.ForbiddenCursor)
    else:
        item.removeEventFilter(_pen_gate_filter)
        item.unsetCursor()


def lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    return QColor(
        int(c1.red()   + (c2.red()   - c1.red())   * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue()  + (c2.blue()  - c1.blue())  * t),
    )


def motor_force_badge_label(preset: str) -> str:
    """Return the compact timeline badge for a motor-force preset."""
    normalized = str(preset or "").strip().lower()
    return {
        "soft": "MF-S",
        "natural": "MF-N",
        "strong": "MF-H",
        "blast": "MF-X",
    }.get(normalized, "")


# ══════════════════════════════════════════════════════════════════════════════
#  ClipItem
# ══════════════════════════════════════════════════════════════════════════════
#: 「本次不改这一侧」哨兵（NaN 不可能来自用户输入；见 `TimelineWidget._write_edges`）
_NAN = float("nan")


def _connect_if_hasattr(src, attr: str, dst_sig) -> None:
    """src 上存在信号 attr 才连接到 dst_sig（老 item 缺该信号时安全跳过）。"""
    if hasattr(src, attr):
        getattr(src, attr).connect(dst_sig)


def _dragged_edge_pair(clip, prefix: str, drag_mode: str) -> tuple:
    """拖拽手柄提交值：返回 `(in 值, out 值)`，**只填被拖的那一侧**，另一侧 NaN。

    ★★ R135 修复（多选时"拖 c1 的淡入，c2/c3 的淡出被害"）：原先两侧都提交，而槽端
      对选中集里**每个** clip 无差别写两个值 → 拖 c1 淡入会把 c1 的 fade_out 抄给
      所有选中 clip（实测 c2 的 0.25 → 1.5、c3 的 0.0 → 1.5）。自定义淡入/淡出同理。
      哨兵语义：NaN = "本次不改这一侧"（NaN 不可能来自用户输入；见
      `TimelineWidget._on_clip_fade_commit` / `_on_clip_seq_delay_commit`）。
    信号签名不变 → 既有连接与测试零改动。
    """
    nan = float("nan")
    side = "in" if drag_mode.endswith("_in") else "out"
    val = max(0.0, float(getattr(clip, f"{prefix}_{side}", 0.0) or 0.0))
    return (val, nan) if side == "in" else (nan, val)


class ClipItem(QWidget):
    clip_selected      = pyqtSignal(str, bool, bool)
    clip_delete_req    = pyqtSignal(str)
    clip_to_processor  = pyqtSignal(object)
    clip_load_fixture_group = pyqtSignal(object)
    clip_duplicate     = pyqtSignal(str)
    clip_cut           = pyqtSignal(str)
    clip_copy          = pyqtSignal(str)
    clip_move_commit   = pyqtSignal(str, float)
    clip_resize_commit = pyqtSignal(str, float)
    clip_fade_commit   = pyqtSignal(str, float, float)
    clip_seq_delay_commit = pyqtSignal(str, float, float)   # clip_id, seq_delay_in, seq_delay_out
    cross_track_drag     = pyqtSignal(str, int, float)
    cross_track_drag_end = pyqtSignal(str, int, float)
    clip_razor_cut       = pyqtSignal(str, float)
    clip_dir_fade_set    = pyqtSignal(str, str, str, int, str, float, int, list)   # clip_id, in_direction, out_direction, groups, mode, pause, batches, pauses
    clip_spatial_scope_set = pyqtSignal(str, str)  # clip_id, per_shape/global
    clip_skip_base_order_set = pyqtSignal(str, bool)  # clip_id, skip_base_order（#18 跳过编号排序）
    clip_transition_strategy_set = pyqtSignal(str, str, str)  # clip_id, edge(in/out), strategy_id（淡入/淡出分开设置）
    clip_dir_edge_set = pyqtSignal(str, str)  # clip_id, edge(both/in/out)（分组方向过渡应用边 2026-08-12）
    clip_auto_smooth     = pyqtSignal(str, str)              # clip_id, preset_id
    clip_motor_force     = pyqtSignal(str, str)              # clip_id, force_preset (bypass/soft/natural/strong/blast)
    clip_light_tracking  = pyqtSignal(str, bool, int, float, float, str, str, int)  # clip_id, enabled, threshold, attack, release, color, source, max_brightness
    clip_hold_toggled    = pyqtSignal(str, bool)              # clip_id, enabled
    clip_hold_mode_changed = pyqtSignal(str, str)             # clip_id, none/last_frame/last_cycle
    clip_speed_changed   = pyqtSignal(str, float)             # clip_id, playback_rate
    clip_record_output_source_changed = pyqtSignal(str, bool) # clip_id, enabled
    clip_motor_transition_phase_changed = pyqtSignal(str, bool, float) # clip_id, enabled, amount
    clip_precharge_changed = pyqtSignal(str, bool, float, int)      # clip_id, enabled, lead_ms, level
    clip_optimize_transitions = pyqtSignal(list)                      # [clip_id, ...] bulk
    clip_open_processing_studio = pyqtSignal(str)                     # clip_id（R172：打开 Clip 附加处理 Studio）
    RESIZE_ZONE = 12
    FADE_HANDLE_W = 8

    def __init__(self, clip: Clip, track: Track, px_per_sec: float, parent=None):
        super().__init__(parent)
        self._clip       = clip
        self._track      = track
        self._px_per_sec = px_per_sec
        self._selected   = False
        self._hover      = False

        self._drag_mode      = None
        self._drag_origin_gx = 0.0
        self._drag_start_val = 0.0
        self._drag_moved     = False
        self._is_dragging    = False

        self._drag_local_x: Optional[int] = None
        self._razor_x: Optional[int] = None
        self._hover_handle: Optional[str] = None   # D5: 手柄 hover 外圈 focus_blue
        # 波形条缓存：clip 帧静止时，paintEvent 每次重算 n_bars 个采样高度是浪费。
        # 按 (n_bars, len(frames)) 缓存计算好的高度列表；宽度/帧数变化时自动失效。
        self._waveform_cache_key: Optional[tuple] = None
        self._waveform_cache_bars: Optional[list] = None
        # 卡体路径缓存：键 (w, h, is_hold)，尺寸/模式不变时复用 QPainterPath。
        self._path_cache_key: Optional[tuple] = None
        self._path_cache: Optional[QPainterPath] = None
        # 省略号标题缓存：键 (label, 可用宽度)，避免每帧重算字体度量。
        self._label_cache_key: Optional[tuple] = None
        self._label_cache: Optional[str] = None
        # FX 徽标缓存：键为影响徽标的全部 clip 字段快照，字段不变时复用列表。
        self._badges_cache_key: Optional[tuple] = None
        self._badges_cache: Optional[list] = None
        if not hasattr(self._clip, "fade_in"):
            self._clip.fade_in = 0.0
        if not hasattr(self._clip, "fade_out"):
            self._clip.fade_out = 0.0

        self.setMouseTracking(True)
        self._update_geometry()
        # ★ R182 四轮审计修（PEN 门禁重建缺口）：轨道/clip 重建路径
        #   （clip_area._make_view_for_track/_refresh_clips）不重装门过滤器
        #   ——PEN 激活期间新建的普通轨 clip 无门可拖动。构造时自查工具态。
        if get_tool_mode() == ToolMode.PEN:
            _apply_pen_gate_to_item(self, True)

    # ── 几何 ──────────────────────────────────
    def _update_geometry(self):
        x = int(self._clip.start_time * self._px_per_sec)
        w = max(16, int(self._clip.duration * self._px_per_sec))
        h = self.parent().height() if self.parent() else 64
        self.setGeometry(x, 0, w, h)

    def _update_geometry_drag(self, local_x: int):
        """拖动中只改 x 坐标，不改 model"""
        w = max(16, int(self._clip.duration * self._px_per_sec))
        h = self.parent().height() if self.parent() else 64
        self.setGeometry(local_x, 0, w, h)

    def set_px_per_sec(self, val: float):
        self._px_per_sec = val
        self._update_geometry()

    def set_selected(self, val: bool):
        # R57 框选性能：状态无变化不重绘——框选拖动中每次 mousemove 会对
        # 全部 item 重设选择态，无门控时整个时间线每帧全量重绘。
        if val == self._selected:
            return
        self._selected = val
        self.update()

    def _hold_mode(self) -> str:
        mode = str(getattr(self._clip, "hold_mode", "") or "").strip().lower()
        if mode not in ("none", "last_frame", "last_cycle"):
            mode = "last_cycle" if bool(getattr(self._clip, "hold_last_frame", False)) else "none"
        return mode

    def _is_hold(self) -> bool:
        return self._hold_mode() != "none"

    def _speed_rate(self) -> float:
        try:
            return max(0.001, float(getattr(self._clip, "playback_rate", 1.0) or 1.0))
        except Exception:
            return 1.0

    # ── 绘制 ─────────────────────────────────
    # D5 卡片解剖参数：
    #   圆角 4 / 边 1px（选中 select_blue · hover 白40% · 常态 rgba(255,255,255,26)）
    #   选中 = select_blue 12% 底 + 外圈 focus_blue 1px 半透
    #   轨道色（TRACK_COLORS，§4.B 保护）→ 顶部 3px 色带；正文不再整块染色
    SELECT_RING_ALPHA = 160        # focus_blue 外圈半透
    SEL_FILL_ALPHA    = 31         # select_blue ≈12% 底

    def _resolve_card_path(self, w: int, h: int, is_hold: bool) -> QPainterPath:
        """Hold 模式：右端为箭头形状（无圆角右端）；普通模式：4px 圆角矩形。
        卡体路径只依赖 (w, h, is_hold)，尺寸/模式不变时复用缓存的 QPainterPath。"""
        path_key = (w, h, is_hold)
        if self._path_cache_key == path_key:
            return self._path_cache
        if is_hold:
            arrow_w = min(max(8, h // 2), 20)  # 箭头宽度（三角形水平长度）
            path = QPainterPath()
            # 左端圆角矩形主体（不含右端）
            path.moveTo(4, 1)
            path.arcTo(1, 1, 6, 6, 180, -90)          # 左上圆角
            path.lineTo(w - arrow_w, 1)                 # 顶边
            path.lineTo(w - 1, h / 2)                  # 右端箭头尖
            path.lineTo(w - arrow_w, h - 1)             # 底边右端
            path.arcTo(1, h - 7, 6, 6, -90, -90)       # 左下圆角
            path.closeSubpath()
        else:
            path = QPainterPath()
            path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 4, 4)
        self._path_cache_key = path_key
        self._path_cache = path
        return path

    def _resolve_elided_label(self, p: QPainter, label: str, elide_w: int) -> str:
        """省略号标题只依赖 (label, 可用宽度)；不变时复用，避免每帧重算字体度量。"""
        label_key = (label, elide_w)
        if self._label_cache_key != label_key:
            fm = p.fontMetrics()
            self._label_cache = fm.elidedText(label, Qt.TextElideMode.ElideRight, elide_w)
            self._label_cache_key = label_key
        return self._label_cache

    def paintEvent(self, event: QPaintEvent):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track_color = QColor(self._track.color)
        hold_mode = self._hold_mode()
        is_hold = hold_mode != "none"
        path = self._resolve_card_path(w, h, is_hold)

        # ── 扁平卡体：抬升面板底（bg_panel），不再用轨道色整卡渐变 ──
        p.fillPath(path, QBrush(QColor(_th.BG_PANEL)))
        if self._selected:
            sel_fill = QColor(_th.SELECT_BLUE)
            sel_fill.setAlpha(self.SEL_FILL_ALPHA)                    # select_blue ≈12%
            p.fillPath(path, QBrush(sel_fill))
        elif self._hover and get_tool_mode() == ToolMode.SELECT:
            p.fillPath(path, QBrush(QColor(255, 255, 255, 14)))

        # ── 用户轨道色带：顶部 3px（clip 到路径内保圆角）──
        p.save()
        p.setClipPath(path)
        p.fillRect(QRect(0, 0, w, 3), track_color)
        if self._is_dragging:
            # 拖拽幽灵提亮：active_orange 半透覆层（语义=拖动中，同旧琥珀提示）
            p.fillRect(self.rect(), QColor(255, 160, 40, 60))
        p.restore()

        # ── 1px 描边：状态三态 ──
        if self._selected:
            border_pen = QPen(QColor(_th.SELECT_BLUE), 1)
        elif self._hover and get_tool_mode() == ToolMode.SELECT:
            border_pen = QPen(QColor(_th.BRAND_ORANGE_INK), 1)          # 白 ~40%
        else:
            border_pen = QPen(QColor(_th.BORDER_NORMAL), 1)
        p.setPen(border_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # ── 选中外圈：focus_blue 1px 半透（外扩 1px）──
        if self._selected:
            ring_pen = QColor(_th.FOCUS_BLUE)
            ring_pen.setAlpha(self.SELECT_RING_ALPHA)
            p.setPen(QPen(ring_pen, 1))
            p.drawRoundedRect(QRectF(-0.5, -0.5, w + 0.5, h + 0.5), 5, 5)

        if self._clip.frames and w > 20:
            self._draw_waveform(p, w, h, track_color)

        label = self._clip.label or f"Clip ({self._clip.duration:.1f}s)"
        elide_w = max(10, w - 46)
        elided_label = self._resolve_elided_label(p, label, elide_w)
        p.setFont(_font_ui(11))
        p.setPen(QColor(_th.TEXT_PRIMARY))
        p.drawText(QRectF(5, 4, w - 10, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   elided_label)
        # 时长：右上，等宽字 text_secondary
        p.setFont(_font_mono(10))
        p.setPen(QColor(_th.TEXT_SECONDARY))
        p.drawText(QRectF(4, 3, w - 8, 15),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                   f"{self._clip.duration:.2f}s")
        self._draw_fx_badges(p, w, h)

        self._draw_fade_handles(p, w, h)

        # Hold 模式：不绘制右端 resize 竖线（箭头已代替）；普通模式正常绘制
        if get_tool_mode() == ToolMode.SELECT and not is_hold:
            p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))  # 半透明白 resize 线（原 #ffffff80）
            p.drawLine(w - 5, 4, w - 5, h - 4)
            p.drawLine(w - 8, 4, w - 8, h - 4)

        if get_tool_mode() == ToolMode.RAZOR and self._razor_x is not None:
            p.setPen(QPen(QColor(_th.BRIGHT_ERROR), 2))  # bright_error 剥刀预览线
            p.drawLine(self._razor_x, 0, self._razor_x, h)
            # 与点击位置计算保持一致：用真实像素 x 计算时间
            cut_time = (self.x() + self._razor_x) / max(1.0, self._px_per_sec)
            p.setFont(_font_mono(10))
            p.setPen(QColor(_th.BRIGHT_ERROR))  # bright_error 剥刀时间文字
            p.drawText(self._razor_x + 3, 12, f"{cut_time:.2f}s")

        # Hold 模式：在箭头区域绘制保持标签（bright_warn 金）
        if is_hold:
            p.setPen(QPen(QColor(_th.BRIGHT_WARN), 1))  # bright_warn
            p.setFont(_font_ui(10))
            hold_label = "HOLD F" if hold_mode == "last_frame" else "HOLD C"
            p.drawText(self.rect().adjusted(4, 2, -4, -2),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, hold_label)
        p.end()

    def _collect_fx_badges(self) -> list[tuple[str, str]]:
        """收集 Clip 附加处理徽标 [(文字, 色键)]。hue 编码见 _fx_badge_color。"""
        badges: list[tuple[str, str]] = []
        if max(0.0, float(getattr(self._clip, "fade_in", 0.0) or 0.0)) > 0.01 or \
           max(0.0, float(getattr(self._clip, "fade_out", 0.0) or 0.0)) > 0.01:
            badges.append(("FADE", "fade"))
        in_dir = getattr(self._clip, "fade_in_direction", "none") or "none"
        out_dir = getattr(self._clip, "fade_out_direction", "none") or "none"
        groups = max(1, int(getattr(self._clip, "fade_groups", 1) or 1))
        if in_dir != "none" or out_dir != "none" or groups > 1:
            mode = str(getattr(self._clip, "fade_group_sequence_mode", "parallel") or "parallel").strip().lower()
            if mode == "sequential":
                pause = max(0.0, float(getattr(self._clip, "fade_group_pause", 0.0) or 0.0))
                batches = max(0, int(getattr(self._clip, "fade_group_sequence_batches", 0) or 0))
                batch_label = "" if batches <= 0 or batches == groups else f" {batches}批"
                batch_count = groups if batches <= 0 else max(1, min(groups, batches))
                pauses = normalize_group_pauses(
                    getattr(self._clip, "fade_group_pauses", []),
                    max(0, batch_count - 1),
                    pause,
                )
                pause_label = (
                    f"{pauses[0]:.1f}s"
                    if pauses and all(abs(p - pauses[0]) <= 1e-6 for p in pauses)
                    else f"{sum(pauses):.1f}s/{len(pauses)}"
                ) if pauses else "0.0s"
                badges.append((f"DIR×{groups} SEQ{batch_label} {pause_label}", "dir"))
            else:
                badges.append((f"DIR×{groups}", "dir"))
        smooth_name = getattr(self._clip, "smooth_preset_name", "") or getattr(self._clip, "smooth_preset", "") or ""
        if smooth_name:
            badges.append(("SMOOTH", "smooth"))
        if bool(getattr(self._clip, "light_tracking_enabled", False)):
            th = max(0, min(255, int(getattr(self._clip, "light_tracking_threshold", 128) or 128)))
            badges.append((f"TRACK {th}", "track"))
        # PH — 泵预充
        if bool(getattr(self._clip, "precharge_enabled", False)):
            badges.append(("PH", "ph"))
        # REC — 录制输出源
        if bool(getattr(self._clip, "record_output_source_enabled", False)):
            badges.append(("REC", "rec"))
        # PHASE — 电机参与过渡相位差
        if bool(getattr(self._clip, "motor_transition_phase_enabled", False)):
            amount = max(0.0, float(getattr(self._clip, "motor_transition_phase_amount", 1.0) or 1.0))
            badges.append((f"PHASE {amount:.1f}x", "phase"))
        # TR — 优化过渡方式（梯形速度曲线）
        if getattr(self._clip, "transitions_optimized", ""):
            badges.append(("TR", "tr"))
        # MF — 优化电机力度（电机力度优化）
        force_label = motor_force_badge_label(getattr(self._clip, "motor_force_preset", ""))
        if force_label:
            badges.append((force_label, "mf"))
        speed = self._speed_rate()
        if abs(speed - 1.0) > 0.001:
            badges.append((f"{speed * 100:.0f}%", "speed"))
        return badges

    def _fx_badge_key_fade(self) -> tuple:
        """缓存 key 分片 1/4：淡入淡出时长 + 方向。"""
        c = self._clip
        return (
            round(max(0.0, float(getattr(c, "fade_in", 0.0) or 0.0)), 4),
            round(max(0.0, float(getattr(c, "fade_out", 0.0) or 0.0)), 4),
            getattr(c, "fade_in_direction", "none") or "none",
            getattr(c, "fade_out_direction", "none") or "none",
        )

    def _fx_badge_key_groups(self) -> tuple:
        """缓存 key 分片 2/4：方向分组数 + 顺序播放批次设置。"""
        c = self._clip
        return (
            int(getattr(c, "fade_groups", 1) or 1),
            str(getattr(c, "fade_group_sequence_mode", "parallel") or "parallel").strip().lower(),
            round(max(0.0, float(getattr(c, "fade_group_pause", 0.0) or 0.0)), 4),
            int(getattr(c, "fade_group_sequence_batches", 0) or 0),
        )

    def _fx_badge_key_misc1(self) -> tuple:
        """缓存 key 分片 3/4：分组暂停表 + 平滑预设 + 灯跟踪。"""
        c = self._clip
        return (
            tuple(getattr(c, "fade_group_pauses", []) or []),
            getattr(c, "smooth_preset_name", "") or getattr(c, "smooth_preset", "") or "",
            bool(getattr(c, "light_tracking_enabled", False)),
            int(getattr(c, "light_tracking_threshold", 128) or 128),
        )

    def _fx_badge_key_misc2(self) -> tuple:
        """缓存 key 分片 4/4：泵预充 / 录制源 / 电机相位差 / 优化过渡 / 力度 / 速率。"""
        c = self._clip
        return (
            bool(getattr(c, "precharge_enabled", False)),
            bool(getattr(c, "record_output_source_enabled", False)),
            bool(getattr(c, "motor_transition_phase_enabled", False)),
            round(max(0.0, float(getattr(c, "motor_transition_phase_amount", 1.0) or 1.0)), 4),
            getattr(c, "transitions_optimized", "") or "",
            getattr(c, "motor_force_preset", "") or "",
            round(self._speed_rate(), 4),
        )

    def _fx_badges_cache_key(self) -> tuple:
        """收集影响 `_collect_fx_badges` 结果的全部 clip 字段快照，
        用作缓存失效判据——任一字段变化即视为不同 key，重建徽标列表。
        拆成 4 个分片方法只为压低单函数圈复杂度（各分片 CC 独立且低），
        拼接顺序与拆分前的单一 tuple 完全一致，纯移动不改值。"""
        return (
            self._fx_badge_key_fade()
            + self._fx_badge_key_groups()
            + self._fx_badge_key_misc1()
            + self._fx_badge_key_misc2()
        )

    def _draw_fx_badges(self, p: QPainter, w: int, h: int):
        """D5 徽标行解剖：9px 圆角色片（hue=特征编码，§4.C 保 hue 提明度）
        + 10px 同色小字；徽标间 2px；放不下以「…」收尾并把全列写进 tooltip。
        徽标列表按 `_fx_badges_cache_key` 缓存——clip 相关字段不变时复用，
        避免每次 paintEvent（含祖先控件曝光区重绘触发的级联重绘）都重算。"""
        badges_key = self._fx_badges_cache_key()
        if self._badges_cache_key != badges_key:
            self._badges_cache_key = badges_key
            self._badges_cache = self._collect_fx_badges()
        badges = self._badges_cache
        if not badges or w < 44 or h < 30:
            return

        p.save()
        p.setFont(_font_ui(10))
        fm = p.fontMetrics()
        x = 5
        y = 20                      # 名字行下缘起
        chip = 9                    # 圆角小片边长
        gap_chip_text = 3
        unit_gap = 2                # 徽标间距
        max_x = max(8, w - 6)
        ellipsis_drawn = False
        for text, kind in badges:
            text_w = fm.horizontalAdvance(text)
            unit_w = chip + gap_chip_text + text_w
            if x + unit_w > max_x:
                ellipsis_drawn = True
                break
            col = _fx_badge_color(kind)
            fill = QColor(col)
            fill.setAlpha(235)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fill))
            p.drawRoundedRect(QRect(x, y, chip, chip), 2, 2)
            p.setPen(QPen(col))
            p.drawText(QRect(x + chip + gap_chip_text, y - 3, text_w + 2, chip + 6),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            x += unit_w + unit_gap
        if ellipsis_drawn:
            p.setPen(QPen(QColor(_th.TEXT_SECONDARY)))
            p.drawText(QRect(x, y - 3, max_x - x, chip + 6),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "…")
        p.restore()

        # 全列 tooltip（悬停查看被省略的完整清单）
        tip_lines = [f"● {t}" for t, _k in badges]
        tip = "\n".join(tip_lines)
        if self.toolTip() != tip:
            self.setToolTip(tip)

    def _draw_waveform(self, p: QPainter, w: int, h: int, color: QColor):
        n_bars = min(w // 2, 60)
        if n_bars < 2:
            return
        frames = self._clip.frames
        bar_w  = max(1, w // n_bars)

        # 计算每根条的归一化幅度（0..1），按 (n_bars, 帧数) 缓存。clip 帧静止时
        # 每次 paintEvent 都重扫帧求 max 是浪费；缓存与高度 h 无关，故 h 变化也复用。
        cache_key = (n_bars, len(frames))
        amps = self._waveform_cache_bars
        if amps is None or self._waveform_cache_key != cache_key:
            amps = self._compute_waveform_amplitudes(frames, n_bars)
            self._waveform_cache_bars = amps
            self._waveform_cache_key = cache_key

        wc = color.lighter(110)
        wc.setAlpha(120)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(wc))
        span = h - 20
        for i in range(n_bars):
            bar_h = int(amps[i] * span)
            p.drawRect(i * bar_w + 1, h - 4 - bar_h, bar_w - 1, bar_h)

    @staticmethod
    def _compute_waveform_amplitudes(frames, n_bars: int) -> list:
        """返回 n_bars 个归一化幅度（0..1）。语义与原实现一致：每段取代表帧的
        最高活动通道值（max），再除以 255。"""
        if not frames:
            return [0.0] * n_bars
        step = max(1, len(frames) // n_bars)
        out = []
        for i in range(n_bars):
            fi    = min(i * step, len(frames) - 1)
            frame = frames[fi]
            # ★ 用 max()（而非 mean）：录制中只有少数通道有值，mean 会被 508 个
            # 零通道稀释到几乎不可见。max 反映当前帧最高活动通道，与通道数无关。
            data = getattr(frame, "data", None)
            if data is None:
                avg = 0.0
            else:
                try:
                    arr = np.asarray(data, dtype=np.uint8).reshape(-1)
                    avg = float(arr.max()) if arr.size else 0.0
                except Exception:
                    avg = 0.0
            out.append(avg / 255.0)
        return out

    def _direction_set(self) -> bool:
        """是否已通过右击「分组方向过渡」选定方向——延迟时间手柄的解锁前提。"""
        di = str(getattr(self._clip, "fade_in_direction", "") or "").strip().lower()
        do = str(getattr(self._clip, "fade_out_direction", "") or "").strip().lower()
        return (di not in ("", "none")) or (do not in ("", "none"))

    def _handle_hit(self, x: int, y: int) -> Optional[str]:
        """命中检测：底部小圆点=过渡(fade)，顶部小加号=延迟(seq_delay，需先选方向)。"""
        if get_tool_mode() != ToolMode.SELECT:
            return None
        w = max(1, self.width())
        h = max(1, self.height())
        is_hold = self._is_hold()

        # 顶部延迟手柄（仅方向已选、非 hold 模式，且在 clip 上半区）
        # 加号位于过渡带之后：x = (淡入 + 依次过渡) ；淡出侧镜像
        if (not is_hold) and self._direction_set() and y < h * 0.5:
            fade_in = max(0.0, float(getattr(self._clip, "fade_in", 0.0) or 0.0))
            fade_out = max(0.0, float(getattr(self._clip, "fade_out", 0.0) or 0.0))
            din = max(0.0, float(getattr(self._clip, "seq_delay_in", 0.0) or 0.0))
            dout = max(0.0, float(getattr(self._clip, "seq_delay_out", 0.0) or 0.0))
            lx = max(0, min(w - 1, int((fade_in + din) * self._px_per_sec)))
            rx = max(0, min(w - 1, w - int((fade_out + dout) * self._px_per_sec)))
            if abs(x - lx) <= self.FADE_HANDLE_W:
                return "seq_delay_in"
            if abs(x - rx) <= self.FADE_HANDLE_W:
                return "seq_delay_out"

        # 底部过渡手柄（淡入淡出），全高度可命中以兼容旧操作习惯
        fade_in_px = int(max(0.0, float(getattr(self._clip, "fade_in", 0.0) or 0.0)) * self._px_per_sec)
        fade_out_px = int(max(0.0, float(getattr(self._clip, "fade_out", 0.0) or 0.0)) * self._px_per_sec)
        left_x = max(0, min(w - 1, fade_in_px))
        right_x = max(0, min(w - 1, w - fade_out_px))
        if abs(x - left_x) <= self.FADE_HANDLE_W:
            return "fade_in"
        # Hold 模式下禁用淡出手柄
        if not is_hold:
            if abs(x - right_x) <= self.FADE_HANDLE_W:
                return "fade_out"
        return None

    def _draw_fade_handles(self, p: QPainter, w: int, h: int):
        is_hold = self._is_hold()
        duration = max(0.0, float(getattr(self._clip, "duration", 0.0) or 0.0))
        fade_in = max(0.0, min(float(getattr(self._clip, "fade_in", 0.0) or 0.0), duration))
        fade_out = 0.0 if is_hold else max(0.0, min(float(getattr(self._clip, "fade_out", 0.0) or 0.0), duration))
        fi_x = max(0, min(w, int(fade_in * self._px_per_sec)))
        fo_x = max(0, min(w, w - int(fade_out * self._px_per_sec)))

        dir_set = (not is_hold) and self._direction_set()
        din = max(0.0, min(float(getattr(self._clip, "seq_delay_in", 0.0) or 0.0), max(0.0, duration - fade_in))) if dir_set else 0.0
        dout = max(0.0, min(float(getattr(self._clip, "seq_delay_out", 0.0) or 0.0), max(0.0, duration - fade_out))) if dir_set else 0.0
        din_x  = max(0, min(w, int((fade_in + din) * self._px_per_sec)))
        dout_x = max(0, min(w, w - int((fade_out + dout) * self._px_per_sec)))

        track_color = QColor(self._track.color)

        # ── 淡入淡出帘：轨道色 25% → 内缘渐隐（拉幕感，扁平无斜纹）────────────
        p.setPen(Qt.PenStyle.NoPen)
        for (x0, x1, direction) in [(0, fi_x, True), (fo_x, w, False)]:
            if x1 <= x0:
                continue
            edge = QColor(track_color)
            inner = QColor(track_color)
            edge.setAlpha(64)      # 25% 外缘
            inner.setAlpha(12)     # ≈5% 内缘
            grad = QLinearGradient(x0, 0, x1, 0)
            if direction:  # 淡入：左深右浅
                grad.setColorAt(0.0, edge)
                grad.setColorAt(1.0, inner)
            else:          # 淡出：左浅右深
                grad.setColorAt(0.0, inner)
                grad.setColorAt(1.0, edge)
            p.setBrush(QBrush(grad))
            p.drawRect(x0, 0, x1 - x0, h)

        # ── 分组过渡延迟带（依次过渡）：active_orange 半透底 + bright_warn 顶条 ──
        if dir_set:
            band_base = QColor(_th.ACTIVE_ORANGE)
            band_base.setAlpha(42)
            top_bar = QColor(_th.BRIGHT_WARN)
            top_bar.setAlpha(215)
            divider = QColor(_th.BRIGHT_WARN)
            divider.setAlpha(150)
            for (lx, rx) in [(fi_x, din_x), (dout_x, fo_x)]:
                if rx <= lx:
                    continue
                p.setBrush(QBrush(band_base))
                p.drawRect(lx, 0, rx - lx, h)
                # 顶部琥珀色条（2px）
                p.setBrush(QBrush(top_bar))
                p.drawRect(lx, 0, rx - lx, 2)
                # 左边界分隔细线
                p.setPen(QPen(divider, 1))
                p.drawLine(lx, 0, lx, h)
                p.setPen(Qt.PenStyle.NoPen)

        # ── 淡入淡出手柄：7px 菱形（形状语义保留）+ hover 外圈 focus_blue ────
        diamond_h = 7   # 菱形半高
        bottom_handles = (fi_x,) if is_hold else (fi_x, fo_x)
        handle_tags = ("fade_in", "fade_out")
        handle_pen = QPen(QColor(_th.BRAND_ORANGE_INK), 1)
        fill_sel = QColor(_th.SELECT_BLUE)
        fill_sel.setAlpha(235)
        ring_col = QColor(_th.FOCUS_BLUE)
        ring_col.setAlpha(210)
        # hover 判定需手柄命中模式优先于延迟徽章命中，两族分开比对
        for idx, x in enumerate(bottom_handles):
            cx = max(diamond_h + 1, min(w - diamond_h - 1, x))
            cy = h - diamond_h - 2
            tag = handle_tags[idx] if idx < len(handle_tags) else ""
            hovered = bool(tag) and self._hover_handle == tag
            if hovered:
                p.setPen(QPen(ring_col, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), diamond_h + 3.5, diamond_h + 3.5)
            p.setPen(handle_pen)
            p.setBrush(QBrush(fill_sel))
            pts = [
                QPointF(cx, cy - diamond_h),
                QPointF(cx + diamond_h, cy),
                QPointF(cx, cy + diamond_h),
                QPointF(cx - diamond_h, cy),
            ]
            p.drawPolygon(QPolygonF(pts))

        # ── 延迟带手柄：圆形徽章 + 加号（语义保留），hover 同款外圈 ──────────
        if dir_set:
            badge_r = 6
            badge_y = 8
            warn_line = QColor(_th.BRIGHT_WARN)
            warn_plus = QColor(_th.BRIGHT_WARN)
            warn_line.setAlpha(225)
            warn_plus.setAlpha(240)
            fill_badge = QColor(_th.BG_INPUT)
            fill_badge.setAlpha(210)
            for x, tag in ((din_x, "seq_delay_in"), (dout_x, "seq_delay_out")):
                cx = max(badge_r + 2, min(w - badge_r - 2, x))
                hovered = self._hover_handle == tag
                if hovered:
                    p.setPen(QPen(ring_col, 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(QPointF(cx, badge_y), badge_r + 3.5, badge_r + 3.5)
                p.setPen(QPen(warn_line, 1.5))
                p.setBrush(QBrush(fill_badge))
                p.drawEllipse(QPointF(cx, badge_y), badge_r, badge_r)
                # 加号图标（细线）
                p.setPen(QPen(warn_plus, 1.5))
                p.drawLine(cx - 3, badge_y, cx + 3, badge_y)
                p.drawLine(cx, badge_y - 3, cx, badge_y + 3)

        # ── 时间标注 ───────────────────────────────────────────────────────
        p.setFont(_font_mono(10))
        p.setPen(QColor(_th.TEXT_PRIMARY))
        if fade_in > 0.01:
            p.drawText(4, h - 15, f"IN {fade_in:.2f}s")
        if not is_hold and fade_out > 0.01:
            p.drawText(max(4, w - 66), h - 15, f"OUT {fade_out:.2f}s")

    # ── 鼠标事件 ─────────────────────────────
    def mousePressEvent(self, e: QMouseEvent):
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return

        if get_tool_mode() == ToolMode.RAZOR:
            # 用 ClipItem 在 ClipView 中的真实像素 x + 鼠标局部偏移计算绝对时间
            # 避免 _update_geometry 中 int() 截断导致的偏差
            abs_px  = self.x() + e.pos().x()
            cut_abs = abs_px / max(1.0, self._px_per_sec)
            self.clip_razor_cut.emit(self._clip.id, cut_abs)
            e.accept()
            return

        fade_hit = self._handle_hit(e.pos().x(), e.pos().y())
        is_hold = self._is_hold()
        # Hold 模式下禁用右端 resize 拖拽（箭头区域不可拖动改变长度）
        in_resize = (not is_hold) and (e.pos().x() >= self.width() - self.RESIZE_ZONE) and (fade_hit is None)
        self._drag_mode      = fade_hit or ('resize' if in_resize else 'move')
        # R57 性能：快照延迟到首次 move（见 mouseMoveEvent）；R183 T1 吸附值槽清零
        self._pre_snapped = False
        self._drag_origin_gx = float(e.globalPosition().x())
        # 多选批量拉伸: 按下时快照选中集其他 clip 的原始时长(等量同步基准,
        # 基于原始值算 orig+delta; 旧实现每次 mousemove 读已改写的 cur 再
        # 叠加全量 delta → 复利漂移)。非 resize 手势置 None 防残留。
        self._multi_orig_durs = _group_edit.prepare(self, in_resize)
        if in_resize:
            self._drag_start_val = self._clip.duration
        elif fade_hit == 'fade_in':
            self._drag_start_val = float(getattr(self._clip, 'fade_in', 0.0))
        elif fade_hit == 'fade_out':
            self._drag_start_val = float(getattr(self._clip, 'fade_out', 0.0))
        elif fade_hit == 'seq_delay_in':
            self._drag_start_val = float(getattr(self._clip, 'seq_delay_in', 0.0))
        elif fade_hit == 'seq_delay_out':
            self._drag_start_val = float(getattr(self._clip, 'seq_delay_out', 0.0))
        else:
            self._drag_start_val = self._clip.start_time
        self._drag_moved    = False
        self._is_dragging   = False
        self._drag_local_x  = self.x()
        self._drag_last_start = None   # ★ T1：move 阶段吸附后的落点（release 复用）

        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ctrl  = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        self.clip_selected.emit(self._clip.id, shift, ctrl)

        self.grabMouse()
        self._is_dragging = True
        e.accept()

    # ── 拖拽数值 tooltip（D5：统一格式/统一位置）────────────────────────
    def _snapshot_pre_drag(self):
        """R49 性能：手势起点拍轻快照（剥离 frames，亚秒级）。

        必须在首个预览变异前调用；commit 处理器见 _pre_snapped 标志即不重复。
        """
        try:
            root = self._find_timeline_root()
            if root is not None and hasattr(root, "_snapshot_for_undo_scoped"):
                _group_edit.snapshot(self, root)
                self._pre_snapped = True
        except Exception:
            pass

    def _show_drag_tip(self, text: str):
        # R49 性能：tooltip 节流（同文本 40ms 内不重复 show，防拖拽风暴）
        _now = time.monotonic()
        if (getattr(self, "_tip_last_text", None) == text
                and _now - getattr(self, "_tip_last_t", 0.0) < 0.04):
            return
        self._tip_last_text = text
        self._tip_last_t = _now
        _drag_tip.show(self, text)

    def _snap_move_start(self, dx: float, mods=None) -> float:
        """T1 拖动落位：原始落点 → 网格吸附（上下文/公式在 `timeline_snap`）。

        记住吸附后值供 release 复用（防 move/release 两次取整漂移）；命中时
        亮吸附参考线。Alt 按住 = 临时旁路（同款手势）。
        """
        raw = self._drag_start_val + dx / self._px_per_sec
        val = snap_move_start(self, raw, _alt_down(mods))
        self._drag_last_start = val
        _show_snap_guide(self, raw, val)
        return val

    def _resize_duration(self, dx: float, mods=None) -> float:
        """T1 裁剪落位：拖右缘与拖动**同一套**网格（吸终点，不吸起点）。

        ★ 清查⑧B P1-1：`_drag_start_val` 存的是**时长**，而 `snap_resize_end`
        收的是**时间线终点坐标**——修前把时长当起点传给吸附（start=3/长 2 拖
        +1.37s 落位只剩 1.0）。现换算成真实终点：end = start_time + duration。
        """
        dur0 = float(getattr(self, "_drag_start_val", 0.0) or 0.0)
        t0 = float(getattr(self._clip, "start_time", 0.0) or 0.0)
        raw_end = t0 + dur0 + dx / self._px_per_sec
        dur = snap_resize_end(self, t0, raw_end, _alt_down(mods))
        _show_snap_guide(self, raw_end, t0 + dur)
        return max(0.1, dur)

    def _final_move_start(self, gpos, mods=None) -> float:
        """释放落位：优先复用 move 阶段吸附值，缺则现算；随后收参考线。"""
        val = getattr(self, "_drag_last_start", None)
        if val is None:
            val = self._snap_move_start(
                float(gpos.x()) - self._drag_origin_gx, mods)
        _show_snap_guide(self, None, None)
        return max(0.0, float(val))

    def _ensure_pre_snapshot(self) -> None:
        """兜底：快照未拍成（root 缺失等）时在首次确认拖动处补拍（resize 专用）。

        从 `mouseMoveEvent` 外提（该函数在架构基线里 88 行、门禁只许缩小）。
        """
        if getattr(self, "_pre_snapped", False):
            return
        root = self._root_ref if getattr(self, "_root_ref", None) is not None \
            else self._find_timeline_root()
        self._root_ref = root
        if root is not None and hasattr(root, "ctrl") \
                and hasattr(root.ctrl, "_snapshot_for_undo"):
            root.ctrl._snapshot_for_undo()

    def mouseMoveEvent(self, e: QMouseEvent):
        if get_tool_mode() == ToolMode.RAZOR:
            self._razor_x = e.pos().x()
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
            e.accept()
            return

        if not (e.buttons() & Qt.MouseButton.LeftButton) or not self._is_dragging:
            hit = self._handle_hit(e.pos().x(), e.pos().y())
            if hit != getattr(self, "_hover_handle", None):
                self._hover_handle = hit
                self.update()
            if hit is not None or e.pos().x() >= self.width() - self.RESIZE_ZONE:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            return

        gpos = e.globalPosition().toPoint()
        gx   = float(gpos.x())
        gy   = gpos.y()
        dx   = gx - self._drag_origin_gx

        # R57：首次 move（含微小抖动）先拍 pre-state 快照——必须位于下方任何
        # 模型写入（fade/duration 预览、cross_track_drag 的 start_time 写入）之前。
        if not getattr(self, "_pre_snapped", False):
            self._snapshot_pre_drag()

        if abs(dx) > 2:
            if not self._drag_moved and self._drag_mode == 'resize':
                self._ensure_pre_snapshot()
            self._drag_moved = True

        if self._drag_mode == 'move':
            # ★ T1 网格吸附：落位吸到所选档（Alt 旁路）；吸附值 release 复用。
            new_start = self._snap_move_start(dx, e.modifiers())
            new_local_x = max(0, int(new_start * self._px_per_sec))
            self._drag_local_x = new_local_x
            self._update_geometry_drag(new_local_x)
            self.update()
            self._show_drag_tip(f"起点 {new_start:.2f}s")
            self.cross_track_drag.emit(self._clip.id, gy, new_start)

        elif self._drag_mode == 'resize':
            # ★ R163 用户定案：拖右缘**只改 duration，绝不裁两翼** —— "过渡翼和
            #   延迟翼是一切效果延迟的最终管理"。旧写法每移动一像素就把四个翼字段
            #   `min(翼, new_dur)` **永久**截短（拉长不恢复）→ 用户报的"clip 总长决定
            #   效果淡入最终时间"。装不下时由引擎渲染期临时压紧（不改字段）。
            new_dur = self._resize_duration(dx, e.modifiers())
            self._clip.duration = new_dur
            self._update_geometry()
            self._sync_multi_resize(new_dur)
            self._show_drag_tip(f"时长 {self._clip.duration:.2f}s")

        elif self._drag_mode == 'fade_in':
            new_fade = max(0.0, min(float(getattr(self._clip, 'duration', 0.0)), self._drag_start_val + dx / self._px_per_sec))
            self._clip.fade_in = new_fade
            self.update()
            self._show_drag_tip(f"淡入 {new_fade:.2f}s")

        elif self._drag_mode == 'fade_out':
            new_fade = max(0.0, min(float(getattr(self._clip, 'duration', 0.0)), self._drag_start_val - dx / self._px_per_sec))
            self._clip.fade_out = new_fade
            self.update()
            self._show_drag_tip(f"淡出 {new_fade:.2f}s")

        elif self._drag_mode == 'seq_delay_in':
            # R12(2026-08-13): 分组过渡翼钳制改为 ≤ duration(不再 −fade_in, 语义解耦:
            # 扫掠 span 与单台 fade 相互独立, 均由各自翼决定)。
            dur = float(getattr(self._clip, 'duration', 0.0))
            new_d = max(0.0, min(max(0.0, dur), self._drag_start_val + dx / self._px_per_sec))
            self._clip.seq_delay_in = new_d
            self.update()
            self._show_drag_tip(f"分组扫掠·入 {new_d:.2f}s")

        elif self._drag_mode == 'seq_delay_out':
            dur = float(getattr(self._clip, 'duration', 0.0))
            new_d = max(0.0, min(max(0.0, dur), self._drag_start_val - dx / self._px_per_sec))
            self._clip.seq_delay_out = new_d
            self.update()
            self._show_drag_tip(f"分组扫掠·出 {new_d:.2f}s")

        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            if self._drag_moved:
                if self._drag_mode == 'move':
                    gpos = e.globalPosition().toPoint()
                    gy   = gpos.y()
                    # ★ T1：优先复用 move 阶段吸附后的落点（release 只读一次光标
                    #   重算会绕过吸附；Alt 旁路时 move 阶段已按原值记录，同样一致）。
                    final_start = self._final_move_start(gpos, e.modifiers())
                    if DEBUG:
                        print(f"[ClipItem] cross_track_drag_end emit: "
                              f"clip={self._clip.id[:6]} global_y={gy} "
                              f"new_start={final_start:.3f}")
                    # ★ 先发信号，再 releaseMouse
                    self.cross_track_drag_end.emit(self._clip.id, gy, final_start)

                elif self._drag_mode == 'resize':
                    self.clip_resize_commit.emit(self._clip.id, self._clip.duration)

                elif self._drag_mode in ('fade_in', 'fade_out'):
                    # ★ R135：只提交被拖的那一侧（另一侧 NaN = 保持各 clip 原值）
                    self.clip_fade_commit.emit(self._clip.id,
                                               *_dragged_edge_pair(self._clip, "fade", self._drag_mode))

                elif self._drag_mode in ('seq_delay_in', 'seq_delay_out'):
                    # ★ R135：同款（见 `_dragged_edge_pair`）
                    self.clip_seq_delay_commit.emit(self._clip.id,
                                                    *_dragged_edge_pair(self._clip, "seq_delay", self._drag_mode))

            # ★ releaseMouse 放到最后
            _group_edit.release(self)
            self._is_dragging   = False
            self._drag_mode     = None
            self._drag_moved    = False
            self._drag_local_x  = None
            self._multi_orig_durs = None
            _show_snap_guide(self, None, None)     # T1：收吸附参考线（防残留竖线）
            QToolTip.hideText()
            _drag_tip.hide(self)
            self.update()

        e.accept()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover   = False
        self._razor_x = None
        if self._hover_handle is not None:
            self._hover_handle = None
            self.update()
        self.update()

    def contextMenuEvent(self, e: QContextMenuEvent):
        """Select the target Clip, then show the grouped context menu."""
        parent_view = self.parent()
        selected_ids = set(
            getattr(parent_view, "_selected_ids", set()) or set()
        ) if parent_view is not None else set()
        if self._clip.id not in selected_ids:
            self.clip_selected.emit(self._clip.id, False, False)
        build_clip_context_menu(self, e.globalPos())

    # ── 多 Clip 冲突处理 ───────────────────────────────────────────────
    def _find_timeline_root(self):
        """向上查找暴露 ctrl + _selected_clip_ids 的 TimelineWidget。"""
        p = self.parent()
        for _ in range(8):
            if p is None:
                break
            if hasattr(p, "ctrl") and hasattr(p, "_selected_clip_ids"):
                return p
            p = p.parent()
        return None

    def _snapshot_multi_orig_durs(self):
        """多选批量拉伸: 按下时快照选中集内其他 clip 的原始时长。

        等量同步基于该快照计算(orig + delta); 旧实现每次 mousemove 读已被
        上次 mousemove 改写的 cur 再叠加全量 delta → 复利漂移(拖 +0.1s 共
        20 次, 其他 clip 从 5s 被放大成 26s)。取不到 root/选中集时返回
        None → _sync_multi_resize 回退旧路径(仅极端环境, 不静默失败)。"""
        root = self._root_ref if getattr(self, "_root_ref", None) is not None else self._find_timeline_root()
        self._root_ref = root
        if root is None:
            return None
        try:
            ids = set(root._selected_clip_ids() or [])
        except Exception:
            return None
        ids.discard(self._clip.id)
        if not ids:
            return None
        out = {}
        for view in root._iter_clip_views():
            for item in (getattr(view, "_items", {}) or {}).values():
                c = item._clip
                if c.id in ids:
                    out[c.id] = max(0.1, float(getattr(c, "duration", 0.1) or 0.1))
        return out

    def _sync_multi_resize(self, new_dur: float):
        """Preview a shared duration delta; original resize finishes on release."""
        _group_edit.preview_resize(self, new_dur)

    def _conflict_selected_ids(self) -> list:
        """跨轨当前选中的全部 clip id；取不到时回退到本 clip。"""
        root = self._find_timeline_root()
        if root is not None:
            try:
                ids = list(root._selected_clip_ids() or [])
            except Exception:
                ids = []
            if ids:
                return ids
        return [self._clip.id]

    def _add_conflict_menu(self, menu: QMenu):
        ids = self._conflict_selected_ids()
        enabled = len(ids) >= 2
        # ★ R142：必须用**常开**子菜单 —— 原生 `addMenu` 出来的子项点击会
        #   `hideUpToMenuBar` 把整棵树收掉，用户在"常开"右击页里点它会被踢出菜单。
        from .sticky_menu import make_sticky_menu
        conflict_menu = make_sticky_menu(menu, "多 Clip 冲突处理")
        menu.addMenu(conflict_menu)
        if _make_icon: conflict_menu.setIcon(_make_icon("layout_programming", 16))
        conflict_menu.setStyleSheet(menu.styleSheet())
        conflict_menu.setEnabled(enabled)
        if not enabled:
            tip = conflict_menu.addAction("先框选 ≥2 个构成冲突的 Clip")
            tip.setEnabled(False)
            return
        act_trans = conflict_menu.addAction("过渡模式（后者渐入接管前者）")
        act_trans.triggered.connect(
            lambda checked=False: self._apply_conflict_mode("transition", crossfade=True))
        act_trans_in = conflict_menu.addAction("过渡模式（仅后者淡入，不淡出前者）")
        act_trans_in.triggered.connect(
            lambda checked=False: self._apply_conflict_mode("transition", crossfade=False))
        conflict_menu.addSeparator()
        act_comp = conflict_menu.addAction("合成模式（同时段一起输出）")
        act_comp.triggered.connect(
            lambda checked=False: self._apply_conflict_mode("composite", crossfade=True))

    def _apply_conflict_mode(self, mode: str, crossfade: bool = True):
        root = self._find_timeline_root()
        ids = self._conflict_selected_ids()
        if root is None or len(ids) < 2:
            return
        ctrl = getattr(root, "ctrl", None)
        if ctrl is None or not hasattr(ctrl, "resolve_clip_conflicts"):
            return
        ctrl.resolve_clip_conflicts(ids, mode, crossfade=crossfade)



    def _ask_custom_fade(self, which: str):
        from PyQt6.QtWidgets import QInputDialog
        title = "自定义淡入时长" if which == "in" else "自定义淡出时长"
        current = float(getattr(self._clip, "fade_in" if which == "in" else "fade_out", 0.0) or 0.0)
        val, ok = QInputDialog.getDouble(self, title, "时长（秒）：", current, 0.0, 999.0, 2)
        if ok:
            # ★ R135：只提交被改的那一侧，另一侧发 NaN（保持各 clip 原值）
            #   原实现两个值一起发 → 多选时把主人的另一侧抄给所有人（数据污染）。
            if which == "in":
                self.clip_fade_commit.emit(self._clip.id, float(val), _NAN)
            else:
                self.clip_fade_commit.emit(self._clip.id, _NAN, float(val))

    def _ask_custom_speed(self):
        from PyQt6.QtWidgets import QInputDialog
        current_pct = self._speed_rate() * 100.0
        val, ok = QInputDialog.getDouble(
            self,
            "自定义 Clip 速度",
            "速度百分比（100 为原速）：",
            current_pct,
            0.1,
            10000.0,
            2,
        )
        if ok:
            self.clip_speed_changed.emit(self._clip.id, max(0.001, float(val) / 100.0))

    def _ask_motor_transition_phase_amount(self):
        from PyQt6.QtWidgets import QInputDialog
        current_pct = max(0.0, float(getattr(self._clip, "motor_transition_phase_amount", 1.0) or 0.0)) * 100.0
        val, ok = QInputDialog.getDouble(
            self,
            "电机相位差强度",
            "强度百分比（100 为标准，200 更明显）：",
            max(0.0, current_pct),
            0.0,
            1000.0,
            1,
        )
        if ok:
            amount = max(0.0, min(10.0, float(val) / 100.0))
            self.clip_motor_transition_phase_changed.emit(self._clip.id, amount > 0.0, amount)

    def _emit_record_output_source_action_state(self, action):
        enabled = bool(action.isChecked()) if action is not None else False
        self.clip_record_output_source_changed.emit(self._clip.id, enabled)

    def _emit_motor_transition_phase_action_state(self, action):
        enabled = bool(action.isChecked()) if action is not None else False
        try:
            amount = max(0.0, float(getattr(self._clip, "motor_transition_phase_amount", 1.0) or 1.0))
        except Exception:
            amount = 1.0
        self.clip_motor_transition_phase_changed.emit(self._clip.id, enabled, amount)

    def _group_mode(self) -> str:
        mode = str(getattr(self._clip, "fade_group_sequence_mode", "parallel") or "parallel").strip().lower()
        return mode if mode in ("parallel", "sequential") else "parallel"

    def _group_pause(self) -> float:
        return max(0.0, float(getattr(self._clip, "fade_group_pause", 0.0) or 0.0))

    def _group_batches(self, groups: Optional[int] = None) -> int:
        groups_i = max(1, int(groups if groups is not None else getattr(self._clip, "fade_groups", 1) or 1))
        batches = max(0, int(getattr(self._clip, "fade_group_sequence_batches", 0) or 0))
        return max(0, min(groups_i, batches))

    def _group_batch_count(self, groups: Optional[int] = None, batches: Optional[int] = None) -> int:
        groups_i = max(1, int(groups if groups is not None else getattr(self._clip, "fade_groups", 1) or 1))
        batches_i = self._group_batches(groups_i) if batches is None else max(0, min(groups_i, int(batches or 0)))
        return groups_i if batches_i <= 0 else max(1, min(groups_i, batches_i))

    def _group_pauses(self, groups: Optional[int] = None, batches: Optional[int] = None,
                      pause: Optional[float] = None) -> list[float]:
        pause_f = self._group_pause() if pause is None else max(0.0, float(pause or 0.0))
        return normalize_group_pauses(
            getattr(self._clip, "fade_group_pauses", []),
            max(0, self._group_batch_count(groups, batches) - 1),
            pause_f,
        )

    def _directions_for_sequential_current(self) -> tuple[str, str]:
        in_dir = getattr(self._clip, "fade_in_direction", "none") or "none"
        out_dir = getattr(self._clip, "fade_out_direction", "none") or "none"
        if in_dir == "none" and out_dir != "none":
            in_dir = out_dir
        if in_dir == "none":
            in_dir = "ltr"
        if out_dir == "none":
            out_dir = in_dir
        return in_dir, out_dir

    def _emit_dir_settings(self, in_direction: str, out_direction: str, groups: int,
                           mode: str, pause: Optional[float] = None,
                           batches: Optional[int] = None,
                           pauses: Optional[list[float]] = None):
        groups_i = max(1, int(groups or 1))
        mode_s = str(mode or "parallel").strip().lower()
        if mode_s not in ("parallel", "sequential"):
            mode_s = "parallel"
        in_direction = in_direction or "none"
        out_direction = out_direction or "none"
        if mode_s == "sequential":
            if in_direction == "none" and out_direction != "none":
                in_direction = out_direction
            if in_direction == "none":
                in_direction = "ltr"
            if out_direction == "none":
                out_direction = in_direction
        else:
            if in_direction == "none" and out_direction != "none":
                in_direction = out_direction
            elif out_direction == "none" and in_direction != "none":
                out_direction = in_direction
        pause_f = self._group_pause() if pause is None else max(0.0, float(pause or 0.0))
        batches_i = self._group_batches(groups_i) if batches is None else max(0, min(groups_i, int(batches or 0)))
        pause_list = (
            self._group_pauses(groups_i, batches_i, pause_f)
            if pauses is None
            else normalize_group_pauses(pauses, max(0, self._group_batch_count(groups_i, batches_i) - 1), pause_f)
        )
        self.clip_dir_fade_set.emit(
            self._clip.id,
            in_direction,
            out_direction,
            groups_i,
            mode_s,
            pause_f,
            batches_i,
            pause_list,
        )

    def _ask_custom_groups(self):
        from PyQt6.QtWidgets import QInputDialog
        val, ok = QInputDialog.getInt(self, "自定义分组数", "请输入分组数（1-100）：",
                                      max(1, int(getattr(self._clip, "fade_groups", 1) or 1)), 1, 100, 1)
        if ok:
            mode = str(getattr(self._clip, "fade_group_sequence_mode", "parallel") or "parallel").strip().lower()
            if mode not in ("parallel", "sequential"):
                mode = "parallel"
            batches = max(0, int(getattr(self._clip, "fade_group_sequence_batches", 0) or 0))
            self._emit_dir_settings(
                getattr(self._clip, "fade_in_direction", "none") or "none",
                getattr(self._clip, "fade_out_direction", "none") or "none",
                int(val),
                mode,
                self._group_pause(),
                max(0, min(int(val), batches)),
            )

    def _ask_custom_sequence_batches(self):
        from PyQt6.QtWidgets import QInputDialog
        groups = max(2, int(getattr(self._clip, "fade_groups", 1) or 1))
        current = max(0, int(getattr(self._clip, "fade_group_sequence_batches", 0) or 0))
        if current <= 0:
            current = groups
        val, ok = QInputDialog.getInt(
            self,
            "自定义顺序执行批次",
            "请输入执行批次数（1 到当前分组数）：",
            max(1, min(groups, current)),
            1,
            groups,
            1,
        )
        if ok:
            in_dir = getattr(self._clip, "fade_in_direction", "none") or "none"
            out_dir = getattr(self._clip, "fade_out_direction", "none") or "none"
            if in_dir == "none" and out_dir != "none":
                in_dir = out_dir
            if in_dir == "none":
                in_dir = "ltr"
            if out_dir == "none":
                out_dir = in_dir
            self._emit_dir_settings(
                in_dir,
                out_dir,
                groups,
                "sequential",
                self._group_pause(),
                int(val),
            )

    def _ask_custom_group_pause(self):
        from PyQt6.QtWidgets import QDialog, QDoubleSpinBox, QDialogButtonBox, QFormLayout, QLabel
        groups = max(2, int(getattr(self._clip, "fade_groups", 1) or 1))
        batches = self._group_batches(groups)
        batch_count = self._group_batch_count(groups, batches)
        pause_count = max(1, batch_count - 1)
        current = self._group_pauses(groups, batches, self._group_pause())

        dlg = QDialog(self)
        dlg.setWindowTitle("自定义组间停顿")
        form = QFormLayout(dlg)
        form.addRow(QLabel(f"当前最终执行批次：{batch_count}，需要设置 {pause_count} 次停顿。"))
        spins: list[QDoubleSpinBox] = []
        for idx in range(pause_count):
            spin = QDoubleSpinBox(dlg)
            spin.setRange(0.0, 999.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.1)
            spin.setSuffix(" s")
            spin.setValue(float(current[idx] if idx < len(current) else self._group_pause()))
            form.addRow(f"第 {idx + 1} 次停顿", spin)
            spins.append(spin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pauses = [float(sp.value()) for sp in spins]
            in_dir, out_dir = self._directions_for_sequential_current()
            self._emit_dir_settings(
                in_dir,
                out_dir,
                groups,
                "sequential",
                pauses[0] if pauses else 0.0,
                batches,
                pauses,
            )

    def _ask_light_tracking(self):
        # 对话框链搬到 src/ui/timeline/light_tracking_dialog.py（R178 门禁：本函数
        # 曾被 R17x 的说明确认框撑到 73 行/CC 14）。语义不变：任一步取消 → 不 emit。
        from src.ui.timeline.light_tracking_dialog import collect_light_tracking_params
        params = collect_light_tracking_params(self, self._clip)
        if params is None:
            return
        self.clip_light_tracking.emit(self._clip.id, True, *params)

    def _show_light_tracking_help(self) -> None:
        """显示灯跟踪详细帮助对话框。"""
        from src.ui.help_dialogs import show_help_dialog, LIGHT_TRACKING_HELP_HTML
        show_help_dialog(self, "灯光跟踪帮助", LIGHT_TRACKING_HELP_HTML)

    def _optimize_transitions(self):
        """连贯动作过渡：多选相邻 clip 后自动填充过渡。

        原则：
        - 只在两个 clip 之间的空隙插入一个轻型「bridge」过渡帧，不覆盖手动参数。
        - 若 clip 已有手动 fade_in/fade_out/seq_delay 设置 → 跳过该侧不碰。
        - 电机动作间: bridge≈200ms 位置渐变
        - 泵动作间: bridge≈600ms 转速渐变（管道充水补偿）
        - 灯光动作间: bridge≈300ms 颜色/亮度渐变
        """
        root = self._find_timeline_root()
        if root is None:
            return
        ctrl = getattr(root, "ctrl", None)
        if ctrl is None or not hasattr(ctrl, "_find_track_and_clip"):
            return
        ids = self._conflict_selected_ids()
        if len(ids) < 2:
            QMessageBox.information(
                self, "优化过渡方式",
                "请先框选 ≥2 个相邻 Clip（Ctrl 多选），\n"
                "系统将按顺序检测相邻 Clip 间的过渡缺口并自动填补。"
            )
            return
        # 通知 TimelineWidget 批量处理
        self.clip_optimize_transitions.emit(ids)

    def _ask_precharge(self):
        """泵预充设置对话框：启用/提前时间/预置电平。"""
        from PyQt6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout,
            QCheckBox, QDoubleSpinBox, QSpinBox,
            QDialogButtonBox, QLabel,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("泵预充设置")
        dlg.setMinimumWidth(380)
        # 删除手写stylesheet，继承父widget样式

        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 12, 16, 12)

        # 状态预览标签
        current_enabled = bool(getattr(self._clip, "precharge_enabled", False))
        status_label = QLabel(f"当前状态：{'已启用' if current_enabled else '未启用'}")
        layout.addWidget(status_label)

        cb = QCheckBox("启用泵预充")
        cb.setChecked(current_enabled)
        cb.setToolTip("勾选后在 Clip 开始前向泵通道发送预置 DMX 电平")
        layout.addWidget(cb)

        hl1 = QHBoxLayout()
        hl1.addWidget(QLabel("提前时间 (ms)："))
        time_spin = QDoubleSpinBox()
        time_spin.setRange(0.0, 10000.0)
        time_spin.setSingleStep(100.0)
        time_spin.setDecimals(0)
        time_spin.setValue(max(0.0, float(getattr(self._clip, "precharge_lead_ms", 1000.0) or 1000.0)))
        time_spin.setToolTip("Clip 开始前多少毫秒启动预充电")
        time_spin.setMinimumWidth(120)
        hl1.addWidget(time_spin, 1)
        layout.addLayout(hl1)

        hl2 = QHBoxLayout()
        hl2.addWidget(QLabel("预置电平 (0-255)："))
        level_spin = QSpinBox()
        level_spin.setRange(0, 255)
        level_spin.setValue(max(0, min(255, int(getattr(self._clip, "precharge_level", 13) or 0))))
        level_spin.setToolTip("发送给泵通道的预充电 DMX 值 (~5Hz 等效 = 13/255)")
        level_spin.setMinimumWidth(120)
        hl2.addWidget(level_spin, 1)
        layout.addLayout(hl2)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            dlg,
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            enabled = bool(cb.isChecked())
            lead_ms = max(0.0, float(time_spin.value()))
            level = max(0, min(255, int(level_spin.value())))
            self.clip_precharge_changed.emit(self._clip.id, enabled, lead_ms, level)

    def _razor_at_cursor(self):
        pos = self.mapFromGlobal(QCursor.pos())
        # 与 mousePressEvent 中保持一致：用真实像素 x 计算绝对时间
        abs_px  = self.x() + pos.x()
        cut_abs = abs_px / max(1.0, self._px_per_sec)
        self.clip_razor_cut.emit(self._clip.id, cut_abs)

    def wheelEvent(self, e):
        """将滚轮事件向上转发给 ClipArea 统一处理"""
        # ClipItem 父 = ClipView，ClipView 父 = _TrackContainer，
        # _TrackContainer 父 = _ScrollViewport，_ScrollViewport 父 = ClipArea
        p = self.parent()
        for _ in range(4):
            if p is None:
                break
            if hasattr(p, '_handle_wheel'):
                p._handle_wheel(e)
                e.accept()  # 阻止事件继续冒泡，避免重复触发
                return
            p = p.parent()
        e.ignore()

    @property
    def clip(self) -> Clip:
        return self._clip


# ══════════════════════════════════════════════════════════════════════════════
#  ClipView
# ══════════════════════════════════════════════════════════════════════════════
class ClipView(QWidget):
    clip_move_commit     = pyqtSignal(str, float)
    clip_selected        = pyqtSignal(str, bool, bool)
    clip_deleted         = pyqtSignal(str)
    clip_to_processor    = pyqtSignal(object)
    clip_load_fixture_group = pyqtSignal(object)
    clip_duplicated      = pyqtSignal(str)
    clip_cut             = pyqtSignal(str)
    clip_copy            = pyqtSignal(str)
    clip_resize_commit   = pyqtSignal(str, float)
    clip_fade_commit     = pyqtSignal(str, float, float)
    clip_seq_delay_commit = pyqtSignal(str, float, float)
    cross_track_drag     = pyqtSignal(str, int, float)
    cross_track_drag_end = pyqtSignal(str, int, float)
    clip_razor_cut       = pyqtSignal(str, float)
    clip_dir_fade_set    = pyqtSignal(str, str, str, int, str, float, int, list)   # clip_id, in_direction, out_direction, groups, mode, pause, batches, pauses
    clip_spatial_scope_set = pyqtSignal(str, str)  # clip_id, per_shape/global
    clip_skip_base_order_set = pyqtSignal(str, bool)  # clip_id, skip_base_order（#18 跳过编号排序）
    clip_transition_strategy_set = pyqtSignal(str, str, str)  # clip_id, edge(in/out), strategy_id（淡入/淡出分开设置）
    clip_dir_edge_set = pyqtSignal(str, str)  # clip_id, edge(both/in/out)（分组方向过渡应用边 2026-08-12）
    clip_auto_smooth     = pyqtSignal(str, str)              # clip_id, preset_id
    clip_motor_force     = pyqtSignal(str, str)              # clip_id, force_preset (bypass/soft/natural/strong/blast)
    clip_light_tracking  = pyqtSignal(str, bool, int, float, float, str, str, int)  # clip_id, enabled, threshold, attack, release, color, source, max_brightness
    clip_hold_toggled    = pyqtSignal(str, bool)              # clip_id, enabled
    clip_hold_mode_changed = pyqtSignal(str, str)             # clip_id, none/last_frame/last_cycle
    clip_speed_changed   = pyqtSignal(str, float)             # clip_id, playback_rate
    clip_record_output_source_changed = pyqtSignal(str, bool) # clip_id, enabled
    clip_motor_transition_phase_changed = pyqtSignal(str, bool, float) # clip_id, enabled, amount
    clip_precharge_changed = pyqtSignal(str, bool, float, int)      # clip_id, enabled, lead_ms, level
    clip_optimize_transitions = pyqtSignal(list)                      # [clip_id, ...] bulk
    clip_open_processing_studio = pyqtSignal(str)                     # clip_id（R172：转发自 ClipItem，打开 Studio）
    def __init__(self, track: Track, px_per_sec: float, parent=None):
        super().__init__(parent)
        self._track          = track
        self._px_per_sec     = px_per_sec
        self._items: dict[str, ClipItem] = {}
        self._selected_ids: set[str]     = set()

        self._rubberband_origin: Optional[QPoint] = None
        self._rubberband_rect:   Optional[QRect]  = None

        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background:transparent;")
        self.setMouseTracking(True)
        self._refresh_clips()

    def _refresh_clips(self):
        """★ 全量重建（tracks_changed 时调用，不在拖动中调用）。"""
        # ★ 文件夹轨道不创建 ClipItem，清除已有子控件
        _is_no_clip = bool(getattr(self._track, "is_folder", False))
        if _is_no_clip:
            for item in list(self._items.values()):
                item.deleteLater()
            self._items.clear()
            self._update_min_width()
            return
        clip_ids = {c.id for c in self._track.clips}
        for cid in list(self._items):
            if cid not in clip_ids:
                self._items[cid].deleteLater()
                del self._items[cid]
        for clip in self._track.clips:
            if clip.id not in self._items:
                item = ClipItem(clip, self._track, self._px_per_sec, parent=self)
                item.clip_selected.connect(self._on_item_selected)
                item.clip_delete_req.connect(self.clip_deleted)
                item.clip_to_processor.connect(self.clip_to_processor)
                item.clip_load_fixture_group.connect(self.clip_load_fixture_group)
                item.clip_duplicate.connect(self.clip_duplicated)
                item.clip_cut.connect(self.clip_cut)
                item.clip_copy.connect(self.clip_copy)
                item.clip_move_commit.connect(self.clip_move_commit)
                item.clip_resize_commit.connect(self.clip_resize_commit)
                item.clip_fade_commit.connect(self.clip_fade_commit)
                item.clip_seq_delay_commit.connect(self.clip_seq_delay_commit)
                item.cross_track_drag.connect(self.cross_track_drag)

                # ★ v8 修复：改为中转槽，方便调试
                item.cross_track_drag_end.connect(self._on_item_cross_track_drag_end)

                item.clip_razor_cut.connect(self.clip_razor_cut)
                item.clip_dir_fade_set.connect(self.clip_dir_fade_set)
                item.clip_spatial_scope_set.connect(self.clip_spatial_scope_set)
                item.clip_skip_base_order_set.connect(self.clip_skip_base_order_set)
                item.clip_transition_strategy_set.connect(self.clip_transition_strategy_set)
                item.clip_dir_edge_set.connect(self.clip_dir_edge_set)
                item.clip_auto_smooth.connect(self.clip_auto_smooth)
                if hasattr(item, "clip_motor_force"):
                    item.clip_motor_force.connect(self.clip_motor_force)
                item.clip_light_tracking.connect(self.clip_light_tracking)
                if hasattr(item, "clip_hold_toggled"):
                    item.clip_hold_toggled.connect(self.clip_hold_toggled)
                if hasattr(item, "clip_hold_mode_changed"):
                    item.clip_hold_mode_changed.connect(self.clip_hold_mode_changed)
                if hasattr(item, "clip_speed_changed"):
                    item.clip_speed_changed.connect(self.clip_speed_changed)
                if hasattr(item, "clip_record_output_source_changed"):
                    item.clip_record_output_source_changed.connect(self.clip_record_output_source_changed)
                if hasattr(item, "clip_motor_transition_phase_changed"):
                    item.clip_motor_transition_phase_changed.connect(self.clip_motor_transition_phase_changed)
                if hasattr(item, "clip_precharge_changed"):
                    item.clip_precharge_changed.connect(self.clip_precharge_changed)
                _connect_if_hasattr(item, "clip_optimize_transitions", self.clip_optimize_transitions)
                _connect_if_hasattr(item, "clip_open_processing_studio", self.clip_open_processing_studio)
                if DEBUG:
                    print(f"[ClipView] CONNECT clip={clip.id[:6]} "
                          f"cross_track_drag_end → _on_item_cross_track_drag_end")

                self._items[clip.id] = item
            else:
                item = self._items[clip.id]
                item._clip = clip
                item._track = self._track
                if not getattr(item, "_is_dragging", False):
                    item._update_geometry()
                    item.update()
            self._items[clip.id].show()
        self._update_min_width()

    def _on_item_cross_track_drag_end(self, clip_id: str, global_y: int, new_start: float):
        """
        ★ 中转槽：接收 ClipItem 的 cross_track_drag_end，
        打印日志后转发给上层。
        """
        if DEBUG:
            print(f"[ClipView] _on_item_cross_track_drag_end: "
                  f"clip={clip_id[:6]} global_y={global_y} new_start={new_start:.3f}")
        self.cross_track_drag_end.emit(clip_id, global_y, new_start)

    def refresh_geometry_only(self, clip_id: str = None, reset_waveform: bool = False):
        """
        ★ 轻量刷新：仅更新现有 ClipItem 的 geometry，不销毁重建。
        由 clip_changed 信号触发（拖动过程中）。
        reset_waveform=True：帧数据被原地重写（平滑应用）时同步失效
        item 波形条缓存（其 key 只含形状，内容变化不自动失效）。
        """
        if clip_id and clip_id in self._items:
            item = self._items[clip_id]
            if reset_waveform:
                item._waveform_cache_key = None
                item._waveform_cache_bars = None
            if not item._is_dragging:
                item._update_geometry()
                item.update()
        else:
            for item in self._items.values():
                if reset_waveform:
                    item._waveform_cache_key = None
                    item._waveform_cache_bars = None
                if not item._is_dragging:
                    item._update_geometry()
                    item.update()
        self._update_min_width()

    def _update_min_width(self):
        if not self._track.clips:
            self.setMinimumWidth(400)
            return
        max_end = max(c.start_time + c.duration for c in self._track.clips)
        self.setMinimumWidth(int(max_end * self._px_per_sec) + 300)

    def _on_item_selected(self, clip_id: str, shift: bool, ctrl: bool):
        _group_edit.select_item(self, clip_id, shift, ctrl)

    def set_selected_ids(self, ids: set[str]):
        self._selected_ids = ids
        for cid, item in self._items.items():
            item.set_selected(cid in ids)

    def deselect_all(self):
        self._selected_ids.clear()
        for item in self._items.values():
            item.set_selected(False)

    def mousePressEvent(self, e: QMouseEvent):
        # ★ 文件夹轨道禁止任何鼠标交互
        if getattr(self._track, "is_folder", False):
            e.ignore()
            return
        if e.button() == Qt.MouseButton.RightButton:
            # 右键点击空白区域：显示粘贴菜单
            child = self.childAt(e.pos())
            if child is None or not isinstance(child, ClipItem):
                self._show_blank_context_menu(e)
                e.accept()
                return
        if e.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(e.pos())
            if child is None or not isinstance(child, ClipItem):
                parent = self.parent()
                if parent is not None and hasattr(parent, "begin_rubberband_from_view"):
                    parent.begin_rubberband_from_view(self, e.pos(), e.modifiers())
                else:
                    self._rubberband_origin = e.pos()
                    self._rubberband_rect   = QRect(e.pos(), QSize(0, 0))
                    if not (e.modifiers() & (Qt.KeyboardModifier.ShiftModifier |
                                             Qt.KeyboardModifier.ControlModifier)):
                        self.deselect_all()
        e.accept()

    def _show_blank_context_menu(self, e: QMouseEvent):
        """ClipView 空白区域的右键菜单（粘贴片段等）"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        # 2026-08-27 换肤：原自定义 QMenu 样式表（#262626/#3a5f7a/#444）与全局主题重复，
        # 按 §3/§5 删除，统一使用全局 QMenu 四态样式。
        click_time = e.pos().x() / max(1.0, self._px_per_sec)
        # 粘贴 Clip
        paste_act = menu.addAction("↳  粘贴片段（到点击位置）")
        # 检查剪贴板是否有内容（通过父级 TimelineWidget 查询）
        has_clip_cb = False
        parent = self.parent()
        while parent:
            if hasattr(parent, 'ctrl'):
                has_clip_cb = parent.ctrl.has_clip_clipboard()
                break
            parent = parent.parent()
        paste_act.setEnabled(has_clip_cb)
        paste_act.triggered.connect(lambda: self.paste_at(click_time))
        menu.exec(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e: QMouseEvent):
        parent = self.parent()
        if parent is not None and hasattr(parent, "is_rubberband_active") and parent.is_rubberband_active():
            parent.update_rubberband_from_view(self, e.pos())
            e.accept()
            return
        if self._rubberband_origin and (e.buttons() & Qt.MouseButton.LeftButton):
            self._rubberband_rect = QRect(
                self._rubberband_origin, e.pos()).normalized()
            self.update()
        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent):
        parent = self.parent()
        if parent is not None and hasattr(parent, "is_rubberband_active") and parent.is_rubberband_active():
            parent.end_rubberband_from_view(self, e.pos())
            e.accept()
            return
        if self._rubberband_rect and self._rubberband_origin:
            rb = self._rubberband_rect
            for cid, item in self._items.items():
                if rb.intersects(item.geometry()):
                    self._selected_ids.add(cid)
            for cid, item in self._items.items():
                item.set_selected(cid in self._selected_ids)
        self._rubberband_origin = None
        self._rubberband_rect   = None
        self.update()
        e.accept()

    def paste_at(self, time_pos: float):
        parent = self.parent()
        while parent:
            if hasattr(parent, 'handle_paste_at'):
                parent.handle_paste_at(self._track.id, time_pos)
                return
            parent = parent.parent()

    def wheelEvent(self, e):
        """将滚轮事件向上转发给 ClipArea 统一处理"""
        p = self.parent()
        for _ in range(4):
            if p is None:
                break
            if hasattr(p, '_handle_wheel'):
                p._handle_wheel(e)
                e.accept()  # 阻止事件继续冒泡，避免重复触发
                return
            p = p.parent()
        e.ignore()

    def set_px_per_sec(self, val: float):
        self._px_per_sec = val
        for item in self._items.values():
            item.set_px_per_sec(val)
        self._update_min_width()

    def refresh(self):
        """★ 全量刷新（tracks_changed 触发，结构变化时）。"""
        self._refresh_clips()
        for item in self._items.values():
            item._update_geometry()
        self._update_min_width()

    # ── 泳道高度（不含 TRACK_GAP 分隔区）─────────────────────────────────
    @property
    def _lane_height(self) -> int:
        """实际轨道内容高度，不含底部 TRACK_GAP 分隔区。"""
        from .clip_area import TRACK_GAP, _track_height
        return _track_height(self._track)

    def _zebra_row_index(self) -> int:
        """斑马纹行号来自父容器（`_TrackContainer._track_row_index`），
        取不到时按 0 处理（不影响绘制，只是条纹起始色退化为默认）。"""
        pw = self.parent()
        if pw and hasattr(pw, '_track_row_index'):
            return pw._track_row_index(self._track.id)
        return 0

    @staticmethod
    def _phase_aligned_start(x0: int, step: int, phase: int = 0) -> int:
        """给定曝光区左边界 x0、步长 step、循环相位 phase（例如文件夹斜线的
        i0=-lane_h），返回 >= x0 且与相位对齐的第一个循环起点 —— 只画曝光区
        与"从 phase 起画到头"在可见范围内逐像素相同，只是不再白算。"""
        aligned = phase + ((x0 - phase) // step) * step
        if aligned < x0:
            aligned += step
        return aligned

    def paintEvent(self, event: QPaintEvent):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        lane_h = self._lane_height          # 真实轨道高度，不含 TRACK_GAP
        w      = self.width()
        rect   = event.rect()
        x0     = max(0, rect.left())
        x1     = max(x0, min(w, rect.right() + 1))

        # ── 背景色：只填 lane_h，TRACK_GAP 区域留透明让容器分割线透出 ──
        row_idx = self._zebra_row_index()
        bg = QColor(_th.BG_EDITOR) if row_idx % 2 == 0 else QColor(_th.BG_BOX)  # bg_editor/bg_box 斑马纹
        p.fillRect(x0, 0, x1 - x0, lane_h, bg)

        # ── 文件夹轨道：斜线禁用标记 ─────────────────────────────────────
        if getattr(self._track, "is_folder", False):
            # 半透明深色遮罩
            p.fillRect(x0, 0, x1 - x0, lane_h, QColor(0, 0, 0, 80))
            # 斜线填充（间距 18px，45°）——只画曝光区覆盖的斜线，相位对齐 -lane_h 保持逐像素一致
            hatch_pen = QPen(QColor(80, 80, 80, 120), 1)
            p.setPen(hatch_pen)
            step = 18
            i = self._phase_aligned_start(x0 - lane_h, step, -lane_h)  # 斜线跨 lane_h 宽：左侧起点前推 lane_h
            i_end = min(w, x1)
            while i < i_end:
                p.drawLine(i, 0, i + lane_h, lane_h)
                i += step
            # 禁用提示文字
            hint_col = QColor(_th.TEXT_DISABLED)
            hint_col.setAlpha(200)
            p.setPen(hint_col)
            p.setFont(_font_ui(11))
            p.drawText(
                0, 0, w, lane_h,
                Qt.AlignmentFlag.AlignCenter,
                "文件夹轨道 — 不可放置 Clip",
            )
            p.end()
            return

        # ── 普通轨道：时间刻度虚线（只画曝光区覆盖的竖线，相位对齐 0）──────
        if self._px_per_sec > 0:
            p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1, Qt.PenStyle.DotLine))  # 秒点线（与容器网格同 alpha）
            for x in second_grid_positions(self._px_per_sec, x0, x1):
                p.drawLine(x, 0, x, lane_h)

        # ── 框选橡皮筋 ───────────────────────────────────────────────────
        if self._rubberband_rect:
            p.setPen(QPen(QColor("#4772B3"), 1, Qt.PenStyle.DashLine))  # select_blue（与 clip_area.py 同步）
            p.setBrush(QBrush(QColor(71, 114, 179, 40)))
            p.drawRect(self._rubberband_rect)

        # ── 底部分割线（仅 lane_h 处，不延伸到 TRACK_GAP）───────────────
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))  # separator
        p.drawLine(0, lane_h - 1, w, lane_h - 1)
        p.end()

    @property
    def track(self) -> Track:
        return self._track

    @property
    def selected_ids(self) -> set[str]:
        return self._selected_ids
