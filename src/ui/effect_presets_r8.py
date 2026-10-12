# -*- coding: utf-8 -*-
"""
effect_presets_r8 — R8 效果再创作（2026-08-13）

8 个全新效果，全部来自侦察报告的高价值空白区（非 matrix、非状态类）：
  水景/泵   : 水镜涟漪(点源圆波+岸边镜像干涉) / 水面风纹(阵风斑) / 涌浪拍岸(碎波)
  通用/高级 : 等离子流彩(不可公约正弦干涉场) / 噪声流(平滑值噪声场)
  灯光/明暗 : 烛光摇曳(真蜡烛统计闪烁: 高频焰尖+慢速风摆)
  电机/编组 : 萤火虫场(2Hz 频闪 × 12s 爆发波双时间尺度) / 聚阵焦点(全场指向游移焦点)

铁律遵守：
  - 确定性可复现：全部为纯场函数 f(pos, t, seed)，hash 为整数确定性 hash，无 random。
  - 设备集 scoped：target_channels 精确声明（pump / ALL / motor_x+y）。
  - 单通道通用：标量类效果经 ch_type 作用于任意单通道，不覆盖整机 fill。
  - schema+compute 约定：EffectPreset + ParamSpec，compute 签名与引擎一致：
    fn(ef, t, sorted_fx, fdefs, params, fps)，写 ef.values[orig_i][ch]。
"""
from __future__ import annotations

import copy
import math
from typing import Callable

from .effect_engine import EffectPreset, ParamSpec, CH_TYPES


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
    """分形值噪声 [0,1]。"""
    amp = 0.5
    freq = 1.0
    total = 0.0
    norm = 0.0
    for o in range(octaves):
        total += amp * _snoise01(x * freq, seed + o * 101)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / max(1e-6, norm)


def _motor_write(ef, orig_i: int, x: float, y: float, center: int, rad: float):
    """按编组约定写入双轴电机值：center ± 归一化偏移 × 角度半径。"""
    ef.values[orig_i]["motor_x"] = int(max(0, min(255, center + x * rad)))
    ef.values[orig_i]["motor_y"] = int(max(0, min(255, center + y * rad)))


# ═════════════════ 水景 / 泵（3） ═════════════════

def _fx_mirror_ripple(ef, t, sorted_fx, fdefs, params, fps):
    """水镜涟漪：点源圆波 + 两岸镜像源干涉，波到岸如镜面折返。"""
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    speed = float(params.get("speed", 0.8))
    freq = float(params.get("spatial_freq", 4.0))
    seed = int(params.get("seed", 0))
    src = max(1, int(params.get("sources", 2)))
    n = max(1, len(sorted_fx))
    k = freq * math.pi
    om = 2.0 * math.pi * speed
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        pos = seq_i / max(1, n - 1)
        acc = 0.0
        for s in range(src):
            cx = 0.18 + 0.64 * _hash01(s + 1, seed)
            for mx in (cx, -cx, 2.0 - cx):  # 实源 + 左右两岸镜像源
                d = abs(pos - mx)
                acc += math.cos(k * d - om * t) * (0.5 / (1.0 + d * 1.8))
        w = 0.5 + 0.5 * _clamp01(0.5 + acc)
        ef.values[orig_i]["pump"] = int(lo + w * (hi - lo))


def _fx_wind_streaks(ef, t, sorted_fx, fdefs, params, fps):
    """水面风纹：阵风贴水掠过，风斑徐移，水面明暗成纹。"""
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    speed = float(params.get("speed", 0.5))
    seed = int(params.get("seed", 0))
    gusts = max(1, int(params.get("sources", 3)))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        pos = seq_i / max(1, n - 1)
        acc = 0.0
        for g in range(gusts):
            c = (_hash01(g, seed) + t * speed * (0.10 + 0.08 * _hash01(g, seed + 5))) % 1.0
            sig = 0.06 + 0.10 * _hash01(g, seed + 9)
            d = pos - c
            if d > 0.5:
                d -= 1.0
            if d < -0.5:
                d += 1.0
            acc += math.exp(-(d * d) / (2.0 * sig * sig))
        w = 0.25 + 0.55 * min(1.0, acc)
        ef.values[orig_i]["pump"] = int(lo + w * (hi - lo))


def _fx_surge_breaker(ef, t, sorted_fx, fdefs, params, fps):
    """涌浪拍岸：慢涌渐起、峰成即碎的非对称碎波，浪头向岸依次拍落。"""
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    speed = float(params.get("speed", 0.5))
    freq = float(params.get("spatial_freq", 2.0))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        pos = seq_i / max(1, n - 1)
        cyc = (t * speed * 0.3 + pos * freq) % 1.0
        if cyc < 0.62:  # 涌起
            w = 0.12 + 0.78 * _smooth01(cyc / 0.62)
        elif cyc < 0.82:  # 拍岸崩塌
            col = (cyc - 0.62) / 0.20
            w = 0.9 * math.exp(-col * 3.2) + 0.18 * (1.0 - col)
        else:  # 退水
            w = 0.18 * (1.0 - (cyc - 0.82) / 0.18) + 0.08
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


