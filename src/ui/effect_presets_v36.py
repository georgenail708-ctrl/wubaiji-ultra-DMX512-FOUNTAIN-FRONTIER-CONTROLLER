"""
effect_presets_v36.py — Cue 一次性电机动作 v36
=================================================
新增约 80 个 Cue（一次性执行）电机动作，覆盖：
  A. 单轴 Cue 基础系列（到位、扫描、展开、收合等）
  B. 波浪 Cue 系列（波浪展开、波浪收合、波浪过场）
  C. 交叉 Cue 系列（22/44 交叉到位、交叉展开）
  D. 扇形 Cue 系列（扇形展开、收合、定位）
  E. XY 双轴 Cue 系列（圆弧到位、螺旋展开、孔雀开屏等）
  F. 追逐 Cue 系列（追逐过场、中心扩散等）
  G. 相位差 Cue 系列（相位展开、相位复位等）
  H. 综合 Cue 系列（进场、出场、翻转、呼吸到位等）

设计原则：
  - 每个 Cue 动作通过 progress = min(1.0, t / duration) 实现一次性执行
  - duration 参数控制时长（可发送到时间线）
  - 缓动曲线可选（Linear/EaseIn/EaseOut/EaseInOut/Bounce/Elastic）
  - 提供 enable_x / enable_y 独立开关
  - 参数范围宽，可调项丰富
  - 支持整体偏移（offset）、相位差（phase_diff）、反转（invert）等
"""
from __future__ import annotations
import math
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .effect_engine import EffectEngine

# ── 工具函数 ──────────────────────────────────────────────────────────────────
def _clamp(v, lo=0, hi=255):
    return max(lo, min(hi, int(round(v))))

def _clampf(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(v)))

def _deg2dmx(deg, center=128.0):
    return _clamp(center + deg * 255.0 / 360.0)

def _ease(curve: str, p: float) -> float:
    p = _clampf(p)
    if curve == "Linear":
        return p
    elif curve == "EaseIn":
        return p * p
    elif curve == "EaseOut":
        return 1.0 - (1.0 - p) ** 2
    elif curve == "EaseInOut":
        return p * p * (3.0 - 2.0 * p)
    elif curve == "EaseIn3":
        return p * p * p
    elif curve == "EaseOut3":
        return 1.0 - (1.0 - p) ** 3
    elif curve == "EaseInOut3":
        return p * p * p * (p * (p * 6.0 - 15.0) + 10.0)
    elif curve == "Bounce":
        if p < 0.5:
            return 4.0 * p * p
        else:
            return 1.0 - (-2.0 * p + 2.0) ** 2 / 2.0
    elif curve == "Elastic":
        if p == 0.0 or p == 1.0:
            return p
        return -(2.0 ** (10.0 * p - 10.0)) * math.sin((p * 10.0 - 10.75) * 2.0 * math.pi / 3.0) + 1.0
    elif curve == "Back":
        c1 = 1.70158
        c3 = c1 + 1.0
        return 1.0 + c3 * (p - 1.0) ** 3 + c1 * (p - 1.0) ** 2
    elif curve == "Overshoot":
        c1 = 1.70158
        return (c1 + 1.0) * p * p * p - c1 * p * p
    else:
        return p

def _sine_raw(x):
    return math.sin(2.0 * math.pi * x)

EASE_CHOICES = ["Linear", "EaseIn", "EaseOut", "EaseInOut",
                "EaseIn3", "EaseOut3", "EaseInOut3",
                "Bounce", "Elastic", "Back", "Overshoot"]
MCH = ["motor_x", "motor_y", "motor_z", "speed", "pan", "tilt"]

