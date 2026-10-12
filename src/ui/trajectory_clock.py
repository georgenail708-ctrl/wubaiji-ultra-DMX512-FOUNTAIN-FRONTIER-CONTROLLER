# -*- coding: utf-8 -*-
"""trajectory_clock — 轨迹曲线的节拍时钟（R178 节拍吸附）

挂全局音频驱动 get_audio_driver() 的 frame_ready，从 AudioFrame 取 bpm /
beat_phase 维护「最近一次节拍时刻 + 拍长」的节拍网格；提供两个吸附入口：

  snap(t, division)        —— t 视为音频流时间轴（frame.t 域）上的秒，
                              四舍五入到最近拍时刻（division∈{1,2,4} 拍，
                              网格间距 = 拍长 × division）。
  snap_local(t, division)  —— t 视为曲线本地时间轴（0..duration 秒）上的秒：
                              网格间距同上，原点 = 当前 beat_phase × 拍长
                              （把「此刻听到的拍位」对齐进曲线时间轴，
                              供编辑器松手吸附用）。

无音频（未挂载 / 无有效帧 / bpm 非法）→ 两者都返回 None，调用方原样保留 t。
纯数学 snap_to_grid(t, origin, spacing) 独立导出，便于单测。

注意：attach() 会触发 get_audio_driver()（必要时创建并启动全局分析器），
故只在用户显式打开吸附开关时调用；无音频输入时帧的 has_audio=False，
网格不更新，吸附退化为 None（原样），无副作用。
"""
from __future__ import annotations

from typing import Optional, Tuple

from PyQt6.QtCore import QObject

# division ∈ {1,2,4}：吸附网格每 N 拍一格（1拍=每拍、2拍=每两拍、4拍=每四拍）。
SNAP_DIVISIONS = (1, 2, 4)
_BPM_MIN, _BPM_MAX = 30.0, 300.0


def snap_to_grid(t: float, grid_origin: float, spacing: float) -> float:
    """四舍五入到最近网格时刻：grid_origin + round((t-origin)/spacing)*spacing。"""
    if spacing <= 1e-9:
        return float(t)
    k = round((float(t) - float(grid_origin)) / spacing)
    return float(grid_origin) + k * spacing


class TrajectoryClock(QObject):
    """节拍网格维护器（挂 frame_ready；无 Qt 事件循环也可手动 _update_grid）。"""

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._beat_period: Optional[float] = None   # 秒/拍
        self._grid_origin: Optional[float] = None   # 最近拍时刻（frame.t 域）
        self._beat_phase: float = 0.0               # 最近帧的拍内相位 [0,1)
        self._driver = None
        self._attached = False

    # ── 挂载 ──
    def attach(self) -> bool:
        """挂到全局音频驱动（失败/已挂 → False/True 不重复挂）。"""
        if self._attached:
            return True
        try:
            from .audio_driver import get_audio_driver
            self._driver = get_audio_driver()
            self._driver.frame_ready.connect(self._on_frame)
            self._attached = True
            return True
        except Exception:
            self._driver = None
            return False

    def detach(self) -> None:
        if self._attached and self._driver is not None:
            try:
                self._driver.frame_ready.disconnect(self._on_frame)
            except Exception:
                pass
        self._attached = False
        self._driver = None

    # ── 帧处理 ──
    def _on_frame(self, frame) -> None:
        self._update_grid(frame)

    def _update_grid(self, frame) -> None:
        bpm = float(getattr(frame, "bpm", 0.0) or 0.0)
        if not (_BPM_MIN <= bpm <= _BPM_MAX):
            return
        t = float(getattr(frame, "t", 0.0) or 0.0)
        phase = float(getattr(frame, "beat_phase", 0.0) or 0.0)
        self._beat_period = 60.0 / bpm
        self._beat_phase = float(phase) % 1.0
        self._grid_origin = t - self._beat_phase * self._beat_period

    # ── 查询 ──
    def beat_period(self) -> Optional[float]:
        return self._beat_period

    def has_grid(self) -> bool:
        return self._beat_period is not None and self._beat_period > 1e-9

    def local_grid(self) -> Optional[Tuple[float, float]]:
        """曲线本地域网格参数 (origin_s, beat_period_s)：origin = 当前拍位
        beat_phase×拍长（与 snap_local 的原点同义）；无网格 → None。
        画布画网格线直接读它，保证「画的格」与「会吸的格」同一张。"""
        if not self.has_grid():
            return None
        return (self._beat_phase * self._beat_period, self._beat_period)

    # ── 吸附 ──
    def snap(self, t: float, division: int) -> Optional[float]:
        """音频流时间域吸附：t（秒，frame.t 域）→ 最近拍时刻；无网格 → None。"""
        if not self.has_grid():
            return None
        spacing = self._beat_period * _valid_division(division)
        return snap_to_grid(t, self._grid_origin, spacing)

    def snap_local(self, t: float, division: int) -> Optional[float]:
        """曲线本地时间域吸附：网格原点 = 当前拍位 beat_phase×拍长（音乐「此刻」
        的拍相对位对齐进曲线 0 点）；无网格 → None（调用方原样保留）。"""
        grid = self.local_grid()
        if grid is None:
            return None
        origin, beat_period = grid
        spacing = beat_period * _valid_division(division)
        return snap_to_grid(t, origin, spacing)


def _valid_division(division: int) -> int:
    try:
        d = int(division)
    except (TypeError, ValueError):
        return 1
    return d if d in SNAP_DIVISIONS else 1
