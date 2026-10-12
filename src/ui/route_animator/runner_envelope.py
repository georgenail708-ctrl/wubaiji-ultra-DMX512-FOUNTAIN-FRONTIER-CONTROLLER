# -*- coding: utf-8 -*-
"""R3/R4：每车「油门包络」时间轴（复用 trajectory_curve_widget 贝塞尔编辑器）。

深查③ R3：跑动只有手动推子，没有可回放的编排曲线。本模块给每列火车一条
0..duration 秒的油门包络（``TrajectoryCurve``，与 Programmer 轨迹曲线同一
数据模型/同一编辑器控件/同一节拍吸附），播放时由面板帧循环按
``set_throttle(tid, curve.value_at(elapsed))`` 推给引擎——引擎零改动。

R4：编辑器接 ``trajectory_clock.TrajectoryClock``（BPM 网格 + 松手吸附，
真工程 bpm=120），吸附开关与 Programmer 侧同款（1/2/4 拍）。

数据落点 = 跑动场景 ``_page_scenes[page]["envelopes"]``（本模块
``_ENVELOPE_KEY``），随 `runner_panel._save_scene` → `runner_scene_store.
persist_scene` 写进 ``project.route_animator_state``，与 R3 的持久化合并。

帧循环接线：`runner_panel._tick_impl` 首行 ``advance_envelopes(self, dt)``
（引擎 tick 之前——包络先写油门，当帧即生效）。无包络/未播放时零开销。
"""
from __future__ import annotations
from ..editor_workspace_regions import popup

import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from ..widget_kit import PanelSection, prop_row
from ..qascade_theme import TEXT_SECONDARY
from ..trajectory_curve import TrajectoryCurve

log = logging.getLogger(__name__)

#: 场景字典里的包络键（`_save_scene` 写出、`_restore_scene` 读回）
_ENVELOPE_KEY = "envelopes"

#: 包络时长默认/边界（秒）——"0..60s 油门包络线"
DEFAULT_DURATION = 30.0
MIN_DURATION = 5.0
MAX_DURATION = 60.0

#: 节拍吸附档（0=关；与 TrajectoryClock.SNAP_DIVISIONS 同义）
SNAP_CHOICES = ((0, "关"), (1, "1 拍"), (2, "2 拍"), (4, "4 拍"))


def envelopes_of(panel) -> Dict[str, dict]:
    """面板的包络表（懒建；键 = 火车 id）。"""
    table = getattr(panel, "_throttle_envelopes", None)
    if table is None:
        table = {}
        panel._throttle_envelopes = table
    return table


def current_page_envelopes(panel) -> Dict[str, dict]:
    """当前页场景里的包络表（未保存过 → 空 dict，不新建）。"""
    pid = getattr(panel, "_current_page_id", None)
    scenes = getattr(panel, "_page_scenes", None) or {}
    scene = scenes.get(pid) if pid else None
    if not isinstance(scene, dict):
        return {}
    env = scene.get(_ENVELOPE_KEY)
    return env if isinstance(env, dict) else {}


def hydrate_envelopes(panel) -> int:
    """从当前页场景重建包络对象（切页/载档后调用）。返回条数。"""
    raw = current_page_envelopes(panel)
    table = envelopes_of(panel)
    table.clear()
    for tid, item in raw.items():
        env = envelope_from_dict(item)
        if env is not None:
            table[str(tid)] = env
    return len(table)


def envelope_from_dict(item) -> Optional[dict]:
    """场景 dict → 运行态包络（坏数据返回 None，不炸面板）。"""
    if not isinstance(item, dict):
        return None
    try:
        curve = TrajectoryCurve.from_dict(item.get("curve") or {})
    except Exception:
        log.debug("throttle envelope curve parse failed", exc_info=True)
        return None
    return {
        "curve": curve,
        "duration": float(item.get("duration", DEFAULT_DURATION)
                          or DEFAULT_DURATION),
        "loop": bool(item.get("loop", False)),
        "active": bool(item.get("active", False)),
        "elapsed": 0.0,
    }


