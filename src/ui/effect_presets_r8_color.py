# -*- coding: utf-8 -*-
"""
effect_presets_r8_color — R8 第二轮·颜色旅程家族（2026-08-13）

8 个颜色旅程效果（灯光/颜色，target_channels=["R","G","B"]）。
色相环巡游 / 双色碰撞融合 / 温度渐变带 / 分频带色彩 / 极光色带 /
油膜干涉 / 双色串扰 / 色温呼吸。

铁律遵守：
  - 确定性可复现：纯场函数，hash/fbm 确定性，无 random。
  - 设备集 scoped：只写 R/G/B 三通道，不覆盖整机 fill。
  - 每个效果只暴露其 compute 实际消费的参数。
  - 与既有 _color_kernel 各 kind(rainbow/hue_rot/colortemp/...)机制互不重复。
"""
from __future__ import annotations

import copy
import math
from typing import Callable

from .effect_engine import EffectPreset, ParamSpec
from .effect_presets_curated import _hsv_to_rgb, _kelvin_rgb
from .effect_presets_r8_common import (
    _clamp01, _smooth01, _fbm01, _gauss,
    _speed_spec, _freq_spec, _seed_spec, _width_spec,
)

_RGB = ["R", "G", "B"]


def _sat_spec(sat: float) -> ParamSpec:
    return ParamSpec("saturation", "饱和度", "float", sat, 0.0, 1.0, 0.02,
                     tooltip="控制颜色饱和度。0=纯白灰阶，1=最浓郁纯色")


def _bright_spec(bright: float) -> ParamSpec:
    return ParamSpec("brightness", "亮度", "float", bright, 0.0, 1.0, 0.02,
                     tooltip="控制颜色整体亮度。0=全灭，1=最亮")


def _hue_spec(key: str, hue: float) -> ParamSpec:
    return ParamSpec(key, "色相", "float", hue, 0.0, 1.0, 0.01,
                     tooltip="控制基准色相(0=红 0.33=绿 0.66=蓝)。1 圈=色相环一整周")


def _k_spec(k_lo: float) -> ParamSpec:
    return ParamSpec("k_lo", "低色温", "float", k_lo, 1000.0, 12000.0, 100.0,
                     tooltip="控制色温下限(开尔文)。值越低越暖(烛光≈2200K)，越高越冷")


def _write_rgb(ef, orig_i: int, r: float, g: float, b: float, bright: float) -> None:
    k = _clamp01(bright)
    ef.values[orig_i]["R"] = int(max(0, min(255, r * k)))
    ef.values[orig_i]["G"] = int(max(0, min(255, g * k)))
    ef.values[orig_i]["B"] = int(max(0, min(255, b * k)))


def _fx_huetour(ef, t, sorted_fx, fdefs, params, fps):
    """色相环巡游：色相沿色环分段行进，每段在基准色上滞留(平滑停顿)再跳向下一
    色——"巡游"的驻留感(而非匀速旋转)让六大主色依次登场谢幕，是有叙事感的旅程。"""
    speed = float(params.get("speed", 0.25))
    freq = float(params.get("spatial_freq", 0.6))
    sat = float(params.get("saturation", 1.0))
    bright = float(params.get("brightness", 1.0))
    n = max(1, len(sorted_fx))
    seg = 6.0
    tour = (t * speed) % 1.0
    seg_i = int(tour * seg)
    local = tour * seg - seg_i
    h_base = (seg_i + _smooth01(local)) / seg
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        h = (h_base + p * freq / seg * 0.5) % 1.0
        v = 0.78 + 0.22 * math.sin(p * math.pi * 2.0 + t * speed * 4.0)
        r, g, b = _hsv_to_rgb(h, sat, v)
        _write_rgb(ef, orig_i, r, g, b, bright)


def _fx_collision(ef, t, sorted_fx, fdefs, params, fps):
    """双色碰撞融合：左端色域与右端色域各自推进、相遇，交界带两色交融成第三
    色——碰撞融合(而非静态双色渐变)把"两种颜色相遇"的过程本身演出来。"""
    speed = float(params.get("speed", 0.4))
    hue_a = float(params.get("hue_a", 0.0))
    hue_b = float(params.get("hue_b", 0.55))
    sat = float(params.get("saturation", 1.0))
    bright = float(params.get("brightness", 1.0))
    n = max(1, len(sorted_fx))
    ph = (t * speed) % 1.0
    advance = _smooth01(ph / 0.45) if ph < 0.5 else _smooth01((1.0 - ph) / 0.5)
    fa = 0.5 - 0.46 * advance
    fb = 0.5 + 0.46 * advance
    clash = _gauss(ph - 0.5, 0.06)
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        if p <= fa:
            h = hue_a
        elif p >= fb:
            h = hue_b
        else:
            m = (p - fa) / max(1e-6, fb - fa)
            h = (hue_a * (1.0 - m) + hue_b * m) % 1.0
        v = 0.75 + 0.25 * clash
        r, g, b = _hsv_to_rgb(h, sat, v)
        _write_rgb(ef, orig_i, r, g, b, bright)


