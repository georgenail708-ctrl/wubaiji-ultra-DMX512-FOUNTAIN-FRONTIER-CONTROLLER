"""
Qascade V1.0 — Timeline Widget  [FIXED v9]
修复项（相对 v8）：
  1. 删除内部 TransportBar
  2. TimelineWidget 只负责 ruler_row + content_row + audio_row
  3. v9: 跨轨拖动坐标转换为内容区本地坐标
"""
from __future__ import annotations

import math
import uuid
import time
import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMessageBox, QInputDialog, QDialog,
)
from ...core.models import MOTOR_FORCE_PROFILES, Track, normalize_group_pauses
from .clip_area import ClipArea
from .clip_view import apply_pen_tool_gate, set_tool_mode, ToolMode
from .timeline_controller import TimelineController
from . import timeline_group_gesture as _group_edit
from .clip_smoothing_ops import apply_smoothing
from ...engine.clip_smoothing import apply_clip_smoothing as _smooth_clip_frames
from ..main_icon_library import load_tabler_icon, make_main_icon
from .timeline_ruler import TimelineRuler
from .timeline_zoom import clamp_zoom, px_per_sec_for_span
from .timeline_operating_preferences import bind_timeline_zoom
from .track_list import TrackList
from .audio_mod_lane import AudioModLaneView, make_audio_mod_track
from .audio_ref_track import AudioRefTrack
from .audio_mod_track import AudioModTrack
from .timeline_perf import (adjacent_index, audio_ref_end as _audio_ref_end,
                            duplicate_many, timeline_gesture_snapshot, wire_track_moved,
                            invalidate_scoped_snapshot_cache,
                            schedule_scoped_snapshot_prime)
from typing import Optional

try:
    from ...core.project_manager import DOMAIN_TIMELINE, ProjectManager as _PM
except Exception:
    _PM = None  # type: ignore
    DOMAIN_TIMELINE = frozenset()  # type: ignore

TOOL_SELECT = "select"
TOOL_RAZOR  = "razor"
TOOL_PEN    = "pen"    # R179 画笔：调制轨上拖动绘制调制 clip（用户规格 ②）

_TOOL_MODES = {
    TOOL_SELECT: ToolMode.SELECT,
    TOOL_RAZOR: ToolMode.RAZOR,
    TOOL_PEN: ToolMode.PEN,
}
TRACK_LIST_WIDTH = 204
DEFAULT_TIMELINE_SECONDS = 600.0
TIMELINE_TAIL_SECONDS = 120.0

#: 相邻判定的**间隔上限**（秒）。见 `_pair_is_transitionable` 的说明（R134）。
_TRANSITION_PAIR_GAP_SEC = 2.0

#: R172b：Studio 自动应用窗口内置 True → `_resolve_context_clip_ids` 不向多选扩散
#: （sheet 编译字段只作用于绑定 clip；菜单/信号路径保持既有多选语义）。
#: 仅由 `studio_apply` 以 with 模式（try/finally）置位，勿在别处改写。
_STUDIO_SINGLE_CLIP = False


def _connect_if_hasattr(src, attr: str, dst_slot) -> None:
    """src 上存在信号 attr 才连接到 dst_slot（老 ClipArea 缺该信号时安全跳过）。"""
    if hasattr(src, attr):
        getattr(src, attr).connect(dst_slot)


class _BakeCacheBar(QWidget):
    """R178 时间线**后台自动烘焙缓存条**：clip_area 顶部 3px 细条。

    ★ R182（用户定案）：烘焙进度条统一收敛到**主窗状态栏内嵌条**
      （`ui/autobake_progress.SlimStatusProgress`）——本控件不再由烘焙信号
      驱动、不再参与布局；类保留仅为 R178 回归测试（直接实例化冒烟）与
      老接口兼容，`TimelineWidget.set_bake_progress` 已改为转发到状态栏条。

    画 `baked_until/total` 进度（琥珀=烘焙中，绿=已烘焙段），照 clip_area
    播放头 overlay 先例：鼠标事件全穿透、不参与布局交互，只吃外部 set。
    零进度隐藏；烘焙完成后满条保持（缓存就绪可视化）；失效清零隐藏。
    """

    HEIGHT_PX = 3
    _COLOR_BAKING = "#B29700"     # 警告琥珀（烘焙进行中），与文件夹紫系同明度带
    _COLOR_DONE = "#56B65B"       # 完成绿（与新增轨道 glyph 同色）

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._fill_frac: float = 0.0
        self._baking: bool = False
        self.setFixedHeight(self.HEIGHT_PX)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_progress(self, frac: float, baking: bool) -> None:
        frac = max(0.0, min(1.0, float(frac)))
        if frac <= 0.0:
            self.clear_progress()
            return
        changed = (abs(frac - self._fill_frac) > 1e-4) or (bool(baking) != self._baking)
        self._fill_frac = frac
        self._baking = bool(baking)
        self.setVisible(True)
        if changed or self._baking:
            self.update()

    def clear_progress(self) -> None:
        self._fill_frac = 0.0
        self._baking = False
        self.hide()
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255, 14))   # 凹槽底
        if self._fill_frac > 0.0:
            w = max(1, int(round(self.width() * self._fill_frac)))
            color = QColor(self._COLOR_BAKING if self._baking else self._COLOR_DONE)
            p.fillRect(0, 0, w, self.height(), color)
        p.end()