def envelope_to_dict(env: dict) -> dict:
    """运行态包络 → 可持久化 dict（只存静态字段，elapsed 不落盘）。"""
    curve = env.get("curve")
    data = {}
    try:
        data = curve.to_dict() if curve is not None else {}
    except Exception:
        log.debug("throttle envelope serialize failed", exc_info=True)
    return {
        "curve": data,
        "duration": float(env.get("duration", DEFAULT_DURATION)
                          or DEFAULT_DURATION),
        "loop": bool(env.get("loop", False)),
        "active": bool(env.get("active", False)),
    }


def ensure_envelope(panel, tid: str) -> dict:
    """取/建某车的包络（默认 Linear 0→1 全开曲线，时长 30s）。"""
    table = envelopes_of(panel)
    env = table.get(str(tid))
    if env is None:
        curve = TrajectoryCurve(name="油门包络", duration=DEFAULT_DURATION)
        env = {"curve": curve, "duration": DEFAULT_DURATION,
               "loop": True, "active": False, "elapsed": 0.0}
        table[str(tid)] = env
    return env


def set_envelope_duration(panel, tid: str, duration: float) -> dict:
    """改包络时长（**env 与本曲线必须同步**，否则 elapsed 与相位脱钩）。

    `curve.value_at(t)` 用曲线自己的 duration 算相位——只改 env["duration"]
    会让 1s 的包络按 30s 曲线求值（实测 0.5s 只出 1.7% 油门）。
    """
    env = ensure_envelope(panel, tid)
    dur = max(1.0, float(duration or DEFAULT_DURATION))
    env["duration"] = dur
    curve = env.get("curve")
    if curve is not None:
        try:
            curve.duration = dur
        except Exception:
            log.debug("envelope curve duration set failed", exc_info=True)
    return env


def _advance_one(engine, tid: str, env: dict, dt: float) -> None:
    """推进单条包络并写油门（非循环走完自动摘除激活位）。"""
    duration = max(1e-3, float(env.get("duration", DEFAULT_DURATION)
                              or DEFAULT_DURATION))
    elapsed = float(env.get("elapsed", 0.0)) + float(dt)
    if elapsed >= duration:
        if env.get("loop"):
            elapsed = elapsed % duration
        else:
            elapsed = duration
            env["active"] = False
    env["elapsed"] = elapsed
    curve = env.get("curve")
    if curve is None:
        return
    try:
        value = float(curve.value_at(elapsed))
    except Exception:
        log.debug("throttle envelope eval failed (tid=%s)", tid, exc_info=True)
        return
    try:
        engine.set_throttle(str(tid), max(0.0, min(1.0, value)), ramp_sec=0.0)
    except Exception:
        log.debug("throttle envelope write failed (tid=%s)", tid, exc_info=True)


def advance_envelopes(panel, dt: float) -> None:
    """帧循环钩子：推进全部**激活**包络，把油门写到引擎（R3）。

    在 ``engine.tick(dt)`` **之前**调用：包络值当帧即参与运动积分。
    写油门固定 ``ramp_sec=0``（包络本身即整形，叠加 R1 斜坡会二次延迟）；
    包络放完（非循环）→ 自动摘除激活位，停在该曲线终点值。
    """
    table = getattr(panel, "_throttle_envelopes", None)
    if not table or dt <= 0.0:
        return
    engine = getattr(panel, "_engine", None)
    if engine is None:
        return
    trains = getattr(engine, "_trains", {}) or {}
    for tid, env in list(table.items()):
        if not env.get("active"):
            continue
        if str(tid) not in trains:
            env["active"] = False     # 火车已删：停掉悬挂包络
            continue
        _advance_one(engine, tid, env, dt)


def _train_name(panel, tid: str) -> str:
    return str(getattr(panel, "_train_names", {}).get(tid, tid))


