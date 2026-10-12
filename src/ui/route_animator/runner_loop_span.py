# -*- coding: utf-8 -*-
"""R8 单轨 A-B 段循环：浮窗 + 面板入口（逻辑与 R183-W2 从 runner_track_ops 分出）。

引擎侧 `set_train_loop_span` / `clear_train_loop_span` 是唯一真源；本模块只
负责取值、挑目标车与展示（含"用当前车头位置当 A"这类实操快捷）。
拆出动机：`runner_track_ops.py` 逼近 500 行门禁。
"""
from __future__ import annotations
from ..editor_workspace_regions import popup

import math
from typing import List

from PyQt6.QtWidgets import (
    QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from ..qascade_theme import TEXT_SECONDARY


def _track_length(track) -> float:
    """轨道折线总长（世界单位；坏数据 → 0.0）。"""
    pts = (track or {}).get("points") or []
    total = 0.0
    for i in range(1, len(pts)):
        total += math.hypot(float(pts[i][0]) - float(pts[i - 1][0]),
                            float(pts[i][1]) - float(pts[i - 1][1]))
    return total


def _status(panel, text: str) -> None:
    try:
        panel._set_status(text)
    except Exception:
        pass


def _selected_train_ids(panel) -> List[str]:
    try:
        return [str(t) for t in panel._fleet_selected_train_ids()]
    except Exception:
        return []


def _selected_track_ids(panel) -> List[str]:
    try:
        from .runner_track_ops import selected_track_ids
        return selected_track_ids(getattr(panel, "_track_list", None))
    except Exception:
        return []


def _loop_span_targets(panel, trains) -> List[str]:
    """段循环作用对象：车队树选中集优先；无选中 → 选中轨道上的 owner；再退首车。"""
    tids = _selected_train_ids(panel)
    if tids:
        return tids
    track_ids = set(_selected_track_ids(panel))
    owners = [str(t) for t, tr in trains.items()
              if str(getattr(tr, "_home_edge_id", "")) in track_ids]
    return owners or list(trains.keys())[:1]


def _span_edge_of(tr) -> str:
    """火车段循环钉住的边：home edge 优先，缺省车头当前边。"""
    home = str(getattr(tr, "_home_edge_id", "") or "")
    if home:
        return home
    return str(tr.cars[0].edge_id) if tr.cars else ""


def set_loop_span_for_selected(panel, s0: float, s1: float) -> int:
    """给选中火车设 A-B 段循环（弧长单位）；`s1<=s0` = 关闭（恢复整轨）。

    未选中火车时退化为「选中轨道上的 owner 火车」；无车 → 状态行提示。
    返回成功设置的车数。
    """
    engine = getattr(panel, "_engine", None)
    trains = getattr(engine, "_trains", None) or {}
    tids = _loop_span_targets(panel, trains)
    if not tids:
        _status(panel, "请先添加火车，再设置 A-B 段循环")
        return 0
    if float(s1) <= float(s0):
        for tid in tids:
            engine.clear_train_loop_span(tid)
        _status(panel, f"已关闭 {len(tids)} 列火车的 A-B 段循环")
        return len(tids)
    done = _apply_spans(engine, trains, tids, float(s0), float(s1))
    if done:
        _status(panel, f"已限定 {done} 列火车在 [{float(s0):.0f}, {float(s1):.0f}] "
                       f"弧长段内循环（单程模式到段端停）")
    else:
        _status(panel, "这些火车没有可用轨道，无法限定段循环")
    return done


def _apply_spans(engine, trains, tids, s0: float, s1: float) -> int:
    """逐车写段循环并把所在边钉住（无车/无轨跳过）。返回成功车数。"""
    done = 0
    for tid in tids:
        tr = trains.get(tid)
        if tr is None:
            continue
        eid = _span_edge_of(tr)
        if engine.set_train_loop_span(tid, s0, s1):
            tr.loop_span_edge = eid     # 显式钉在设置时的轨道上
            done += 1
    return done


def open_loop_span_dialog(panel) -> None:
    """面板按钮入口：单例浮窗，按当前选中轨道刷新默认区间。"""
    tracks = getattr(panel, "_tracks", None) or {}
    tids = _selected_track_ids(panel)
    tid = tids[0] if tids else next(iter(tracks), "")
    dlg = getattr(panel, "_loop_span_dlg", None)
    if dlg is None:
        dlg = popup(LoopSpanDialog(panel))
        panel._loop_span_dlg = dlg
        dlg.destroyed.connect(lambda: setattr(panel, "_loop_span_dlg", None))
    dlg.set_track(tid, _track_length(tracks.get(tid)))
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


class LoopSpanDialog(QDialog):
    """A-B 段循环设置（弧长单位 = 世界单位，与轨道 cum 同口径）。"""

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent or panel)
        self._panel = panel
        self._tid = ""
        self._length = 0.0
        self.setWindowTitle("A-B 段循环")
        self.resize(420, 190)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        lay.addWidget(self._info)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("A 起点弧长"))
        self._s0 = QDoubleSpinBox()
        self._s0.setRange(0.0, 1e9)
        self._s0.setDecimals(1)
        row.addWidget(self._s0)
        row.addWidget(QLabel("B 终点弧长"))
        self._s1 = QDoubleSpinBox()
        self._s1.setRange(0.0, 1e9)
        self._s1.setDecimals(1)
        row.addWidget(self._s1)
        lay.addLayout(row)
        btns = QHBoxLayout()
        head_btn = QPushButton("A = 当前车头")
        head_btn.setFixedHeight(22)
        head_btn.setToolTip("把 A 设为选中火车此刻的车头弧长")
        head_btn.clicked.connect(self._use_head_as_a)
        btns.addWidget(head_btn)
        apply_btn = QPushButton("应用段循环")
        apply_btn.setFixedHeight(22)
        apply_btn.clicked.connect(self._apply)
        btns.addWidget(apply_btn)
        clear_btn = QPushButton("关闭段循环")
        clear_btn.setFixedHeight(22)
        clear_btn.clicked.connect(self._clear)
        btns.addWidget(clear_btn)
        btns.addStretch()
        close = QPushButton("关闭窗口")
        close.setFixedHeight(22)
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        w = QWidget()
        w.setLayout(btns)
        lay.addWidget(w)

    def set_track(self, tid: str, length: float) -> None:
        """按选中轨道刷新默认区间（A 保留现值，B = 全长）。"""
        self._tid = str(tid or "")
        self._length = float(length or 0.0)
        self._s1.setValue(self._length)
        self._info.setText(
            f"轨道 {self._tid[:8] or '(未选)'} · 全长 {self._length:.1f}。"
            "区间只在所属轨道内回绕；单程模式到段端停住（出段自动停）。"
            "B ≤ A = 关闭段循环。")

    def _use_head_as_a(self) -> None:
        """把 A 设为选中火车（或任一车）此刻的车头弧长。"""
        trains = getattr(self._panel._engine, "_trains", {}) or {}
        for tid in _selected_train_ids(self._panel):
            tr = trains.get(tid)
            if tr is not None and tr.cars:
                self._s0.setValue(float(tr.nose_s))
                return
        for tr in trains.values():
            if tr.cars:
                self._s0.setValue(float(tr.nose_s))
                return

    def _apply(self) -> None:
        set_loop_span_for_selected(self._panel, float(self._s0.value()),
                                   float(self._s1.value()))

    def _clear(self) -> None:
        set_loop_span_for_selected(self._panel, 0.0, 0.0)


__all__ = ["LoopSpanDialog", "open_loop_span_dialog",
           "set_loop_span_for_selected"]
