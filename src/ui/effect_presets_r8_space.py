# -*- coding: utf-8 -*-
"""
effect_presets_r8_space — R8 第二轮·空间游戏家族（2026-08-13）

8 个空间游戏效果（通用/高级，单通道通用经 ch_type 作用于任意通道）。
象限轮舞 / 内外圈对冲 / 对角张力 / 棋盘翻牌 / 螺旋内外交替 /
两极磁吸 / 风车四叶 / 潮汐分界。

空间语义：设备队列 seq 序即空间序（global 空间模式下引擎已按全局矩阵坐标
重排 seq_i），本家族把 seq 序摊平成虚拟方阵(u,v)∈[-1,1]² 做 2D 几何游戏，
与既有效果的 seq_i 相位约定一致。

铁律遵守：
  - 确定性可复现：纯场函数 f(u,v,t,seed)，无 random。
  - 单通道通用：经 ch_type 作用于任意单通道，不覆盖整机 fill。
  - 每个效果只暴露其 compute 实际消费的参数。
"""
from __future__ import annotations

import copy
import math
from typing import Callable

from .effect_engine import EffectPreset, CH_TYPES
from .effect_presets_r8_common import (
    _clamp01, _smooth01, _snoise01, _tri01, _gauss,
    _speed_spec, _freq_spec, _seed_spec, _lo_spec, _hi_spec,
    _ch_type_spec, _width_spec,
)


def _grid_uv(seq_i: int, n: int):
    """seq 序 → 虚拟方阵坐标 (u,v)∈[-1,1]²。g=ceil(sqrt(n)) 的方阵铺排。"""
    g = max(1, int(math.ceil(math.sqrt(n))))
    row = seq_i // g
    col = seq_i % g
    u = (col / max(1, g - 1)) * 2.0 - 1.0
    v = (row / max(1, g - 1)) * 2.0 - 1.0
    return u, v


def _ang(rx: float, ry: float) -> float:
    return math.atan2(ry, rx) / (2.0 * math.pi)


