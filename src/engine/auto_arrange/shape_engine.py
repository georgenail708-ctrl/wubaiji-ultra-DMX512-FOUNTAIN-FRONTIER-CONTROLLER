"""
shape_engine — 静态形状 = effect 的冻结版

核心洞察（来自用户）：一帧"静态形状"和一段"流动 effect"本质同源——都是把
"哪台设备该出多少值"按几何/序关系算出来。区别只在于：

  · 冻结（静态 programmer 值）：调用方传一个固定 phase（默认 0.0），得到一帧分布。
  · 流动（动态 effect）：调用方按时间逐帧传 phase=(t*speed)%1.0，本模块每次产一帧，
    连起来就是动画。

本模块只负责"从设备空间序 / 坐标 / 下标 生成每台设备的值分布(0..255)"，
既能二值（哪些开：飞鸟轮廓 / 奇偶），又能连续（开多高：山形 / 梯度）。

纯 core：仅依赖 numpy / math，无 Qt、无引擎依赖。确定性可复现（含伪随机用固定 seed）。

约定：
  · member_ids: List[int]   设备 id 列表。其"顺序"即空间序 seq_i（调用方应先用
                            topology 排好序再传入，本模块不重排）。
  · coords: Dict[fid, (x, y)] 设备坐标。山形/谷形/横纵渐变依赖坐标；
                            two_high_two_low/odd_even/wave/ramp 仅依赖空间序。
  · 返回 Dict[fid, int]，值已裁剪到 [0, 255]（或调用方给定的 [lo, hi]）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


# 支持的形状名（便于调用方做校验/列菜单）
SHAPES = (
    "flat",
    "mountain",
    "valley",
    "gradient_x",
    "gradient_y",
    "multi_wave",
    "two_high_two_low",
    "odd_even",
    "wave",
    "ramp",
    "random_undulate",
)


# ── 内部工具 ────────────────────────────────────────────────────────────
def _clamp_byte(v: float, lo: int, hi: int) -> int:
    """裁剪到 [lo, hi] 并取整成 dmx 字节。lo/hi 自身被夹在 0..255。"""
    lo = int(max(0, min(255, lo)))
    hi = int(max(0, min(255, hi)))
    iv = int(round(v))
    if iv < lo:
        iv = lo
    if iv > hi:
        iv = hi
    return iv


def _coord_array(member_ids: Sequence[int],
                 coords: Dict[int, Tuple[float, float]]) -> np.ndarray:
    """取出与 member_ids 对齐的坐标数组，缺失坐标记为 (0,0)。形状 (N,2)。"""
    pts = []
    for fid in member_ids:
        xy = coords.get(fid, (0.0, 0.0)) if coords else (0.0, 0.0)
        pts.append((float(xy[0]), float(xy[1])))
    if not pts:
        return np.zeros((0, 2), dtype=float)
    return np.asarray(pts, dtype=float)


def _lerp(lo: int, hi: int, t: float) -> float:
    """t∈[0,1] 线性插值到 [lo,hi]（不裁剪，交给 _clamp_byte）。"""
    return lo + (hi - lo) * t


# ── 主接口：shape_values ────────────────────────────────────────────────
def shape_values(member_ids: Sequence[int],
                 coords: Optional[Dict[int, Tuple[float, float]]] = None,
                 shape: str = "flat",
                 phase: float = 0.0,
                 lo: int = 0,
                 hi: int = 255,
                 *,
                 seed: int = 0,
                 wave_k: float = 1.0,
                 ramp_step: Optional[float] = None,
                 undulate_amp: float = 1.0,
                 **kw) -> Dict[int, int]:
    """从空间序 / 坐标生成每台设备的值分布。

    参数
    ----
    member_ids : 设备 id 列表，其顺序即空间序 seq_i（本模块不重排）。
    coords     : {fid:(x,y)}，山形/谷形/渐变所需；序类形状可不传。
    shape      : SHAPES 之一。
    phase      : 相位。静态调用传固定值（默认 0.0）；动态调用传 (t*speed)%1.0。
                 仅 'wave'（及可选影响 random_undulate 的滚动）随 phase 流动；
                 其余形状对 phase 不敏感（冻结）。
    lo, hi     : 输出值域，默认 0..255。
    seed       : 'random_undulate' 的确定性随机种子。
    wave_k     : 'wave' 的空间频率系数（每台设备相位推进 2π*k/N）。
    ramp_step  : 'ramp' 每级步进（lo..hi 之间）；None 时按 N 等分铺满。
    undulate_amp : 'random_undulate' 起伏幅度系数 (0..1]。

    返回
    ----
    {fid: int}，值已裁剪到 [lo, hi]∩[0,255]。
    """
    ids: List[int] = list(member_ids)
    n = len(ids)
    if n == 0:
        return {}

    shape = (shape or "flat").lower()
    out: Dict[int, int] = {}

    # ―― 齐平：全 hi（最简单的"冻结" effect）――
    if shape == "flat":
        for fid in ids:
            out[fid] = _clamp_byte(hi, lo, hi)
        return out

    # ―― 山形 / 谷形：按到质心距离映射 ――
    if shape in ("mountain", "valley"):
        pts = _coord_array(ids, coords or {})
        centroid = pts.mean(axis=0)
        d = np.sqrt(((pts - centroid) ** 2).sum(axis=1))  # 到质心距离
        dmax = float(d.max()) if d.size else 0.0
        # ★ 零坐标兜底:本项目设备 pos 可能全=0(真坐标在 topview_state),
        #   导致 dmax≈0 → 全塌成 flat。退化时改用 seq_i 当 1D 伪坐标:
        #   按下标距中点的距离做山形(中间 hi 两端 lo),保住空间梯度。
        if dmax <= 1e-9 and n > 1:
            mid = (n - 1) / 2.0
            for i, fid in enumerate(ids):
                frac = abs(i - mid) / mid if mid > 0 else 0.0
                if shape == "mountain":
                    v = hi - (hi - lo) * frac
                else:
                    v = lo + (hi - lo) * frac
                out[fid] = _clamp_byte(v, lo, hi)
            return out
        for i, fid in enumerate(ids):
            frac = (d[i] / dmax) if dmax > 1e-12 else 0.0
            if shape == "mountain":
                # 中心 hi，边缘 lo：value = hi - (hi-lo)*d/dmax
                v = hi - (hi - lo) * frac
            else:  # valley：中心 lo，边缘 hi
                v = lo + (hi - lo) * frac
            out[fid] = _clamp_byte(v, lo, hi)
        return out

    # ―― 横 / 纵向线性渐变：按坐标 x / y 在 [min,max] 上归一化 ――
    if shape in ("gradient_x", "gradient_y"):
        pts = _coord_array(ids, coords or {})
        axis = 0 if shape == "gradient_x" else 1
        col = pts[:, axis]
        cmin, cmax = float(col.min()), float(col.max())
        span = cmax - cmin
        # ★ 零坐标兜底:坐标退化时用 seq_i 线性渐变,保住梯度不塌成 flat。
        if span <= 1e-9 and n > 1:
            for i, fid in enumerate(ids):
                out[fid] = _clamp_byte(_lerp(lo, hi, i / (n - 1)), lo, hi)
            return out
        for i, fid in enumerate(ids):
            frac = ((col[i] - cmin) / span) if span > 1e-12 else 0.0
            out[fid] = _clamp_byte(_lerp(lo, hi, frac), lo, hi)
        return out

    # ―― 每 2 台高 2 台低（按空间序）――
    if shape == "two_high_two_low":
        for i, fid in enumerate(ids):
            high = (i % 4) < 2  # 0,1 高；2,3 低；循环
            out[fid] = _clamp_byte(hi if high else lo, lo, hi)
        return out

    # ―― 奇高偶低（按空间序）――
    if shape == "odd_even":
        for i, fid in enumerate(ids):
            # seq_i 偶数下标(0,2,4..)给一档，奇数给另一档；二者必不同
            out[fid] = _clamp_byte(hi if (i % 2 == 1) else lo, lo, hi)
        return out

    # ―― 正弦波（沿空间序，phase 流动则整体波动）――
    if shape == "wave":
        ph = float(phase) * 2.0 * math.pi
        for i, fid in enumerate(ids):
            ang = ph + (2.0 * math.pi * wave_k * i / max(1, n))
            s = (math.sin(ang) + 1.0) * 0.5  # 0..1
            out[fid] = _clamp_byte(_lerp(lo, hi, s), lo, hi)
        return out

    # ―― 多波叠加（基波 + 谐波,空间更丰富平滑）――
    if shape == "multi_wave":
        ph = float(phase) * 2.0 * math.pi
        harmonics = [(1.0, 1.0), (3.0, 0.4)]   # 基波 + 3次谐波细纹
        amp_sum = sum(abs(a) for _, a in harmonics) or 1.0
        for i, fid in enumerate(ids):
            acc = 0.0
            for k, a in harmonics:
                acc += a * math.sin(ph + 2.0 * math.pi * k * i / max(1, n))
            s = (acc / amp_sum + 1.0) * 0.5   # 归一 0..1
            out[fid] = _clamp_byte(_lerp(lo, hi, max(0.0, min(1.0, s))), lo, hi)
        return out

    # ―― 阶梯递增（按空间序）――
    if shape == "ramp":
        for i, fid in enumerate(ids):
            if ramp_step is None:
                frac = (i / (n - 1)) if n > 1 else 0.0
                v = _lerp(lo, hi, frac)
            else:
                v = lo + ramp_step * i
            out[fid] = _clamp_byte(v, lo, hi)
        return out

    # ―― 确定性伪随机起伏 ――
    if shape == "random_undulate":
        # 用 seed 派生每台设备的确定性基相位，再叠 phase 滚动 → 可静可动
        rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
        base = rng.random(n)  # 每台设备 0..1 的固定基值
        roll = float(phase)
        amp = max(0.0, min(1.0, float(undulate_amp)))
        for i, fid in enumerate(ids):
            # base 提供空间随机分布，phase 让其整体平移（流动）
            s = (base[i] + roll) % 1.0
            # 以 amp 收缩到中线附近，amp=1 时铺满 lo..hi
            mid = 0.5
            s = mid + (s - mid) * amp
            out[fid] = _clamp_byte(_lerp(lo, hi, s), lo, hi)
        return out

    # 未知形状 → 退化为 flat（不抛异常，便于调用方容错）
    for fid in ids:
        out[fid] = _clamp_byte(hi, lo, hi)
    return out


# ── 自定义二值轮廓：飞鸟 ────────────────────────────────────────────────
def bird_mask(member_ids: Sequence[int],
              coords: Optional[Dict[int, Tuple[float, float]]] = None) -> Dict[int, int]:
    """示例：从几何坐标生成任意二值 pattern（飞鸟轮廓）。返回 {fid: 0/1}。

    简化的"海鸥"判定（坐标归一化到质心、以最大跨度为尺度）：
      · 身体：靠近中轴(|x| 小)且 y 落在中段 → 1
      · 两翼：从中心向左右上扬的带状区域 |y - k*|x|| 小 → 1
    其余为 0。纯几何、确定性，用于展示"从坐标生成任意 pattern"的能力。
    """
    ids: List[int] = list(member_ids)
    n = len(ids)
    if n == 0:
        return {}

    pts = _coord_array(ids, coords or {})
    centroid = pts.mean(axis=0)
    rel = pts - centroid
    # 归一化尺度：最大坐标跨度的一半
    scale = float(np.abs(rel).max())
    if scale <= 1e-12:
        return {fid: 0 for fid in ids}
    rel = rel / scale  # 大致落在 [-1,1]

    out: Dict[int, int] = {}
    wing_slope = 0.6     # 翼上扬斜率
    wing_band = 0.28     # 翼带宽容差
    body_xband = 0.22    # 身体横向半宽
    for i, fid in enumerate(ids):
        x, y = float(rel[i, 0]), float(rel[i, 1])
        # 两翼：y 随 |x| 线性上扬的带（向上为正 y）
        on_wing = abs(y - wing_slope * abs(x)) <= wing_band and abs(x) <= 1.05
        # 身体：中轴附近的竖直短带
        on_body = abs(x) <= body_xband and (-0.5 <= y <= 0.55)
        out[fid] = 1 if (on_wing or on_body) else 0
    return out
