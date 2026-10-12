# -*- coding: utf-8 -*-
"""跑动面板车队运动控件（深查③ R2 缓动预设库 / R6 错峰出发 / R1 斜坡秒数）。

「车队状态·运行控制」区的注入式扩展：油门斜坡秒数 + 缓动预设下拉 +
试跑按钮 + 错峰出发步长/按钮。全部逻辑在本模块闭环，`runner_panel`
只加一行 `inject_motion_controls(panel, tools)`；油门写入统一经
`set_throttle_with_panel_motion`（带面板斜坡/缓动设置）。

缓动名与 `engine.route_animator.easing` 同源（QEasingCurve 同名语义），
未装 pyqtgraph 不影响本模块（速度剖面在 runner_track_ops）。
"""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
)

#: 试跑斜坡时长（秒）：油门瞬间归零 → 按所选缓动爬回原值
_TEST_RAMP_SEC = 2.0
_TEST_ZERO_DELAY_MS = 150

DEFAULT_RAMP_SEC = 0.0
DEFAULT_EASING = "OutCubic"


def _status(panel, text: str) -> None:
    """面板状态行写入（异常静默；注入控件不阻断面板）。"""
    try:
        panel._set_status(text)
    except Exception:
        pass


def motion_ramp_sec(panel) -> float:
    """面板当前油门斜坡秒数（控件未建时 0 = 立即生效，原行为）。"""
    v = getattr(panel, "_motion_ramp_sec", None)
    return DEFAULT_RAMP_SEC if v is None else max(0.0, float(v))


def motion_easing(panel) -> str:
    """面板当前缓动预设名（控件未建时 OutCubic）。"""
    name = getattr(panel, "_motion_easing", None)
    return str(name or DEFAULT_EASING)


def set_throttle_with_panel_motion(engine, panel, tid: str, throttle: float) -> None:
    """面板侧油门写入统一入口：带斜坡秒数与缓动预设（ramp=0 立即生效）。"""
    engine.set_throttle(tid, throttle,
                        ramp_sec=motion_ramp_sec(panel),
                        easing=motion_easing(panel))


def _selected_train_ids(panel) -> list:
    try:
        return list(panel._fleet_selected_train_ids())
    except Exception:
        return []


def apply_stagger(panel, step_sec: float) -> int:
    """R6 错峰出发：按选中序给每车 start_delay = i*step（step<=0 全部清零）。"""
    tids = _selected_train_ids(panel)
    if not tids:
        _status(panel, "请先在车队状态树选中火车(Ctrl/Shift 多选)")
        return 0
    step = max(0.0, float(step_sec))
    for i, tid in enumerate(tids):
        panel._engine.set_train_start_delay(tid, step * i)
    _status(panel, "已清零出发延迟" if step <= 0.0 else
            f"错峰出发: {len(tids)} 列按 {step:.2f}s 步长依次出发"
            f"(末车延迟 {step * (len(tids) - 1):.2f}s)")
    return len(tids)


def test_run_easing(panel, easing_name: str) -> int:
    """R2 试跑：选中车油门瞬间归零 → 按所选缓动在 2s 内爬回原值。"""
    tids = _selected_train_ids(panel)
    if not tids:
        _status(panel, "请先在车队状态树选中火车再试跑缓动")
        return 0
    engine = panel._engine
    restore: dict = {}
    for tid in tids:
        tr = engine._trains.get(tid)
        if tr is None:
            continue
        restore[tid] = float(getattr(tr, "throttle_target", tr.throttle))
        engine.set_throttle(tid, 0.0, ramp_sec=0.0)
    def _ramp_back() -> None:
        for tid, target in restore.items():
            engine.set_throttle(tid, target,
                                ramp_sec=_TEST_RAMP_SEC, easing=easing_name)
    QTimer.singleShot(_TEST_ZERO_DELAY_MS, _ramp_back)
    _status(panel, f"缓动试跑: {len(restore)} 列 · {easing_name} · "
                   f"{_TEST_RAMP_SEC:.0f}s 爬坡")
    return len(restore)


_LABEL_QSS = "color:{c};font-size:11px;background:transparent;"


def _pair(text: str, widget):
    """「标签 + 控件」打包为一个流式单元（换行时两者不分家）。"""
    from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget
    from .. import qascade_theme as _th
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lab = QLabel(text)
    lab.setStyleSheet(_LABEL_QSS.format(c=_th.TEXT_SECONDARY))
    lay.addWidget(lab)
    lay.addWidget(widget)
    return box


