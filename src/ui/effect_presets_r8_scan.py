# -*- coding: utf-8 -*-
"""
effect_presets_r8_scan — R8 第二轮·扫描追逐家族（2026-08-13）

8 个扫描/追逐类效果（通用/跑灯，单通道通用经 ch_type 作用于任意通道）。
取材自舞台扫描器与光束追逐经典语汇：慧星扫尾 / 双头对扫 / 十字交叉扫 /
回旋镖 / 流星雨 / 蛇形折返 / 接力跑 / 乒乓对弹。

铁律遵守：
  - 确定性可复现：纯场函数 f(pos, t, seed)，整数确定性 hash（流星雨轨迹），无 random。
  - 单通道通用：经 ch_type 作用于任意单通道，不覆盖整机 fill。
  - 每个效果只暴露其 compute 实际消费的参数。
  - compute 签名与引擎一致：fn(ef, t, sorted_fx, fdefs, params, fps)。
"""
from __future__ import annotations

import copy
import math
from typing import Callable

from .effect_engine import EffectPreset, CH_TYPES
from .effect_presets_r8_common import (
    _hash01, _clamp01, _smooth01, _tri01, _gauss,
    _speed_spec, _seed_spec, _lo_spec, _hi_spec, _ch_type_spec,
    _decay_spec, _count_spec, _width_spec,
)