def _fx_tempband(ef, t, sorted_fx, fdefs, params, fps):
    """温度渐变带：一列暖色光带(黑体 3200K)贴着冷底(9000K)巡游，带边软过渡——
    局部暖带+全局冷底的黑体温度对比，是"热源移动"的直觉表达。"""
    speed = float(params.get("speed", 0.5))
    width = float(params.get("width", 0.12))
    bright = float(params.get("brightness", 1.0))
    n = max(1, len(sorted_fx))
    band = (t * speed) % 1.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = _gauss(p - band, width)
        k = 9000.0 - 5800.0 * w
        r, g, b = _kelvin_rgb(k)
        _write_rgb(ef, orig_i, r, g, b, bright)


def _fx_spectral(ef, t, sorted_fx, fdefs, params, fps):
    """分频带色彩：链分三段分别映射低频(红)/中频(绿)/高频(蓝)，亮度跟随对应
    频段能量——音乐的能量结构被直接画成空间色谱。无音频时三段按不同速率呼吸，
    保证离线预览有形态。"""
    speed = float(params.get("speed", 0.5))
    sat = float(params.get("saturation", 0.9))
    bright = float(params.get("brightness", 1.0))
    n = max(1, len(sorted_fx))
    audio = params.get("_audio_data") or {}
    has_audio = bool(audio.get("has_audio")) if audio else False
    low = float(audio.get("low", 0.0)) if has_audio else 0.0
    mid = float(audio.get("mid", 0.0)) if has_audio else 0.0
    high = float(audio.get("high", 0.0)) if has_audio else 0.0
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        if has_audio:
            band = low if p < 1.0 / 3.0 else (mid if p < 2.0 / 3.0 else high)
            hue = 0.0 if p < 1.0 / 3.0 else (0.33 if p < 2.0 / 3.0 else 0.66)
            v = _clamp01(band)
        else:
            ph = t * speed + p * 2.0
            band = 0.5 + 0.5 * math.sin(ph * math.pi * 2.0)
            hue = (0.0 if p < 1.0 / 3.0 else (0.33 if p < 2.0 / 3.0 else 0.66))
            v = 0.25 + 0.45 * band
        r, g, b = _hsv_to_rgb(hue, sat, v)
        _write_rgb(ef, orig_i, r, g, b, bright)


def _fx_aurora(ef, t, sorted_fx, fdefs, params, fps):
    """极光色带：多条色带被噪声流扰动，飘移中摇曳卷曲——极光幕布的不规则
    摆动(噪声驱动,非匀速平移)是它与普通渐变带的区别。"""
    speed = float(params.get("speed", 0.3))
    freq = float(params.get("spatial_freq", 3.0))
    seed = int(params.get("seed", 0))
    bright = float(params.get("brightness", 1.0))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        sway = _fbm01(p * 2.0 + t * speed * 0.7, seed, 3)
        band = 0.0
        for k in range(3):
            bp = (p - ((t * speed + k * 0.33) % 1.0) + (sway - 0.5) * 0.5 + 0.5) % 1.0 - 0.5
            band = max(band, _gauss(bp, 0.09))
        hue = (0.33 + 0.28 * sway) % 1.0
        r, g, b = _hsv_to_rgb(hue, 0.85, _clamp01(0.2 + band))
        _write_rgb(ef, orig_i, r, g, b, bright)


def _fx_oilfilm(ef, t, sorted_fx, fdefs, params, fps):
    """油膜干涉：色相直接取自分形噪声场的时空截面——高饱和虹彩随流漂移，
    相邻设备色相连续游走，是水面油膜的干涉虹彩质感。"""
    speed = float(params.get("speed", 0.5))
    freq = float(params.get("spatial_freq", 3.0))
    seed = int(params.get("seed", 0))
    bright = float(params.get("brightness", 1.0))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        hue = _fbm01(p * freq + t * speed * 0.4, seed, 6)
        v = 0.6 + 0.4 * _fbm01(p * freq * 2.0 - t * speed * 0.3, seed + 7, 4)
        r, g, b = _hsv_to_rgb(hue, 0.9, v)
        _write_rgb(ef, orig_i, r, g, b, bright)


