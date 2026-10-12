"""
effect_presets_curated_ext — 电机水型扩充姿态库（2026-08-12）

50 个新编组效果（22 kind × 标准/俯仰 + 3 序列 × 标准/缓演）的姿态函数。
为遵守函数长度硬限制（≤100 行），新 kind 全部走独立小函数，经
_formation_pose_ext 分发；主文件 _formation_pose 顶部仅加 2 行 dispatch。

约定与 effect_presets_curated._formation_pose 一致：
  返回 (x, y) ∈ 大致[-1,1]（x→pan、y→tilt 偏移量，再乘角度半径）
  u = 设备到队列中心的有符号位置 [-1,1]；pos = [0,1]；p = 参数 dict。

分组语义（用户硬约束：组间状态不同、组内各异）：
  引擎只预置 g/gu/par/ph，gi（组号）/ gk（组号归一）/ gpos（组内位置）
  由 _pose_common 统一计算——每个 kind 必消费 gi/gk/gpos 之一以上，
  禁止纯单波形×位置结构。

循环导入规避：curated.py 顶部 import 本模块；本模块顶层只
`import src.ui.effect_presets_curated as _cu`（模块对象，函数体内才取
属性），故任一方加载时均不触发未定义符号。
"""
from __future__ import annotations
import math
from typing import Tuple

import src.ui.effect_presets_curated as _cu  # 延迟属性访问（防循环导入）

_TWO = 2 * math.pi


def _pose_common(t: float, p: dict, pos: float, seq_i: int):
    """公共前导：相位 + 完整分组语义。返回 (ph, gi, gk, gpos, gu, sg, par, groups)。"""
    speed = float(p.get("speed", 0.5))
    groups = max(1, int(p.get("groups", 1)))
    ph = t * speed
    gi = int(pos * groups)
    gk = gi / max(1, groups - 1)
    gpos = (pos * groups) % 1.0
    gu = gpos * 2.0 - 1.0
    sg = 1.0 if gi % 2 == 0 else -1.0   # 奇偶组方向
    par = 1.0 if seq_i % 2 == 0 else -1.0
    return ph, gi, gk, gpos, gu, sg, par, groups


# ───────────────────────── 流体 / 抽象（5）─────────────────────────

def _p_whirlpool(t, p, u, pos, seq_i, n):
    """漩涡吞卷：旋臂扇区 + 收缩进度 + 径向角速渐变 + 下沉。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    sws = _cu._smooth(ph % 1.0)
    a = gi * _TWO / groups + ph * _TWO / (0.35 + 0.85 * gpos) * 0.45 \
        + gpos * 0.55 * math.sin(ph * _TWO + gpos * 5.0)
    r = (0.3 + 0.68 * (1.0 - sws)) * (1.0 - 0.22 * gpos * sws)
    return (math.cos(a) * r * 0.9, math.sin(a) * r - 0.34 * sws * (1.0 - gpos))


def _p_snowfall(t, p, u, pos, seq_i, n):
    """飞雪漫舞：落程组错拍+片噪声错位 + 旋幅随落程渐变。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    nz = _cu._vnoise(seq_i, 11)
    cyc = (ph * 0.5 + gk * 0.47 + nz * 0.09) % 1.0
    sc = _cu._smooth(cyc)
    x = 0.88 * math.sin(ph * _TWO * 0.55 + nz * 3.6 + gpos * 4.2) \
        * (0.3 + 0.7 * (1.0 - sc)) + 0.06 * math.sin(ph * _TWO * 0.2 + gk * 1.5)
    return (x, 1.0 - 2.0 * sc)


