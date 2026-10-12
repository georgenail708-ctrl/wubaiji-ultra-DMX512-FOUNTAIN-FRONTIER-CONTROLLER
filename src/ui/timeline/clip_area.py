"""
Qascade V1.0 — Clip Area  [FIXED v34]
负责时间线核心区域的渲染与交互：
  1. 渲染轨道背景与时间刻度
  2. 管理 ClipView 实例的布局
  3. 处理播放指针 (Playhead) 的同步与绘制
  4. v33: 新增 global_y_to_content_y 坐标转换，修复跨轨拖动
  5. v34: 将播放指针改为独立 overlay widget，确保始终绘制在所有
         ClipView 子控件之上，解决指针被轨道/Clip 遮挡的问题。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollBar,
    QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QPoint, QRect
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPaintEvent, QWheelEvent

from .. import qascade_theme as _th
from .clip_view import ClipView, ToolMode
from .audio_mod_lane import AudioModLaneView
from .timeline_perf import refresh_owning_views

SCROLLBAR_SIZE = 12
DEBUG = False

DEFAULT_TRACK_HEIGHT = 64
TRACK_GAP = 4


def _track_height(track=None) -> int:
    if track is None:
        return DEFAULT_TRACK_HEIGHT
    h = getattr(track, "height", DEFAULT_TRACK_HEIGHT)
    return int(h) if h else DEFAULT_TRACK_HEIGHT


def _view_kind_changed(v, track) -> bool:
    """R179：视图类型与轨道类型不匹配（调制轨↔普通轨互转）→ 需重建视图。"""
    return isinstance(v, AudioModLaneView) != bool(getattr(track, "is_audio_mod", False))


def _connect_if_hasattr(src, attr: str, dst_sig) -> None:
    """src 上存在信号 attr 才连接到 dst_sig（老视图/容器缺该信号时安全跳过）。"""
    if hasattr(src, attr):
        getattr(src, attr).connect(dst_sig)


# ──────────────────────────────────────────────────────────────────────────────
#  _PlayheadOverlay  ← 新增：透明 overlay，专门负责绘制播放指针
#  它作为 _TrackContainer 的子控件，但始终保持在最顶层（raise_()）
# ──────────────────────────────────────────────────────────────────────────────
class _PlayheadOverlay(QWidget):
    """透明覆盖层，仅绘制播放指针竖线和三角形箭头。

    - 背景完全透明，鼠标事件全部穿透到下层控件。
    - 始终与父控件（_TrackContainer）等大，并在父控件 resizeEvent 中同步。
    - 通过 raise_() 确保 Z-order 最高。
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._position: float = 0.0
        self._prev_position_px: int = -1
        self._px_per_sec: float = 100.0
        self._rubberband_rect: QRect | None = None
        #: T1 吸附参考线（秒；None = 不画）——由 ClipItem 拖动/裁剪命中时写入。
        self._snap_guide_t = None
        # 透明背景，鼠标事件穿透
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_playhead(self, pos: float):
        """B3: dirty-rect invalidation — only repaint 4px-wide strips around playhead."""
        old_px = int(self._position * self._px_per_sec) if self._px_per_sec > 0 else -1
        self._position = pos
        new_px = int(self._position * self._px_per_sec) if self._px_per_sec > 0 else -1
        # Only invalidate the narrow region around old + new playhead positions.
        # The polygon triangle at the top extends 6px left/right from the line.
        margin = 8
        rects = QRect()
        for px in {old_px, new_px}:
            if px >= 0:
                rects = rects.united(QRect(px - margin, 0, margin * 2, self.height()))
        if not rects.isNull():
            self.update(rects.normalized())

    def set_px_per_sec(self, px: float):
        self._px_per_sec = px
        self.update()

    def set_snap_guide(self, t) -> None:
        """T1 吸附参考线：网格命中位置（秒；None = 收线）画成跨轨竖线。

        必须画在这一层：参考线要**贯穿整个内容区**，而 ClipItem 是单轨子控件
        画不出跨轨长线；覆盖层本就是"画在所有 clip 之上"的那一层（v34 播放头
        架构），语义正好一致。
        """
        self._snap_guide_t = None if t is None else max(0.0, float(t))
        self.update()

    def set_rubberband_rect(self, rect: QRect | None):
        self._rubberband_rect = QRect(rect) if rect is not None and not rect.isNull() else None
        self.update()

    def paintEvent(self, event: QPaintEvent):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        if self._rubberband_rect is not None:
            rb = self._rubberband_rect.normalized()
            # D5: 橡皮筋选区=select_blue 半透填充+虚线描边（令牌引用，值不变）
            rb_fill = QColor(_th.SELECT_BLUE)
            rb_fill.setAlpha(42)
            p.fillRect(rb, rb_fill)
            p.setPen(QPen(QColor(_th.SELECT_BLUE), 1, Qt.PenStyle.DashLine))
            p.drawRect(rb)

        if self._px_per_sec <= 0:
            p.end()
            return
        # ★ T1 吸附参考线（画在播放头之下：它是编辑辅助，不该压过走带指针）
        if self._snap_guide_t is not None:
            gx = int(self._snap_guide_t * self._px_per_sec)
            p.setPen(QPen(QColor(_th.FOCUS_BLUE), 1, Qt.PenStyle.DashLine))
            p.drawLine(gx, 0, gx, self.height())
        px = int(self._position * self._px_per_sec)
        # 竖线
        p.setPen(QPen(QColor("#FFA028"), 1.5))  # active_orange 播放头（与 timeline_ruler.py 同步）
        p.drawLine(px, 0, px, self.height())
        # 顶部三角
        p.setBrush(QBrush(QColor("#FFA028")))  # active_orange（与 timeline_ruler.py 同步）
        p.setPen(Qt.PenStyle.NoPen)
        tri = [QPointF(px - 6, 0), QPointF(px + 6, 0), QPointF(px, 10)]
        p.drawPolygon(tri)
        p.end()


