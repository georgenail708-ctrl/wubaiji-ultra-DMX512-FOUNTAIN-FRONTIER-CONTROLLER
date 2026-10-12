# -*- coding: utf-8 -*-
"""R12：轨道平滑参数（强度下拉 + 前后折线预览）。

深查③ R12：平滑跑在 worker 线程里但**无强度选项**（只有最小半径/吸附容差）。
本模块给 HUD 参数行注入「平滑强度」下拉：把强度折算成既有的三档管线参数
（Catmull-Rom 段内点数 `per_seg` / 最小半径倍率 / 去抖遍数），供
`runner_panel._start_async_smooth` 读取；并在平滑前后画一条对比折线预览
（`SmoothPreviewDialog`，自绘 QWidget，零新依赖），让"圆滑度"可预判。

不重写平滑算法：强度只调 `track_smooth.smooth_track` 的**入参与后处理**
（现成 `_cr_point` 族与 `min_radius` 全部复用）。
"""
from __future__ import annotations
from ..editor_workspace_regions import popup

import logging
from typing import List, Tuple

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..widget_kit import prop_row
from ..qascade_theme import BG_DEEP, SEP_LINE, TEXT_SECONDARY

log = logging.getLogger(__name__)

#: 强度档：key → (标签, per_seg, 半径倍率, **额外**去抖遍数)。
#: "标准" == 与修前逐字一致（smooth_track 内部固定两遍去抖，此处不再加）。
STRENGTH_CHOICES = (
    ("light", "轻(保留棱角)", 8, 0.5, 0),
    ("standard", "标准", 12, 1.0, 0),
    ("strong", "强", 16, 1.4, 1),
    ("max", "最大(最圆滑)", 20, 1.8, 2),
)
DEFAULT_STRENGTH = "standard"

_RAW_COLOR = "#E6B455"     # BRIGHT_WARN：平滑前折线
_SMOOTH_COLOR = "#56B65B"  # BRIGHT_SUCCESS：平滑后折线


def smooth_params(panel) -> Tuple[int, float, int]:
    """当前强度 → (per_seg, 半径倍率, 额外去抖遍数)；控件未建 → 标准档。"""
    key = str(getattr(panel, "_smooth_strength", DEFAULT_STRENGTH))
    for k, _label, per_seg, mult, extra in STRENGTH_CHOICES:
        if k == key:
            return (per_seg, mult, extra)
    return (12, 1.0, 0)


def smooth_strength_label(panel) -> str:
    key = str(getattr(panel, "_smooth_strength", DEFAULT_STRENGTH))
    for k, label, *_ in STRENGTH_CHOICES:
        if k == key:
            return label
    return "标准"


def apply_smooth_profile(panel, chain, snap_tol: float, min_radius: float,
                         length: int):
    """按当前强度算平滑入参：返回 (per_seg, min_radius_eff, 额外去抖遍数)。

    面板 `_start_async_smooth` 唯一调用点——长链仍走后台线程，只是参数由
    强度档决定（`per_seg` 同时受链长限制，避免点数爆掉）。
    """
    per_seg, mult, extra = smooth_params(panel)
    if length > 200:
        per_seg = min(per_seg, 4)          # 超长链降密（与修前逐字一致）
    return (max(2, int(per_seg)), max(0.0, float(min_radius) * float(mult)),
            max(0, int(extra)))


def smooth_chain(chain, snap_tol: float, min_radius: float, per_seg: int,
                 extra_passes: int):
    """调既有 `smooth_track`；`extra_passes` 表示标准两遍去抖之外**再加**的遍数。"""
    from ...engine.route_animator.track_smooth import moving_average, smooth_track
    pts, closed, lap_count = smooth_track(
        chain, snap_tol=snap_tol, min_radius=min_radius, per_seg=per_seg)
    for _ in range(max(0, int(extra_passes))):
        pts = moving_average(pts, 2, closed)
    return pts, closed, lap_count


