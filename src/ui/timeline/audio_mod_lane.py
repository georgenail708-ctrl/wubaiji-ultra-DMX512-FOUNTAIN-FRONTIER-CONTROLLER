# -*- coding: utf-8 -*-
"""音频调制轨行内画布 + 右键菜单（R179 调制轨升级；R183 M1 拖动语义）。

架构决策：调制轨成为**真正的 Track**（`Track.is_audio_mod`，可添加/可拖动
排序），行内渲染走 ClipArea 的轨道行——本文件的 `AudioModLaneView` 即该行
的交互面；卡片绘制拆在同目录 `audio_mod_paint.py`（本文件 500 行门禁）；
`audio_mod_track.AudioModTrack` 为共享状态真源持有者（R183 A7 起**无控件
面**）。菜单与行内画布同源（`open_mod_context_menu`），clips 结构保持兼容。

交互：
  - 画笔工具（ToolMode.PEN）左键拖动=新建调制 clip（≥0.05s、音频时长截断）；
  - SELECT 工具（M1）：卡体拖动=平移、左右缘(±6px)=缩放、两端 fade 手柄
    (±5px)=per-clip 淡入/淡出；拖期只改本地预览，松手经编辑器写口提交；
  - 右键=区段菜单；左双击=编辑大页（秒位沿链）。

状态真源 = `Project.audio_modulation_state`（schema 见 engine/audio_modulation.py）。
"""
from __future__ import annotations
from .audio_mod_menu_text import decorate_mod_menu, wave_menu_text

from typing import Optional

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QCursor, QPainter
from PyQt6.QtWidgets import QToolTip, QWidget

from ...engine import audio_modulation as _mod
from .audio_mod_paint import paint_mod_surface, MOD_HINT_NO_AUDIO  # noqa: F401  （再导出，旧 import 路径不变）
from .clip_view import ToolMode, get_tool_mode
from .sticky_menu import StickyMenu, add_info_line, bind_mark

MOD_TRACK_NAME = "音频调制"

# R183 成熟化轮③：补 Shark Tooth/S&H/Wander（引擎 WAVE_FUNCS 成熟化轮②已支持，
# 菜单口径归一；顺序照 draw_cards.WAVE_CHOICES）
WAVES = ("", "Sine", "Square", "Sawtooth", "Triangle", "Cosine", "Bounce",
         "Expo", "Random", "Shark Tooth", "S&H", "Wander")

# ── M1 SELECT 拖动手势热区（抄 clip_view._handle_hit 常量语义）───────────────
_LANE_RESIZE_PX = 6     # 卡体左右缘 resize 热区（±6px）
_LANE_FADE_PX = 5       # 两端 fade 手柄热区（±5px；位于 start+fade_in/端内）


def _pen_draw_active() -> bool:
    """画笔工具是否激活（调制面左键拖动画 clip 的开关；用户规格 ②）。"""
    return get_tool_mode() == ToolMode.PEN


def _clip_fades(state: dict, clip: dict) -> tuple:
    """clip 生效淡入/淡出秒（per-clip 非负覆盖全局；哨兵 FADE_INHERIT → 全局）。"""
    g_att = max(0.0, float(state.get("attack", 0.05) or 0.0))
    g_rel = max(0.0, float(state.get("release", 0.2) or 0.0))
    fi = _mod._effective_fade(clip.get("fade_in"), g_att)
    fo = _mod._effective_fade(clip.get("fade_out"), g_rel)
    return fi, fo


def _hit_fade(x: int, clips: list, st: dict, px: float) -> tuple:
    """fade 手柄命中（±5px @ start+fade_in / end-fade_out）→ (mode, clip)。"""
    for clip in clips:
        x0, x1 = float(clip["start"]) * px, float(clip["end"]) * px
        fi, fo = _clip_fades(st, clip)
        if abs(x - (x0 + fi * px)) <= _LANE_FADE_PX:
            return "fade_in", clip
        if abs(x - (x1 - fo * px)) <= _LANE_FADE_PX:
            return "fade_out", clip
    return None, None


def _hit_edge(x: int, clips: list, px: float) -> tuple:
    """左右缘 resize 命中（±6px @ 卡缘）→ (mode, clip)。"""
    for clip in clips:
        x0, x1 = float(clip["start"]) * px, float(clip["end"]) * px
        if abs(x - x0) <= _LANE_RESIZE_PX:
            return "resize_l", clip
        if abs(x - x1) <= _LANE_RESIZE_PX:
            return "resize_r", clip
    return None, None