class ThrottleEnvelopeDialog(QDialog):
    """单车油门包络编辑器（TrajectoryCurveView 整体复用 + 节拍吸附 R4）。"""

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent or panel)
        self._panel = panel
        self._clock = None
        self.setWindowTitle("油门包络时间轴")
        self.resize(720, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self._train_combo = QComboBox()
        for tid in (getattr(panel, "_engine", None)._trains
                    if getattr(panel, "_engine", None) is not None else {}):
            self._train_combo.addItem(_train_name(panel, str(tid)), str(tid))
        self._train_combo.currentIndexChanged.connect(self._on_train_changed)
        root.addWidget(prop_row("火车", self._train_combo))

        self._view = self._build_view()
        root.addWidget(self._view, 1)

        root.addWidget(self._build_controls())
        root.addWidget(self._build_buttons())
        self._load_current()

    def _build_view(self) -> QWidget:
        # R4 补遗：带**常驻**节拍网格的曲线视图（吸附关也画格线，看拍不必须先开吸附）
        from .runner_beat_grid import attach_beat_clock, make_beat_grid_view
        view = make_beat_grid_view()
        view.set_mode("point")
        view.points_changed.connect(self._on_points_changed)
        attach_beat_clock(view)          # 时钟挂上（无音频 → 无网格，不画线）
        self._clock = getattr(view, "_snap_clock", None)
        return view

    def _build_controls(self) -> QWidget:
        sec = PanelSection("包络设置")
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("时长s"))
        self._dur_spin = QDoubleSpinBox()
        self._dur_spin.setRange(MIN_DURATION, MAX_DURATION)
        self._dur_spin.setDecimals(1)
        self._dur_spin.setSingleStep(5.0)
        self._dur_spin.setValue(DEFAULT_DURATION)
        self._dur_spin.valueChanged.connect(self._on_duration_changed)
        self._dur_spin.setToolTip("包络总时长（秒）：播放时按该时长循环/走完")
        row.addWidget(self._dur_spin)

        row.addWidget(QLabel("吸附"))
        self._snap_combo = QComboBox()
        for div, label in SNAP_CHOICES:
            self._snap_combo.addItem(label, div)
        self._snap_combo.currentIndexChanged.connect(self._on_snap_changed)
        self._snap_combo.setToolTip(
            "关键帧松手吸附到音频节拍网格（BPM 来自音频分析；无音频则原样落点）")
        row.addWidget(self._snap_combo)

        self._loop_check = QCheckBox("循环")
        self._loop_check.setChecked(True)
        self._loop_check.setToolTip("勾选=到时长后从头再来；不勾=走完停在终点值")
        row.addWidget(self._loop_check)
        row.addStretch()
        sec.add_layout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self._play_btn = QPushButton("播放包络")
        self._play_btn.setCheckable(True)
        self._play_btn.setFixedHeight(22)
        self._play_btn.setToolTip(
            "把该曲线按时间推给火车油门（引擎每帧读取）；再点一次停止并保持当前值")
        self._play_btn.toggled.connect(self._on_play_toggled)
        row2.addWidget(self._play_btn)
        self._preview_btn = QPushButton("预览速度剖面")
        self._preview_btn.setFixedHeight(22)
        self._preview_btn.setToolTip("打开 R11 速度剖面浮窗（最大速度×油门(t)）")
        self._preview_btn.clicked.connect(self._open_profile)
        row2.addWidget(self._preview_btn)
        row2.addStretch()
        self._hint = QLabel("")
        self._hint.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        row2.addWidget(self._hint)
        sec.add_layout(row2)
        return sec

    def _build_buttons(self) -> QWidget:
        row = QHBoxLayout()
        clear = QPushButton("清空包络")
        clear.setFixedHeight(22)
        clear.setToolTip("删除该车的包络（恢复纯手动油门）")
        clear.clicked.connect(self._on_clear)
        row.addWidget(clear)
        row.addStretch()
        close = QPushButton("关闭")
        close.setFixedHeight(22)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        w = QWidget()
        w.setLayout(row)
        return w

    # ── 数据流 ───────────────────────────────────────────────────────────

    def _current_tid(self) -> str:
        return str(self._train_combo.currentData() or "")

    def _load_current(self) -> None:
        """把当前火车的包络灌进编辑器（无则建默认全开曲线）。"""
        tid = self._current_tid()
        if not tid:
            self._hint.setText("面板还没有火车")
            return
        env = ensure_envelope(self._panel, tid)
        self._view.set_trajectory(env.get("curve"))
        for w, val in ((self._dur_spin, env.get("duration", DEFAULT_DURATION)),
                       (self._loop_check, bool(env.get("loop", True))),
                       (self._play_btn, bool(env.get("active", False)))):
            w.blockSignals(True)
            if isinstance(w, QDoubleSpinBox):
                w.setValue(float(val or DEFAULT_DURATION))
            else:
                w.setChecked(bool(val))
            w.blockSignals(False)
        self._hint.setText(f"{env.get('elapsed', 0.0):.1f}s / "
                           f"{float(env.get('duration', DEFAULT_DURATION)):.1f}s")

    def _on_train_changed(self, _idx: int) -> None:
        self._load_current()

    def _on_points_changed(self) -> None:
        tid = self._current_tid()
        if tid:
            env = ensure_envelope(self._panel, tid)
            env["curve"] = self._view._traj or env.get("curve")

    def _on_duration_changed(self, value: float) -> None:
        tid = self._current_tid()
        if not tid:
            return
        set_envelope_duration(self._panel, tid, float(value))

    def _on_snap_changed(self, _idx: int) -> None:
        division = int(self._snap_combo.currentData() or 0)
        if division <= 0:
            self._view.set_snap_division(0)
            return
        if self._clock is None:
            from .runner_beat_grid import attach_beat_clock
            self._clock = attach_beat_clock(self._view)
        self._view.set_snap_clock(self._clock)
        self._view.set_snap_division(division)

    def _on_play_toggled(self, on: bool) -> None:
        tid = self._current_tid()
        if not tid:
            return
        env = ensure_envelope(self._panel, tid)
        env["active"] = bool(on)
        if on:
            env["elapsed"] = 0.0

    def _on_clear(self) -> None:
        tid = self._current_tid()
        if not tid:
            return
        env = envelopes_of(self._panel).pop(tid, None)
        if env is not None:
            try:
                self._panel._engine.set_throttle(tid, 1.0, ramp_sec=0.0)
            except Exception:
                pass
        self._play_btn.setChecked(False)
        self._load_current()

    def _open_profile(self) -> None:
        from .runner_track_ops import open_speed_profile
        open_speed_profile(self._panel)

    def persist_into_scene(self) -> None:
        """把运行态包络写回场景（走面板单一咽喉 `_save_scene`）。

        `_save_scene` 已把包络表写进场景 dict 并 `persist_scene` 到工程
        （`route_animator_state`），此处只触发它——不在本模块重复实现。
        """
        pid = getattr(self._panel, "_current_page_id", None)
        if not pid:
            return
        try:
            self._panel._save_scene(pid)
        except Exception:
            log.debug("envelope persist into scene failed", exc_info=True)

    def accept(self) -> None:
        self.persist_into_scene()
        try:
            self._panel._refresh_track_list()
        except Exception:
            pass
        super().accept()


