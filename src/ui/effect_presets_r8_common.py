# -*- coding: utf-8 -*-
"""
effect_presets_r8_common — R8 第二轮公共工具（2026-08-13）

五个主题家族文件（water / scan / color / space / audio）共用的确定性场函数与
参数工厂。全部为纯函数：同输入同输出，无全局状态，无 random，打包安全。
"""
from __future__ import annotations

import math

from .effect_engine import ParamSpec, CH_TYPES


# ───────────────────────── 确定性场函数 ─────────────────────────

def _hash01(i: int, seed: int) -> float:
    """确定性整数 hash → [0,1)。同输入同输出。"""
    x = (i * 2654435761 + seed * 2246822519) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 2246822519) & 0xFFFFFFFF
    x ^= x >> 13
    return (x & 0xFFFFFF) / float(0x1000000)


def _clamp01(x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x


def _smooth01(x: float) -> float:
    """smoothstep [0,1]→[0,1]，越界钳制。"""
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


def _snoise01(x: float, seed: int) -> float:
    """一维平滑值噪声 [0,1]。"""
    i = int(math.floor(x))
    f = x - i
    a = _hash01(i, seed)
    b = _hash01(i + 1, seed)
    return a + (b - a) * _smooth01(f)


def _fbm01(x: float, seed: int, octaves: int = 4) -> float:
    """分形值噪声 [0,1]，octaves 层叠加（每层频率×2、振幅×0.5）。"""
    total = 0.0
    amp = 1.0
    norm = 0.0
    freq = 1.0
    for _ in range(max(1, octaves)):
        total += amp * _snoise01(x * freq, seed)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / max(1e-9, norm)


def _gauss(x: float, sigma: float) -> float:
    """高斯脉冲，σ>0，峰值 1。"""
    return math.exp(-(x * x) / max(1e-9, 2.0 * sigma * sigma))


def _tri01(x: float) -> float:
    """三角波 [0,1]：0→1→0，周期 1。"""
    return 1.0 - abs(2.0 * (x % 1.0) - 1.0)


def _motor_write(ef, orig_i: int, x: float, y: float, center: int, rad: float) -> None:
    """电机双轴输出：中位 + 归一化偏移×半径，钳制 [0,255]。"""
    ef.values[orig_i]["motor_x"] = int(max(0, min(255, center + x * rad)))
    ef.values[orig_i]["motor_y"] = int(max(0, min(255, center + y * rad)))


# ───────────────────────── 参数工厂 ─────────────────────────
# 每次调用返回全新 ParamSpec 实例：_reg 会就地改 min/max 并追加 6 个通用参数，
# 因此参数表绝不可跨效果共享（必须逐效果构造）。

def _speed_spec(speed: float, max_v: float = 8.0) -> ParamSpec:
    return ParamSpec("speed", "速度", "float", speed, 0.0, max_v, 0.05,
                     tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快")


def _freq_spec(freq: float) -> ParamSpec:
    return ParamSpec("spatial_freq", "空间频率", "float", freq, 0.0, 12.0, 0.1,
                     tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集")


def _seed_spec() -> ParamSpec:
    return ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
                     tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列")


def _lo_spec() -> ParamSpec:
    return ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                     tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低")


def _hi_spec() -> ParamSpec:
    return ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                     tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高")


def _ch_type_spec() -> ParamSpec:
    return ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
                     tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置")


def _angle_spec() -> ParamSpec:
    return ParamSpec("angle", "幅度", "float", 1.0, 0.0, 1.0, 0.02,
                     tooltip="控制电机偏移幅度。0=不动，1=走满全角域")


def _center_spec() -> ParamSpec:
    return ParamSpec("center", "中位", "int", 128, 0, 255, 1,
                     tooltip="控制电机输出的中位基准值。轨迹围绕该值上下摆动")


def _decay_spec(decay: float) -> ParamSpec:
    return ParamSpec("decay", "衰减", "float", decay, 0.02, 2.0, 0.02,
                     tooltip="控制脉冲衰减速度。值越小衰减越快，余辉越短；值越大拖尾越长")


def _count_spec(count: int) -> ParamSpec:
    return ParamSpec("count", "数量", "int", count, 1, 8, 1,
                     tooltip="控制同步运动单元的数量。值越大场中运动体越多")


def _width_spec(width: float) -> ParamSpec:
    return ParamSpec("width", "宽度", "float", width, 0.02, 1.0, 0.02,
                     tooltip="控制运动体的空间宽度。值越大光斑/水柱越宽胖")