# ═══════════════════════════════════════════════════════════════════════════════
#  注册函数
# ═══════════════════════════════════════════════════════════════════════════════
def _register_v36_presets(engine: "EffectEngine"):
    from .effect_engine import EffectPreset, ParamSpec
    def reg(preset, fn):
        engine._reg(preset, fn)

    # ═══════════════════════════════════════════════════════════════════════
    #  A. 单轴 Cue 基础系列
    # ═══════════════════════════════════════════════════════════════════════

    # A1: 单轴到位 Cue（所有设备同时移动到目标角度）
    def _cue_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        val = _clamp(start + (target - start) * p)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_goto_v36", name="Cue 单轴到位", category="电机/Cue",
        description="所有设备同时移动到目标角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→|",
        tags=["motor","cue","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_goto)

    # A2: 单轴往返 Cue（移动到目标后返回）
    def _cue_goto_return(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        hold     = float(params.get("hold_ratio", 0.3))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        p = min(1.0, t / max(0.001, duration))
        go_time = (1.0 - hold) * 0.5
        if p < go_time:
            pp = _ease(curve, p / max(0.001, go_time))
            val = _clamp(start + (target - start) * pp)
        elif p < go_time + hold:
            val = _clamp(target)
        else:
            pp = _ease(curve, (p - go_time - hold) / max(0.001, go_time))
            val = _clamp(target + (start - target) * pp)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_goto_return_v36", name="Cue 到位返回", category="电机/Cue",
        description="移动到目标后保持，再返回起点，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→|←",
        tags=["motor","cue","goto","return","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("hold_ratio","保持比例","float",0.3,0.0,0.8,0.05,
                tooltip="控制保持阶段占整个时长的比例。值越大中间持续输出越久"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_goto_return)

    # A3: 单轴扫描 Cue（从一端扫到另一端）
    def _cue_scan(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        start    = float(params.get("start_val", 50.0))
        end      = float(params.get("end_val", 200.0))
        curve    = params.get("curve", "Linear")
        ch       = params.get("ch", "motor_x")
        invert   = bool(params.get("invert", False))
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        if invert:
            p = 1.0 - p
        val = _clamp(start + (end - start) * p)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_scan_v36", name="Cue 单轴扫描", category="电机/Cue",
        description="所有设备同步从起点扫到终点，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→→→",
        tags=["motor","cue","scan","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",50.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("end_val","终止值","float",200.0,0.0,255.0,1.0,
                tooltip="控制输出结束值。效果结束时到达此数值"),
            ParamSpec("curve","缓动曲线","choice","Linear",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("invert","反向","bool",False,
                tooltip="控制是否反转效果的方向或极性。反转后输出信号正负颠倒"),
        ],
    ), _cue_scan)

    # A4: 单轴弹跳到位 Cue（弹跳后稳定在目标）
    def _cue_bounce_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.5))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        bounces  = max(1, int(params.get("bounces", 3)))
        decay    = float(params.get("decay", 0.5))
        ch       = params.get("ch", "motor_x")
        p = min(1.0, t / max(0.001, duration))
        # 弹跳衰减
        amp = (1.0 - p) * (target - start)
        bounce = 0.0
        for i in range(bounces):
            freq = (i + 1) * 2.0
            bounce += amp * (decay ** i) * math.sin(p * freq * math.pi)
        val = _clamp(start + (target - start) * p + bounce * (1.0 - p))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_bounce_goto_v36", name="Cue 弹跳到位", category="电机/Cue",
        description="弹跳后稳定在目标角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="↗↘|",
        tags=["motor","cue","bounce","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("bounces","弹跳次数","int",3,1,8,1,
                tooltip="控制弹跳次数。值越大弹跳越多次才停止"),
            ParamSpec("decay","衰减系数","float",0.5,0.1,0.9,0.05,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
        ],
    ), _cue_bounce_goto)

    # A5: 单轴脉冲 Cue（快速脉冲后归位）
    def _cue_pulse(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 1.0))
        center   = float(params.get("center", 128.0))
        amp      = float(params.get("amp", 60.0))
        pulses   = max(1, int(params.get("pulses", 2)))
        ch       = params.get("ch", "motor_x")
        p = min(1.0, t / max(0.001, duration))
        # 多次脉冲，逐渐衰减
        val_offset = 0.0
        for i in range(pulses):
            pulse_p = (p - i / pulses) * pulses
            if 0.0 <= pulse_p <= 1.0:
                val_offset += amp * math.sin(pulse_p * math.pi) * ((pulses - i) / pulses)
        val = _clamp(center + val_offset * 127.0 / 90.0)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_pulse_v36", name="Cue 脉冲", category="电机/Cue",
        description="多次脉冲后归位，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="↑↑|",
        tags=["motor","cue","pulse","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",1.0,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("pulses","脉冲次数","int",2,1,8,1,
                tooltip="控制脉冲数量。值越大单个周期内产生的脉冲越密集"),
        ],
    ), _cue_pulse)

    # A6: 单轴随机到位 Cue（每台设备随机目标角度）
    def _cue_random_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        min_val  = float(params.get("min_val", 60.0))
        max_val  = float(params.get("max_val", 200.0))
        start    = float(params.get("start_val", 128.0))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        seed     = int(params.get("seed", 42))
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            h = ((seed + seq_i * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff
            target = min_val + h * (max_val - min_val)
            val = _clamp(start + (target - start) * p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_random_goto_v36", name="Cue 随机到位", category="电机/Cue",
        description="每台设备随机移动到不同目标角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="?→|",
        tags=["motor","cue","random","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("min_val","最小目标","float",60.0,0.0,255.0,1.0,
                tooltip="控制允许的最小值。限制参数的最低边界，防止输出过低"),
            ParamSpec("max_val","最大目标","float",200.0,0.0,255.0,1.0,
                tooltip="控制允许的最大值。限制参数的最高边界，防止输出过高"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("seed","随机种子","int",42,0,999,1,
                tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
        ],
    ), _cue_random_goto)

    # A7: 单轴阶梯到位 Cue（每台设备按顺序依次到位）
    def _cue_stagger_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        delay    = float(params.get("delay_ratio", 0.15))
        curve    = params.get("curve", "EaseOut")
        ch       = params.get("ch", "motor_x")
        invert   = bool(params.get("invert", False))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            idx = (n - 1 - seq_i) if invert else seq_i
            t_start = idx * delay * duration / max(1, n - 1)
            t_move = duration - t_start
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, t_move))))
            val = _clamp(start + (target - start) * local_p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_stagger_goto_v36", name="Cue 阶梯到位", category="电机/Cue",
        description="每台设备按顺序依次移动到目标角度，形成瀑布效果。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="▶▶▶|",
        tags=["motor","cue","stagger","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("delay_ratio","延迟比例","float",0.15,0.0,0.5,0.01,
                tooltip="控制延迟量相对于周期的比例。值越大设备间错位感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("invert","反向","bool",False,
                tooltip="控制是否反转效果的方向或极性。反转后输出信号正负颠倒"),
        ],
    ), _cue_stagger_goto)

    # A8: 单轴中心扩散到位 Cue（从中心向两侧依次到位）
    def _cue_center_spread(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        delay    = float(params.get("delay_ratio", 0.2))
        curve    = params.get("curve", "EaseOut")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1.0, mid)
            t_start = dist * delay * duration
            t_move = duration - t_start
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, t_move))))
            val = _clamp(start + (target - start) * local_p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_center_spread_v36", name="Cue 中心扩散到位", category="电机/Cue",
        description="从中心向两侧依次到位，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="|→→→",
        tags=["motor","cue","center","spread","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("delay_ratio","延迟比例","float",0.2,0.0,0.5,0.01,
                tooltip="控制延迟量相对于周期的比例。值越大设备间错位感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_center_spread)

    # A9: 单轴渐变展开 Cue（每台设备目标角度按序号线性分布）
    def _cue_linear_spread(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.5))
        start    = float(params.get("start_val", 128.0))
        min_tgt  = float(params.get("min_target", 60.0))
        max_tgt  = float(params.get("max_target", 200.0))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        invert   = bool(params.get("invert", False))
        n = len(sorted_fx)
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            if invert:
                ratio = 1.0 - ratio
            target = min_tgt + ratio * (max_tgt - min_tgt)
            val = _clamp(start + (target - start) * p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_linear_spread_v36", name="Cue 线性展开", category="电机/Cue",
        description="每台设备目标角度按序号线性分布，同时到位，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="↗↗↗",
        tags=["motor","cue","linear","spread","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("min_target","最小目标","float",60.0,0.0,255.0,1.0,
                tooltip="控制目标范围的下限。定义目标值可变化的最低边界"),
            ParamSpec("max_target","最大目标","float",200.0,0.0,255.0,1.0,
                tooltip="控制目标范围的上限。定义目标值可变化的最高边界"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("invert","反向","bool",False,
                tooltip="控制是否反转效果的方向或极性。反转后输出信号正负颠倒"),
        ],
    ), _cue_linear_spread)

    # A10: 单轴镜像展开 Cue（从中心向两侧对称展开）
    def _cue_mirror_spread(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.5))
        center   = float(params.get("center_val", 128.0))
        amp      = float(params.get("amp", 80.0))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = (seq_i - mid) / max(1.0, mid)
            target = center + dist * amp * 127.0 / 90.0
            val = _clamp(center + (target - center) * p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_mirror_spread_v36", name="Cue 镜像展开", category="电机/Cue",
        description="从中心向两侧对称展开，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="←|→",
        tags=["motor","cue","mirror","spread","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("center_val","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制居中位置时的输出值。设备回到中心时输出此值"),
            ParamSpec("amp","展开幅度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_mirror_spread)

    # ═══════════════════════════════════════════════════════════════════════
    #  B. 波浪 Cue 系列
    # ═══════════════════════════════════════════════════════════════════════

    # B1: 波浪展开 Cue（波浪从一端展开到另一端）
    def _cue_wave_spread(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        amp      = float(params.get("amp", 60.0))
        center   = float(params.get("center", 128.0))
        phase_diff= float(params.get("phase_diff", 0.2))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p_global = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            # 每台设备的波浪相位
            phase = ratio * phase_diff * n * 0.5
            wave = math.sin(2.0 * math.pi * (p_global - phase))
            val = _clamp(center + amp * wave * p_global * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_wave_spread_v36", name="Cue 波浪展开", category="电机/Cue",
        description="波浪从一端展开到另一端，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≈→",
        tags=["motor","cue","wave","spread","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_diff","设备间相位差","float",0.2,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_wave_spread)

    # B2: 波浪过场 Cue（波浪从左到右扫过一次）
    def _cue_wave_pass(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        amp      = float(params.get("amp", 60.0))
        center   = float(params.get("center", 128.0))
        width    = float(params.get("width", 0.3))
        ch       = params.get("ch", "motor_x")
        invert   = bool(params.get("invert", False))
        n = len(sorted_fx)
        p = min(1.0, t / max(0.001, duration))
        if invert:
            p = 1.0 - p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            dist = abs(ratio - p)
            dist = min(dist, 1.0 - dist)
            wave = max(0.0, 1.0 - dist / max(0.01, width))
            val = _clamp(center + amp * wave * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_wave_pass_v36", name="Cue 波浪过场", category="电机/Cue",
        description="波浪从一端扫过到另一端，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≈→→",
        tags=["motor","cue","wave","pass","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("width","波宽","float",0.3,0.05,1.0,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("invert","反向","bool",False,
                tooltip="控制是否反转效果的方向或极性。反转后输出信号正负颠倒"),
        ],
    ), _cue_wave_pass)

    # B3: 波浪收合 Cue（波浪从两端向中心收合）
    def _cue_wave_gather(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.5))
        amp      = float(params.get("amp", 60.0))
        center   = float(params.get("center", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p_global = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1.0, mid)
            # 越靠外越先到达中心
            t_arrive = dist * phase_diff * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_arrive) / max(0.001, duration - t_arrive))))
            val = _clamp(center + amp * (1.0 - local_p) * (1.0 if seq_i >= mid else -1.0) * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_wave_gather_v36", name="Cue 波浪收合", category="电机/Cue",
        description="波浪从两端向中心收合，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→|←",
        tags=["motor","cue","wave","gather","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_diff","相位差","float",0.15,0.0,0.5,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_wave_gather)

    # B4: 波浪相位展开 Cue（相位差从0逐渐增大，波浪从同步变为追逐）
    def _cue_phase_expand(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 4.0))
        speed     = float(params.get("speed", 1.5))
        amp       = float(params.get("amp", 60.0))
        center    = float(params.get("center", 128.0))
        max_phase = float(params.get("max_phase", 0.3))
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = min(1.0, t / max(0.001, duration))
        phase_now = p * max_phase  # 相位差从0增大
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_now
            val = _clamp(center + amp * math.sin(2.0 * math.pi * phase) * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_phase_expand_v36", name="Cue 相位展开", category="电机/Cue",
        description="相位差从0逐渐增大，波浪从同步变为追逐，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≡→≈",
        tags=["motor","cue","phase","expand","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",4.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("speed","波浪速度","float",1.5,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("max_phase","最大相位差","float",0.3,0.0,1.0,0.01,
                tooltip="控制允许的最大相位差。值越大设备间的最终错位越远"),
        ],
    ), _cue_phase_expand)

    # B5: 波浪相位收合 Cue（相位差从最大逐渐减小到0，追逐变为同步）
    def _cue_phase_gather(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 4.0))
        speed     = float(params.get("speed", 1.5))
        amp       = float(params.get("amp", 60.0))
        center    = float(params.get("center", 128.0))
        start_phase= float(params.get("start_phase", 0.3))
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = min(1.0, t / max(0.001, duration))
        phase_now = start_phase * (1.0 - p)  # 相位差从max减小到0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_now
            val = _clamp(center + amp * math.sin(2.0 * math.pi * phase) * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_phase_gather_v36", name="Cue 相位收合", category="电机/Cue",
        description="相位差从最大逐渐减小到0，追逐变为同步，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≈→≡",
        tags=["motor","cue","phase","gather","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",4.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("speed","波浪速度","float",1.5,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("start_phase","起始相位差","float",0.3,0.0,1.0,0.01,
                tooltip="控制效果的起始相位。决定效果在波形周期上的初始位置"),
        ],
    ), _cue_phase_gather)

    # B6: 波浪幅度展开 Cue（幅度从0增大到最大）
    def _cue_amp_expand(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        speed     = float(params.get("speed", 1.5))
        max_amp   = float(params.get("max_amp", 80.0))
        center    = float(params.get("center", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        curve     = params.get("curve", "EaseIn")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        amp_now = max_amp * p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            val = _clamp(center + amp_now * math.sin(2.0 * math.pi * phase) * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_amp_expand_v36", name="Cue 幅度展开", category="电机/Cue",
        description="波浪幅度从0增大到最大，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≈↑",
        tags=["motor","cue","amp","expand","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("speed","波浪速度","float",1.5,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("max_amp","最大幅度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制幅度的最大值上限。限制效果的幅度不超过此值"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseIn",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_amp_expand)

    # B7: 波浪幅度收合 Cue（幅度从最大减小到0）
    def _cue_amp_gather(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        speed     = float(params.get("speed", 1.5))
        start_amp = float(params.get("start_amp", 80.0))
        center    = float(params.get("center", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        curve     = params.get("curve", "EaseOut")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        amp_now = start_amp * (1.0 - p)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            val = _clamp(center + amp_now * math.sin(2.0 * math.pi * phase) * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_amp_gather_v36", name="Cue 幅度收合", category="电机/Cue",
        description="波浪幅度从最大减小到0，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≈↓",
        tags=["motor","cue","amp","gather","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("speed","波浪速度","float",1.5,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("start_amp","起始幅度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制起始幅度值。决定效果刚开始时的变化强度"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_amp_gather)

    # ═══════════════════════════════════════════════════════════════════════
    #  C. 交叉 Cue 系列
    # ═══════════════════════════════════════════════════════════════════════

    # C1: 22交叉到位 Cue（22交叉同时到位）
    def _cue_cross22_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        amp      = float(params.get("amp", 60.0))
        center   = float(params.get("center", 128.0))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        gphase   = float(params.get("group_delay", 0.0))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            t_start = group_idx * gphase * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            val = _clamp(center + direction * amp * local_p * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_cross22_goto_v36", name="Cue 22交叉到位", category="电机/Cue",
        description="22交叉同时到位，一次性执行。组间延迟可调。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⇄2|",
        tags=["motor","cue","cross","22","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("group_delay","组间延迟比","float",0.0,0.0,0.5,0.01,
                tooltip="控制组与组之间的延迟量。值越大各组启动的时间间隔越远"),
        ],
    ), _cue_cross22_goto)

    # C2: 44交叉到位 Cue
    def _cue_cross44_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        amp      = float(params.get("amp", 60.0))
        center   = float(params.get("center", 128.0))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        gphase   = float(params.get("group_delay", 0.0))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 4
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            t_start = group_idx * gphase * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            val = _clamp(center + direction * amp * local_p * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_cross44_goto_v36", name="Cue 44交叉到位", category="电机/Cue",
        description="44交叉同时到位，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⇄4|",
        tags=["motor","cue","cross","44","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("group_delay","组间延迟比","float",0.0,0.0,0.5,0.01,
                tooltip="控制组与组之间的延迟量。值越大各组启动的时间间隔越远"),
        ],
    ), _cue_cross44_goto)

    # C3: 22交叉展开 Cue（22交叉从中心展开）
    def _cue_cross22_spread(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.5))
        amp      = float(params.get("amp", 60.0))
        center   = float(params.get("center", 128.0))
        curve    = params.get("curve", "EaseOut")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            dist = abs(seq_i - mid) / max(1.0, mid)
            val = _clamp(center + direction * amp * dist * p * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_cross22_spread_v36", name="Cue 22交叉展开", category="电机/Cue",
        description="22交叉从中心向外展开，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⇄2↔",
        tags=["motor","cue","cross","22","spread","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_cross22_spread)

    # C4: N组交叉到位 Cue（可设置N，通用交叉到位）
    def _cue_crossN_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        amp      = float(params.get("amp", 60.0))
        center   = float(params.get("center", 128.0))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        group_n  = max(1, int(params.get("group_n", 2)))
        gphase   = float(params.get("group_delay", 0.0))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // group_n
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            t_start = group_idx * gphase * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            val = _clamp(center + direction * amp * local_p * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_crossN_goto_v36", name="Cue N组交叉到位", category="电机/Cue",
        description="可设置N，每N个一组交叉到位，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⇄N|",
        tags=["motor","cue","cross","N","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("group_n","每组数量","int",2,1,16,1,
                tooltip="控制每组包含的设备数量。值越大同组设备越多，组间变换越平滑"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("group_delay","组间延迟比","float",0.0,0.0,0.5,0.01,
                tooltip="控制组与组之间的延迟量。值越大各组启动的时间间隔越远"),
        ],
    ), _cue_crossN_goto)

    # ═══════════════════════════════════════════════════════════════════════
    #  D. 扇形 Cue 系列
    # ═══════════════════════════════════════════════════════════════════════

    # D1: 扇形展开 Cue（从中心展开到目标角度）
    def _cue_fan_open(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 2.5))
        fan_angle = float(params.get("fan_angle", 80.0))
        offset    = float(params.get("offset", 0.0))
        curve     = params.get("curve", "EaseOut")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            angle = ratio * fan_angle * 0.5 * p + offset
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_fan_open_v36", name="Cue 扇形展开", category="电机/Cue",
        description="从中心展开到目标扇形角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃→⊂",
        tags=["motor","cue","fan","open","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("offset","整体偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制效果在设备序列上的偏移位置。值越大起始点越靠后"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_fan_open)

    # D2: 扇形收合 Cue（从展开收合到中心）
    def _cue_fan_close(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 2.5))
        fan_angle = float(params.get("fan_angle", 80.0))
        offset    = float(params.get("offset", 0.0))
        curve     = params.get("curve", "EaseIn")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            angle = ratio * fan_angle * 0.5 * (1.0 - p) + offset
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_fan_close_v36", name="Cue 扇形收合", category="电机/Cue",
        description="从展开状态收合到中心，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃←⊂",
        tags=["motor","cue","fan","close","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("offset","整体偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制效果在设备序列上的偏移位置。值越大起始点越靠后"),
            ParamSpec("curve","缓动曲线","choice","EaseIn",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_fan_close)

    # D3: 扇形定位 Cue（扇形整体移动到目标偏移角度）
    def _cue_fan_position(ef, t, sorted_fx, fdefs, params, fps):
        duration     = float(params.get("duration", 2.0))
        fan_angle    = float(params.get("fan_angle", 60.0))
        start_offset = float(params.get("start_offset", 0.0))
        target_offset= float(params.get("target_offset", 30.0))
        curve        = params.get("curve", "EaseInOut")
        ch           = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        offset_now = start_offset + (target_offset - start_offset) * p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            angle = ratio * fan_angle * 0.5 + offset_now
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_fan_position_v36", name="Cue 扇形定位", category="电机/Cue",
        description="扇形保持展开角度，整体移动到目标偏移位置，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃→⊂→",
        tags=["motor","cue","fan","position","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("start_offset","起始偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制起始位置的偏移量。使起始点偏离原始位置"),
            ParamSpec("target_offset","目标偏移(度)","float",30.0,-90.0,90.0,1.0,
                tooltip="控制目标位置的偏移量。使目标点偏离原始位置"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_fan_position)

    # D4: 多扇展开 Cue（多个扇形同时展开，相位差可调）
    def _cue_multi_fan_open(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        fans      = max(2, int(params.get("fans", 3)))
        fan_angle = float(params.get("fan_angle", 60.0))
        fan_phase = float(params.get("fan_phase_diff", 0.0))
        offset    = float(params.get("offset", 0.0))
        curve     = params.get("curve", "EaseOut")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            t_start = fan_idx * fan_phase * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            angle = local_ratio * fan_angle * 0.5 * local_p + offset
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_multi_fan_open_v36", name="Cue 多扇展开", category="电机/Cue",
        description="多个扇形同时展开，扇间延迟可调，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃⊃→⊂⊂",
        tags=["motor","cue","fan","multi","open","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",3,2,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("fan_phase_diff","扇间延迟比","float",0.0,0.0,0.5,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("offset","整体偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制效果在设备序列上的偏移位置。值越大起始点越靠后"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_multi_fan_open)

    # D5: 多扇交替展开 Cue（奇数扇先展开，偶数扇后展开）
    def _cue_alt_fan_open(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.5))
        fans      = max(2, int(params.get("fans", 4)))
        fan_angle = float(params.get("fan_angle", 60.0))
        delay     = float(params.get("delay_ratio", 0.3))
        curve     = params.get("curve", "EaseOut")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 奇偶扇交替延迟
            t_start = (0.0 if fan_idx % 2 == 0 else delay) * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            angle = local_ratio * fan_angle * 0.5 * local_p
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_alt_fan_open_v36", name="Cue 交替扇形展开", category="电机/Cue",
        description="奇数扇先展开，偶数扇后展开，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃↔⊂",
        tags=["motor","cue","fan","alternate","open","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",4,2,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("duration","时长(秒)","float",3.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("delay_ratio","延迟比例","float",0.3,0.0,0.8,0.05,
                tooltip="控制延迟量相对于周期的比例。值越大设备间错位感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_alt_fan_open)

    # D6: 扇形漂移到位 Cue（扇形展开同时整体漂移到目标位置）
    def _cue_fan_drift_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration     = float(params.get("duration", 3.0))
        fan_angle    = float(params.get("fan_angle", 60.0))
        start_offset = float(params.get("start_offset", -40.0))
        target_offset= float(params.get("target_offset", 40.0))
        curve        = params.get("curve", "EaseInOut")
        ch           = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        offset_now = start_offset + (target_offset - start_offset) * p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            angle = ratio * fan_angle * 0.5 * p + offset_now
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_fan_drift_goto_v36", name="Cue 扇形漂移到位", category="电机/Cue",
        description="扇形展开同时整体从起始位置漂移到目标位置，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃→→⊂",
        tags=["motor","cue","fan","drift","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("start_offset","起始偏移(度)","float",-40.0,-180.0,180.0,1.0,
                tooltip="控制起始位置的偏移量。使起始点偏离原始位置"),
            ParamSpec("target_offset","目标偏移(度)","float",40.0,-180.0,180.0,1.0,
                tooltip="控制目标位置的偏移量。使目标点偏离原始位置"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_fan_drift_goto)

    # ═══════════════════════════════════════════════════════════════════════
    #  E. XY 双轴 Cue 系列
    # ═══════════════════════════════════════════════════════════════════════

    # E1: XY 同时到位 Cue
    def _cue_xy_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.5))
        start_x  = float(params.get("start_x", 128.0))
        target_x = float(params.get("target_x", 200.0))
        start_y  = float(params.get("start_y", 128.0))
        target_y = float(params.get("target_y", 160.0))
        curve    = params.get("curve", "EaseInOut")
        xy_phase = float(params.get("xy_phase", 0.0))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        p = min(1.0, t / max(0.001, duration))
        px = _ease(curve, min(1.0, p))
        py = _ease(curve, min(1.0, max(0.0, p - xy_phase)))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(start_x + (target_x - start_x) * px)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(start_y + (target_y - start_y) * py)
    reg(EffectPreset(
        id="motor_cue_xy_goto_v36", name="Cue XY同时到位", category="电机/Cue",
        description="XY双轴同时移动到目标位置，XY相位差可调，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY→|",
        tags=["motor","cue","xy","goto","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_x","X起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 X 轴运动的起始位置。决定水平运动的出发点"),
            ParamSpec("target_x","X目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制 X 轴的目标位置。决定水平方向上的去向"),
            ParamSpec("start_y","Y起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 Y 轴运动的起始位置。决定垂直运动的出发点"),
            ParamSpec("target_y","Y目标值","float",160.0,0.0,255.0,1.0,
                tooltip="控制 Y 轴的目标位置。决定垂直方向上的去向"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("xy_phase","XY相位差","float",0.0,0.0,0.5,0.01,
                tooltip="控制 X 与 Y 轴运动的相位差。值越大两轴运动的不同步程度越高"),
        ],
    ), _cue_xy_goto)

    # E2: XY 圆弧到位 Cue（沿圆弧路径移动到目标）
    def _cue_xy_arc(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        cx       = float(params.get("center_x", 128.0))
        cy       = float(params.get("center_y", 128.0))
        radius_x = float(params.get("radius_x", 50.0))
        radius_y = float(params.get("radius_y", 50.0))
        start_ang= float(params.get("start_angle", 0.0))
        end_ang  = float(params.get("end_angle", 90.0))
        curve    = params.get("curve", "EaseInOut")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        angle_now = math.radians(start_ang + (end_ang - start_ang) * p)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(cx + radius_x * math.cos(angle_now))
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(cy + radius_y * math.sin(angle_now))
    reg(EffectPreset(
        id="motor_cue_xy_arc_v36", name="Cue XY圆弧到位", category="电机/Cue",
        description="沿圆弧路径移动到目标角度，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY⌒→",
        tags=["motor","cue","xy","arc","goto","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("radius_x","X半径","float",50.0,0.0,127.0,1.0,
                tooltip="控制 X 轴方向的椭圆半径。决定水平方向上的运动范围"),
            ParamSpec("radius_y","Y半径","float",50.0,0.0,127.0,1.0,
                tooltip="控制 Y 轴方向的椭圆半径。决定垂直方向上的运动范围"),
            ParamSpec("start_angle","起始角度(度)","float",0.0,-360.0,360.0,1.0,
                tooltip="控制起始方位角。决定螺旋或圆弧运动的起点"),
            ParamSpec("end_angle","终止角度(度)","float",90.0,-360.0,360.0,1.0,
                tooltip="控制终止方位角。决定螺旋或圆弧运动的终点"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_xy_arc)

    # E3: XY 扇形展开 Cue（XY双轴同时展开扇形）
    def _cue_xy_fan_open(ef, t, sorted_fx, fdefs, params, fps):
        duration    = float(params.get("duration", 3.0))
        fan_angle_x = float(params.get("fan_angle_x", 80.0))
        fan_angle_y = float(params.get("fan_angle_y", 40.0))
        xy_phase    = float(params.get("xy_phase", 0.0))
        offset_x    = float(params.get("offset_x", 0.0))
        offset_y    = float(params.get("offset_y", 0.0))
        curve       = params.get("curve", "EaseOut")
        en_x        = bool(params.get("enable_x", True))
        en_y        = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p_base = min(1.0, t / max(0.001, duration))
        px = _ease(curve, p_base)
        py = _ease(curve, min(1.0, max(0.0, p_base - xy_phase)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            if en_x:
                ef.values[orig_i]["motor_x"] = _deg2dmx(ratio * fan_angle_x * 0.5 * px + offset_x)
            if en_y:
                ef.values[orig_i]["motor_y"] = _deg2dmx(abs(ratio) * fan_angle_y * 0.5 * py + offset_y)
    reg(EffectPreset(
        id="motor_cue_xy_fan_open_v36", name="Cue XY扇形展开", category="电机/Cue",
        description="XY双轴同时展开扇形，XY相位差可调，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY⊃→⊂",
        tags=["motor","cue","xy","fan","open","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle_x","X扇形角度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的扇形展开角度。值越大水平扇面越宽"),
            ParamSpec("fan_angle_y","Y扇形角度(度)","float",40.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的扇形展开角度。值越大垂直扇面越宽"),
            ParamSpec("xy_phase","XY相位差","float",0.0,0.0,0.5,0.01,
                tooltip="控制 X 与 Y 轴运动的相位差。值越大两轴运动的不同步程度越高"),
            ParamSpec("offset_x","X偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制 X 轴方向的偏移量。值越大水平偏移越远"),
            ParamSpec("offset_y","Y偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制 Y 轴方向的偏移量。值越大垂直偏移越远"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_xy_fan_open)

    # E4: XY 阶梯到位 Cue（每台设备XY按顺序依次到位）
    def _cue_xy_stagger(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 4.0))
        target_x = float(params.get("target_x", 180.0))
        target_y = float(params.get("target_y", 160.0))
        start_x  = float(params.get("start_x", 128.0))
        start_y  = float(params.get("start_y", 128.0))
        delay    = float(params.get("delay_ratio", 0.15))
        curve    = params.get("curve", "EaseOut")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            t_start = seq_i * delay * duration / max(1, n - 1)
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(start_x + (target_x - start_x) * local_p)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(start_y + (target_y - start_y) * local_p)
    reg(EffectPreset(
        id="motor_cue_xy_stagger_v36", name="Cue XY阶梯到位", category="电机/Cue",
        description="每台设备XY按顺序依次到位，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY▶▶▶|",
        tags=["motor","cue","xy","stagger","goto","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",4.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_x","X起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 X 轴运动的起始位置。决定水平运动的出发点"),
            ParamSpec("target_x","X目标值","float",180.0,0.0,255.0,1.0,
                tooltip="控制 X 轴的目标位置。决定水平方向上的去向"),
            ParamSpec("start_y","Y起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 Y 轴运动的起始位置。决定垂直运动的出发点"),
            ParamSpec("target_y","Y目标值","float",160.0,0.0,255.0,1.0,
                tooltip="控制 Y 轴的目标位置。决定垂直方向上的去向"),
            ParamSpec("delay_ratio","延迟比例","float",0.15,0.0,0.5,0.01,
                tooltip="控制延迟量相对于周期的比例。值越大设备间错位感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_xy_stagger)

    # E5: XY 螺旋展开 Cue（XY沿螺旋路径展开）
    def _cue_xy_spiral(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 4.0))
        cx       = float(params.get("center_x", 128.0))
        cy       = float(params.get("center_y", 128.0))
        max_r_x  = float(params.get("max_radius_x", 60.0))
        max_r_y  = float(params.get("max_radius_y", 40.0))
        turns    = float(params.get("turns", 2.0))
        phase_diff= float(params.get("phase_diff", 0.1))
        curve    = params.get("curve", "EaseInOut")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            local_t = max(0.0, t - seq_i * phase_diff * duration / max(1, n))
            p = _ease(curve, min(1.0, local_t / max(0.001, duration)))
            angle = 2.0 * math.pi * turns * p
            r_x = max_r_x * p
            r_y = max_r_y * p
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(cx + r_x * math.cos(angle))
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(cy + r_y * math.sin(angle))
    reg(EffectPreset(
        id="motor_cue_xy_spiral_v36", name="Cue XY螺旋展开", category="电机/Cue",
        description="XY沿螺旋路径展开，圈数/半径/相位差可调，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY🌀→",
        tags=["motor","cue","xy","spiral","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",4.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("max_radius_x","X最大半径","float",60.0,0.0,127.0,1.0,
                tooltip="控制 X 轴方向的最大运动半径。限制水平活动范围"),
            ParamSpec("max_radius_y","Y最大半径","float",40.0,0.0,127.0,1.0,
                tooltip="控制 Y 轴方向的最大运动半径。限制垂直活动范围"),
            ParamSpec("turns","圈数","float",2.0,0.25,10.0,0.25,
                tooltip="控制旋转圈数。值越大旋转的总圈数越多"),
            ParamSpec("phase_diff","设备间相位差","float",0.1,0.0,0.5,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_xy_spiral)

    # ═══════════════════════════════════════════════════════════════════════
    #  F. 追逐 Cue 系列
    # ═══════════════════════════════════════════════════════════════════════

    # F1: 追逐过场 Cue（追逐光点从一端扫过一次）
    def _cue_chase_pass(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        amp      = float(params.get("amp", 80.0))
        center   = float(params.get("center", 128.0))
        width    = float(params.get("width", 0.25))
        ch       = params.get("ch", "motor_x")
        invert   = bool(params.get("invert", False))
        curve    = params.get("curve", "Linear")
        n = len(sorted_fx)
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        if invert:
            p = 1.0 - p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            dist = abs(ratio - p)
            dist = min(dist, 1.0 - dist)
            intensity = max(0.0, 1.0 - dist / max(0.01, width))
            val = _clamp(center + amp * intensity * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_chase_pass_v36", name="Cue 追逐过场", category="电机/Cue",
        description="追逐光点从一端扫过一次，宽度/幅度/方向可调，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→→→",
        tags=["motor","cue","chase","pass","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("width","追逐宽度","float",0.25,0.01,1.0,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("curve","缓动曲线","choice","Linear",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("invert","反向","bool",False,
                tooltip="控制是否反转效果的方向或极性。反转后输出信号正负颠倒"),
        ],
    ), _cue_chase_pass)

    # F2: 中心扩散 Cue（从中心向两侧扩散一次）
    def _cue_center_expand(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        amp      = float(params.get("amp", 80.0))
        center   = float(params.get("center", 128.0))
        curve    = params.get("curve", "EaseOut")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1.0, mid)
            # 越靠近中心越先被激活
            intensity = max(0.0, 1.0 - abs(dist - p) / max(0.01, 0.3))
            val = _clamp(center + amp * intensity * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_center_expand_v36", name="Cue 中心扩散", category="电机/Cue",
        description="从中心向两侧扩散一次，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="|→→",
        tags=["motor","cue","center","expand","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_center_expand)

    # F3: 双向追逐 Cue（两个追逐点从两端向中心汇聚）
    def _cue_dual_chase(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.5))
        amp      = float(params.get("amp", 80.0))
        center   = float(params.get("center", 128.0))
        width    = float(params.get("width", 0.2))
        ch       = params.get("ch", "motor_x")
        curve    = params.get("curve", "EaseInOut")
        n = len(sorted_fx)
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        # 两个追逐点从两端向中心
        pos1 = p * 0.5
        pos2 = 1.0 - p * 0.5
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            d1 = abs(ratio - pos1)
            d2 = abs(ratio - pos2)
            i1 = max(0.0, 1.0 - d1 / max(0.01, width))
            i2 = max(0.0, 1.0 - d2 / max(0.01, width))
            intensity = max(i1, i2)
            val = _clamp(center + amp * intensity * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_dual_chase_v36", name="Cue 双向追逐汇聚", category="电机/Cue",
        description="两个追逐点从两端向中心汇聚，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→|←",
        tags=["motor","cue","chase","dual","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("width","追逐宽度","float",0.2,0.01,1.0,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_dual_chase)

    # ═══════════════════════════════════════════════════════════════════════
    #  G. 进场/出场 Cue 系列
    # ═══════════════════════════════════════════════════════════════════════

    # G1: 进场 Cue（从边缘到中心展开）
    def _cue_enter(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        start_x  = float(params.get("start_x", 50.0))
        end_x    = float(params.get("end_x", 128.0))
        start_y  = float(params.get("start_y", 50.0))
        end_y    = float(params.get("end_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.1))
        curve    = params.get("curve", "EaseOut")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", False))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            t_start = seq_i * phase_diff * duration / max(1, n)
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(start_x + (end_x - start_x) * local_p)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(start_y + (end_y - start_y) * local_p)
    reg(EffectPreset(
        id="motor_cue_enter_v36", name="Cue 进场", category="电机/Cue",
        description="设备依次从边缘位置移动到中心位置，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="→→|",
        tags=["motor","cue","enter","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_x","X起始值","float",50.0,0.0,255.0,1.0,
                tooltip="控制 X 轴运动的起始位置。决定水平运动的出发点"),
            ParamSpec("end_x","X终止值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 X 轴运动的终止位置。决定水平运动的结束点"),
            ParamSpec("start_y","Y起始值","float",50.0,0.0,255.0,1.0,
                tooltip="控制 Y 轴运动的起始位置。决定垂直运动的出发点"),
            ParamSpec("end_y","Y终止值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 Y 轴运动的终止位置。决定垂直运动的结束点"),
            ParamSpec("phase_diff","设备间相位差","float",0.1,0.0,0.5,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_enter)

    # G2: 出场 Cue（从中心向边缘散开）
    def _cue_exit(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        start_x  = float(params.get("start_x", 128.0))
        end_x    = float(params.get("end_x", 200.0))
        start_y  = float(params.get("start_y", 128.0))
        end_y    = float(params.get("end_y", 200.0))
        phase_diff= float(params.get("phase_diff", 0.1))
        curve    = params.get("curve", "EaseIn")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", False))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            t_start = seq_i * phase_diff * duration / max(1, n)
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(start_x + (end_x - start_x) * local_p)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(start_y + (end_y - start_y) * local_p)
    reg(EffectPreset(
        id="motor_cue_exit_v36", name="Cue 出场", category="电机/Cue",
        description="设备依次从中心位置移动到边缘位置，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="|→→",
        tags=["motor","cue","exit","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_x","X起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 X 轴运动的起始位置。决定水平运动的出发点"),
            ParamSpec("end_x","X终止值","float",200.0,0.0,255.0,1.0,
                tooltip="控制 X 轴运动的终止位置。决定水平运动的结束点"),
            ParamSpec("start_y","Y起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 Y 轴运动的起始位置。决定垂直运动的出发点"),
            ParamSpec("end_y","Y终止值","float",200.0,0.0,255.0,1.0,
                tooltip="控制 Y 轴运动的终止位置。决定垂直运动的结束点"),
            ParamSpec("phase_diff","设备间相位差","float",0.1,0.0,0.5,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseIn",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_exit)

    # G3: 翻转 Cue（所有设备从一个角度翻转到反向角度）
    def _cue_flip(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 1.5))
        start    = float(params.get("start_val", 60.0))
        end      = float(params.get("end_val", 200.0))
        phase_diff= float(params.get("phase_diff", 0.05))
        curve    = params.get("curve", "EaseInOut3")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            t_start = seq_i * phase_diff * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            val = _clamp(start + (end - start) * local_p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_flip_v36", name="Cue 翻转", category="电机/Cue",
        description="所有设备快速从一个角度翻转到反向角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="↔|",
        tags=["motor","cue","flip","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",1.5,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",60.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("end_val","终止值","float",200.0,0.0,255.0,1.0,
                tooltip="控制输出结束值。效果结束时到达此数值"),
            ParamSpec("phase_diff","设备间相位差","float",0.05,0.0,0.3,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut3",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_flip)

    # G4: 呼吸到位 Cue（呼吸几次后稳定在目标）
    def _cue_breath_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 4.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        breaths  = max(1, int(params.get("breaths", 3)))
        ch       = params.get("ch", "motor_x")
        p = min(1.0, t / max(0.001, duration))
        # 呼吸衰减到目标
        breath_amp = (1.0 - p) * abs(target - start) * 0.5
        breath = breath_amp * math.sin(p * breaths * 2.0 * math.pi)
        trend = start + (target - start) * p * p
        val = _clamp(trend + breath)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_breath_goto_v36", name="Cue 呼吸到位", category="电机/Cue",
        description="呼吸几次后逐渐稳定在目标角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="♡→|",
        tags=["motor","cue","breath","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",4.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("breaths","呼吸次数","int",3,1,8,1,
                tooltip="控制呼吸起伏的次数。值越大一呼一吸的循环越多"),
        ],
    ), _cue_breath_goto)

    # G5: 龙摆进场 Cue（龙摆从一端扫过一次后稳定）
    def _cue_dragon_enter(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        amp       = float(params.get("amp", 60.0))
        center    = float(params.get("center", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        speed     = float(params.get("speed", 1.5))
        target    = float(params.get("target_val", 128.0))
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = min(1.0, t / max(0.001, duration))
        amp_now = amp * (1.0 - p * p)  # 幅度逐渐衰减
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            wave = math.sin(2.0 * math.pi * phase)
            trend = center + (target - center) * p * p
            val = _clamp(trend + amp_now * wave * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_dragon_enter_v36", name="Cue 龙摆进场", category="电机/Cue",
        description="龙摆幅度逐渐衰减，最终稳定在目标角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≈→|",
        tags=["motor","cue","dragon","enter","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","初始幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("target_val","目标值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("speed","龙摆速度","float",1.5,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _cue_dragon_enter)

    # G6: 龙摆出场 Cue（从稳定状态逐渐展开为龙摆）
    def _cue_dragon_exit(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        max_amp   = float(params.get("max_amp", 80.0))
        center    = float(params.get("center", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        speed     = float(params.get("speed", 1.5))
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = min(1.0, t / max(0.001, duration))
        amp_now = max_amp * p * p  # 幅度逐渐增大
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            wave = math.sin(2.0 * math.pi * phase)
            val = _clamp(center + amp_now * wave * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_dragon_exit_v36", name="Cue 龙摆出场", category="电机/Cue",
        description="从稳定状态逐渐展开为龙摆，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="|→≈",
        tags=["motor","cue","dragon","exit","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("max_amp","最大幅度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制幅度的最大值上限。限制效果的幅度不超过此值"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("speed","龙摆速度","float",1.5,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _cue_dragon_exit)

    # G7: 速度渐变 Cue（速度从慢到快或从快到慢）
    def _cue_speed_ramp(ef, t, sorted_fx, fdefs, params, fps):
        duration   = float(params.get("duration", 4.0))
        start_speed= float(params.get("start_speed", 0.2))
        end_speed  = float(params.get("end_speed", 3.0))
        amp        = float(params.get("amp", 60.0))
        center     = float(params.get("center", 128.0))
        phase_diff = float(params.get("phase_diff", 0.15))
        ch         = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = min(1.0, t / max(0.001, duration))
        speed_now = start_speed + (end_speed - start_speed) * p
        # 累积相位（积分速度）
        phase_acc = (start_speed * t + (end_speed - start_speed) * t * p * 0.5)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = phase_acc - seq_i * phase_diff
            val = _clamp(center + amp * math.sin(2.0 * math.pi * phase) * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_speed_ramp_v36", name="Cue 速度渐变", category="电机/Cue",
        description="波浪速度从慢到快（或快到慢），一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≈↑↑",
        tags=["motor","cue","speed","ramp","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",4.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_speed","起始速度","float",0.2,0.0,10.0,0.1,
                tooltip="控制起始时的变化速度。决定效果刚启动时的快慢"),
            ParamSpec("end_speed","终止速度","float",3.0,0.0,10.0,0.1,
                tooltip="控制结束时的变化速度。决定效果收尾时的快慢"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _cue_speed_ramp)

    # G8: 随机扰动 Cue（随机抖动后归位）
    def _cue_random_shake(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        amp      = float(params.get("amp", 40.0))
        center   = float(params.get("center", 128.0))
        freq     = float(params.get("freq", 8.0))
        ch       = params.get("ch", "motor_x")
        seed     = int(params.get("seed", 42))
        p = min(1.0, t / max(0.001, duration))
        decay = (1.0 - p) ** 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 伪随机相位
            h = ((seed + seq_i * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff
            phase = t * freq + h * 10.0
            val = _clamp(center + amp * decay * math.sin(2.0 * math.pi * phase) * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_random_shake_v36", name="Cue 随机抖动归位", category="电机/Cue",
        description="随机抖动后逐渐归位，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="?↑↓|",
        tags=["motor","cue","random","shake","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("freq","抖动频率(Hz)","float",8.0,1.0,30.0,0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("seed","随机种子","int",42,0,999,1,
                tooltip="控制随机种子值。相同的种子在不同次运行时产生相同的随机序列"),
        ],
    ), _cue_random_shake)

    # G9: 22交叉进场 Cue（22交叉从中心展开进场）
    def _cue_cross22_enter(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        amp      = float(params.get("amp", 70.0))
        center   = float(params.get("center", 128.0))
        curve    = params.get("curve", "EaseOut")
        ch       = params.get("ch", "motor_x")
        phase_diff= float(params.get("phase_diff", 0.1))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            dist = abs(seq_i - mid) / max(1.0, mid)
            t_start = (1.0 - dist) * phase_diff * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            val = _clamp(center + direction * amp * local_p * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_cross22_enter_v36", name="Cue 22交叉进场", category="电机/Cue",
        description="22交叉从中心向外展开进场，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⇄2→",
        tags=["motor","cue","cross","22","enter","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",70.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("phase_diff","相位差","float",0.1,0.0,0.5,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _cue_cross22_enter)

    # G10: 扇形进场 Cue（扇形从折叠状态展开进场）
    def _cue_fan_enter(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        fan_angle = float(params.get("fan_angle", 80.0))
        start_offset= float(params.get("start_offset", -60.0))
        end_offset= float(params.get("end_offset", 0.0))
        curve     = params.get("curve", "EaseOut")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        offset_now = start_offset + (end_offset - start_offset) * p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            angle = ratio * fan_angle * 0.5 * p + offset_now
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_fan_enter_v36", name="Cue 扇形进场", category="电机/Cue",
        description="扇形从折叠状态展开并移动到目标位置，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃→|",
        tags=["motor","cue","fan","enter","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("start_offset","起始偏移(度)","float",-60.0,-180.0,180.0,1.0,
                tooltip="控制起始位置的偏移量。使起始点偏离原始位置"),
            ParamSpec("end_offset","终止偏移(度)","float",0.0,-180.0,180.0,1.0,
                tooltip="控制结束位置的偏移量。使结束点偏离原始位置"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_fan_enter)

    # G11: 扇形出场 Cue（扇形收合并移动到边缘）
    def _cue_fan_exit(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        fan_angle = float(params.get("fan_angle", 80.0))
        start_offset= float(params.get("start_offset", 0.0))
        end_offset= float(params.get("end_offset", 60.0))
        curve     = params.get("curve", "EaseIn")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        offset_now = start_offset + (end_offset - start_offset) * p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            angle = ratio * fan_angle * 0.5 * (1.0 - p) + offset_now
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_fan_exit_v36", name="Cue 扇形出场", category="电机/Cue",
        description="扇形收合并移动到边缘位置，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃←|",
        tags=["motor","cue","fan","exit","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("start_offset","起始偏移(度)","float",0.0,-180.0,180.0,1.0,
                tooltip="控制起始位置的偏移量。使起始点偏离原始位置"),
            ParamSpec("end_offset","终止偏移(度)","float",60.0,-180.0,180.0,1.0,
                tooltip="控制结束位置的偏移量。使结束点偏离原始位置"),
            ParamSpec("curve","缓动曲线","choice","EaseIn",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_fan_exit)

    # G12: 全体同步到位 Cue（所有设备完全同步到同一目标）
    def _cue_all_sync(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 180.0))
        curve    = params.get("curve", "EaseInOut3")
        ch       = params.get("ch", "motor_x")
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        val = _clamp(start + (target - start) * p)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_all_sync_v36", name="Cue 全体同步到位", category="电机/Cue",
        description="所有设备完全同步移动到同一目标角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="|||→|",
        tags=["motor","cue","sync","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",180.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut3",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_all_sync)

    # G13: 奇偶交替到位 Cue（奇数先到，偶数后到）
    def _cue_odd_even_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.5))
        start    = float(params.get("start_val", 128.0))
        target_odd = float(params.get("target_odd", 180.0))
        target_even= float(params.get("target_even", 80.0))
        delay    = float(params.get("delay_ratio", 0.3))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            is_odd = seq_i % 2 == 0
            t_start = 0.0 if is_odd else delay * duration
            target = target_odd if is_odd else target_even
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            val = _clamp(start + (target - start) * local_p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_odd_even_goto_v36", name="Cue 奇偶交替到位", category="电机/Cue",
        description="奇数设备先到位，偶数设备后到位，目标角度可分别设置，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="1→|2→|",
        tags=["motor","cue","odd","even","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_odd","奇数目标值","float",180.0,0.0,255.0,1.0,
                tooltip="控制奇数位置设备的目标输出值。用于奇偶分组交替输出"),
            ParamSpec("target_even","偶数目标值","float",80.0,0.0,255.0,1.0,
                tooltip="控制偶数位置设备的目标输出值。用于奇偶分组交替输出"),
            ParamSpec("delay_ratio","延迟比例","float",0.3,0.0,0.8,0.05,
                tooltip="控制延迟量相对于周期的比例。值越大设备间错位感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_odd_even_goto)

    # G14: 两端向中心到位 Cue（两端先到，逐渐向中心）
    def _cue_outside_in(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        curve    = params.get("curve", "EaseOut")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1.0, mid)
            # 越靠外越先到位
            t_start = (1.0 - dist) * 0.4 * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            val = _clamp(start + (target - start) * local_p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_outside_in_v36", name="Cue 两端向中心到位", category="电机/Cue",
        description="两端设备先到位，逐渐向中心收拢，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="←→|",
        tags=["motor","cue","outside","in","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_outside_in)

    # G15: 中心向两端到位 Cue（中心先到，逐渐向外）
    def _cue_inside_out(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        curve    = params.get("curve", "EaseOut")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1.0, mid)
            # 越靠近中心越先到位
            t_start = dist * 0.4 * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            val = _clamp(start + (target - start) * local_p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_inside_out_v36", name="Cue 中心向两端到位", category="电机/Cue",
        description="中心设备先到位，逐渐向两端展开，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="|→←",
        tags=["motor","cue","inside","out","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_inside_out)

    # G16: XY 22交叉进场 Cue（XY双轴22交叉同时进场）
    def _cue_xy_cross22_enter(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 3.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 30.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        curve    = params.get("curve", "EaseOut")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        xy_phase = float(params.get("xy_phase", 0.0))
        n = len(sorted_fx)
        p_base = min(1.0, t / max(0.001, duration))
        px = _ease(curve, p_base)
        py = _ease(curve, min(1.0, max(0.0, p_base - xy_phase)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(center_x + direction * amp_x * px * 127.0 / 90.0)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(center_y + direction * amp_y * py * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_cue_xy_cross22_enter_v36", name="Cue XY 22交叉进场", category="电机/Cue",
        description="XY双轴22交叉同时进场，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY⇄2→",
        tags=["motor","cue","xy","cross","22","enter","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("xy_phase","XY相位差","float",0.0,0.0,0.5,0.01,
                tooltip="控制 X 与 Y 轴运动的相位差。值越大两轴运动的不同步程度越高"),
        ],
    ), _cue_xy_cross22_enter)

    # G17: XY 扇形漂移进场 Cue（扇形展开同时整体漂移进场）
    def _cue_xy_fan_drift_enter(ef, t, sorted_fx, fdefs, params, fps):
        duration    = float(params.get("duration", 3.5))
        fan_angle   = float(params.get("fan_angle", 70.0))
        start_x     = float(params.get("start_x", -60.0))
        end_x       = float(params.get("end_x", 0.0))
        start_y     = float(params.get("start_y", 0.0))
        end_y       = float(params.get("end_y", 0.0))
        curve       = params.get("curve", "EaseOut")
        en_x        = bool(params.get("enable_x", True))
        en_y        = bool(params.get("enable_y", False))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        offset_x = start_x + (end_x - start_x) * p
        offset_y = start_y + (end_y - start_y) * p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            if en_x:
                ef.values[orig_i]["motor_x"] = _deg2dmx(ratio * fan_angle * 0.5 * p + offset_x)
            if en_y:
                ef.values[orig_i]["motor_y"] = _deg2dmx(offset_y)
    reg(EffectPreset(
        id="motor_cue_xy_fan_drift_enter_v36", name="Cue XY扇形漂移进场", category="电机/Cue",
        description="扇形展开同时整体从边缘漂移进场，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY⊃→|",
        tags=["motor","cue","xy","fan","drift","enter","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",3.5,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",70.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("start_x","X起始偏移(度)","float",-60.0,-180.0,180.0,1.0,
                tooltip="控制 X 轴运动的起始位置。决定水平运动的出发点"),
            ParamSpec("end_x","X终止偏移(度)","float",0.0,-180.0,180.0,1.0,
                tooltip="控制 X 轴运动的终止位置。决定水平运动的结束点"),
            ParamSpec("start_y","Y起始偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制 Y 轴运动的起始位置。决定垂直运动的出发点"),
            ParamSpec("end_y","Y终止偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制 Y 轴运动的终止位置。决定垂直运动的结束点"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_xy_fan_drift_enter)

    # G18: 多扇相位展开 Cue（多扇形从中心向外展开，相位差可调）
    def _cue_multi_fan_phase_open(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 4.0))
        fans      = max(2, int(params.get("fans", 3)))
        fan_angle = float(params.get("fan_angle", 60.0))
        fan_phase = float(params.get("fan_phase_diff", 0.2))
        curve     = params.get("curve", "EaseOut")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            t_start = fan_idx * fan_phase * duration
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            angle = local_ratio * fan_angle * 0.5 * local_p
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_multi_fan_phase_open_v36", name="Cue 多扇相位展开", category="电机/Cue",
        description="多个扇形依次展开，相位差可调，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃⊃⊃→",
        tags=["motor","cue","fan","multi","phase","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",3,2,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("duration","时长(秒)","float",4.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.2,0.0,0.8,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_multi_fan_phase_open)

    # G19: 孔雀开屏 Cue（XY双轴同时展开，Y轴随X轴展开程度增大）
    def _cue_peacock_open(ef, t, sorted_fx, fdefs, params, fps):
        duration    = float(params.get("duration", 3.0))
        fan_angle_x = float(params.get("fan_angle_x", 80.0))
        max_y       = float(params.get("max_y", 60.0))
        center_x    = float(params.get("center_x", 128.0))
        center_y    = float(params.get("center_y", 80.0))
        curve       = params.get("curve", "EaseOut")
        en_x        = bool(params.get("enable_x", True))
        en_y        = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            if en_x:
                ef.values[orig_i]["motor_x"] = _deg2dmx(ratio * fan_angle_x * 0.5 * p)
            if en_y:
                # Y轴随展开程度增大（越靠外Y越高）
                y_val = center_y + abs(ratio) * max_y * p * 127.0 / 90.0
                ef.values[orig_i]["motor_y"] = _clamp(y_val)
    reg(EffectPreset(
        id="motor_cue_peacock_open_v36", name="Cue 孔雀开屏", category="电机/Cue",
        description="XY双轴同时展开，Y轴随X轴展开程度增大，形成孔雀开屏效果，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY🦚→",
        tags=["motor","cue","xy","peacock","open","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle_x","X扇形角度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的扇形展开角度。值越大水平扇面越宽"),
            ParamSpec("max_y","Y最大偏移(度)","float",60.0,0.0,90.0,1.0,
                tooltip="控制Y最大偏移(度)的值。范围 0.0~90.0，值越大越强"),
            ParamSpec("center_x","X中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y基准值","float",80.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_peacock_open)

    # G20: 孔雀收屏 Cue（孔雀开屏的反向）
    def _cue_peacock_close(ef, t, sorted_fx, fdefs, params, fps):
        duration    = float(params.get("duration", 3.0))
        fan_angle_x = float(params.get("fan_angle_x", 80.0))
        max_y       = float(params.get("max_y", 60.0))
        center_x    = float(params.get("center_x", 128.0))
        center_y    = float(params.get("center_y", 80.0))
        curve       = params.get("curve", "EaseIn")
        en_x        = bool(params.get("enable_x", True))
        en_y        = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            if en_x:
                ef.values[orig_i]["motor_x"] = _deg2dmx(ratio * fan_angle_x * 0.5 * (1.0 - p))
            if en_y:
                y_val = center_y + abs(ratio) * max_y * (1.0 - p) * 127.0 / 90.0
                ef.values[orig_i]["motor_y"] = _clamp(y_val)
    reg(EffectPreset(
        id="motor_cue_peacock_close_v36", name="Cue 孔雀收屏", category="电机/Cue",
        description="孔雀开屏的反向收合，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY🦚←",
        tags=["motor","cue","xy","peacock","close","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle_x","X扇形角度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的扇形展开角度。值越大水平扇面越宽"),
            ParamSpec("max_y","Y最大偏移(度)","float",60.0,0.0,90.0,1.0,
                tooltip="控制Y最大偏移(度)的值。范围 0.0~90.0，值越大越强"),
            ParamSpec("center_x","X中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y基准值","float",80.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("curve","缓动曲线","choice","EaseIn",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_peacock_close)

    # G21: 波浪进场 Cue（波浪从一端涌入）
    def _cue_wave_enter(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        amp       = float(params.get("amp", 60.0))
        center    = float(params.get("center", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        speed     = float(params.get("speed", 1.5))
        curve     = params.get("curve", "EaseIn")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        amp_now = amp * p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            val = _clamp(center + amp_now * math.sin(2.0 * math.pi * phase) * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_wave_enter_v36", name="Cue 波浪进场", category="电机/Cue",
        description="波浪幅度从0增大，逐渐进场，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≈→",
        tags=["motor","cue","wave","enter","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("speed","波浪速度","float",1.5,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("curve","缓动曲线","choice","EaseIn",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_wave_enter)

    # G22: 波浪出场 Cue（波浪幅度逐渐减小到0）
    def _cue_wave_exit(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        amp       = float(params.get("amp", 60.0))
        center    = float(params.get("center", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        speed     = float(params.get("speed", 1.5))
        curve     = params.get("curve", "EaseOut")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        amp_now = amp * (1.0 - p)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            val = _clamp(center + amp_now * math.sin(2.0 * math.pi * phase) * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_wave_exit_v36", name="Cue 波浪出场", category="电机/Cue",
        description="波浪幅度逐渐减小到0，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="≈→0",
        tags=["motor","cue","wave","exit","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("speed","波浪速度","float",1.5,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("curve","缓动曲线","choice","EaseOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_wave_exit)

    # G23: 速度加速到位 Cue（速度从0加速到目标）
    def _cue_accel_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        ch       = params.get("ch", "motor_x")
        p = min(1.0, t / max(0.001, duration))
        # 加速度曲线（二次方）
        val = _clamp(start + (target - start) * p * p)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_accel_goto_v36", name="Cue 加速到位", category="电机/Cue",
        description="从起点加速到目标角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→→|",
        tags=["motor","cue","accel","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
        ],
    ), _cue_accel_goto)

    # G24: 速度减速到位 Cue（速度从快减速到目标）
    def _cue_decel_goto(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        start    = float(params.get("start_val", 128.0))
        target   = float(params.get("target_val", 200.0))
        ch       = params.get("ch", "motor_x")
        p = min(1.0, t / max(0.001, duration))
        # 减速曲线（1-(1-p)^2）
        val = _clamp(start + (target - start) * (1.0 - (1.0 - p) ** 2))
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_decel_goto_v36", name="Cue 减速到位", category="电机/Cue",
        description="从起点快速启动后减速到目标角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→→|",
        tags=["motor","cue","decel","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("target_val","目标值","float",200.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
        ],
    ), _cue_decel_goto)

    # G25: 多步到位 Cue（经过中间点到达目标）
    def _cue_multi_step(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 4.0))
        start     = float(params.get("start_val", 128.0))
        mid1      = float(params.get("mid1_val", 180.0))
        mid2      = float(params.get("mid2_val", 100.0))
        target    = float(params.get("target_val", 160.0))
        curve     = params.get("curve", "EaseInOut")
        ch        = params.get("ch", "motor_x")
        p = min(1.0, t / max(0.001, duration))
        # 4段插值
        waypoints = [start, mid1, mid2, target]
        seg = min(2, int(p * 3))
        seg_p = _ease(curve, (p * 3.0) - seg)
        val = _clamp(waypoints[seg] + (waypoints[seg + 1] - waypoints[seg]) * seg_p)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_multi_step_v36", name="Cue 多步到位", category="电机/Cue",
        description="经过两个中间点到达目标角度，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→→→|",
        tags=["motor","cue","multi","step","goto","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",4.0,0.1,60.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("start_val","起始值","float",128.0,0.0,255.0,1.0,
                tooltip="控制输出起始值。效果开始时从此数值出发"),
            ParamSpec("mid1_val","中间点1","float",180.0,0.0,255.0,1.0,
                tooltip="控制第一中间节点的输出值。用于多段路径的过渡点"),
            ParamSpec("mid2_val","中间点2","float",100.0,0.0,255.0,1.0,
                tooltip="控制第二中间节点的输出值。用于多段路径的过渡点"),
            ParamSpec("target_val","目标值","float",160.0,0.0,255.0,1.0,
                tooltip="控制效果的目标值。效果向此值渐变或趋近"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_multi_step)

    # G26: 扇形翻转 Cue（扇形整体从一侧翻转到另一侧）
    def _cue_fan_flip(ef, t, sorted_fx, fdefs, params, fps):
        duration    = float(params.get("duration", 2.0))
        fan_angle   = float(params.get("fan_angle", 60.0))
        start_offset= float(params.get("start_offset", -45.0))
        end_offset  = float(params.get("end_offset", 45.0))
        curve       = params.get("curve", "EaseInOut3")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        offset_now = start_offset + (end_offset - start_offset) * p
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            angle = ratio * fan_angle * 0.5 + offset_now
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_fan_flip_v36", name="Cue 扇形翻转", category="电机/Cue",
        description="扇形整体从一侧快速翻转到另一侧，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃↔⊂",
        tags=["motor","cue","fan","flip","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("start_offset","起始偏移(度)","float",-45.0,-180.0,180.0,1.0,
                tooltip="控制起始位置的偏移量。使起始点偏离原始位置"),
            ParamSpec("end_offset","终止偏移(度)","float",45.0,-180.0,180.0,1.0,
                tooltip="控制结束位置的偏移量。使结束点偏离原始位置"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut3",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_fan_flip)

    # G27: 22交叉反转 Cue（22交叉方向反转）
    def _cue_cross22_reverse(ef, t, sorted_fx, fdefs, params, fps):
        duration = float(params.get("duration", 2.0))
        amp      = float(params.get("amp", 60.0))
        center   = float(params.get("center", 128.0))
        start_amp= float(params.get("start_amp", 60.0))
        curve    = params.get("curve", "EaseInOut")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        p = _ease(curve, min(1.0, t / max(0.001, duration)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            # 从start_amp到-amp（反转）
            current_amp = start_amp + (-amp - start_amp) * p
            val = _clamp(center + direction * current_amp * 127.0 / 90.0)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_cross22_reverse_v36", name="Cue 22交叉反转", category="电机/Cue",
        description="22交叉方向从正向反转到反向，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⇄2↔",
        tags=["motor","cue","cross","22","reverse","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("amp","目标幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度/强度。值越大变化范围越广，输出越强烈"),
            ParamSpec("start_amp","起始幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制起始幅度值。决定效果刚开始时的变化强度"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_cross22_reverse)

    # G28: 扇形脉冲 Cue（扇形快速开合几次后稳定）
    def _cue_fan_pulse(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 3.0))
        fan_angle = float(params.get("fan_angle", 70.0))
        pulses    = max(1, int(params.get("pulses", 3)))
        offset    = float(params.get("offset", 0.0))
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        p = min(1.0, t / max(0.001, duration))
        # 脉冲衰减
        decay = (1.0 - p) ** 1.5
        pulse = max(0.0, math.sin(p * pulses * math.pi)) * decay
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = (seq_i - mid) / max(1.0, mid)
            angle = ratio * fan_angle * 0.5 * pulse + offset
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_cue_fan_pulse_v36", name="Cue 扇形脉冲", category="电机/Cue",
        description="扇形快速开合几次后稳定，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="⊃↑↓|",
        tags=["motor","cue","fan","pulse","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",3.0,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("fan_angle","扇形角度(度)","float",70.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("pulses","脉冲次数","int",3,1,8,1,
                tooltip="控制脉冲数量。值越大单个周期内产生的脉冲越密集"),
            ParamSpec("offset","整体偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制效果在设备序列上的偏移位置。值越大起始点越靠后"),
        ],
    ), _cue_fan_pulse)

    # G29: 全体归位 Cue（所有设备回到中心位置）
    def _cue_home(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 2.0))
        home_val  = float(params.get("home_val", 128.0))
        phase_diff= float(params.get("phase_diff", 0.05))
        curve     = params.get("curve", "EaseInOut")
        ch        = params.get("ch", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            t_start = seq_i * phase_diff * duration / max(1, n)
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            current = ef.values[orig_i].get(ch, home_val)
            val = _clamp(current + (home_val - current) * local_p)
            ef.values[orig_i][ch] = val
    reg(EffectPreset(
        id="motor_cue_home_v36", name="Cue 全体归位", category="电机/Cue",
        description="所有设备依次回到归位值，一次性执行。",
        target_channels=["motor_x","motor_y","motor_z","speed","pan","tilt"], icon="→|0",
        tags=["motor","cue","home","v36"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("home_val","归位值","float",128.0,0.0,255.0,1.0,
                tooltip="控制默认归位时的输出值。效果回到原点设备输出此值"),
            ParamSpec("phase_diff","设备间相位差","float",0.05,0.0,0.3,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_home)

    # G30: XY 归位 Cue（XY双轴同时回到中心）
    def _cue_xy_home(ef, t, sorted_fx, fdefs, params, fps):
        duration  = float(params.get("duration", 2.0))
        home_x    = float(params.get("home_x", 128.0))
        home_y    = float(params.get("home_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.05))
        curve     = params.get("curve", "EaseInOut")
        en_x      = bool(params.get("enable_x", True))
        en_y      = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            t_start = seq_i * phase_diff * duration / max(1, n)
            local_p = _ease(curve, min(1.0, max(0.0, (t - t_start) / max(0.001, duration - t_start))))
            if en_x:
                cur_x = ef.values[orig_i].get("motor_x", home_x)
                ef.values[orig_i]["motor_x"] = _clamp(cur_x + (home_x - cur_x) * local_p)
            if en_y:
                cur_y = ef.values[orig_i].get("motor_y", home_y)
                ef.values[orig_i]["motor_y"] = _clamp(cur_y + (home_y - cur_y) * local_p)
    reg(EffectPreset(
        id="motor_cue_xy_home_v36", name="Cue XY归位", category="电机/Cue",
        description="XY双轴同时回到归位值，一次性执行。",
        target_channels=["motor_x","motor_y"], icon="XY→|0",
        tags=["motor","cue","xy","home","v36"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("duration","时长(秒)","float",2.0,0.1,30.0,0.1,
                tooltip="控制效果的完整运行时长。值越大单次周期运行越久"),
            ParamSpec("home_x","X归位值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 X 轴的默认归位位置。效果暂停或结束时回到此坐标"),
            ParamSpec("home_y","Y归位值","float",128.0,0.0,255.0,1.0,
                tooltip="控制 Y 轴的默认归位位置。效果暂停或结束时回到此坐标"),
            ParamSpec("phase_diff","设备间相位差","float",0.05,0.0,0.3,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("curve","缓动曲线","choice","EaseInOut",choices=EASE_CHOICES,
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
        ],
    ), _cue_xy_home)