class TimelineWidget(QWidget):
    """
    时间线核心区域组件（不含 TransportBar）。

    布局：
      ┌──────────────────────────────────────────┐
      │ [_track_list_header 32px] │ [Ruler 32px] │  ← ruler_row
      ├───────────────────────────┼──────────────┤
      │ [TrackList]               │ [ClipArea]   │  ← content_row
      ├───────────────────────────┼──────────────┤
      │ [音频控制区 204px]         │ [波形显示]   │  ← audio_row（56px）
      └──────────────────────────────────────────┘
    """

    processor_requested = pyqtSignal(object)
    clip_fixture_group_load_requested = pyqtSignal(object)
    track_selected      = pyqtSignal(str)
    clip_selected       = pyqtSignal(str)  # clip_id，clip 被选中时发出
    # ★ R172：clip 右击菜单「Clip 附加处理…」→ 打开 Studio 卡片画布的**公开出口**。
    #   链路 ClipItem→ClipView→_TrackContainer→ClipArea 逐层转发到本组件，
    #   main_window 的最终连接由 Studio 波次接入（R22 教训：断一层=静默失效）。
    clip_processing_studio_requested = pyqtSignal(str)  # clip_id

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self._session      = session
        self._current_tool = TOOL_SELECT
        self._selected_track_id = ""

        self.ctrl = TimelineController(session, parent=self)

        self._setup_ui()
        # R179 调制轨升级挂点（_setup_ui/_connect_signals 行数基线冻结，另开方法）
        self._setup_timeline_tool_extensions()
        self._setup_snap_toggle()   # R183 T1：吸附档位按钮（工具区末尾）
        self._setup_marker_support()   # R183 T2/T3/T6：标记条 + BPM 网格 + 循环区
        self._wire_mod_extensions()
        self._connect_signals()
        self._reload_tracks()
        bind_timeline_zoom(self)

    # ── R179 工具栏扩展：画笔按钮（与选择/剥刀平行）+ 添加调制轨按钮 ──────
    def _setup_timeline_tool_extensions(self):
        self._btn_tool_pen = QPushButton()
        self._btn_tool_pen.setIcon(load_tabler_icon("pencil", 16))
        self._btn_tool_pen.setToolTip("画笔工具 — 在音频调制轨上拖动绘制调制区段")
        self._btn_tool_pen.setFixedSize(28, 24)
        self._btn_tool_pen.setStyleSheet(getattr(self, "_tool_btn_ss_idle", ""))
        self._btn_tool_pen.clicked.connect(lambda: self.set_tool(TOOL_PEN))
        bar = self._btn_tool_razor.parentWidget()
        try:
            bar.setFixedWidth(bar.width() + 31)
            bar.layout().addWidget(self._btn_tool_pen)
        except Exception:
            pass

        self._btn_add_audio_mod = QPushButton()
        self._btn_add_audio_mod.setToolTip("新增音频调制轨道（每节目一条，可拖动排序）")
        self._btn_add_audio_mod.setFixedSize(24, 24)
        self._btn_add_audio_mod.setStyleSheet("""
            QPushButton {
                background:#303030; border:1px solid #3D3D3D; border-radius:4px;
                min-height:0px; padding:0px;
            }
            QPushButton:hover { background:#545454; border-color:#5C5C5C; }
        """)
        _am = load_tabler_icon("wave-sine", 16, "#56B65B")
        if not _am.isNull():
            self._btn_add_audio_mod.setIcon(_am)
        self._btn_add_audio_mod.clicked.connect(self._on_add_audio_mod_track)
        try:
            hdr_lay = self._track_list_header.layout()
            hdr_lay.addSpacing(8)
            hdr_lay.addWidget(self._btn_add_audio_mod)
        except Exception:
            pass

    # ── R179 调制轨信号接线（旧档→自动补轨；双击→绑 scope 并开编辑页）────
    def _wire_mod_extensions(self):
        mod = self._audio_mod_track
        mod.legacy_state_needs_track.connect(self._ensure_mod_track_for_legacy_state)
        # R181：打开编辑页前把当前作用域（选中文件夹）接进调制状态（修死链）
        mod.open_editor_requested.connect(self._bind_mod_scope_from_selection)
        # R181：音频删除 → 调制轨复位（禁画幽灵区段；审计 B2）
        self._audio_track.audio_removed.connect(self._on_audio_ref_removed)

    def _on_audio_ref_removed(self):
        """音频参考轨删除 → 调制时长清零（画笔门禁随之关闭）。"""
        self._audio_mod_track.set_audio_duration(0.0)

    def _bind_mod_scope_from_selection(self, at_sec: float = 0.0) -> None:
        """左双击打开编辑页：选中文件夹范围 → 调制 scope（文件夹唯一主控）。

        M1：`at_sec`（双击秒位）本槽只管 scope 绑定不消费它——时间位由同一
        信号在 main_window 的 `_open_audio_mod_editor(mw, at_sec)` 槽沿链带进
        编辑页（两槽并行连接，互不拦截）。
        """
        folder = self._folder_scope_for_selection()
        if folder:
            self._audio_mod_track.set_scope_folder(folder)

    def _folder_scope_for_selection(self) -> str:
        """当前选择推导 scope 文件夹：选中文件夹本身，或选中调制轨所在文件夹。"""
        sel = (self._session.get_track(self._selected_track_id)
               if hasattr(self._session, "get_track") and self._selected_track_id
               else None)
        if sel is None:
            return ""
        if bool(getattr(sel, "is_folder", False)):
            return str(getattr(sel, "id", "") or "")
        if not bool(getattr(sel, "is_audio_mod", False)):
            return ""
        return self._mod_track_parent_folder(str(getattr(sel, "parent_id", "") or ""))

    def _mod_track_parent_folder(self, parent_id: str) -> str:
        """调制轨的上级文件夹 id（无/父级不是文件夹 → 空）。"""
        if not parent_id or not hasattr(self._session, "get_track"):
            return ""
        parent = self._session.get_track(parent_id)
        if parent is not None and bool(getattr(parent, "is_folder", False)):
            return str(getattr(parent, "id", "") or "")
        return ""

    def _setup_ui(self):
        from .timeline_workspace_ui import build_workspace
        build_workspace(self)

    # ── R183 时间线批 T1：吸附档位按钮（拖动/裁剪/切割共用同一档）────────
    def _setup_snap_toggle(self):
        """工具栏加"吸附"档位按钮（关/秒/帧/小节循环）；状态存 QSettings。

        挂在工具选择按钮区（`_btn_tool_select` 所在那一行的末尾）——该区是
        时间线唯一的工具栏，且宽度已为 72px 固定，故把按钮追加到其 layout 上
        并把固定宽放宽 31px（每个按钮 28px + 3px 间距，与画笔按钮同款做法）。
        """
        from .timeline_snap import (SNAP_LABELS, SNAP_MODES, load_snap_mode,
                                    next_snap_mode, save_snap_mode)
        self._snap_mode = load_snap_mode()
        self._btn_snap = QPushButton()
        self._btn_snap.setFixedSize(28, 24)
        self._btn_snap.setStyleSheet(getattr(self, "_tool_btn_ss_idle", ""))
        self._btn_snap.clicked.connect(self._on_snap_cycle)
        self._sync_snap_button()
        try:
            bar = self._btn_tool_razor.parentWidget()
            bar.setFixedWidth(bar.width() + 31)
            bar.layout().addWidget(self._btn_snap)
        except Exception:
            pass
        self._SNAP_MODES = SNAP_MODES          # 供测试/外部查询档位顺序
        self._SNAP_LABELS = SNAP_LABELS
        self._next_snap_mode = next_snap_mode
        self._save_snap_mode = save_snap_mode

    def _sync_snap_button(self):
        """按钮视觉 = 当前档（关档=空闲灰、开档=激活蓝；图标 magnet/magnet-off）。"""
        from .timeline_snap import SNAP_OFF
        mode = str(getattr(self, "_snap_mode", "") or "")
        label = self._SNAP_LABELS.get(mode, mode) if hasattr(self, "_SNAP_LABELS") else mode
        self._btn_snap.setToolTip(
            f"{label} — 点击切换（秒/帧/小节/关）；拖动时按 Alt 临时旁路")
        self._btn_snap.setStyleSheet(
            self._tool_btn_ss_active if mode != SNAP_OFF else self._tool_btn_ss_idle)
        try:
            from ..main_icon_library import load_tabler_icon
            ico = load_tabler_icon("magnet" if mode != SNAP_OFF else "magnet-off", 16)
            if not ico.isNull():
                self._btn_snap.setIcon(ico)
        except Exception:
            pass

    def _on_snap_cycle(self):
        """点击：档位循环（关→秒→帧→小节→关）并持久化。"""
        from .timeline_snap import next_snap_mode, save_snap_mode
        self._snap_mode = next_snap_mode(self._snap_mode)
        save_snap_mode(self._snap_mode)
        self._sync_snap_button()

    # ── R182 兼容壳：烘焙进度统一走主窗状态栏内嵌条 ──────────────────────
    # R181/R182 定案：进度可视化只有一处 —— 状态栏「网络」右侧的
    # SlimStatusProgress（ui/autobake_progress）。以下三个方法保留同名
    # （R178 接口契约/测试依赖），转发到主窗注入的 `_bake_status_bar`；
    # 无转发目标（单测直连本类）时静默 no-op，不再创建时间线缓存条控件。
    def set_bake_progress(self, frac: float) -> None:
        """兼容壳：把烘焙进度（0..1）转发到状态栏内嵌条。"""
        bar = getattr(self, "_bake_status_bar", None)
        if bar is None:
            return
        try:
            bar.set_progress(float(frac) * 100.0)
        except Exception:
            pass

    def set_bake_baker(self, baker) -> None:
        """兼容壳：R182 起着色由状态栏条自理，仅保留引用（防外部断开报错）。"""
        self._bake_baker_ref = baker

    def clear_bake_progress(self) -> None:
        """兼容壳：失效 → 状态栏条淡出（无转发目标时静默）。"""
        bar = getattr(self, "_bake_status_bar", None)
        if bar is None:
            return
        try:
            bar.show_idle()
        except Exception:
            pass

    def _connect_signals(self):
        ctrl = self.ctrl
        ca   = self._clip_area
        tl   = self._track_list
        at   = self._audio_track

        ctrl.tracks_changed.connect(self._reload_tracks)
        ctrl.clip_changed.connect(ca.refresh_clip)
        ctrl.clip_changed.connect(lambda _cid: self._sync_timeline_duration())
        ctrl.zoom_changed.connect(self._on_zoom_changed)
        # B3: GIL fleet — playhead redraw moved to Tier 2 (every 4th frame ~10Hz)
        ctrl.playback_tick_tier2.connect(self._on_playback_tick)
        ctrl.playback_started.connect(lambda pos: self.audio_play(pos))
        ctrl.playback_stopped.connect(lambda pos: self.audio_stop())
        # ★ 新增：音频删除时停止播放并发出通知
        at.audio_removed.connect(self.audio_stop)
        at.audio_loaded.connect(self._on_audio_duration_changed)
        # ★ 音频 clip 编辑（位置/淡入/淡出）提交后刷新时间线总时长
        at.audio_clip_edited.connect(lambda: self._sync_timeline_duration())

        ca.clip_move_commit.connect(ctrl.move_clip)
        ca.clip_deleted.connect(self._on_clip_deleted_request)
        ca.clip_duplicated.connect(self._on_clip_duplicated_request)
        ca.clip_cut.connect(self._on_clip_cut_request)
        ca.clip_copy.connect(self._on_clip_copy_request)
        ca.clip_resize_commit.connect(self._on_clip_resize_commit_multi)
        ca.clip_fade_commit.connect(self._on_clip_fade_commit)
        ca.clip_seq_delay_commit.connect(self._on_clip_seq_delay_commit)
        ca.clip_dir_fade_set.connect(self._on_clip_dir_fade_set)
        if hasattr(ca, "clip_spatial_scope_set"):
            ca.clip_spatial_scope_set.connect(self._on_clip_spatial_scope_set)
        if hasattr(ca, "clip_skip_base_order_set"):
            ca.clip_skip_base_order_set.connect(self._on_clip_skip_base_order_set)
        if hasattr(ca, "clip_transition_strategy_set"):
            ca.clip_transition_strategy_set.connect(self._on_clip_transition_strategy_set)
        if hasattr(ca, "clip_dir_edge_set"):
            ca.clip_dir_edge_set.connect(self._on_clip_dir_edge_set)
        ca.clip_auto_smooth.connect(self._on_clip_auto_smooth)
        if hasattr(ca, "clip_motor_force"):
            ca.clip_motor_force.connect(self._on_clip_motor_force)
        # 转发 clip 选中信号给 MainWindow（R172：原面板消费者已撤，信号保留）
        if hasattr(ca, "_container") and hasattr(ca._container, "clip_selected"):
            ca._container.clip_selected.connect(
                lambda cid, shift, ctrl: self.clip_selected.emit(cid))
        elif hasattr(ca, "clip_selected"):
            ca.clip_selected.connect(
                lambda cid, shift, ctrl: self.clip_selected.emit(cid))
        ca.clip_light_tracking.connect(self._on_clip_light_tracking)
        ca.clip_hold_toggled.connect(self._on_clip_hold_toggled)
        if hasattr(ca, "clip_hold_mode_changed"):
            ca.clip_hold_mode_changed.connect(self._on_clip_hold_mode_changed)
        if hasattr(ca, "clip_speed_changed"):
            ca.clip_speed_changed.connect(self._on_clip_speed_changed)
        if hasattr(ca, "clip_record_output_source_changed"):
            ca.clip_record_output_source_changed.connect(self._on_clip_record_output_source_changed)
        if hasattr(ca, "clip_motor_transition_phase_changed"):
            ca.clip_motor_transition_phase_changed.connect(self._on_clip_motor_transition_phase_changed)
        if hasattr(ca, "clip_precharge_changed"):
            ca.clip_precharge_changed.connect(self._on_clip_precharge_changed)
        _connect_if_hasattr(ca, "clip_open_processing_studio", self._on_clip_open_processing_studio)
        _connect_if_hasattr(ca, "clip_optimize_transitions", self._on_clip_optimize_transitions)
        ca.clip_to_processor.connect(self.processor_requested)
        if hasattr(ca, "clip_load_fixture_group"):
            ca.clip_load_fixture_group.connect(self.clip_fixture_group_load_requested)
        ca.clip_razor_cut.connect(ctrl.razor_cut_clip)
        # #20：ClipArea 滚轮缩放改发 zoom_at_requested（播放指针锚定）；
        # 无锚点 zoom_requested 已无发射者，保留信号定义兼容旧代码。
        if hasattr(ca, "zoom_at_requested"):
            ca.zoom_at_requested.connect(self._set_zoom_at)

        ca.cross_track_drag.connect(self._on_cross_track_drag)
        ca.cross_track_drag_end.connect(self._on_cross_track_drag_end)
        self._ruler.seek_requested.connect(ctrl.seek)
        self._ruler.zoom_requested.connect(ctrl.set_zoom)
        self._ruler.loop_range_changed.connect(ctrl.set_loop_range)
        if hasattr(self._ruler, "zoom_at_requested"):
            self._ruler.zoom_at_requested.connect(self._set_zoom_at)
        ca.scroll_x_changed.connect(self._ruler.set_scroll_x)
        ca.scroll_x_changed.connect(at.set_scroll_x)
        ca.scroll_y_changed.connect(tl.sync_scroll)
        tl.scroll_y_changed.connect(ca.sync_scroll_y)
        if hasattr(tl, "wheel_requested"):
            tl.wheel_requested.connect(ca._handle_wheel)

        tl.mute_toggled.connect(ctrl.set_track_mute)
        tl.solo_toggled.connect(ctrl.set_track_solo)
        tl.track_renamed.connect(ctrl.rename_track)
        tl.track_deleted.connect(ctrl.delete_track)
        tl.track_deleted.connect(lambda tid: self._on_clip_deleted_invalidate_cache(tid))
        tl.color_changed.connect(ctrl.set_track_color)
        tl.add_track.connect(self._on_add_track)
        tl.add_folder.connect(self._on_add_folder)
        tl.folder_toggled.connect(self._on_folder_toggled)
        tl.assign_parent_requested.connect(self._on_assign_parent)
        if hasattr(tl, "track_height_changed"):
            tl.track_height_changed.connect(self._on_track_height_changed)
        if hasattr(tl, "reparent_track_requested"):
            tl.reparent_track_requested.connect(self._on_reparent_track)
        tl.track_selected.connect(self._on_track_selected_local)
        tl.track_selected.connect(self.track_selected)
        if hasattr(tl, "track_copy"):
            tl.track_copy.connect(self.ctrl.copy_track)
        wire_track_moved(tl, self._on_track_moved)

    def rebind_session(self, session):
        """重新绑定当前 Project 对象。

        打开/新建项目时 ProjectManager.project 会被替换；如果 TimelineWidget
        仍持有旧 session，录制生成的 clip、过滤器编辑和 PlaybackEngine 读取的
        项目就会分裂成两套轨道，表现为“未找到轨道”或“录到了但播放无反应”。
        """
        if session is None:
            return
        self._session = session
        if hasattr(self, "ctrl"):
            self.ctrl._session = session
        self._reload_tracks()
        # ★ R183 T2/T3/T6：换工程必须重绑标尺上下文（bpm/标记/循环都在工程里）——
        #   原 rebind 只重绑轨道，标尺永远停在上一个工程的网格与标记条上。
        self._setup_marker_support()

    def _reload_tracks(self):
        tracks = self._session.tracks
        self._track_list.refresh_tracks(tracks)
        visible_tracks = getattr(self._track_list, "_track_order", None)
        if visible_tracks is not None:
            id_map = {t.id: t for t in tracks}
            tracks_for_clip_area = [id_map[tid] for tid in visible_tracks if tid in id_map]
        else:
            tracks_for_clip_area = tracks
        self._clip_area.refresh_tracks(tracks_for_clip_area)
        self._bind_mod_lane_editors()   # R179：新行内画布接上状态真源
        self._clip_area.sync_scroll_y(self._track_list.get_scroll_y())
        self._sync_timeline_duration()
        self._workspace_context.refresh()
        # Warm the immutable undo payload after a project/track rebuild so the
        # next mouse gesture does not serialize every Clip on its first move.
        schedule_scoped_snapshot_prime(self._get_pm(), DOMAIN_TIMELINE)

    # ── ★ R183 T2/T3/T6：标记轨 + BPM 小节标尺 + 循环区（数据在 Project）─────
    def _setup_marker_support(self):
        """把工程侧数据接进标尺：标记条 + bpm 网格 + 循环区回填。

        在 `__init__`（首次）与 `rebind_session`（换工程）两条路径都调用 ——
        `rebind_session` 此前只重绑轨道，标尺的 bpm/标记/循环从不刷新。
        """
        self._ruler.set_marker_context(self._session, self.ctrl)
        self.sync_markers()
        self.sync_loop()
        self._sync_ruler_bpm()
        if not getattr(self, "_marker_signals_wired", False):
            self._marker_signals_wired = True   # 幂等：rebind 不重复连接
            try:
                self._ruler.markers_changed.connect(self._on_markers_changed)
                self._ruler.loop_committed.connect(self._on_loop_committed)
            except Exception:
                self._marker_signals_wired = False

    def _sync_ruler_bpm(self):
        """工程 bpm → 标尺（<=0/缺失 = 不画 BPM 网格；R183 T3）。"""
        try:
            self._ruler.set_bpm(float(getattr(self._session, "bpm", 0.0) or 0.0))
        except Exception:
            self._ruler.set_bpm(0.0)

    def sync_markers(self):
        """工程 → 标尺标记条（打开工程 / undo 回灌 / 编辑后统一走这里）。"""
        from .timeline_markers import load_markers
        try:
            self._ruler.set_markers(load_markers(self._session))
        except Exception:
            pass

    def sync_loop(self):
        """工程循环区 → 标尺 + 控制器（R183 T6 持久化回填）。

        ★ 这是"设计预留无消费方"的收口：`loop_range_changed` 此前只写内存、
        关掉工程即丢；现在装载时回填，落位时写回工程。

        ★★ 审计⑦B P1-6 修复（跨工程循环区残留）：新工程无 loop 存档时原实现
        直接 `return` —— 旧工程的循环**留在标尺与 controller 上**，于是新工程
        一播到旧工程的 loop_end 就被拽回旧 loop_start（实测：B 播到 4.5s 被拉回
        A 的 1.0s）。"没有 loop"必须**显式清零**，不能什么都不做。
        """
        from .timeline_markers import load_loop
        try:
            start, end, enabled = load_loop(self._session)
        except Exception:
            start, end, enabled = 0.0, 0.0, False
        if end <= start:
            # 该工程没有（有效的）循环区 → 清零，绝不继承上一个工程的值
            try:
                self._ruler.set_loop(0.0, 0.0, False)
                self.ctrl.set_loop_range(0.0, 0.0, enabled=False)
            except Exception:
                pass
            return
        try:
            self._ruler.set_loop(start, end, enabled)
            self.ctrl.set_loop_range(start, end, enabled=enabled)
        except Exception:
            pass

    def _on_markers_changed(self):
        """标记条被改（增删改名改色/移动）→ 标脏（持久化由工程保存统一做）。"""
        self._mark_project_dirty()

    def _on_loop_committed(self, start: float, end: float, enabled: bool):
        """循环区手势落位 → 写工程（区间 + 开关一起）+ 标脏。

        ★ `snapshot=False`：撤销条目已在**手势起点**（ruler `_loop_snapshot`）压好 ——
        落位时再压会拍到"已经改过的"工程状态，撤销就回不到拖之前（R57 同款纪律：
        快照必须严格先于首个变异）。
        """
        from .timeline_markers import set_loop
        try:
            if set_loop(self._session, self.ctrl, start, end, enabled, snapshot=False):
                self._mark_project_dirty()
        except Exception:
            pass

    def _content_end_time(self) -> float:
        """live 轨道里 clip 尾端的最大值（与 audio/playhead 取大）。

        R141：本函数是残留的 O(N)/帧（`clip_changed` 每帧一发），但实测 400 clip
        仅 0.057ms（对比手势快照 740ms）→ 刻意保留原逐字实现：微优化在 N≥1600
        反而更慢，不值得承担取值语义风险。音频段抽到 `_audio_ref_end` 是为让
        本函数留在架构基线的行数/CC 内（门禁只许缩小）。
        """
        end_time = 0.0
        for track in list(getattr(self._session, "tracks", []) or []):
            for clip in list(getattr(track, "clips", []) or []):
                try:
                    start = float(getattr(clip, "start_time", 0.0) or 0.0)
                    dur = float(getattr(clip, "duration", 0.0) or 0.0)
                except Exception:
                    continue
                end_time = max(end_time, start + max(0.0, dur))
        audio_end = _audio_ref_end(self._audio_track)
        try:
            play_pos = float(getattr(self.ctrl, "_play_pos", 0.0) or 0.0)
        except Exception:
            play_pos = 0.0
        # ★ 2026-08-12 修复: play_pos 量化到 5s 粒度。播放中 _play_pos 每 tick
        #   (~23ms) 增长 → 直接参与会让 duration 每 tick 变化 → 时间线反复
        #   resize/重绘(用户实测「反复重置绘制残缺时间线」)。量化后每 5s 才
        #   扩充一次, 尾部跟随仍有效。
        play_pos = math.ceil(play_pos / 5.0) * 5.0
        return max(end_time, audio_end, play_pos)

    def _sync_timeline_duration(self, force: bool = False):
        # ★ R173 性能（编辑信号风暴，见 perf-hotspot-survey #11）：一次编辑常
        #   同时发 clip_changed + tracks_changed，二者各自触发一次本方法
        #   （前者经 lambda，后者经 _reload_tracks 尾部），导致 _content_end_time
        #   的全量扫描在同一编辑动作里重复跑。非强制调用改走单帧合并——同一
        #   事件循环轮次内的多次请求只真正扫描一次；force=True（播放 tick 扩容,
        #   见 2026-08-12 "残缺时间线" 修复）保持原同步立即路径完全不变，绝不
        #   经过合并，避免重新引入该已修复回归。
        if force:
            self._sync_timeline_duration_now(force=True)
            return
        if getattr(self, "_duration_sync_pending", False):
            return
        self._duration_sync_pending = True
        QTimer.singleShot(0, self._flush_duration_sync)

    def _flush_duration_sync(self):
        self._duration_sync_pending = False
        self._sync_timeline_duration_now(force=False)

    def _sync_timeline_duration_now(self, force: bool):
        # R141：只算一次（原先同一次调用里跑两遍全量扫描）。中间只调
        # ruler.set_duration（只写自身 _duration + update），不碰 clip/audio/
        # playhead，故复用同一取值与逐位重算一致。
        content_end = self._content_end_time()
        duration = max(DEFAULT_TIMELINE_SECONDS, content_end + TIMELINE_TAIL_SECONDS)
        old_dur = float(getattr(self._ruler, "_duration", DEFAULT_TIMELINE_SECONDS) or 0.0)
        # ★ 2026-08-12 修复: ruler 在 set_playhead 内已自扩到同一量化值 → 普通
        #   调用方(音频编辑/clip 变更)靠防抖防抖动; 播放 tick 路径已确认需要
        #   扩充(force=True), 必须绕过防抖让 clip 容器真实扩容, 否则残缺。
        if hasattr(self._clip_area, "set_audio_duration"):
            try:
                if force or getattr(self._clip_area, "_audio_duration", None) != max(0.0, content_end):
                    self._clip_area.set_audio_duration(max(0.0, content_end))
            except Exception:
                pass
        if not force and abs(duration - old_dur) < 0.5:
            return
        self._ruler.set_duration(duration)

    def _on_zoom_changed(self, px_per_sec: float):
        self._ruler.set_px_per_sec(px_per_sec)
        self._clip_area.set_px_per_sec(px_per_sec)
        self._audio_track.set_px_per_sec(px_per_sec)
        self._audio_mod_track.set_px_per_sec(px_per_sec)

    def _set_zoom_at(self, px_per_sec: float, anchor_time: float, viewport_x: float):
        old_zoom = max(1.0, float(getattr(self.ctrl, "px_per_sec", 100.0) or 100.0))
        # ★ R183 T4：上下限统一到 timeline_zoom（修 500 vs 2000 自相矛盾 ——
        #   标尺/滚轮一路放大到 2000，到此被静默折断回 500 → 锚点换算被跳过
        #   （`abs(new-old)<1e-6` 提前 return）→ 播放指针在屏幕上滑走 = 锚点漂移）。
        new_zoom = clamp_zoom(px_per_sec)
        anchor_time = max(0.0, float(anchor_time))
        # 可负：指针可能被滚动到视口左侧外（scroll > 指针px），钳 0 会破坏锚点数学
        viewport_x = float(viewport_x)
        if abs(new_zoom - old_zoom) < 1e-6:
            return
        self.ctrl.set_zoom(new_zoom)
        try:
            target_x = int(round(anchor_time * self.ctrl.px_per_sec - viewport_x))
            self._clip_area.set_scroll_x(target_x)
        except Exception:
            pass

    def set_zoom_centered(self, px_per_sec: float):
        """以当前播放指针为中心缩放（工具栏/快捷键 +/- 缩放入口）。

        与 _set_zoom_at 同款锚点数学：指针在屏幕上保持不动。
        """
        px = clamp_zoom(px_per_sec)   # R183 T4：与 _set_zoom_at 同一上下限真源
        anchor_time = float(getattr(self._clip_area, "_position", 0.0) or 0.0)
        play_x = anchor_time * self.ctrl.px_per_sec - float(self._clip_area.scroll_bar_h.value())
        if abs(px - self.ctrl.px_per_sec) < 1e-6:
            return
        self.ctrl.set_zoom(px)
        target_x = int(round(anchor_time * self.ctrl.px_per_sec - play_x))
        self._clip_area.set_scroll_x(target_x)

    def _on_playback_tick(self, pos: float):
        # ★ 2026-08-12 修复: 必须在 set_playhead 之前快照 old_dur —— ruler 的
        #   set_position 会自行扩容(pos+120), 若先 set_playhead 再判断, 条件
        #   永远 False → _sync_timeline_duration 从不触发 → 标尺扩容而 clip
        #   容器从不扩容 = 残缺时间线(用户实测)。快照后条件才真实成立。
        old_dur = float(getattr(self._ruler, "_duration", DEFAULT_TIMELINE_SECONDS) or 0.0)
        self._ruler.set_playhead(pos)
        self._clip_area.set_playhead(pos)
        self._audio_track.set_playhead(pos)
        if float(pos or 0.0) + TIMELINE_TAIL_SECONDS > old_dur:
            self._sync_timeline_duration(force=True)

    def _get_pm(self):
        """获取 ProjectManager 单例（如可用），用于 _snapshot 支持撤销。"""
        if _PM is not None and hasattr(_PM, 'instance'):
            try:
                return _PM.instance()
            except Exception:
                pass
        return None

    def _snapshot_for_undo(self):
        """改 clip 属性前保存 undo 快照（R49 轻快照；R141 只存域内字段）。

        策略见 `timeline_perf.timeline_gesture_snapshot`（domain 内切片 →
        `_snapshot_scoped` → `_snapshot`）。结构级操作走 main_window 全量快照。
        """
        timeline_gesture_snapshot(self._get_pm(), DOMAIN_TIMELINE)

    def _snapshot_for_undo_scoped(self):
        """手势起点（clip_view 按下/首次 move）的 pre-state 快照。

        ★ 修复 R49 缺陷：clip_view._snapshot_pre_drag 用
        hasattr(root, "_snapshot_for_undo_scoped") 探测，而该方法只在
        main_window 上，TimelineWidget 没有 → 手势起点快照从未生效。
        与 `_snapshot_for_undo` 同策略（拖拽/fade 手势不改面板域）。
        """
        timeline_gesture_snapshot(self._get_pm(), DOMAIN_TIMELINE)

    def _mark_project_dirty(self):
        pm = self._get_pm()
        if pm is not None:
            invalidate_scoped_snapshot_cache(pm)
            try:
                if hasattr(pm, "_mark_dirty"):
                    pm._mark_dirty()
                elif hasattr(pm, "set_dirty"):
                    pm.set_dirty()
            except Exception:
                pass
        parent = self.parent()
        while parent is not None:
            if hasattr(parent, "mark_dirty"):
                try:
                    parent.mark_dirty()
                except Exception:
                    pass
                break
            parent = parent.parent() if hasattr(parent, "parent") else None
        schedule_scoped_snapshot_prime(pm, DOMAIN_TIMELINE)

    def _iter_clip_views(self):
        container = getattr(self._clip_area, "_container", None)
        return list(getattr(container, "_views", []) or [])

    def _selected_clip_ids(self) -> list[str]:
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for view in self._iter_clip_views():
            selected_ids = set(getattr(view, "selected_ids", set()) or set())
            if not selected_ids:
                continue
            track = getattr(view, "track", None)
            ordered_in_view: list[str] = []
            if track is not None:
                for c in getattr(track, "clips", []) or []:
                    if c.id in selected_ids:
                        ordered_in_view.append(c.id)
            for cid in ordered_in_view:
                if cid not in seen:
                    seen.add(cid)
                    ordered_ids.append(cid)
            for cid in selected_ids:
                if cid not in seen:
                    seen.add(cid)
                    ordered_ids.append(cid)
        return ordered_ids

    def _resolve_context_clip_ids(self, clip_id: str) -> list[str]:
        if _STUDIO_SINGLE_CLIP:
            # R172b：Studio 编译 fields 只作用于绑定 clip，不向多选扩散
            # （开关由 studio_apply 在槽调用窗口内 with 置位/恢复）
            return [clip_id]
        selected = self._selected_clip_ids()
        if clip_id in selected and len(selected) > 1:
            return selected
        return [clip_id]

    def _invalidate_engine_cache(self, clip_id: str):
        """通知 PlaybackEngine 清除指定 clip 的缓存，避免属性修改后残留效果叠加。"""
        pm = self._get_pm()
        engine = None
        if pm is not None:
            engine = getattr(pm, '_engine', None) or getattr(pm, 'engine', None)
        # 也尝试从父级 MainWindow 获取
        if engine is None:
            p = self.parent()
            while p is not None:
                eng = getattr(p, '_engine', None)
                if eng is not None and hasattr(eng, 'invalidate_clip_cache'):
                    engine = eng
                    break
                p = p.parent() if hasattr(p, 'parent') else None
        if engine is not None and hasattr(engine, 'invalidate_clip_cache'):
            try:
                engine.invalidate_clip_cache(clip_id)
            except Exception:
                pass
            if hasattr(engine, 'precompile_clip'):
                try:
                    track, clip = self.ctrl._find_track_and_clip(clip_id)
                except Exception:
                    track, clip = None, None
                if clip is not None:
                    # R49 性能：预编译异步单发（去重合并）。同步版 218ms/次
                    # 是"拖完卡死"的第二元凶；0ms singleShot 在当前事件返回后
                    # 立即执行，早于任何播放 tick，行为无差。
                    self._schedule_precompile(engine, clip, track, pm)

    def _schedule_precompile(self, engine, clip, track, pm):
        """R57：precompile 合并节流。

        旧实现每 clip 一个 0ms singleShot——批量应用 N clip 时事件循环
        连续执行 N 次重预编译（R50 实测单次 218ms），松手/应用后主线程
        连环卡。现改为 120ms 单发定时器统一冲洗全部待编译 clip；播放中
        保持原 0ms 立即单发（gen 线程下一帧就要用，延迟会转嫁为输出卡帧）。
        """
        pending = getattr(self, "_precompile_pending", None)
        if pending is None:
            pending = {}
            self._precompile_pending = pending
        key = str(getattr(clip, "id", ""))
        pending[key] = (engine, clip, track, pm)
        playing = False
        try:
            playing = bool(self.ctrl.is_playing)
        except Exception:
            playing = False
        if playing:
            self._flush_precompile_pending()
            return
        timer = getattr(self, "_precompile_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(120)
            timer.timeout.connect(self._flush_precompile_pending)
            self._precompile_timer = timer
        timer.start()

    def _flush_precompile_pending(self):
        pending = getattr(self, "_precompile_pending", None) or {}
        self._precompile_pending = {}
        for key, (engine, clip, track, pm) in pending.items():
            try:
                proj = (getattr(pm, 'project', None) or getattr(pm, '_project', None)) if pm is not None else None
                engine.precompile_clip(clip, track=track, proj=proj)
            except Exception:
                pass

    def _on_clip_deleted_invalidate_cache(self, clip_id: str):
        """clip 被删除后立即清空引擎缓存和 timeline 层，避免停止状态下旧帧数据持续输出。"""
        pm = self._get_pm()
        engine = None
        if pm is not None:
            engine = getattr(pm, '_engine', None) or getattr(pm, 'engine', None)
        if engine is None:
            p = self.parent()
            while p is not None:
                eng = getattr(p, '_engine', None)
                if eng is not None and hasattr(eng, 'invalidate_all_caches'):
                    engine = eng
                    break
                p = p.parent() if hasattr(p, 'parent') else None
        if engine is not None and hasattr(engine, 'invalidate_all_caches'):
            try:
                engine.invalidate_all_caches()
            except Exception:
                pass

    def _on_clip_deleted_request(self, clip_id: str):
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        if len(target_ids) == 1:
            self.ctrl.delete_clip(target_ids[0])
            self._on_clip_deleted_invalidate_cache(target_ids[0])
            return
        self.ctrl._snapshot_for_undo()
        for cid in target_ids:
            self.ctrl._remove_clip_globally(cid)
        self._on_clip_deleted_invalidate_cache(target_ids[0])
        self.ctrl.tracks_changed.emit()

    def _on_clip_duplicated_request(self, clip_id: str):
        """重复片段（★ R141：多选只压 1 条撤销快照，见 `duplicate_many`）。"""
        created_ids = duplicate_many(
            self.ctrl, self._resolve_context_clip_ids(clip_id))
        if not created_ids:
            return
        for cid in created_ids:
            self._invalidate_engine_cache(cid)
        self._mark_project_dirty()

    def _on_clip_open_processing_studio(self, clip_id: str):
        """★ R172：clip 右击菜单「Clip 附加处理…」→ 打开 Studio 卡片画布。

        本层只 re-emit 公开信号 `clip_processing_studio_requested` ——
        main_window 的最终连接由 Studio 波次接入。R22 教训：历史上曾因
        两层转发缺失导致功能静默失效，故本链每一层都必须 connect 并可回归验证
        （见 tests/test_clip_menu_surgery_r172.py 信号链全通断言）。
        """
        self.clip_processing_studio_requested.emit(str(clip_id))

    def _on_clip_cut_request(self, clip_id: str):
        """剪切（★ R135：走多选解析 —— 选中多个时一次全剪切）。"""
        self.ctrl.cut_to_clipboard(self._resolve_context_clip_ids(clip_id) or [clip_id])

    def _on_clip_copy_request(self, clip_id: str):
        """复制（★ R135：走多选解析 —— 选中多个时一次全复制）。"""
        self.ctrl.copy_clip(self._resolve_context_clip_ids(clip_id) or [clip_id])

    def _on_clip_resize_commit_multi(self, clip_id: str, new_duration: float):
        """Finish frozen members through original resize and one gesture snapshot."""
        _group_edit.commit_resize(self, clip_id, new_duration)

    def _schedule_tracks_refresh(self, delay_ms: int = 120):
        """R49 性能：全量 tracks_changed 合并节流（120ms 单发）。

        fade/resize/delay 这类单 clip 微提交只需要精准单点刷新；
        全量重建（轨道头/视图重建）合并到一拍之后执行，拖完不卡。
        """
        if getattr(self, "_tracks_refresh_scheduled", False):
            return
        self._tracks_refresh_scheduled = True

        def _fire():
            self._tracks_refresh_scheduled = False
            if hasattr(self.ctrl, "tracks_changed"):
                self.ctrl.tracks_changed.emit()

        QTimer.singleShot(int(delay_ms), _fire)

    def _refresh_clips_targeted(self, target_ids, reset_waveform: bool = False):
        """单 clip/批量精准刷新（引擎缓存失效 + 视图局部更新），R57 批量合并。

        - 引擎缓存失效：批量走 invalidate_clip_cache_batch 单趟扫描
          （旧逐 clip 全表扫描 ×N，停播态大批量应用时秒级）；
        - reset_waveform：帧数据被原地重写（如平滑应用）时同步清 item 波形条
          缓存（其 key 只含形状，数据变化不自动失效）。
        """
        ca = getattr(self, "_clip_area", None)
        ids = [cid for cid in target_ids if cid]
        if not ids:
            return
        if len(ids) > 1:
            self._invalidate_engine_cache_batch(ids)
        for cid in ids:
            if len(ids) == 1:
                self._invalidate_engine_cache(cid)
            if ca is not None and hasattr(ca, "refresh_clip"):
                try:
                    ca.refresh_clip(cid, reset_waveform=reset_waveform)
                except Exception:
                    pass
            if hasattr(self.ctrl, "clip_changed"):
                self.ctrl.clip_changed.emit(cid)

    def _invalidate_engine_cache_batch(self, clip_ids):
        """批量引擎缓存失效（R57：单趟合并）。引擎不支持批量时回退逐个。"""
        engine = self._resolve_engine()
        if engine is None or not hasattr(engine, "invalidate_clip_cache_batch"):
            for cid in clip_ids:
                self._invalidate_engine_cache(cid)
            return
        try:
            engine.invalidate_clip_cache_batch(list(clip_ids))
        except Exception:
            pass
        # 批量失效后按现有单发路径补 precompile 预约
        pm = self._get_pm()
        for cid in clip_ids:
            track, clip = self.ctrl._find_track_and_clip(cid)
            if clip is not None:
                self._schedule_precompile(engine, clip, track, pm)

    def _resolve_engine(self):
        pm = self._get_pm()
        engine = None
        if pm is not None:
            engine = getattr(pm, '_engine', None) or getattr(pm, 'engine', None)
        if engine is None:
            p = self.parent()
            while p is not None:
                eng = getattr(p, '_engine', None)
                if eng is not None and hasattr(eng, 'invalidate_clip_cache'):
                    engine = eng
                    break
                p = p.parent() if hasattr(p, 'parent') else None
        return engine

    def _consume_pre_snap(self, clip_id: str) -> bool:
        """R49 性能：手势起点已拍轻快照则本次提交免快照（消费一次性标志）。

        R57 修正两处历史缺陷：
        - views 既可能在 ClipArea._views 也可能在 _container._views，两处都查；
        - `_pre_snapped` 标志在 ClipItem（clip_view._snapshot_pre_drag 设定），
          旧实现误读 ClipView 的同名属性 → 永远 False → 提交点误补拍拖后
          状态快照（undo 回不到拖前的根因之一）。
        """
        ca = getattr(self, "_clip_area", None)
        view_lists = []
        if ca is not None:
            if getattr(ca, "_views", None):
                view_lists.append(ca._views)
            cont = getattr(ca, "_container", None)
            if cont is not None and getattr(cont, "_views", None):
                view_lists.append(cont._views)
        item = None
        for views in view_lists:
            for v in views:
                item = getattr(v, "_items", {}).get(clip_id)
                if item is not None:
                    break
            if item is not None:
                break
        if item is not None and getattr(item, "_pre_snapped", False):
            item._pre_snapped = False
            return True
        return False

    def _on_clip_fade_commit(self, clip_id: str, fade_in: float, fade_out: float):
        """提交淡入/淡出时长（多选批量）。

        ★★ R135 修复（多选时"拖 c1 的淡入，c2/c3 的淡出被害"）：本槽对选中集里**每个**
          clip 无差别写**两个**值，而发射端把两侧都提交 → 拖 c1 淡入会把 c1 的 fade_out
          抄给所有选中项（实测 c2 的 0.25 → 1.5、c3 的 0.0 → 1.5），自定义淡入/淡出同理。
          修：**NaN 哨兵** = "本次不改这一侧"，只写非 NaN 的那一侧（另一侧保留各 clip 原值）。
          信号签名不变 → 所有既有连接与测试零改动；"整体淡入淡出/清除过渡"仍传两侧实数
          （语义本就是两边一起设）。
        """
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        pre = self._consume_pre_snap(clip_id)
        if not pre:
            self._snapshot_for_undo()   # 键盘/菜单路径兜底（保持旧语义）
        want_in = None if fade_in != fade_in else float(fade_in)     # NaN → None = 不改
        want_out = None if fade_out != fade_out else float(fade_out)
        if want_in is None and want_out is None:
            return
        changed = _write_edges(self.ctrl, target_ids, in_key="fade_in",
                               want_in=want_in, out_key="fade_out", want_out=want_out)
        if not changed:
            return
        # R49 性能：精准单点刷新 + 全量合并节流（原=每次全量 tracks_changed 重建）
        self._refresh_clips_targeted(target_ids)

    def _on_clip_seq_delay_commit(self, clip_id: str, seq_delay_in: float, seq_delay_out: float):
        """处理延迟时间（依次过渡扫掠时长）提交——顶部小加号手柄。

        ★ R135：与 `_on_clip_fade_commit` 同款 —— NaN 哨兵 = 本次不改这一侧
          （原先两侧一起写，多选拖 c1 的 seq_delay_in 会污染 c2/c3 的 seq_delay_out）。
        """
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        want_in = None if seq_delay_in != seq_delay_in else float(seq_delay_in)
        want_out = None if seq_delay_out != seq_delay_out else float(seq_delay_out)
        if want_in is None and want_out is None:
            return
        self._snapshot_for_undo()
        changed = _write_edges(self.ctrl, target_ids, in_key="seq_delay_in",
                               want_in=want_in, out_key="seq_delay_out", want_out=want_out)
        if not changed:
            return
        # R57 性能：精准单点刷新替代全量 tracks_changed 重建（属性级变更）
        self._refresh_clips_targeted(target_ids)

    def _clip_sequence_batch_count(self, clip, groups: int | None = None) -> int:
        if groups is None:
            groups = max(1, int(getattr(clip, "fade_groups", 1) or 1))
        groups = max(1, int(groups or 1))
        try:
            raw_batches = int(getattr(clip, "fade_group_sequence_batches", 0) or 0)
        except Exception:
            raw_batches = 0
        if raw_batches <= 0:
            return groups
        return max(1, min(groups, raw_batches))

    def _on_clip_dir_fade_set(self, clip_id: str, in_direction: str, out_direction: str, groups: int,
                              sequence_mode: str = "parallel", group_pause: float = 0.0,
                              sequence_batches: int = 0, sequence_pauses: list | None = None):
        """处理方向性分组淡入淡出设置（淡入/淡出方向分别设置）。"""
        import logging as _logging
        _log = _logging.getLogger(__name__)
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        sequence_mode = str(sequence_mode or "parallel").strip().lower()
        if sequence_mode not in ("parallel", "sequential"):
            sequence_mode = "parallel"
        try:
            group_pause = max(0.0, float(group_pause or 0.0))
        except Exception:
            group_pause = 0.0
        try:
            sequence_batches = max(0, int(sequence_batches or 0))
        except Exception:
            sequence_batches = 0
        self._snapshot_for_undo()
        changed = False
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                _log.warning("[DIR_FADE_SET] clip not found: %s", cid[:8])
                continue
            in_dir = in_direction or "none"
            out_dir = out_direction or "none"
            # ★ 2026-08-12 用户要求: 分组方向过渡「应用边」— edge=in/out 时只应用
            #   所选单边, 另一侧保留原值(跳过下面的自动补全); both=淡入淡出一致(默认)。
            edge = str(getattr(clip, "fade_direction_edge", "both") or "both").strip().lower()
            if edge not in ("in", "out"):
                edge = "both"
            if edge == "in":
                out_dir = str(getattr(clip, "fade_out_direction", "none") or "none")
            elif edge == "out":
                in_dir = str(getattr(clip, "fade_in_direction", "none") or "none")
            if edge == "both":
                if sequence_mode == "sequential":
                    if in_dir == "none" and out_dir != "none":
                        in_dir = out_dir
                    if in_dir == "none":
                        in_dir = "ltr"
                    if out_dir == "none":
                        out_dir = in_dir
                else:
                    if in_dir == "none" and out_dir != "none":
                        in_dir = out_dir
                    elif out_dir == "none" and in_dir != "none":
                        out_dir = in_dir
            clip.fade_in_direction = in_dir or "none"
            clip.fade_out_direction = out_dir or "none"
            if in_dir == out_dir:
                clip.fade_direction = in_dir or "none"
            else:
                clip.fade_direction = "none"
            clip.fade_groups = max(1, int(groups))
            clip.fade_group_sequence_batches = max(0, min(clip.fade_groups, sequence_batches))
            batch_count = self._clip_sequence_batch_count(clip, clip.fade_groups)
            group_pauses = normalize_group_pauses(
                sequence_pauses,
                max(0, batch_count - 1),
                group_pause,
            )
            duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
            direction_enabled = ((in_dir or "none") != "none" or (out_dir or "none") != "none")
            # R12(2026-08-13): duration 只属于内容 —— 方向/分组/停顿菜单只写结构字段,
            # 一律不写 duration/source_duration/playback_rate/fade_group_single_duration,
            # 也不自动注入 fade 默认值(fade=0 即全强度, 合法)。过渡时长唯一真源 = 过渡翼:
            # 淡入淡出翼 → fade_in/out; 分组过渡翼 → seq_delay_in/out(引擎侧从翼推导)。★R163 不裁到 duration。
            if direction_enabled:
                clip.fade_group_sequence_mode = "sequential" if sequence_mode == "sequential" else "parallel"
                clip.fade_group_pause = group_pause
                clip.fade_group_pauses = list(group_pauses) if sequence_mode == "sequential" else []
            else:
                clip.fade_group_sequence_mode = sequence_mode
                clip.fade_group_pause = group_pause if sequence_mode == "sequential" else 0.0
                clip.fade_group_pauses = list(group_pauses) if sequence_mode == "sequential" else []
            clip.fade_in = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
            clip.fade_out = max(0.0, float(getattr(clip, "fade_out", 0.0) or 0.0))
            changed = True
        if not changed:
            return
        # R57 性能：方向/分组/停顿均为 clip 属性变更，精准刷新替代全量重建
        self._refresh_clips_targeted(target_ids)

    def _on_clip_spatial_scope_set(self, clip_id: str, scope: str):
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        scope = str(scope or "per_shape").strip().lower()
        if scope not in ("per_shape", "global", "follow"):
            scope = "per_shape"
        self._snapshot_for_undo()
        changed = False
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            if str(getattr(clip, "fade_group_spatial_scope", "per_shape") or "per_shape") == scope:
                continue
            clip.fade_group_spatial_scope = scope
            changed = True
        if changed:
            self._refresh_clips_targeted(target_ids)

    def _on_clip_skip_base_order_set(self, clip_id: str, skip_base_order: bool):
        # ★ 2026-08-11 #18：方向过渡「跳过编号排序」开关——只按设备坐标应用方向，
        #   不看 clip/选择的编号序。默认 False = 原逻辑（先编号后坐标系）零影响。
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        self._snapshot_for_undo()
        changed = False
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            if bool(getattr(clip, "fade_group_skip_base_order", False)) == bool(skip_base_order):
                continue
            clip.fade_group_skip_base_order = bool(skip_base_order)
            changed = True
        if changed:
            self._refresh_clips_targeted(target_ids)

    def _on_clip_transition_strategy_set(self, clip_id: str, edge: str, strategy_id: str):
        # ★ 2026-08-12 用户要求: 淡入/淡出过渡方式分开设置（右击「过渡/淡入淡出」）。
        #   写 transition_strategy_in/out 独立字段; 空串 = 跟随统一策略(transition_strategy)。
        #   引擎 playback_engine._clip_transition_strategy 按 fade_in 分支读对应字段。
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        edge = str(edge or "in").strip().lower()
        if edge not in ("in", "out"):
            edge = "in"
        strategy_id = str(strategy_id or "").strip()
        self._snapshot_for_undo()
        changed = False
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            attr = "transition_strategy_in" if edge == "in" else "transition_strategy_out"
            if str(getattr(clip, attr, "") or "") == strategy_id:
                continue
            setattr(clip, attr, strategy_id)
            changed = True
        if changed:
            self._refresh_clips_targeted(target_ids)

    def _on_clip_dir_edge_set(self, clip_id: str, edge: str):
        """★ 2026-08-12 用户要求: 分组方向过渡「应用边」— 淡入/淡出分边应用。
        both=淡入淡出一致(默认); in/out=只应用单边(另一侧方向保留原值)。
        edge 持久化在 clip.fade_direction_edge; _on_clip_dir_fade_set 按此字段
        过滤写入侧(单边时跳过自动补全逻辑)。"""
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        edge = str(edge or "both").strip().lower()
        if edge not in ("in", "out"):
            edge = "both"
        # ★ 多选 toggle 统一判定(2026-08-12 probe41 复现): 目标集全部已是
        # target → 视为「再点一次取消」, 全体回 both; 否则全体设为 target
        # (幂等批量, 不再以右键 clip 单个状态广播——旧逻辑导致多选误切 both)
        if edge != "both":
            all_target = True
            for cid in target_ids:
                _, clip = self.ctrl._find_track_and_clip(cid)
                if clip is None:
                    continue
                cur = str(getattr(clip, "fade_direction_edge", "both") or "both").strip().lower()
                if cur not in ("in", "out"):
                    cur = "both"
                if cur != edge:
                    all_target = False
                    break
            if all_target:
                edge = "both"
        self._snapshot_for_undo()
        changed = False
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            current = str(getattr(clip, "fade_direction_edge", "both") or "both").strip().lower()
            if current not in ("in", "out"):
                current = "both"
            if current == edge:
                continue
            clip.fade_direction_edge = edge
            changed = True
        if changed:
            self._refresh_clips_targeted(target_ids)

    def _on_clip_auto_smooth(self, clip_id: str, preset_id: str, custom=None):
        return apply_smoothing(self, clip_id, preset_id, custom)

    # ══════════════════════════════════════════════════════════════════════════
    #  优化电机力度（梯形速度曲线 + 加速剖面）
    #  力度 = acceleration(加速度值) + AccelProfile(加速剖面形状) + speedFactor(速度倍率)
    #  映射到 Qascade Clip 参数: motor_transition_phase + playback_rate + fade
    #  ___________________________________________________________________________

    # 力度预设 → 梯形速度曲线参数（完整版：accel/cruise/decel三段shape + motor_phase）
    # 梯形速度剖面：加速段/匀速段/减速段时间比例 + 相位差强度
    _MOTOR_FORCE_MAP = {
        "bypass": {"phase_amt": 0.0, "accel": 0.2, "cruise": 0.6, "decel": 0.2},
        **MOTOR_FORCE_PROFILES,
    }

    def _on_clip_motor_force(self, clip_id: str, preset_id: str) -> None:
        """批量设置电机力度层，不覆盖手动相位差、播放速度或淡入淡出。"""
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        preset_id = preset_id if preset_id in self._MOTOR_FORCE_MAP else "natural"
        preset = self._MOTOR_FORCE_MAP[preset_id]
        self._snapshot_for_undo()
        changed = 0
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            if preset_id == "bypass":
                if not str(getattr(clip, "motor_force_preset", "") or ""):
                    continue
                clip.motor_force_preset = ""
                clip.motor_force_phase_amount = 0.0
            else:
                clip.motor_force_preset = preset_id
                clip.motor_force_phase_amount = float(preset["phase_amt"])
                clip.motor_force_accel = float(preset["accel"])
                clip.motor_force_cruise = float(preset["cruise"])
                clip.motor_force_decel = float(preset["decel"])
            changed += 1
        if changed:
            self._refresh_clips_targeted(target_ids)
            self._mark_project_dirty()
        name_map = {"gentle": "极柔和", "soft": "柔和", "natural": "自然", "strong": "强力", "blast": "爆发", "bypass": "旁通"}
        preset_name = name_map.get(preset_id, preset_id)

        if preset_id == "bypass":
            msg_detail = "力度已关闭，恢复原始电机参数。"
        else:
            accel = preset.get("accel", 0.2)
            cruise = preset.get("cruise", 0.6)
            decel = preset.get("decel", 0.2)
            phase = preset.get("phase_amt", 0.0)
            msg_detail = (
                f"相位差强度: {phase:.2f}\n"
                f"梯形曲线: 加速{accel*100:.0f}% / 匀速{cruise*100:.0f}% / 减速{decel*100:.0f}%"
            )

        QMessageBox.information(
            self, "优化电机力度",
            f"已对 {changed} 个 Clip 应用「{preset_name}」电机力度预设。\n{msg_detail}"
        )

    def _on_clip_light_tracking(self, clip_id: str, enabled: bool, threshold: int, attack: float, release: float, color: str = "#FFFFFF", source: str = "pump", max_brightness: int = 255):
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        self._snapshot_for_undo()
        color = str(color or "#FFFFFF").strip().upper()
        if not (len(color) == 7 and color.startswith("#")):
            color = "#FFFFFF"
        source = str(source or "pump").strip().lower()
        if source not in {"pump", "valve"}:
            source = "pump"

        changed = False
        last_clip = None
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            clip.light_tracking_enabled = bool(enabled)
            clip.light_tracking_threshold = max(0, min(255, int(threshold)))
            clip.light_tracking_attack = max(0.01, float(attack))
            clip.light_tracking_release = max(0.01, float(release))
            clip.light_tracking_color = color
            clip.light_tracking_source = source
            clip.light_tracking_max_brightness = max(0, min(255, int(max_brightness)))
            changed = True
            last_clip = clip
        if not changed:
            return
        self._refresh_clips_targeted(target_ids)

        if len(target_ids) == 1 and last_clip is not None:
            if enabled:
                source_label = "valve" if getattr(last_clip, "light_tracking_source", "pump") == "valve" else "pump"
                QMessageBox.information(
                    self,
                    "Light Tracking",
                    f"Enabled: source={source_label}, color={last_clip.light_tracking_color}, "
                    f"threshold={last_clip.light_tracking_threshold}, "
                    f"response={last_clip.light_tracking_attack:.2f}/{last_clip.light_tracking_release:.2f}s, "
                    f"max_brightness={last_clip.light_tracking_max_brightness}.",
                )
            else:
                QMessageBox.information(self, "Light Tracking", "Disabled for this clip.")

    def _on_clip_hold_toggled(self, clip_id: str, enabled: bool):
        self._on_clip_hold_mode_changed(clip_id, "last_cycle" if enabled else "none")

    def _on_clip_hold_mode_changed(self, clip_id: str, mode: str):
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        mode = str(mode or "none").strip().lower()
        if mode not in ("none", "last_frame", "last_cycle"):
            mode = "none"
        self._snapshot_for_undo()
        changed = False
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            old_mode = str(getattr(clip, "hold_mode", "") or "").strip().lower()
            if old_mode not in ("none", "last_frame", "last_cycle"):
                old_mode = "last_cycle" if bool(getattr(clip, "hold_last_frame", False)) else "none"
            if old_mode == mode:
                continue
            if old_mode == "none" and mode != "none":
                clip.hold_restore_fade_out = max(0.0, float(getattr(clip, "fade_out", 0.0) or 0.0))
            clip.hold_mode = mode
            clip.hold_last_frame = mode != "none"
            if mode != "none":
                clip.fade_out = 0.0
            else:
                restore = max(0.0, float(getattr(clip, "hold_restore_fade_out", 0.0) or 0.0))
                if restore > 0.0:
                    clip.fade_out = restore      # ★ R163：原样还原，不裁到 duration
                clip.hold_restore_fade_out = 0.0
            changed = True
        if not changed:
            return
        self._refresh_clips_targeted(target_ids)

    def _clip_source_duration(self, clip) -> float:
        source_duration = max(0.0, float(getattr(clip, "source_duration", 0.0) or 0.0))
        if source_duration > 0.0:
            return source_duration
        rate = max(0.001, float(getattr(clip, "playback_rate", 1.0) or 1.0))
        duration = max(0.05, float(getattr(clip, "duration", 0.05) or 0.05))
        return max(0.05, duration * rate)

    def _on_clip_speed_changed(self, clip_id: str, playback_rate: float):
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        try:
            playback_rate = float(playback_rate)
        except Exception:
            playback_rate = 1.0
        if math.isnan(playback_rate):
            # ★ NaN 哨兵→ 1.0：否则 max(0.001, min(100.0, nan)) 返回 100.0
            #   （nan 参与比较恒 False）→ 坏值变成"100 倍速"灾难。
            #   inf 走下方既有钳制（→100.0）语义不变。
            playback_rate = 1.0
        playback_rate = max(0.001, min(100.0, playback_rate))
        self._snapshot_for_undo()
        changed = False
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            if self._apply_clip_speed(clip, playback_rate):
                changed = True
        if not changed:
            return
        self._refresh_clips_targeted(target_ids)

    def _apply_clip_speed(self, clip, playback_rate: float) -> bool:
        """把**显式变速**落到单个 clip；无变化返回 False。

        ★ R12(2026-08-13)：速度只改内容时长（duration = 源长/速率）；顺序分组结构
          （单组窗口/扫掠）由引擎按过渡翼派生，这里不按「组数×单组+停顿」展开。

        ★★ 2026-09-14 用户定案：显式变速（右击菜单「速度 / 持续时间」/自定义）后把该
          clip 标 `resize_cycle=False` → 此后拖右缘仍按旧语义"保持速度读数"（用 rate
          折算时长）；未显式变速的 clip 默认 True，拖时长只变内容长度不变速。
          "保留更改速度的功能，但是仅通过右击菜单应用变速"。
        """
        source_duration = self._clip_source_duration(clip)
        new_duration = max(0.05, source_duration / playback_rate)
        if abs(float(getattr(clip, "playback_rate", 1.0) or 1.0) - playback_rate) < 0.0001 and            abs(float(getattr(clip, "duration", 0.0) or 0.0) - new_duration) < 0.0001:
            return False
        clip.source_duration = source_duration
        clip.playback_rate = playback_rate
        clip.duration = new_duration
        # ★ 2026-09-14 深查修正 B4：**1.0 是"无变速"**，必须把标志复位回 True。
        #   原实现无条件置 False，于是"先变速又调回 100%"的 clip 之后拖时长永远走
        #   旧的"保持速度"语义（内容被拉伸），与用户要的默认"只改时长不变速"相反。
        clip.resize_cycle = abs(float(playback_rate) - 1.0) < 0.0001
        _clamp_clip_timing(clip, new_duration)
        return True

    def _on_clip_record_output_source_changed(self, clip_id: str, enabled: bool):
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        self._snapshot_for_undo()
        changed = False
        enabled = bool(enabled)
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            if bool(getattr(clip, "record_output_source_enabled", False)) == enabled:
                continue
            clip.record_output_source_enabled = enabled
            changed = True
        if not changed:
            return
        self._refresh_clips_targeted(target_ids)
        self._mark_project_dirty()

    def _on_clip_motor_transition_phase_changed(self, clip_id: str, enabled: bool, amount: float = 1.0):
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        self._snapshot_for_undo()
        changed = False
        enabled = bool(enabled)
        try:
            amount = max(0.0, min(10.0, float(amount)))
        except Exception:
            amount = 1.0
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            old_enabled = bool(getattr(clip, "motor_transition_phase_enabled", False))
            old_amount = max(0.0, float(getattr(clip, "motor_transition_phase_amount", 1.0) or 0.0))
            if old_enabled == enabled and abs(old_amount - amount) < 0.0001:
                continue
            clip.motor_transition_phase_enabled = enabled
            clip.motor_transition_phase_amount = amount
            changed = True
        if not changed:
            return
        self._refresh_clips_targeted(target_ids)
        self._mark_project_dirty()

    def _on_clip_precharge_changed(self, clip_id: str, enabled: bool, lead_ms: float, level: int):
        """处理泵预充参数变更。"""
        target_ids = self._resolve_context_clip_ids(clip_id)
        if not target_ids:
            return
        self._snapshot_for_undo()
        changed = False
        enabled = bool(enabled)
        try:
            lead_ms = max(0.0, float(lead_ms))
        except Exception:
            lead_ms = 1000.0
        try:
            level = max(0, min(255, int(level)))
        except Exception:
            level = 13
        for cid in target_ids:
            _, clip = self.ctrl._find_track_and_clip(cid)
            if clip is None:
                continue
            old_enabled = bool(getattr(clip, "precharge_enabled", False))
            old_lead_ms = max(0.0, float(getattr(clip, "precharge_lead_ms", 1000.0) or 1000.0))
            old_level = max(0, min(255, int(getattr(clip, "precharge_level", 13) or 0)))
            if old_enabled == enabled and abs(old_lead_ms - lead_ms) < 0.5 and old_level == level:
                continue
            clip.precharge_enabled = enabled
            clip.precharge_lead_ms = lead_ms
            clip.precharge_level = level
            changed = True
        if not changed:
            return
        self._refresh_clips_targeted(target_ids)
        self._mark_project_dirty()
        # 确认弹窗（QMessageBox 用模块顶层导入 :17；局部导入会绕过 studio_apply 的静音桩）
        count = len(target_ids)
        if enabled:
            QMessageBox.information(
                self, "泵预充已启用",
                f"已对 {count} 个Clip启用泵预充\n\n"
                f"提前时间：{lead_ms:.0f} ms\n"
                f"预置电平：{level}/255"
            )
        else:
            QMessageBox.information(
                self, "泵预充已禁用",
                f"已对 {count} 个Clip禁用泵预充"
            )

    #: 相邻判定的**间隔上限**（秒）：前后两个 clip 的间距 ≤ 此值即视为"可过渡的一对"。
    #:
    #: ★★ R134 用户实报修复："必须在上一个 clip 结束前放上新的 clip…才能实现过渡淡入…
    #   只要新的 clip 在老的 clip 结束之后放置，新的 clip 就会没有淡入效果直接到对应
    #   角度…这种 bug 必须在上一个 clip 距离下一个 clip 在时间线上间隔不久才会触发"
    #:
    #: 真因：原先硬编码 `abs(gap) > 0.05` 才算"不紧接" → **间隔 >50ms 就不配对**，
    #: 于是 `fade_in` 从未被写进新 clip（用户看到的"没有过渡、直接到角度"，而
    #: 引擎侧的过渡起点/渐变本身都是好的 —— 这与"过渡起点角度正确"完全吻合）。
    #: 语义纠正：**"紧接"不是过渡的必要条件**。上一个动作结束后设备停在末值，
    #: 新动作从这个末值过渡过去，间隔多长都成立 —— 间隔只影响"中间那段空档"，
    #: 不影响新 clip 自己的淡入。故按"最近的前一个动作"配对，容差放宽到 2.0s
    #: （更远的动作更像"另起一段"，留给用户手调）。
    def _cross_track_transition_pairs(self, clip_map: dict) -> list:
        ordered = sorted(
            ((clip, track) for track, clip in clip_map.values()),
            key=lambda item: float(getattr(item[0], "start_time", 0.0) or 0.0),
        )
        pairs = []
        for index in range(len(ordered) - 1):
            ca, ta = ordered[index]
            cb, tb = ordered[index + 1]
            if ta is tb:
                continue
            if not _pair_is_transitionable(ca, cb, _TRANSITION_PAIR_GAP_SEC):
                continue
            has_manual = (
                float(getattr(ca, "fade_out", 0.0) or 0.0) > 0.01
                or float(getattr(cb, "fade_in", 0.0) or 0.0) > 0.01
                or float(getattr(ca, "seq_delay_out", 0.0) or 0.0) > 0.01
                or float(getattr(cb, "seq_delay_in", 0.0) or 0.0) > 0.01
            )
            if has_manual:
                continue
            types_a = self._infer_clip_channel_types(ca)
            types_b = self._infer_clip_channel_types(cb)
            duration, _enable_motor = self._transition_preset(types_a, types_b)
            pairs.append((ca, cb, types_a, types_b, duration))
        return pairs

    def _on_clip_optimize_transitions(self, clip_ids: list) -> int:
        """模仿既有填表系统的连贯动作过渡：多选 clip 后自动优化相邻 clip 间的淡入淡出/预充/电机相位。

        原则：
        - 在两个 clip **前后相邻**时生效（★ R134：允许中间有空档，见
          `_TRANSITION_PAIR_GAP_SEC` —— 上一个动作停在哪，新动作就从哪过渡）
        - 已有手动 fade_in/fade_out/seq_delay 的 clip 跳过不碰
        - 电机→电机: 自动 fade_out + fade_in（~200ms）+ motor_transition_phase
        - 泵→泵: 自动 fade_out + fade_in（~600ms，水惯性补偿）+ precharge
        - 灯光→灯光: 自动 Fade_out + fade_in（~300ms，平滑过渡）
        - 混合类型: 用较长值统一
        """
        if not clip_ids or len(clip_ids) < 2:
            return 0

        # 第1步：收集待优化的clip对
        clip_map: dict = {}
        clip_pairs = []
        skipped_manual = 0
        skipped_not_adjacent = 0
        for cid in clip_ids:
            t, clip = self.ctrl._find_track_and_clip(cid)
            if clip is not None:
                clip_map[cid] = (t, clip)
        # 在同一条 track 内按 start_time 排序
        track_groups: dict = {}
        for cid, (t, clip) in clip_map.items():
            tid = t.id if hasattr(t, "id") else id(t)
            track_groups.setdefault(tid, []).append((cid, clip))
        for tid, items in track_groups.items():
            items.sort(key=lambda x: x[1].start_time)
            for i in range(len(items) - 1):
                cid_a, ca = items[i]
                cid_b, cb = items[i + 1]
                # ★ R134：有间隔也算可过渡（见 `_pair_is_transitionable`）
                if not _pair_is_transitionable(ca, cb, _TRANSITION_PAIR_GAP_SEC):
                    skipped_not_adjacent += 1
                    continue
                # 跳过已有手动过渡参数的（不覆盖用户设置）
                has_manual = (
                    float(getattr(ca, "fade_out", 0.0) or 0.0) > 0.01
                    or float(getattr(cb, "fade_in", 0.0) or 0.0) > 0.01
                    or float(getattr(cb, "seq_delay_in", 0.0) or 0.0) > 0.01
                    or float(getattr(ca, "seq_delay_out", 0.0) or 0.0) > 0.01
                )
                if has_manual:
                    skipped_manual += 1
                    continue
                # 推断通道类型
                ch_a = self._infer_clip_channel_types(ca)
                ch_b = self._infer_clip_channel_types(cb)
                # 根据通道类型组合选择过渡方案
                dur, enable_motor_trans = self._transition_preset(ch_a, ch_b)
                # 收集到待优化列表（不立即应用）
                clip_pairs.append((ca, cb, ch_a, ch_b, dur))

        cross_track_pairs = self._cross_track_transition_pairs(clip_map)
        dialog_pairs = clip_pairs or cross_track_pairs
        if not dialog_pairs:
            msg = "没有找到可优化的 Clip 对。\n\n"
            if skipped_manual > 0:
                msg += f"跳过 {skipped_manual} 对：已有手动淡入淡出/延迟参数（不覆盖）\n"
            if skipped_not_adjacent > 0:
                msg += f"跳过 {skipped_not_adjacent} 对：不紧接（中间有其他 Clip）\n"
            QMessageBox.information(self, "优化过渡方式", msg)
            return 0

        from .transition_optimizer_dialog import TransitionOptimizerDialog, resolve_adjusted_pairs
        cross_track_only = not clip_pairs and bool(cross_track_pairs)
        dlg = TransitionOptimizerDialog(
            dialog_pairs,
            parent=self,
            cross_track_default=cross_track_only,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return 0

        adjusted_pairs, global_settings = resolve_adjusted_pairs(
            dlg, cross_track_pairs, cross_track_only)

        if not adjusted_pairs:
            return 0
        self._snapshot_for_undo()
        changed = 0
        touched_ids: list[str] = []

        for ca, cb, fade_out_sec, fade_in_sec, strategy_id, run_mode, curve_profile in adjusted_pairs:
            # 写入fade参数
            if float(getattr(ca, "fade_out", 0.0) or 0.0) < 0.01:
                ca.fade_out = fade_out_sec
            if float(getattr(cb, "fade_in", 0.0) or 0.0) < 0.01:
                cb.fade_in = fade_in_sec

            # A 的淡出与 B 的淡入分别保存，避免同一 Clip 两端策略互相覆盖。
            ca.transition_strategy_out = strategy_id
            cb.transition_strategy_in = strategy_id
            ca.transition_curve_out = curve_profile
            cb.transition_curve_in = curve_profile
            cb.transition_strategy = strategy_id
            cb.transition_curve = curve_profile
            cb.run_mode = run_mode

            # 推断类型以决定附加逻辑
            ch_a = self._infer_clip_channel_types(ca)
            ch_b = self._infer_clip_channel_types(cb)

            # 电机相位差（电机→电机）
            if "motor" in ch_a and "motor" in ch_b:
                if hasattr(cb, "motor_transition_phase_enabled"):
                    cb.motor_transition_phase_enabled = True
                    cb.motor_transition_phase_amount = 1.0

            # 泵预充（按默认: 1000ms / 5Hz等效=13）
            if "pump" in ch_a or "pump" in ch_b:
                if hasattr(cb, "precharge_enabled") and not bool(getattr(cb, "precharge_enabled", False)):
                    cb.precharge_enabled = True
                    cb.precharge_lead_ms = max(1000.0, float(getattr(cb, "precharge_lead_ms", 1000.0) or 1000.0))

            # 标记已应用优化过渡方式（仅 cb，ca 不设，避免影响 A 的入边曲线）
            if hasattr(cb, "transitions_optimized"):
                cb.transitions_optimized = "optimized"

            changed += 1
            touched_ids.extend((ca.id, cb.id))

        if changed:
            # R57：全部为 clip 属性写入，精准刷新替代全量重建
            self._refresh_clips_targeted(touched_ids)
            self._mark_project_dirty()

        # 汇总提示
        cross_track_info = "（含跨track）" if global_settings.get("cross_track_enabled") else ""
        QMessageBox.information(
            self, "优化过渡方式",
            f"已优化 {changed} 对关联 Clip 的过渡方式{cross_track_info}。\n"
            f"跳过 {skipped_manual} 对（已有手动参数）、{skipped_not_adjacent} 对（不紧接）。\n"
            f"默认策略：{global_settings.get('default_curve')} curve, {global_settings.get('default_run_mode')} mode"
        )
        return changed


    @staticmethod
    def _infer_clip_channel_types(clip) -> set:
        """推断clip包含的通道类型 — 改进版：基于实际DMX数据分析，非硬编码通道号。

        策略：
        1. 检查clip元数据（fixture_type, device_type等）
        2. 分析DMX帧数据的变化模式（高频变化→motor，低频→pump，渐变→light）
        3. 后备：通道号范围硬编码（兼容旧数据）
        """
        types: set = set()

        # 策略1: 优先使用clip元数据
        fixture_type = getattr(clip, "fixture_type", None) or getattr(clip, "device_type", None)
        if fixture_type:
            ft = str(fixture_type).lower()
            if "motor" in ft or "servo" in ft or "axis" in ft:
                types.add("motor")
            if "pump" in ft or "valve" in ft or "water" in ft:
                types.add("pump")
            if "light" in ft or "dimmer" in ft or "led" in ft or "par" in ft:
                types.add("light")
            if types:
                return types

        # 策略2: 分析DMX帧数据变化特征（需要访问frames）
        try:
            frames = getattr(clip, "frames", None) or getattr(clip, "_frames", None)
            if frames and len(frames) > 5:
                # 采样分析：取前10帧，计算通道变化率
                sample_frames = frames[:min(10, len(frames))]
                channel_variance = {}

                for frame in sample_frames:
                    data = getattr(frame, "data", None)
                    if data is not None:
                        for ch_idx, value in enumerate(data[:20]):  # 只分析前20通道
                            if ch_idx not in channel_variance:
                                channel_variance[ch_idx] = []
                            channel_variance[ch_idx].append(value)

                # 统计各类型通道数量，按主导类型判断
                motor_count = 0
                pump_count = 0
                light_count = 0

                for ch_idx, values in channel_variance.items():
                    if len(values) < 2:
                        continue
                    variance = max(values) - min(values)
                    # 高方差→motor（频繁变化），中方差→pump，低方差→dimmer/light
                    if variance > 100:
                        motor_count += 1
                    elif variance > 30:
                        pump_count += 1
                    elif variance > 5:
                        light_count += 1

                # 返回主导类型（数量最多的）
                counts = [("motor", motor_count), ("pump", pump_count), ("light", light_count)]
                counts.sort(key=lambda x: x[1], reverse=True)
                if counts[0][1] > 0:
                    types.add(counts[0][0])
                    # 如果第二类型数量>=50%主导类型，也添加（混合类型clip）
                    if counts[1][1] >= counts[0][1] * 0.5:
                        types.add(counts[1][0])

                if types:
                    return types
        except Exception:
            pass

        # 策略3: 后备硬编码（兼容旧数据 + 无帧数据场景）
        try:
            ac = getattr(clip, "active_channels_by_universe", None)
            if ac and isinstance(ac, dict):
                for u, chs in ac.items():
                    for ch in (chs or []):
                        try:
                            c = int(ch)
                        except Exception:
                            continue
                        # 硬编码规则（基于常见DMX布局）
                        if 1 <= c <= 4:
                            types.add("motor")
                        elif 5 <= c <= 8:
                            types.add("pump")
                        elif c == 9:
                            types.add("dimmer")
                        elif 10 <= c <= 20:
                            types.add("light")
        except Exception:
            pass

        # 兜底：如果完全无法推断，默认motor（最保守）
        if not types:
            types.add("motor")
        return types

    @staticmethod

    @staticmethod
    def _transition_preset(types_a: set, types_b: set) -> "tuple[float, bool]":
        """根据前后 clip 通道类型组合，返回 (fade_out_A_ms, fade_in_B_ms, 启用电机相位差?)。

        值取自/等效参数：
        - 电机补间(ASS6): 200ms EASE_OUT + EASE_IN + 预加速相位
        - 泵变频预置: 600ms（水惯性，显大于电机）
        - 灯光渐变: 300ms SmoothStep
        """
        motor_dur = 0.2
        pump_dur = 0.6
        light_dur = 0.3
        vals = []
        for t in (types_a, types_b):
            if "pump" in t:
                vals.append(pump_dur)
            elif "light" in t:
                vals.append(light_dur)
            else:
                vals.append(motor_dur)
        # 用较长值统一（折中，不覆盖用户的精细控制）
        d = max(vals)
        enable_motor = "motor" in types_a or "motor" in types_b
        return d, enable_motor

    def _on_add_folder(self):
        # 文件夹插入到当前选中轨道之后（顶层）
        insert_after_id = self._selected_track_id or ""
        folder = Track(
            id=str(uuid.uuid4()),
            name=f"文件夹 {len(self._session.tracks) + 1}",
            is_folder=True,
            color="#7A6AD8",
            height=34,
        )
        self.ctrl.add_track_after(folder, insert_after_id)

    def _on_folder_toggled(self, track_id: str):
        t = self._session.get_track(track_id) if hasattr(self._session, "get_track") else None
        if t is None:
            return
        t.folder_expanded = not bool(getattr(t, "folder_expanded", True))
        if hasattr(self.ctrl, "tracks_changed"):
            self.ctrl.tracks_changed.emit()

    def _on_track_moved(self, track_id: str, delta: int):
        """右键「上移/下移轨道」（★ R141 修复见 `timeline_perf.wire_track_moved`）。

        沿 `session.tracks` 扁平顺序与相邻项换位（顺序真源即此列表；边界返回
        None → 不改模型/不压空撤销）。换位交给控制器既有 `move_track`
        （快照 + pop/insert + tracks_changed），与其它轨道编辑共用同一链路。
        """
        tracks = list(getattr(self._session, "tracks", None) or [])
        target = adjacent_index(tracks, track_id, delta)
        if target is None:
            return
        move = getattr(self.ctrl, "move_track", None)
        if move is not None:
            move(track_id, target)

    def _on_assign_parent(self, track_id: str):
        # 允许选择任意轨道作为父节点（不限文件夹）
        all_tracks = [t for t in self._session.tracks if t.id != track_id]
        names = ["（顶层，无上级）"] + [t.name for t in all_tracks]
        name, ok = QInputDialog.getItem(self, "选择上级轨道", "把该轨道放入：", names, 0, False)
        if not ok:
            return
        parent_id = "" if name == names[0] else all_tracks[names.index(name)-1].id
        self._on_reparent_track(track_id, parent_id)

    def _on_track_height_changed(self, track_id: str, height: int):
        t = self._session.get_track(track_id) if hasattr(self._session, "get_track") else None
        if t is None:
            return
        t.height = max(36, min(240, int(height)))
        if hasattr(self.ctrl, "tracks_changed"):
            self.ctrl.tracks_changed.emit()

    def _on_reparent_track(self, track_id: str, parent_id: str):
        t = self._session.get_track(track_id) if hasattr(self._session, "get_track") else None
        if t is None:
            return
        parent_id = parent_id or ""
        if parent_id == track_id:
            return
        if parent_id:
            parent = self._session.get_track(parent_id) if hasattr(self._session, "get_track") else None
            if parent is None:
                parent_id = ""
            else:
                # 防止循环：遍历目标父节点的所有祖先，确保其中没有 track_id
                probe = parent
                seen: set[str] = set()
                while probe is not None:
                    if probe.id == track_id:
                        return  # 循环引用，拖放非法
                    pid = getattr(probe, "parent_id", "") or ""
                    if not pid or pid in seen:
                        break
                    seen.add(pid)
                    probe = self._session.get_track(pid) if hasattr(self._session, "get_track") else None
                # 如果目标是文件夹，自动展开它（方便用户看到新加入的子轨道）
                if bool(getattr(parent, "is_folder", False)):
                    parent.folder_expanded = True
        if getattr(t, "parent_id", "") == parent_id:
            return
        t.parent_id = parent_id
        if hasattr(self.ctrl, "tracks_changed"):
            self.ctrl.tracks_changed.emit()

    def _on_cross_track_drag(self, clip_id: str, global_y: int, new_start: float):
        _group_edit.preview_move(self, clip_id, global_y, new_start)

    def _on_cross_track_drag_end(self, clip_id: str, global_y: int, new_start: float):
        _group_edit.commit_move(self, clip_id, global_y, new_start)

    def _on_track_selected_local(self, track_id: str):
        self._selected_track_id = track_id or ""

    # ── R179 音频调制轨（可添加/可拖动的真正 Track；用户规格 ①③）──────────
    def _on_add_audio_mod_track(self):
        """「添加音频调制轨道」：与添加轨道/添加文件夹同级，在选中位置插入。"""
        existing = [t for t in self._session.tracks
                    if bool(getattr(t, "is_audio_mod", False))]
        if existing:
            self._audio_mod_track.set_scope_track(existing[0].id)   # 单例：改绑
            return
        track = make_audio_mod_track()
        parent_id = ""
        insert_after_id = ""
        selected = (self._session.get_track(self._selected_track_id)
                    if hasattr(self._session, "get_track") and self._selected_track_id
                    else None)
        if selected is not None:
            parent_id = (selected.id if bool(getattr(selected, "is_folder", False))
                         else (getattr(selected, "parent_id", "") or ""))
            insert_after_id = selected.id
        track.parent_id = parent_id
        self.ctrl.add_track_after(track, insert_after_id)
        self._audio_mod_track.set_scope_track(track.id)

    def _ensure_mod_track_for_legacy_state(self):
        """旧档迁移（用户规格 ③）：无 scope_track_id → 顶部补/绑一条调制轨。

        直插 session.tracks（加载期，不压撤销快照）；已有调制轨则只改绑。
        """
        tracks = list(getattr(self._session, "tracks", None) or [])
        mod_tracks = [t for t in tracks if bool(getattr(t, "is_audio_mod", False))]
        if mod_tracks:
            target = mod_tracks[0]
        else:
            target = make_audio_mod_track()
            tracks.insert(0, target)
            self._session.tracks = tracks
            self._reload_tracks()
        self._audio_mod_track.set_scope_track(target.id)

    def _bind_mod_lane_editors(self):
        """把共享编辑器（状态真源）绑给全部调制轨行内画布。"""
        for v in self._clip_area._container._views:
            if isinstance(v, AudioModLaneView):
                v.bind_editor(self._audio_mod_track)

    def _on_add_track(self):
        # 新轨道继承当前选中轨道的 parent_id，并插入到选中轨道之后
        parent_id = ""
        insert_after_id = ""
        selected = self._session.get_track(self._selected_track_id) if hasattr(self._session, "get_track") and self._selected_track_id else None
        if selected is not None:
            if bool(getattr(selected, "is_folder", False)):
                # 选中的是文件夹：新轨道成为其子轨道
                parent_id = selected.id
            else:
                # 选中的是普通轨道：新轨道与其同级
                parent_id = getattr(selected, "parent_id", "") or ""
            insert_after_id = selected.id
        new_track = Track(
            id=str(uuid.uuid4()),
            name=f"Track {len(self._session.tracks) + 1}",
            parent_id=parent_id,
        )
        self.ctrl.add_track_after(new_track, insert_after_id)

    def handle_paste_at(self, track_id: str, time_pos: float):
        self.ctrl.paste_to_track(track_id, time_pos)

    def audio_play(self, pos: float = None):
        self._audio_track.play(pos)

    def audio_pause(self):
        self._audio_track.pause()

    def audio_stop(self):
        self._audio_track.stop()

    def audio_seek(self, pos: float):
        self._audio_track.seek(pos)

    def set_tool(self, tool: str):
        self._current_tool = tool
        set_tool_mode(_TOOL_MODES.get(tool, ToolMode.SELECT))
        self._sync_tool_button_states()
        self._apply_pen_gate()
        for view in self._clip_area._container._views:
            view.update()
            for item in view._items.values():
                item.update()

    def _tool_buttons(self) -> dict:
        """工具键 → 按钮（含 R179 画笔；缺按钮安全跳过）。"""
        btns = {TOOL_SELECT: getattr(self, "_btn_tool_select", None),
                TOOL_RAZOR: getattr(self, "_btn_tool_razor", None),
                TOOL_PEN: getattr(self, "_btn_tool_pen", None)}
        return {k: b for k, b in btns.items() if b is not None}

    def _sync_tool_button_states(self):
        active_ss = getattr(self, "_tool_btn_ss_active", "")
        idle_ss = getattr(self, "_tool_btn_ss_idle", "")
        for tool, btn in self._tool_buttons().items():
            btn.setStyleSheet(active_ss if tool == self._current_tool else idle_ss)

    def _apply_pen_gate(self):
        """PEN 禁用态同步到全部普通轨 ClipItem（工具切换与轨道重建共用）。"""
        apply_pen_tool_gate(self._clip_area._container._views,
                            self._current_tool == TOOL_PEN)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        ctrl_held = bool(mods & Qt.KeyboardModifier.ControlModifier)

        if ctrl_held and key in (Qt.Key.Key_C, Qt.Key.Key_X, Qt.Key.Key_V):
            self._handle_clipboard_key(key)     # ★ R135：Ctrl+C/X/V 统一收口
        elif key == Qt.Key.Key_V and not ctrl_held:
            self.set_tool(TOOL_SELECT)
        elif key == Qt.Key.Key_B:
            self.set_tool(TOOL_RAZOR)
        else:
            super().keyPressEvent(event)

    def _handle_clipboard_key(self, key) -> None:
        """Ctrl+C/X/V 统一处理（★ R135：C/X 原先完全不存在，V 的轨道选择见 R131）。"""
        if key == Qt.Key.Key_V:
            cursor_time = self.ctrl._play_pos
            if self.ctrl.has_track_clipboard():
                self.ctrl.paste_track_at_pos(cursor_time)
            elif self.ctrl.has_clip_clipboard():
                # 优先**当前选中**轨道，否则第一条**非文件夹**轨道（R131）
                self.ctrl._selected_track_id = self._selected_track_id
                self.ctrl.paste_clip_at_cursor(cursor_time)
            return
        ids = _clipboard_key_targets(self)
        if not ids:
            return
        if key == Qt.Key.Key_C:
            self.ctrl.copy_clip(ids)
        else:
            self.ctrl.cut_to_clipboard(ids)

    def _on_audio_duration_changed(self, path: str, duration: float):
        """音频加载后更新标尺时长和容器宽度，修复长音频截断。"""
        self._audio_mod_track.set_audio_duration(duration)
        if duration > 0:
            self._sync_timeline_duration()

    # ══════════════════════════════════════════════════════════════════════════
    #  调度台：Clip 一等公民（**完整快照**，非"包含效果线索"）
    #
    #  用户定稿口径：clip 缓存 = clip.to_dict(include_frames=True) 全量
    #  （复用项目加载同一条 Track.from_dict 反序列化路径 —— 零第二套序列化，
    #   Clip 模型加字段缓存自动覆盖）。recall：clip 还在 → 整体替换回快照；
    #   已被删 → 按快照**重建**（轨道也在则入原轨，不在则建"Clip 恢复轨"）。
    #  显示：标签=clip label/preset_name，分类=按 preset_name 查效果库的
    #   真实分类（查不到=未分类），按轨道分组（track_type）。
    # ══════════════════════════════════════════════════════════════════════════
    _CLIP_BUS_REPORT_INTERVAL = 0.25
    _clip_bus_last_report: float = 0.0
    _clip_cat_cache: dict = {}

    def _clip_category(self, preset_name: str) -> str:
        """preset_name → 效果库真实分类（先 id 后 name；查不到=空=未分类）。"""
        pn = str(preset_name or "").strip()
        if not pn:
            return ""
        if pn in self._clip_cat_cache:
            return self._clip_cat_cache[pn]
        cat = ""
        try:
            from ..effect_engine import get_engine as _ge, normalize_effect_category
            eng = _ge()
            preset = eng.get_preset(pn)
            if preset is None:
                for p in eng.all_presets():
                    if p.name == pn:
                        preset = p
                        break
            if preset is not None:
                cat = normalize_effect_category(getattr(preset, "category", ""))
        except Exception:
            cat = ""
        self._clip_cat_cache[pn] = cat
        return cat

    def report_to_bus(self, force: bool = False) -> None:
        """把当前时间线全部 clip 上报为调度台窗格（节流；异常绝不影响时间线）。"""
        try:
            import time as _t
            now = _t.monotonic()
            if not force and (now - self._clip_bus_last_report) < self._CLIP_BUS_REPORT_INTERVAL:
                return
            self._clip_bus_last_report = now
            from ...processing.dispatch import EffectBus, EffectNode
            # ★ 当前选中的 clip（用户要求：调度台里高亮提示）——时间线选中态原本
            #   不进总线，故调度台看不出哪个 clip 被选中。此处取一次快照后随节点上报。
            try:
                _sel_ids = {str(x) for x in (self._selected_clip_ids() or set())}
            except Exception:
                _sel_ids = set()
            nodes = []
            for track in list(getattr(getattr(self, "ctrl", None), "_session", None)
                              and self.ctrl._session.tracks or []):
                tname = str(getattr(track, "name", "") or "")
                for c in list(getattr(track, "clips", []) or []):
                    label = str(getattr(c, "label", "") or "")
                    pname = str(getattr(c, "preset_name", "") or "")
                    dur = float(getattr(c, "duration", 0.0) or 0.0)
                    nfix = len(getattr(c, "base_order_fixture_ids", []) or [])
                    cat = self._clip_category(pname)
                    name = label or pname or f"Clip {dur:.1f}s"
                    summary = f"{dur:.1f}s · {nfix}台" + (f" · {pname}" if pname and pname != name else "")
                    nodes.append(EffectNode(
                        panel="clip", track_type=tname,
                        group_key=f"clip:{c.id}",
                        group_name=tname or "轨道",
                        fixture_ids=[str(x) for x in (c.base_order_fixture_ids or [])],
                        effect_id=str(c.id), effect_name=name,
                        category=cat, layers=["clip_playback"],
                        summary=summary, live=False, level=-1.0,
                        tag=pname or label or "clip",
                        # 时间线选中 → 调度台格子高亮（FOCUS 角标）
                        selected=(str(c.id) in _sel_ids),
                        extra={"clip_id": str(c.id),
                               "track_id": str(getattr(track, "id", "") or "")},
                    ))
            bus = EffectBus.instance()
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
        """调度台捕获：全部 clip 的**完整快照**（含 frames； Track.from_dict 可无损重建）。"""
        out = {}
        try:
            for track in list(getattr(getattr(self, "ctrl", None), "_session", None)
                              and self.ctrl._session.tracks or []):
                for c in list(getattr(track, "clips", []) or []):
                    out[str(c.id)] = {
                        "clip": c.to_dict(include_frames=True),
                        "track_id": str(getattr(track, "id", "") or ""),
                        "track_name": str(getattr(track, "name", "") or ""),
                    }
        except Exception:
            pass
        return {"clips": out}

    def dispatch_recall_state(self, node: dict, state: Optional[dict] = None) -> bool:
        """按节点还原 clip：存在→整体替换回快照；被删→按快照重建并选中。"""
        try:
            node = dict(node or {})
            state = dict(state or {})
            cd = (state.get("clips", {}) or {}).get(
                str(node.get("effect_id", "") or "")) if "clips" in state else state.get("clip")
            if cd is None and isinstance(node.get("extra"), dict) and node["extra"].get("clip_id"):
                cd = (state.get("clips", {}) or {}).get(
                    str(node["extra"]["clip_id"]))
            if not isinstance(cd, dict) or not cd.get("clip"):
                # 无快照（如直接双击监控格）→ 退化为**定位选中**：跳进时间线编辑上下文
                cid = str(node.get("effect_id", "") or "")
                track, clip = self.ctrl._find_track_and_clip(cid)
                if clip is None:
                    return False
                for view in self._iter_clip_views():
                    if getattr(view, "track", None) is track and hasattr(view, "set_selected_ids"):
                        view.set_selected_ids({cid})
                return True
            # ★ 深查修正：原 `from ..core.models import Track` 解析到不存在的 src.ui.core。
            rebuilt = Track.from_dict({
                "id": str(cd.get("track_id") or ""),
                "name": str(cd.get("track_name") or ""),
                "clips": [cd["clip"]],
            })
            if not rebuilt.clips:
                return False
            new_clip = rebuilt.clips[0]
            track, old = self.ctrl._find_track_and_clip(new_clip.id)
            target = track
            if track is not None and old is not None:
                track.clips[track.clips.index(old)] = new_clip
                self._clip_area.refresh_clip(new_clip.id)
            else:
                tr = next((t for t in self._session.tracks
                           if t.id == str(cd.get("track_id") or "")), None)
                if tr is None:
                    tr = next((t for t in self._session.tracks
                               if t.name == str(cd.get("track_name") or "")), None)
                if tr is None:
                    tr = Track(id=str(cd.get("track_id") or new_clip.track_id or uuid.uuid4()),
                               name=str(cd.get("track_name") or "Clip 恢复轨"),
                               universes=list(new_clip.clip_universes or [1]))
                    self._session.tracks.append(tr)
                new_clip.track_id = tr.id
                tr.clips.append(new_clip)
                target = tr
                self._reload_tracks()
            self._invalidate_engine_cache(new_clip.id)
            for view in self._iter_clip_views():
                if getattr(view, "track", None) is target and hasattr(view, "set_selected_ids"):
                    view.set_selected_ids({new_clip.id})
            self.report_to_bus(force=True)
            return True
        except Exception:
            return False


def _write_edges(ctrl, target_ids, *, in_key: str, want_in,
                 out_key: str, want_out) -> bool:
    """对选中集逐 clip 写**指定的两侧**（None = 该侧保持原值）；返回是否有改动。

    ★ R135：多选提交的共用实现 —— 淡入淡出与序列延迟两个槽都用它。`want_*` 为 None
      表示"本次不改这一侧"（发射端用 NaN 哨兵表达，见 `clip_view._dragged_edge_pair`），
      这样"拖 c1 的淡入"不会再污染 c2/c3 的淡出。
    值按 clip 自身 duration 钳制；**值未变不写**（memcmp 纪律：不产生无谓失效/重建）。
    """
    changed = False
    for cid in (target_ids or ()):
        try:
            _, clip = ctrl._find_track_and_clip(cid)
        except Exception:
            continue
        if clip is None:
            continue
        try:
            if _apply_clip_edges(clip, in_key, want_in, out_key, want_out):
                changed = True
        except Exception:
            continue
    return changed


def _apply_clip_edges(clip, in_key: str, want_in, out_key: str, want_out) -> bool:
    """给单个 clip 写两侧（None = 该侧保持原值）；返回**是否真的改了**。

    值按 clip 自身 duration 钳制；值未变不写（memcmp 纪律，见 `_write_edges`）。
    """
    duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
    cur_in = float(getattr(clip, in_key, 0.0) or 0.0)
    cur_out = float(getattr(clip, out_key, 0.0) or 0.0)
    new_in = cur_in if want_in is None else max(0.0, min(float(want_in), duration))
    new_out = cur_out if want_out is None else max(0.0, min(float(want_out), duration))
    if abs(cur_in - new_in) < 1e-6 and abs(cur_out - new_out) < 1e-6:
        return False
    setattr(clip, in_key, new_in)
    setattr(clip, out_key, new_out)
    return True


def _clipboard_key_targets(win) -> list:
    """Ctrl+C / Ctrl+X 的作用目标：当前选中集，无选中则用最近一次上下文 clip。

    ★ R135：键盘复制/剪切原先完全不存在（只有右键菜单），多选后键盘没法用。
      返回 id 列表（可能为空 → 调用方据此不发信号）。
    """
    try:
        ids = list(win._selected_clip_ids() or [])
    except Exception:
        ids = []
    if ids:
        return ids
    last = str(getattr(win, "_last_context_clip_id", "") or "")
    return [last] if last else []


def _pair_is_transitionable(ca, cb, max_gap: float) -> bool:
    """`ca → cb` 是否算"可过渡的一对"（按时间关系；不含"已有手动参数"的判定）。

    ★★ R134 用户实报（"新 clip 放在老 clip 结束之后就没有淡入效果，直接到对应角度"）：
      原先判据是 `abs(gap) > 0.05` → **间隔 >50ms 就不配对**，于是自动过渡
      （`_on_clip_optimize_transitions`）从不给新 clip 写 `fade_in`。
      语义上：**"紧接"不是过渡的必要条件** —— 上一个动作结束后设备停在末值，新动作
      从该末值过渡过去，间隔多长都成立（间隔只决定"中间那段空档"长什么样）。
      故按"最近的前一个动作"配对：`-0.05 <= gap <= max_gap` 即成立
      （负 gap = 重叠；重叠过多说明不是前后关系，沿用旧容差）。
    """
    gap = float(getattr(cb, "start_time", 0.0) or 0.0) - float(ca.end_time())
    return -0.05 <= gap <= float(max_gap)


def _clamp_clip_timing(clip, duration: float) -> None:
    """只保证四个翼**非负**；**不再**把它们裁到新时长（R163 用户定案）。

    ★ R163：用户规则"过渡翼和延迟翼是一切效果延迟的最终管理" —— 时长与翼互相
    独立，翼才是过渡/相位的权威。旧写法 `min(翼, duration)` 在缩短时**永久**
    截短翼（拉长不恢复）→ "clip 总长决定效果淡入最终时间"（用户报的灰色地带）。
    翼装不下时由引擎渲染期临时压紧（`_clamp_window_to_duration`：不改字段、
    拉长即自动恢复）。

    `duration` 参数保留以维持调用点签名不变。
    """
    del duration  # 语义上不再参与钳制（见 docstring）
    clip.fade_in = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
    clip.fade_out = max(0.0, float(getattr(clip, "fade_out", 0.0) or 0.0))
    clip.seq_delay_in = max(0.0, float(getattr(clip, "seq_delay_in", 0.0) or 0.0))
    clip.seq_delay_out = max(0.0, float(getattr(clip, "seq_delay_out", 0.0) or 0.0))


# ── 驱动层：释放（调度台"关闭场景" / 取消激活）────────────────────────────
def _timeline_dispatch_release_state(panel, node: dict, state=None) -> bool:
    """释放某 clip 节点：停止该 clip 的输出并清掉 clip 层。

    ★★ 2026-09-17 深查修正（关闭场景后 clip 照播）：路由表里 clip 面板取的是
      `getattr(w, "dispatch_release_state", None)` —— 本类**只有** capture/recall，
      于是该值为 None → `release_one_node` 走 "面板未实现 release 视为成功" 分支
      返回 True → 关闭含 clip 节点的场景时 clip 层无人清、照常播放且无任何报错
      （recall 有实现、release 没有，正是既往"消不掉"的根因形态）。
    语义：停掉时间线播放（停生成线程 = 停 clip 写入者）→ 清 clip 三层 →
      刷新走带 UI。不做删除（节点数据仍在会话里，重新调用可再播）。
    """
    try:
        ctrl = getattr(panel, "ctrl", None)
        if ctrl is not None:
            ctrl.stop()          # 停生成线程 = 停 clip 层写入者
        engine = _resolve_panel_engine(panel)
        nm = _resolve_panel_nm(panel, engine)
        if nm is not None:
            for layer in ("clip_playback", "clip_preview", "timeline"):
                try:
                    nm.clear_layer(layer)
                except Exception:
                    pass
        if engine is not None:
            try:
                from ...engine.timeline_runtime_release import release_playback_runtime
                release_playback_runtime(engine)
            except Exception:
                pass
        return True
    except Exception:
        return False


_timeline_dispatch_release_state._dispatch_release_whole_panel = True


def _resolve_panel_engine(panel):
    """面板的 PlaybackEngine（经 `_resolve_engine`，失败给 None）。"""
    try:
        return panel._resolve_engine()
    except Exception:
        return None


def _resolve_panel_nm(panel, engine):
    """取 NetworkManager：优先引擎持有，其次沿父链找 MainWindow。"""
    nm = getattr(engine, "_nm", None) if engine is not None else None
    p = panel.parent() if nm is None else None
    while p is not None and nm is None:
        nm = getattr(p, "_nm", None)
        p = p.parent() if hasattr(p, "parent") else None
    return nm


TimelineWidget.dispatch_release_state = _timeline_dispatch_release_state
