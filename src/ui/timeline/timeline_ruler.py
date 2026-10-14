from __future__ import annotations

import math

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont,
    QPaintEvent, QMouseEvent, QPolygon, QWheelEvent
)

from .. import qascade_theme as _th
from . import timeline_ruler_paint as _paint

_HANDLE_HIT = 8
DEFAULT_RULER_SECONDS = 600.0
#: ★ R183 T3：每拍至少这么宽才画拍线（Ableton 小节标尺同款分级：太密只画小节线）。
MIN_PX_PER_BEAT = 8.0
#: 小节线也糊了（间隔小于本值）→ 整层不画，交由秒刻度兜底。
_MIN_BAR_PX = 12.0


def _mono_font(px: int) -> QFont:
    """标尺数字：等宽（时间码）10px 起（§1 最小字号允许值）。

    R183 横向性能批：paint 逐帧构造 → _th.cached_font 模块级缓存（共享只读）。
    """
    return _th.cached_font(px, families=_th.MONO_FONT_FAMILIES)


class TimelineRuler(QWidget):
    seek_requested     = pyqtSignal(float)
    loop_range_changed = pyqtSignal(float, float)  # 设计预留, 无消费方
    zoom_requested     = pyqtSignal(float)   # Alt+滚轮缩放，参数为新 px_per_sec
    zoom_at_requested  = pyqtSignal(float, float, float)  # px_per_sec, anchor_time, viewport_x
    #: ★ R183 T2：标记条被改动（新增/改名/改色/移动/删除）——TimelineWidget 据此标脏。
    markers_changed    = pyqtSignal()
    loop_committed     = pyqtSignal(float, float, bool)   # R183 T6：拖动**结束**才提交

    def __init__(self, px_per_sec: float = 100.0, parent=None):
        super().__init__(parent)

        self._px_per_sec = max(1.0, px_per_sec)
        self._position = 0.0
        self._duration = DEFAULT_RULER_SECONDS

        self._loop_start = 0.0
        self._loop_end = 0.0
        self._loop_enabled = False

        #: ★ R183 T3：工程 bpm（`Project.bpm`；0/缺失 = 不画 BPM 网格）。
        self._bpm = 0.0
        #: ★ R183 T2：标记条数据 `[{"t","text","color"}, …]`（数据真源在 Project.markers）。
        self._markers: list = []
        #: 标记色条宽度（px；仅绘制用，与工程数据无关）。
        self._marker_bar_w = 26
        #: 标记拖动状态（按下记下标 + **冻结身份键**；`_marker_drag_moved` 区分
        #: "点击跳转"与"拖动移动"；身份键防拖动跨相邻标记时串台 —— 审计⑦B P2-11）
        self._marker_drag_idx = None
        self._marker_drag_key = None
        self._marker_drag_moved = False
        #: 标记读写的工程/控制器（`set_marker_context` 注入；未注入 = 只读）
        self._session = None
        self._ctrl = None

        self._scroll_x = 0
        self._drag = None
        self._prev_playhead_px: int = -1  # B3: dirty-rect tracking

        self.setFixedHeight(32)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed
        )
        self.setMouseTracking(True)

    # ── ★ R183 T3：BPM 小节网格（工程数据零成本；绘制在 timeline_ruler_paint）──
    def set_bpm(self, bpm) -> None:
        """设置工程 bpm（<=0/非法 → 关闭 BPM 网格，退回纯秒刻度）。"""
        try:
            val = float(bpm)
        except (TypeError, ValueError):
            val = 0.0
        if val != val or val <= 0.0:      # NaN / 非正
            val = 0.0
        if val != self._bpm:
            self._bpm = val
            self.update()

    # ── ★ R183 T2：标记条 ───────────────────────────────────────────────
    def set_markers(self, markers) -> None:
        """注入标记条数据（不解释内容；非法条目剔除，绝不抛）。"""
        clean = []
        for m in (markers or ()):
            if not isinstance(m, dict):
                continue
            try:
                t = max(0.0, float(m.get("t", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
            clean.append({"t": t, "text": str(m.get("text", "") or ""),
                          "color": str(m.get("color", "") or "")})
        clean.sort(key=lambda x: x["t"])
        self._markers = clean
        self.update()

    def markers(self) -> list:
        """当前标记条数据（副本；写入一律走工程 `Project.markers`）。"""
        return [dict(m) for m in self._markers]

    def set_marker_context(self, session, ctrl) -> None:
        """注入标记读写的工程/控制器（TimelineWidget 装配时调）。

        未注入时标记条**只读**（能画，右键菜单/拖动会安全 no-op）——单测直连
        `TimelineRuler` 时不会因为缺 session 而抛。
        """
        self._session = session
        self._ctrl = ctrl

    def marker_at(self, x: float, tol: int = 6):
        """命中标记：返回 `(下标, 标记 dict)`；未命中 → `(None, None)`。

        先按"标记竖线"精确命中（±tol px），再退化为"色条范围"命中 —— 长条标记
        （文字长、色条宽）在条内任意位置都算命中，与 Depence 的"拖条即拖标记"一致。
        """
        if not self._markers:
            return None, None
        near = self._nearest_marker(x, tol)
        return near if near is not None else self._marker_in_bar(x)

    def _nearest_marker(self, x: float, tol: int):
        """竖线命中：返回离 `x` 最近的标记（±tol 内）；无 → None。"""
        best = None
        for i, m in enumerate(self._markers):
            dx = abs(x - self._time_to_px(m["t"]))
            if dx <= tol and (best is None or dx < best[0]):
                best = (dx, i, m)
        return None if best is None else (best[1], best[2])

    def _marker_in_bar(self, x: float):
        """色条范围命中（标记条宽内任意位置）——退路，避免"只点得中竖线"。"""
        width = max(2, int(self._marker_bar_w))
        for i, m in enumerate(self._markers):
            mx = self._time_to_px(m["t"])
            if mx <= x <= mx + width:
                return i, m
        return None, None

    def set_px_per_sec(self, val: float):
        self._px_per_sec = max(1.0, val)
        self.update()

    def set_position(self, pos: float):
        self._position = max(0.0, pos)
        # ★ 2026-08-12 修复: 自扩量化到 5s 粒度。播放中 pos 每 tick(~23ms)增长,
        #   原 pos+120.0 让 _duration 每 tick 变化 → 标尺反复重绘(用户实测
        #   「反复重置绘制残缺时间线」)。量化后每 5s 才扩一次, 与 timeline_widget
        #   的 _sync_timeline_duration 同一粒度, 容器扩容随之触发。
        if self._position + 120.0 > self._duration:
            self._duration = math.ceil(self._position / 5.0) * 5.0 + 120.0
        ph_px = self._time_to_px(self._position)
        # B3: dirty-rect invalidation — only repaint 4px strip around playhead
        invalid = QRect()
        if self._prev_playhead_px >= 0:
            invalid = invalid.united(QRect(self._prev_playhead_px - 4, 0, 8, self.height()))
        if ph_px >= 0:
            invalid = invalid.united(QRect(ph_px - 4, 0, 8, self.height()))
        self._prev_playhead_px = ph_px
        if not invalid.isNull():
            self.update(invalid.normalized())
        else:
            self.update()

    def set_playhead(self, pos: float):
        """timeline_widget.py 调用此方法，与 set_position 等价。"""
        self.set_position(pos)
    def set_duration(self, dur: float):
        self._duration = max(0.1, dur)
        self.update()

    def set_scroll_x(self, x: int):
        self._scroll_x = max(0, int(x))
        self.update()

    def set_loop(self, start: float, end: float, enabled: bool = True):
        """设置循环区（**不**触发 `loop_committed` —— 供工程装载/undo 回填）。

        手势路径（拖动/落位）走鼠标事件 + `loop_committed`；回填路径走本方法，
        两者都只写内存，写工程分别由 widget 的 `_on_loop_committed` 与装载逻辑负责。
        """
        self._loop_start = max(0.0, start)
        self._loop_end = max(0.0, end)
        self._loop_enabled = enabled
        self.update()

    def _time_to_px(self, t: float) -> int:
        return int(t * self._px_per_sec) - self._scroll_x

    def _px_to_time(self, px: int) -> float:
        return max(0.0, (px + self._scroll_x) / self._px_per_sec)

    def _near(self, px1: int, px2: int) -> bool:
        return abs(px1 - px2) <= _HANDLE_HIT

    def _tick_loop_limit(self, w: int, interval: float) -> float:
        """循环上界只到可见区（含 ±80px 缓冲）而非整条时间线：超出可见区的
        刻度原本也会被 paintEvent 里的 `-80 <= x <= w + 80` 过滤掉、不产生任何
        绘制，提前截断循环次数与逐像素输出完全一致，只是不再白算。"""
        visible_end = self._px_to_time(w + 80)
        return min(self._duration, visible_end) + interval

    def paintEvent(self, event: QPaintEvent):
        """标尺绘制（编排在 `timeline_ruler_paint`：秒刻度/BPM 网格/标记条/循环/播放头）。

        ★ R183 T3/T2：本方法只做背景填充 + 交给外提的绘制段 —— 既有实现把三层
        刻度与循环/播放头全写在这里（基线 104 行 / CC 15），新增 BPM 小节层与标记条
        必然"债务恶化"；外提后本方法净减行，层序与逐像素输出不变。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(_th.BG_ROOT))  # bg_root
        _paint.draw_all(p, self, w, h, _mono_font(10))
        p.end()

    def mousePressEvent(self, e: QMouseEvent):
        """按下：循环手柄 → 标记 → 播放头 → Ctrl 建循环区 → 定位。

        分支表在 `_press_target(x)` / `_press_apply(target, x)`（外提压本方法 CC：
        加入标记手势后原内联链会超架构基线）。
        """
        if e.button() != Qt.MouseButton.LeftButton:
            return
        x = e.position().x()
        self._press_apply(self._press_target(x, e.modifiers()), x)
        e.accept()

    def _press_target(self, x: float, mods) -> str:
        """按下命中判定（返回动作名；循环手柄需要已启用循环）。"""
        if self._loop_enabled and self._near(x, self._time_to_px(self._loop_start)):
            return "loop_start"
        if self._loop_enabled and self._near(x, self._time_to_px(self._loop_end)):
            return "loop_end"
        if self._marker_press(x):
            return "marker"
        if self._near(x, self._time_to_px(self._position)):
            return "playhead"
        if mods & Qt.KeyboardModifier.ControlModifier:
            return "new_loop"
        return "seek"

    def _press_apply(self, target: str, x: float) -> None:
        from .timeline_sntc_control import perform_pointer
        if target in ('seek', 'playhead'):
            perform_pointer(self, self._press_apply_local, target, x)
            return
        self._press_apply_local(target, x)

    def _press_apply_local(self, target: str, x: float) -> None:
        """执行按下动作（`_press_target` 的对应实现，一一对应）。"""
        if target == "marker":
            return                    # 起拖已在 `_marker_press` 内完成
        t = self._px_to_time(x)
        if target in ("loop_start", "loop_end"):
            self._drag = target
            self._loop_drawing = False   # 手柄拖动 = 单端调整（清查⑧B P2-2 区分）
            self._loop_snapshot()
        elif target == "new_loop":
            # ★ 清查⑧B P2-2：new_loop 期间 end 必须**跟随光标**（修前只改 start
            #   并被夹到旧 end-0.1 → Ctrl 拖 1.0→5.0 实得 (1.9,2.0)）。用
            #   `_loop_drawing` 标记本手势为"拉区间"，_drag_move 里双端更新。
            self._loop_start = t
            self._loop_end = t
            self._loop_enabled = True
            self._loop_drawing = True
            self._drag = "loop_start"
            self._loop_snapshot()
            self.update()
            self.loop_range_changed.emit(self._loop_start, self._loop_end)
        elif target == "playhead":
            self._drag = "playhead"
        else:                         # 定位（seek）
            self._position = t
            self._drag = "playhead"
            self.update()
            self.seek_requested.emit(t)

    # ── ★ R183 T2：标记手势薄壳（实现见 timeline_marker_menu）──────────────
    def _marker_press(self, x: float) -> bool:
        """点中标记 → 起拖（返回 True；未点中 False，让标尺走原手势）。"""
        if not self._markers or self._session is None:
            return False
        from . import timeline_marker_menu as _mm
        return bool(_mm.on_press(self, self._px_to_time(x), x))

    def _loop_snapshot(self) -> None:
        """循环区手势起点压一条撤销条目（拖动中不再压，落位也不压）。"""
        from . import timeline_markers as _mk
        _mk.snapshot_markers(self._session, self._ctrl)

    def mouseMoveEvent(self, e: QMouseEvent):
        x = e.position().x()

        ph_x = self._time_to_px(self._position)
        ls_x = self._time_to_px(self._loop_start)
        le_x = self._time_to_px(self._loop_end)

        if self._loop_enabled and (
            self._near(x, ls_x) or self._near(x, le_x)
        ):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._near(x, ph_x):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        if not (e.buttons() & Qt.MouseButton.LeftButton) or not self._drag:
            # ★ T2：标记拖动（独立于 _drag 状态机 —— 标记手势不起 _drag）
            # 清查⑧B P1-2：首帧后拖动游标转为身份键（_marker_drag_idx 置 None），
            # 守卫必须两者并查——修前第 2 帧起不再派发（拖不动，实测落点 2.5/7.0）。
            if (self._marker_drag_idx is not None
                    or getattr(self, "_marker_drag_key", None)):
                from . import timeline_marker_menu as _mm
                _mm.on_move(self, self._px_to_time(x))
                e.accept()
            return

        self._drag_move(self._px_to_time(x))
        self.update()
        e.accept()

    def _drag_move(self, t: float) -> None:
        from .timeline_sntc_control import perform_pointer
        if self._drag == 'playhead':
            if not perform_pointer(self, self._drag_move_local, t):
                self._drag = None
            return
        self._drag_move_local(t)

    def _drag_move_local(self, t: float) -> None:
        """推进当前拖动手势（播放头 / 循环起止三条；外提压 mouseMoveEvent CC）。"""
        if self._drag == "playhead":
            self._position = t
            self.seek_requested.emit(t)
        elif self._drag == "loop_start":
            if getattr(self, "_loop_drawing", False):
                # 拉区间手势：锚点保持、末端跟随（两端都动可读作"从锚点拉开"）
                self._loop_end = max(t, self._loop_start + 0.1)
            else:
                self._loop_start = min(t, self._loop_end - 0.1)
            self.loop_range_changed.emit(self._loop_start, self._loop_end)
        elif self._drag == "loop_end":
            self._loop_end = max(t, self._loop_start + 0.1)
            self.loop_range_changed.emit(self._loop_start, self._loop_end)

    def mouseReleaseEvent(self, e: QMouseEvent):
        # 清查⑧B P1-2：同 mouseMoveEvent 守卫——身份键也要认（否则松手不清理状态）
        if (self._marker_drag_idx is not None
                or getattr(self, "_marker_drag_key", None)):
            from . import timeline_marker_menu as _mm
            _mm.on_release(self)
        elif self._drag in ("loop_start", "loop_end"):
            # ★ T6：循环区落位提交（点右缘/拖左缘都算）；落入工程 + 标脏。
            self.loop_committed.emit(float(self._loop_start), float(self._loop_end),
                                     bool(self._loop_enabled))
        self._drag = None
        self._loop_drawing = False
        e.accept()

    def contextMenuEvent(self, e):        # noqa: N802
        """右键：标记条区域 → 标记菜单（新建/改名/改色/删除/跳转）。"""
        from . import timeline_marker_menu as _mm
        x = float(e.pos().x())
        _mm.context_menu(self, e.globalPos(), self._px_to_time(x), x)
        e.accept()

    def wheelEvent(self, e: QWheelEvent):
        """处理滚轮事件：
        - Ctrl/Alt + 垂直滚轮 → 双向缩放时间轴（zoom_requested）
        - 其他滚轮 → 向上传递（交由 ClipArea 处理左右滚动）
        """
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt  = bool(mods & Qt.KeyboardModifier.AltModifier)
        if ctrl or alt:
            angle_delta = e.angleDelta()
            pixel_delta = e.pixelDelta()
            dy = angle_delta.y() if angle_delta.y() != 0 else pixel_delta.y()
            if dy == 0:
                e.accept()
                return
            steps = dy / 120.0 if abs(dy) >= 120 else (1.0 if dy > 0 else -1.0)
            factor = 1.12 ** steps
            # ★ R183 T4：上限与 clip_area/widget/controller 统一（唯一真源
            #   `timeline_zoom`）——此前这里钳 2000 而 widget 钳 500，滚轮放大
            #   >500 被静默折断 → 锚点换算被跳过 = 播放指针漂移。
            from .timeline_zoom import clamp_zoom
            new_zoom = clamp_zoom(self._px_per_sec * factor)
            # ★ 2026-08-11 用户要求：缩放以当前播放指针为中心（指针在屏幕上
            #   保持不动），不再以鼠标位置为锚点。
            anchor_time = float(getattr(self, "_position", 0.0) or 0.0)
            play_x = anchor_time * self._px_per_sec - float(getattr(self, "_scroll_x", 0) or 0)
            self.zoom_at_requested.emit(float(new_zoom), float(anchor_time), float(play_x))
            e.accept()
        else:
            e.ignore()