def _fx_comet(ef, t, sorted_fx, fdefs, params, fps):
    """慧星扫尾：亮核在前、尾迹指数拖在身后——扫尾(而非光斑平移)才是慧星的
    辨识度：核亮尾长，运动方向被尾迹写出来。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.6))
    decay = float(params.get("decay", 0.15))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    head = (t * speed) % 1.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        d = head - p
        w = math.exp(-d / decay) if 0.0 <= d <= 0.5 else 0.0
        w = max(w, _gauss(d, 0.03))
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_dualsweep(ef, t, sorted_fx, fdefs, params, fps):
    """双头对扫：两个扫头从链两端相向而行、穿越彼此——对撞互穿的瞬间两束光
    叠加成一道更亮的墙，随后各自奔向对岸，方向感与相遇感并存。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.5))
    width = float(params.get("width", 0.08))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    h1 = (t * speed) % 1.0
    h2 = 1.0 - h1
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = _gauss(p - h1, width) + 0.8 * _gauss(p - h2, width)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_cross(ef, t, sorted_fx, fdefs, params, fps):
    """十字交叉扫：两束光同向而行、速度不可公约(1:1.618)——快束不断追越慢束，
    交叉点沿链漂移且永不落在同一位置，交叉处双倍亮。追越的节拍差是表现力。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.5))
    width = float(params.get("width", 0.05))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    h1 = (t * speed) % 1.0
    h2 = (t * speed * 1.618) % 1.0
    cross_flash = 1.0 if abs(h1 - h2) < width * 3.0 else 0.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = _gauss(p - h1, width) + _gauss(p - h2, width)
        w += cross_flash * 0.5 * _gauss(p - (h1 + h2) * 0.5, width * 2.0)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_boomerang(ef, t, sorted_fx, fdefs, params, fps):
    """回旋镖：光点飞出、减速、折返——smoothstep 位置轮廓给出"抛出去再回来"
    的加速-滞留-返回三段体感，折返点有自然停顿，是机械往复扫描没有的。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.4))
    width = float(params.get("width", 0.05))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    u = _tri01(t * speed)
    pos = 0.05 + 0.9 * _smooth01(u)
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = _gauss(p - pos, width)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_meteor(ef, t, sorted_fx, fdefs, params, fps):
    """流星雨：多颗流星各自按 hash 决定的起点/速度/周期划过链上的一段——轨迹
    随机但完全可复现。流星头亮尾暗的条带+突然出现与熄灭，是随机感表演的母题。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 1.0))
    count = int(params.get("count", 4))
    seed = int(params.get("seed", 0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = 0.0
        for k in range(max(1, count)):
            sk = seed + k * 101
            start = _hash01(k, sk)
            vk = 0.4 + 0.8 * _hash01(k, sk + 1)
            period = 2.0 + 3.0 * _hash01(k, sk + 2)
            phase = ((t * speed * vk / period) + _hash01(k, sk + 3)) % 1.0
            if phase < 0.22:
                length = 0.2 + 0.4 * _hash01(k, sk + 4)
                u = phase / 0.22
                head = start + u * length
                d = head - p
                env = math.sin(u * math.pi)
                if 0.0 <= d <= 0.12:
                    w = max(w, env * math.exp(-d / 0.05))
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_snake(ef, t, sorted_fx, fdefs, params, fps):
    """蛇形折返：蛇头沿链往返游走，身体尾迹拖在运动方向一侧——折返时尾迹
    换边(跟随方向)，蛇的"游"感来自方向感知的尾迹，而非往复平移。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.5))
    decay = float(params.get("decay", 0.25))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    phase = t * speed * math.pi
    head = 0.5 - 0.5 * math.cos(phase)
    d_sign = 1.0 if math.sin(phase) > 0.0 else -1.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        d = (head - p) * d_sign
        w = math.exp(-d / decay) if 0.0 <= d <= 0.5 else 0.0
        w = max(w, _gauss((p - head) * d_sign * -1.0, 0.03))
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_relay(ef, t, sorted_fx, fdefs, params, fps):
    """接力跑：链分多段，接力棒在每段内越跑越快(平方加速)，段与段交接瞬间
    边界闪亮——交接的闪光把"接力"这个协作语义写了出来。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.6))
    count = int(params.get("count", 4))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    u = (t * speed) % 1.0
    seg = min(count - 1, int(u * count))
    local = (u * count - seg)
    handoff = 1.0 if local < 0.06 else 0.0
    baton = seg / count + (local * local) * (1.0 / count)
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = _gauss(p - baton, 0.04)
        if handoff:
            w = max(w, 0.6 * _gauss(p - seg / count, 0.02))
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_pingpong(ef, t, sorted_fx, fdefs, params, fps):
    """乒乓对弹：一重一轻两颗弹丸在两端之间反弹，相遇瞬间对撞闪光——对撞的
    偶然性(两颗弹丸周期不同,相遇点漂移)让追逐有了比赛般的张力。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.7))
    width = float(params.get("width", 0.05))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    p1 = _tri01(t * speed)
    p2 = _tri01(t * speed * 1.4 + 0.5)
    clash = 0.6 if abs(p1 - p2) < 0.04 else 0.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = 0.75 * _gauss(p - p1, width) + 0.5 * _gauss(p - p2, width)
        w += clash * _gauss(p - (p1 + p2) * 0.5, width * 2.0)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _register_r8_scan(engine) -> int:
    """注册 R8 第二轮扫描追逐家族 8 效果，返回注册数。由 effect_engine 注册链调用。"""
    reg = engine._reg
    count = 0

    def _add(pid, name, desc, icon, params, fn: Callable):
        nonlocal count
        reg(EffectPreset(
            id=pid, name=name, category="通用/跑灯", description=desc,
            target_channels=CH_TYPES, icon=icon,
            tags=["curated", "r8", "scan"], params=copy.deepcopy(params),
        ), fn)
        count += 1

    _add("cur_r8s_comet", "慧星扫尾",
         "亮核在前指数尾迹拖后，运动方向被尾迹写出的经典追逐语汇。",
         "☄️", [_ch_type_spec(), _speed_spec(0.6), _decay_spec(0.15), _lo_spec(), _hi_spec()], _fx_comet)
    _add("cur_r8s_dualsweep", "双头对扫",
         "两扫头相向穿越，相遇瞬间叠加成光墙后各奔对岸。",
         "🔀", [_ch_type_spec(), _speed_spec(0.5), _width_spec(0.08), _lo_spec(), _hi_spec()], _fx_dualsweep)
    _add("cur_r8s_cross", "十字交叉扫",
         "两束光同向不可公约速度追越，交叉点漂移且永不重复。",
         "❌", [_ch_type_spec(), _speed_spec(0.5), _width_spec(0.05), _lo_spec(), _hi_spec()], _fx_cross)
    _add("cur_r8s_boomerang", "回旋镖",
         "光点抛出-减速-折返，折返点自然停顿的三段体感。",
         "🪃", [_ch_type_spec(), _speed_spec(0.4), _width_spec(0.05), _lo_spec(), _hi_spec()], _fx_boomerang)
    _add("cur_r8s_meteor", "流星雨",
         "多颗流星按 hash 轨迹划过链段，随机感但完全可复现。",
         "🌠", [_ch_type_spec(), _speed_spec(1.0), _count_spec(4), _seed_spec(), _lo_spec(), _hi_spec()], _fx_meteor)
    _add("cur_r8s_snake", "蛇形折返",
         "蛇头往返游走，尾迹随折返换边，方向感知的尾迹即蛇的游感。",
         "🐍", [_ch_type_spec(), _speed_spec(0.5), _decay_spec(0.25), _lo_spec(), _hi_spec()], _fx_snake)
    _add("cur_r8s_relay", "接力跑",
         "链分多段接力，棒在段内平方加速，交接瞬间边界闪光。",
         "🏃", [_ch_type_spec(), _speed_spec(0.6), _count_spec(4), _lo_spec(), _hi_spec()], _fx_relay)
    _add("cur_r8s_pingpong", "乒乓对弹",
         "一重一轻两弹丸两端反弹，相遇点漂移的对撞闪光张力。",
         "🏓", [_ch_type_spec(), _speed_spec(0.7), _width_spec(0.05), _lo_spec(), _hi_spec()], _fx_pingpong)
    return count
