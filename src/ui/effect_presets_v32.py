"""
effect_presets_v32.py — 电机效果大扩充 v32
=============================================
新增约50个电机效果变式，包含：
  A. 龙摆高级变式（多段/加速/弹跳/双向/三点）
  B. 交叉高级变式（多频/不对称/涌浪）
  C. 高级变式（群脉冲/节奏/随机/分组）
  D. 双轴高级变式（阶梯/中心/随机游走）
  E. 矩阵高级变式（扫描线/噪声/旋转）
"""
from __future__ import annotations
import math
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .effect_engine import EffectEngine, EffectPreset, EffectFrame, ParamSpec


def _register_v32_presets(engine: "EffectEngine"):
    """注册 v32 电机效果大扩充预设"""
    from .effect_engine import EffectPreset, ParamSpec
    reg = engine._reg

    def _clamp(v, lo=0, hi=255):
        return max(lo, min(hi, int(v)))

    def _clampf(v, lo=0.0, hi=1.0):
        return max(lo, min(hi, float(v)))

    def _sine(t):
        return (math.sin(2.0 * math.pi * t) + 1.0) / 2.0

    def _sine_raw(t):
        return math.sin(2.0 * math.pi * t)

    def _triangle(t):
        return 1.0 - abs(2.0 * (t % 1.0) - 1.0)

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

    def _to_speed(v, center=128, lo=0, hi=255):
        """将0~1的值映射到电机速度通道"""
        return _clamp(lo + v * (hi - lo))

    # ═══════════════════════════════════════════════════════════════════
    #  A. 龙摆高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- A1: 弹跳龙摆 ---
    def _motor_bounce_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        width = float(params.get("width", 0.15))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        pos = (t * speed) % 2.0
        if pos > 1.0: pos = 2.0 - pos
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i / n - pos)
            if dist > 0.5: dist = 1.0 - dist
            v = max(0.0, 1.0 - dist / width)
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_bounce_dragon_v32", name="弹跳龙摆", category="电机/龙摆",
        description="电机激活点在首尾之间来回弹跳，如乒乓球效果",
        target_channels=["speed"], icon="↔",
        tags=["motor","dragon","bounce","ping-pong"],
        params=[
            ParamSpec("speed","速度","float",1.5,0.1,15.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","宽度","float",0.15,0.02,0.5,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_bounce_dragon)

    # --- A2: 彗星龙摆（带长尾）---
    def _motor_comet_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        tail = float(params.get("tail", 0.3))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        pos = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            diff = (pos - p) % 1.0
            v = max(0.0, 1.0 - diff / tail) if diff < tail else 0.0
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_comet_dragon_v32", name="彗星龙摆", category="电机/龙摆",
        description="电机激活点带有长尾拖影，如彗星划过",
        target_channels=["speed"], icon="☄→",
        tags=["motor","dragon","comet","tail","trail"],
        params=[
            ParamSpec("speed","速度","float",2.0,0.1,15.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("tail","尾巴长度","float",0.3,0.05,0.8,0.05,
                tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_comet_dragon)

    # --- A3: 双向对冲龙摆 ---
    def _motor_dual_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.12))
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
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_dual_dragon_v32", name="双向对冲龙摆", category="电机/龙摆",
        description="两个激活点从两端相向而行，对冲穿越",
        target_channels=["speed"], icon="←→",
        tags=["motor","dragon","dual","collision"],
        params=[
            ParamSpec("speed","速度","float",2.0,0.1,15.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","宽度","float",0.12,0.02,0.4,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_dual_dragon)

    # --- A4: 三点追逐龙摆 ---
    def _motor_triple_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.1))
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
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_triple_dragon_v32", name="三点追逐龙摆", category="电机/龙摆",
        description="三个等间距激活点同向追逐，形成三重龙摆效果",
        target_channels=["speed"], icon="•••→",
        tags=["motor","dragon","triple","multi"],
        params=[
            ParamSpec("speed","速度","float",2.0,0.1,15.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","宽度","float",0.1,0.02,0.3,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_triple_dragon)

    # --- A5: 锯齿龙摆 ---
    def _motor_sawtooth_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.3))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = (seq_i / n - t * speed) % 1.0
            if phase < width:
                v = phase / width
            else:
                v = 1.0 - (phase - width) / (1.0 - width)
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_sawtooth_dragon_v32", name="锯齿龙摆", category="电机/龙摆",
        description="锯齿波形龙摆：快速激活，缓慢衰减，产生拖尾效果",
        target_channels=["speed"], icon="⟋",
        tags=["motor","dragon","sawtooth","trail"],
        params=[
            ParamSpec("speed","速度","float",2.0,0.1,20.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","上升宽度","float",0.3,0.05,0.9,0.05,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_sawtooth_dragon)

    # --- A6: 加速龙摆 ---
    def _motor_accel_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        accel = float(params.get("accel", 2.0))
        width = float(params.get("width", 0.12))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        phase = (t * speed) % 1.0
        pos = phase ** (1.0 / accel)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i / n - pos)
            if dist > 0.5: dist = 1.0 - dist
            v = max(0.0, 1.0 - dist / width)
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_accel_dragon_v32", name="加速龙摆", category="电机/龙摆",
        description="电机激活点从慢到快加速跑过，每个周期都有加速感",
        target_channels=["speed"], icon="→→→",
        tags=["motor","dragon","accelerate","speed"],
        params=[
            ParamSpec("speed","基础速度","float",1.0,0.1,10.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("accel","加速指数","float",2.0,0.5,5.0,0.5,
                tooltip="控制加速度或加速因子。值越大速度随时间的增长越快"),
            ParamSpec("width","宽度","float",0.12,0.02,0.4,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_accel_dragon)

    # ═══════════════════════════════════════════════════════════════════
    #  B. 交叉高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- B1: 双频交叉 ---
    def _motor_dual_freq_cross(ef, t, sorted_fx, fdefs, params, fps):
        freq1 = float(params.get("freq1", 1.0))
        freq2 = float(params.get("freq2", 2.3))
        speed = float(params.get("speed", 0.5))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            v = (_sine(p * freq1 - t * speed) + _sine(p * freq2 - t * speed * 1.3)) / 2.0
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_dual_freq_cross_v32", name="双频交叉", category="电机/交叉",
        description="两个不同频率的波浪叠加，产生复杂的交叉形态",
        target_channels=["speed"], icon="≈≈",
        tags=["motor","cross","dual","frequency","interference"],
        params=[
            ParamSpec("freq1","频率1","float",1.0,0.1,8.0,0.5,
                tooltip="控制第一层效果的波动频率。值越高波动越急促"),
            ParamSpec("freq2","频率2","float",2.3,0.1,8.0,0.5,
                tooltip="控制第二层效果的波动频率。值越高波动越急促"),
            ParamSpec("speed","速度","float",0.5,0.0,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_dual_freq_cross)

    # --- B2: 不对称交叉 ---
    def _motor_asymm_cross(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        skew = float(params.get("skew", 0.3))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            phase = (p - t * speed) % 1.0
            if phase < skew:
                v = _ease_out(phase / skew)
            else:
                v = 1.0 - _ease_in_out((phase - skew) / (1.0 - skew))
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_asymm_cross_v32", name="不对称交叉", category="电机/交叉",
        description="激活波形上升快、下降慢，产生不对称的交叉形态",
        target_channels=["speed"], icon="∿",
        tags=["motor","cross","asymmetric","natural"],
        params=[
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("skew","不对称度","float",0.3,0.05,0.5,0.05,
                tooltip="控制分布的非对称倾斜程度。值越大偏斜越明显，0 为对称"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_asymm_cross)

    # --- B3: 驻波交叉 ---
    def _motor_standing_cross(ef, t, sorted_fx, fdefs, params, fps):
        freq = float(params.get("freq", 2.0))
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            v = abs(math.sin(p * freq * math.pi * 2) * math.cos(t * speed * math.pi * 2))
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_standing_cross_v32", name="驻波交叉", category="电机/交叉",
        description="驻波效果：波节固定，波腹随时间呼吸，产生静止的交叉图案",
        target_channels=["speed"], icon="∿∿",
        tags=["motor","cross","standing","wave","physics"],
        params=[
            ParamSpec("freq","空间频率","float",2.0,0.5,8.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("speed","时间速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_standing_cross)

    # --- B4: 涌浪交叉 ---
    def _motor_surge_cross(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        surge_freq = float(params.get("surge_freq", 0.3))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        amplitude = _sine(t * surge_freq)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            v = _sine(p * 2.0 - t * speed) * amplitude
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_surge_cross_v32", name="涌浪交叉", category="电机/交叉",
        description="交叉振幅随时间周期性变化，产生涌浪效果",
        target_channels=["speed"], icon="〜",
        tags=["motor","cross","surge","amplitude","modulation"],
        params=[
            ParamSpec("speed","波浪速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("surge_freq","涌浪频率","float",0.3,0.05,2.0,0.05,
                tooltip="控制涌波频率。值越高涌动的节奏越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_surge_cross)

    # ═══════════════════════════════════════════════════════════════════
    #  C. 高级变式（脉冲/节奏/随机）
    # ═══════════════════════════════════════════════════════════════════

    # --- C1: 群脉冲电机 ---
    def _motor_burst_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        pulses = max(1, int(params.get("pulses", 3)))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        phase = (t * speed) % 1.0
        v = 0.0
        if phase < 0.6:
            pulse_phase = phase / 0.6 * pulses
            sub_phase = pulse_phase % 1.0
            v = _sine(sub_phase * 0.5)
        val = _clamp(lo + v * (hi - lo))
        for orig_i, fi in sorted_fx:
            ef.values[orig_i]["speed"] = val

    reg(EffectPreset(
        id="motor_burst_pulse_v32", name="群脉冲电机", category="电机/脉冲",
        description="连续发出几个快速脉冲后停止，再重复，如心跳群",
        target_channels=["speed"], icon="∿∿∿_",
        tags=["motor","pulse","burst","heartbeat","group"],
        params=[
            ParamSpec("speed","速度","float",1.0,0.1,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("pulses","脉冲数","int",3,1,8,1,
                tooltip="控制脉冲数量。值越大单个周期内产生的脉冲越密集"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_burst_pulse)

    # --- C2: 节奏脉冲电机 ---
    def _motor_rhythm_pulse(ef, t, sorted_fx, fdefs, params, fps):
        bpm = float(params.get("bpm", 120.0))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        beat_phase = (t * bpm / 60.0) % 1.0
        if beat_phase < 0.1:
            v = 1.0
        elif beat_phase < 0.3:
            v = 1.0 - (beat_phase - 0.1) / 0.2
        else:
            v = 0.0
        val = _clamp(lo + v * (hi - lo))
        for orig_i, fi in sorted_fx:
            ef.values[orig_i]["speed"] = val

    reg(EffectPreset(
        id="motor_rhythm_pulse_v32", name="节奏脉冲电机", category="电机/脉冲",
        description="按BPM节拍脉冲，强拍全速，快速衰减，产生节奏感",
        target_channels=["speed"], icon="♩♩",
        tags=["motor","pulse","rhythm","beat","bpm"],
        params=[
            ParamSpec("bpm","BPM","float",120.0,30.0,300.0,5.0,
                tooltip="控制效果与音乐节拍同步的 BPM 值。值越小同步节奏越慢，值越大同步越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_rhythm_pulse)

    # --- C3: 分组脉冲电机 ---
    def _motor_group_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        groups = max(2, int(params.get("groups", 3)))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        phase = (t * speed) % 1.0
        active_group = int(phase * groups)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            g = seq_i % groups
            if g == active_group:
                sub_phase = (phase * groups) % 1.0
                v = _sine(sub_phase * 0.5)
            else:
                v = 0.0
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_group_pulse_v32", name="分组脉冲电机", category="电机/脉冲",
        description="电机分组，各组依次脉冲，产生轮流激活效果",
        target_channels=["speed"], icon="▌▐",
        tags=["motor","pulse","group","alternate"],
        params=[
            ParamSpec("speed","速度","float",2.0,0.5,10.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("groups","分组数","int",3,2,8,1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_group_pulse)

    # --- C4: 随机脉冲电机 ---
    def _motor_random_pulse(ef, t, sorted_fx, fdefs, params, fps):
        freq = float(params.get("freq", 3.0))
        density = float(params.get("density", 0.4))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seed = int(t * freq + seq_i * 0.7) + seq_i * 1000
            v = 1.0 if _hash_f(seed) < density else 0.0
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_random_pulse_v32", name="随机脉冲电机", category="电机/脉冲",
        description="每个电机随机独立脉冲，产生随机激活效果",
        target_channels=["speed"], icon="✦✦",
        tags=["motor","pulse","random","sparkle"],
        params=[
            ParamSpec("freq","脉冲频率","float",3.0,0.5,15.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("density","密度","float",0.4,0.05,0.9,0.05,
                tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_random_pulse)

    # ═══════════════════════════════════════════════════════════════════
    #  D. 双轴高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- D1: 中心渐变呼吸电机 ---
    def _motor_center_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        pulse = _sine(t * speed)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = seq_i / n
            dist_center = abs(p - 0.5) * 2.0
            v = (1.0 - dist_center) * pulse
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_center_breath_v32", name="中心渐变呼吸", category="电机/渐变",
        description="中间最快，向两端渐慢，整体呼吸，产生聚焦效果",
        target_channels=["speed"], icon="▁▅█▅▁",
        tags=["motor","gradient","center","breath","focus"],
        params=[
            ParamSpec("speed","呼吸速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_center_breath)

    # --- D2: 多段渐变电机 ---
    def _motor_multi_gradient(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        segments = max(2, int(params.get("segments", 4)))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p = (seq_i / n * segments - t * speed) % 1.0
            v = 1.0 - abs(2.0 * p - 1.0)
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_multi_gradient_v32", name="多段渐变电机", category="电机/渐变",
        description="电机分成多段，每段内部渐变，整体形成多段渐变图案",
        target_channels=["speed"], icon="▁▃▅▇▅▃▁",
        tags=["motor","gradient","multi","segment"],
        params=[
            ParamSpec("speed","移动速度","float",0.5,0.0,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("segments","段数","int",4,2,12,1,
                tooltip="控制设备分割的段数。值越大分段越细，变化越丰富"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_multi_gradient)

    # --- D3: 随机游走速度 ---
    def _motor_random_walk_speed(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seed1 = seq_i * 1000 + int(t * speed * 10)
            seed2 = seed1 + 1
            v1 = _hash_f(seed1)
            v2 = _hash_f(seed2)
            frac = (t * speed * 10) % 1.0
            v = v1 + (v2 - v1) * frac
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_random_walk_v32", name="随机游走速度", category="电机/渐变",
        description="每个电机速度独立随机游走，产生有机的随机速度变化",
        target_channels=["speed"], icon="~",
        tags=["motor","random","walk","organic","noise"],
        params=[
            ParamSpec("speed","变化速度","float",1.0,0.1,10.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_random_walk_speed)

    # --- D4: 阶梯渐变电机 ---
    def _motor_stair_gradient(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        steps = max(2, int(params.get("steps", 6)))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0: return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            step = int(seq_i / n * steps)
            v = step / (steps - 1)
            phase = (v - t * speed) % 1.0
            ef.values[orig_i]["speed"] = _clamp(lo + phase * (hi - lo))

    reg(EffectPreset(
        id="motor_stair_gradient_v32", name="阶梯渐变电机", category="电机/渐变",
        description="电机速度按阶梯分布，形成从慢到快的阶梯图案",
        target_channels=["speed"], icon="▁▂▃▄▅▆▇",
        tags=["motor","gradient","stair","step"],
        params=[
            ParamSpec("speed","移动速度","float",0.5,0.0,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("steps","阶梯数","int",6,2,16,1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_stair_gradient)

    # ═══════════════════════════════════════════════════════════════════
    #  E. 矩阵高级变式
    # ═══════════════════════════════════════════════════════════════════

    # --- E1: 电机矩阵扫描线 ---
    def _motor_matrix_scan_line(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        width = float(params.get("width", 1.5))
        direction = params.get("direction", "horizontal")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        if n == 0: return
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            if direction == "horizontal":
                pos = (t * speed * rows) % rows
                dist = abs(row - pos)
            elif direction == "vertical":
                pos = (t * speed * cols) % cols
                dist = abs(col - pos)
            else:
                pos = (t * speed * (rows + cols)) % (rows + cols)
                dist = abs(row + col - pos)
            v = max(0.0, 1.0 - dist / width)
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_matrix_scan_line_v32", name="电机矩阵扫描线", category="电机/矩阵",
        description="一条高速线在电机矩阵中扫描，可以水平/垂直/对角方向",
        target_channels=["speed"], icon="▦—",
        tags=["motor","matrix","scan","line","sweep"],
        params=[
            ParamSpec("speed","扫描速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","线宽","float",1.5,0.5,5.0,0.5,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("direction","方向","choice","horizontal",choices=["horizontal","vertical","diagonal"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
            ParamSpec("cols","列数","int",4,2,16,1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_matrix_scan_line)

    # --- E2: 电机矩阵噪声 ---
    def _motor_matrix_noise(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        if n == 0: return
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            x = col / cols + t * speed * 0.3
            y = row / rows + t * speed * 0.2
            v = (math.sin(x * 7.3 + t * speed) * math.cos(y * 5.1 + t * speed * 0.7) +
                 math.sin(x * 3.1 + y * 4.7 + t * speed * 1.3) * 0.5 +
                 math.sin(x * 11.3 - y * 8.9 + t * speed * 0.5) * 0.25) / 1.75
            v = v * 0.5 + 0.5
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_matrix_noise_v32", name="电机矩阵噪声", category="电机/矩阵",
        description="电机矩阵产生流动的噪声图案，有机自然的速度变化",
        target_channels=["speed"], icon="▦~",
        tags=["motor","matrix","noise","organic","flow"],
        params=[
            ParamSpec("speed","流动速度","float",0.5,0.1,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols","列数","int",4,2,16,1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_matrix_noise)

    # --- E3: 电机矩阵旋转扫描 ---
    def _motor_matrix_rotate_scan(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        width = float(params.get("width", 0.3))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        if n == 0: return
        rows = max(1, (n + cols - 1) // cols)
        cx = (cols - 1) / 2.0
        cy = (rows - 1) / 2.0
        angle = t * speed * 2 * math.pi
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            dx = col - cx
            dy = row - cy
            cell_angle = math.atan2(dy, dx)
            diff = (cell_angle - angle) % (2 * math.pi)
            if diff > math.pi: diff = 2 * math.pi - diff
            v = max(0.0, 1.0 - diff / (width * math.pi))
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_matrix_rotate_scan_v32", name="电机矩阵旋转扫描", category="电机/矩阵",
        description="高速线从中心旋转扫描，如雷达扫描效果",
        target_channels=["speed"], icon="▦⟳",
        tags=["motor","matrix","rotate","scan","radar"],
        params=[
            ParamSpec("speed","旋转速度","float",0.5,0.1,5.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","扫描宽度","float",0.3,0.05,1.0,0.05,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("cols","列数","int",4,2,16,1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_matrix_rotate_scan)

    # --- E4: 电机矩阵棋盘呼吸 ---
    def _motor_matrix_checker_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        if n == 0: return
        v1 = _sine(t * speed)
        v2 = 1.0 - v1
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            is_white = (row + col) % 2 == 0
            v = v1 if is_white else v2
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_matrix_checker_breath_v32", name="电机矩阵棋盘呼吸", category="电机/矩阵",
        description="棋盘格交替呼吸，奇偶格电机速度交替变化",
        target_channels=["speed"], icon="▦⬛⬜",
        tags=["motor","matrix","checker","breath","alternate"],
        params=[
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols","列数","int",4,2,16,1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_matrix_checker_breath)

    # --- E5: 电机矩阵行波 ---
    def _motor_matrix_row_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        freq = float(params.get("freq", 2.0))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        if n == 0: return
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            v = _sine(row / rows * freq - t * speed)
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_matrix_row_wave_v32", name="电机矩阵行波", category="电机/矩阵",
        description="电机矩阵按行产生波浪，每行速度不同",
        target_channels=["speed"], icon="▦≈",
        tags=["motor","matrix","row","wave"],
        params=[
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("freq","频率","float",2.0,0.5,8.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("cols","列数","int",4,2,16,1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_matrix_row_wave)

    # --- E6: 电机矩阵双螺旋 ---
    def _motor_matrix_double_helix(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        if n == 0: return
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            phase1 = (col / cols * 2 * math.pi * 2 - row / rows * 4 * math.pi - t * speed * 2 * math.pi) % (2 * math.pi)
            phase2 = phase1 + math.pi
            v1 = max(0.0, math.cos(phase1))
            v2 = max(0.0, math.cos(phase2))
            v = max(v1, v2)
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_matrix_double_helix_v32", name="电机矩阵双螺旋", category="电机/矩阵",
        description="电机矩阵产生DNA双螺旋图案，两条螺旋线交替出现",
        target_channels=["speed"], icon="▦⌀",
        tags=["motor","matrix","double","helix","dna","spiral"],
        params=[
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols","列数","int",4,2,16,1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_matrix_double_helix)

    # --- E7: 电机矩阵涌浪 ---
    def _motor_matrix_surge(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        freq = float(params.get("freq", 3.0))
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        if n == 0: return
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            wave = math.sin(col / cols * freq * 2 * math.pi - t * speed * 2 * math.pi)
            wave_height = (wave + 1) / 2.0 * rows
            dist = abs(row - wave_height)
            v = max(0.0, 1.0 - dist / 1.5)
            ef.values[orig_i]["speed"] = _clamp(lo + v * (hi - lo))

    reg(EffectPreset(
        id="motor_matrix_surge_v32", name="电机矩阵涌浪", category="电机/矩阵",
        description="电机矩阵产生涌浪效果，波浪线在矩阵中起伏",
        target_channels=["speed"], icon="▦〜",
        tags=["motor","matrix","surge","wave","ocean"],
        params=[
            ParamSpec("speed","速度","float",1.0,0.1,8.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("freq","频率","float",3.0,0.5,8.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("cols","列数","int",4,2,16,1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("lo","低值","int",0,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",255,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _motor_matrix_surge)