class SmoothPreviewDialog(QDialog):
    """平滑前后折线对比（自绘；原始链 vs 平滑结果同框）。"""

    def __init__(self, panel, raw=None, smoothed=None, parent=None) -> None:
        super().__init__(parent or panel)
        self._panel = panel
        self._raw = list(raw or [])
        self._smoothed = list(smoothed or [])
        self.setWindowTitle("轨道平滑预览")
        self.resize(560, 400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self._canvas = _PreviewCanvas(self)
        lay.addWidget(self._canvas, 1)
        info = QLabel(f"强度：{smooth_strength_label(panel)} · "
                      f"原始 {len(self._raw)} 点 → 平滑 {len(self._smoothed)} 点")
        info.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        lay.addWidget(info)
        row = QHBoxLayout()
        refresh = QPushButton("重算预览")
        refresh.setFixedHeight(22)
        refresh.setToolTip("按当前强度重算最近一次画轨的平滑结果（不提交轨道）")
        refresh.clicked.connect(self.rebuild)
        row.addWidget(refresh)
        row.addStretch()
        close = QPushButton("关闭")
        close.setFixedHeight(22)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        w = QWidget()
        w.setLayout(row)
        lay.addWidget(w)

    def set_chains(self, raw, smoothed) -> None:
        self._raw = list(raw or [])
        self._smoothed = list(smoothed or [])
        self._canvas.set_chains(self._raw, self._smoothed)

    def rebuild(self) -> None:
        """按当前强度重算（用最近一次画轨的原始链；无记录 → 原样保留）。"""
        raw = getattr(self._panel, "_last_raw_chain", None) or []
        if len(raw) < 2:
            self._canvas.set_chains(self._raw, self._smoothed)
            return
        per_seg, rmin, extra = apply_smooth_profile(
            self._panel, raw, 12.0, getattr(self._panel, "_min_radius", 25.0),
            len(raw))
        try:
            pts, _closed, _laps = smooth_chain(raw, 12.0, rmin, per_seg, extra)
        except Exception:
            log.debug("smooth preview recompute failed", exc_info=True)
            return
        self.set_chains(raw, pts)
        self.setWindowTitle(f"轨道平滑预览 · {smooth_strength_label(self._panel)}")


class _PreviewCanvas(QWidget):
    """两条折线的等比例适配绘制（原始=虚线橙，平滑=实线绿）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._raw: List[Tuple[float, float]] = []
        self._smoothed: List[Tuple[float, float]] = []
        self.setMinimumHeight(220)
        self.setStyleSheet(f"background:{BG_DEEP};")

    def set_chains(self, raw, smoothed) -> None:
        self._raw = [tuple(p) for p in (raw or [])]
        self._smoothed = [tuple(p) for p in (smoothed or [])]
        self.update()

    def _bounds(self):
        pts = list(self._raw) + list(self._smoothed)
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(BG_DEEP))
        p.setPen(QPen(QColor(SEP_LINE), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        b = self._bounds()
        if b is None:
            p.setPen(QColor(TEXT_SECONDARY))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "先画一条轨道，再打开预览")
            return
        x0, y0, x1, y1 = b
        pad = 14
        w = max(1.0, (x1 - x0) or 1.0)
        h = max(1.0, (y1 - y0) or 1.0)
        sx = (self.width() - 2 * pad) / w
        sy = (self.height() - 2 * pad) / h
        s = min(sx, sy)

        def _to(p):
            return QPointF(pad + (p[0] - x0) * s,
                           self.height() - pad - (p[1] - y0) * s)

        for pts, color, style, width in (
                (self._raw, _RAW_COLOR, Qt.PenStyle.DashLine, 1.4),
                (self._smoothed, _SMOOTH_COLOR, Qt.PenStyle.SolidLine, 2.0)):
            if len(pts) < 2:
                continue
            p.setPen(QPen(QColor(color), width, style))
            for i in range(len(pts) - 1):
                p.drawLine(_to(pts[i]), _to(pts[i + 1]))


def inject_smooth_row(panel, params_layout) -> None:
    """在 HUD 参数行注入「平滑强度」下拉 + 「平滑预览」按钮（面板零新逻辑）。"""
    if params_layout is None or getattr(panel, "_smooth_opts_injected", False):
        return
    panel._smooth_opts_injected = True
    panel._smooth_strength = DEFAULT_STRENGTH
    combo = QComboBox()
    for key, label, *_ in STRENGTH_CHOICES:
        combo.addItem(label, key)
    idx = combo.findData(DEFAULT_STRENGTH)
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    combo.setFixedWidth(96)
    combo.setToolTip("轨道平滑强度：段内插值密度/最小转弯半径倍率/去抖遍数。"
                     "「标准」= 修前逐字行为。")
    combo.currentIndexChanged.connect(
        lambda _i, p=panel, c=combo: setattr(
            p, "_smooth_strength", str(c.itemData(c.currentIndex())
                                       or DEFAULT_STRENGTH)))
    params_layout.addWidget(prop_row("平滑", combo))
    btn = QPushButton("平滑预览")
    btn.setFixedHeight(22)
    btn.setToolTip("打开平滑前后折线对比（不提交轨道）")
    btn.clicked.connect(lambda: open_smooth_preview(panel))
    params_layout.addWidget(btn)


def remember_raw_chain(panel, chain) -> None:
    """记下最近一次画轨的原始链（预览重算用；不落盘）。"""
    panel._last_raw_chain = [tuple(p) for p in (chain or [])]


def open_smooth_preview(panel) -> None:
    """面板按钮入口：单例浮窗（随面板销毁）。"""
    dlg = getattr(panel, "_smooth_preview_dlg", None)
    if dlg is None:
        dlg = popup(SmoothPreviewDialog(panel))
        panel._smooth_preview_dlg = dlg
        dlg.destroyed.connect(lambda: setattr(panel, "_smooth_preview_dlg", None))
    dlg.rebuild()
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


__all__ = ["DEFAULT_STRENGTH", "STRENGTH_CHOICES", "SmoothPreviewDialog",
           "apply_smooth_profile", "inject_smooth_row", "open_smooth_preview",
           "remember_raw_chain", "smooth_chain", "smooth_params",
           "smooth_strength_label"]