def _fx_moire(ef, t, sorted_fx, fdefs, params, fps):
    """双色串扰：两条色相波以不可公约速度行进，叠加产生拍频干涉——波节处
    色相与亮度同时发亮、波腹处互消，是"两色打架"的莫尔条纹美学。"""
    speed = float(params.get("speed", 0.6))
    freq = float(params.get("spatial_freq", 2.0))
    bright = float(params.get("brightness", 1.0))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        h1 = (p * freq + t * speed) % 1.0
        h2 = (p * freq * 1.4142 + t * speed * 1.4142) % 1.0
        beat = 0.5 - 0.5 * math.cos((h1 - h2) * math.pi * 2.0)
        hue = (h1 + h2) % 1.0
        r, g, b = _hsv_to_rgb(hue, 0.85, 0.35 + 0.65 * beat)
        _write_rgb(ef, orig_i, r, g, b, bright)


def _fx_tempbreathe(ef, t, sorted_fx, fdefs, params, fps):
    """色温呼吸：整链色温按不对称呼吸包络升降(吸慢呼快+呼末轻颤)，且呼吸波沿
    链传播——每一台设备依次"吸气变暖、呼气变冷"，暖浪扫过全场。"""
    speed = float(params.get("speed", 0.5))
    k_lo = float(params.get("k_lo", 2200.0))
    k_hi = float(params.get("k_hi", 8200.0))
    bright = float(params.get("brightness", 1.0))
    n = max(1, len(sorted_fx))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        ph = (t * speed + p * 0.6) % 1.0
        if ph < 0.4:
            w = _smooth01(ph / 0.4)
        else:
            w = 1.0 - _smooth01((ph - 0.4) / 0.55)
            w *= 1.0 + 0.04 * math.sin((ph - 0.4) * math.pi * 8.0)
        k = k_lo + (k_hi - k_lo) * w
        r, g, b = _kelvin_rgb(k)
        _write_rgb(ef, orig_i, r, g, b, bright)


def _register_r8_color(engine) -> int:
    """注册 R8 第二轮颜色旅程家族 8 效果，返回注册数。由 effect_engine 注册链调用。"""
    reg = engine._reg
    count = 0

    def _add(pid, name, desc, icon, params, fn: Callable):
        nonlocal count
        reg(EffectPreset(
            id=pid, name=name, category="灯光/颜色", description=desc,
            target_channels=_RGB, icon=icon,
            tags=["curated", "r8", "color"], params=copy.deepcopy(params),
        ), fn)
        count += 1

    _add("cur_r8c_huetour", "色相环巡游",
         "色相分段巡游，每段在基准色滞留再行进，六大主色依次登场谢幕。",
         "🎠", [_speed_spec(0.25), _freq_spec(0.6), _sat_spec(1.0), _bright_spec(1.0)], _fx_huetour)
    _add("cur_r8c_collision", "双色碰撞融合",
         "两端色域各自推进相遇，交界带交融成第三色，把相遇过程演出来。",
         "🎨", [_speed_spec(0.4), _hue_spec("hue_a", 0.0), _hue_spec("hue_b", 0.55), _sat_spec(1.0), _bright_spec(1.0)], _fx_collision)
    _add("cur_r8c_tempband", "温度渐变带",
         "暖色黑体光带贴冷底巡游，带边软过渡，热源移动的直觉表达。",
         "🌡", [_speed_spec(0.5), _width_spec(0.12), _bright_spec(1.0)], _fx_tempband)
    _add("cur_r8c_spectral", "分频带色彩",
         "链三段映射低/中/高频为红/绿/蓝，亮度随频段能量，能量结构画成色谱。",
         "🎚", [_speed_spec(0.5), _sat_spec(0.9), _bright_spec(1.0)], _fx_spectral)
    _add("cur_r8c_aurora", "极光色带",
         "多条色带被噪声流扰动，飘移中摇曳卷曲，极光幕布的不规则摆动。",
         "🌌", [_speed_spec(0.3), _freq_spec(3.0), _seed_spec(), _bright_spec(1.0)], _fx_aurora)
    _add("cur_r8c_oilfilm", "油膜干涉",
         "色相取自分形噪声时空截面，高饱和虹彩随流漂移的油膜质感。",
         "🫧", [_speed_spec(0.5), _freq_spec(3.0), _seed_spec(), _bright_spec(1.0)], _fx_oilfilm)
    _add("cur_r8c_moire", "双色串扰",
         "两条色相波不可公约速度叠加，拍频干涉产生波节发亮波腹互消。",
         "🪢", [_speed_spec(0.6), _freq_spec(2.0), _bright_spec(1.0)], _fx_moire)
    _add("cur_r8c_tempbreathe", "色温呼吸",
         "色温按不对称呼吸包络升降，呼吸波沿链传播，暖浪依次扫过全场。",
         "🌬", [_speed_spec(0.5), _k_spec(2200.0), _bright_spec(1.0)], _fx_tempbreathe)
    return count
