# -*- coding: utf-8 -*-
"""Effect 进阶方向（与 clip 过渡方向**同种类**）—— 2026-09-19 用户要求。

用户原话::

    "effect的应用模式，方向等等，至少要跟clip的过渡方向拥有不低于它的方向选择种类啊。
     目前的方向太少了"

## 现状对比（改前）

  effect：default / reverse / ltr / rtl / ftb / btf / 4×diag / center_out / edge_in
          —— 但 4 个 `diag_*` 只是 ltr/rtl 的别名，**实际只有 7 种独立语义**。
  clip  ：none / ltr / rtl / ftb / btf / **cw / ccw** / ctr / rtc /
          **alt / alt_block / wave / cascade / rev_cascade / zigzag / random**
          —— 16 种。

  缺 9 种（本模块补齐）：顺时针、逆时针、奇偶交替、块交替、波浪、级联、
  反向级联、折返、随机。

## 语义对齐（**逐字照搬 clip 的公式**，不自创）

  clip 侧实现在 `playback_engine._compute_direction_positions`：对 K 个元素先生成
  `pos = linspace(0,1,K)`，再按方向把它映射成**相位位置**，喂给淡入/时序曲线。

  ★★ 关键区分（**第一版我搞错了，测试当场抓出**）：
    · cw/ccw 是**重排**（绕质心极角定序 → 圆周扫掠顺序）；
    · alt / alt_block / wave / cascade / rev_cascade / zigzag / random 是
      **相位曲线** —— 它们**不改变设备顺序**（`cascade: p²` 单调、`wave: sin`
      也不换序），而是改变**每台设备拿到的相位值**（级联 = 由慢到快）。
      按"排序"实现会让这些方向**完全没有反应**（正是历史上反复出现的
      "选了没反应"形态）。
    故本模块对前者出**顺序**，对后者出**相位表**，分别由
    `advanced_direction_order` / `direction_phases` 提供。

  设备坐标相关：
    · alt      —— `i % 2`（奇偶交替）
    · alt_block—— `(i // (K//4)) % 2`（块交替，块宽 = K/4）
    · wave     —— `(sin(2π·p − π/2) + 1)/2`
    · cascade  —— `p²`
    · rev_cascade —— `1 − (1−p)²`
    · zigzag   —— 四段折返，段内线性、逐段反向，再归一化
    · random   —— `default_rng(seed = K*42+7)` 洗牌（**与 clip 同种子同算法** →
                  同规模同布局可复现，不引入系统间随机差异）

  其中 `p = i/(K-1)`（i = 该设备在**当前块**中的序号），与 clip 的
  `pos = linspace(0, 1, K)` 完全一致 → 两种功能方向语义互通、可互相解释。

## 与既有实现的关系

`effect_engine.apply_effect_direction` 先按 `_EFFECT_DIRECTION_AXIS` 等做坐标/距离
定序；本模块的 `advanced_direction_order` 处理这批**相位曲线型**方向，同样只在
**单元块内部**生效（块序不变，与 P2-2 审查结论一致）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

#: 本模块负责的方向（= clip 有、effect 原先缺的那 9 种）。
ADVANCED_DIRECTIONS: Tuple[str, ...] = (
    "clockwise", "counterclockwise",
    "alternate", "alternate_block", "wave", "cascade", "reverse_cascade",
    "zigzag", "random",
)

#: 输入别名 → 规范名（与 clip 的 `_CLIP_DIRECTION_ALIASES` 同名同义，便于互译）。
ALIASES: Dict[str, str] = {
    "cw": "clockwise",
    "ccw": "counterclockwise",
    "anti_clockwise": "counterclockwise",
    "anticlockwise": "counterclockwise",
    "alt": "alternate",
    "alt_block": "alternate_block",
    "rev_cascade": "reverse_cascade",
}


def normalize(direction: Any) -> str:
    """方向值 → 规范名（不在本模块范围内则原样返回）。"""
    d = str(direction or "").strip().lower()
    return ALIASES.get(d, d)


def is_advanced(direction: Any) -> bool:
    return normalize(direction) in ADVANCED_DIRECTIONS


def _xy(positions: Dict[str, dict], fi: Any) -> Tuple[float, float]:
    """设备坐标 (x, y)；缺失/非法按 (0,0)（与 effect_engine 同宽厚）。"""
    p = (positions or {}).get(str(getattr(fi, "id", "") or "")) or {}
    try:
        return float(p.get("pos_x", 0.0) or 0.0), float(p.get("pos_y", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _shape(d: str, pos, k: int):
    """按方向把 `linspace(0,1,k)` 整形成相位（**与 clip 逐行同式同 dtype**）。"""
    import numpy as np

    if d == "counterclockwise":
        return 1.0 - pos
    if d == "alternate":
        return np.array([float(i % 2) for i in range(k)], dtype=np.float32)
    if d == "alternate_block":
        w = max(1, k // 4)
        return np.array([float((i // w) % 2) for i in range(k)], dtype=np.float32)
    if d == "zigzag":
        return _zigzag_np(pos, k)
    if d == "random":
        return _random_np(k)
    if d in ("wave", "cascade", "reverse_cascade"):
        return _curve(d, pos)
    return pos      # clockwise（圆周语义走 advanced_direction_order）/ 未知 → 原样


def _curve(d: str, pos):
    """三个"曲线族"方向（与 clip 同式：wave / cascade / reverse_cascade）。"""
    import numpy as np

    if d == "wave":
        return (np.sin(pos * np.pi * 2.0 - np.pi / 2.0) + 1.0) / 2.0
    if d == "cascade":
        return pos ** 2.0
    return 1.0 - (1.0 - pos) ** 2.0


def _random_np(k: int):
    """确定性洗牌（与 clip 同种子同算法 K*42+7）。"""
    import numpy as np

    rng = np.random.default_rng(seed=k * 42 + 7)
    order = np.arange(k, dtype=np.float32)
    rng.shuffle(order)
    return order / max(1, k - 1)


def direction_positions(direction: Any, k: int) -> List[float]:
    """K 个元素的相位位置 [0,1] —— **与 clip 的
    `playback_engine._compute_direction_positions` 逐位一致**。

    ★ 刻意用 `np.float32` + `np.linspace(dtype=float32)` + `default_rng`，
      与 clip 同路径（float64 有 1e-8 级偏差 → "对齐"就成口头承诺）。
    """
    import numpy as np

    if k <= 1:
        return [0.5]
    pos = np.linspace(0.0, 1.0, k, dtype=np.float32)
    out = _shape(normalize(direction), pos, k)
    return [float(v) for v in np.asarray(out, dtype=np.float32)]


def _zigzag_np(pos, k: int):
    """四段折返（与 clip 同式同 dtype）：段宽 K/4，段内线性、奇段反向，再归一化。"""
    import numpy as np

    quarter = k // 4 if k >= 4 else 1
    zigzag_pos = np.zeros(k, dtype=np.float32)
    for i in range(k):
        segment = i // quarter
        local_pos = (i % quarter) / max(1, quarter - 1)
        zigzag_pos[i] = local_pos if segment % 2 == 0 else 1.0 - local_pos
    mn, mx = zigzag_pos.min(), zigzag_pos.max()
    if mx > mn:
        return (zigzag_pos - mn) / (mx - mn)
    return np.zeros(k, dtype=np.float32)


def _circular_angles(points: List[Tuple[float, float]]) -> List[float]:
    """绕块质心的极角（弧度，atan2）；质心处退化点按 0。

    与 clip 的圆周扫掠同向：数学正方向为逆时针 → `clockwise` 用负角排序。
    """
    n = len(points)
    if not n:
        return []
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    return [math.atan2(p[1] - cy, p[0] - cx) for p in points]


def advanced_direction_order(block: List[Tuple[int, Any]], positions: Dict[str, dict],
                             direction: Any) -> Optional[List[Tuple[int, Any]]]:
    """对**单个块**应用进阶方向；返回重排后的块，方向不归本模块时返回 None。

    与 `effect_engine._effect_direction_block_order` 同契约：块内原序只作平局决胜。

    ★ 只处理**重排型**（cw/ccw）；相位曲线型返回 None（由 `direction_phases`
      注入相位，**不换序** —— 换序会让级联/波浪语义错误）。
    """
    d = normalize(direction)
    if d in ORDER_DIRECTIONS and len(block) > 1:
        pts = [_xy(positions, it[1]) for it in block]
        angles = _circular_angles(pts)
        # ★ cw/ccw = 极角升/降序（atan2 正方向 = 逆时针 → cw 取负角 = 降序）。
        #   同角（同一射线上）按块内原序决胜（`i` 在键尾且升序不翻转）。
        #   （2026-09-20 复核：与 `sorted(..., reverse=True)` 语义等价，仅同角平局
        #     顺序不同 —— 保留本式以维持"原序决胜"的既有行为。）
        sign = -1.0 if d == "clockwise" else 1.0
        order = sorted(range(len(block)), key=lambda i: (sign * angles[i], i))
        return [block[i] for i in order]
    return None      # 相位曲线型：不改顺序（见 direction_phases）


#: 只做"重排"的进阶方向（其余为相位曲线）。
ORDER_DIRECTIONS = ("clockwise", "counterclockwise")

#: 只做"相位整形"的进阶方向（设备顺序不变，改变每台的相位值）。
PHASE_DIRECTIONS = tuple(d for d in ADVANCED_DIRECTIONS if d not in ORDER_DIRECTIONS)


def direction_phases(direction: Any, keys: List[str], n: int = 0) -> Dict[str, float]:
    """方向 → {设备id: 相位值}（量纲与 seq_i 一致：0..n-1）。

    ★ 这是 clip 语义在 effect 侧的落点：clip 把 `_compute_direction_positions`
      的结果当**相位**喂给时序曲线；effect 的相位源是
      `effect_engine.spatial_phase_index()`，本表由 `compute()` 注入
      `params["_dir_phase"]` 供其优先读取。

    `keys` = 设备 id 列表（**当前顺序**，与 `direction_positions` 的下标一一对应）。
    """
    d = normalize(direction)
    if d not in PHASE_DIRECTIONS:
        return {}
    k = len(keys)
    if k <= 1:
        return {}
    span = float(max(1, n - 1)) if n else 1.0
    vals = direction_positions(d, k)
    return {str(fid): float(v) * span for fid, v in zip(keys, vals)}


__all__ = ["ADVANCED_DIRECTIONS", "ALIASES", "ORDER_DIRECTIONS",
           "PHASE_DIRECTIONS", "normalize", "is_advanced",
           "direction_positions", "direction_phases", "advanced_direction_order"]