def _p_taiji(t, p, u, pos, seq_i, n):
    """太极旋运：左右半上下对抱 + 双弧逆旋（u 符号分半,非奇偶）。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    half = 1.0 if u >= 0 else -1.0
    th = (gu + 1.0) * 0.5 * math.pi + (0.0 if u >= 0 else math.pi) \
        + ph * _TWO * 0.5 * half * (0.7 + 0.4 * gk) + gpos * 0.45 * math.sin(ph * _TWO * 1.3)
    r = 0.6 * (1.0 - 0.22 * gk) * (1.0 + 0.18 * math.sin(ph * _TWO * 0.5 + gpos * 1.7))
    return (math.cos(th) * r, math.sin(th) * r * half)


def _p_windchime(t, p, u, pos, seq_i, n):
    """风铃摇响：音高双梯度 + 行进高斯风脉冲调幅。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    f = 1.0 + gk * 1.3 + gu * 0.35
    w = _cu._waveshape("gauss", (pos * 1.4 - ph * 1.1) % 1.0, 0.16)
    return (math.sin(f * ph * _TWO) * (0.5 + 0.5 * w),
            -0.4 + 0.22 * math.cos(f * ph * _TWO * 1.05 + 0.7) * (0.5 + 0.5 * w))


def _p_dandelion(t, p, u, pos, seq_i, n):
    """蒲公英散：黄金角籽位 + 环错时绽放 + 半径分层。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    b = _cu._smooth((ph * 0.5 + gk * 0.45) % 1.0)
    a = seq_i * 2.39996 + gpos * 0.9 * math.sin(ph * _TWO + seq_i * 0.7)
    r = 0.16 + 0.78 * b * (0.5 + 0.5 * gpos)
    return (math.cos(a) * r,
            math.sin(a) * r * 0.92 - 0.24 * (1.0 - b) + 0.1 * math.sin(ph * _TWO + seq_i * 1.7))


# ───────────────────────── 几何 / 星象（6）─────────────────────────

def _p_star_trails(t, p, u, pos, seq_i, n):
    """星轨旋舞：同心环阶梯半径 + 奇偶环反旋 + 开普勒差速 + 星辉微颤。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    r = 0.22 + gk * 0.72
    a = gpos * _TWO + ph * _TWO * (0.55 - 0.32 * gk) * sg \
        + gpos * 0.35 * math.sin(ph * _TWO * 0.45)
    tw = 0.05 * (_cu._vnoise(seq_i, 3) - 0.5) * math.sin(ph * _TWO * 0.4)
    return (math.cos(a) * (r + tw) * (1.0 + 0.1 * math.sin(ph * _TWO * 0.13)),
            math.sin(a) * (r + tw) * (1.0 - 0.1 * math.sin(ph * _TWO * 0.13)))


def _p_meteor_rain(t, p, u, pos, seq_i, n):
    """流星逐夜：延迟复制拖尾（尾迹=同一抛物线轨道延迟采样）+ 组错时发射。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    s = (ph * 0.8 + gk * 0.61 + gpos * 0.12) % 1.0
    return ((s * 2.0 - 1.0) * 0.92 - gpos * 0.18 * (1.0 - s),
            0.74 - 3.4 * s * (0.85 - s))


def _p_moon_phases(t, p, u, pos, seq_i, n):
    """月相盈缺：旋转光源角距软门控 + 盈亏相位 + 亮区上浮。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    sun = ph * 0.5 * _TWO
    a = pos * _TWO + gpos * 0.6 * math.sin(ph * _TWO * 0.3 + gi)
    da = abs(((a - sun + math.pi) % _TWO) - math.pi) / math.pi
    w = 0.5 + 0.5 * math.sin(ph * _TWO * 0.5)
    lit = _cu._smooth(_cu._clamp01((w * 0.92 - da) / 0.18 + 0.5))
    r = 0.66 + 0.06 * (_cu._vnoise(seq_i, 8) - 0.5)
    return (math.cos(a) * r, math.sin(a) * r - 0.2 + 0.42 * lit * (0.7 + 0.3 * gpos))