def _fx_quadrant(ef, t, sorted_fx, fdefs, params, fps):
    """象限轮舞：四个象限各自绕本象限中心转轮，相邻象限反向——四轮各转各的、
    交界处相位相撞，观众能同时读到四组独立又互咬的节奏。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.7))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        u, v = _grid_uv(seq_i, n)
        qx = 0.5 if u >= 0.0 else -0.5
        qy = 0.5 if v >= 0.0 else -0.5
        parity = 1.0 if ((u >= 0.0) == (v >= 0.0)) else -1.0
        ph = _ang(u - qx, v - qy) + t * speed * parity
        w = 0.5 + 0.5 * math.cos(ph * math.pi * 2.0)
        w *= _gauss(max(abs(u) - 0.5, abs(v) - 0.5), 0.5)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_ioflow(ef, t, sorted_fx, fdefs, params, fps):
    """内外圈对冲：内圈与内圈反向旋转，环界带两股相位撕扯互消——对冲的边界
    带是表现力所在：两侧越转越快，边界越撕越暗。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.6))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        u, v = _grid_uv(seq_i, n)
        r = math.hypot(u, v) / 1.4142
        side = 1.0 if r > 0.5 else -1.0
        ph = _ang(u, v) + side * t * speed
        w = 0.5 + 0.5 * math.cos(ph * math.pi * 2.0)
        shear = _gauss(r - 0.5, 0.22)
        w *= 0.35 + 0.65 * (1.0 - shear)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_diag(ef, t, sorted_fx, fdefs, params, fps):
    """对角张力：两条对角轴交替拉扯亮度场，先主对角线后副对角线——对角线
    张力是方形阵列里最"斜"的视觉语言，交替轴让画面像被两只手轮流拧动。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.4))
    freq = float(params.get("spatial_freq", 3.0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    cyc = t * speed / 2.0
    blend = _tri01(cyc)
    axis_swap = 1.0 if int(cyc) % 2 == 0 else -1.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        u, v = _grid_uv(seq_i, n)
        field = (u - v) * (1.0 - blend) + (u + v) * blend
        field *= axis_swap
        w = 0.5 + 0.5 * math.sin(field * math.pi * freq)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_chess(ef, t, sorted_fx, fdefs, params, fps):
    """棋盘翻牌：棋盘格整行整行地翻牌——翻牌波前逐行推进，每翻一行该行黑白
    互换。翻转的"波前"(而非瞬切)是翻牌动画的仪式感来源。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.5))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    g = max(1, int(math.ceil(math.sqrt(n))))
    flip = t * speed
    cyc = int(flip)
    front = (flip % 1.0) * (g + 2)
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        row = seq_i // g
        col = seq_i % g
        cell = (row + col + cyc) % 2
        flipped = 1 if row < front else 0
        on = (cell + flipped) % 2
        edge = 1.0 - _smooth01(max(0.0, row - front + 1.0) / 1.5)
        w = 0.25 + 0.75 * on * (0.4 + 0.6 * edge)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_spiral(ef, t, sorted_fx, fdefs, params, fps):
    """螺旋内外交替：阿基米德螺线波沿半径方向传播，每周期方向反转——螺旋
    先向外绽开、再向内收拢，一呼一吸间全场随之开合。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.6))
    arms = float(params.get("spatial_freq", 3.0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    d_sign = 1.0 if int(t * speed) % 2 == 0 else -1.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        u, v = _grid_uv(seq_i, n)
        r = math.hypot(u, v) / 1.4142
        ph = _ang(u, v) + r * arms + d_sign * t * speed
        w = 0.5 + 0.5 * math.cos(ph * math.pi * 2.0)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_poles(ef, t, sorted_fx, fdefs, params, fps):
    """两极磁吸：两个磁极沿四角轮转，亮场被吸向当前正极、负极方向压暗——
    全场亮度像被磁铁轮流拽动，是"吸引与排斥"的空间叙事。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.5))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    corners = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
    k = int(t * speed) % 4
    px, py = corners[k]
    mx, my = corners[(k + 2) % 4]
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        u, v = _grid_uv(seq_i, n)
        d_plus = math.hypot(u - px, v - py) / 2.8284
        d_minus = math.hypot(u - mx, v - my) / 2.8284
        w = _gauss(d_plus, 0.55) - 0.35 * _gauss(d_minus, 0.55)
        w = max(0.0, w)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_windmill(ef, t, sorted_fx, fdefs, params, fps):
    """风车四叶：四片风叶绕中心缓旋，叶身随转速轻颤——叶片的"颤"把旋转的
    机械感变成风推叶片的呼吸感，四叶对称里藏着不对称的抖动。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.4))
    width = float(params.get("width", 0.1))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        u, v = _grid_uv(seq_i, n)
        a = _ang(u, v)
        w = 0.0
        for k in range(4):
            vane = t * speed + k * 0.25
            flutter = 0.03 * math.sin(t * speed * math.pi * 6.0 + k * 1.7)
            dv = (a - (vane + flutter) + 0.5) % 1.0 - 0.5
            w = max(w, _gauss(dv, width))
        w *= _clamp01(math.hypot(u, v) / 0.9)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_tide(ef, t, sorted_fx, fdefs, params, fps):
    """潮汐分界：明暗分界线沿四个方向轮流扫过全场，界线上水光细碎闪烁——
    潮线的缓慢推进(而非整体闪烁)是"涨潮退潮"的空间叙事。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 0.4))
    seed = int(params.get("seed", 0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    fields = ((1.0, 1.0), (1.0, -1.0), (-1.0, -1.0), (-1.0, 1.0))
    k = int(t * speed / 4.0) % 4
    fu, fv = fields[k]
    term = _tri01(t * speed / 4.0) * 2.0 - 1.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        u, v = _grid_uv(seq_i, n)
        side = (u * fu + v * fv) / 2.0 - term
        w = _smooth01(side / 0.35 + 0.5)
        edge = _gauss(side, 0.25)
        w += 0.18 * edge * (_snoise01(seq_i * 0.5 + t * 3.0, seed) - 0.5)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _register_r8_space(engine) -> int:
    """注册 R8 第二轮空间游戏家族 8 效果，返回注册数。由 effect_engine 注册链调用。"""
    reg = engine._reg
    count = 0

    def _add(pid, name, desc, icon, params, fn: Callable):
        nonlocal count
        reg(EffectPreset(
            id=pid, name=name, category="通用/高级", description=desc,
            target_channels=CH_TYPES, icon=icon,
            tags=["curated", "r8", "space"], params=copy.deepcopy(params),
        ), fn)
        count += 1

    _add("cur_r8x_quadrant", "象限轮舞",
         "四象限各绕本象限中心转轮，相邻反向，四组节奏独立又互咬。",
         "🩰", [_ch_type_spec(), _speed_spec(0.7), _lo_spec(), _hi_spec()], _fx_quadrant)
    _add("cur_r8x_ioflow", "内外圈对冲",
         "内圈外圈反向旋转，环界带相位撕扯互消，对冲边界越转越暗。",
         "🌀", [_ch_type_spec(), _speed_spec(0.6), _lo_spec(), _hi_spec()], _fx_ioflow)
    _add("cur_r8x_diag", "对角张力",
         "两条对角轴交替拉扯亮度场，像被两只手轮流拧动。",
         "⚡", [_ch_type_spec(), _speed_spec(0.4), _freq_spec(3.0), _lo_spec(), _hi_spec()], _fx_diag)
    _add("cur_r8x_chess", "棋盘翻牌",
         "棋盘格整行翻牌，翻牌波前逐行推进，黑白的仪式感切换。",
         "♟", [_ch_type_spec(), _speed_spec(0.5), _lo_spec(), _hi_spec()], _fx_chess)
    _add("cur_r8x_spiral", "螺旋内外交替",
         "阿基米德螺线波先向外绽开再向内收拢，一呼一吸全场开合。",
         "🐚", [_ch_type_spec(), _speed_spec(0.6), _freq_spec(3.0), _lo_spec(), _hi_spec()], _fx_spiral)
    _add("cur_r8x_poles", "两极磁吸",
         "两磁极沿四角轮转，亮场被轮流吸引压暗，吸引与排斥的空间叙事。",
         "🧲", [_ch_type_spec(), _speed_spec(0.5), _lo_spec(), _hi_spec()], _fx_poles)
    _add("cur_r8x_windmill", "风车四叶",
         "四叶绕心缓旋，叶身随转速轻颤，风推叶片的呼吸感。",
         "🎡", [_ch_type_spec(), _speed_spec(0.4), _width_spec(0.1), _lo_spec(), _hi_spec()], _fx_windmill)
    _add("cur_r8x_tide", "潮汐分界",
         "明暗分界线沿四方向轮流扫过，界线上水光细碎闪烁的涨退潮叙事。",
         "🌗", [_ch_type_spec(), _speed_spec(0.4), _seed_spec(), _lo_spec(), _hi_spec()], _fx_tide)
    return count
