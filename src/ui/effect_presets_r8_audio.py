# -*- coding: utf-8 -*-
"""
effect_presets_r8_audio — R8 第二轮·音乐联动家族（2026-08-13）

8 个音乐联动效果（音频反应）。音频数据经引擎注入 params["_audio_data"]
(rms/low/mid/high/is_beat/onset_strength/bpm/beat_phase)，与既有
_audio_light_kernel 的 12 个 kind(beat_sync/beat_chase/...)机制互不重复。

★ R173 音频精编修正：本族曾在无音频时用确定性合成场(固定 120 BPM 拍格 + 慢呼吸
能量)冒充音乐律动——这正是用户明确批评过的"以音频为题材的假效果"（见
effect_presets_audio_true.py 顶部 2026-09-21 用户需求引述）。现已改为与 atx_* 真
音频族一致的硬契约：**无音频 → 全组静止在中性/待机值（dimmer/pump 类=lo，电机类=
center），不伪造任何拍点/能量动画**；有音频时逐帧跟随真实能量。

铁律遵守：
  - 确定性可复现：闪光用 hash，无 random。
  - 无音频不出效果：与真音频族口径一致，见各 `_fx_*` 顶部的 `if not a["has"]` 闸门。
  - 设备集 scoped：标量类经 ch_type 单通道；泵/电机类精确声明通道。
  - 每个效果只暴露其 compute 实际消费的参数。
"""
from __future__ import annotations

import copy
import math
from typing import Callable

from .effect_engine import EffectPreset, ParamSpec, CH_TYPES
from .effect_presets_r8_common import (
    _hash01, _clamp01, _snoise01, _fbm01, _gauss, _tri01,
    _speed_spec, _seed_spec, _lo_spec, _hi_spec, _ch_type_spec,
    _decay_spec, _width_spec, _angle_spec, _center_spec,
    _motor_write,
)

_PUMP = ["pump"]
_MXY = ["motor_x", "motor_y"]


def _sens_spec(sens: float) -> ParamSpec:
    return ParamSpec("sensitivity", "灵敏度", "float", sens, 0.05, 1.0, 0.05,
                     tooltip="控制对起音/能量触发的敏感度。值越小越容易被触发")


def _audio_read(params, t: float):
    """读实时音频数据；无音频时返回中性静止场（`has=False`，全零/零拍，供各
    `_fx_*` 顶部的静音闸门判定——不再伪造合成拍点/能量，见模块顶部 R173 说明）。"""
    audio = params.get("_audio_data") or {}
    has = bool(audio.get("has_audio")) if audio else False
    bpm = float(audio.get("bpm", 120.0)) if has else 120.0
    beat_period = 60.0 / max(1.0, bpm)
    if has:
        return {
            "has": True,
            "rms": float(audio.get("rms", 0.0)),
            "low": float(audio.get("low", 0.0)),
            "mid": float(audio.get("mid", 0.0)),
            "high": float(audio.get("high", 0.0)),
            "is_beat": bool(audio.get("is_beat", False)),
            "onset": float(audio.get("onset_strength", 0.0)),
            "beat_period": beat_period,
            "beat_phase": float(audio.get("beat_phase", (t / beat_period) % 1.0)),
            "beat_count": int(t / beat_period),
        }
    return {
        "has": False,
        "rms": 0.0,
        "low": 0.0,
        "mid": 0.0,
        "high": 0.0,
        "is_beat": False,
        "onset": 0.0,
        "beat_period": beat_period,
        "beat_phase": 0.0,
        "beat_count": 0,
    }