def _p_hexagon_breath(t, p, u, pos, seq_i, n):
    """蜂巢六边：静态多边形几何项 + 邻边对相呼吸 + 组内凸渐变。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    a = pos * _TWO + ph * 0.12 * _TWO
    poly = 1.0 + 0.2 * math.cos(a * groups)
    r = 0.62 * poly * (1.0 + 0.13 * math.sin(ph * _TWO + gi * _TWO / groups))
    r *= 0.94 + 0.06 * gpos
    return (math.cos(a) * r * (1.0 + 0.06 * math.sin(ph * _TWO * 0.5 + gpos * 2.0)),
            math.sin(a) * r * 0.92)


def _p_constellation(t, p, u, pos, seq_i, n):
    """星座连线：折线星图几何 + 双构型形变 + 逐星独立闪烁。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    morph = 0.5 - 0.5 * math.cos(ph * _TWO * 0.38)
    th = gi * math.pi / groups * (0.6 + 0.8 * morph) \
        + gpos * math.pi / max(1, groups) * 0.5
    ln = (0.5 + 0.35 * math.sin(gpos * math.pi)) \
        * (1.0 + 0.25 * math.sin(ph * _TWO * 0.5 + gi * 0.8))
    tw = 0.045 * (_cu._vnoise(seq_i, 13) - 0.5) * (1.0 + 0.5 * math.sin(ph * _TWO * 1.6 + seq_i))
    ra = ph * 0.1 * _TWO
    px = gpos * math.cos(th) * ln
    py = gpos * math.sin(th) * ln * 0.85
    return (math.cos(ra) * px - math.sin(ra) * py + tw,
            math.sin(ra) * px + math.cos(ra) * py + tw)


# ───────────────────────── 自然（3）─────────────────────────

def _p_waterfall(t, p, u, pos, seq_i, n):
    """飞瀑倾泻：帘列位来自组号 + 组内错拍珠散 + 入水溅沫。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    col = (gi - (groups - 1) * 0.5) / max(1, groups - 1) * 0.85
    cyc = (ph * 0.55 + gk * 0.4 + gu * 0.1) % 1.0
    sc = _cu._smooth(cyc)
    x = col * (1.0 + 0.1 * math.sin(ph * _TWO * 0.4 + gi * 1.1)) \
        + 0.12 * math.sin(ph * _TWO * 0.8 + gpos * 4.0)
    return (x, 1.0 - 2.0 * sc - 0.18 * math.exp(-(((cyc - 0.06) % 1.0) * 9.0) ** 2))


def _p_aurora_dance(t, p, u, pos, seq_i, n):
    """极光流转：幕层对向流动（组间对涌）+ 行进高斯峰包络。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    x = (gk - 0.5) * 0.75 + 0.3 * math.sin(ph * _TWO * 1.15 * sg + pos * 6.8)
    hc = (ph * 0.5 + gk * 0.35) % 1.0
    hump = math.exp(-(((pos - hc) * 3.6) ** 2)) * (0.75 + 0.25 * gk)
    y = 0.12 + 0.72 * hump + 0.12 * math.sin(ph * _TWO * 0.45 + gpos * 2.4) * (0.5 + hump)
    return (x, y)


def _p_tide_wash(t, p, u, pos, seq_i, n):
    """潮汐洄澜：非对称冲滩循环（快进慢退）+ 弧锋弯曲 + 滩位错时。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    cyc = (ph * 0.42 + gk * 0.35) % 1.0
    fwd = _cu._smooth(_cu._clamp01((cyc - gpos * 0.08) * 2.6))
    back = _cu._smooth(_cu._clamp01((cyc - 0.35) * 1.8))
    p = _cu._clamp01(fwd - back)
    bow = 0.7 + 0.3 * math.sin(gpos * math.pi)
    return ((gk - 0.5) * 1.4 + p * 0.62 * bow, -0.5 + 0.55 * p * (0.6 + 0.4 * bow))


# ───────────────────────── 人文（6）─────────────────────────

def _p_silk_ribbon(t, p, u, pos, seq_i, n):
    """绸带卷舒：段内 sin 包络 + 双频嵌套 + 段间慢波交替卷向。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    seg = math.sin(gpos * math.pi)
    x = gu * 0.72 + 0.18 * math.sin(ph * _TWO * 1.35 + pos * 4.5) \
        + 0.12 * math.sin(ph * _TWO * 0.3 + gk * 2.6) * seg
    y = 0.38 * math.sin(ph * _TWO * 0.42 + gk * 1.9) * (0.4 + 0.6 * seg) \
        - 0.16 * math.sin(ph * _TWO * 1.15 + pos * 5.2) * (0.5 + 0.5 * seg) \
        + par * 0.07 * math.sin(ph * _TWO * 0.25 + gk)
    return (x, y)


