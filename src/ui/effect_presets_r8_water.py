# -*- coding: utf-8 -*-
"""
effect_presets_r8_water — R8 第二轮·水形家族（2026-08-13）

8 个喷泉现场真实水柱形态效果（水景/泵，target_channels=["pump"]）。
取材自音乐喷泉实景水型：波尔卡跳跃 / 郁金香杯 / 扇形分层 / 水幕梳 /
踏浪(驰骋浪) / 碎浪(浪花搅动) / 水圈套水圈 / 皇冠喷头。

铁律遵守：
  - 确定性可复现：全部为纯场函数 f(pos, t, seed)，整数确定性 hash，无 random。
  - 设备集 scoped：只写 pump 通道，不覆盖整机 fill。
  - 每个效果只暴露其 compute 实际消费的参数（无装饰性旋钮）。
  - compute 签名与引擎一致：fn(ef, t, sorted_fx, fdefs, params, fps)。
"""
from __future__ import annotations

import copy
import math
from typing import Callable

from .effect_engine import EffectPreset
from .effect_presets_r8_common import (
    _hash01, _clamp01, _smooth01, _fbm01, _gauss,
    _speed_spec, _freq_spec, _seed_spec, _lo_spec, _hi_spec,
    _count_spec, _width_spec,
)

_PUMP = ["pump"]


def _fx_polka(ef, t, sorted_fx, fdefs, params, fps):
    """波尔卡跳跃：单点水柱沿链跳跃，跳跃弧线为抛物线——每个跳跃点从 0 起跳到
    最高点再落回 0，且每小节有一主跳+一轻跳(波尔卡双拍律动)，像一排水柱
    依次蹦跳。跳跃的高度轮廓才是表现力来源(非平移脉冲)。"""
    speed = float(params.get("speed", 0.8))
    width = float(params.get("width", 0.15))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    head = (t * speed * 3.0) % 1.0
    accent = (t * speed * 3.0 + 0.5) % 1.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = 0.0
        d1 = abs(p - head)
        if d1 < width:
            w = max(w, 1.0 - ((d1 / width) * 2.0 - 1.0) ** 2)
        d2 = abs(p - accent)
        if d2 < width * 0.6:
            w = max(w, 0.5 * (1.0 - ((d2 / (width * 0.6)) * 2.0 - 1.0) ** 2))
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


def _fx_tulip(ef, t, sorted_fx, fdefs, params, fps):
    """郁金香杯：喷泉先立细茎、茎顶绽放成杯、杯形定格后整体收束。杯口是两条
    对称外扩的水翼——收束与绽放的不对称节奏(升慢收快)才是郁金香的辨识度。"""
    speed = float(params.get("speed", 0.4))
    width = float(params.get("width", 0.45))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    ph = (t * speed) % 1.0
    stem = _smooth01(ph / 0.40) if ph < 0.45 else 1.0
    bloom = _smooth01((ph - 0.35) / 0.30) if ph < 0.75 else 1.0
    collapse = _smooth01((ph - 0.80) / 0.20) if ph >= 0.80 else 1.0
    hold = 1.0 - collapse
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        col = _gauss(p - 0.5, 0.06) * stem
        rim_l = 0.5 - width * bloom
        rim_r = 0.5 + width * bloom
        rim = _gauss(p - rim_l, 0.05) + _gauss(p - rim_r, 0.05)
        w = (col + 0.85 * rim * bloom) * hold
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


def _fx_fanlayers(ef, t, sorted_fx, fdefs, params, fps):
    """扇形分层：三把不同速度(1x/2x/3x)与不同宽窄的扇子同时扫过——近层宽缓、
    远层窄疾，叠加出纵深分层的扇形。层次差速是表现力来源(单把扇子没有纵深)。"""
    speed = float(params.get("speed", 0.5))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    layers = ((1.0, 0.14, 1.0), (2.0, 0.09, 0.7), (3.0, 0.05, 0.5))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = 0.0
        for mult, sigma, amp in layers:
            head = (t * speed * mult) % 1.0
            w += amp * _gauss(p - head, sigma)
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


def _fx_curtain(ef, t, sorted_fx, fdefs, params, fps):
    """水幕梳：整幅水幕均匀而立，梳齿间隙在幕上匀速滑动——满幕与缺口的对比
    让"整面水"有了可以被读出的纹理，是喷泉水幕类的基础形态。"""
    speed = float(params.get("speed", 0.3))
    count = int(params.get("count", 6))
    width = float(params.get("width", 0.06))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    drift = (t * speed) % 1.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        tooth = ((p * count + drift) % 1.0)
        gap = 1.0 if tooth < width else 0.0
        sway = 1.0 - 0.08 * math.sin(p * math.pi * 2.0 + t * speed * 2.0)
        ef.values[orig_i]["pump"] = int(lo + _clamp01(gap * sway) * (hi - lo))