def make_audio_mod_track():
    """调制轨工厂：真正的 Track（is_audio_mod=True），经既有拖拽机制排序。"""
    from ...core.models import Track
    return Track(name=MOD_TRACK_NAME, is_audio_mod=True, color="#56B65B")


def open_mod_context_menu(owner, canvas, ev) -> None:
    """调制画布右键菜单（列表式；删除/清空/淡入淡出/波形/scope 摘要 + M1 per-clip）。

    `owner` = AudioModTrack（状态真源持有者）；`canvas` 需提供 `_x_to_sec`。
    R183 A7：底部「清空」按钮随编辑器移除 → 批量清空入口落在这里（清空全部区段）。
    M1：波形子菜单走 `bind_mark`（常开菜单勾选高亮，sticky_menu R146 语义）；
    新增 per-clip 条目（此区段淡入/淡出时长、拆分于此点）。
    """
    menu = StickyMenu("")
    t = canvas._x_to_sec(int(ev.pos().x()))
    clip = owner.clip_at(t)
    n_clips = len(owner._state.get("clips") or [])
    _add_clip_actions(menu, owner, clip, n_clips, t)
    _add_global_entries(menu, owner)
    decorate_mod_menu(menu)             # B5：tabler 图标 + 危险项红色
    menu.exec(ev.globalPos())


def _add_clip_actions(menu, owner, clip, n_clips: int, t: float) -> None:
    """区段级条目（删除/清空/per-clip 淡入淡出/拆分；命中区段才出 per-clip 段）。"""
    if clip is not None:
        menu.addAction(f"删除此区段（{clip['start']:.2f}s–{clip['end']:.2f}s）",
                       lambda: owner.delete_clip_range(clip))
    if n_clips:
        menu.addAction(f"清空全部区段（{n_clips} 段）", owner.clear_clips)
    if clip is None:
        return
    menu.addSeparator()
    _add_clip_fade_entries(menu, owner, clip)
    split = menu.addAction("拆分于此点",
                           lambda c=dict(clip), at=float(t): owner.split_clip_at(c, at))
    split.setEnabled(clip["start"] + 0.1 < t < clip["end"] - 0.1)
    menu.addSeparator()


def _add_global_entries(menu, owner) -> None:
    """全局条目（全局包络淡入淡出 / 波形 bind_mark / scope 摘要行）。"""
    fade_menu = menu.addMenu("淡入淡出（全局包络）")
    for label, key in (("淡入时长…", "attack"), ("淡出时长…", "release")):
        fade_menu.addAction(label, lambda k=key: owner.ask_edit_fade(k))
    menu.addSeparator()
    wf = menu.addMenu("波形")
    for wave in WAVES:
        txt = wave_menu_text(wave)      # B5：Draw 同源标签 + & 转义
        act = wf.addAction(txt, lambda w=wave: owner.set_default_wave(w))
        _bind_wave_mark(act, owner, wave)
    menu.addSeparator()
    add_info_line(menu, owner.scope_summary())


def _bind_wave_mark(act, owner, wave: str) -> None:
    """波形条目勾选判据登记（判据从状态真源现取，refresh_states 重算不骗人）。"""
    bind_mark(act, lambda: str(owner._state.get("default_wave", "") or "") == wave)


def _add_clip_fade_entries(menu, owner, clip: dict) -> None:
    """per-clip 淡入/淡出条目（显示生效值：per-clip 未设 → 全局值兜底展示）。"""
    fi, fo = _clip_fades(owner._state, clip)
    sub = menu.addMenu(f"此区段淡入淡出（{clip['start']:.2f}s–{clip['end']:.2f}s）")
    sub.addAction(f"淡入时长…（当前 {fi:.2f}s）",
                  lambda c=dict(clip): owner.ask_edit_clip_fade(c, "in"))
    sub.addAction(f"淡出时长…（当前 {fo:.2f}s）",
                  lambda c=dict(clip): owner.ask_edit_clip_fade(c, "out"))