def _p_bronze_bells(t, p, u, pos, seq_i, n):
    """编钟错鸣：离散击打 + exp 指数衰减包络 + 击打前沿逐组传递。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    f = 1.0 + gk * 1.1 + gu * 0.3
    st = (ph * 0.85 + gk * 0.26 + gpos * 0.1) % 1.0
    env = math.exp(-st * 5.5)
    return (math.sin(f * ph * _TWO) * env * 0.5,
            -0.42 + 0.26 * math.cos(f * ph * _TWO + 0.6) * env)


def _p_water_wheel(t, p, u, pos, seq_i, n):
    """水车轮转：旋转轮缘 × 二次谐波径向偏心 + 轮缘段滞后波。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    a = pos * _TWO + ph * _TWO * 0.55
    ecc = 1.0 - 0.16 * math.sin(2.0 * a + gpos * 1.2)
    r = 0.68 * ecc + 0.06 * math.sin(ph * _TWO + gi * 1.4)
    return (math.cos(a) * r,
            math.sin(a) * r * 0.86 + 0.1 * math.sin(ph * _TWO * 1.6 + gpos * 3.0))


def _p_kite_line(t, p, u, pos, seq_i, n):
    """纸鸢牵线：锚点缓漂 + 错时升空斜坡 + 组内小轨道。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    prog = (ph * 0.55 + gk * 0.43) % 1.0
    la = _cu._smooth(prog)
    ax = (gk - 0.5) * 1.55 + 0.4 * math.sin(ph * _TWO * 0.11 + gk * 2.0)
    x = ax + 0.3 * math.sin(ph * _TWO * 1.1 + gpos * 2.0) * la
    y = 0.62 * la - 0.18 - 0.26 * math.sin(ph * _TWO * 0.75 + gpos * 1.7) * la \
        + 0.08 * math.sin(ph * _TWO * 0.23 + gk)
    return (x, y)


def _p_fireworks_ring(t, p, u, pos, seq_i, n):
    """烟花绽放：√t 减速扩张 + 重力二次垂落 + 组错时爆炸。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    bp = _cu._clamp01((ph * 0.6 + gk * 0.33 - 0.12) * 1.5)
    r = 0.9 * math.sqrt(bp) * (0.55 + 0.45 * gpos)
    a = pos * _TWO + ph * _TWO * 0.5 * (0.4 + gpos) * (1.0 - bp * 0.5)
    return (math.cos(a) * r * (1.0 + 0.07 * (_cu._vnoise(seq_i, 5) - 0.5)),
            math.sin(a) * r * 0.95 - 0.75 * bp * bp + 0.3)


# ───────────────────────── 音乐 / 抽象（1）─────────────────────────

def _p_glissando(t, p, u, pos, seq_i, n):
    """滑音游走：行进高斯音符窗 + 折返扫掠 + 八度递进。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    sweep = 0.5 - 0.5 * math.cos(ph * _TWO * 0.6)
    nw = _cu._waveshape("gauss", (pos * groups - sweep * groups + gk * 0.4) % 1.0, 0.1)
    y = 0.78 * nw * (1.0 - 0.25 * gk) + 0.1 * math.sin(ph * _TWO * 2.1 + pos * 6.0) * nw
    return (gu * 0.8 + 0.1 * math.sin(ph * _TWO * 0.32 + gk * 2.2), y)


# ───────────────────────── 动物（2）─────────────────────────

def _p_jellyfish(t, p, u, pos, seq_i, n):
    """水母浮沉：伞缘高斯脉冲外扩 + 触手列方位摆 + 竖直浮沉。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    au = abs(u)
    pulse = _cu._waveshape("gauss", (au - ph * 0.8) % 1.0, 0.2)
    a = (gi + gpos) * _TWO / groups + gpos * 0.5 * math.sin(ph * _TWO + gi)
    r = 0.48 + 0.3 * pulse + 0.12 * (1.0 - gpos)
    return (math.cos(a) * r,
            math.sin(a) * r * 0.5 - 0.35 * gpos - 0.05 + 0.28 * math.sin(ph * _TWO * 0.32))