def _fx_pulsecluster(ef, t, sorted_fx, fdefs, params, fps):
    """节拍脉冲群：每拍在链上一处爆发一团亮斑，团心随拍数做确定性随机游走，
    拍内指数衰减——不是逐台走灯，而是一团团"心跳"在链上游牧。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 1.0))
    width = float(params.get("width", 0.12))
    seed = int(params.get("seed", 0))
    decay = float(params.get("decay", 0.4))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    a = _audio_read(params, t)
    if not a["has"]:
        for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            ef.values[orig_i][ch] = lo
        return
    bp = a["beat_phase"]
    center = 0.5 + 0.4 * math.sin(math.pi * 2.0 * _fbm01(a["beat_count"] * 0.13 + seed * 0.001, seed))
    base = math.exp(-bp / max(0.01, decay))
    amp = 0.55 + 0.45 * a["rms"] * speed
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = amp * _gauss(p - center, width) * base
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_afterglow(ef, t, sorted_fx, fdefs, params, fps):
    """起音余辉：起音瞬间全链闪亮、每台设备按自身确定性 hash 的微差错峰闪耀，
    随后是 RMS 驱动的长余辉衰减——瞬闪的"锐"与余辉的"绵"同时在场。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 1.0))
    decay = float(params.get("decay", 0.5))
    sens = float(params.get("sensitivity", 0.3))
    seed = int(params.get("seed", 0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    a = _audio_read(params, t)
    if not a["has"]:
        for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            ef.values[orig_i][ch] = lo
        return
    onset = a["onset"]
    flash_band = int(onset / max(0.02, sens)) if onset > sens else -1
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        if flash_band >= 0:
            sparkle = 0.6 + 0.4 * _hash01(seq_i * 31 + flash_band, seed)
            w = onset * sparkle + 0.5 * a["rms"] * math.exp(-a["beat_phase"] / max(0.01, decay))
        else:
            w = 0.22 + 0.16 * _snoise01(p * 3.0 + t * speed * 0.5, seed)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_bandmap(ef, t, sorted_fx, fdefs, params, fps):
    """频谱分带：链分三段各自镜像低频/中频/高频能量——低频段起伏沉重、高频段
    轻快，全场亮度的空间分布就是音乐的频谱形状。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 1.0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    a = _audio_read(params, t)
    if not a["has"]:
        for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            ef.values[orig_i][ch] = lo
        return
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        if p < 1.0 / 3.0:
            w = a["low"]
        elif p < 2.0 / 3.0:
            w = a["mid"]
        else:
            w = a["high"]
        w *= 0.6 + 0.4 * speed
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_metronome(ef, t, sorted_fx, fdefs, params, fps):
    """节拍折返：整组电机像节拍器指针一样往返摆动，每拍折返一次，拍点处摆幅
    随衰减放大——机械摆动与音乐节拍硬同步，是最直觉的"看得见的节拍器"。"""
    speed = float(params.get("speed", 1.0))
    angle = float(params.get("angle", 1.0))
    center = int(params.get("center", 128))
    decay = float(params.get("decay", 0.35))
    n = max(1, len(sorted_fx))
    a = _audio_read(params, t)
    if not a["has"]:
        for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            _motor_write(ef, orig_i, 0.0, 0.0, center, 0.0)
        return
    sweep = _tri01(a["beat_phase"]) * 2.0 - 1.0
    rad = 255.0 * angle * (0.7 + 0.3 * math.exp(-a["beat_phase"] / max(0.01, decay)))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        _motor_write(ef, orig_i, sweep, 0.0, center, rad)


def _fx_bassdrive(ef, t, sorted_fx, fdefs, params, fps):
    """低频驱动波：泵链的行进波速度由低频能量实时驱动——低频越猛波浪越急，
    低音是这台"水引擎"的油门。"""
    speed = float(params.get("speed", 1.0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    a = _audio_read(params, t)
    if not a["has"]:
        for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            ef.values[orig_i]["pump"] = lo
        return
    v_speed = 0.3 + a["low"] * 2.2 * speed
    base = 0.15 + a["low"] * 0.5
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = base + 0.7 * (0.5 + 0.5 * math.sin((p * 2.0 + t * v_speed) * math.pi * 2.0))
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


def _fx_breath(ef, t, sorted_fx, fdefs, params, fps):
    """呼吸节律：全场随拍格同呼吸——吸气半拍渐起、呼气半拍渐落，呼吸幅度
    跟随 RMS，链上带一丝空间波纹。没有炸点，只有安静的整体律动。"""
    ch = params.get("ch_type", "dimmer")
    speed = float(params.get("speed", 1.0))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    a = _audio_read(params, t)
    if not a["has"]:
        for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            ef.values[orig_i][ch] = lo
        return
    rms_eff = max(0.25, a["rms"]) * speed
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        ph = (a["beat_phase"] + p * 0.15) % 1.0
        w = (0.5 - 0.5 * math.cos(ph * math.pi * 2.0)) * rms_eff
        if ph < 0.5:
            w += a["low"] * 0.25 * (0.5 - 0.5 * math.cos(ph * math.pi * 2.0))
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * (hi - lo))


def _fx_onsetripple(ef, t, sorted_fx, fdefs, params, fps):
    """音落涟漪：像石子砸进水里——拍点起在链中央激出一圈波纹向外扩散，
    落点越猛(起音越强)涟漪越亮，扩散速度与拍长同步。"""
    speed = float(params.get("speed", 1.0))
    decay = float(params.get("decay", 0.5))
    lo = int(params.get("lo", 0))
    hi = int(params.get("hi", 255))
    n = max(1, len(sorted_fx))
    a = _audio_read(params, t)
    if not a["has"]:
        for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            ef.values[orig_i]["pump"] = lo
        return
    r = a["beat_phase"]
    amp = (0.35 + 0.65 * a["onset"]) * speed
    amp *= math.exp(-r / max(0.01, decay))
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        p = seq_i / max(1, n - 1)
        w = amp * _gauss(abs(p - 0.5) - r * 0.55, 0.06)
        ef.values[orig_i]["pump"] = int(lo + _clamp01(w) * (hi - lo))


def _fx_spectilt(ef, t, sorted_fx, fdefs, params, fps):
    """频谱仰角：全场电机随频谱质心仰头低头——低音厚重时向下、高频明亮时向上，
    群体会"听"着频谱做统一的俯仰动作。"""
    speed = float(params.get("speed", 1.0))
    angle = float(params.get("angle", 1.0))
    center = int(params.get("center", 128))
    n = max(1, len(sorted_fx))
    a = _audio_read(params, t)
    if not a["has"]:
        for _seq_i, (orig_i, _fi) in enumerate(sorted_fx):
            _motor_write(ef, orig_i, 0.0, 0.0, center, 0.0)
        return
    denom = a["low"] + a["mid"] + a["high"]
    centroid = (a["low"] * 0.15 + a["mid"] * 0.5 + a["high"] * 0.85) / denom if denom > 1e-6 else 0.5
    tilt = max(-1.0, min(1.0, (centroid - 0.5) * 2.0))
    rad = 255.0 * angle
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        wobble = math.sin(t * speed * math.pi * 0.6) * 0.12
        _motor_write(ef, orig_i, tilt * 0.85, wobble, center, rad)


def _register_r8_audio(engine) -> int:
    """注册 R8 第二轮音乐联动家族 8 效果，返回注册数。由 effect_engine 注册链调用。"""
    reg = engine._reg
    count = 0

    def _add(pid, name, desc, icon, channels, params, fn: Callable):
        nonlocal count
        reg(EffectPreset(
            id=pid, name=name, category="音频反应", description=desc,
            target_channels=channels, icon=icon,
            tags=["curated", "r8", "audio"], params=copy.deepcopy(params),
        ), fn)
        count += 1

    _add("cur_r8a_pulsecluster", "节拍脉冲群",
         "每拍一处亮斑爆发，团心随拍数确定性游走，团簇心跳在链上游牧。",
         "💥", CH_TYPES, [_ch_type_spec(), _speed_spec(1.0), _width_spec(0.12), _seed_spec(), _decay_spec(0.4), _lo_spec(), _hi_spec()], _fx_pulsecluster)
    _add("cur_r8a_afterglow", "起音余辉",
         "起音瞬间全链错峰闪亮(hash 微差)，随后 RMS 长余辉衰减，锐与绵并存。",
         "💫", CH_TYPES, [_ch_type_spec(), _speed_spec(1.0), _decay_spec(0.5), _sens_spec(0.3), _seed_spec(), _lo_spec(), _hi_spec()], _fx_afterglow)
    _add("cur_r8a_bandmap", "频谱分带",
         "链三段镜像低/中/高频能量，全场亮度分布就是频谱形状。",
         "📊", CH_TYPES, [_ch_type_spec(), _speed_spec(1.0), _lo_spec(), _hi_spec()], _fx_bandmap)
    _add("cur_r8a_metronome", "节拍折返",
         "电机如节拍器指针每拍折返，拍点摆幅衰减放大，看得见的节拍器。",
         "🪞", _MXY, [_speed_spec(1.0), _angle_spec(), _center_spec(), _decay_spec(0.35)], _fx_metronome)
    _add("cur_r8a_bassdrive", "低频驱动波",
         "行进波速度由低频能量实时驱动，低音是水引擎的油门。",
         "🐋", _PUMP, [_speed_spec(1.0), _lo_spec(), _hi_spec()], _fx_bassdrive)
    _add("cur_r8a_breath", "呼吸节律",
         "全场随拍格同呼吸，吸气渐起呼气渐落，幅度跟随 RMS 的安静律动。",
         "🫁", CH_TYPES, [_ch_type_spec(), _speed_spec(1.0), _lo_spec(), _hi_spec()], _fx_breath)
    _add("cur_r8a_onsetripple", "音落涟漪",
         "拍点链中央激出波纹外扩，起音越猛涟漪越亮，石子砸进水里。",
         "🪨", _PUMP, [_speed_spec(1.0), _decay_spec(0.5), _lo_spec(), _hi_spec()], _fx_onsetripple)
    _add("cur_r8a_spectilt", "频谱仰角",
         "电机随频谱质心俯仰，低音厚重向下高频明亮向上，群体听声俯仰。",
         "🎻", _MXY, [_speed_spec(1.0), _angle_spec(), _center_spec()], _fx_spectilt)
    return count
