# -*- coding: utf-8 -*-
"""trajectory_reduce — 轨迹录制样本的抽稀（R180，纯函数无 Qt）

录制采样周期 50ms，长录一段动辄数百上千点。DAW 惯例（Reaper reduce
points）用 Ramer–Douglas–Peucker 抽稀成控制点；本模块与
``engine.route_animator.track_smooth.rdp`` 同思路、同迭代式实现，
但**距离口径不同**：

  * track_smooth.rdp 用点到弦的**欧氏垂距**（平面几何抽稀）；
  * 这里用**垂直偏差** |y_i - chord(x_i)|——因为曲线回放求值是「同一
    时刻取纵值」，epsilon 直接给出回放值的逐点上界：任一点抽掉后，
    折线在该时刻与原采样值之差 ≤ epsilon。单调时间轴上垂直口径比
    欧氏口径语义更准（保留点要求不松不紧）。

首尾点恒保留；按原序抽取子集 → x 单调性天然保持。
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

#: 默认抽稀容差（归一化值 0..1）。0.005 ≈ 1.3 个 DMX 级（255 级），
#: 输出侧取整后不可感知——保守默认，用户可在录制区调高。
DEFAULT_RDP_EPSILON = 0.005
_EPS = 1e-12


def _parse_samples(samples: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
    """[[t, v], ...] 宽松解析成点对；畸形项丢弃不崩。"""
    pts: List[Tuple[float, float]] = []
    for s in samples or []:
        try:
            pts.append((float(s[0]), float(s[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return pts


def _segment_split(xs: "np.ndarray", ys: "np.ndarray", start: int, end: int,
                   epsilon: float) -> Optional[int]:
    """RDP 单段：返回段内垂直偏差最大点的索引；未超容差/退化段返回 None。"""
    if end <= start + 1:
        return None
    ax, ay = xs[start], ys[start]
    bx, by = xs[end], ys[end]
    mx = xs[start + 1:end]
    my = ys[start + 1:end]
    span = bx - ax
    if span <= _EPS:
        # 零宽基线：退化为到端点的纵差。
        d = np.abs(my - ay)
    else:
        chord = ay + (by - ay) * (mx - ax) / span
        d = np.abs(my - chord)
    if d.size and float(d.max()) > epsilon:
        return start + 1 + int(np.argmax(d))
    return None


def reduce_recorded_samples(
    samples: Sequence[Sequence[float]],
    epsilon: float = DEFAULT_RDP_EPSILON,
) -> List[List[float]]:
    """Ramer–Douglas–Peucker 抽稀录制样本 [[t, v], ...]（垂直偏差口径）。

    epsilon ≤ 0 → 原样返回（抽稀关闭）。返回与原输入同形的
    ``[[t, v], ...]`` 列表；<3 点不做任何处理。
    """
    pts = _parse_samples(samples)
    n = len(pts)
    if n < 3 or epsilon <= 0.0:
        return [[p[0], p[1]] for p in pts]
    xs = np.asarray([p[0] for p in pts], dtype=np.float64)
    ys = np.asarray([p[1] for p in pts], dtype=np.float64)
    keep = [False] * n
    keep[0] = True
    keep[n - 1] = True
    stack: List[Tuple[int, int]] = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        idx = _segment_split(xs, ys, start, end, epsilon)
        if idx is not None:
            keep[idx] = True
            stack.append((start, idx))
            stack.append((idx, end))
    return [[float(xs[i]), float(ys[i])] for i in range(n) if keep[i]]
