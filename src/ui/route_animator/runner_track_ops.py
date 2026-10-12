# -*- coding: utf-8 -*-
"""跑动面板轨道运维工具（深查③ R5 轨道镜像 / R11 速度剖面预览）。

纯逻辑（镜像点变换）与 UI（pyqtgraph 速度剖面浮窗）都在本模块闭环，
`runner_panel` 只在轨道列表区插入两枚镜像按钮 + 一枚「速度剖面」按钮
（面板源码零新逻辑，与 topview_track_draw 的注入式接线同范式）。
"""
from __future__ import annotations
from ..editor_workspace_regions import popup

import math
from typing import List, Sequence, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QListWidget, QPushButton, QVBoxLayout, QWidget,
)

from .runner_scene_store import snapshot_route_undo

TrackPoints = List[Tuple[float, float]]


def _polyline_cumlen(points) -> List[float]:
    """折线累积弧长（与面板同口径，避免 import runner_panel 造成环）。"""
    pts = list(points or [])
    if not pts:
        return []
    cum = [0.0]
    for i in range(1, len(pts)):
        dx = float(pts[i][0]) - float(pts[i - 1][0])
        dy = float(pts[i][1]) - float(pts[i - 1][1])
        cum.append(cum[-1] + math.sqrt(dx * dx + dy * dy))
    return cum

MIRROR_STATUS = {
    "x": "镜像水平(上下翻, y 取反)",
    "y": "镜像垂直(左右翻, x 取反)",
}


def mirror_points(points: Sequence[Sequence[float]], axis: str) -> TrackPoints:
    """轨道点列镜像：axis="y" 左右翻（x 取反）；"x" 上下翻（y 取反）。

    顺序同时反转——镜像后保持"起点语义"不变（原起点仍是车头出发点，
    与 TopView `flip_selected_fixtures` 绕轴翻面的视觉结果一致）。
    闭合轨道（首点==末点）反转后仍闭合。
    """
    pts = [(float(p[0]), float(p[1])) for p in (points or [])]
    if len(pts) < 2:
        return pts
    if str(axis or "").lower() == "x":
        mirrored = [(x, -y) for x, y in pts]
    else:
        mirrored = [(-x, y) for x, y in pts]
    mirrored.reverse()
    return mirrored


def selected_track_ids(track_list) -> List[str]:
    """轨道列表（QListWidget）当前选中的轨道 id（UserRole，与删除按钮同源）。"""
    out: List[str] = []
    if track_list is None:
        return out
    for i in range(track_list.count()):
        it = track_list.item(i)
        if it is not None and it.isSelected():
            tid = str(it.data(Qt.ItemDataRole.UserRole) or "")
            if tid and tid not in out:
                out.append(tid)
    return out


def _panel_status(panel, text: str) -> None:
    """面板状态行写入（无状态行/异常时静默——与注入式接线同纪律）。"""
    try:
        panel._set_status(text)
    except Exception:
        pass


def _mirror_tracks(tracks: dict, tids: Sequence[str], axis: str) -> int:
    """就地镜像给定轨道点列；返回实际镜像条数（缺失轨道跳过）。"""
    done = 0
    for tid in tids:
        track = tracks.get(tid)
        if not track:
            continue
        track["points"] = mirror_points(track.get("points") or [], axis)
        done += 1
    return done


def _track_length(track) -> float:
    pts = (track or {}).get("points") or []
    total = 0.0
    for i in range(1, len(pts)):
        total += math.hypot(float(pts[i][0]) - float(pts[i - 1][0]),
                            float(pts[i][1]) - float(pts[i - 1][1]))
    return total


def _recompute_cum(tracks, hit) -> dict:
    """镜像后的轨道 cum 重算（`_switch_pos` 与画布都读它）。返回 {tid: 长度}。"""
    lengths: dict = {}
    for tid in hit:
        track = tracks.get(tid)
        if not track:
            continue
        track["cum"] = _polyline_cumlen(track.get("points") or [])
        lengths[tid] = _track_length(track)
    return lengths


def _remap_switches(panel, hit, lengths) -> int:
    """该轨道上的道岔 s → L-s（节点留在镜像后的同一物理位置）。"""
    done = 0
    for node in (getattr(panel, "_switches", None) or {}).values():
        tid = str(node.get("track_id", "") or "")
        length = lengths.get(tid, 0.0)
        if tid in hit and length > 0.0:
            node["s"] = max(0.0, length - float(node.get("s", 0.0) or 0.0))
            done += 1
    return done