# ═════════════════ 通用 / 高级（2） ═════════════════

def _fx_plasma(ef, t, sorted_fx, fdefs, params, fps):
    """等离子流彩：四条不可公约正弦干涉，有机流动，永不机械重复。"""
    ch = params.get("ch_type", "dimmer")
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    speed = float(params.get("speed", 0.6))
    freq = float(params.get("spatial_freq", 3.0))
    seed = int(params.get("seed", 0))
    n = max(1, len(sorted_fx))
    p0 = _hash01(seed + 3, seed) * 6.28318
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        x = (seq_i / max(1, n - 1)) * freq * 6.28318
        acc = math.sin(x * 1.0 + t * speed * 1.7 + p0)
        acc += math.sin(x * 2.7 - t * speed * 1.1 + p0 * 0.6)
        acc += math.sin(x * 4.1 + t * speed * 0.7 + p0 * 1.3)
        acc += math.sin(x * 7.3 - t * speed * 2.3 + p0 * 1.9)
        w = 0.5 + acc * 0.125
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_noise_flow(ef, t, sorted_fx, fdefs, params, fps):
    """噪声流：平滑分形值噪声场缓缓流淌，如云影过野。"""
    ch = params.get("ch_type", "dimmer")
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    speed = float(params.get("speed", 0.8))
    freq = float(params.get("spatial_freq", 3.0))
    seed = int(params.get("seed", 0))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        pos = seq_i / max(1, n - 1)
        w = _fbm01(pos * freq + t * speed * 0.6, seed)
        ef.values[orig_i][ch] = int(lo + w * (hi - lo))


# ═════════════════ 灯光 / 明暗（1） ═════════════════

def _fx_candle(ef, t, sorted_fx, fdefs, params, fps):
    """烛光摇曳：焰尖高频抖动叠慢速风摆，明灭如豆，非机械正弦。"""
    ch = params.get("ch_type", "dimmer")
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    speed = float(params.get("speed", 1.0))
    seed = int(params.get("seed", 0))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        f = int(t * speed * 13.0)
        flick = _hash01(f * 3 + seq_i * 7, seed)  # 高频焰尖抖动
        gust_ph = (t * speed * 0.07 + _hash01(seq_i, seed + 11)) % 1.0
        gust = 0.5 - 0.5 * math.cos(gust_ph * 6.28318)  # 慢速风摆
        w = 0.80 + 0.16 * (flick - 0.5) + 0.10 * (0.5 - gust) * math.sin(flick * 6.28318)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


# ═════════════════ 电机 / 编组（2） ═════════════════

def _fx_firefly_field(ef, t, sorted_fx, fdefs, params, fps):
    """萤火虫场：2Hz 频闪 × 12 秒爆发波双时间尺度，明灭如流萤过野。"""
    center = int(params.get("center", 128))
    rad = 127.0 * _clamp01(float(params.get("angle", 1.0)))
    speed = float(params.get("speed", 1.0))
    seed = int(params.get("seed", 0))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        pos = seq_i / max(1, n - 1)
        u = pos * 2.0 - 1.0
        burst = (t * speed * 0.085 + pos * 0.6 + _hash01(seq_i % 7, seed)) % 1.0
        env = math.exp(-(((burst - 0.5) * 2.0) ** 2) * 2.2)
        ph = _hash01(seq_i, seed + 3)
        blink = max(0.0, math.sin((t * speed * 2.0 + ph) * 6.28318)) ** 3
        sway = math.sin(burst * 6.28318 * 2.0)
        x = 0.42 * env * sway * (0.55 + 0.45 * u)
        y = 0.55 * env * (0.28 + 0.72 * blink) - 0.1
        _motor_write(ef, orig_i, x, y, center, rad)


def _fx_focus_beam(ef, t, sorted_fx, fdefs, params, fps):
    """聚阵焦点：全场指向同一游移焦点，近聚远散，万众一心。"""
    center = int(params.get("center", 128))
    rad = 127.0 * _clamp01(float(params.get("angle", 1.0)))
    speed = float(params.get("speed", 0.5))
    n = max(1, len(sorted_fx))
    fcx = 0.72 * math.sin(t * speed * 0.23 * 6.28318)
    fcy = 0.55 * math.sin(t * speed * 0.17 * 6.28318 + 2.1)
    fd = 0.35 + 0.85 * (0.5 - 0.5 * math.cos(t * speed * 0.14 * 6.28318))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        u = (seq_i / max(1, n - 1)) * 2.0 - 1.0
        ax = math.atan((u - fcx) / fd) / (math.pi * 0.5)
        ay = math.atan(-fcy / fd) / (math.pi * 0.5)
        _motor_write(ef, orig_i, ax, ay, center, rad)


# ═════════════════ 参数与注册 ═════════════════