# ════════════════════════════════════════════════════════════════════════════════
#  R179 调制轨行内画布：调制轨成为真正 Track 后，ClipArea 轨道行的渲染/交互面
# ════════════════════════════════════════════════════════════════════════════════
class AudioModLaneView(QWidget):
    """调制轨的轨道行画布（_TrackContainer 渲染 is_audio_mod 轨用）。

    与 ClipView 同形的最小兼容面（容器/工具遍历鸭子类型）：
      `track` 属性 / `_items`（恒空）/ `_refresh_clips` / `refresh_geometry_only` /
      `deselect_all` / `set_selected_ids` / `set_px_per_sec`。
    交互（用户规格 ②）：PEN 工具左键拖动=画调制 clip（复用共享编辑器
    `commit_clip_span`：≥0.05s、音频时长截断）；右键=区段菜单；左双击=编辑大页。
    状态真源 = 绑定的 AudioModTrack（timeline_widget 注入），绘制数据实时拉取。
    """

    def __init__(self, track, px_per_sec: float, parent=None):
        super().__init__(parent)
        self._track = track
        self._px_per_sec = max(1.0, float(px_per_sec or 100.0))
        self._editor = None                     # AudioModTrack（timeline 注入）
        self._items: dict = {}                  # 与 ClipView 同形（空实现）
        self._drag_start_x: Optional[int] = None
        self._drag_cur_x: Optional[int] = None
        self._hover_span: Optional[tuple] = None   # 悬停区段高亮（R182）
        self._editor_wired = False
        # ── M1 SELECT 拖动状态机（audio_ref_track._drag_mode 同族槽位）──────
        self._sel_span: Optional[tuple] = None     # 选中 clip（(start,end) 键）
        self._drag_mode: Optional[str] = None      # move/resize_l/resize_r/fade_*
        self._drag_clip: Optional[dict] = None     # 手势起点 clip 快照（定位键）
        self._drag_origin_x: int = 0
        self._drag_moved: bool = False
        self._drag_val: float = 0.0                # 被拖值实时预览（提交用）
        self._drag_preview: Optional[tuple] = None # 预览 span 秒 (start,end)
        self._drag_start_val: float = 0.0
        self._pre_snapped: bool = False
        self.setMouseTracking(True)

    # ── ClipView 同形兼容面 ─────────────────────────────────────────────
    @property
    def track(self):
        return self._track

    def bind_editor(self, editor) -> None:
        """绑定共享编辑器（状态真源；timeline_widget 在轨道刷新后注入）。"""
        self._editor = editor
        if editor is not None and not self._editor_wired:
            editor.state_changed.connect(self.update)
            self._editor_wired = True
        self.update()

    def set_px_per_sec(self, px: float) -> None:
        self._px_per_sec = max(1.0, float(px or 100.0))
        self.update()

    def _refresh_clips(self) -> None:
        pass                        # 状态真源在编辑器，无需 clip 级刷新

    def refresh_geometry_only(self, *_args, **_kwargs) -> None:
        pass                        # 无 ClipItem 几何可刷

    def deselect_all(self) -> None:
        """清选中（M1：lane 自持选中态；无选中零噪声）。"""
        if self._sel_span is not None:
            self._sel_span = None
            self.update()

    def set_selected_ids(self, _ids) -> None:
        pass                        # 无 ClipItem id 体系（选中走 _sel_span）

    # ── 绘制数据 ────────────────────────────────────────────────────────
    def _x_to_sec(self, x) -> float:
        px = self._editor._px_per_sec if self._editor is not None else self._px_per_sec
        return max(0.0, float(x) / max(1.0, float(px)))

    def _pps(self) -> float:
        """生效像素/秒（编辑器持真源；缺编辑器退本行值）。"""
        return (max(1.0, float(self._editor._px_per_sec))
                if self._editor is not None else self._px_per_sec)

    def mod_paint_state(self):
        """`paint_mod_surface` 数据源：编辑器实时拉取 + 拖拽预览 + 选中/悬停。

        drag 槽（第 4 元）两来源：PEN 画笔拖拽 = 原始像素对；M1 SELECT 拖动 =
        预览 span（秒）× px 合成像素对（同一虚线预览绘制路径）。
        选中槽：`_sel_span` 优先，无选中退悬停 `_hover_span`。
        """
        if self._editor is None:
            return ({}, 0.0, self._px_per_sec, None, self._hover_span)
        sel = self._sel_span if self._sel_span is not None else self._hover_span
        st = self._editor.mod_paint_state(sel)
        px = st[2]
        drag = None
        if self._drag_start_x is not None and self._drag_cur_x is not None:
            drag = (self._drag_start_x, self._drag_cur_x)
        elif self._drag_preview is not None:
            drag = (self._drag_preview[0] * px, self._drag_preview[1] * px)
        if drag is not None:
            st = (st[0], st[1], px, drag, st[4] if len(st) > 4 else None)
        return st

    def paintEvent(self, ev):
        p = QPainter(self)
        try:
            paint_mod_surface(p, self, exposed_rect=ev.rect())
        finally:
            p.end()

    # ── 交互（PEN=画 clip；SELECT=M1 拖动/缩放/fade；右键菜单；双击编辑页）──
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.RightButton and self._editor is not None:
            ev.accept()             # 菜单走 contextMenuEvent（右键释放触发）
            return
        if self._begin_pen_drag(ev):
            ev.accept()
            return
        if self._begin_select_drag(ev):
            ev.accept()
            return
        super().mousePressEvent(ev)

    def _begin_pen_drag(self, ev) -> bool:
        """PEN 左键按下 → 进入画 clip 拖拽（无编辑器/无音频时无效）。"""
        if (ev.button() != Qt.MouseButton.LeftButton or self._editor is None
                or not _pen_draw_active() or self._editor._audio_duration <= 0):
            return False
        self._drag_start_x = int(ev.position().x())
        self._drag_cur_x = self._drag_start_x
        self.update()
        return True

    # ── M1 SELECT 拖动状态机（抄 clip_view press/move/release + 哨兵口径）──
    def _begin_select_drag(self, ev) -> bool:
        """SELECT 左键按下 → 命中即入手势（卡体 move/缘 resize/端手柄 fade）；
        空处点击 = 清选中。拖期只改本地预览，松手走编辑器写口。"""
        if (ev.button() != Qt.MouseButton.LeftButton or self._editor is None
                or _pen_draw_active() or self._editor._audio_duration <= 0):
            return False
        x = int(ev.position().x())
        mode, clip = self._hit_test(x)
        if mode is None:
            self.deselect_all()
            return False
        self._drag_mode = mode
        self._drag_clip = dict(clip)
        self._drag_origin_x = x
        self._drag_moved = False
        self._pre_snapped = False               # R57：首 move 才拍快照
        self._drag_start_val = self._drag_start_value(mode, clip)
        self._drag_val = self._drag_start_val
        self._drag_preview = None
        self._sel_span = (float(clip["start"]), float(clip["end"]))   # 点卡体=选中
        if mode == "move":
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.grabMouse()
        self.update()
        return True

    def _drag_start_value(self, mode: str, clip: dict) -> float:
        """手势起点被拖值的快照（等量基准，防复利漂移——clip_view 同因）。"""
        fi, fo = _clip_fades(self._editor._state, clip)
        return {"move": float(clip["start"]),
                "resize_l": float(clip["start"]),
                "resize_r": float(clip["end"]),
                "fade_in": float(fi),
                "fade_out": float(fo)}.get(mode, 0.0)

    def _hit_test(self, x: int) -> tuple:
        """SELECT 命中 → (mode, clip)：两端 fade 手柄（±5px）> 左右缘 resize
        （±6px）> 卡体 move；无 → (None, None)。手柄位 = start+fade_in /
        end-fade_out（生效值；角柄语义，贴端即 fade 优先）。clip 由
        手柄/缘就近归属（相邻卡贴边时 clip_at 会歧义，不能用落点反查）。"""
        if self._editor is None or self._editor._audio_duration <= 0:
            return None, None
        px = self._pps()
        st = self._editor._state
        clips = [c for c in (st.get("clips") or []) if isinstance(c, dict)]
        hit = _hit_fade(x, clips, st, px)
        if hit[0] is None:
            hit = _hit_edge(x, clips, px)
        if hit[0] is not None:
            return hit
        clip = self._editor.clip_at(self._x_to_sec(x))
        return ("move", clip) if clip is not None else (None, None)

    def _move_select_drag(self, x: int) -> None:
        """拖移：只写本地预览（模型变异在 release 提交）；首 move 先拍快照。"""
        if not self._pre_snapped:
            self._snapshot_pre_drag()
        dx = (x - self._drag_origin_x) / self._pps()
        if abs(x - self._drag_origin_x) > 2:
            self._drag_moved = True
        c = self._drag_clip or {}
        s, e = float(c.get("start", 0.0)), float(c.get("end", 1.0))
        dur = max(0.0, float(self._editor._audio_duration))
        v = self._drag_start_val
        if self._drag_mode == "move":
            v = max(0.0, min(self._drag_start_val + dx, max(0.0, dur - (e - s))))
            self._drag_preview = (v, v + (e - s))
            self._show_drag_tip(f"起点 {v:.2f}s")
        elif self._drag_mode == "resize_l":
            v = max(0.0, min(self._drag_start_val + dx, e - _mod.MIN_CLIP_S))
            self._drag_preview = (v, e)
            self._show_drag_tip(f"起点 {v:.2f}s")
        elif self._drag_mode == "resize_r":
            v = max(s + _mod.MIN_CLIP_S,
                    min(self._drag_start_val + dx, dur) if dur > 0
                    else self._drag_start_val + dx)
            self._drag_preview = (s, v)
            self._show_drag_tip(f"终点 {v:.2f}s")
        else:                               # fade_in / fade_out（单侧，他侧不动）
            lim = e - s
            v = max(0.0, min(self._drag_start_val + dx if self._drag_mode == "fade_in"
                             else self._drag_start_val - dx, lim))
            self._drag_preview = (s, e)
            self._show_drag_tip(f"{'淡入' if self._drag_mode == 'fade_in' else '淡出'} {v:.2f}s")
        self._drag_val = v
        self.update()

    def _finish_select_drag(self) -> None:
        """松手：拖过才提交（写口=AudioModTrack.move/resize/set_clip_fade）。"""
        mode, clip = self._drag_mode, self._drag_clip
        moved, val = self._drag_moved, self._drag_val
        self._drag_mode = None
        self._drag_clip = None
        self._drag_preview = None
        self._drag_moved = False
        self._pre_snapped = False
        QToolTip.hideText()
        from .timeline_drag_tip import hide
        hide(self)
        if mode is not None and clip is not None and moved \
                and self._editor is not None:
            self._commit_select_drag(mode, clip, val)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _commit_select_drag(self, mode: str, clip: dict, val: float) -> None:
        """手势提交分发（`_finish_select_drag` 拍板后走编辑器写口）。"""
        if mode == "move":
            self._editor.move_clip(clip, val)
        elif mode == "resize_l":
            self._editor.resize_clip(clip, val, "start")
        elif mode == "resize_r":
            self._editor.resize_clip(clip, val, "end")
        elif mode in ("fade_in", "fade_out"):
            self._editor.set_clip_fade(clip, "in" if mode == "fade_in" else "out",
                                       val)

    def _find_timeline_root(self):
        """向上查找 TimelineWidget（clip_view._find_timeline_root 同式）。"""
        p = self.parent()
        for _ in range(8):
            if p is None:
                break
            if hasattr(p, "ctrl") and hasattr(p, "_selected_clip_ids"):
                return p
            p = p.parent()
        return None

    def _snapshot_pre_drag(self) -> None:
        """手势起点 pre-state 快照（R57 同式：首 move 才拍，纯点击零成本；
        快照严格先于首个预览写入 → 撤销真正回到拖前状态）。"""
        if self._pre_snapped:
            return
        root = self._find_timeline_root()
        if root is not None and hasattr(root, "_snapshot_for_undo_scoped"):
            root._snapshot_for_undo_scoped()
            self._pre_snapped = True

    def _show_drag_tip(self, text: str) -> None:
        """拖拽数值 tooltip（clip_view D5 同位：光标右下偏移）。"""
        from .timeline_drag_tip import show
        show(self, text)

    def _sync_cursor(self, x: int) -> None:
        """悬停光标：缘/手柄 SizeHor、卡体 OpenHand、PEN Cross、其余 Arrow。"""
        if _pen_draw_active():
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        mode, _clip = self._hit_test(x)
        if mode in ("resize_l", "resize_r", "fade_in", "fade_out"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif mode == "move":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, ev):
        x = int(ev.position().x())
        if self._drag_mode is not None:         # M1 SELECT 手势
            self._move_select_drag(x)
            ev.accept()
            return
        if self._drag_start_x is not None:      # PEN 画笔手势
            self._drag_cur_x = x
            self.update()
            ev.accept()
            return
        self._sync_hover(x)
        self._sync_cursor(x)
        super().mouseMoveEvent(ev)

    def _sync_hover(self, x: int) -> None:
        """悬停区段高亮同步（编辑器缺失/无内容 → None；变化才重绘）。"""
        span = (self._editor.span_at_sec(self._x_to_sec(x))
                if self._editor is not None else None)
        if span != self._hover_span:
            self._hover_span = span
            self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._drag_mode is not None:
            self._finish_select_drag()
            self.releaseMouse()
            ev.accept()
            return
        if ev.button() == Qt.MouseButton.LeftButton and self._drag_start_x is not None:
            self._finish_pen_drag(int(ev.position().x()))
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def _finish_pen_drag(self, release_x: int) -> None:
        x0, x1 = sorted((self._drag_start_x, release_x))
        self._drag_start_x = self._drag_cur_x = None
        if self._editor is not None:
            self._editor.commit_clip_span(self._x_to_sec(x0), self._x_to_sec(x1))
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._editor is not None:
            self._editor.open_editor_requested.emit(
                float(self._x_to_sec(int(ev.position().x()))))
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)

    def contextMenuEvent(self, ev):
        if self._editor is not None:
            open_mod_context_menu(self._editor, self, ev)
            return
        super().contextMenuEvent(ev)
