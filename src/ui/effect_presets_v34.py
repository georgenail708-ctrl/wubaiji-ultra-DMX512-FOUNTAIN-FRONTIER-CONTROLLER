"""
effect_presets_v34.py — 单轴电机基础动作大量变式 v34
=====================================================
新增约100个单轴电机基础动作变式，覆盖：
  A. 龙摆变式（正弦/三角/锯齿/脉冲/反转/多组/镜像/中心/边缘/随机相位等）
  B. 交叉变式（2组/3组/4组/6组/8组/奇偶/正反/软硬/宽窄等）
  C. 开合变式（同步/交替/渐进/阶梯/脉冲/随机等）
  D. 波形变式（正弦/三角/锯齿/阶梯/脉冲/弹跳/各种组合）
  E. 分组变式（2/3/4/5/6/8组，各种相位差和波形）
  F. 追逐变式（顺/逆/双向/中心扩散/边缘收拢/随机）
每个动作均提供丰富可调参数。
"""
from __future__ import annotations
import math
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .effect_engine import EffectEngine

# ── 公共工具函数 ──────────────────────────────────────────────────────────────
def _clamp(v, lo=0, hi=255):
    return max(lo, min(hi, int(round(v))))

def _clampf(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(v)))

def _sine(x):
    return (math.sin(2.0 * math.pi * x) + 1.0) * 0.5

def _sine_raw(x):
    return math.sin(2.0 * math.pi * x)

def _tri(x):
    x = x % 1.0
    return 1.0 - abs(2.0 * x - 1.0)

def _saw_up(x):
    return x % 1.0

def _saw_down(x):
    return 1.0 - (x % 1.0)

def _pulse(x, w):
    return 1.0 if (x % 1.0) < _clampf(w, 0.01, 0.99) else 0.0

def _soft_pulse(x, w):
    x = x % 1.0
    w = _clampf(w, 0.01, 0.99)
    v = max(0.0, 1.0 - abs(x - 0.5) / max(0.01, w * 0.5))
    return v * v * (3.0 - 2.0 * v)

def _bounce(x):
    x = x % 1.0
    v = _tri(x)
    return v * v * (3.0 - 2.0 * v)

def _step(x, n):
    return int((x % 1.0) * n) / max(1, n - 1)

def _ease_in(x):
    x = _clampf(x)
    return x * x

def _ease_out(x):
    x = _clampf(x)
    return 1.0 - (1.0 - x) ** 2

def _ease_inout(x):
    x = _clampf(x)
    return x * x * (3.0 - 2.0 * x)

def _hash(seed):
    h = (int(seed) * 1103515245 + 12345) & 0x7fffffff
    h ^= (h >> 11)
    h = (h * 2654435761) & 0xffffffff
    return (h & 0xffff) / 65535.0

def _motor_ch_list():
    return ["motor_x", "motor_y", "motor_z", "speed", "pan", "tilt"]