# ──────────────────────────────────────────────────────────────────────────────
#  _TrackContainer
# ──────────────────────────────────────────────────────────────────────────────
class _TrackContainer(QWidget):
    clip_move_commit     = pyqtSignal(str, float)
    clip_deleted         = pyqtSignal(str)
    clip_duplicated      = pyqtSignal(str)
    clip_cut             = pyqtSignal(str)
    clip_copy            = pyqtSignal(str)
    clip_resize_commit   = pyqtSignal(str, float)
    clip_fade_commit     = pyqtSignal(str, float, float)
    clip_seq_delay_commit = pyqtSignal(str, float, float)
    clip_dir_fade_set    = pyqtSignal(str, str, str, int, str, float, int, list)  # clip_id, in_dir, out_dir, groups, mode, pause, batches, pauses
    clip_auto_smooth    = pyqtSignal(str, str)
    clip_light_tracking = pyqtSignal(str, bool, int, float, float, str, str, int)  # clip_id, enabled, threshold, attack, release, color, source, max_brightness
    clip_hold_toggled = pyqtSignal(str, bool)
    clip_hold_mode_changed = pyqtSignal(str, str)
    clip_speed_changed = pyqtSignal(str, float)
    clip_to_processor    = pyqtSignal(object)
    clip_razor_cut       = pyqtSignal(str, float)
    cross_track_drag     = pyqtSignal(str, int, float)
    cross_track_drag_end = pyqtSignal(str, int, float)
    clip_selected        = pyqtSignal(str, bool, bool)  # clip_id, shift, ctrl
    clip_motor_force     = pyqtSignal(str, str)                        # clip_id, force_preset
    clip_optimize_transitions = pyqtSignal(list)                       # [clip_id, ...]
    clip_spatial_scope_set = pyqtSignal(str, str)                      # clip_id, per_shape/global
    clip_skip_base_order_set = pyqtSignal(str, bool)                   # clip_id, skip_base_order（#18 跳过编号排序）
    clip_transition_strategy_set = pyqtSignal(str, str, str)           # clip_id, edge(in/out), strategy_id（淡入/淡出分开设置）
    clip_dir_edge_set = pyqtSignal(str, str)                         # clip_id, edge(both/in/out)（分组方向过渡应用边）
    clip_load_fixture_group = pyqtSignal(object)                     # 加载该 Clip 设备组（右键工具菜单）
    clip_record_output_source_changed = pyqtSignal(str, bool)          # clip_id, enabled
    clip_motor_transition_phase_changed = pyqtSignal(str, bool, float) # clip_id, enabled, amount
    clip_precharge_changed = pyqtSignal(str, bool, float, int)         # clip_id, enabled, lead_ms, level
    clip_open_processing_studio = pyqtSignal(str)                      # clip_id（R172：转发自 ClipView，打开 Studio）

    def __init__(self, px_per_sec: float, parent=None):
        super().__init__(parent)
        self._px_per_sec = px_per_sec
        self._position   = 0.0
        self._track_ids: list[str] = []
        self._tracks_ref: list = []
        self._views: list[ClipView] = []
        self._rubberband_origin: QPoint | None = None
        self._rubberband_rect: QRect | None = None
        self._rubberband_additive: bool = False
        self._rubberband_base_selection: dict[str, set[str]] = {}
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ★ 创建播放指针 overlay，作为子控件但始终保持最高 Z-order
        self._playhead_overlay = _PlayheadOverlay(self)
        self._playhead_overlay.set_px_per_sec(px_per_sec)
        self._playhead_overlay.resize(self.size())
        self._playhead_overlay.show()
        self._playhead_overlay.raise_()

    def set_audio_duration(self, dur: float):
        """设置音频参考轨时长，用于计算时间线总宽度。"""
        self._audio_duration = max(0.0, dur)
        self._update_geometry()

    def set_px_per_sec(self, px: float):
        self._px_per_sec = px
        self._playhead_overlay.set_px_per_sec(px)
        for v in self._views:
            v.set_px_per_sec(px)
        self._layout_views()
        self.update()

    def set_playhead(self, pos: float):
        self._position = pos
        self._playhead_overlay.set_playhead(pos)
        # 不再在 paintEvent 里画指针，只更新 overlay
        # 保留 self.update() 以刷新背景（如有需要）

    def set_snap_guide(self, t) -> None:
        """T1 吸附参考线入口（ClipItem 命中网格时调用；None = 收线）。

        转发到播放头覆盖层：参考线要**贯穿整个内容区跨轨**，而 ClipItem 是单轨
        子控件画不出跨轨长线；覆盖层本就是"所有 clip 之上"的那一层（v34 架构）。
        """
        try:
            self._playhead_overlay.set_snap_guide(t)
        except Exception:
            pass

    def refresh_tracks(self, tracks: list):
        new_ids = {t.id for t in tracks}
        old_views = {v.track.id: v for v in self._views if hasattr(v, 'track')}
        for tid, v in list(old_views.items()):
            if tid not in new_ids:
                v.deleteLater()
        self._tracks_ref = list(tracks)
        self._track_ids  = [t.id for t in tracks]
        self._views = [self._rebind_or_create_view(t, old_views) for t in tracks]
        self._layout_views()
        self._update_geometry()
        # ★ 确保 overlay 始终在最顶层
        self._playhead_overlay.raise_()
        self.update()

    def _rebind_or_create_view(self, track, old_views: dict):
        """可复用视图重绑 track 对象（R57）；调制轨↔普通轨类型互转则重建。"""
        v = old_views.pop(track.id, None)
        if v is None:
            return self._make_view_for_track(track)
        if _view_kind_changed(v, track):
            v.deleteLater()
            return self._make_view_for_track(track)
        # ★ R57 修复：复用视图必须重绑 track 对象——undo/换节目会把
        #   project.tracks 整体替换（同 id 不同对象），不重绑则
        #   v._refresh_clips 仍遍历旧轨道的 clip 列表，item._clip 全部
        #   指向游离旧对象：此后的拖拽/菜单编辑写不进真模型（表现为
        #   undo 后拖动"没反应"，松手看似无效）。
        v._track = track
        v._refresh_clips()
        return v

    def _make_view_for_track(self, track):
        """R179：按轨道类型造视图——调制轨 → AudioModLaneView，其余 → ClipView。"""
        if bool(getattr(track, "is_audio_mod", False)):
            v = AudioModLaneView(track, self._px_per_sec, parent=self)
            v.show()
            return v
        v = ClipView(track, self._px_per_sec, parent=self)
        self._wire_view(v)
        v.show()
        return v

    def refresh_clip(self, clip_id: str, reset_waveform: bool = False):
        refresh_owning_views(self, clip_id, reset_waveform=reset_waveform)  # R141

    def get_track_y_map(self) -> dict[str, tuple]:
        result = {}
        y = 0
        for track in self._tracks_ref:
            h = _track_height(track)
            result[track.id] = (y, y + h + TRACK_GAP)
            y += h + TRACK_GAP
        return result

    def _wire_view(self, v: ClipView):
        v.clip_move_commit.connect(self.clip_move_commit)
        v.clip_deleted.connect(self.clip_deleted)
        v.clip_duplicated.connect(self.clip_duplicated)
        v.clip_cut.connect(self.clip_cut)
        v.clip_copy.connect(self.clip_copy)
        v.clip_resize_commit.connect(self.clip_resize_commit)
        v.clip_fade_commit.connect(self.clip_fade_commit)
        v.clip_seq_delay_commit.connect(self.clip_seq_delay_commit)
        v.clip_dir_fade_set.connect(self.clip_dir_fade_set)
        v.clip_auto_smooth.connect(self.clip_auto_smooth)
        v.clip_light_tracking.connect(self.clip_light_tracking)
        if hasattr(v, "clip_hold_toggled"):
            v.clip_hold_toggled.connect(self.clip_hold_toggled)
        if hasattr(v, "clip_hold_mode_changed"):
            v.clip_hold_mode_changed.connect(self.clip_hold_mode_changed)
        if hasattr(v, "clip_speed_changed"):
            v.clip_speed_changed.connect(self.clip_speed_changed)
        v.clip_to_processor.connect(self.clip_to_processor)
        v.clip_razor_cut.connect(self.clip_razor_cut)
        v.cross_track_drag.connect(self.cross_track_drag)
        v.cross_track_drag_end.connect(self.cross_track_drag_end)
        if hasattr(v, "clip_selected"):
            v.clip_selected.connect(self._on_view_clip_selected)
        if hasattr(v, "clip_motor_force"):
            v.clip_motor_force.connect(self.clip_motor_force)
        if hasattr(v, "clip_optimize_transitions"):
            v.clip_optimize_transitions.connect(self.clip_optimize_transitions)
        if hasattr(v, "clip_spatial_scope_set"):
            v.clip_spatial_scope_set.connect(self.clip_spatial_scope_set)
            v.clip_skip_base_order_set.connect(self.clip_skip_base_order_set)
            v.clip_transition_strategy_set.connect(self.clip_transition_strategy_set)
            v.clip_dir_edge_set.connect(self.clip_dir_edge_set)
        if hasattr(v, "clip_load_fixture_group"):
            v.clip_load_fixture_group.connect(self.clip_load_fixture_group)
        if hasattr(v, "clip_record_output_source_changed"):
            v.clip_record_output_source_changed.connect(self.clip_record_output_source_changed)
        if hasattr(v, "clip_motor_transition_phase_changed"):
            v.clip_motor_transition_phase_changed.connect(self.clip_motor_transition_phase_changed)
        _connect_if_hasattr(v, "clip_precharge_changed", self.clip_precharge_changed)
        _connect_if_hasattr(v, "clip_open_processing_studio", self.clip_open_processing_studio)

    def _on_view_clip_selected(self, clip_id: str, shift: bool, ctrl: bool):
        if not (shift or ctrl) and not getattr(self.sender(), "_group_preserve_selection", False):
            source = self.sender()
            for view in self._views:
                if view is not source:
                    view.deselect_all()
        self.clip_selected.emit(clip_id, shift, ctrl)

    def is_rubberband_active(self) -> bool:
        return self._rubberband_origin is not None

    def begin_rubberband(self, pos: QPoint, modifiers=Qt.KeyboardModifier.NoModifier):
        self._rubberband_origin = QPoint(pos)
        self._rubberband_rect = QRect(pos, pos).normalized()
        self._rubberband_additive = bool(
            modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)
        )
        self._rubberband_base_selection = {
            str(getattr(view.track, "id", id(view))): set(getattr(view, "selected_ids", set()) or set())
            for view in self._views
        }
        if not self._rubberband_additive:
            for view in self._views:
                view.deselect_all()
        self._playhead_overlay.set_rubberband_rect(self._rubberband_rect)
        self.update()

    def update_rubberband(self, pos: QPoint):
        if self._rubberband_origin is None:
            return
        self._rubberband_rect = QRect(self._rubberband_origin, pos).normalized()
        self._playhead_overlay.set_rubberband_rect(self._rubberband_rect)
        self._apply_rubberband_selection()

    def end_rubberband(self, pos: QPoint):
        if self._rubberband_origin is None:
            return
        self.update_rubberband(pos)
        selected_ids = self._apply_rubberband_selection()
        self._rubberband_origin = None
        self._rubberband_rect = None
        self._rubberband_base_selection = {}
        self._playhead_overlay.set_rubberband_rect(None)
        if selected_ids:
            self.clip_selected.emit(selected_ids[-1], self._rubberband_additive, self._rubberband_additive)
        self._rubberband_additive = False
        self.update()

    def begin_rubberband_from_view(self, view: ClipView, pos: QPoint, modifiers=Qt.KeyboardModifier.NoModifier):
        self.begin_rubberband(view.mapToParent(pos), modifiers)

    def update_rubberband_from_view(self, view: ClipView, pos: QPoint):
        self.update_rubberband(view.mapToParent(pos))

    def end_rubberband_from_view(self, view: ClipView, pos: QPoint):
        self.end_rubberband(view.mapToParent(pos))

    def _apply_rubberband_selection(self) -> list[str]:
        rb = self._rubberband_rect
        if rb is None:
            return []
        rb = rb.normalized()
        selected_ids: list[str] = []
        for view in self._views:
            base_key = str(getattr(view.track, "id", id(view)))
            base = set(self._rubberband_base_selection.get(base_key, set())) if self._rubberband_additive else set()
            hits: set[str] = set()
            for cid, item in getattr(view, "_items", {}).items():
                item_rect = QRect(item.geometry())
                item_rect.translate(view.pos())
                if rb.intersects(item_rect):
                    hits.add(cid)
            final_ids = base | hits
            view.set_selected_ids(final_ids)
            selected_ids.extend(
                c.id for c in getattr(view.track, "clips", []) if c.id in final_ids
            )
        return selected_ids

    def _layout_views(self):
        y = 0
        for v in self._views:
            h = _track_height(v.track if hasattr(v, 'track') else None)
            v.move(0, y)
            v.resize(self.width(), h + TRACK_GAP)
            y += h + TRACK_GAP
        # ★ overlay 始终铺满整个容器，并保持最高 Z-order
        self._playhead_overlay.resize(self.width(), self.height())
        self._playhead_overlay.raise_()

    def _update_geometry(self):
        total_h = sum(_track_height(t) + TRACK_GAP for t in self._tracks_ref)
        # ★ Bug fix: 动态计算时间线总宽度，取所有 clip 结束时间的最大值 + 余量
        max_end = 60.0  # 最小 60 秒
        for t in self._tracks_ref:
            for c in getattr(t, 'clips', []):
                end = getattr(c, 'start_time', 0) + getattr(c, 'duration', 0)
                if end > max_end:
                    max_end = end
        # 额外加 30% 余量，确保用户能看到末尾之后的空白区域
        max_end *= 1.3
        # 同时考虑音频时长（通过 _audio_duration 属性传入）
        audio_dur = getattr(self, '_audio_duration', 0.0)
        if audio_dur > max_end:
            max_end = audio_dur * 1.1
        total_w = int(max_end * self._px_per_sec)
        self.resize(max(800, total_w), max(400, total_h))
        self._layout_views()
        # 安全地向上找 ClipArea.update_scroll_bars，避免 parent 链路异常导致属性错误
        try:
            parent = self.parent()
            if parent is not None:
                clip_area = parent.parent()
                if clip_area is not None and hasattr(clip_area, "update_scroll_bars"):
                    clip_area.update_scroll_bars()
                # ★ 2026-08-27 修复"折叠残影"：容器缩放后，视口中暴露出来的
                #   区域（容器不再覆盖的部分）必须整体重绘，否则残留折叠前
                #   的旧像素。仅 update() 子容器不够——viewport 也要刷。
                if clip_area is not None:
                    vp = getattr(clip_area, "_viewport", None)
                    if vp is not None and hasattr(vp, "viewport"):
                        vp.viewport().update()
                    clip_area.update()
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_views()
        # ★ 同步 overlay 尺寸并保持最高层
        self._playhead_overlay.resize(self.width(), self.height())
        self._playhead_overlay.raise_()

    def paintEvent(self, event: QPaintEvent):
        p = QPainter(self)
        rect = event.rect()
        p.fillRect(rect, QColor(_th.BG_ROOT))  # bg_root 泳道间底（令牌引用，值不变）— 只填曝光区

        y = 0
        p.setPen(QPen(QColor(255, 255, 255, 21), 1))  # separator 泳道分隔发丝线
        for track in self._tracks_ref:
            th = _track_height(track)
            y += th + TRACK_GAP
            line_y = max(0, y - 1)
            if rect.top() - 1 <= line_y <= rect.bottom() + 1:
                p.drawLine(0, line_y, self.width(), line_y)

        if self._px_per_sec > 0:
            sec_step = 1 if self._px_per_sec >= 20 else 5
            px_step = int(self._px_per_sec * sec_step)
            if px_step > 0:
                p.setPen(QPen(QColor(255, 255, 255, 13), 1))  # 时间竖向网格线
                # 只画曝光区覆盖的竖线：相位对齐 0，保证与全量绘制逐像素一致。
                x0 = max(0, rect.left())
                x1 = min(self.width(), rect.right() + 1)
                x = (x0 // px_step) * px_step
                while x < x0:
                    x += px_step
                while x < x1:
                    p.drawLine(x, 0, x, self.height())
                    x += px_step

        # ★ v34: 指针已移至 _PlayheadOverlay，此处不再重复绘制
        p.end()

    def wheelEvent(self, e: QWheelEvent):
        """将滚轮事件转发给父级 ClipArea 统一处理"""
        # 父级是 _ScrollViewport，父级的父级是 ClipArea
        p = self.parent()
        if p:
            p = p.parent()
        if p and hasattr(p, '_handle_wheel'):
            p._handle_wheel(e)
            e.accept()  # 阻止事件继续冒泡，避免重复触发
        else:
            e.ignore()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.begin_rubberband(e.pos(), e.modifiers())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.is_rubberband_active() and (e.buttons() & Qt.MouseButton.LeftButton):
            self.update_rubberband(e.pos())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.is_rubberband_active():
            self.end_rubberband(e.pos())
            e.accept()
            return
        super().mouseReleaseEvent(e)


# ────────────────────────────────────────────────────────────────────────────────
#  _ScrollViewport
# ──────────────────────────────────────────────────────────────────────────────
class _ScrollViewport(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"background:{_th.BG_ROOT};")  # bg_root
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event):
        # ★ 2026-08-27 修复"时间线背景垃圾区"：容器未覆盖的暴露区此前依赖
        #   WA_StyledBackground 隐式填充，在某些缩放/初始化序列下不执行，
        #   留下未初始化像素（黑块+条纹）。这里显式满幅填充，绝无遗漏。
        from PyQt6.QtGui import QPainter as _QP, QColor as _QC
        p = _QP(self)
        p.fillRect(self.rect(), _QC(_th.BG_ROOT))
        p.end()

    def wheelEvent(self, e: QWheelEvent):
        """将滚轮事件转发给父级 ClipArea 统一处理"""
        p = self.parent()
        if p and hasattr(p, '_handle_wheel'):
            p._handle_wheel(e)
            e.accept()  # 阻止事件继续冒泡，避免重复触发
        else:
            e.ignore()