def _ramp_items(panel) -> list:
    """R1 油门斜坡秒数 + R2 缓动预设 + 试跑。"""
    ramp_spin = QDoubleSpinBox()
    ramp_spin.setRange(0.0, 30.0)
    ramp_spin.setDecimals(1)
    ramp_spin.setSingleStep(0.5)
    ramp_spin.setValue(DEFAULT_RAMP_SEC)
    ramp_spin.setToolTip("油门斜坡秒数: 油门变化经缓动曲线在 N 秒内逼近"
                         "(0=立即生效)。作用于卡片/车队/摇杆全部油门写入。")
    ramp_spin.valueChanged.connect(
        lambda v, p=panel: setattr(p, "_motion_ramp_sec", max(0.0, float(v))))
    panel._motion_ramp_sec = DEFAULT_RAMP_SEC
    ease_combo = QComboBox()
    ease_combo.setMinimumWidth(96)
    from ...engine.route_animator.easing import easing_names
    for name in easing_names():
        ease_combo.addItem(name, name)
    ease_combo.setCurrentIndex(max(0, ease_combo.findData(DEFAULT_EASING)))
    ease_combo.setToolTip("油门斜坡的缓动曲线（与 QEasingCurve 同名同语义）")
    ease_combo.currentIndexChanged.connect(
        lambda _i, p=panel, c=ease_combo: setattr(
            p, "_motion_easing", str(c.itemData(c.currentIndex()) or DEFAULT_EASING)))
    panel._motion_easing = DEFAULT_EASING
    test_btn = QPushButton("试跑")
    test_btn.setFixedHeight(22)
    test_btn.setToolTip("选中火车油门归零后按当前缓动预设 2 秒爬回(不改参数)")
    test_btn.clicked.connect(
        lambda: test_run_easing(
            panel, str(ease_combo.itemData(ease_combo.currentIndex())
                       or DEFAULT_EASING)))
    return [_pair("斜坡s", ramp_spin), _pair("缓动", ease_combo), test_btn]


def _stagger_items(panel) -> list:
    """R6 错峰出发：步长 + 错峰出发 + 清零延迟。"""
    step_spin = QDoubleSpinBox()
    step_spin.setRange(0.0, 30.0)
    step_spin.setDecimals(2)
    step_spin.setSingleStep(0.5)
    step_spin.setValue(0.5)
    step_spin.setToolTip("错峰出发: 按选中序每车延迟 = 序号×步长(波浪出发); 0=清零延迟")
    stagger_btn = QPushButton("错峰出发")
    stagger_btn.setFixedHeight(22)
    stagger_btn.setToolTip("按车队树选中序给每车设 start_delay=i*步长, 即刻生效")
    stagger_btn.clicked.connect(
        lambda: apply_stagger(panel, float(step_spin.value())))
    clear_btn = QPushButton("清零延迟")
    clear_btn.setFixedHeight(22)
    clear_btn.setToolTip("选中火车出发延迟全部清零(立即出发)")
    clear_btn.clicked.connect(lambda: apply_stagger(panel, 0.0))
    return [_pair("错峰步长s", step_spin), stagger_btn, clear_btn]


def inject_motion_controls(panel, tools_layout) -> None:
    """在「运行控制」区油门滑条行之下注入运动控制（面板源码零新逻辑）。

    C5 修（截图 13-21-32/38「运行控制区重叠」）：修前两条不换行 QHBoxLayout 分别
    塞 5 / 4 个控件，右栏 ~290px 放不下 → Qt 把控件压到最小宽以下叠画。现在
    「标签 + 控件」成对打包进 FlowLayout，宽度不足自动换行。
    """
    if tools_layout is None or getattr(panel, "_motion_injected", False):
        return
    panel._motion_injected = True
    from PyQt6.QtWidgets import QWidget
    from ..flow_layout import FlowLayout
    box = QWidget()
    flow = FlowLayout(box)
    flow.setContentsMargins(0, 0, 0, 0)
    flow.setSpacing(6)
    for widget in _ramp_items(panel) + _stagger_items(panel):
        flow.addWidget(widget)
    tools_layout.addWidget(box)