def _p_eagle(t, p, u, pos, seq_i, n):
    """苍鹰盘旋：FM 相位调制轨道 + 翼尖大扑（左右翼镜像反相）。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    a = ph * _TWO + 0.52 * math.sin(ph * _TWO * 0.5 + gpos * 2.4)
    R = 0.7 + 0.13 * math.sin(ph * _TWO * 0.37)
    return (math.cos(a) * R * (0.9 + 0.1 * gk),
            math.sin(a) * R * 0.6
            + par * 0.17 * math.sin(ph * _TWO * 2.0 + gpos * 3.2) * (0.35 + 0.65 * gpos))


# ───────────────────────── 流体 / 抽象（补 1）─────────────────────────

def _p_compass_rose(t, p, u, pos, seq_i, n):
    """罗盘八向：象限反旋 + 正交相位呼吸（旋转椭圆错觉）+ 叶形渐变。"""
    ph, gi, gk, gpos, gu, sg, par, groups = _pose_common(t, p, pos, seq_i)
    qd = gi % 4
    qsign = 1.0 if qd % 2 == 0 else -1.0
    a = (gi + gpos) * _TWO / groups * qsign + ph * _TWO * 0.16 * qsign
    br = 0.5 + 0.5 * math.cos(ph * _TWO * 0.5 + qd * math.pi / 2.0)
    r = (0.42 + 0.5 * br) * (0.8 + 0.2 * math.sin(gpos * math.pi))
    return (math.cos(a) * r, math.sin(a) * r * 0.9)


# ───────────────────────── 分发 ─────────────────────────

_POSE_KINDS = frozenset({
    "whirlpool", "snowfall", "taiji", "windchime", "dandelion",
    "star_trails", "meteor_rain", "moon_phases", "hexagon_breath", "constellation",
    "waterfall", "aurora_dance", "tide_wash",
    "silk_ribbon", "bronze_bells", "water_wheel", "kite_line", "fireworks_ring",
    "glissando", "jellyfish", "eagle", "compass_rose",
})

_POSE_FN = {
    "whirlpool": _p_whirlpool, "snowfall": _p_snowfall, "taiji": _p_taiji,
    "windchime": _p_windchime, "dandelion": _p_dandelion,
    "star_trails": _p_star_trails, "meteor_rain": _p_meteor_rain,
    "moon_phases": _p_moon_phases, "hexagon_breath": _p_hexagon_breath,
    "constellation": _p_constellation,
    "waterfall": _p_waterfall, "aurora_dance": _p_aurora_dance, "tide_wash": _p_tide_wash,
    "silk_ribbon": _p_silk_ribbon, "bronze_bells": _p_bronze_bells,
    "water_wheel": _p_water_wheel, "kite_line": _p_kite_line,
    "fireworks_ring": _p_fireworks_ring,
    "glissando": _p_glissando, "jellyfish": _p_jellyfish, "eagle": _p_eagle,
    "compass_rose": _p_compass_rose,
}


def _formation_pose_ext(kind: str, u: float, pos: float,
                        seq_i: int, n: int, t: float, p: dict) -> Tuple[float, float]:
    """新 kind 姿态分发：未知 kind 回退平位（不抛错,保序）。"""
    fn = _POSE_FN.get(kind)
    if fn is None:
        return (u, 0.0)
    return fn(t, p, u, pos, seq_i, n)