# ──────────────────────────────────────────────────────────────────────────────
#  ClipArea
# ──────────────────────────────────────────────────────────────────────────────
class ClipArea(QWidget):
    scroll_x_changed     = pyqtSignal(int)
    scroll_y_changed     = pyqtSignal(int)
    clip_move_commit     = pyqtSignal(str, float)
    clip_deleted         = pyqtSignal(str)
    clip_duplicated      = pyqtSignal(str)
    clip_cut             = pyqtSignal(str)
    clip_copy            = pyqtSignal(str)
    clip_resize_commit   = pyqtSignal(str, float)
    clip_fade_commit     = pyqtSignal(str, float, float)
    clip_seq_delay_commit = pyqtSignal(str, float, float)
    clip_dir_fade_set    = pyqtSignal(str, str, str, int, str, float, int, list)  # clip_id, in_dir, out_dir, groups, mode, pause, batches, pauses
    clip_auto_smooth    = pyqtSignal(str, str)
    clip_light_tracking = pyqtSignal(str, bool, int, float, float, str, str, int)  # clip_id, enabled, threshold, attack, release, color, source, max_brightness
    clip_hold_toggled = pyqtSignal(str, bool)
    clip_hold_mode_changed = pyqtSignal(str, str)
    clip_speed_changed = pyqtSignal(str, float)
    clip_to_processor    = pyqtSignal(object)
    clip_razor_cut       = pyqtSignal(str, float)
    cross_track_drag     = pyqtSignal(str, int, float)
    cross_track_drag_end = pyqtSignal(str, int, float)
    zoom_requested       = pyqtSignal(float)   # Alt+滚轮缩放，参数为新 px_per_sec（无锚点，旧接口保留）
    zoom_at_requested    = pyqtSignal(float, float, float)  # 新 px_per_sec, anchor_time, viewport_x（#20 播放指针锚定）
    clip_selected        = pyqtSignal(str, bool, bool)  # clip_id, shift, ctrl
    clip_motor_force     = pyqtSignal(str, str)                        # clip_id, force_preset
    clip_optimize_transitions = pyqtSignal(list)                       # [clip_id, ...]
    clip_spatial_scope_set = pyqtSignal(str, str)                      # clip_id, per_shape/global
    clip_skip_base_order_set = pyqtSignal(str, bool)                   # clip_id, skip_base_order（#18 跳过编号排序）
    clip_transition_strategy_set = pyqtSignal(str, str, str)           # clip_id, edge(in/out), strategy_id（淡入/淡出分开设置）
    clip_dir_edge_set = pyqtSignal(str, str)                         # clip_id, edge(both/in/out)（分组方向过渡应用边）
    clip_load_fixture_group = pyqtSignal(object)                     # 加载该 Clip 设备组（右键工具菜单）
    clip_record_output_source_changed = pyqtSignal(str, bool)          # clip_id, enabled
    clip_motor_transition_phase_changed = pyqtSignal(str, bool, float) # clip_id, enabled, amount
    clip_precharge_changed = pyqtSignal(str, bool, float, int)         # clip_id, enabled, lead_ms, level
    clip_open_processing_studio = pyqtSignal(str)                      # clip_id（R172：转发自 _TrackContainer，打开 Studio）


    def __init__(self, px_per_sec: float, parent=None):
        super().__init__(parent)
        self._px_per_sec = px_per_sec
        self._scroll_x = 0
        self._scroll_y = 0
        self._position = 0.0
        self._ignore_scroll_sync = False
        self._build_ui()
        self._connect_signals()

    def set_playhead(self, pos: float):
        self._position = pos
        self._container.set_playhead(pos)

    def set_snap_guide(self, t) -> None:
        """T1 吸附参考线入口（转发容器 → 播放头覆盖层；None = 收线）。

        ClipItem 的父链查找会命中最先实现本方法的祖先 —— 层级在不同构建路径下
        可能是 ClipArea 或 _TrackContainer，故两处都提供同名转发，接口一致。
        """
        try:
            self._container.set_snap_guide(t)
        except Exception:
            pass

    def set_audio_duration(self, dur: float):
        """设置音频参考轨时长，用于计算时间线总宽度。

        ★ 2026-08-12 修复: 原调 self._update_geometry() 不存在于 ClipArea
        (该方法属 _TrackContainer), AttributeError 被 timeline_widget 的
        try/except 静默吞掉 → 标尺扩充而 clip 容器从不扩充 = 残缺时间线。
        转发到容器让真实扩容生效。
        """
        self._audio_duration = max(0.0, dur)
        if hasattr(self._container, "set_audio_duration"):
            self._container.set_audio_duration(dur)

    def set_px_per_sec(self, px: float):
        self._px_per_sec = px
        self._container.set_px_per_sec(px)
        # ★ 2026-08-27 修复（用户点名）：缩放后重算容器总宽/滚动范围。
        #   缺这步时 set_scroll_x 被旧范围钳位 → 锚点漂移、右侧不可达，
        #   直到 solo/mute 触发全量刷新才"追认"（渲染不实时）。
        #   _update_geometry 内含 resize+_layout_views+update_scroll_bars 全链路。
        self._container._update_geometry()
        self.update()

    def set_scroll_x(self, x: int):
        """缩放锚点联动（#21）：设置水平滚动位置；valueChanged → _on_hscroll
        更新视图并广播 scroll_x_changed 联动 ruler/audio。setValue 自带范围钳位。
        """
        self.scroll_bar_h.setValue(int(x))

    def refresh_tracks(self, tracks: list):
        self._container.refresh_tracks(tracks)

    def refresh_clip(self, clip_id: str, reset_waveform: bool = False):
        self._container.refresh_clip(clip_id, reset_waveform=reset_waveform)

    def get_track_y_map(self) -> dict[str, tuple]:
        return self._container.get_track_y_map()

    def global_y_to_content_y(self, global_y: int) -> int:
        """把屏幕全局 y 坐标转换成 _TrackContainer 内容区坐标"""
        return self._container.mapFromGlobal(QPoint(0, global_y)).y()

    def sync_scroll_y(self, y: int):
        self._ignore_scroll_sync = True
        self._scroll_bar_v.setValue(y)
        self._ignore_scroll_sync = False

    def _build_ui(self):
        # 防重入保护：确保 _build_ui 只执行一次，避免滚动条叠加显示
        if getattr(self, '_built', False):
            return
        self._built = True

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 2026-08-27 换肤：背景对齐 bg_root；原内联 QScrollBar 完整样式
        # （#1f1f1f/#555555/#6a6a6a 三段轨道+把手+角点）与全局主题重复，按 §3 删除，
        # 统一走 GLOBAL_QSS 的 QScrollBar 四态样式。
        self.setStyleSheet("""
            ClipArea { background:#1D1D1D; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        mid = QHBoxLayout()
        mid.setSpacing(0)
        mid.setContentsMargins(0, 0, 0, 0)

        self._viewport = _ScrollViewport(parent=self)
        mid.addWidget(self._viewport)

        self._scroll_bar_v = QScrollBar(Qt.Orientation.Vertical, self)
        self._scroll_bar_v.setFixedWidth(SCROLLBAR_SIZE)
        self._scroll_bar_v.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        mid.addWidget(self._scroll_bar_v)
        root.addLayout(mid, stretch=1)

        self.scroll_bar_h = QScrollBar(Qt.Orientation.Horizontal, self)
        self.scroll_bar_h.setFixedHeight(SCROLLBAR_SIZE)
        self.scroll_bar_h.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        root.addWidget(self.scroll_bar_h)

        self._container = _TrackContainer(self._px_per_sec, parent=self._viewport)
        self._container.move(0, 0)

    def update_scroll_bars(self):
        cw, ch = self._container.width(), self._container.height()
        vw, vh = self._viewport.width(), self._viewport.height()
        h_keep = self.scroll_bar_h.value()
        v_keep = self._scroll_bar_v.value()
        h_max = max(0, cw - vw)
        v_max = max(0, ch - vh)
        self.scroll_bar_h.setRange(0, h_max)
        self.scroll_bar_h.setPageStep(vw)
        self._scroll_bar_v.setRange(0, v_max)
        self._scroll_bar_v.setPageStep(vh)
        # ★ 2026-08-12 修复: setRange 会把 value 钳到新范围(自动扩充/重建时
        #   容器宽度读取滞后 → scroll 被重置 → 播放指针屏幕位置被覆写,
        #   缩放锚点随之失效)。恢复原值(仅当未超出新范围)。
        self.scroll_bar_h.setValue(min(h_keep, h_max))
        self._scroll_bar_v.setValue(min(v_keep, v_max))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scroll_bars()
        # ★ 残影防御：任一尺寸变化都让视口与容器全量重绘一次
        try:
            self._viewport.viewport().update() if hasattr(self._viewport, "viewport")                 else self._viewport.update()
            self._container.update()
            self.update()
        except Exception:
            pass

    def _connect_signals(self):
        self.scroll_bar_h.valueChanged.connect(self._on_hscroll)
        self._scroll_bar_v.valueChanged.connect(self._on_vscroll)
        c = self._container
        c.clip_move_commit.connect(self.clip_move_commit)
        c.clip_deleted.connect(self.clip_deleted)
        c.clip_duplicated.connect(self.clip_duplicated)
        c.clip_cut.connect(self.clip_cut)
        c.clip_copy.connect(self.clip_copy)
        c.clip_resize_commit.connect(self.clip_resize_commit)
        c.clip_fade_commit.connect(self.clip_fade_commit)
        c.clip_seq_delay_commit.connect(self.clip_seq_delay_commit)
        c.clip_dir_fade_set.connect(self.clip_dir_fade_set)
        c.clip_auto_smooth.connect(self.clip_auto_smooth)
        c.clip_light_tracking.connect(self.clip_light_tracking)
        if hasattr(c, "clip_hold_toggled"):
            c.clip_hold_toggled.connect(self.clip_hold_toggled)
        if hasattr(c, "clip_hold_mode_changed"):
            c.clip_hold_mode_changed.connect(self.clip_hold_mode_changed)
        if hasattr(c, "clip_speed_changed"):
            c.clip_speed_changed.connect(self.clip_speed_changed)
        c.clip_to_processor.connect(self.clip_to_processor)
        c.clip_razor_cut.connect(self.clip_razor_cut)
        c.cross_track_drag.connect(self.cross_track_drag)
        c.cross_track_drag_end.connect(self.cross_track_drag_end)
        if hasattr(c, "clip_selected"):
            c.clip_selected.connect(self.clip_selected)
        if hasattr(c, "clip_motor_force"):
            c.clip_motor_force.connect(self.clip_motor_force)
        if hasattr(c, "clip_optimize_transitions"):
            c.clip_optimize_transitions.connect(self.clip_optimize_transitions)
        if hasattr(c, "clip_spatial_scope_set"):
            c.clip_spatial_scope_set.connect(self.clip_spatial_scope_set)
        if hasattr(c, "clip_skip_base_order_set"):
            c.clip_skip_base_order_set.connect(self.clip_skip_base_order_set)
        if hasattr(c, "clip_transition_strategy_set"):
            c.clip_transition_strategy_set.connect(self.clip_transition_strategy_set)
        if hasattr(c, "clip_dir_edge_set"):
            c.clip_dir_edge_set.connect(self.clip_dir_edge_set)
        if hasattr(c, "clip_load_fixture_group"):
            c.clip_load_fixture_group.connect(self.clip_load_fixture_group)
        if hasattr(c, "clip_record_output_source_changed"):
            c.clip_record_output_source_changed.connect(self.clip_record_output_source_changed)
        if hasattr(c, "clip_motor_transition_phase_changed"):
            c.clip_motor_transition_phase_changed.connect(self.clip_motor_transition_phase_changed)
        _connect_if_hasattr(c, "clip_precharge_changed", self.clip_precharge_changed)
        _connect_if_hasattr(c, "clip_open_processing_studio", self.clip_open_processing_studio)

    def _on_hscroll(self, val: int):
        self._scroll_x = val
        self._container.move(-val, -self._scroll_y)
        if not self._ignore_scroll_sync:
            self.scroll_x_changed.emit(val)

    def _on_vscroll(self, val: int):
        self._scroll_y = val
        self._container.move(-self._scroll_x, -val)
        if not self._ignore_scroll_sync:
            self.scroll_y_changed.emit(val)

    def wheelEvent(self, e: QWheelEvent):
        self._handle_wheel(e)

    def _handle_wheel(self, e: QWheelEvent):
        """统一处理滚轮事件：
        - Ctrl/Alt + 垂直滚轮 → 缩放时间轴（zoom_requested）
          滚轮一个方向放大，相反方向缩小；支持普通鼠标与触控板滚轮增量。
        - Shift + 垂直滚轮    → 左右滚动（scroll_bar_h）
        - 水平滚轮            → 左右滚动（scroll_bar_h）
        - 普通垂直滚轮        → 上下滚动轨道（_scroll_bar_v）
        """
        delta = e.angleDelta()
        pixel_delta = e.pixelDelta()
        mods  = e.modifiers()
        ctrl  = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt   = bool(mods & Qt.KeyboardModifier.AltModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if ctrl or alt:
            self._wheel_zoom(e, delta, pixel_delta)
        elif shift or (delta.x() != 0 and delta.y() == 0):
            # 左右滚动
            dx = -(delta.x() if delta.x() != 0 else delta.y())
            step = max(1, self.scroll_bar_h.pageStep() // 8)
            new_h = max(self.scroll_bar_h.minimum(),
                        min(self.scroll_bar_h.maximum(),
                            self.scroll_bar_h.value() + (dx // 3 or (step if dx > 0 else -step))))
            self.scroll_bar_h.setValue(new_h)
            e.accept()
        else:
            # 上下滚动
            dy = -delta.y()
            step = max(1, self._scroll_bar_v.pageStep() // 8)
            new_v = max(self._scroll_bar_v.minimum(),
                        min(self._scroll_bar_v.maximum(),
                            self._scroll_bar_v.value() + (dy // 3 or (step if dy > 0 else -step))))
            self._scroll_bar_v.setValue(new_v)
            e.accept()

    def _wheel_zoom(self, e, delta, pixel_delta) -> None:
        """Ctrl/Alt + 滚轮 → 双向缩放（锚点 = 播放指针，指针在屏幕上保持不动）。

        从 `_handle_wheel` 外提（该函数在架构基线里 49 行、门禁只许缩小）：
        angleDelta 通常以 120 为一格；触控板可能只有 pixelDelta，故两者做兼容。
        """
        dy = delta.y() if delta.y() != 0 else pixel_delta.y()
        if dy == 0:
            e.accept()
            return
        steps = dy / 120.0 if abs(dy) >= 120 else (1.0 if dy > 0 else -1.0)
        from .timeline_zoom import clamp_zoom       # R183 T4：上限唯一真源
        new_zoom = clamp_zoom(self._px_per_sec * (1.12 ** steps))
        anchor_time = float(getattr(self, "_position", 0.0) or 0.0)
        play_x = anchor_time * self._px_per_sec - float(self.scroll_bar_h.value())
        self.zoom_at_requested.emit(new_zoom, anchor_time, play_x)
        e.accept()