def _fx_gallop(ef, t, sorted_fx, fdefs, params, fps):
    """踏浪(驰骋浪)：浪头在前进中被空间压缩波推挤——浪峰忽聚忽散、时密时疏，
    如同马群踏浪而来。浪形为快起慢落的不对称锯齿，聚散是表现力来源。"""
    speed = float(params.get("speed", 0.6))
    freq = float(params.get("spatial_freq", 3.0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        squeezed = p + 0.12 * math.sin(p * math.pi * 2.0 * 2.0 - t * speed * 2.0)
        u = (squeezed * freq + t * speed) % 1.0
        w = u / 0.30 if u < 0.30 else 1.0 - (u - 0.30) / 0.70
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


def _fx_chop(ef, t, sorted_fx, fdefs, params, fps):
    """碎浪(浪花搅动)：短波长分形噪声场被锐化——浪尖细碎明亮、浪谷深暗，
    是风把水面撕碎后的扰动质感。锐化(非平滑)是其区别于普通噪声流的辨识度。"""
    speed = float(params.get("speed", 1.2))
    freq = float(params.get("spatial_freq", 6.0))
    seed = int(params.get("seed", 0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        v = _fbm01(p * freq + t * speed, seed, 5)
        w = v * v * (1.5 - 0.5 * v)
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


def _fx_rings(ef, t, sorted_fx, fdefs, params, fps):
    """水圈套水圈：多个点源同时向外扩出圆环，环与环交错互套——套环嵌套是
    水面最本能的"生命感"纹理(单环只是涟漪，多源互套才是表演)。"""
    speed = float(params.get("speed", 0.7))
    count = int(params.get("count", 3))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = 0.0
        for k in range(max(1, count)):
            src = 0.5 if count == 1 else k / (count - 1)
            r = ((t * speed + k / max(1, count)) % 1.0) * 0.55
            w += _gauss(abs(p - src) - r, 0.06)
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


def _fx_crown(ef, t, sorted_fx, fdefs, params, fps):
    """皇冠喷头：中央主水柱托起一圈匀速旋转的细水齿——齿圈旋转的错觉让整排水
    柱像一顶缓缓转动的皇冠。柱与齿的强弱对比(柱稳齿旋)是皇冠形态的辨识度。"""
    speed = float(params.get("speed", 0.5))
    count = int(params.get("count", 7))
    width = float(params.get("width", 0.3))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        col = _gauss(p - 0.5, 0.05) * 0.9
        tooth = 0.0
        for k in range(max(1, count)):
            tp = 0.5 - width / 2.0 + (((k + t * speed) % 1.0) / count) * width
            tooth = max(tooth, _gauss(p - tp, 0.015))
        w = col + 0.75 * tooth
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


def _register_r8_water(engine) -> int:
    """注册 R8 第二轮水形家族 8 效果，返回注册数。由 effect_engine 注册链调用。"""
    reg = engine._reg
    count = 0

    def _add(pid, name, desc, icon, params, fn: Callable):
        nonlocal count
        reg(EffectPreset(
            id=pid, name=name, category="水景/泵", description=desc,
            target_channels=_PUMP, icon=icon,
            tags=["curated", "r8", "water"], params=copy.deepcopy(params),
        ), fn)
        count += 1

    _add("cur_r8w_polka", "波尔卡跳跃",
         "单点水柱沿链依次蹦跳，主跳+轻跳双拍律动，抛物线跳跃轮廓。",
         "🦘", [_speed_spec(0.8), _width_spec(0.15), _lo_spec(), _hi_spec()], _fx_polka)
    _add("cur_r8w_tulip", "郁金香杯",
         "细茎升立、茎顶绽放成杯、杯形定格后收束，升慢收快的不对称节奏。",
         "🌷", [_speed_spec(0.4), _width_spec(0.45), _lo_spec(), _hi_spec()], _fx_tulip)
    _add("cur_r8w_fanlayers", "扇形分层",
         "三把不同速度与宽窄的扇子同时扫过，近宽缓远窄疾，叠出纵深分层扇形。",
         "🪭", [_speed_spec(0.5), _lo_spec(), _hi_spec()], _fx_fanlayers)
    _add("cur_r8w_curtain", "水幕梳",
         "整幅水幕均匀而立，梳齿间隙在幕上滑动，满幕与缺口对比成纹理。",
         "🚿", [_speed_spec(0.3), _count_spec(6), _width_spec(0.06), _lo_spec(), _hi_spec()], _fx_curtain)
    _add("cur_r8w_gallop", "踏浪",
         "浪头被空间压缩波推挤，峰聚峰散时密时疏，快起慢落如马群踏浪。",
         "🐎", [_speed_spec(0.6), _freq_spec(3.0), _lo_spec(), _hi_spec()], _fx_gallop)
    _add("cur_r8w_chop", "碎浪",
         "短波长分形噪声锐化，浪尖细碎明亮浪谷深暗，风撕水面的扰动质感。",
         "🌊", [_speed_spec(1.2), _freq_spec(6.0), _seed_spec(), _lo_spec(), _hi_spec()], _fx_chop)
    _add("cur_r8w_rings", "水圈套水圈",
         "多点源同时向外扩圆环，环环交错互套，套环嵌套的水面生命感。",
         "⭕", [_speed_spec(0.7), _count_spec(3), _lo_spec(), _hi_spec()], _fx_rings)
    _add("cur_r8w_crown", "皇冠喷头",
         "中央主水柱托起匀速旋转的细水齿圈，柱稳齿旋如缓缓转动的皇冠。",
         "👑", [_speed_spec(0.5), _count_spec(7), _width_spec(0.3), _lo_spec(), _hi_spec()], _fx_crown)
    return count
