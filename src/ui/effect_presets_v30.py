"""
effect_presets_v30.py — 灯光效果大扩充 v30
=============================================
新增约60个灯光效果变式，包含：
  A. 跑灯高级变式（锯齿/阶梯/弹跳/反弹/加速/减速）
  B. 呼吸高级变式（双频/三频/不对称/随机/脉冲群）
  C. 闪烁高级变式（随机/分组/交替/节奏/频闪序列）
  D. 亮度渐变高级变式（彩虹/渐变序列/多段/波形亮度）
  E. 矩阵高级变式（更多矩阵灯光效果）
  F. 颜色高级变式（颜色序列/渐变/跳变）
"""
from __future__ import annotations
import math
import random
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .effect_engine import EffectEngine, EffectPreset, EffectFrame, ParamSpec


def _register_v30_presets(engine: "EffectEngine"):
    """注册 v30 灯光效果大扩充预设"""
    from .effect_engine import EffectPreset, ParamSpec
    reg = engine._reg

    def _clamp(v, lo=0, hi=255):
        return max(lo, min(hi, int(v)))

    def _clampf(v, lo=0.0, hi=1.0):
        return max(lo, min(hi, float(v)))

    def _sine(t):
        return (math.sin(2.0 * math.pi * t) + 1.0) / 2.0

    def _triangle(t):
        return 1.0 - abs(2.0 * (t % 1.0) - 1.0)

    def _sawtooth(t):
        return t % 1.0

    def _square(t, duty=0.5):
        return 1.0 if (t % 1.0) < duty else 0.0

    def _ease_out(t):
        t = _clampf(t)
        return 1.0 - (1.0 - t) ** 2

    def _ease_in_out(t):
        t = _clampf(t)
        return t * t * (3.0 - 2.0 * t)

    def _hash_f(seed):
        h = (seed * 1619 + 31337) & 0xFFFFFF
        h = ((h >> 8) ^ h) * 0x6c62272e & 0xFFFFFF
        return (h & 0xFFFF) / 65535.0

    # ═══════════════════════════════════════════════════════════════════
    #  A. 跑灯高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- A1: 锯齿跑灯 ---
    def _light_sawtooth_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.3))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = (seq_i / n - t * speed) % 1.0
            # 锯齿：快速上升，缓慢下降
            if phase < width:
                v = phase / width
            else:
                v = 1.0 - (phase - width) / (1.0 - width)
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_sawtooth_chase_v30", name="锯齿跑灯", category="灯光/亮度",
        description="锯齿波形跑灯：快速点亮，缓慢熄灭，产生拖尾效果",
        target_channels=["dimmer","R","G","B","W"], icon="⟋",
        tags=["light","chase","sawtooth","trail"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",2.0,0.1,20.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","上升宽度","float",0.3,0.05,0.9,0.05,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_sawtooth_chase)

    # --- A2: 弹跳跑灯 ---
    def _light_bounce_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        width = float(params.get("width", 0.15))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        # 来回弹跳
        pos = (t * speed) % 2.0
        if pos > 1.0: pos = 2.0 - pos
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i / n - pos)
            if dist > 0.5: dist = 1.0 - dist
            v = max(0.0, 1.0 - dist / width)
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_bounce_chase_v30", name="弹跳跑灯", category="灯光/亮度",
        description="光点在首尾之间来回弹跳，如乒乓球效果",
        target_channels=["dimmer","R","G","B","W"], icon="↔",
        tags=["light","chase","bounce","ping-pong"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",1.5,0.1,15.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","光点宽度","float",0.15,0.02,0.5,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_bounce_chase)

    # --- A3: 加速跑灯 ---
    def _light_accel_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        accel = float(params.get("accel", 2.0))
        width = float(params.get("width", 0.12))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        # 加速：位置 = t^accel
        phase = (t * speed) % 1.0
        pos = phase ** (1.0 / accel)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i / n - pos)
            if dist > 0.5: dist = 1.0 - dist
            v = max(0.0, 1.0 - dist / width)
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_accel_chase_v30", name="加速跑灯", category="灯光/亮度",
        description="光点从慢到快加速跑过，每个周期都有加速感",
        target_channels=["dimmer","R","G","B","W"], icon="→→→",
        tags=["light","chase","accelerate","speed"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","基础速度","float",1.0,0.1,10.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("accel","加速指数","float",2.0,0.5,5.0,0.5,
                tooltip="控制加速度或加速因子。值越大速度随时间的增长越快"),
            ParamSpec("width","光点宽度","float",0.12,0.02,0.4,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_accel_chase)

    # --- A4: 双向对冲跑灯 ---
    def _light_dual_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.12))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        pos1 = (t * speed) % 1.0
        pos2 = 1.0 - pos1
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            d1 = abs(p - pos1); d1 = min(d1, 1.0 - d1)
            d2 = abs(p - pos2); d2 = min(d2, 1.0 - d2)
            v = max(
                max(0.0, 1.0 - d1 / width),
                max(0.0, 1.0 - d2 / width)
            )
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_dual_chase_v30", name="双向对冲跑灯", category="灯光/亮度",
        description="两个光点从两端相向而行，对冲穿越，产生碰撞感",
        target_channels=["dimmer","R","G","B","W"], icon="←→",
        tags=["light","chase","dual","collision"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",2.0,0.1,15.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","光点宽度","float",0.12,0.02,0.4,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_dual_chase)

    # --- A5: 三点追逐 ---
    def _light_triple_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.1))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            v = 0.0
            for k in range(3):
                pos = (t * speed + k / 3.0) % 1.0
                d = abs(p - pos); d = min(d, 1.0 - d)
                v = max(v, max(0.0, 1.0 - d / width))
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_triple_chase_v30", name="三点追逐", category="灯光/亮度",
        description="三个等间距光点同向追逐，形成三重跑灯效果",
        target_channels=["dimmer","R","G","B","W"], icon="•••→",
        tags=["light","chase","triple","multi"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",2.0,0.1,15.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","光点宽度","float",0.1,0.02,0.3,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_triple_chase)

    # --- A6: 阶梯跑灯 ---
    def _light_staircase_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        steps = max(2, int(params.get("steps", 4)))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        pos = (t * speed * steps) % steps
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            step_idx = int(seq_i / n * steps)
            # 当前激活的阶梯
            active = int(pos) % steps
            if step_idx <= active:
                v = 1.0
            else:
                v = 0.1
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_staircase_chase_v30", name="阶梯跑灯", category="灯光/亮度",
        description="灯光像阶梯一样逐段点亮，形成阶梯式递进效果",
        target_channels=["dimmer","R","G","B","W"], icon="▁▃▅▇",
        tags=["light","chase","staircase","step"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",1.0,0.1,10.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("steps","阶梯数","int",4,2,16,1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_staircase_chase)

    # --- A7: 彗星跑灯（带长尾）---
    def _light_comet_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        tail = float(params.get("tail", 0.3))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        pos = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            diff = (pos - p) % 1.0
            if diff < tail:
                v = 1.0 - diff / tail
            else:
                v = 0.0
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_comet_chase_v30", name="彗星跑灯", category="灯光/亮度",
        description="光点带有长尾拖影，如彗星划过，尾部渐渐消失",
        target_channels=["dimmer","R","G","B","W"], icon="☄→",
        tags=["light","chase","comet","tail","trail"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",2.0,0.1,15.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("tail","尾巴长度","float",0.3,0.05,0.8,0.05,
                tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_comet_chase)

    # --- A8: 双彗星对冲 ---
    def _light_dual_comet(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        tail = float(params.get("tail", 0.25))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        pos1 = (t * speed) % 1.0
        pos2 = (1.0 - t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            d1 = (pos1 - p) % 1.0
            v1 = max(0.0, 1.0 - d1 / tail) if d1 < tail else 0.0
            d2 = (pos2 - p) % 1.0
            v2 = max(0.0, 1.0 - d2 / tail) if d2 < tail else 0.0
            ef.values[orig_i][ch] = _clamp(lo + max(v1, v2) * (hi - lo))

    reg(EffectPreset(
        id="light_dual_comet_v30", name="双彗星对冲", category="灯光/亮度",
        description="两个彗星从两端相向而行，各带拖尾，产生碰撞感",
        target_channels=["dimmer","R","G","B","W"], icon="☄←→☄",
        tags=["light","chase","comet","dual","collision"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",2.0,0.1,15.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("tail","尾巴长度","float",0.25,0.05,0.6,0.05,
                tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_dual_comet)

    # ═══════════════════════════════════════════════════════════════════
    #  B. 呼吸高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- B1: 双频呼吸 ---
    def _light_dual_freq_breath(ef, t, sorted_fx, fdefs, params, fps):
        freq1 = float(params.get("freq1", 0.5))
        freq2 = float(params.get("freq2", 1.3))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        v = (_sine(t * freq1) + _sine(t * freq2)) / 2.0
        val = _clamp(lo + v * (hi - lo))
        for orig_i, fi in sorted_fx:
            ef.values[orig_i][ch] = val

    reg(EffectPreset(
        id="light_dual_freq_breath_v30", name="双频呼吸", category="灯光/呼吸",
        description="两个不同频率的正弦波叠加，产生不规则的呼吸节奏",
        target_channels=["dimmer","R","G","B","W"], icon="∿∿",
        tags=["light","breath","dual","frequency","irregular"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("freq1","频率1","float",0.5,0.1,5.0,0.1,
                tooltip="控制第一层效果的波动频率。值越高波动越急促"),
            ParamSpec("freq2","频率2","float",1.3,0.1,5.0,0.1,
                tooltip="控制第二层效果的波动频率。值越高波动越急促"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_dual_freq_breath)

    # --- B2: 三频呼吸 ---
    def _light_triple_freq_breath(ef, t, sorted_fx, fdefs, params, fps):
        freq1 = float(params.get("freq1", 0.5))
        freq2 = float(params.get("freq2", 1.3))
        freq3 = float(params.get("freq3", 2.1))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        v = (_sine(t * freq1) + _sine(t * freq2) + _sine(t * freq3)) / 3.0
        val = _clamp(lo + v * (hi - lo))
        for orig_i, fi in sorted_fx:
            ef.values[orig_i][ch] = val

    reg(EffectPreset(
        id="light_triple_freq_breath_v30", name="三频呼吸", category="灯光/呼吸",
        description="三个不同频率的正弦波叠加，产生复杂的呼吸节奏",
        target_channels=["dimmer","R","G","B","W"], icon="∿∿∿",
        tags=["light","breath","triple","frequency","complex"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("freq1","频率1","float",0.5,0.1,5.0,0.1,
                tooltip="控制第一层效果的波动频率。值越高波动越急促"),
            ParamSpec("freq2","频率2","float",1.3,0.1,5.0,0.1,
                tooltip="控制第二层效果的波动频率。值越高波动越急促"),
            ParamSpec("freq3","频率3","float",2.1,0.1,5.0,0.1,
                tooltip="控制第三层效果的波动频率。值越高波动越急促"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_triple_freq_breath)

    # --- B3: 不对称呼吸（快吸慢呼）---
    def _light_asymm_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        rise_ratio = float(params.get("rise_ratio", 0.2))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        phase = (t * speed) % 1.0
        if phase < rise_ratio:
            v = _ease_out(phase / rise_ratio)
        else:
            v = 1.0 - _ease_in_out((phase - rise_ratio) / (1.0 - rise_ratio))
        val = _clamp(lo + v * (hi - lo))
        for orig_i, fi in sorted_fx:
            ef.values[orig_i][ch] = val

    reg(EffectPreset(
        id="light_asymm_breath_v30", name="不对称呼吸", category="灯光/呼吸",
        description="快速点亮，缓慢熄灭，模拟真实呼吸节奏",
        target_channels=["dimmer","R","G","B","W"], icon="∿",
        tags=["light","breath","asymmetric","natural"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("rise_ratio","上升比例","float",0.2,0.05,0.5,0.05,
                tooltip="控制上升阶段占周期的比例。值越大攀升过程越缓"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_asymm_breath)

    # --- B4: 脉冲群呼吸 ---
    def _light_pulse_burst_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        pulses = max(1, int(params.get("pulses", 3)))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        phase = (t * speed) % 1.0
        # 在前半段发出pulses个脉冲，后半段静止
        v = 0.0
        if phase < 0.6:
            pulse_phase = phase / 0.6 * pulses
            sub_phase = pulse_phase % 1.0
            v = _sine(sub_phase * 0.5)
        val = _clamp(lo + v * (hi - lo))
        for orig_i, fi in sorted_fx:
            ef.values[orig_i][ch] = val

    reg(EffectPreset(
        id="light_pulse_burst_breath_v30", name="脉冲群呼吸", category="灯光/呼吸",
        description="连续发出几个快速脉冲后静止，再重复，如心跳群",
        target_channels=["dimmer","R","G","B","W"], icon="∿∿∿_",
        tags=["light","breath","pulse","burst","heartbeat"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",1.0,0.1,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("pulses","脉冲数","int",3,1,8,1,
                tooltip="控制脉冲数量。值越大单个周期内产生的脉冲越密集"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_pulse_burst_breath)

    # --- B5: 分组呼吸（奇偶交替）---
    def _light_group_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        groups = max(2, int(params.get("groups", 2)))
        offset = float(params.get("offset", 0.5))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            g = seq_i % groups
            phase_off = g / groups * offset
            v = _sine(t * speed + phase_off)
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_group_breath_v30", name="分组呼吸", category="灯光/呼吸",
        description="灯光分组，各组呼吸相位不同，产生波浪式呼吸效果",
        target_channels=["dimmer","R","G","B","W"], icon="∿∿",
        tags=["light","breath","group","wave","phase"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("groups","分组数","int",2,2,8,1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("offset","相位偏移","float",0.5,0.0,1.0,0.1,
                tooltip="控制效果在设备序列上的偏移位置。值越大起始点越靠后"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_group_breath)

    # --- B6: 随机相位呼吸 ---
    def _light_random_phase_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase_off = _hash_f(seq_i * 1234)
            v = _sine(t * speed + phase_off)
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    def _hash_f_local(seed):
        h = (seed * 1619 + 31337) & 0xFFFFFF
        h = ((h >> 8) ^ h) * 0x6c62272e & 0xFFFFFF
        return (h & 0xFFFF) / 65535.0

    reg(EffectPreset(
        id="light_random_phase_breath_v30", name="随机相位呼吸", category="灯光/呼吸",
        description="每盏灯有随机的呼吸相位，整体看起来像星空闪烁",
        target_channels=["dimmer","R","G","B","W"], icon="✦✦✦",
        tags=["light","breath","random","phase","starfield"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_random_phase_breath)

    # ═══════════════════════════════════════════════════════════════════
    #  C. 闪烁高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- C1: 随机闪烁 ---
    def _light_random_strobe(ef, t, sorted_fx, fdefs, params, fps):
        freq = float(params.get("freq", 5.0))
        density = float(params.get("density", 0.3))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 每盏灯有独立的随机闪烁
            seed = int(t * freq + seq_i * 0.7) + seq_i * 1000
            v = 1.0 if _hash_f(seed) < density else 0.0
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_random_strobe_v30", name="随机闪烁", category="灯光/亮度",
        description="每盏灯随机独立闪烁，产生随机闪光效果",
        target_channels=["dimmer","R","G","B","W"], icon="✦✦",
        tags=["light","strobe","random","sparkle"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("freq","闪烁频率","float",5.0,0.5,30.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("density","密度","float",0.3,0.05,0.9,0.05,
                tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_random_strobe)

    # --- C2: 分组交替闪烁 ---
    def _light_group_strobe(ef, t, sorted_fx, fdefs, params, fps):
        freq = float(params.get("freq", 4.0))
        groups = max(2, int(params.get("groups", 2)))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        phase = (t * freq) % 1.0
        active_group = int(phase * groups)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            g = seq_i % groups
            v = 1.0 if g == active_group else 0.0
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_group_strobe_v30", name="分组交替闪烁", category="灯光/亮度",
        description="灯光分组，各组依次闪烁，产生轮流闪光效果",
        target_channels=["dimmer","R","G","B","W"], icon="▌▐",
        tags=["light","strobe","group","alternate"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("freq","闪烁频率","float",4.0,0.5,20.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("groups","分组数","int",2,2,8,1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_group_strobe)

    # --- C3: 节奏闪烁（强拍全亮，弱拍随机）---
    def _light_rhythm_strobe(ef, t, sorted_fx, fdefs, params, fps):
        bpm = float(params.get("bpm", 120.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        beat_phase = (t * bpm / 60.0) % 1.0
        # 强拍（beat_phase < 0.1）全亮，弱拍随机
        if beat_phase < 0.1:
            v = 1.0
        elif beat_phase < 0.2:
            v = 1.0 - (beat_phase - 0.1) / 0.1
        else:
            v = 0.0
        val = _clamp(lo + v * (hi - lo))
        for orig_i, fi in sorted_fx:
            ef.values[orig_i][ch] = val

    reg(EffectPreset(
        id="light_rhythm_strobe_v30", name="节奏闪烁", category="灯光/亮度",
        description="按BPM节拍闪烁，强拍全亮，快速衰减，产生节奏感",
        target_channels=["dimmer","R","G","B","W"], icon="♩♩",
        tags=["light","strobe","rhythm","beat","bpm"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("bpm","BPM","float",120.0,30.0,300.0,5.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_rhythm_strobe)

    # --- C4: 频闪序列（不同频率交替）---
    def _light_strobe_sequence(ef, t, sorted_fx, fdefs, params, fps):
        freq_lo = float(params.get("freq_lo", 2.0))
        freq_hi = float(params.get("freq_hi", 10.0))
        cycle = float(params.get("cycle", 4.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        # 频率随时间在freq_lo~freq_hi之间扫描
        cycle_phase = (t / cycle) % 1.0
        freq = freq_lo + (freq_hi - freq_lo) * cycle_phase
        v = _square(t * freq)
        val = _clamp(lo + v * (hi - lo))
        for orig_i, fi in sorted_fx:
            ef.values[orig_i][ch] = val

    reg(EffectPreset(
        id="light_strobe_sequence_v30", name="频闪序列", category="灯光/亮度",
        description="频闪频率从低到高扫描，产生加速频闪效果",
        target_channels=["dimmer","R","G","B","W"], icon="▌▌▌",
        tags=["light","strobe","sequence","frequency","sweep"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("freq_lo","最低频率","float",2.0,0.5,10.0,0.5,
                tooltip="控制频率变化的下限边界。值越低波动越舒缓"),
            ParamSpec("freq_hi","最高频率","float",10.0,2.0,30.0,1.0,
                tooltip="控制频率变化范围的上限。值越高波动越急促"),
            ParamSpec("cycle","扫描周期","float",4.0,1.0,20.0,1.0,
                tooltip="控制扫描周期数。值越大扫描完成的来回次数越多"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_strobe_sequence)

    # ═══════════════════════════════════════════════════════════════════
    #  D. 亮度渐变高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- D1: 波形亮度（正弦分布）---
    def _light_sine_level(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        freq = float(params.get("freq", 2.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            v = _sine(seq_i / n * freq - t * speed)
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_sine_level_v30", name="正弦亮度分布", category="灯光/亮度",
        description="灯光亮度按正弦波分布，整体形成波浪形亮度图案",
        target_channels=["dimmer","R","G","B","W"], icon="∿",
        tags=["light","level","sine","wave","distribution"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","移动速度","float",0.5,0.0,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("freq","频率","float",2.0,0.5,8.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_sine_level)

    # --- D2: 多段渐变 ---
    def _light_multi_gradient(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        segments = max(2, int(params.get("segments", 4)))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = (seq_i / n * segments - t * speed) % 1.0
            v = _triangle(p)
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_multi_gradient_v30", name="多段渐变", category="灯光/亮度",
        description="灯光分成多段，每段内部渐变，整体形成多段渐变图案",
        target_channels=["dimmer","R","G","B","W"], icon="▁▃▅▇▅▃▁",
        tags=["light","gradient","multi","segment"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","移动速度","float",0.5,0.0,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("segments","段数","int",4,2,12,1,
                tooltip="控制设备分割的段数。值越大分段越细，变化越丰富"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_multi_gradient)

    # --- D3: 亮度阶梯 ---
    def _light_brightness_stair(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        steps = max(2, int(params.get("steps", 8)))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            step = int(seq_i / n * steps)
            v = step / (steps - 1)
            # 整体随时间移动
            phase = (v - t * speed) % 1.0
            ef.values[orig_i][ch] = _clamp(lo + phase * (hi - lo))

    reg(EffectPreset(
        id="light_brightness_stair_v30", name="亮度阶梯", category="灯光/亮度",
        description="灯光亮度按阶梯分布，形成从暗到亮的阶梯图案",
        target_channels=["dimmer","R","G","B","W"], icon="▁▂▃▄▅▆▇",
        tags=["light","brightness","stair","gradient"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","移动速度","float",0.5,0.0,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("steps","阶梯数","int",8,2,16,1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_brightness_stair)

    # --- D4: 亮度从中心向两端渐变 ---
    def _light_center_gradient(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        pulse = _sine(t * speed)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            # 中心最亮，两端最暗
            dist_center = abs(p - 0.5) * 2.0
            v = (1.0 - dist_center) * pulse
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_center_gradient_v30", name="中心渐变呼吸", category="灯光/呼吸",
        description="中间最亮，向两端渐暗，整体呼吸，产生聚光灯效果",
        target_channels=["dimmer","R","G","B","W"], icon="▁▅█▅▁",
        tags=["light","gradient","center","breath","spotlight"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","呼吸速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_center_gradient)

    # --- D5: 亮度随机游走 ---
    def _light_random_walk_brightness(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 每盏灯有独立的随机游走
            seed1 = seq_i * 1000 + int(t * speed * 10)
            seed2 = seed1 + 1
            v1 = _hash_f(seed1)
            v2 = _hash_f(seed2)
            frac = (t * speed * 10) % 1.0
            v = v1 + (v2 - v1) * frac
            ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="light_random_walk_v30", name="亮度随机游走", category="灯光/亮度",
        description="每盏灯亮度独立随机游走，产生有机的随机亮度变化",
        target_channels=["dimmer","R","G","B","W"], icon="~",
        tags=["light","random","walk","organic","noise"],
        params=[
            ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed","变化速度","float",1.0,0.1,10.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_random_walk_brightness)

    # ═══════════════════════════════════════════════════════════════════
    #  E. 矩阵高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- E1: 矩阵扫描线 ---
#     def _light_matrix_scan_line(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         width = float(params.get("width", 1.5))
#         direction = params.get("direction", "horizontal")
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             if direction == "horizontal":
#                 pos = (t * speed * rows) % rows
#                 dist = abs(row - pos)
#             elif direction == "vertical":
#                 pos = (t * speed * cols) % cols
#                 dist = abs(col - pos)
#             else:  # diagonal
#                 pos = (t * speed * (rows + cols)) % (rows + cols)
#                 dist = abs(row + col - pos)
#             v = max(0.0, 1.0 - dist / width)
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_scan_line_v30", name="矩阵扫描线", category="灯光/矩阵",
#         description="一条亮线在矩阵中扫描，可以水平/垂直/对角方向",
#         target_channels=["dimmer","R","G","B","W"], icon="▦—",
#         tags=["light","matrix","scan","line","sweep"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","扫描速度","float",1.0,0.1,8.0,0.1),
#             ParamSpec("width","线宽","float",1.5,0.5,5.0,0.5),
#             ParamSpec("direction","方向","choice","horizontal",choices=["horizontal","vertical","diagonal"]),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_scan_line)

    # --- E2: 矩阵棋盘呼吸 ---
#     def _light_matrix_checker_breath(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         v1 = _sine(t * speed)
#         v2 = 1.0 - v1
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             is_white = (row + col) % 2 == 0
#             v = v1 if is_white else v2
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_checker_breath_v30", name="矩阵棋盘呼吸", category="灯光/矩阵",
#         description="棋盘格交替呼吸，黑格亮时白格暗，交替变化",
#         target_channels=["dimmer","R","G","B","W"], icon="▦⬛⬜",
#         tags=["light","matrix","checker","breath","alternate"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_checker_breath)

    # --- E3: 矩阵行波 ---
#     def _light_matrix_row_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         freq = float(params.get("freq", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             v = _sine(row / rows * freq - t * speed)
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_row_wave_v30", name="矩阵行波", category="灯光/矩阵",
#         description="矩阵按行产生波浪，每行亮度不同，整体形成行方向的波浪",
#         target_channels=["dimmer","R","G","B","W"], icon="▦≈",
#         tags=["light","matrix","row","wave"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1),
#             ParamSpec("freq","频率","float",2.0,0.5,8.0,0.5),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_row_wave)

    # --- E4: 矩阵列波 ---
#     def _light_matrix_col_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         freq = float(params.get("freq", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             col = seq_i % cols
#             v = _sine(col / cols * freq - t * speed)
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_col_wave_v30", name="矩阵列波", category="灯光/矩阵",
#         description="矩阵按列产生波浪，整体形成列方向的波浪",
#         target_channels=["dimmer","R","G","B","W"], icon="▦≋",
#         tags=["light","matrix","col","wave"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1),
#             ParamSpec("freq","频率","float",2.0,0.5,8.0,0.5),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_col_wave)

    # --- E5: 矩阵噪声 ---
#     def _light_matrix_noise(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         scale = float(params.get("scale", 0.3))
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
            # 伪噪声
#             x = col / cols + t * speed * 0.3
#             y = row / rows + t * speed * 0.2
#             v = (math.sin(x * 7.3 + t * speed) * math.cos(y * 5.1 + t * speed * 0.7) +
#                  math.sin(x * 3.1 + y * 4.7 + t * speed * 1.3) * 0.5 +
#                  math.sin(x * 11.3 - y * 8.9 + t * speed * 0.5) * 0.25) / 1.75
#             v = v * 0.5 + 0.5
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_noise_v30", name="矩阵噪声", category="灯光/矩阵",
#         description="矩阵产生流动的噪声图案，有机自然的亮度变化",
#         target_channels=["dimmer","R","G","B","W"], icon="▦~",
#         tags=["light","matrix","noise","organic","flow"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","流动速度","float",0.5,0.1,5.0,0.1),
#             ParamSpec("scale","噪声尺度","float",0.3,0.1,2.0,0.1),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_noise)

    # --- E6: 矩阵旋转扫描 ---
#     def _light_matrix_rotate_scan(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         width = float(params.get("width", 0.3))
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         rows = max(1, (n + cols - 1) // cols)
#         cx = (cols - 1) / 2.0
#         cy = (rows - 1) / 2.0
#         angle = t * speed * 2 * math.pi
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             dx = col - cx
#             dy = row - cy
#             cell_angle = math.atan2(dy, dx)
#             diff = (cell_angle - angle) % (2 * math.pi)
#             if diff > math.pi: diff = 2 * math.pi - diff
#             v = max(0.0, 1.0 - diff / (width * math.pi))
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_rotate_scan_v30", name="矩阵旋转扫描", category="灯光/矩阵",
#         description="一条亮线从中心旋转扫描，如雷达扫描效果",
#         target_channels=["dimmer","R","G","B","W"], icon="▦⟳",
#         tags=["light","matrix","rotate","scan","radar"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","旋转速度","float",0.5,0.1,5.0,0.1),
#             ParamSpec("width","扫描宽度","float",0.3,0.05,1.0,0.05),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_rotate_scan)

    # --- E7: 矩阵分形 ---
#     def _light_matrix_fractal(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.3))
#         zoom = float(params.get("zoom", 1.0))
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             x = (col / cols - 0.5) * zoom * 3 + math.sin(t * speed) * 0.5
#             y = (row / rows - 0.5) * zoom * 3 + math.cos(t * speed * 0.7) * 0.5
            # 简单分形：Mandelbrot近似
#             zx, zy = 0.0, 0.0
#             iters = 0
#             for _ in range(8):
#                 zx2 = zx * zx - zy * zy + x
#                 zy2 = 2 * zx * zy + y
#                 zx, zy = zx2, zy2
#                 if zx * zx + zy * zy > 4:
#                     break
#                 iters += 1
#             v = iters / 8.0
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_fractal_v30", name="矩阵分形", category="灯光/矩阵",
#         description="矩阵产生分形图案（Mandelbrot近似），随时间缓慢变化",
#         target_channels=["dimmer","R","G","B","W"], icon="▦∞",
#         tags=["light","matrix","fractal","mandelbrot","complex"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","变化速度","float",0.3,0.05,2.0,0.05),
#             ParamSpec("zoom","缩放","float",1.0,0.3,3.0,0.1),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_fractal)

    # --- E8: 矩阵双螺旋 ---
#     def _light_matrix_double_helix(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
            # 双螺旋：两条螺旋线叠加
#             phase1 = (col / cols * 2 * math.pi * 2 - row / rows * 4 * math.pi - t * speed * 2 * math.pi) % (2 * math.pi)
#             phase2 = phase1 + math.pi
#             v1 = max(0.0, math.cos(phase1))
#             v2 = max(0.0, math.cos(phase2))
#             v = max(v1, v2)
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_double_helix_v30", name="矩阵双螺旋", category="灯光/矩阵",
#         description="矩阵产生DNA双螺旋图案，两条螺旋线交替出现",
#         target_channels=["dimmer","R","G","B","W"], icon="▦⌀",
#         tags=["light","matrix","double","helix","dna","spiral"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_double_helix)

    # --- E9: 矩阵涌浪 ---
#     def _light_matrix_surge(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         freq = float(params.get("freq", 3.0))
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
            # 涌浪：列方向的正弦波 + 行方向的调制
#             wave = math.sin(col / cols * freq * 2 * math.pi - t * speed * 2 * math.pi)
#             wave_height = (wave + 1) / 2.0 * rows
#             dist = abs(row - wave_height)
#             v = max(0.0, 1.0 - dist / 1.5)
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_surge_v30", name="矩阵涌浪", category="灯光/矩阵",
#         description="矩阵产生涌浪效果，波浪线在矩阵中起伏",
#         target_channels=["dimmer","R","G","B","W"], icon="▦〜",
#         tags=["light","matrix","surge","wave","ocean"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1),
#             ParamSpec("freq","频率","float",3.0,0.5,8.0,0.5),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_surge)

    # --- E10: 矩阵细胞自动机 ---
#     def _light_matrix_cellular(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         if n == 0: return
#         rows = max(1, (n + cols - 1) // cols)
        # 基于时间的伪细胞自动机
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
            # 每个细胞的状态由其坐标和时间决定
#             cell_t = int(t * speed)
            # 用哈希模拟细胞状态演化
#             state = _hash_f(row * 100 + col + cell_t * 10000)
            # 邻居影响
#             neighbors = sum(
#                 _hash_f((row + dr) * 100 + (col + dc) + cell_t * 10000) > 0.5
#                 for dr in [-1, 0, 1] for dc in [-1, 0, 1]
#                 if not (dr == 0 and dc == 0)
#             )
            # 简化的生命游戏规则
#             alive = (state > 0.5 and 2 <= neighbors <= 3) or (state <= 0.5 and neighbors == 3)
#             v = 1.0 if alive else 0.0
#             ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
# 
#     reg(EffectPreset(
#         id="light_matrix_cellular_v30", name="矩阵细胞自动机", category="灯光/矩阵",
#         description="矩阵产生类似生命游戏的细胞自动机图案",
#         target_channels=["dimmer","R","G","B","W"], icon="▦⬜⬛",
#         tags=["light","matrix","cellular","automata","life","game"],
#         params=[
#             ParamSpec("ch_type","通道","choice","dimmer",choices=["dimmer","R","G","B","W"]),
#             ParamSpec("speed","演化速度","float",0.5,0.1,5.0,0.1),
#             ParamSpec("cols","列数","int",4,2,16,1),
#             ParamSpec("lo","低值","int",0,0,255,1),
#             ParamSpec("hi","高值","int",255,0,255,1),
#         ],
#     ), _light_matrix_cellular)

    # ═══════════════════════════════════════════════════════════════════
    #  F. 颜色高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- F1: 颜色序列跑 ---
    def _light_color_sequence_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        n = len(sorted_fx)
        if n == 0: return
        colors = [
            (255, 0, 0), (255, 128, 0), (255, 255, 0),
            (0, 255, 0), (0, 255, 255), (0, 0, 255),
            (128, 0, 255), (255, 0, 255),
        ]
        nc = len(colors)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = (seq_i / n - t * speed) % 1.0
            ci = int(phase * nc) % nc
            ci2 = (ci + 1) % nc
            frac = (phase * nc) % 1.0
            r = int(colors[ci][0] * (1 - frac) + colors[ci2][0] * frac)
            g = int(colors[ci][1] * (1 - frac) + colors[ci2][1] * frac)
            b = int(colors[ci][2] * (1 - frac) + colors[ci2][2] * frac)
            ef.values[orig_i]["R"] = _clamp(r)
            ef.values[orig_i]["G"] = _clamp(g)
            ef.values[orig_i]["B"] = _clamp(b)

    reg(EffectPreset(
        id="light_color_sequence_chase_v30", name="颜色序列跑", category="灯光/颜色",
        description="彩虹颜色序列在灯光上跑动，产生彩色流动效果",
        target_channels=["R","G","B"], icon="🌈→",
        tags=["light","color","rainbow","chase","sequence"],
        params=[
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _light_color_sequence_chase)

    # --- F2: 颜色呼吸轮换 ---
    def _light_color_breath_cycle(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        n = len(sorted_fx)
        if n == 0: return
        colors = [
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (0, 255, 255), (255, 0, 255),
        ]
        nc = len(colors)
        phase = (t * speed) % 1.0
        ci = int(phase * nc) % nc
        ci2 = (ci + 1) % nc
        frac = (phase * nc) % 1.0
        breath = _sine(t * speed * nc)
        r = int((colors[ci][0] * (1 - frac) + colors[ci2][0] * frac) * breath)
        g = int((colors[ci][1] * (1 - frac) + colors[ci2][1] * frac) * breath)
        b = int((colors[ci][2] * (1 - frac) + colors[ci2][2] * frac) * breath)
        for orig_i, fi in sorted_fx:
            ef.values[orig_i]["R"] = _clamp(r)
            ef.values[orig_i]["G"] = _clamp(g)
            ef.values[orig_i]["B"] = _clamp(b)

    reg(EffectPreset(
        id="light_color_breath_cycle_v30", name="颜色呼吸轮换", category="灯光/颜色",
        description="颜色在红→绿→蓝→黄→青→紫之间循环，同时呼吸",
        target_channels=["R","G","B"], icon="🌈∿",
        tags=["light","color","breath","cycle","rainbow"],
        params=[
            ParamSpec("speed","速度","float",0.3,0.05,3.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _light_color_breath_cycle)

    # --- F3: 颜色分组跑 ---
    def _light_color_group_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        group_size = max(1, int(params.get("group_size", 3)))
        n = len(sorted_fx)
        if n == 0: return
        colors = [
            (255, 0, 0), (0, 255, 0), (0, 0, 255),
            (255, 255, 0), (0, 255, 255), (255, 0, 255),
        ]
        nc = len(colors)
        offset = int(t * speed * group_size) % (nc * group_size)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ci = ((seq_i + offset) // group_size) % nc
            r, g, b = colors[ci]
            ef.values[orig_i]["R"] = _clamp(r)
            ef.values[orig_i]["G"] = _clamp(g)
            ef.values[orig_i]["B"] = _clamp(b)

    reg(EffectPreset(
        id="light_color_group_chase_v30", name="颜色分组跑", category="灯光/颜色",
        description="灯光按组分配颜色，颜色组依次跑动",
        target_channels=["R","G","B"], icon="🌈▌▌",
        tags=["light","color","group","chase"],
        params=[
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("group_size","组大小","int",3,1,10,1,
                tooltip="控制每组包含的设备数量。值越大同组运行的设备越多"),
        ],
    ), _light_color_group_chase)

    # --- F4: 颜色波浪 ---
    def _light_color_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            hue = (seq_i / n + t * speed) % 1.0
            # HSV to RGB（简化版，S=1, V=1）
            h6 = hue * 6.0
            i = int(h6)
            f = h6 - i
            p = 0.0
            q = 1.0 - f
            r_val = f
            if i == 0: r, g, b = 1.0, r_val, p
            elif i == 1: r, g, b = q, 1.0, p
            elif i == 2: r, g, b = p, 1.0, r_val
            elif i == 3: r, g, b = p, q, 1.0
            elif i == 4: r, g, b = r_val, p, 1.0
            else: r, g, b = 1.0, p, q
            ef.values[orig_i]["R"] = _clamp(r * 255)
            ef.values[orig_i]["G"] = _clamp(g * 255)
            ef.values[orig_i]["B"] = _clamp(b * 255)

    reg(EffectPreset(
        id="light_color_wave_v30", name="颜色波浪", category="灯光/颜色",
        description="彩虹色按位置分布，整体形成彩色波浪，随时间流动",
        target_channels=["R","G","B"], icon="🌈〜",
        tags=["light","color","wave","rainbow","hue"],
        params=[
            ParamSpec("speed","速度","float",0.5,0.05,5.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _light_color_wave)

    # --- F5: 双色交替闪烁 ---
    def _light_dual_color_strobe(ef, t, sorted_fx, fdefs, params, fps):
        freq = float(params.get("freq", 4.0))
        r1 = int(params.get("r1", 255))
        g1 = int(params.get("g1", 0))
        b1 = int(params.get("b1", 0))
        r2 = int(params.get("r2", 0))
        g2 = int(params.get("g2", 0))
        b2 = int(params.get("b2", 255))
        phase = (t * freq) % 1.0
        if phase < 0.5:
            r, g, b = r1, g1, b1
        else:
            r, g, b = r2, g2, b2
        for orig_i, fi in sorted_fx:
            ef.values[orig_i]["R"] = _clamp(r)
            ef.values[orig_i]["G"] = _clamp(g)
            ef.values[orig_i]["B"] = _clamp(b)

    reg(EffectPreset(
        id="light_dual_color_strobe_v30", name="双色交替闪烁", category="灯光/颜色",
        description="两种颜色交替闪烁，产生强烈的视觉冲击",
        target_channels=["R","G","B"], icon="🔴🔵",
        tags=["light","color","dual","strobe","alternate"],
        params=[
            ParamSpec("freq","频率","float",4.0,0.5,20.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("r1","颜色1-R","int",255,0,255,1,
                tooltip="控制第一种颜色的红色通道强度。0=无红色，255=满红"),
            ParamSpec("g1","颜色1-G","int",0,0,255,1,
                tooltip="控制第一种颜色的绿色通道强度。0=无绿色，255=满绿"),
            ParamSpec("b1","颜色1-B","int",0,0,255,1,
                tooltip="控制第一种颜色的蓝色通道强度。0=无蓝色，255=满蓝"),
            ParamSpec("r2","颜色2-R","int",0,0,255,1,
                tooltip="控制第二种颜色的红色通道强度。0=无红色，255=满红"),
            ParamSpec("g2","颜色2-G","int",0,0,255,1,
                tooltip="控制第二种颜色的绿色通道强度。0=无绿色，255=满绿"),
            ParamSpec("b2","颜色2-B","int",255,0,255,1,
                tooltip="控制第二种颜色的蓝色通道强度。0=无蓝色，255=满蓝"),
        ],
    ), _light_dual_color_strobe)

    # --- F6: 颜色随机跳变 ---
    def _light_color_random_jump(ef, t, sorted_fx, fdefs, params, fps):
        freq = float(params.get("freq", 2.0))
        n = len(sorted_fx)
        if n == 0: return
        colors = [
            (255, 0, 0), (255, 128, 0), (255, 255, 0),
            (0, 255, 0), (0, 255, 128), (0, 255, 255),
            (0, 128, 255), (0, 0, 255), (128, 0, 255),
            (255, 0, 255), (255, 255, 255),
        ]
        nc = len(colors)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seed = int(t * freq) + seq_i * 100
            ci = int(_hash_f(seed) * nc) % nc
            r, g, b = colors[ci]
            ef.values[orig_i]["R"] = _clamp(r)
            ef.values[orig_i]["G"] = _clamp(g)
            ef.values[orig_i]["B"] = _clamp(b)

    reg(EffectPreset(
        id="light_color_random_jump_v30", name="颜色随机跳变", category="灯光/颜色",
        description="每盏灯随机跳变颜色，产生彩色随机闪烁效果",
        target_channels=["R","G","B"], icon="🎲🌈",
        tags=["light","color","random","jump","sparkle"],
        params=[
            ParamSpec("freq","跳变频率","float",2.0,0.1,10.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
        ],
    ), _light_color_random_jump)