def _remap_trains(panel, hit, lengths) -> int:
    """以镜像轨道为 home 的火车 nose_s → L-s（整列留在镜像位置）。"""
    engine = getattr(panel, "_engine", None)
    done = 0
    for _tid, tr in list((getattr(engine, "_trains", None) or {}).items()):
        home = str(getattr(tr, "_home_edge_id", "") or "")
        length = lengths.get(home, 0.0)
        if home in hit and length > 0.0:
            tr.nose_s = max(0.0, length - float(tr.nose_s))
            done += 1
    return done


def remap_after_mirror(panel, tids: Sequence[str]) -> dict:
    """镜像后的**位置口径**重映射：cum / 道岔 s / 火车 nose_s。

    点列反转后新轨道在弧长 u 处的点 = 旧轨道弧长 (L-u) 处点的镜像，因此
    "保持物理位置不变"必须做 ``x → L - x`` 换算：
      · cum 重算（`_switch_pos` 与画布都读它，修前镜像后 cum 是旧值 → 道岔漂移）；
      · 该轨道上的道岔 s → L-s（节点留在镜像后的同一物理位置）；
      · 以该轨道为 home 的火车 nose_s → L-s（整列留在镜像位置）。

    返回 {"cum": n, "switches": n, "trains": n}（供状态行/测试断言）。
    """
    tracks = getattr(panel, "_tracks", None) or {}
    hit = {str(t) for t in (tids or ())}
    lengths = _recompute_cum(tracks, hit)
    return {"cum": len(lengths),
            "switches": _remap_switches(panel, hit, lengths),
            "trains": _remap_trains(panel, hit, lengths)}



def _commit_scene(panel) -> None:
    """面板既有「场景提交」链路：重建引擎 → 刷新画布/火车 → 存档 → 轨道列表。"""
    panel._rebuild_engine()
    panel._refresh_canvas_scene()
    panel._preview_train_visuals()
    panel._save_scene(panel._current_page_id)
    panel._refresh_track_list()


def mirror_selected_tracks(panel, axis: str) -> int:
    """镜像面板当前选中的轨道并走既有重建/存档链路。返回镜像条数。"""
    tids = selected_track_ids(getattr(panel, "_track_list", None))
    if not tids:
        _panel_status(panel, "请先在轨道列表选择要镜像的轨道(Ctrl/Shift 多选)")
        return 0
    tracks = getattr(panel, "_tracks", {}) or {}
    snapshot_route_undo(panel)          # 撤销域登记：镜像前快照（R9 域口径）
    done = _mirror_tracks(tracks, tids, axis)
    if not done:
        return 0
    stats = remap_after_mirror(panel, tids)
    _commit_scene(panel)
    _panel_status(panel, f"已镜像 {done} 条轨道（{MIRROR_STATUS.get(axis, axis)}）"
                         f"· 道岔 {stats['switches']} · 火车 {stats['trains']} 已随镜像定位")
    return done


# ── R11 速度剖面预览（pyqtgraph 已装，零新依赖） ────────────────────────────

def _throttle_curve(tr, horizon: float, steps: int) -> List[Tuple[float, float]]:
    """单车的油门包络采样：现值 → 目标值按缓动曲线在 ramp_sec 内逼近。"""
    from ...engine.route_animator.easing import easing_value
    cur = float(getattr(tr, "throttle", 1.0))
    target = float(getattr(tr, "throttle_target", cur))
    ramp = float(getattr(tr, "ramp_sec", 0.0) or 0.0)
    name = str(getattr(tr, "ramp_easing", "OutCubic") or "OutCubic")
    pts: List[Tuple[float, float]] = []
    if ramp <= 0.0 or abs(target - cur) < 1e-12:
        return [(0.0, target), (horizon, target)]
    for i in range(steps + 1):
        t = horizon * i / steps
        p = min(1.0, t / ramp)
        pts.append((t, cur + (target - cur) * easing_value(name, p)))
    return pts


def _speed_profile_series(tr, horizon: float, steps: int) \
        -> Tuple[List[float], List[float]]:
    """单车速度剖面 = base_speed × throttle(t)（世界单位/秒）。"""
    curve = _throttle_curve(tr, horizon, steps)
    base = float(getattr(tr, "base_speed", 0.0) or 0.0)
    xs = [t for t, _ in curve]
    ys = [base * v for _, v in curve]
    return xs, ys