def _register_v34_presets(engine: "EffectEngine"):
    from .effect_engine import EffectPreset, ParamSpec
    reg = engine._reg
    MCH = _motor_ch_list()

    # ═══════════════════════════════════════════════════════════════════════════
    #  A. 龙摆变式（单轴追逐点在设备阵列中移动）
    # ═══════════════════════════════════════════════════════════════════════════

    # A1 正弦龙摆（可调幅度/中心/相位步长）
    def _sine_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        reverse = bool(params.get("reverse", False))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step * (-1 if reverse else 1)
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_sine_dragon_v34", name="正弦龙摆", category="电机/龙摆",
        description="正弦波依次相位偏移，形成流畅龙摆。可调幅度、中心、相位步长、方向。",
        target_channels=MCH, icon="~→",
        tags=["motor","dragon","sine","basic","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
            ParamSpec("reverse","反向","bool",False,
                tooltip="控制是否反转顺序。反转后设备序列从头到尾颠倒"),
        ],
    ), _sine_dragon)

    # A2 三角龙摆
    def _tri_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = center + amplitude * (_tri(t * speed + ph) * 2.0 - 1.0)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_tri_dragon_v34", name="三角龙摆", category="电机/龙摆",
        description="三角波龙摆，线性往返，棱角分明。",
        target_channels=MCH, icon="/\\→",
        tags=["motor","dragon","triangle","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _tri_dragon)

    # A3 锯齿上升龙摆
    def _saw_up_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = lo + (hi - lo) * _saw_up(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_saw_up_dragon_v34", name="锯齿上升龙摆", category="电机/龙摆",
        description="锯齿上升波龙摆，快速复位后缓慢上扫。",
        target_channels=MCH, icon="/→",
        tags=["motor","dragon","sawtooth","up","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _saw_up_dragon)

    # A4 锯齿下降龙摆
    def _saw_down_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = lo + (hi - lo) * _saw_down(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_saw_down_dragon_v34", name="锯齿下降龙摆", category="电机/龙摆",
        description="锯齿下降波龙摆，缓慢下扫后快速复位。",
        target_channels=MCH, icon="\\→",
        tags=["motor","dragon","sawtooth","down","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _saw_down_dragon)

    # A5 弹跳龙摆（bounce波形）
    def _bounce_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.2))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = lo + (hi - lo) * _bounce(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_bounce_wave_dragon_v34", name="弹跳波龙摆", category="电机/龙摆",
        description="弹跳波形龙摆，缓入缓出，有弹性感。",
        target_channels=MCH, icon="⌒→",
        tags=["motor","dragon","bounce","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.2,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _bounce_dragon)

    # A6 脉冲龙摆（窄脉冲追逐）
    def _pulse_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        width = float(params.get("width", 0.15))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = lo + (hi - lo) * _pulse(t * speed + ph, width)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_pulse_dragon_v34", name="脉冲龙摆", category="电机/龙摆",
        description="窄脉冲依次触发，形成快速追逐效果。宽度可调。",
        target_channels=MCH, icon="|→",
        tags=["motor","dragon","pulse","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.5,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.15,0.02,0.8,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _pulse_dragon)

    # A7 软脉冲龙摆
    def _soft_pulse_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        width = float(params.get("width", 0.2))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = lo + (hi - lo) * _soft_pulse(t * speed + ph, width)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_soft_pulse_dragon_v34", name="软脉冲龙摆", category="电机/龙摆",
        description="柔和脉冲依次追逐，边缘平滑，视觉流畅。",
        target_channels=MCH, icon="○→",
        tags=["motor","dragon","soft","pulse","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.5,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.2,0.02,0.8,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _soft_pulse_dragon)

    # A8 反向正弦龙摆
    def _reverse_sine_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = center + amplitude * _sine_raw(-t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_reverse_sine_dragon_v34", name="反向正弦龙摆", category="电机/龙摆",
        description="正弦龙摆反向传播，与正向龙摆形成对比。",
        target_channels=MCH, icon="~←",
        tags=["motor","dragon","sine","reverse","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _reverse_sine_dragon)

    # A9 镜像龙摆（从中心向两端）
    def _mirror_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            mirror_i = abs(seq_i - (n - 1) / 2.0) / (n / 2.0)
            v = center + amplitude * _sine_raw(t * speed - mirror_i * phase_step * n)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_mirror_dragon_v34", name="镜像龙摆", category="电机/龙摆",
        description="从中心向两端对称传播的龙摆，两端同步。",
        target_channels=MCH, icon="~↔~",
        tags=["motor","dragon","mirror","symmetric","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _mirror_dragon)

    # A10 大相位步长龙摆（相邻设备相差大，形成明显波纹）
    def _wide_phase_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        phase_step = float(params.get("phase_step", 0.25))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_wide_phase_dragon_v34", name="宽相位龙摆", category="电机/龙摆",
        description="相邻设备相位差大，形成明显分段波纹效果。",
        target_channels=MCH, icon="~~ →",
        tags=["motor","dragon","wide","phase","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",0.8,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_step","相位步长","float",0.25,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _wide_phase_dragon)

    # A11 随机相位龙摆（每设备随机相位偏移，有机感）
    def _random_phase_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        rand_amount = float(params.get("rand_amount", 0.3))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            base_ph = seq_i * 0.08
            rand_ph = (_hash(seq_i * 137) - 0.5) * rand_amount
            v = center + amplitude * _sine_raw(t * speed + base_ph + rand_ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_random_phase_dragon_v34", name="随机相位龙摆", category="电机/龙摆",
        description="在标准龙摆基础上叠加随机相位偏移，产生有机自然感。",
        target_channels=MCH, icon="~?→",
        tags=["motor","dragon","random","phase","organic","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("rand_amount","随机量","float",0.3,0.0,1.0,0.05,
                tooltip="控制随机扰动的强度。值越大随机抖动的幅度越明显"),
        ],
    ), _random_phase_dragon)

    # A12 双频叠加龙摆（两个正弦叠加）
    def _dual_freq_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed1 = float(params.get("speed1", 1.0))
        speed2 = float(params.get("speed2", 2.3))
        amp1 = float(params.get("amp1", 70.0))
        amp2 = float(params.get("amp2", 30.0))
        center = float(params.get("center", 128.0))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = center + amp1 * _sine_raw(t * speed1 + ph) + amp2 * _sine_raw(t * speed2 + ph * 1.5)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_dual_freq_dragon_v34", name="双频叠加龙摆", category="电机/龙摆",
        description="两个不同频率正弦叠加，产生复杂有趣的运动轨迹。",
        target_channels=MCH, icon="~~→",
        tags=["motor","dragon","dual","frequency","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed1","主频速度","float",1.0,0.05,15.0,0.05,
                tooltip="控制第一层（低频）效果的速度。值越小越缓慢，值越大越急促"),
            ParamSpec("speed2","副频速度","float",2.3,0.05,15.0,0.05,
                tooltip="控制第二层（高频）效果的速度。值越小越缓慢，值越大越急促"),
            ParamSpec("amp1","主幅度","float",70.0,5.0,120.0,1.0,
                tooltip="控制第一层效果的幅度大小。值越大该层影响越明显"),
            ParamSpec("amp2","副幅度","float",30.0,5.0,80.0,1.0,
                tooltip="控制第二层效果的幅度大小。值越大该层影响越明显"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _dual_freq_dragon)

    # A13 步进龙摆（4步阶梯）
    def _step4_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        phase_step = float(params.get("phase_step", 0.08))
        steps = int(params.get("steps", 4))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = lo + (hi - lo) * _step(t * speed + ph, steps)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_step_dragon_v34", name="阶梯龙摆", category="电机/龙摆",
        description="阶梯波形龙摆，按步进跳跃，可调步数。",
        target_channels=MCH, icon="▁▃▅▇→",
        tags=["motor","dragon","step","stair","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",0.8,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
            ParamSpec("steps","步数","int",4,2,16,1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
        ],
    ), _step4_dragon)

    # A14 缓入缓出龙摆
    def _ease_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        phase_step = float(params.get("phase_step", 0.08))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            raw = _tri(t * speed + ph)
            v = lo + (hi - lo) * _ease_inout(raw)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_ease_dragon_v34", name="缓入缓出龙摆", category="电机/龙摆",
        description="三角波经缓入缓出处理，运动更平滑自然。",
        target_channels=MCH, icon="⌒→",
        tags=["motor","dragon","ease","smooth","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("phase_step","相位步长","float",0.08,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _ease_dragon)

    # A15 慢速正弦龙摆
    def _slow_sine_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        phase_step = float(params.get("phase_step", 0.06))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_slow_sine_dragon_v34", name="慢速正弦龙摆", category="电机/龙摆",
        description="极慢的正弦龙摆，适合氛围营造。",
        target_channels=MCH, icon="~~",
        tags=["motor","dragon","slow","sine","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",0.3,0.01,5.0,0.01,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_step","相位步长","float",0.06,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _slow_sine_dragon)

    # A16 快速正弦龙摆
    def _fast_sine_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 4.0))
        amplitude = float(params.get("amplitude", 80.0))
        center = float(params.get("center", 128.0))
        phase_step = float(params.get("phase_step", 0.1))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * phase_step
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_fast_sine_dragon_v34", name="快速正弦龙摆", category="电机/龙摆",
        description="高速正弦龙摆，适合激烈节奏。",
        target_channels=MCH, icon="~~~",
        tags=["motor","dragon","fast","sine","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",4.0,1.0,20.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",80.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("phase_step","相位步长","float",0.1,0.0,0.5,0.005,
                tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
        ],
    ), _fast_sine_dragon)

    # ═══════════════════════════════════════════════════════════════════════════
    #  B. 交叉变式（分组相位差180°或其他角度）
    # ═══════════════════════════════════════════════════════════════════════════

    # B1 2组交叉（奇偶相差180°）
    def _cross2(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        wave = params.get("wave", "sine")
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = 0.5 if seq_i % 2 == 1 else 0.0
            if wave == "sine":
                raw = _sine_raw(t * speed + ph)
            elif wave == "triangle":
                raw = _tri(t * speed + ph) * 2.0 - 1.0
            elif wave == "saw_up":
                raw = _saw_up(t * speed + ph) * 2.0 - 1.0
            else:
                raw = _sine_raw(t * speed + ph)
            v = center + amplitude * raw
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross2_v34", name="2组交叉", category="电机/交叉",
        description="奇偶设备相位差180°，形成标准2组交叉。可选波形。",
        target_channels=MCH, icon="X2",
        tags=["motor","cross","2group","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","sine",choices=["sine","triangle","saw_up"],
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _cross2)

    # B2 3组交叉（120°相位差）
    def _cross3(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = (seq_i % 3) / 3.0
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross3_v34", name="3组交叉", category="电机/交叉",
        description="每3个设备为一组，组间相位差120°，三相交叉效果。",
        target_channels=MCH, icon="X3",
        tags=["motor","cross","3group","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _cross3)

    # B3 4组交叉（90°相位差）
    def _cross4(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = (seq_i % 4) / 4.0
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross4_v34", name="4组交叉", category="电机/交叉",
        description="每4个设备为一组，组间相位差90°，四相交叉效果。",
        target_channels=MCH, icon="X4",
        tags=["motor","cross","4group","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _cross4)

    # B4 6组交叉（60°相位差）
    def _cross6(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = (seq_i % 6) / 6.0
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross6_v34", name="6组交叉", category="电机/交叉",
        description="每6个设备为一组，组间相位差60°，六相交叉效果。",
        target_channels=MCH, icon="X6",
        tags=["motor","cross","6group","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _cross6)

    # B5 8组交叉（45°相位差）
    def _cross8(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = (seq_i % 8) / 8.0
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross8_v34", name="8组交叉", category="电机/交叉",
        description="每8个设备为一组，组间相位差45°，八相交叉效果。",
        target_channels=MCH, icon="X8",
        tags=["motor","cross","8group","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _cross8)

    # B6 可调组数交叉（通用）
    def _cross_n(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        groups = max(2, int(params.get("groups", 2)))
        wave = params.get("wave", "sine")
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = (seq_i % groups) / float(groups)
            if wave == "sine":
                raw = _sine_raw(t * speed + ph)
            elif wave == "triangle":
                raw = _tri(t * speed + ph) * 2.0 - 1.0
            elif wave == "saw_up":
                raw = _saw_up(t * speed + ph) * 2.0 - 1.0
            elif wave == "bounce":
                raw = _bounce(t * speed + ph) * 2.0 - 1.0
            else:
                raw = _sine_raw(t * speed + ph)
            v = center + amplitude * raw
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross_n_v34", name="N组交叉（通用）", category="电机/交叉",
        description="可自由设定组数，自动均分相位差，支持多种波形。",
        target_channels=MCH, icon="XN",
        tags=["motor","cross","groups","universal","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("groups","组数","int",2,2,16,1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("wave","波形","choice","sine",choices=["sine","triangle","saw_up","bounce"],
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _cross_n)

    # B7 2组交叉反转（奇偶互换）
    def _cross2_inv(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = 0.5 if seq_i % 2 == 0 else 0.0
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross2_inv_v34", name="2组交叉反转", category="电机/交叉",
        description="2组交叉的反转版本，奇偶设备相位互换。",
        target_channels=MCH, icon="X2↔",
        tags=["motor","cross","2group","invert","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _cross2_inv)

    # B8 4组交叉反转
    def _cross4_inv(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 100.0))
        center = float(params.get("center", 128.0))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = 1.0 - (seq_i % 4) / 4.0
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross4_inv_v34", name="4组交叉反转", category="电机/交叉",
        description="4组交叉的反转版本，相位顺序倒置。",
        target_channels=MCH, icon="X4↔",
        tags=["motor","cross","4group","invert","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",100.0,5.0,127.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _cross4_inv)

    # B9 2组脉冲交叉（开合式）
    def _cross2_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        width = float(params.get("width", 0.5))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = 0.5 if seq_i % 2 == 1 else 0.0
            v = lo + (hi - lo) * _pulse(t * speed + ph, width)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross2_pulse_v34", name="2组脉冲交叉", category="电机/交叉",
        description="奇偶设备脉冲交替，形成开合式交叉效果。",
        target_channels=MCH, icon="[]X",
        tags=["motor","cross","2group","pulse","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","占空比","float",0.5,0.05,0.95,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _cross2_pulse)

    # B10 4组脉冲交叉
    def _cross4_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        width = float(params.get("width", 0.25))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = (seq_i % 4) / 4.0
            v = lo + (hi - lo) * _pulse(t * speed + ph, width)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross4_pulse_v34", name="4组脉冲交叉", category="电机/交叉",
        description="4组设备依次脉冲，形成四相追逐式交叉。",
        target_channels=MCH, icon="||||X",
        tags=["motor","cross","4group","pulse","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","占空比","float",0.25,0.05,0.9,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _cross4_pulse)

    # B11 2组三角交叉
    def _cross2_tri(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = 0.5 if seq_i % 2 == 1 else 0.0
            v = lo + (hi - lo) * _tri(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross2_tri_v34", name="2组三角交叉", category="电机/交叉",
        description="奇偶设备三角波交叉，线性往返，棱角分明。",
        target_channels=MCH, icon="/\\X",
        tags=["motor","cross","2group","triangle","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _cross2_tri)

    # B12 2组锯齿交叉
    def _cross2_saw(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = 0.5 if seq_i % 2 == 1 else 0.0
            v = lo + (hi - lo) * _saw_up(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross2_saw_v34", name="2组锯齿交叉", category="电机/交叉",
        description="奇偶设备锯齿波交叉，一升一降形成剪刀效果。",
        target_channels=MCH, icon="/X",
        tags=["motor","cross","2group","saw","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _cross2_saw)

    # ═══════════════════════════════════════════════════════════════════════════
    #  C. 开合变式（同步/交替/渐进/阶梯/脉冲）
    # ═══════════════════════════════════════════════════════════════════════════

    # C1 同步开合（全部同步）
    def _sync_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        wave = params.get("wave", "sine")
        ch = params.get("ch", "motor_x")
        if wave == "sine":
            v_raw = _sine(t * speed)
        elif wave == "triangle":
            v_raw = _tri(t * speed)
        elif wave == "pulse":
            v_raw = _pulse(t * speed, float(params.get("width", 0.5)))
        else:
            v_raw = _sine(t * speed)
        val = lo + (hi - lo) * v_raw
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_sync_open_close_v34", name="同步开合", category="电机/开合",
        description="所有设备同步开合，可选正弦/三角/脉冲波形。",
        target_channels=MCH, icon="[]",
        tags=["motor","open","close","sync","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("wave","波形","choice","sine",choices=["sine","triangle","pulse"],
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("width","脉冲宽度","float",0.5,0.05,0.95,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
        ],
    ), _sync_open_close)

    # C2 交替开合（奇偶反相）
    def _alt_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = 0.5 if seq_i % 2 == 1 else 0.0
            v = lo + (hi - lo) * _sine(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_alt_open_close_v34", name="交替开合", category="电机/开合",
        description="奇偶设备交替开合，形成剪刀式效果。",
        target_channels=MCH, icon="[][]",
        tags=["motor","open","close","alternate","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _alt_open_close)

    # C3 渐进开合（依次延迟）
    def _seq_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        delay = float(params.get("delay", 0.1))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * delay
            v = lo + (hi - lo) * _sine(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_seq_open_close_v34", name="渐进开合", category="电机/开合",
        description="设备依次延迟开合，形成波浪式开合效果。",
        target_channels=MCH, icon="[→]",
        tags=["motor","open","close","sequential","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("delay","延迟步长","float",0.1,0.0,0.5,0.01,
                tooltip="控制效果的延迟/错开时间。值越大效果启动越滞后"),
        ],
    ), _seq_open_close)

    # C4 中心扩散开合
    def _center_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - (n - 1) / 2.0) / (n / 2.0)
            v = lo + (hi - lo) * _sine(t * speed + dist * 0.3)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_center_open_close_v34", name="中心扩散开合", category="电机/开合",
        description="从中心向两端扩散的开合，中间先动，两端后动。",
        target_channels=MCH, icon="<[]>",
        tags=["motor","open","close","center","expand","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _center_open_close)

    # C5 边缘收拢开合
    def _edge_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - (n - 1) / 2.0) / (n / 2.0)
            v = lo + (hi - lo) * _sine(t * speed - dist * 0.3)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_edge_open_close_v34", name="边缘收拢开合", category="电机/开合",
        description="从两端向中心收拢的开合，两端先动，中间后动。",
        target_channels=MCH, icon=">[]<",
        tags=["motor","open","close","edge","converge","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _edge_open_close)

    # C6 阶梯开合（分段跳跃）
    def _stair_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        steps = int(params.get("steps", 4))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seg = int(seq_i / n * steps)
            ph = seg / float(steps)
            v = lo + (hi - lo) * _sine(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_stair_open_close_v34", name="阶梯开合", category="电机/开合",
        description="设备按阶梯分段，每段内同步，段间有相位差。",
        target_channels=MCH, icon="▁▃▅▇[]",
        tags=["motor","open","close","stair","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",0.8,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("steps","阶梯数","int",4,2,16,1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
        ],
    ), _stair_open_close)

    # C7 随机开合（每设备独立随机相位）
    def _random_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = _hash(seq_i * 73 + 1)
            v = lo + (hi - lo) * _sine(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_random_open_close_v34", name="随机开合", category="电机/开合",
        description="每个设备随机相位开合，产生有机随机效果。",
        target_channels=MCH, icon="?[]",
        tags=["motor","open","close","random","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _random_open_close)

    # C8 快速脉冲开合
    def _fast_pulse_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 3.0))
        width = float(params.get("width", 0.3))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            v = lo + (hi - lo) * _pulse(t * speed, width)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_fast_pulse_open_close_v34", name="快速脉冲开合", category="电机/开合",
        description="所有设备同步快速脉冲开合，适合节拍强调。",
        target_channels=MCH, icon="|||",
        tags=["motor","open","close","fast","pulse","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",3.0,0.5,20.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","占空比","float",0.3,0.05,0.95,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _fast_pulse_open_close)

    # ═══════════════════════════════════════════════════════════════════════════
    #  D. 波形变式（各种波形的单轴循环）
    # ═══════════════════════════════════════════════════════════════════════════

    # D1 正弦呼吸（全同步）
    def _sine_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        val = lo + (hi - lo) * _sine(t * speed)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_sine_breath_v34", name="正弦呼吸", category="电机/波形",
        description="所有设备同步正弦呼吸，平滑往返。",
        target_channels=MCH, icon="~",
        tags=["motor","breath","sine","sync","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",0.8,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _sine_breath)

    # D2 三角呼吸
    def _tri_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        val = lo + (hi - lo) * _tri(t * speed)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_tri_breath_v34", name="三角呼吸", category="电机/波形",
        description="所有设备同步三角波呼吸，线性往返。",
        target_channels=MCH, icon="/\\",
        tags=["motor","breath","triangle","sync","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",0.8,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _tri_breath)

    # D3 锯齿上扫（全同步）
    def _saw_up_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        val = lo + (hi - lo) * _saw_up(t * speed)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_saw_up_breath_v34", name="锯齿上扫", category="电机/波形",
        description="所有设备同步锯齿上扫，缓慢上升后快速复位。",
        target_channels=MCH, icon="/|",
        tags=["motor","saw","up","sync","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _saw_up_breath)

    # D4 锯齿下扫（全同步）
    def _saw_down_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        val = lo + (hi - lo) * _saw_down(t * speed)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_saw_down_breath_v34", name="锯齿下扫", category="电机/波形",
        description="所有设备同步锯齿下扫，缓慢下降后快速复位。",
        target_channels=MCH, icon="\\|",
        tags=["motor","saw","down","sync","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _saw_down_breath)

    # D5 4步阶梯（全同步）
    def _step4_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        steps = int(params.get("steps", 4))
        ch = params.get("ch", "motor_x")
        val = lo + (hi - lo) * _step(t * speed, steps)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_step_breath_v34", name="阶梯波形", category="电机/波形",
        description="所有设备同步阶梯跳跃，步数可调。",
        target_channels=MCH, icon="▁▃▅▇",
        tags=["motor","step","stair","sync","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",0.8,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("steps","步数","int",4,2,16,1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
        ],
    ), _step4_breath)

    # D6 弹跳波形（全同步）
    def _bounce_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        val = lo + (hi - lo) * _bounce(t * speed)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_bounce_breath_v34", name="弹跳波形", category="电机/波形",
        description="所有设备同步弹跳，缓入缓出，有弹性感。",
        target_channels=MCH, icon="⌒",
        tags=["motor","bounce","sync","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _bounce_breath)

    # D7 脉冲闪动（全同步）
    def _pulse_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.3))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        val = lo + (hi - lo) * _pulse(t * speed, width)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_pulse_breath_v34", name="脉冲闪动", category="电机/波形",
        description="所有设备同步脉冲，宽度可调。",
        target_channels=MCH, icon="|",
        tags=["motor","pulse","sync","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",2.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","占空比","float",0.3,0.05,0.95,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pulse_breath)

    # D8 软脉冲（全同步）
    def _soft_pulse_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.3))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        val = lo + (hi - lo) * _soft_pulse(t * speed, width)
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_soft_pulse_breath_v34", name="软脉冲", category="电机/波形",
        description="所有设备同步柔和脉冲，边缘平滑。",
        target_channels=MCH, icon="○",
        tags=["motor","soft","pulse","sync","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",2.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","宽度","float",0.3,0.05,0.95,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _soft_pulse_breath)

    # ═══════════════════════════════════════════════════════════════════════════
    #  E. 分组变式（N组，各组相位差均分）
    # ═══════════════════════════════════════════════════════════════════════════

    def _make_group_fn(n_groups, wave_type):
        def _fn(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 1.0))
            lo = int(params.get("lo", 28))
            hi = int(params.get("hi", 228))
            ch = params.get("ch", "motor_x")
            width = float(params.get("width", 0.5))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                ph = (seq_i % n_groups) / float(n_groups)
                if wave_type == "sine":
                    raw = _sine(t * speed + ph)
                elif wave_type == "pulse":
                    raw = _pulse(t * speed + ph, width)
                elif wave_type == "triangle":
                    raw = _tri(t * speed + ph)
                elif wave_type == "saw":
                    raw = _saw_up(t * speed + ph)
                else:
                    raw = _sine(t * speed + ph)
                ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
        return _fn

    for ng, wt, wname, wicon in [
        (2, "sine", "正弦", "~"), (2, "pulse", "脉冲", "|"),
        (2, "triangle", "三角", "/\\"), (2, "saw", "锯齿", "/"),
        (3, "sine", "正弦", "~"), (3, "pulse", "脉冲", "|"),
        (3, "triangle", "三角", "/\\"),
        (4, "sine", "正弦", "~"), (4, "pulse", "脉冲", "|"),
        (4, "triangle", "三角", "/\\"), (4, "saw", "锯齿", "/"),
        (5, "sine", "正弦", "~"), (5, "pulse", "脉冲", "|"),
        (6, "sine", "正弦", "~"), (6, "pulse", "脉冲", "|"),
        (8, "sine", "正弦", "~"), (8, "pulse", "脉冲", "|"),
    ]:
        pid = f"motor_group{ng}_{wt}_v34"
        pname = f"{ng}组{wname}"
        pcat = "电机/分组"
        pdesc = f"设备按{ng}组均分相位差，{wname}波形循环。"
        params_list = [
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ]
        if wt == "pulse":
            params_list.append(ParamSpec("width","占空比","float",0.5,0.05,0.95,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"))
        reg(EffectPreset(
            id=pid, name=pname, category=pcat,
            description=pdesc, target_channels=MCH, icon=f"{ng}{wicon}",
            tags=["motor","group",f"{ng}group",wt,"v34"],
            params=params_list,
        ), _make_group_fn(ng, wt))

    # ═══════════════════════════════════════════════════════════════════════════
    #  F. 追逐变式（顺/逆/双向/中心/边缘）
    # ═══════════════════════════════════════════════════════════════════════════

    # F1 顺向追逐
    def _chase_fwd(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.2))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i / n
            raw = _soft_pulse(t * speed - ph, width)
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_fwd_v34", name="顺向追逐", category="电机/追逐",
        description="激活点从头到尾顺向追逐，软脉冲平滑过渡。",
        target_channels=MCH, icon="→",
        tags=["motor","chase","forward","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",2.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.2,0.02,0.8,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_fwd)

    # F2 逆向追逐
    def _chase_rev(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.2))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i / n
            raw = _soft_pulse(-t * speed - ph, width)
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_rev_v34", name="逆向追逐", category="电机/追逐",
        description="激活点从尾到头逆向追逐。",
        target_channels=MCH, icon="←",
        tags=["motor","chase","reverse","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",2.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.2,0.02,0.8,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_rev)

    # F3 双向追逐（两个点对冲）
    def _chase_dual(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.15))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i / n
            r1 = _soft_pulse(t * speed - ph, width)
            r2 = _soft_pulse(-t * speed - ph, width)
            raw = max(r1, r2)
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_dual_v34", name="双向追逐", category="电机/追逐",
        description="两个激活点同时从两端对冲追逐。",
        target_channels=MCH, icon="←→",
        tags=["motor","chase","dual","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",2.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.15,0.02,0.8,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_dual)

    # F4 中心扩散追逐
    def _chase_center_out(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.2))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - (n - 1) / 2.0) / (n / 2.0)
            raw = _soft_pulse(t * speed - dist, width)
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_center_out_v34", name="中心扩散追逐", category="电机/追逐",
        description="激活点从中心向两端扩散追逐。",
        target_channels=MCH, icon="<→>",
        tags=["motor","chase","center","expand","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",2.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.2,0.02,0.8,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_center_out)

    # F5 边缘收拢追逐
    def _chase_edge_in(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.2))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - (n - 1) / 2.0) / (n / 2.0)
            raw = _soft_pulse(-t * speed - dist, width)
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_edge_in_v34", name="边缘收拢追逐", category="电机/追逐",
        description="激活点从两端向中心收拢追逐。",
        target_channels=MCH, icon=">←<",
        tags=["motor","chase","edge","converge","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",2.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.2,0.02,0.8,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_edge_in)

    # F6 宽带追逐（宽脉冲）
    def _chase_wide(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        width = float(params.get("width", 0.5))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i / n
            raw = _soft_pulse(t * speed - ph, width)
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_wide_v34", name="宽带追逐", category="电机/追逐",
        description="宽脉冲追逐，同时激活多个设备，形成宽带扫过效果。",
        target_channels=MCH, icon="▬→",
        tags=["motor","chase","wide","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.5,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.5,0.1,0.9,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_wide)

    # F7 窄带追逐（极窄脉冲）
    def _chase_narrow(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 3.0))
        width = float(params.get("width", 0.08))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i / n
            raw = _soft_pulse(t * speed - ph, width)
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_narrow_v34", name="窄带追逐", category="电机/追逐",
        description="极窄脉冲追逐，每次只激活单个设备，精准快速。",
        target_channels=MCH, icon="·→",
        tags=["motor","chase","narrow","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",3.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.08,0.01,0.4,0.005,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_narrow)

    # F8 双点追逐（两个点同向）
    def _chase_double(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.12))
        gap = float(params.get("gap", 0.5))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i / n
            r1 = _soft_pulse(t * speed - ph, width)
            r2 = _soft_pulse(t * speed - ph + gap, width)
            raw = max(r1, r2)
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_double_v34", name="双点追逐", category="电机/追逐",
        description="两个激活点同向追逐，间距可调。",
        target_channels=MCH, icon="··→",
        tags=["motor","chase","double","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",2.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.12,0.01,0.4,0.005,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("gap","间距","float",0.5,0.1,0.9,0.05,
                tooltip="控制设备/元素之间的间隔。值越大间距越宽，视觉上越分散"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_double)

    # F9 三点追逐
    def _chase_triple(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        width = float(params.get("width", 0.1))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i / n
            raw = max(
                _soft_pulse(t * speed - ph, width),
                _soft_pulse(t * speed - ph + 1/3.0, width),
                _soft_pulse(t * speed - ph + 2/3.0, width),
            )
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_triple_v34", name="三点追逐", category="电机/追逐",
        description="三个等间距激活点同向追逐，形成三重追逐效果。",
        target_channels=MCH, icon="···→",
        tags=["motor","chase","triple","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",2.0,0.05,20.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width","脉冲宽度","float",0.1,0.01,0.4,0.005,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_triple)

    # F10 随机追逐（随机激活）
    def _chase_random(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        density = float(params.get("density", 0.2))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seed = seq_i * 1000 + int(t * speed * 10)
            raw = 1.0 if _hash(seed) < density else 0.0
            ef.values[orig_i][ch] = _clamp(lo + (hi - lo) * raw)
    reg(EffectPreset(
        id="motor_chase_random_v34", name="随机激活", category="电机/追逐",
        description="随机激活设备，密度可调，产生随机跳动效果。",
        target_channels=MCH, icon="?→",
        tags=["motor","chase","random","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","变化速度","float",2.0,0.1,20.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("density","激活密度","float",0.2,0.05,0.95,0.05,
                tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
            ParamSpec("lo","低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _chase_random)

    # ═══════════════════════════════════════════════════════════════════════════
    #  G. 位置保持/定位变式（设备保持在特定角度）
    # ═══════════════════════════════════════════════════════════════════════════

    # G1 全部保持固定值
    def _hold_fixed(ef, t, sorted_fx, fdefs, params, fps):
        val = int(params.get("val", 128))
        ch = params.get("ch", "motor_x")
        for orig_i, fi in [(iv[0], iv[1]) for iv in sorted_fx]:
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_hold_fixed_v34", name="固定位置", category="电机/位置",
        description="所有设备保持在固定值，用于定位。",
        target_channels=MCH, icon="—",
        tags=["motor","hold","fixed","position","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("val","固定值","int",128,0,255,1,
                tooltip="控制固定输出值。效果持续输出此值不随时间变化"),
        ],
    ), _hold_fixed)

    # G2 线性渐变分布（从左到右线性分布）
    def _linear_spread(ef, t, sorted_fx, fdefs, params, fps):
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            v = lo + (hi - lo) * (seq_i / max(1, n - 1))
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_linear_spread_v34", name="线性渐变分布", category="电机/位置",
        description="设备从低到高线性分布，形成渐变造型。",
        target_channels=MCH, icon="▁▂▃▄▅▆▇",
        tags=["motor","spread","linear","position","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("lo","最低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","最高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _linear_spread)

    # G3 反向线性渐变分布
    def _linear_spread_rev(ef, t, sorted_fx, fdefs, params, fps):
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            v = hi - (hi - lo) * (seq_i / max(1, n - 1))
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_linear_spread_rev_v34", name="反向线性渐变分布", category="电机/位置",
        description="设备从高到低线性分布，形成反向渐变造型。",
        target_channels=MCH, icon="▇▆▅▄▃▂▁",
        tags=["motor","spread","linear","reverse","position","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("lo","最低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","最高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _linear_spread_rev)

    # G4 中心高两端低分布
    def _center_high_spread(ef, t, sorted_fx, fdefs, params, fps):
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - (n - 1) / 2.0) / (n / 2.0)
            v = lo + (hi - lo) * (1.0 - dist)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_center_high_spread_v34", name="中心高两端低", category="电机/位置",
        description="中间设备值高，两端低，形成拱形分布。",
        target_channels=MCH, icon="▁▃▅▇▅▃▁",
        tags=["motor","spread","center","arch","position","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("lo","最低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","最高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _center_high_spread)

    # G5 两端高中心低分布
    def _edge_high_spread(ef, t, sorted_fx, fdefs, params, fps):
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - (n - 1) / 2.0) / (n / 2.0)
            v = lo + (hi - lo) * dist
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_edge_high_spread_v34", name="两端高中心低", category="电机/位置",
        description="两端设备值高，中间低，形成V形分布。",
        target_channels=MCH, icon="▇▅▃▁▃▅▇",
        tags=["motor","spread","edge","V","position","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("lo","最低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","最高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _edge_high_spread)

    # G6 奇偶交替分布
    def _alt_spread(ef, t, sorted_fx, fdefs, params, fps):
        val_a = int(params.get("val_a", 64))
        val_b = int(params.get("val_b", 192))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ef.values[orig_i][ch] = _clamp(val_a if seq_i % 2 == 0 else val_b)
    reg(EffectPreset(
        id="motor_alt_spread_v34", name="奇偶交替分布", category="电机/位置",
        description="奇偶设备交替设置不同值，形成交替造型。",
        target_channels=MCH, icon="▁▇▁▇",
        tags=["motor","spread","alternate","position","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("val_a","奇数值","int",64,0,255,1,
                tooltip="控制交替模式中第一组的值。用于奇偶分组交替输出"),
            ParamSpec("val_b","偶数值","int",192,0,255,1,
                tooltip="控制交替模式中第二组的值。用于奇偶分组交替输出"),
        ],
    ), _alt_spread)

    # G7 移动渐变分布（渐变分布随时间移动）
    def _moving_spread(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        lo = int(params.get("lo", 28))
        hi = int(params.get("hi", 228))
        ch = params.get("ch", "motor_x")
        n = max(1, len(sorted_fx))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = (seq_i / n + t * speed) % 1.0
            v = lo + (hi - lo) * ph
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_moving_spread_v34", name="移动渐变分布", category="电机/位置",
        description="渐变分布随时间缓慢移动，形成流动的渐变效果。",
        target_channels=MCH, icon="▁▂▃▄→",
        tags=["motor","spread","moving","position","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","移动速度","float",0.5,0.01,10.0,0.01,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo","最低值","int",28,0,255,1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi","最高值","int",228,0,255,1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _moving_spread)

    # ═══════════════════════════════════════════════════════════════════════════
    #  H. 抖动/颤动变式
    # ═══════════════════════════════════════════════════════════════════════════

    # H1 快速抖动（高频小幅）
    def _fast_jitter(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 8.0))
        amplitude = float(params.get("amplitude", 20.0))
        center = float(params.get("center", 128.0))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * 0.13
            v = center + amplitude * _sine_raw(t * speed + ph)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_fast_jitter_v34", name="快速抖动", category="电机/抖动",
        description="高频小幅抖动，模拟震动效果。",
        target_channels=MCH, icon="≈",
        tags=["motor","jitter","fast","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","频率","float",8.0,1.0,30.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",20.0,1.0,80.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _fast_jitter)

    # H2 随机抖动（每设备独立随机）
    def _random_jitter(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 5.0))
        amplitude = float(params.get("amplitude", 30.0))
        center = float(params.get("center", 128.0))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seed = seq_i * 997 + int(t * speed * 20)
            rand = (_hash(seed) - 0.5) * 2.0
            v = center + amplitude * rand
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_random_jitter_v34", name="随机抖动", category="电机/抖动",
        description="每设备独立随机抖动，产生有机颤动效果。",
        target_channels=MCH, icon="≋",
        tags=["motor","jitter","random","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","变化速度","float",5.0,0.5,30.0,0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度","float",30.0,1.0,100.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _random_jitter)

    # H3 双频抖动（两频叠加）
    def _dual_jitter(ef, t, sorted_fx, fdefs, params, fps):
        speed1 = float(params.get("speed1", 6.0))
        speed2 = float(params.get("speed2", 13.7))
        amp1 = float(params.get("amp1", 25.0))
        amp2 = float(params.get("amp2", 10.0))
        center = float(params.get("center", 128.0))
        ch = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ph = seq_i * 0.11
            v = center + amp1 * _sine_raw(t * speed1 + ph) + amp2 * _sine_raw(t * speed2 + ph * 1.7)
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_dual_jitter_v34", name="双频抖动", category="电机/抖动",
        description="两个频率叠加的抖动，产生复杂颤动效果。",
        target_channels=MCH, icon="≈≈",
        tags=["motor","jitter","dual","v34"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed1","主频","float",6.0,1.0,30.0,0.5,
                tooltip="控制第一层（低频）效果的速度。值越小越缓慢，值越大越急促"),
            ParamSpec("speed2","副频","float",13.7,1.0,30.0,0.5,
                tooltip="控制第二层（高频）效果的速度。值越小越缓慢，值越大越急促"),
            ParamSpec("amp1","主幅度","float",25.0,1.0,80.0,1.0,
                tooltip="控制第一层效果的幅度大小。值越大该层影响越明显"),
            ParamSpec("amp2","副幅度","float",10.0,1.0,50.0,1.0,
                tooltip="控制第二层效果的幅度大小。值越大该层影响越明显"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _dual_jitter)


# ═══════════════════════════════════════════════════════════════════════════
#  公开注册入口
# ═══════════════════════════════════════════════════════════════════════════

def register_v34(engine):
    """注册所有 v34 单轴基础电机动作变式"""
    _register_v34_presets(engine)
