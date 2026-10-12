# -*- coding: utf-8 -*-
"""时间线**小节/拍网格**（BPM → 秒；缩放自适应；纯函数，可无 GUI 单测）。

用户语义来源：3 Show Sequencer 的 BPM 网格 + 手册 Time Ruler
（小节标尺随缩放分级，密到看不清就退级）。工程里 `Project.bpm` 早已存在
（真工程 To The Moon 实测 bpm=120.0）却**没有任何 UI 消费**——本模块是它的
第一个消费者，零数据成本。

为什么独立成模块：`timeline_ruler.py` 的 `paintEvent` 已顶住架构门禁基线
（104 行 / CC 15），刻度换算放进来只能"改一点涨一点"；把纯换算外提后
paintEvent 只做绘制、且**净减行**（顺带把秒刻度也收敛到同一处循环）。

分级规则（`bpm_ticks`）：
  · `px_per_beat >= MIN_PX_PER_BEAT`（默认 8px）→ 画拍线 + 小节线；
  · 否则只画小节线，且小节间隔 `>= MIN_PX_PER_BAR` 才画（再密则返回空 = 不画）。
"""
from __future__ import annotations

from typing import List, Tuple

#: 一拍至少这么宽才画拍线（同款"密则退级"）
MIN_PX_PER_BEAT = 8.0
#: 小节线也密到看不清（间隔小于本值）→ 整层不画
MIN_PX_PER_BAR = 12.0
#: 每小节拍数（4/4 拍；与 `timeline_snap` 的小节折算同一口径）
BEATS_PER_BAR = 4


def beat_seconds(bpm) -> float:
    """一拍时长（秒）；bpm 非法/<=0 → 0（调用方据此判"无 BPM 网格"）。"""
    try:
        b = float(bpm)
    except (TypeError, ValueError):
        return 0.0
    if b != b or b <= 0.0:      # NaN / 非正
        return 0.0
    return 60.0 / b


def bar_seconds(bpm) -> float:
    """一小节时长（秒）= 4 拍；bpm 非法 → 0。"""
    beat = beat_seconds(bpm)
    return beat * BEATS_PER_BAR if beat > 0.0 else 0.0


def bpm_ticks(bpm, px_per_sec, start_time: float, end_time: float,
              min_px_per_beat: float = MIN_PX_PER_BEAT, *,
              min_px_per_bar: float = MIN_PX_PER_BAR) -> List[Tuple[float, bool]]:
    """`[start_time, end_time]` 内的网格刻度 → `[(秒, 是否小节线), …]`。

    返回空列表 = 当前缩放下不该画 BPM 网格（密度不足）。**绝不抛**：
    任何非法输入都当作"无网格"，让标尺退回纯秒刻度。
    """
    bar = bar_seconds(bpm)
    if bar <= 0.0:
        return []
    try:
        px = float(px_per_sec)
        t0 = max(0.0, float(start_time))
        t1 = float(end_time)
        min_beat_px = float(min_px_per_beat)
        min_bar_px = float(min_px_per_bar)
    except (TypeError, ValueError):
        return []
    if px <= 0.0 or t1 <= t0:
        return []
    if bar * px < min_bar_px:
        return []                                   # 小节线都太密 → 整层不画
    beat = beat_seconds(bpm)
    draw_beats = beat * px >= min_beat_px
    step = beat if draw_beats else bar
    # 相位：网格锚在 0 秒（与 clip_area 的秒网格同一相位，避免两套网格错位）
    idx = int(t0 / step)
    t = idx * step
    out: List[Tuple[float, bool]] = []
    guard = 0
    while t <= t1 + 1e-9 and guard < 200000:
        guard += 1
        # 是否小节线：用"该刻度是第几拍"判定（浮点累加会漂，故用序号取整）
        n = int(round(t / beat)) if beat > 0.0 else 0
        out.append((t, n % BEATS_PER_BAR == 0))
        t += step
    return out


def second_grid_positions(px_per_sec: float, left: int, right: int) -> range:
    """Original lane second grid: zero phase, clipped to exposed x bounds."""
    if px_per_sec <= 0:
        return range(0)
    step = max(1, int(px_per_sec))
    start = ((int(left) + step - 1) // step) * step
    return range(start, int(right), step)


__all__ = ["MIN_PX_PER_BEAT", "MIN_PX_PER_BAR", "BEATS_PER_BAR",
           "beat_seconds", "bar_seconds", "bpm_ticks", "second_grid_positions"]