def _profile_horizon(trains) -> float:
    """剖面时间窗：默认 10s，随最长斜坡扩到 60s（只看目标斜坡窗口）。"""
    horizon = 10.0
    for tr in trains.values():
        ramp = float(getattr(tr, "ramp_sec", 0.0) or 0.0)
        horizon = max(horizon, min(60.0, ramp * 2.0))
    return horizon


def _ordered_train_ids(panel, trains) -> list:
    """要画的车：面板选中集（仍存在的）优先，否则全部。"""
    try:
        sel = [str(t) for t in panel._fleet_selected_train_ids()]
    except Exception:
        sel = []
    ordered = [t for t in sel if t in trains]
    return ordered or list(trains.keys())


class SpeedProfileDialog(QDialog):
    """速度剖面浮窗：每车一条 base_speed×throttle(t) 曲线（pyqtgraph）。"""

    #: 曲线颜色轮换（无裸 hex 例外：图表数据色，主题令牌无 8 色序列）
    PALETTE = ("#4fc3f7", "#ffb74d", "#81c784", "#e57373",
               "#ba68c8", "#fff176", "#4db6ac", "#f06292")

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent or panel)
        self._panel = panel
        self.setWindowTitle("火车速度剖面预览")
        self.resize(680, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        from ..qascade_theme import TEXT_SECONDARY
        from PyQt6.QtWidgets import QLabel
        tip = QLabel("速度 = 最大速度 × 油门包络（现值→目标值, 按缓动预设逼近）。"
                     "刷新读取引擎当前状态；选中火车只画选中, 无选中画全部。")
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        lay.addWidget(tip)
        self._plot = None
        try:
            import pyqtgraph as pg
            from ..qascade_theme import BG_ROOT
            self._plot = pg.PlotWidget(background=BG_ROOT)
            for name in ("bottom", "left"):
                axis = self._plot.getAxis(name)
                axis.setPen(pg.mkPen(TEXT_SECONDARY))
                axis.setTextPen(pg.mkPen(TEXT_SECONDARY))
            self._plot.addLegend(offset=(8, 8))
            self._plot.showGrid(x=True, y=True, alpha=0.25)
            self._plot.setLabel("bottom", "时间", units="s")
            self._plot.setLabel("left", "速度", units="u/s")
        except Exception:
            self._plot = None
        if self._plot is not None:
            lay.addWidget(self._plot, 1)
        else:
            from ..qascade_theme import BRIGHT_WARN
            err = QLabel("pyqtgraph 不可用，无法绘制曲线。")
            err.setStyleSheet(f"color:{BRIGHT_WARN};background:transparent;")
            lay.addWidget(err, 1)
        btns = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.rebuild)
        btns.addWidget(refresh_btn)
        btns.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        self.rebuild()

    @staticmethod
    def _trains_of(panel) -> dict:
        engine = getattr(panel, "_engine", None)
        return getattr(engine, "_trains", {}) or {}

    def _plot_one(self, trains, tid: str, idx: int, horizon: float) -> int:
        """画单车曲线；返回采样点数（车不存在/异常 → 0，绝不炸窗口）。"""
        tr = trains.get(tid)
        if tr is None:
            return 0
        try:
            xs, ys = _speed_profile_series(tr, horizon, self.STEPS)
        except Exception:
            return 0
        name = str(getattr(self._panel, "_train_names", {}).get(tid, tid))
        color = self.PALETTE[idx % len(self.PALETTE)]
        self._plot.plot(xs, ys, pen=color, name=name[:18])
        return len(xs)

    def _plot_ramp_marker(self, trains, ordered) -> None:
        """最长斜坡窗口参考线（无斜坡车不画）。"""
        for tid in ordered[:16]:
            ramp = float(getattr(trains.get(tid), "ramp_sec", 0.0) or 0.0)
            if ramp > 0.0:
                self._plot.addLine(x=ramp, pen="#666666")
                return

    #: 采样点数（0..horizon 等分；>0 供离屏探针断言"曲线真画"）
    STEPS = 200

    def rebuild(self) -> None:
        """重画全部曲线（离屏可构造：无 pyqtgraph 时早退不炸）。"""
        if self._plot is None:
            return
        self._plot.clear()
        trains = self._trains_of(self._panel)
        ordered = _ordered_train_ids(self._panel, trains)
        horizon = _profile_horizon(trains)
        self.point_count = 0
        for idx, tid in enumerate(ordered[:16]):
            self.point_count += self._plot_one(trains, str(tid), idx, horizon)
        self._plot_ramp_marker(trains, ordered)
        self.setWindowTitle(
            f"火车速度剖面预览 · {len(ordered[:16])} 列 · {horizon:.0f}s 窗")