def _speed_spec(speed: float, max_v: float = 8.0):
    return ParamSpec("speed", "速度", "float", speed, 0.0, max_v, 0.05,
        tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快")


def _freq_spec(freq: float):
    return ParamSpec("spatial_freq", "空间频率", "float", freq, 0.0, 12.0, 0.1,
        tooltip="控制效果在空间维度上的变化频率。值越高单位空间内变化越密集")


def _sources_spec(sources: int):
    return ParamSpec("sources", "源数", "int", sources, 1, 6, 1,
        tooltip="控制点源/风斑的数量。值越大干涉纹理越丰富")


def _seed_spec():
    return ParamSpec("seed", "随机种子", "int", 0, 0, 9999, 1,
        tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列")


def _lo_spec():
    return ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
        tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低")


def _hi_spec():
    return ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
        tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高")


def _ch_type_spec():
    return ParamSpec("ch_type", "通道类别", "choice", "dimmer", choices=CH_TYPES,
        tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置")


def _angle_spec():
    return ParamSpec("angle", "幅度", "float", 1.0, 0.0, 1.0, 0.02,
        tooltip="控制电机偏移幅度。0=不动，1=走满全角域")


def _center_spec():
    return ParamSpec("center", "中位", "int", 128, 0, 255, 1,
        tooltip="控制电机输出的中位基准值。轨迹围绕该值上下摆动")


# 每个效果只暴露其 compute 实际消费的参数（无装饰性旋钮）：
_PUMP_RIPPLE = [_speed_spec(0.8), _freq_spec(4.0), _sources_spec(2), _seed_spec(), _lo_spec(), _hi_spec()]
_PUMP_WIND = [_speed_spec(0.5), _sources_spec(3), _seed_spec(), _lo_spec(), _hi_spec()]
_PUMP_SURGE = [_speed_spec(0.5), _freq_spec(2.0), _lo_spec(), _hi_spec()]
_SCALAR_FIELD = [_ch_type_spec(), _speed_spec(0.8), _freq_spec(3.0), _seed_spec(), _lo_spec(), _hi_spec()]
_CANDLE = [_ch_type_spec(), _speed_spec(1.0), _seed_spec(), _lo_spec(), _hi_spec()]
_MOTOR_FIELD = [_speed_spec(1.0, 4.0), _angle_spec(), _center_spec(), _seed_spec()]
_MOTOR_FOCUS = [_speed_spec(0.5, 4.0), _angle_spec(), _center_spec()]


def _register_r8_presets(engine) -> int:
    """注册 R8 再创作效果，返回注册数。由 effect_engine 注册链末段调用。"""
    reg = engine._reg
    count = 0

    def _add(pid, name, cat, desc, channels, icon, params, fn: Callable):
        nonlocal count
        reg(EffectPreset(
            id=pid, name=name, category=cat, description=desc,
            target_channels=channels, icon=icon,
            tags=["curated", "r8"], params=copy.deepcopy(params),
        ), fn)
        count += 1

    MXY = ["motor_x", "motor_y"]
    ALL = CH_TYPES

    _add("cur_r8_mirror_ripple", "水镜涟漪", "水景/泵",
         "点源圆波遇岸成镜，波纹往返干涉如镜面回响。",
         ["pump"], "🌊", _PUMP_RIPPLE, _fx_mirror_ripple)
    _add("cur_r8_wind_streaks", "水面风纹", "水景/泵",
         "阵风贴水掠过，风斑徐移，水面明暗成纹。",
         ["pump"], "🍃", _PUMP_WIND, _fx_wind_streaks)
    _add("cur_r8_surge_breaker", "涌浪拍岸", "水景/泵",
         "慢涌渐起、峰成即碎，浪头向岸依次拍落。",
         ["pump"], "🌊", _PUMP_SURGE, _fx_surge_breaker)
    _add("cur_r8_plasma", "等离子流彩", "通用/高级",
         "四条不可公约正弦相互干涉，有机流动，生生不息。",
         ALL, "🌈", [_ch_type_spec(), _speed_spec(0.6), _freq_spec(3.0), _seed_spec(), _lo_spec(), _hi_spec()],
         _fx_plasma)
    _add("cur_r8_noise_flow", "噪声流", "通用/高级",
         "平滑分形值噪声场缓缓流淌，如云影过野。",
         ALL, "☁", _SCALAR_FIELD, _fx_noise_flow)
    _add("cur_r8_candle", "烛光摇曳", "灯光/明暗",
         "焰尖高频抖动叠慢速风摆，明灭如豆，非机械正弦。",
         ["dimmer"], "🕯", _CANDLE, _fx_candle)
    _add("cur_r8_firefly_field", "萤火虫场", "电机/编组",
         "2Hz 频闪叠 12 秒爆发波，双时间尺度，明灭如流萤过野。",
         MXY, "✨", _MOTOR_FIELD, _fx_firefly_field)
    _add("cur_r8_focus_beam", "聚阵焦点", "电机/编组",
         "全场指向同一游移焦点，近聚远散，万众一心。",
         MXY, "🎯", _MOTOR_FOCUS, _fx_focus_beam)
    return count
