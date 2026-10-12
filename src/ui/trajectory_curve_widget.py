"""
trajectory_curve_widget — 轨迹曲线的心电图编辑/分段视图

横轴=相位[0,1]（对应 0..duration 秒），纵轴=归一值[0,1]。深底+网格+辉光曲线，
叠加：分段竖切线、选中段高亮、每段方向/翼/块角标、可选播放头。

三种交互模式（mode）：
  - "select"：左键只选中光标所在段（设置方向/翼/块），不改曲线、不加点（默认）。
  - "point" ：左键空白加点 / 拖点 / 右键删点，编辑曲线本身。
  - "cut"   ：左键在某相位竖切一刀；点中已有分界线则删除它（剪辑工具）。
任何模式下，单击都会把光标所在段设为「选中段」并发 segment_selected，方便右侧设置面板。

节拍吸附（R178 引入、R180 补「手感」）：
  - 吸附开启即画节拍网格：主格（间距=拍长×division）最亮，半格/四分格
    细分渐淡；画布过密时主格自动 ×2 疏化，但吸附格保证仍被画出。
  - 拖点靠近格线显示「落点预对齐」高亮竖线；松手且距格线 ≤ 屏幕像素阈值
    （trajectory_grid.SNAP_PX_THRESHOLD）才吸附——画的格 = 会吸的格。
  - 分段刀（cut 模式加点）同样吃近网格吸附。

贝塞尔柄 × cut 边界规则（R180）：
  - 控制点入柄 c1 属左邻曲线片、出柄 c2 属右邻片；柄 x 钳制到
    「邻点区间 ∩ 点所在 cut 段的边界」内——越段拖柄会被钉在分割线上，
    保证 cut 一侧的曲线形状不被另一侧的柄改写（morph 窗口内外可预期）。
  - 点恰在 cut 上时该 cut 不构成两侧边界（入柄归左段、出柄归右段）。
  - add_cut 后立即对全曲线重钳（cut 边界联动）。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient, QPainterPath, QFont
from PyQt6.QtWidgets import QWidget

from .trajectory_curve import (
    TrajectoryCurve, DIR_WHOLE, DIR_LTR, DIR_RTL,
    DIR_FTB, DIR_BTF, DIR_CTR, DIR_RTC, DIR_CW, DIR_CCW, DIR_ALT,
    DIRECTION_LABELS, CURVE_TYPE_SHAPE,
)
from .trajectory_grid import (
    beat_grid_lines, effective_spacing, near_grid_t,
)
from . import qascade_theme as _th

# ── 画布配色（主题令牌化；蓝紫夜底 → 中性灰）──────────────────────────────
_BG = QColor(_th.BG_DEEP)                # #181818 曲线画布底
_GRID = QColor(_th.BORDER_NORMAL)        # Light editor grid contrast.
_GRID_MAJOR = QColor(_th.BORDER_NORMAL)  # #3D3D3D 主网格/边线
_CURVE = QColor("#46E0A0")               # 「数据曲线」绿（受保护语义，保义）
_CURVE_GLOW = QColor(70, 224, 160, 70)   # 本文件辉光 alpha 统一 = 70
_PLAYHEAD = QColor(_th.ACTIVE_ORANGE)    # #FFA028 播放头专用橙（时间推进）
_AXIS_TXT = QColor(_th.TEXT_DISABLED)    # #8B8B8B 轴刻度文字
_POINT = QColor("#9FF0CC")               # 控制点（曲线绿同族）
_CUT = QColor(_th.BRIGHT_WARN)           # #E6B455 切割线（黄琥珀→bright_warn 线条）
_SEG_SEL = QColor(120, 160, 255, 28)
_SEG_TXT = QColor(_th.TEXT_PRIMARY)
# morph 手柄家族与播放头拆色：播放头=橙（不可交互的时间推进），
# 手柄/过渡窗口=focus_blue 族（可拖拽高亮语义），拖拽可视性保留。
_MORPH_BAND = QColor(113, 168, 255, 40)       # morph 过渡窗口阴影（focus_blue α40）
_MORPH_BAND_SEL = QColor(113, 168, 255, 80)   # 选中边界的窗口阴影（α80）
_BND_HANDLE = QColor(_th.FOCUS_BLUE)          # 边界手柄 #71A8FF
_BND_HANDLE_SEL = QColor(_th.BRAND_ORANGE_INK)
_BND_TXT = QColor(_th.TEXT_PRIMARY)
# 贝塞尔控制柄（R178）：与 morph 边界手柄同族（可拖拽高亮语义），柄连线更淡。
_HANDLE = QColor(_th.FOCUS_BLUE)
_HANDLE_HIT = QColor(_th.BRAND_ORANGE_INK)
_HANDLE_LINE = QColor(113, 168, 255, 90)
_HANDLE_KEYS = ("c1x", "c1y", "c2x", "c2y")
# 节拍吸附网格（R180）：focus_blue 族透明度梯度——主格最亮、细分渐淡；
# 落点预对齐高亮用亮变体，与「可拖拽高亮」语义一致。
_SNAP_GRID_MAJOR = QColor(113, 168, 255, 110)   # 吸附格（吸附目标，最亮）
_SNAP_GRID_MID = QColor(113, 168, 255, 55)      # 半格细分
_SNAP_GRID_MINOR = QColor(113, 168, 255, 28)    # 四分格细分
_SNAP_GRID_PENS = (_SNAP_GRID_MAJOR, _SNAP_GRID_MID, _SNAP_GRID_MINOR)
_SNAP_HINT = QColor(_th.BRAND_ORANGE_INK)

_DIR_ARROW = {
    DIR_WHOLE: "≡",
    DIR_LTR:   "→",
    DIR_RTL:   "←",
    DIR_FTB:   "↓",
    DIR_BTF:   "↑",
    DIR_CTR:   "⟺",
    DIR_RTC:   "⟹",
    DIR_CW:    "↻",
    DIR_CCW:   "↺",
    DIR_ALT:   "⇌",
}


class TrajectoryCurveView(QWidget):
    points_changed = pyqtSignal()      # 曲线控制点或 cut 改变
    segment_selected = pyqtSignal(int)  # 选中段索引
    boundary_selected = pyqtSignal(int)  # 【shape】选中边界索引（0=起点 / 1..n-1=分割线 / n=终点）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._traj: Optional[TrajectoryCurve] = None
        self._playhead: Optional[float] = None  # 秒
        self._mode = "select"
        self._drag_idx: Optional[int] = None
        self._sel_segment = 0
        self._sel_boundary = 0           # 【shape】选中的边界索引
        self._sel_point_idx: Optional[int] = None   # 【bezier】select 模式选中的控制点
        self._drag_handle: Optional[tuple] = None   # 【bezier】(点索引, 'c1'|'c2') 柄拖拽
        self._snap_clock = None                    # 【snap】TrajectoryClock（可无）
        self._snap_division = 0                    # 【snap】0=关；1/2/4=每 N 拍一格
        self._snap_hint_ph: Optional[float] = None  # 【snap】拖动中近网格的落点相位
        self._rec_samples = None        # 实时录制样本 [[elapsed, val], ...]
        self._rec_duration_hint = 0.0
        self.setMinimumHeight(220)
        self.setMouseTracking(True)

    # ── 数据 ─────────────────────────────────────────────────────────────
    def _is_shape(self) -> bool:
        return self._traj is not None and getattr(self._traj, "curve_type", "") == CURVE_TYPE_SHAPE

    def set_trajectory(self, traj: Optional[TrajectoryCurve]):
        self._traj = traj
        self._drag_idx = None
        self._sel_segment = 0
        self._sel_boundary = 0
        self._sel_point_idx = None
        self._drag_handle = None
        self._snap_hint_ph = None
        self.update()

    def set_mode(self, mode: str):
        self._mode = mode if mode in ("select", "point", "cut") else "select"
        self.update()

    def selected_segment(self) -> int:
        return self._sel_segment

    def set_selected_segment(self, idx: int):
        self._sel_segment = max(0, int(idx))
        self.update()

    def selected_boundary(self) -> int:
        return self._sel_boundary

    def set_selected_boundary(self, idx: int):
        self._sel_boundary = max(0, int(idx))
        self.update()

    def set_playhead(self, t: Optional[float]):
        self._playhead = t
        self.update()

    # ── 节拍吸附（R178）：松手吸附落点，拖动中不吸附 ─────────────────────────
    def set_snap_clock(self, clock) -> None:
        """挂 TrajectoryClock（None=清除）；吸附开关由 set_snap_division 控制。"""
        self._snap_clock = clock

    def set_snap_division(self, division: int) -> None:
        """0=关；1/2/4=每 N 拍一格（见 trajectory_clock.SNAP_DIVISIONS）。"""
        self._snap_division = int(division) if int(division) in (0, 1, 2, 4) else 0
        self._snap_hint_ph = None
        self.update()

    def set_live_recording(self, samples_ts_val: list, duration_hint: float = 0.0):
        """录制中实时更新：samples_ts_val = [[elapsed_s, val], ...]，不修改模型。"""
        self._rec_samples = samples_ts_val
        self._rec_duration_hint = max(1e-3, float(duration_hint or 1.0))
        self.update()

    def clear_live_recording(self):
        self._rec_samples = None
        self._rec_duration_hint = 0.0
        self.update()

    def _duration(self) -> float:
        d = float(getattr(self._traj, "duration", 4.0) or 4.0) if self._traj else 4.0
        return d if d > 0 else 4.0

    # ── 坐标映射 ─────────────────────────────────────────────────────────
    def _plot_rect(self) -> QRectF:
        m_l, m_r, m_t, m_b = 34, 10, 18, 18
        return QRectF(m_l, m_t, max(1.0, self.width() - m_l - m_r),
                      max(1.0, self.height() - m_t - m_b))

    def _ph_to_x(self, ph: float) -> float:
        r = self._plot_rect()
        return r.left() + r.width() * max(0.0, min(1.0, ph))

    def _v_to_y(self, v: float) -> float:
        r = self._plot_rect()
        return r.bottom() - r.height() * max(0.0, min(1.0, v))

    def _x_to_ph(self, x: float) -> float:
        r = self._plot_rect()
        return max(0.0, min(1.0, (x - r.left()) / r.width())) if r.width() else 0.0

    def _y_to_v(self, y: float) -> float:
        r = self._plot_rect()
        return max(0.0, min(1.0, (r.bottom() - y) / r.height())) if r.height() else 0.0

    def _curve_points(self):
        """返回曲线控制点容器（CurvePoint 列表或 [[x,y]]），权威可改。"""
        t = self._traj
        if t is None:
            return None
        curve = getattr(t, "curve", None)
        if curve is not None and getattr(curve, "points", None) is not None:
            return curve.points
        return t.points

    @staticmethod
    def _pt_xy(pt):
        if hasattr(pt, "x") and hasattr(pt, "y"):
            return float(pt.x), float(pt.y)
        return float(pt[0]), float(pt[1])

    # ── 贝塞尔控制柄（R178；仅 CurvePoint 型点支持，[x,y] 列表点自动跳过）────
    @staticmethod
    def _pt_has_handle(pt, which: str) -> bool:
        return (getattr(pt, which + "x", None) is not None
                or getattr(pt, which + "y", None) is not None)

    def _ensure_point_handles(self, pts, idx: int) -> bool:
        """选中点补默认柄：入柄/出柄落在线性位置（1/3、2/3 共线 → 曲线不变）。
        首点只有出柄 c2、末点只有入柄 c1。返回是否发生了修改。"""
        pt = pts[idx]
        if not hasattr(pt, "c1x"):
            return False
        x, y = self._pt_xy(pt)
        changed = False
        if idx > 0 and not self._pt_has_handle(pt, "c1"):
            px, py = self._pt_xy(pts[idx - 1])
            pt.c1x = px + (x - px) * (2.0 / 3.0)
            pt.c1y = py + (y - py) * (2.0 / 3.0)
            changed = True
        if idx < len(pts) - 1 and not self._pt_has_handle(pt, "c2"):
            nx, ny = self._pt_xy(pts[idx + 1])
            pt.c2x = x + (nx - x) / 3.0
            pt.c2y = y + (ny - y) / 3.0
            changed = True
        return changed

    def _clear_point_handles(self, pts, idx: int) -> bool:
        pt = pts[idx]
        if not hasattr(pt, "c1x"):
            return False
        changed = any(getattr(pt, k, None) is not None for k in _HANDLE_KEYS)
        for k in _HANDLE_KEYS:
            setattr(pt, k, None)
        return changed

    def _clamp_handle_x(self, pts, idx: int) -> None:
        """柄 x 钳回「邻点区间 ∩ 点所在 cut 段边界」（保证求值 x 单调且段形状
        不被越界柄改写）；y 钳 [0,1] 视图域。

        规则：c1 下界 = max(左邻点 x, x 左侧最近的 cut)；c2 上界 = min(右邻点 x,
        x 右侧最近的 cut)。点恰在 cut 上时该 cut 两侧不算边界（入柄归左段、
        出柄归右段）。"""
        pt = pts[idx]
        x, _ = self._pt_xy(pt)
        lo_c1 = self._pt_xy(pts[idx - 1])[0] if idx > 0 else x
        hi_c2 = self._pt_xy(pts[idx + 1])[0] if idx < len(pts) - 1 else x
        cuts = getattr(self._traj, "cuts", None) or ()
        if cuts:
            lo_c1 = max(lo_c1, max((c for c in cuts if c < x), default=0.0))
            hi_c2 = min(hi_c2, min((c for c in cuts if c > x), default=1.0))
        if pt.c1x is not None:
            pt.c1x = max(lo_c1, min(x, float(pt.c1x)))
            pt.c1y = max(0.0, min(1.0, float(pt.c1y)))
        if pt.c2x is not None:
            pt.c2x = max(x, min(hi_c2, float(pt.c2x)))
            pt.c2y = max(0.0, min(1.0, float(pt.c2y)))

    def _clamp_all_handles(self) -> None:
        """cut 边界联动：新分割线收缩柄域后，对全曲线控制点重钳一遍。"""
        pts = self._curve_points() or []
        for i, pt in enumerate(pts):
            if hasattr(pt, "c1x") and (self._pt_has_handle(pt, "c1")
                                       or self._pt_has_handle(pt, "c2")):
                self._clamp_handle_x(pts, i)

    def _handle_pos(self, pt, which: str):
        """柄的曲线坐标 (x, y)；缺单坐标回落端点坐标。"""
        hx = getattr(pt, which + "x", None)
        hy = getattr(pt, which + "y", None)
        x, y = self._pt_xy(pt)
        return (float(hx if hx is not None else x),
                float(hy if hy is not None else y))

    def _hit_handle(self, mx: float, my: float, tol: float = 9.0):
        """选中点两柄的命中测试 → (idx, 'c1'|'c2') 或 None。"""
        if self._sel_point_idx is None:
            return None
        pts = self._curve_points() or []
        if self._sel_point_idx >= len(pts):
            return None
        pt = pts[self._sel_point_idx]
        if not hasattr(pt, "c1x"):
            return None
        for which in ("c1", "c2"):
            if not self._pt_has_handle(pt, which):
                continue
            hx, hy = self._handle_pos(pt, which)
            dx = self._ph_to_x(hx) - mx
            dy = self._v_to_y(hy) - my
            if dx * dx + dy * dy <= tol * tol:
                return (self._sel_point_idx, which)
        return None

    # ── 绘制 ─────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), _BG)
        r = self._plot_rect()
        f = QFont(); f.setPointSize(7); p.setFont(f)

        # 值网格
        for i in range(5):
            y = r.top() + r.height() * i / 4
            p.setPen(QPen(_GRID_MAJOR if i in (0, 4) else _GRID, 1))
            p.drawLine(int(r.left()), int(y), int(r.right()), int(y))
            p.setPen(_AXIS_TXT)
            p.drawText(2, int(y) + 3, f"{1.0 - i/4:.1f}")
        # 时间网格
        dur = self._duration()
        for i in range(5):
            x = r.left() + r.width() * i / 4
            p.setPen(QPen(_GRID_MAJOR if i in (0, 4) else _GRID, 1))
            p.drawLine(int(x), int(r.top()), int(x), int(r.bottom()))
            p.setPen(_AXIS_TXT)
            p.drawText(int(x) - 8, self.height() - 4, f"{dur*i/4:.1f}s")

        # 节拍吸附网格（吸附开且有网格才画；含拖点预对齐高亮）
        self._paint_snap_grid(p, r)

        if self._traj is None:
            p.setPen(_AXIS_TXT)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "（未选择轨迹）")
            return

        bounds = self._traj.segment_bounds()
        # 选中段高亮底
        if 0 <= self._sel_segment < len(bounds):
            s, e = bounds[self._sel_segment]
            x0, x1 = self._ph_to_x(s), self._ph_to_x(e)
            p.fillRect(QRectF(x0, r.top(), x1 - x0, r.height()), _SEG_SEL)

        # 采样曲线
        n = 240
        samples = []
        for i in range(n + 1):
            ph = i / n
            try:
                samples.append(self._traj._eval_phase(ph))
            except Exception:
                samples.append(0.0)
        path = QPainterPath(); fill = QPainterPath()
        for i, v in enumerate(samples):
            x = r.left() + r.width() * i / n
            y = self._v_to_y(v)
            if i == 0:
                path.moveTo(x, y); fill.moveTo(x, r.bottom()); fill.lineTo(x, y)
            else:
                path.lineTo(x, y); fill.lineTo(x, y)
        fill.lineTo(r.right(), r.bottom()); fill.closeSubpath()
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0.0, _CURVE_GLOW); grad.setColorAt(1.0, QColor(70, 224, 160, 6))
        p.fillPath(fill, QBrush(grad))
        p.setPen(QPen(_CURVE, 2)); p.drawPath(path)

        # 分界竖线
        for c in self._traj.cuts:
            x = self._ph_to_x(c)
            p.setPen(QPen(_CUT, 1.5, Qt.PenStyle.DashLine))
            p.drawLine(int(x), int(r.top()), int(x), int(r.bottom()))

        shape = self._is_shape()

        if shape:
            # ── 造型模式：morph 过渡窗口阴影 + 边界手柄 + 段静态翼/块角标 ──
            dur = self._duration()
            try:
                windows = self._traj.shape_boundary_windows()
            except Exception:
                windows = []
            win_by_idx = {int(w.get("idx", -1)): w for w in windows}
            # 1) 过渡窗口阴影（秒→相位）
            for w in windows:
                bidx = int(w.get("idx", -1))
                ph0 = max(0.0, min(1.0, float(w.get("t0", 0.0)) / dur))
                ph1 = max(0.0, min(1.0, float(w.get("t1", 0.0)) / dur))
                x0, x1 = self._ph_to_x(ph0), self._ph_to_x(ph1)
                band = _MORPH_BAND_SEL if bidx == self._sel_boundary else _MORPH_BAND
                p.fillRect(QRectF(x0, r.top(), max(1.0, x1 - x0), r.height()), band)
            # 2) 边界手柄（起点/每条分割线/终点）：倒三角 + 选中高亮
            n_bnd = len(self._traj.cuts) + 2
            fb2 = QFont(); fb2.setPointSize(7); fb2.setBold(True); p.setFont(fb2)
            for bidx in range(n_bnd):
                if bidx == 0:
                    bx = self._ph_to_x(0.0) + 1
                elif bidx == n_bnd - 1:
                    bx = self._ph_to_x(1.0) - 1
                else:
                    bx = self._ph_to_x(self._traj.cuts[bidx - 1])
                sel = (bidx == self._sel_boundary)
                col = _BND_HANDLE_SEL if sel else _BND_HANDLE
                # 顶端倒三角手柄
                tri = QPainterPath()
                ty = r.top()
                tri.moveTo(bx - 5, ty - 1); tri.lineTo(bx + 5, ty - 1); tri.lineTo(bx, ty + 7)
                tri.closeSubpath()
                p.setPen(QPen(col, 1)); p.setBrush(QBrush(col)); p.drawPath(tri)
                if sel:
                    p.setPen(QPen(col, 1.4, Qt.PenStyle.DotLine))
                    p.drawLine(int(bx), int(r.top()), int(bx), int(r.bottom()))
                # 该边界的 morph 角标（在手柄下方）
                w = win_by_idx.get(bidx)
                bseg = self._traj.boundaries[bidx] if bidx < len(self._traj.boundaries) else None
                if bseg is not None:
                    tau = float(getattr(bseg, "transition", 0.0) or 0.0)
                    if tau > 1e-6:
                        arrow = _DIR_ARROW.get(bseg.direction, "≡")
                        p.setPen(_BND_TXT)
                        p.drawText(int(bx - 16), int(r.top() + 22), f"{arrow}{tau:g}s")
            # 3) 段静态角标：翼/块（无方向）
            fbb = QFont(); fbb.setPointSize(8); fbb.setBold(True); p.setFont(fbb)
            for i, (s, e) in enumerate(bounds):
                seg = self._traj.segments[i] if i < len(self._traj.segments) else None
                if seg is None:
                    continue
                cx = (self._ph_to_x(s) + self._ph_to_x(e)) / 2
                p.setPen(_SEG_TXT)
                p.drawText(int(cx - 28), int(r.bottom() - 4), f"造型 翼{seg.wings}/块{seg.block}")
        else:
            # ── 路径模式：每段角标 方向箭头 + 翼/块 ──
            fb = QFont(); fb.setPointSize(8); fb.setBold(True); p.setFont(fb)
            for i, (s, e) in enumerate(bounds):
                seg = self._traj.segments[i] if i < len(self._traj.segments) else None
                if seg is None:
                    continue
                cx = (self._ph_to_x(s) + self._ph_to_x(e)) / 2
                arrow = _DIR_ARROW.get(seg.direction, "≡")
                label = f"{arrow} 翼{seg.wings}/块{seg.block}"
                p.setPen(_SEG_TXT)
                p.drawText(int(cx - 34), int(r.top() + 11), label)

        # 控制点（含 select 模式选中点的贝塞尔柄）
        self._paint_control_points(p)

        # 播放头
        if self._playhead is not None:
            ph = self._traj.phase_at(self._playhead)
            x = self._ph_to_x(ph)
            p.setPen(QPen(_PLAYHEAD, 1.5))
            p.drawLine(int(x), int(r.top()), int(x), int(r.bottom()))

        # 实时录制波形（覆盖在曲线上，亮红色生长线）
        if self._rec_samples:
            _REC_COLOR = QColor("#FF4466")
            _REC_HEAD  = QColor("#FF8899")
            dur = max(1e-3, self._rec_duration_hint)
            path_r = QPainterPath()
            first = True
            for ts, val in self._rec_samples:
                ph = min(1.0, max(0.0, float(ts) / dur))
                rx = r.left() + r.width() * ph
                ry = self._v_to_y(float(val))
                if first:
                    path_r.moveTo(rx, ry); first = False
                else:
                    path_r.lineTo(rx, ry)
            p.setPen(QPen(_REC_COLOR, 2)); p.drawPath(path_r)
            # 录制头：当前位置的红点
            if self._rec_samples:
                last_ts, last_val = self._rec_samples[-1]
                ph_head = min(1.0, float(last_ts) / dur)
                hx = r.left() + r.width() * ph_head
                hy = self._v_to_y(float(last_val))
                p.setPen(QPen(_REC_HEAD, 1))
                p.setBrush(QBrush(_REC_HEAD))
                p.drawEllipse(QPointF(hx, hy), 4.0, 4.0)
                # 录制时间线（当前录到哪）
                p.setPen(QPen(_REC_COLOR, 1, Qt.PenStyle.DotLine))
                p.drawLine(int(hx), int(r.top()), int(hx), int(r.bottom()))

    # ── 交互 ─────────────────────────────────────────────────────────────
    def _paint_snap_grid(self, p, r) -> None:
        """节拍网格竖线：主格最亮、半格/四分格细分渐淡；拖动控制点且近格时
        叠加「落点预对齐」高亮竖线。只画曝光区 [0,duration]（本视图无内部
        视口，曝光区=整条时间轴；线数经疏化上界控制，不拖慢重绘）。"""
        grid = self._snap_grid_params()
        if grid is None or self._traj is None:
            return
        origin, spacing, px_per_sec = grid
        dur = self._duration()
        for t, level in beat_grid_lines(0.0, dur, origin, spacing, px_per_sec):
            x = self._ph_to_x(t / dur)
            p.setPen(QPen(_SNAP_GRID_PENS[min(level, len(_SNAP_GRID_PENS) - 1)],
                          1.4 if level == 0 else 1.0))
            p.drawLine(int(x), int(r.top()), int(x), int(r.bottom()))
        if self._snap_hint_ph is not None:
            hx = self._ph_to_x(self._snap_hint_ph)
            p.setPen(QPen(_SNAP_HINT, 1.6))
            p.drawLine(int(hx), int(r.top()), int(hx), int(r.bottom()))
            tri = QPainterPath()
            tri.moveTo(hx - 4, r.top())
            tri.lineTo(hx + 4, r.top())
            tri.lineTo(hx, r.top() + 6)
            tri.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_SNAP_HINT))
            p.drawPath(tri)

    def _paint_control_points(self, p) -> None:
        """控制点小圆 + select 模式选中点的两枚贝塞尔控制柄（小圆+虚线）。"""
        pts = self._curve_points() or []
        p.setBrush(QBrush(_POINT)); p.setPen(QPen(_BG, 1))
        for pt in pts:
            try:
                x_, y_ = self._pt_xy(pt)
            except Exception:
                continue
            p.drawEllipse(QPointF(self._ph_to_x(x_), self._v_to_y(y_)), 3.5, 3.5)
        if self._mode == "select" and self._sel_point_idx is not None \
                and self._sel_point_idx < len(pts):
            self._paint_point_handles(p, pts[self._sel_point_idx])

    def _paint_point_handles(self, p, pt) -> None:
        if not hasattr(pt, "c1x"):
            return
        ax, ay = self._pt_xy(pt)
        ax_s, ay_s = self._ph_to_x(ax), self._v_to_y(ay)
        for which in ("c1", "c2"):
            if not self._pt_has_handle(pt, which):
                continue
            hx, hy = self._handle_pos(pt, which)
            hx_s, hy_s = self._ph_to_x(hx), self._v_to_y(hy)
            p.setPen(QPen(_HANDLE_LINE, 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(ax_s, ay_s), QPointF(hx_s, hy_s))
            p.setPen(QPen(_HANDLE_HIT, 1))
            p.setBrush(QBrush(_HANDLE))
            p.drawEllipse(QPointF(hx_s, hy_s), 3.0, 3.0)

    def _segment_at_phase(self, ph: float) -> int:
        for i, (s, e) in enumerate(self._traj.segment_bounds()):
            if (s <= ph < e) or (i == len(self._traj.segment_bounds()) - 1 and ph >= s):
                return i
        return 0

    def _nearest_point_idx(self, mx, my, tol=8.0):
        pts = self._curve_points() or []
        best, best_d = None, tol * tol
        for i, pt in enumerate(pts):
            try:
                x_, y_ = self._pt_xy(pt)
            except Exception:
                continue
            dx = self._ph_to_x(x_) - mx; dy = self._v_to_y(y_) - my
            d = dx * dx + dy * dy
            if d <= best_d:
                best, best_d = i, d
        return best

    def _nearest_cut_idx(self, mx, tol=6.0):
        for i, c in enumerate(self._traj.cuts):
            if abs(self._ph_to_x(c) - mx) <= tol:
                return i
        return None

    def _boundary_x(self, bidx: int) -> float:
        """边界索引 → 屏幕 x（0=起点 / 1..n-1=分割线 / n=终点）。"""
        n_bnd = len(self._traj.cuts) + 2
        if bidx <= 0:
            return self._ph_to_x(0.0)
        if bidx >= n_bnd - 1:
            return self._ph_to_x(1.0)
        return self._ph_to_x(self._traj.cuts[bidx - 1])

    def _nearest_boundary_idx(self, mx, tol=9.0):
        """【shape】最近的边界手柄索引（含起点/终点），无则 None。"""
        n_bnd = len(self._traj.cuts) + 2
        best, best_d = None, tol
        for bidx in range(n_bnd):
            d = abs(self._boundary_x(bidx) - mx)
            if d <= best_d:
                best, best_d = bidx, d
        return best

    def _reclamp_boundary_after_cut(self):
        """cut 增删后边界数量变了：钳住 _sel_boundary 并（shape 时）重发 boundary_selected，
        防止高亮/右侧面板指向已移位或越界的边界。"""
        if self._traj is None:
            return
        n_bnd = len(self._traj.cuts) + 2
        new_b = max(0, min(self._sel_boundary, n_bnd - 1))
        changed = (new_b != self._sel_boundary)
        self._sel_boundary = new_b
        if self._is_shape() and changed:
            self.boundary_selected.emit(self._sel_boundary)

    def _set_point(self, idx, ph, v):
        pts = self._curve_points()
        if pts is None or idx >= len(pts):
            return
        pt = pts[idx]
        if hasattr(pt, "x"):
            # 拖点时柄随端点整体平移（保持相对形状），再钳回邻点区间。
            dx = ph - float(pt.x)
            dy = v - float(pt.y)
            pt.x = ph; pt.y = v
            if hasattr(pt, "c1x"):
                if pt.c1x is not None:
                    pt.c1x += dx; pt.c1y += dy
                if pt.c2x is not None:
                    pt.c2x += dx; pt.c2y += dy
                self._clamp_handle_x(pts, idx)
        else:
            pts[idx] = [ph, v]

    def _add_point(self, ph, v):
        pts = self._curve_points()
        if pts is None:
            return None
        if pts and hasattr(pts[0], "x"):
            proto = type(pts[0])
            pts.append(proto(ph, v))
        else:
            pts.append([ph, v])
        pts.sort(key=lambda q: self._pt_xy(q)[0])
        for i, pt in enumerate(pts):
            if abs(self._pt_xy(pt)[0] - ph) < 1e-9:
                return i
        return None

    def mousePressEvent(self, ev):
        if self._traj is None:
            return
        mx, my = ev.position().x(), ev.position().y()
        ph = self._x_to_ph(mx)
        shape = self._is_shape()

        # 选择模式下 shape 命中边界手柄：只选边界，不顺带改选中段（避免双面板同时刷新）。
        if self._mode == "select" and shape:
            bidx = self._nearest_boundary_idx(mx)
            if bidx is not None:
                self._sel_boundary = bidx
                self._drag_idx = None
                self.update()
                self.boundary_selected.emit(bidx)
                return

        # 选中段（其余情况：任何模式都更新）
        seg_i = self._segment_at_phase(ph)
        if seg_i != self._sel_segment:
            self._sel_segment = seg_i
            self.segment_selected.emit(seg_i)

        # 选择模式：path 只选段；shape 未命中边界（上方已 return）则在此选段（设静态造型）。
        # 【bezier】先命中选中点控制柄（拖柄改 c1/c2），再命中控制点（选中+补默认柄）。
        if self._mode == "select":
            self._drag_idx = None
            self._press_select_mode(mx, my, ev.button())
            return

        if self._mode == "cut":
            self._press_cut_mode(mx, ph, ev.button())
            return

        # point 模式
        if ev.button() == Qt.MouseButton.RightButton:
            idx = self._nearest_point_idx(mx, my)
            pts = self._curve_points() or []
            if idx is not None and 0 < idx < len(pts) - 1:
                pts.pop(idx)
                self.update(); self.points_changed.emit()
            return
        idx = self._nearest_point_idx(mx, my)
        if idx is None:
            idx = self._add_point(ph, self._y_to_v(my))
            self.points_changed.emit()
        self._drag_idx = idx
        self.update()
        self.segment_selected.emit(self._sel_segment)

    def _press_select_mode(self, mx: float, my: float, button) -> None:
        """select 模式按压：柄拖拽 > 控制点选中（右键清柄/左键补柄）> 选段。"""
        hit = self._hit_handle(mx, my)
        if hit is not None:
            self._drag_handle = hit
            self.update()
            return
        pts = self._curve_points() or []
        idx = self._nearest_point_idx(mx, my)
        if idx is not None and button == Qt.MouseButton.RightButton:
            self._sel_point_idx = idx
            if self._clear_point_handles(pts, idx):
                self.update(); self.points_changed.emit()
            return
        if idx is not None:
            self._sel_point_idx = idx
            if self._ensure_point_handles(pts, idx):
                self._clamp_handle_x(pts, idx)  # 默认柄也要过 cut 边界钳
                self.update(); self.points_changed.emit()
        else:
            self._sel_point_idx = None
        self.update()
        self.segment_selected.emit(self._sel_segment)

    def _press_cut_mode(self, mx: float, ph: float, button) -> None:
        """cut 模式按压：右键删分界线；左键加/删 cut（点中即删）。"""
        if button == Qt.MouseButton.RightButton:
            ci = self._nearest_cut_idx(mx)
            if ci is not None:
                self._traj.remove_cut(ci)
                self._sel_segment = min(self._sel_segment, len(self._traj.segments) - 1)
                self._reclamp_boundary_after_cut()
                self.update(); self.points_changed.emit()
                self.segment_selected.emit(self._sel_segment)
            return
        ci = self._nearest_cut_idx(mx)
        if ci is not None:
            # 点中已有分界线 → 删除（合并）
            self._traj.remove_cut(ci)
        else:
            snapped = self._snap_phase_near(ph)   # 分段刀同吃近网格吸附
            if snapped is not None:
                ph = snapped
            self._traj.add_cut(ph)
            self._clamp_all_handles()  # cut 边界联动：新分割线收缩柄域
        self._sel_segment = self._segment_at_phase(ph)
        self._reclamp_boundary_after_cut()
        self.update(); self.points_changed.emit()
        self.segment_selected.emit(self._sel_segment)

    def mouseMoveEvent(self, ev):
        if self._traj is None:
            return
        if self._drag_handle is not None:
            self._move_dragged_handle(ev.position().x(), ev.position().y())
            return
        if self._drag_idx is None or self._mode != "point":
            return
        self._move_dragged_point(ev.position().x(), ev.position().y())

    def _move_dragged_point(self, mx: float, my: float) -> None:
        """point 模式拖点：首尾 x 固定；中间点 x 钳邻点 ±1e-3（柄随点平移）。"""
        pts = self._curve_points()
        if not pts or self._drag_idx >= len(pts):
            return
        i = self._drag_idx
        ph = self._x_to_ph(mx); v = self._y_to_v(my)
        # 首尾点 x 固定
        if i == 0:
            ph = 0.0
        elif i == len(pts) - 1:
            ph = 1.0
        else:
            lo = self._pt_xy(pts[i - 1])[0] + 1e-3
            hi = self._pt_xy(pts[i + 1])[0] - 1e-3
            ph = max(lo, min(hi, ph))
        self._set_point(i, ph, v)
        # 预吸附高亮：近格线则显示落点（拖动中不吸附，松手才落位）。
        self._snap_hint_ph = self._snap_phase_near(self._pt_xy(pts[i])[0])
        self.update(); self.points_changed.emit()

    def _move_dragged_handle(self, mx: float, my: float) -> None:
        """拖控制柄：x 钳在 [邻点, 本点] 区间（c1）/[本点, 邻点]（c2），y 钳视图域。"""
        idx, which = self._drag_handle
        pts = self._curve_points()
        if pts is None or idx >= len(pts):
            return
        pt = pts[idx]
        if not hasattr(pt, "c1x"):
            return
        ph = self._x_to_ph(mx)
        v = self._y_to_v(my)
        if which == "c1":
            pt.c1x, pt.c1y = ph, v
        else:
            pt.c2x, pt.c2y = ph, v
        self._clamp_handle_x(pts, idx)
        self.update(); self.points_changed.emit()

    def _snap_dragged_point(self, idx: int) -> None:
        """松手吸附：拖过的中间点 x 落到最近拍时刻（首尾 x 固定、越邻放弃）。"""
        if not self._snap_enabled():
            return
        pts = self._curve_points()
        if pts is None or idx >= len(pts):
            return
        ph = self._snapped_phase(pts, idx)
        if ph is not None:
            self._set_point(idx, ph, self._pt_xy(pts[idx])[1])

    def _snap_enabled(self) -> bool:
        return self._snap_clock is not None and bool(self._snap_division)

    def _snap_grid_params(self):
        """当前吸附网格 (origin_s, spacing_s, px_per_sec)；不可用 → None。

        origin/拍长取时钟的本地域网格（与 snap_local 同源）；间距按画布
        像素密度疏化（effective_spacing）——与 paintEvent 画的格严格同源，
        保证画的格 = 会吸的格。"""
        ck = self._snap_clock
        if not self._snap_enabled() or ck is None:
            return None
        getter = getattr(ck, "local_grid", None)
        grid = getter() if callable(getter) else None
        if grid is None:
            return None
        origin, beat_period = grid
        dur = self._duration()
        if dur <= 1e-9:
            return None
        px_per_sec = self._plot_rect().width() / dur
        spacing = effective_spacing(float(beat_period) * self._snap_division,
                                    px_per_sec)
        return (float(origin), spacing, px_per_sec)

    def _snap_phase_near(self, ph: float):
        """相位 ph 距最近格线 ≤ 屏幕像素阈值 → 返回吸附目标相位；否则 None。"""
        grid = self._snap_grid_params()
        if grid is None:
            return None
        origin, spacing, px_per_sec = grid
        t = near_grid_t(ph * self._duration(), origin, spacing, px_per_sec)
        if t is None:
            return None
        return max(0.0, min(1.0, t / self._duration()))

    def _snapped_phase(self, pts, idx: int):
        """松手吸附后的相位；首尾/无网格/不近格线/吸附目标越邻点 → None。"""
        if idx in (0, len(pts) - 1):
            return None
        ph = self._snap_phase_near(self._pt_xy(pts[idx])[0])
        if ph is None:
            return None
        lo = self._pt_xy(pts[idx - 1])[0] + 1e-3
        hi = self._pt_xy(pts[idx + 1])[0] - 1e-3
        if ph < lo or ph > hi:
            return None
        return ph

    def mouseReleaseEvent(self, _ev):
        if self._drag_handle is not None:
            self._drag_handle = None
            self.update(); self.points_changed.emit()
            return
        if self._drag_idx is not None:
            idx = self._drag_idx
            self._drag_idx = None
            pts = self._curve_points()
            if pts:
                pts.sort(key=lambda q: self._pt_xy(q)[0])
            self._snap_dragged_point(idx)
            self._snap_hint_ph = None
            self.update(); self.points_changed.emit()