def open_speed_profile(panel) -> None:
    """面板按钮入口：单例浮窗（随面板销毁）。"""
    dlg = getattr(panel, "_speed_profile_dlg", None)
    if dlg is None:
        dlg = popup(SpeedProfileDialog(panel))
        panel._speed_profile_dlg = dlg
        dlg.destroyed.connect(lambda: setattr(panel, "_speed_profile_dlg", None))
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    dlg.rebuild()


def _selected_train_ids(panel) -> List[str]:
    try:
        return [str(t) for t in panel._fleet_selected_train_ids()]
    except Exception:
        return []


def _make_button(text: str, tip: str, slot) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(22)
    btn.setToolTip(tip)
    btn.clicked.connect(slot)
    return btn


def inject_track_ops_buttons(panel, track_list: QWidget) -> None:
    """把 镜像/速度剖面/油门包络/段循环/顺序生成轨道 + 顺序跟随 插到轨道列表区。"""
    from .runner_order_follow import inject_order_follow
    lay = track_list.parentWidget().layout() if track_list is not None else None
    if lay is None:
        return
    from ..flow_layout import FlowLayout
    row = FlowLayout()
    row.setContentsMargins(0, 0, 0, 2)
    row.setSpacing(6)
    row.addWidget(_make_button(
        "镜像左右", "选中轨道 x 取反 + 点序反转（对称舞台轨道从两条变一条）",
        lambda: mirror_selected_tracks(panel, "y")))
    row.addWidget(_make_button(
        "镜像上下", "选中轨道 y 取反 + 点序反转",
        lambda: mirror_selected_tracks(panel, "x")))
    row.addWidget(_make_button(
        "速度剖面", "pyqtgraph 预览 每车 最大速度×油门(t) 曲线",
        lambda: open_speed_profile(panel)))
    row.addWidget(_make_button(
        "油门包络", "R3 每车 0..60s 油门包络时间轴（贝塞尔编辑器 + 节拍吸附）",
        lambda: _open_envelope(panel)))
    row.addWidget(_make_button(
        "段循环", "R8 选中轨道上的火车限定在 A-B 弧段内循环（0/0 关闭）",
        lambda: _open_loop_span(panel)))
    row.addWidget(_make_button(
        "顺序生成轨道", "R14 按「跑动顺序」当前页的设备顺序串成一条轨道",
        lambda: _apply_order_track(panel)))
    row.addStretch()
    inject_order_follow(panel, row)     # R14 补遗：顺序跟随开关（默认关）
    # 插在轨道列表控件之前（按钮行在列表上方）
    lay.insertLayout(lay.indexOf(track_list), row)


def _open_loop_span(panel) -> None:
    """段循环设置浮窗（逻辑与浮窗都在 runner_loop_span）。"""
    from .runner_loop_span import open_loop_span_dialog
    open_loop_span_dialog(panel)


def _open_envelope(panel) -> None:
    from .runner_envelope import open_envelope_dialog
    open_envelope_dialog(panel)


def _apply_order_track(panel) -> None:
    from .runner_order_track import apply_order_as_track
    apply_order_as_track(panel)


def set_loop_span_for_selected(panel, s0, s1) -> int:
    """R8 段循环面板入口（实现在 runner_loop_span；此处再导出保旧导入面）。"""
    from .runner_loop_span import set_loop_span_for_selected as _impl
    return _impl(panel, s0, s1)


def order_follow_enabled(panel) -> bool:
    """R14 顺序跟随开关状态（实现在 runner_order_follow；再导出保旧导入面）。"""
    from .runner_order_follow import order_follow_enabled as _impl
    return _impl(panel)


def set_order_follow(panel, on: bool) -> None:
    """R14 顺序跟随开关（实现在 runner_order_follow；再导出保旧导入面）。"""
    from .runner_order_follow import set_order_follow as _impl
    _impl(panel, on)


def _order_follow_tick(panel) -> bool:
    """R14 跟随轮询槽（实现在 runner_order_follow；再导出保旧导入面）。"""
    from .runner_order_follow import order_follow_tick as _impl
    return _impl(panel)
