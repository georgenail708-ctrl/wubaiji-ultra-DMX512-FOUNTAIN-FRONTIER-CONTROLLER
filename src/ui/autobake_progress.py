# -*- coding: utf-8 -*-
"""R182 主窗口状态栏「时间线自动烘焙」进度条（极简内嵌纯条）。

R182 用户规格（替换 R181 的左下角浮动条）：
  · 进度条**内嵌状态栏**、贴在「网络」标签右侧，不再浮动在左下角；
  · 固定约 120×8，无文字（极简纯条）；
  · 槽 `#222222`（经典进度条内槽样式）、填充 SELECT_BLUE 令牌
    （`#4772B3` = 经典进度蓝）、描边 1px BORDER_NORMAL 令牌，全圆角；
  · 无烘焙（未开始 / 已失效）时隐藏；进度到 100% 后停留 700ms 再淡出。

实现纪律：
  · 自绘 QPainter，颜色只取 `qascade_theme` 令牌（槽色 qascade_theme 未导出
    同名常量 → 模块常数并注明 经典进度条内槽样式 来源）；禁 emoji；
  · 鼠标事件全穿透（`WA_TransparentForMouseEvents`）——不挡状态栏操作；
  · 只订阅既有 `TimelineAutoBaker.progress / invalidated` 信号（不新增引擎职责）；
    主窗侧只做挂载接线（见 `main_window._baker_bind_timeline_bar`）。
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import (QAbstractAnimation, QEasingCurve, QPropertyAnimation,
                          QRectF, QTimer, Qt, pyqtProperty)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from . import qascade_theme as _th

log = logging.getLogger(__name__)

#: 进度槽底色 —— 经典进度条 的内槽明度；qascade_theme 未导出
#: 同名令牌（QSlider groove 的 QSS 里为同值 `#222222`），故此处常量并注明来源。
_SLOT_COLOR = "#222222"


def _progress_already_displayed(bar, pct):
    """Duplicates retain completion timing; invalidated active progress can resume."""
    if pct != bar._pct:
        return False
    if pct <= 0:
        return bar.isHidden()
    if pct >= 100:
        return True
    return (bar.isVisible() and bar._fade == 1.0
            and bar._fade_anim.state() == QAbstractAnimation.State.Stopped)


class SlimStatusProgress(QWidget):
    """状态栏内嵌纯进度条（自绘、无文字、固定 120×8）。

    公开 API（接线与测试口径）：
      · `set_progress(pct)` — pct 0..100；<=0 隐藏，>=100 停留后淡出；
      · `show_idle()`       — 无烘焙（失效/取消）→ 淡出隐藏；
      · `on_bake_progress(frac, frame)` — 直接接 `TimelineAutoBaker.progress`；
      · `percent()`         — 当前进度百分比（0..100）。
    """

    WIDTH = 120         # 固定尺寸（状态栏细条同样不参与伸缩）
    HEIGHT = 8
    HOLD_MS = 700       # 100% 后停留时长
    FADE_MS = 380       # 淡出时长

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._pct = 0.0
        self._fade = 1.0
        self._baker_ref = None
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(self.HOLD_MS)
        self._hold.timeout.connect(self._start_fade)
        self._fade_anim = QPropertyAnimation(self, b"fade", self)
        self._fade_anim.setDuration(self.FADE_MS)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade_anim.finished.connect(self._on_fade_finished)
        self.hide()

    # ── 查询（状态/测试口径） ────────────────────────────────────────────────

    def percent(self) -> float:
        """当前进度百分比（0..100）。"""
        return float(self._pct)

    # ── 公开 API ─────────────────────────────────────────────────────────────

    def set_progress(self, pct: float, text: str = "") -> None:
        """设置进度（0..100）：>0 显示；100 停留 `HOLD_MS` 后淡出。

        `text` 为 R181 旧签名兼容参数，极简纯条**不显示任何文字**。
        """
        try:
            pct = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            return
        if _progress_already_displayed(self, pct):
            return
        self._pct = pct
        self._hold.stop()
        self._fade_anim.stop()
        self._set_fade(1.0)
        if pct <= 0.0:
            self.hide()
            return
        self.show()
        self.update()
        if pct >= 100.0:
            self._hold.start()

    def show_idle(self) -> None:
        """无烘焙进行（失效/取消/完成）：淡出隐藏；未显示时直接保持隐藏。"""
        try:
            self._hold.stop()
            if not self.isVisible():
                self.hide()
                return
            self._start_fade()
        except Exception:
            log.debug("autobake progress idle failed", exc_info=True)

    def on_bake_progress(self, frac: float, frame: int = 0) -> None:
        """`TimelineAutoBaker.progress(frac, baked_until_frame)` → 状态栏纯条。"""
        try:
            pct = max(0.0, min(1.0, float(frac))) * 100.0
        except (TypeError, ValueError):
            return
        self.set_progress(pct)

    # ── 淡出属性（QPropertyAnimation 驱动） ─────────────────────────────────

    def _get_fade(self) -> float:
        return float(self._fade)

    def _set_fade(self, value: float) -> None:
        try:
            value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            value = 0.0
        if self._fade == value:
            return
        self._fade = value
        self.update()

    fade = pyqtProperty(float, fget=_get_fade, fset=_set_fade)

    def _start_fade(self) -> None:
        """启动淡出动画（幂等：已在跑不放新）。"""
        if self._fade_anim.state() == QAbstractAnimation.State.Running:
            return
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._fade)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def _on_fade_finished(self) -> None:
        self.hide()
        self._set_fade(1.0)

    # ── 绘制（极简：只有槽 + 填充 + 1px 描边，无文字） ────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(max(0.0, min(1.0, self._fade)))
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        radius = rect.height() / 2.0
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        p.setBrush(QColor(_SLOT_COLOR))
        p.drawRoundedRect(rect, radius, radius)
        frac = max(0.0, min(1.0, self._pct / 100.0))
        if frac > 0.0:
            inner = rect.adjusted(1.0, 1.0, -1.0, -1.0)
            slot = QPainterPath()
            slot.addRoundedRect(inner, inner.height() / 2.0, inner.height() / 2.0)
            p.save()
            p.setClipPath(slot)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(_th.SELECT_BLUE))
            p.drawRect(QRectF(inner.left(), inner.top(),
                              inner.width() * frac, inner.height()))
            p.restore()
        p.end()


def _bar_host_status(host):
    """宿主的状态栏（无 `_status` / 无 `insertPermanentWidget` → None）。"""
    status = getattr(host, "_status", None)
    if status is None or not hasattr(status, "insertPermanentWidget"):
        return None
    return status


def _ensure_bar(host, status) -> Optional[SlimStatusProgress]:
    """惰性建条并插入状态栏（幂等：已有实例直接返回；失败 → None）。"""
    bar = getattr(host, "_autobake_progress_bar", None)
    if bar is not None:
        return bar
    try:
        bar = SlimStatusProgress(status)
    except Exception:
        log.debug("autobake status bar create failed", exc_info=True)
        return None
    try:
        host._autobake_progress_bar = bar
    except Exception:
        pass
    try:
        # 索引 1 = 状态栏永久部件序列里「● 网络」标签（索引 0）右侧
        status.insertPermanentWidget(1, bar)
    except Exception:
        log.debug("autobake status bar insert failed", exc_info=True)
    return bar


def attach_autobake_progress_bar(host, baker) -> Optional[SlimStatusProgress]:
    """把进度条挂到宿主状态栏「网络」标签右侧（`insertPermanentWidget(1, bar)`）。

    `host` 需带 `_status`（AppStatusBar）。幂等：同 baker 重复调用复用同一条，
    不重复插入；换 baker 时先断旧连接再增连到新对象。返回条控件，宿主不可用时 None。
    """
    if host is None or baker is None:
        return None
    status = _bar_host_status(host)
    if status is None:
        return None
    bar = _ensure_bar(host, status)
    if bar is None:
        return None
    if getattr(bar, "_baker_ref", None) is baker:
        return bar
    _rebind(bar, baker)
    return bar


def _rebind(bar: SlimStatusProgress, baker) -> None:
    """断开旧 baker 订阅、连到新 baker（失败静默；幂等由 `_baker_ref` 判定）。"""
    old = getattr(bar, "_baker_ref", None)
    if old is not None:
        for sig, slot in (("progress", bar.on_bake_progress),
                          ("invalidated", bar.show_idle)):
            try:   # 只断本条的绑定，不误伤其它消费者
                getattr(old, sig).disconnect(slot)
            except Exception:
                pass
    try:
        baker.progress.connect(bar.on_bake_progress)
        baker.invalidated.connect(bar.show_idle)
        bar._baker_ref = baker
    except Exception:
        log.debug("autobake status bar bind failed", exc_info=True)