def open_envelope_dialog(panel) -> None:
    """面板按钮入口：单例浮窗（随面板销毁）。"""
    dlg = getattr(panel, "_envelope_dlg", None)
    if dlg is None:
        dlg = popup(ThrottleEnvelopeDialog(panel))
        panel._envelope_dlg = dlg
        dlg.destroyed.connect(lambda: setattr(panel, "_envelope_dlg", None))
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def envelopes_to_scene(panel) -> Dict[str, dict]:
    """面板包络表 → 场景可持久化 dict（`_save_scene` 单一调用点用）。"""
    return {tid: envelope_to_dict(env)
            for tid, env in (getattr(panel, "_throttle_envelopes", None)
                             or {}).items()}


def finish_scene_restore(panel) -> None:
    """场景恢复尾段（`_restore_scene` 两分支共用）：包络重建 + 画布刷新。"""
    hydrate_envelopes(panel)
    panel._refresh_canvas_scene()
    panel._preview_train_visuals()


def envelopes_summary(panel) -> List[str]:
    """当前页包络摘要（轨道列表区提示用；无包络给空表）。"""
    out: List[str] = []
    table = getattr(panel, "_throttle_envelopes", None) or {}
    for tid, env in table.items():
        if env.get("active"):
            state = "播放中"
        else:
            state = "待机"
        out.append(f"{_train_name(panel, str(tid))}: {state} "
                   f"{float(env.get('duration', DEFAULT_DURATION)):.0f}s")
    return out


__all__ = ["advance_envelopes", "ensure_envelope", "envelopes_of",
           "envelopes_summary", "envelopes_to_scene", "hydrate_envelopes",
           "envelope_to_dict", "envelope_from_dict", "set_envelope_duration",
           "open_envelope_dialog", "ThrottleEnvelopeDialog", "_ENVELOPE_KEY"]
