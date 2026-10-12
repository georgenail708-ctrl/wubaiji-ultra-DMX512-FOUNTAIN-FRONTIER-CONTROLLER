"""
effect_presets_v35.py — XY 双轴联动电机动作大量变式 v35
==========================================================
新增约 100 个 XY 双轴联动电机动作，覆盖：
  A. 22/33/44/66/88 交叉系列（每 N 个同向，下 N 个反向，丰富参数）
  B. 扇形开合系列（单扇/多扇/相位差/定位/偏移/漂移）
  C. 圆形/椭圆/8字/利萨如 XY 联动
  D. XY 波浪/图案系列（含整体漂移参数）
  E. XY 追逐/龙摆系列
  F. XY 开合/镜像系列

设计原则：
  - 每个动作通过丰富参数覆盖多种变化效果
  - 速度参数最小值为 0（静止）
  - 提供"整体偏移"参数（drift_speed/drift_dir）让主体左右漂移
  - 提供 enable_x / enable_y 独立开关
  - 参数范围宽（角度允许负值，相位差 0~1，幅度 0~最大）
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

def _sine_raw(x):
    return math.sin(2.0 * math.pi * x)

def _tri_raw(x):
    x = x % 1.0
    return 2.0 * abs(2.0 * x - 1.0) - 1.0   # -1..+1 三角波

def _saw_raw(x):
    return 2.0 * (x % 1.0) - 1.0             # -1..+1 锯齿

def _pulse_raw(x, w=0.5):
    return 1.0 if (x % 1.0) < _clampf(w, 0.01, 0.99) else -1.0

def _ease_inout(x):
    x = _clampf(x)
    return x * x * (3.0 - 2.0 * x)

def _bounce_raw(x):
    x = x % 1.0
    v = 1.0 - abs(2.0 * x - 1.0)
    return 2.0 * v * v * (3.0 - 2.0 * v) - 1.0  # -1..+1

def _hash(seed):
    h = (int(seed) * 1103515245 + 12345) & 0x7fffffff
    h ^= (h >> 11)
    h = (h * 2654435761) & 0xffffffff
    return (h & 0xffff) / 65535.0

def _wave(wtype, x):
    """统一波形函数，返回 -1..+1"""
    if wtype == "正弦":
        return _sine_raw(x)
    elif wtype == "三角":
        return _tri_raw(x)
    elif wtype == "锯齿上":
        return _saw_raw(x)
    elif wtype == "锯齿下":
        return -_saw_raw(x)
    elif wtype == "弹跳":
        return _bounce_raw(x)
    elif wtype == "脉冲":
        return _pulse_raw(x, 0.5)
    else:
        return _sine_raw(x)

def _val(center, amp, raw):
    """将 -1..+1 的 raw 映射到 DMX 值"""
    return _clamp(center + amp * raw * 127.0 / 90.0 * 127.0)

def _deg2dmx(deg, center=128.0):
    """角度（度）→ DMX，center=128 对应 0 度"""
    return _clamp(center + deg * 255.0 / 360.0)

# ── 常量 ──────────────────────────────────────────────────────────────────────
MCH = ["motor_x", "motor_y", "motor_z", "speed", "pan", "tilt"]
WAVE_CHOICES = ["正弦", "三角", "锯齿上", "锯齿下", "弹跳", "脉冲"]

# ═══════════════════════════════════════════════════════════════════════════════
#  注册函数
# ═══════════════════════════════════════════════════════════════════════════════
def _register_v35_presets(engine: "EffectEngine"):
    from .effect_engine import EffectPreset, ParamSpec

    def reg(preset, fn):
        engine._reg(preset, fn)

    # ═══════════════════════════════════════════════════════════════════════
    #  A. 22/33/44/66/88 交叉系列
    #     "22交叉"定义：每 N 个同向，下 N 个反向，同时运动
    #     通过 group_phase_diff 参数控制组间时差（0=完全同步，0.5=交替）
    # ═══════════════════════════════════════════════════════════════════════

    # A1: 22 交叉（每2个同向，下2个反向）
    def _cross22(ef, t, sorted_fx, fdefs, params, fps):
        speed   = float(params.get("speed", 1.0))
        amp     = float(params.get("amplitude", 60.0))
        center  = float(params.get("center", 128.0))
        gphase  = float(params.get("group_phase_diff", 0.0))
        wtype   = params.get("wave", "正弦")
        ch      = params.get("ch", "motor_x")
        invert  = bool(params.get("invert_odd", False))
        drift_s = float(params.get("drift_speed", 0.0))
        drift_d = float(params.get("drift_dir", 1.0))
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_s * drift_d) * float(params.get("drift_amp", 0.0))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            pos_in_group = seq_i % 2
            # 同组内：pos 0,1 同向；偶数组正，奇数组反
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            if invert:
                direction = -direction
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            # 加入整体漂移（改变每台设备的相对位置感）
            drift_offset = drift * (seq_i / max(1, n - 1) - 0.5)
            v = center + amp * raw + drift_offset
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross22_v35", name="22交叉", category="电机/交叉",
        description="每2台同向，下2台反向。group_phase_diff=0全同步，=0.5交替开合。支持整体漂移。",
        target_channels=["motor_x","motor_y"], icon="⇄2",
        tags=["motor","cross","22","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
            ParamSpec("invert_odd","奇数组反转","bool",False,
                tooltip="控制是否反转奇数分组的输出极性。实现奇偶交错效果"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度","float",0.0,0.0,60.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
            ParamSpec("drift_dir","漂移方向","float",1.0,-1.0,1.0,0.1,
                tooltip="控制漂移运动的方向。决定漂移在哪个空间方向上进行"),
        ],
    ), _cross22)

    # A2: 33 交叉（每3个同向，下3个反向）
    def _cross33(ef, t, sorted_fx, fdefs, params, fps):
        speed   = float(params.get("speed", 1.0))
        amp     = float(params.get("amplitude", 60.0))
        center  = float(params.get("center", 128.0))
        gphase  = float(params.get("group_phase_diff", 0.0))
        wtype   = params.get("wave", "正弦")
        ch      = params.get("ch", "motor_x")
        drift_s = float(params.get("drift_speed", 0.0))
        drift_amp = float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 3
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            drift_offset = drift * (seq_i / max(1, n - 1) - 0.5)
            v = center + amp * raw + drift_offset
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross33_v35", name="33交叉", category="电机/交叉",
        description="每3台同向，下3台反向。组间相位差可调，支持整体漂移。",
        target_channels=["motor_x","motor_y"], icon="⇄3",
        tags=["motor","cross","33","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度","float",0.0,0.0,60.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _cross33)

    # A3: 44 交叉（每4个同向，下4个反向）
    def _cross44(ef, t, sorted_fx, fdefs, params, fps):
        speed   = float(params.get("speed", 1.0))
        amp     = float(params.get("amplitude", 60.0))
        center  = float(params.get("center", 128.0))
        gphase  = float(params.get("group_phase_diff", 0.0))
        wtype   = params.get("wave", "正弦")
        ch      = params.get("ch", "motor_x")
        drift_s = float(params.get("drift_speed", 0.0))
        drift_amp = float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 4
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            drift_offset = drift * (seq_i / max(1, n - 1) - 0.5)
            v = center + amp * raw + drift_offset
            ef.values[orig_i][ch] = _clamp(v)
    reg(EffectPreset(
        id="motor_cross44_v35", name="44交叉", category="电机/交叉",
        description="每4台同向，下4台反向。组间相位差可调，支持整体漂移。",
        target_channels=["motor_x","motor_y"], icon="⇄4",
        tags=["motor","cross","44","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度","float",0.0,0.0,60.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _cross44)

    # A4: 66 交叉
    def _cross66(ef, t, sorted_fx, fdefs, params, fps):
        speed   = float(params.get("speed", 1.0))
        amp     = float(params.get("amplitude", 60.0))
        center  = float(params.get("center", 128.0))
        gphase  = float(params.get("group_phase_diff", 0.0))
        wtype   = params.get("wave", "正弦")
        ch      = params.get("ch", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 6
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            ef.values[orig_i][ch] = _clamp(center + amp * raw)
    reg(EffectPreset(
        id="motor_cross66_v35", name="66交叉", category="电机/交叉",
        description="每6台同向，下6台反向。",
        target_channels=["motor_x","motor_y"], icon="⇄6",
        tags=["motor","cross","66","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
        ],
    ), _cross66)

    # A5: 88 交叉
    def _cross88(ef, t, sorted_fx, fdefs, params, fps):
        speed   = float(params.get("speed", 1.0))
        amp     = float(params.get("amplitude", 60.0))
        center  = float(params.get("center", 128.0))
        gphase  = float(params.get("group_phase_diff", 0.0))
        wtype   = params.get("wave", "正弦")
        ch      = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 8
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            ef.values[orig_i][ch] = _clamp(center + amp * raw)
    reg(EffectPreset(
        id="motor_cross88_v35", name="88交叉", category="电机/交叉",
        description="每8台同向，下8台反向。",
        target_channels=["motor_x","motor_y"], icon="⇄8",
        tags=["motor","cross","88","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
        ],
    ), _cross88)

    # A6: 通用 NN 交叉（可配置每组大小）
    def _cross_nn(ef, t, sorted_fx, fdefs, params, fps):
        speed     = float(params.get("speed", 1.0))
        amp       = float(params.get("amplitude", 60.0))
        center    = float(params.get("center", 128.0))
        gphase    = float(params.get("group_phase_diff", 0.0))
        group_sz  = max(1, int(params.get("group_size", 2)))
        wtype     = params.get("wave", "正弦")
        ch        = params.get("ch", "motor_x")
        drift_s   = float(params.get("drift_speed", 0.0))
        drift_amp = float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // group_sz
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            drift_offset = drift * (seq_i / max(1, n - 1) - 0.5)
            ef.values[orig_i][ch] = _clamp(center + amp * raw + drift_offset)
    reg(EffectPreset(
        id="motor_cross_nn_v35", name="NN交叉（通用）", category="电机/交叉",
        description="可配置每组大小的交叉动作。group_size=2即22交叉，=4即44交叉。",
        target_channels=["motor_x","motor_y"], icon="⇄N",
        tags=["motor","cross","nn","configurable","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("group_size","每组大小","int",2,1,16,1,
                tooltip="控制每组包含的设备数量。值越大同组运行的设备越多"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度","float",0.0,0.0,60.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _cross_nn)

    # A7: 22 交叉 XY 联动（X轴22交叉，Y轴同步或独立）
    def _cross22_xy(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 0.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        gphase   = float(params.get("group_phase_diff", 0.0))
        xy_phase = float(params.get("xy_phase_diff", 0.25))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", False))
        drift_s  = float(params.get("drift_speed", 0.0))
        drift_amp= float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            drift_off = drift * (seq_i / max(1, n - 1) - 0.5)
            if en_x:
                raw_x = _wave(wtype, phase) * direction
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x + drift_off)
            if en_y:
                raw_y = _wave(wtype, phase + xy_phase) * direction
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y + drift_off)
    reg(EffectPreset(
        id="motor_cross22_xy_v35", name="22交叉XY联动", category="电机/交叉",
        description="22交叉同时控制X/Y轴，XY可独立开关，XY相位差可调。",
        target_channels=["motor_x","motor_y"], icon="⇄2XY",
        tags=["motor","cross","22","xy","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
            ParamSpec("xy_phase_diff","XY相位差","float",0.25,0.0,1.0,0.01,
                tooltip="控制 X 轴与 Y 轴运动的相位差值。决定了椭圆或利萨如图形的形状"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度","float",0.0,0.0,60.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _cross22_xy)

    # A8: 44 交叉 XY 联动
    def _cross44_xy(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 30.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        gphase   = float(params.get("group_phase_diff", 0.0))
        xy_phase = float(params.get("xy_phase_diff", 0.25))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 4
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            if en_x:
                raw_x = _wave(wtype, phase) * direction
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x)
            if en_y:
                raw_y = _wave(wtype, phase + xy_phase) * direction
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y)
    reg(EffectPreset(
        id="motor_cross44_xy_v35", name="44交叉XY联动", category="电机/交叉",
        description="44交叉同时控制X/Y轴，XY可独立开关，XY相位差可调。",
        target_channels=["motor_x","motor_y"], icon="⇄4XY",
        tags=["motor","cross","44","xy","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
            ParamSpec("xy_phase_diff","XY相位差","float",0.25,0.0,1.0,0.01,
                tooltip="控制 X 轴与 Y 轴运动的相位差值。决定了椭圆或利萨如图形的形状"),
        ],
    ), _cross44_xy)

    # A9: 22交叉反转变式（奇数组反转，偶数组正转）
    def _cross22_rev(ef, t, sorted_fx, fdefs, params, fps):
        speed   = float(params.get("speed", 1.0))
        amp     = float(params.get("amplitude", 60.0))
        center  = float(params.get("center", 128.0))
        gphase  = float(params.get("group_phase_diff", 0.5))
        wtype   = params.get("wave", "正弦")
        ch      = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            # 反转：奇数组反转方向
            if group_idx % 2 == 1:
                direction = -direction
            raw = _wave(wtype, phase) * direction
            ef.values[orig_i][ch] = _clamp(center + amp * raw)
    reg(EffectPreset(
        id="motor_cross22_rev_v35", name="22交叉反转", category="电机/交叉",
        description="22交叉，奇数组反转方向，默认组间相位差0.5形成交替效果。",
        target_channels=["motor_x","motor_y"], icon="⇄2↩",
        tags=["motor","cross","22","reverse","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.5,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
        ],
    ), _cross22_rev)

    # A10: 44交叉反转变式
    def _cross44_rev(ef, t, sorted_fx, fdefs, params, fps):
        speed   = float(params.get("speed", 1.0))
        amp     = float(params.get("amplitude", 60.0))
        center  = float(params.get("center", 128.0))
        gphase  = float(params.get("group_phase_diff", 0.5))
        wtype   = params.get("wave", "正弦")
        ch      = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 4
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            if group_idx % 2 == 1:
                direction = -direction
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            ef.values[orig_i][ch] = _clamp(center + amp * raw)
    reg(EffectPreset(
        id="motor_cross44_rev_v35", name="44交叉反转", category="电机/交叉",
        description="44交叉，奇数组反转方向，默认组间相位差0.5。",
        target_channels=["motor_x","motor_y"], icon="⇄4↩",
        tags=["motor","cross","44","reverse","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.5,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
        ],
    ), _cross44_rev)

    # A11: 22交叉追逐（组间相位差形成追逐感）
    def _cross22_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed   = float(params.get("speed", 1.0))
        amp     = float(params.get("amplitude", 60.0))
        center  = float(params.get("center", 128.0))
        gphase  = float(params.get("group_phase_diff", 0.15))
        wtype   = params.get("wave", "正弦")
        ch      = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            ef.values[orig_i][ch] = _clamp(center + amp * raw)
    reg(EffectPreset(
        id="motor_cross22_chase_v35", name="22交叉追逐", category="电机/交叉",
        description="22交叉，组间相位差产生追逐感，调大相位差可变为波浪。",
        target_channels=["motor_x","motor_y"], icon="⇄2→",
        tags=["motor","cross","22","chase","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
        ],
    ), _cross22_chase)

    # A12: 44交叉追逐
    def _cross44_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed   = float(params.get("speed", 1.0))
        amp     = float(params.get("amplitude", 60.0))
        center  = float(params.get("center", 128.0))
        gphase  = float(params.get("group_phase_diff", 0.12))
        wtype   = params.get("wave", "正弦")
        ch      = params.get("ch", "motor_x")
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 4
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            ef.values[orig_i][ch] = _clamp(center + amp * raw)
    reg(EffectPreset(
        id="motor_cross44_chase_v35", name="44交叉追逐", category="电机/交叉",
        description="44交叉，组间相位差产生追逐感。",
        target_channels=["motor_x","motor_y"], icon="⇄4→",
        tags=["motor","cross","44","chase","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.12,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
        ],
    ), _cross44_chase)

    # ═══════════════════════════════════════════════════════════════════════
    #  B. 扇形开合系列
    #     核心参数：
    #       fans          - 扇子数量（1~8）
    #       fan_phase_diff - 扇子间相位差（0=全同步，0.5=交替开合，0.25=波浪）
    #       open_speed    - 开合速度
    #       fan_angle     - 每把扇子的展开角度
    #       center_offset - 整体中心偏移（定位）
    #       drift_speed   - 主体漂移速度
    #       drift_dir     - 漂移方向（+1左→右，-1右→左）
    # ═══════════════════════════════════════════════════════════════════════

    # B1: 多扇形开合（核心动作，参数最丰富）
    def _multi_fan_open_close(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 2)))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        speed       = float(params.get("open_speed", 0.5))
        fan_angle   = float(params.get("fan_angle", 60.0))
        center_off  = float(params.get("center_offset", 0.0))
        drift_s     = float(params.get("drift_speed", 0.0))
        drift_dir   = float(params.get("drift_dir", 1.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        en_x        = bool(params.get("enable_x", True))
        en_y        = bool(params.get("enable_y", False))
        n = len(sorted_fx)
        if n == 0:
            return
        # 整体主体漂移：让扇形中心随时间左右移动
        drift_offset = _sine_raw(t * drift_s * drift_dir) * float(params.get("drift_amp", 0.0))
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            # 扇内位置比例（-1..+1，中心为0）
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 每把扇子的开合量（由相位差决定各扇子的开合时机）
            fan_t = t * speed + fan_idx * fan_phase
            open_amt = _wave(wtype, fan_t)  # -1..+1
            # 扇内角度：local_ratio * fan_angle/2 * open_amt
            angle = local_ratio * fan_angle * 0.5 * open_amt + center_off + drift_offset
            val = _deg2dmx(angle)
            if en_x:
                ef.values[orig_i]["motor_x"] = val
            if en_y:
                ef.values[orig_i]["motor_y"] = val
    reg(EffectPreset(
        id="motor_multi_fan_open_close_v35", name="多扇形开合", category="电机/扇形",
        description="多把扇子同步/异步开合。fan_phase_diff=0全同步，=0.5交替，=0.25波浪。支持整体漂移。",
        target_channels=["motor_x","motor_y"], icon="⊃⊂N",
        tags=["motor","fan","open","close","multi","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("ch","单轴通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",2,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_phase_diff","扇子间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("open_speed","开合速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制展开速度。值越大从闭合到完全展开越快"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("center_offset","中心偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制中心位置的偏移量。使整个图案的中心偏离原始位置"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
            ParamSpec("drift_dir","漂移方向","float",1.0,-1.0,1.0,0.1,
                tooltip="控制漂移运动的方向。决定漂移在哪个空间方向上进行"),
        ],
    ), _multi_fan_open_close)

    # B2: 扇形定位（固定相位差，整体角度偏移）
    def _fan_position(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 3)))
        fan_angle   = float(params.get("fan_angle", 60.0))
        phase_diff  = float(params.get("phase_diff", 0.33))
        global_off  = float(params.get("global_offset", 0.0))
        drift_s     = float(params.get("drift_speed", 0.0))
        drift_dir   = float(params.get("drift_dir", 1.0))
        drift_amp   = float(params.get("drift_amp", 0.0))
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        # 整体偏移随时间漂移
        drift = _sine_raw(t * drift_s * drift_dir) * drift_amp + global_off
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 每把扇子固定在不同角度位置（由相位差决定）
            fan_center = fan_idx * phase_diff * fan_angle - (fans - 1) * phase_diff * fan_angle * 0.5
            angle = local_ratio * fan_angle * 0.5 + fan_center + drift
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_position_v35", name="扇形定位", category="电机/扇形",
        description="多把扇子固定在不同角度位置，整体可漂移。phase_diff控制扇间角度间隔。",
        target_channels=["motor_x","motor_y"], icon="⊃|⊂",
        tags=["motor","fan","position","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",3,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("phase_diff","扇间角度间隔","float",0.33,0.0,2.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("global_offset","整体偏移(度)","float",0.0,-180.0,180.0,1.0,
                tooltip="控制整体全局偏移量。所有设备同时平移相同距离"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
            ParamSpec("drift_dir","漂移方向","float",1.0,-1.0,1.0,0.1,
                tooltip="控制漂移运动的方向。决定漂移在哪个空间方向上进行"),
        ],
    ), _fan_position)

    # B3: 扇形波浪开合（扇子间相位差形成波浪）
    def _fan_wave_open(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(2, int(params.get("fans", 4)))
        speed       = float(params.get("speed", 0.5))
        fan_angle   = float(params.get("fan_angle", 60.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        drift_s     = float(params.get("drift_speed", 0.0))
        drift_amp   = float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        if n == 0:
            return
        drift = _sine_raw(t * drift_s) * drift_amp
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 波浪相位：每把扇子相位均匀分布
            fan_phase = fan_idx / fans
            open_amt = _wave(wtype, t * speed + fan_phase)
            angle = local_ratio * fan_angle * 0.5 * open_amt + drift
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_wave_open_v35", name="扇形波浪开合", category="电机/扇形",
        description="多把扇子相位均匀分布，形成波浪式开合效果。",
        target_channels=["motor_x","motor_y"], icon="⊃~⊂",
        tags=["motor","fan","wave","open","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",4,2,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _fan_wave_open)

    # B4: 扇形交替开合（奇数扇开时偶数扇合）
    def _fan_alt_open(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(2, int(params.get("fans", 4)))
        speed       = float(params.get("speed", 0.5))
        fan_angle   = float(params.get("fan_angle", 60.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        drift_s     = float(params.get("drift_speed", 0.0))
        drift_amp   = float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        if n == 0:
            return
        drift = _sine_raw(t * drift_s) * drift_amp
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 奇偶扇子相位差0.5（交替）
            fan_phase = 0.5 if fan_idx % 2 == 1 else 0.0
            open_amt = _wave(wtype, t * speed + fan_phase)
            angle = local_ratio * fan_angle * 0.5 * open_amt + drift
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_alt_open_v35", name="扇形交替开合", category="电机/扇形",
        description="奇数扇开时偶数扇合，形成交替呼吸效果。",
        target_channels=["motor_x","motor_y"], icon="⊃↔⊂",
        tags=["motor","fan","alternate","open","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",4,2,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _fan_alt_open)

    # B5: 扇形整体摆动（整把扇子左右摆，内部保持展开）
    def _fan_global_swing(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 2)))
        swing_speed = float(params.get("swing_speed", 0.3))
        swing_amp   = float(params.get("swing_amp", 30.0))
        fan_angle   = float(params.get("fan_angle", 60.0))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 整体摆动
            swing = _wave(wtype, t * swing_speed + fan_idx * fan_phase) * swing_amp
            # 内部展开固定
            angle = local_ratio * fan_angle * 0.5 + swing
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_global_swing_v35", name="扇形整体摆动", category="电机/扇形",
        description="整把扇子保持展开，整体左右摆动。多扇可设相位差。",
        target_channels=["motor_x","motor_y"], icon="⊃↕⊂",
        tags=["motor","fan","swing","global","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",2,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("swing_speed","摆动速度","float",0.3,0.0,5.0,0.05,
                tooltip="控制摆动速度。值越大来回摆动的频率越高"),
            ParamSpec("swing_amp","摆动幅度(度)","float",30.0,0.0,90.0,1.0,
                tooltip="控制摆动运动的幅度。值越大摆动角度范围越广"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _fan_global_swing)

    # B6: 扇形漂移（主体整体向一侧漂移，同时开合）
    def _fan_drift(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 2)))
        open_speed  = float(params.get("open_speed", 0.5))
        fan_angle   = float(params.get("fan_angle", 60.0))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        drift_speed = float(params.get("drift_speed", 0.2))
        drift_amp   = float(params.get("drift_amp", 40.0))
        drift_dir   = float(params.get("drift_dir", 1.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        # 整体漂移
        drift = _sine_raw(t * drift_speed * drift_dir) * drift_amp
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            open_amt = _wave(wtype, t * open_speed + fan_idx * fan_phase)
            angle = local_ratio * fan_angle * 0.5 * open_amt + drift
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_drift_v35", name="扇形漂移", category="电机/扇形",
        description="扇形开合同时整体左右漂移，漂移速度和方向可调。",
        target_channels=["motor_x","motor_y"], icon="⊃→⊂",
        tags=["motor","fan","drift","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",2,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("open_speed","开合速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制展开速度。值越大从闭合到完全展开越快"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.2,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
            ParamSpec("drift_dir","漂移方向","float",1.0,-1.0,1.0,0.1,
                tooltip="控制漂移运动的方向。决定漂移在哪个空间方向上进行"),
        ],
    ), _fan_drift)

    # B7: 扇形呼吸（整体开合，无内部展开，像呼吸）
    def _fan_breath(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 3)))
        speed       = float(params.get("speed", 0.4))
        fan_angle   = float(params.get("fan_angle", 80.0))
        fan_phase   = float(params.get("fan_phase_diff", 0.33))
        center_off  = float(params.get("center_offset", 0.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        drift_s     = float(params.get("drift_speed", 0.0))
        drift_amp   = float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        if n == 0:
            return
        drift = _sine_raw(t * drift_s) * drift_amp
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            open_amt = (_wave(wtype, t * speed + fan_idx * fan_phase) + 1.0) * 0.5  # 0..1
            angle = local_ratio * fan_angle * 0.5 * open_amt + center_off + drift
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_breath_v35", name="扇形呼吸", category="电机/扇形",
        description="多扇形呼吸式开合，fan_phase_diff控制各扇相位差。",
        target_channels=["motor_x","motor_y"], icon="⊃♡⊂",
        tags=["motor","fan","breath","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",3,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("speed","速度","float",0.4,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fan_angle","扇形角度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.33,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("center_offset","中心偏移(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制中心位置的偏移量。使整个图案的中心偏离原始位置"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _fan_breath)

    # B8: 扇形追逐（扇子依次开合，形成追逐感）
    def _fan_chase(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(2, int(params.get("fans", 4)))
        speed       = float(params.get("speed", 0.5))
        fan_angle   = float(params.get("fan_angle", 60.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        direction   = float(params.get("direction", 1.0))
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 追逐：相位按顺序递增
            chase_phase = fan_idx / fans * direction
            open_amt = _wave(wtype, t * speed + chase_phase)
            angle = local_ratio * fan_angle * 0.5 * open_amt
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_chase_v35", name="扇形追逐", category="电机/扇形",
        description="扇子依次开合形成追逐感，direction=-1可反向。",
        target_channels=["motor_x","motor_y"], icon="⊃→→⊂",
        tags=["motor","fan","chase","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",4,2,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("direction","追逐方向","float",1.0,-1.0,1.0,0.1,
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
        ],
    ), _fan_chase)

    # B9: 扇形XY联动开合（X轴展开，Y轴同步倾斜）
    def _fan_xy_open(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 2)))
        speed       = float(params.get("speed", 0.5))
        fan_angle_x = float(params.get("fan_angle_x", 60.0))
        fan_angle_y = float(params.get("fan_angle_y", 20.0))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        xy_phase    = float(params.get("xy_phase_diff", 0.25))
        wtype       = params.get("wave", "正弦")
        en_x        = bool(params.get("enable_x", True))
        en_y        = bool(params.get("enable_y", True))
        drift_s     = float(params.get("drift_speed", 0.0))
        drift_amp   = float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        if n == 0:
            return
        drift = _sine_raw(t * drift_s) * drift_amp
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            open_x = _wave(wtype, t * speed + fan_idx * fan_phase)
            open_y = _wave(wtype, t * speed + fan_idx * fan_phase + xy_phase)
            if en_x:
                angle_x = local_ratio * fan_angle_x * 0.5 * open_x + drift
                ef.values[orig_i]["motor_x"] = _deg2dmx(angle_x)
            if en_y:
                angle_y = local_ratio * fan_angle_y * 0.5 * open_y + drift
                ef.values[orig_i]["motor_y"] = _deg2dmx(angle_y)
    reg(EffectPreset(
        id="motor_fan_xy_open_v35", name="扇形XY联动开合", category="电机/扇形",
        description="X轴展开Y轴同步倾斜，XY可独立开关，XY相位差可调。",
        target_channels=["motor_x","motor_y"], icon="⊃XY⊂",
        tags=["motor","fan","xy","open","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("fans","扇子数量","int",2,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fan_angle_x","X扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的扇形展开角度。值越大水平扇面越宽"),
            ParamSpec("fan_angle_y","Y扇形角度(度)","float",20.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的扇形展开角度。值越大垂直扇面越宽"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("xy_phase_diff","XY相位差","float",0.25,0.0,1.0,0.01,
                tooltip="控制 X 轴与 Y 轴运动的相位差值。决定了椭圆或利萨如图形的形状"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _fan_xy_open)

    # B10: 扇形脉冲开合（快速脉冲式开合）
    def _fan_pulse_open(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 3)))
        speed       = float(params.get("speed", 2.0))
        fan_angle   = float(params.get("fan_angle", 60.0))
        pulse_width = float(params.get("pulse_width", 0.3))
        fan_phase   = float(params.get("fan_phase_diff", 0.33))
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            open_amt = _pulse_raw(t * speed + fan_idx * fan_phase, pulse_width)
            angle = local_ratio * fan_angle * 0.5 * open_amt
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_pulse_open_v35", name="扇形脉冲开合", category="电机/扇形",
        description="快速脉冲式开合，pulse_width控制开合占比，fan_phase_diff控制扇间时差。",
        target_channels=["motor_x","motor_y"], icon="⊃!⊂",
        tags=["motor","fan","pulse","open","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",3,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("speed","速度","float",2.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("pulse_width","脉冲宽度","float",0.3,0.01,0.99,0.01,
                tooltip="控制脉冲信号的高电平宽度。值越大高电平占时越长"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.33,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
        ],
    ), _fan_pulse_open)

    # B11: 扇形收拢展开（从中心向两侧展开，或从两侧向中心收拢）
    def _fan_spread_gather(ef, t, sorted_fx, fdefs, params, fps):
        speed       = float(params.get("speed", 0.4))
        fan_angle   = float(params.get("fan_angle", 90.0))
        mode        = params.get("mode", "展开")
        ch          = params.get("ch", "motor_x")
        drift_s     = float(params.get("drift_speed", 0.0))
        drift_amp   = float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        if n == 0:
            return
        drift = _sine_raw(t * drift_s) * drift_amp
        # 整体开合量
        open_amt = (_sine_raw(t * speed) + 1.0) * 0.5  # 0..1
        if mode == "收拢":
            open_amt = 1.0 - open_amt
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 从中心向两侧展开
            dist = (seq_i - mid) / max(1.0, mid)  # -1..+1
            angle = dist * fan_angle * 0.5 * open_amt + drift
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_spread_gather_v35", name="扇形收拢展开", category="电机/扇形",
        description="从中心向两侧展开或从两侧向中心收拢，mode可切换。",
        target_channels=["motor_x","motor_y"], icon="⊃↔⊂",
        tags=["motor","fan","spread","gather","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",0.4,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fan_angle","扇形角度(度)","float",90.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("mode","模式","choice","展开",choices=["展开","收拢","展开收拢"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _fan_spread_gather)

    # B12: 扇形旋转偏移（整体角度缓慢旋转）
    def _fan_rotate_offset(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 2)))
        fan_angle   = float(params.get("fan_angle", 60.0))
        rotate_speed= float(params.get("rotate_speed", 0.1))
        rotate_amp  = float(params.get("rotate_amp", 45.0))
        open_speed  = float(params.get("open_speed", 0.5))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        # 整体旋转偏移
        global_angle = _sine_raw(t * rotate_speed) * rotate_amp
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            open_amt = _wave(wtype, t * open_speed + fan_idx * fan_phase)
            angle = local_ratio * fan_angle * 0.5 * open_amt + global_angle
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_rotate_offset_v35", name="扇形旋转偏移", category="电机/扇形",
        description="扇形开合同时整体角度缓慢旋转偏移，产生动态漂移感。",
        target_channels=["motor_x","motor_y"], icon="⊃↺⊂",
        tags=["motor","fan","rotate","offset","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",2,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("open_speed","开合速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制展开速度。值越大从闭合到完全展开越快"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("rotate_speed","旋转速度","float",0.1,0.0,3.0,0.01,
                tooltip="控制旋转速度。值越大自转或公转越快"),
            ParamSpec("rotate_amp","旋转幅度(度)","float",45.0,0.0,180.0,1.0,
                tooltip="控制旋转摆动的角度范围。值越大旋转覆盖的角度越广"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _fan_rotate_offset)

    # ═══════════════════════════════════════════════════════════════════════
    #  C. XY 圆形/椭圆/8字/利萨如系列
    # ═══════════════════════════════════════════════════════════════════════

    # C1: 圆形追逐（每台设备画圆，相位依次递进）
    def _circle_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.5))
        radius_x = float(params.get("radius_x", 40.0))
        radius_y = float(params.get("radius_y", 40.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.1))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        drift_s  = float(params.get("drift_speed", 0.0))
        drift_amp= float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed + seq_i * phase_diff
            if en_x:
                vx = center_x + radius_x * math.cos(2 * math.pi * phase) * 255.0 / 360.0 + drift
                ef.values[orig_i]["motor_x"] = _clamp(vx)
            if en_y:
                vy = center_y + radius_y * math.sin(2 * math.pi * phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_y"] = _clamp(vy)
    reg(EffectPreset(
        id="motor_circle_chase_v35", name="圆形追逐", category="电机/圆形",
        description="每台设备画圆，相位依次递进形成追逐感。XY可独立开关，支持漂移。",
        target_channels=["motor_x","motor_y"], icon="○→",
        tags=["motor","circle","chase","xy","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("radius_x","X半径(度)","float",40.0,0.0,90.0,1.0,
                tooltip="控制 X 轴方向的椭圆半径。决定水平方向上的运动范围"),
            ParamSpec("radius_y","Y半径(度)","float",40.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的椭圆半径。决定垂直方向上的运动范围"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.1,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度","float",0.0,0.0,60.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _circle_chase)

    # C2: 椭圆追逐（X/Y半径不同）
    def _ellipse_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.5))
        radius_x = float(params.get("radius_x", 60.0))
        radius_y = float(params.get("radius_y", 20.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.1))
        tilt_angle= float(params.get("tilt_angle", 0.0))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed + seq_i * phase_diff
            cos_p = math.cos(2 * math.pi * phase)
            sin_p = math.sin(2 * math.pi * phase)
            tilt_r = math.radians(tilt_angle)
            # 旋转椭圆
            rx = radius_x * cos_p * math.cos(tilt_r) - radius_y * sin_p * math.sin(tilt_r)
            ry = radius_x * cos_p * math.sin(tilt_r) + radius_y * sin_p * math.cos(tilt_r)
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(center_x + rx * 255.0 / 360.0)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(center_y + ry * 255.0 / 360.0)
    reg(EffectPreset(
        id="motor_ellipse_chase_v35", name="椭圆追逐", category="电机/圆形",
        description="每台设备画椭圆，可旋转椭圆角度，相位依次递进。",
        target_channels=["motor_x","motor_y"], icon="◎→",
        tags=["motor","ellipse","chase","xy","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("radius_x","X半径(度)","float",60.0,0.0,90.0,1.0,
                tooltip="控制 X 轴方向的椭圆半径。决定水平方向上的运动范围"),
            ParamSpec("radius_y","Y半径(度)","float",20.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的椭圆半径。决定垂直方向上的运动范围"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.1,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("tilt_angle","椭圆倾斜角(度)","float",0.0,-90.0,90.0,1.0,
                tooltip="控制椭圆或轨道的倾斜角度。值越大倾斜越明显"),
        ],
    ), _ellipse_chase)

    # C3: 8字追逐（利萨如 2:1）
    def _figure8_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.5))
        amp_x    = float(params.get("amp_x", 50.0))
        amp_y    = float(params.get("amp_y", 25.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.1))
        freq_ratio= float(params.get("freq_ratio", 2.0))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed + seq_i * phase_diff
            if en_x:
                vx = center_x + amp_x * math.sin(2 * math.pi * phase * freq_ratio) * 255.0 / 360.0
                ef.values[orig_i]["motor_x"] = _clamp(vx)
            if en_y:
                vy = center_y + amp_y * math.sin(2 * math.pi * phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_y"] = _clamp(vy)
    reg(EffectPreset(
        id="motor_figure8_chase_v35", name="8字追逐", category="电机/圆形",
        description="每台设备画8字（利萨如图形），freq_ratio调整X/Y频率比。",
        target_channels=["motor_x","motor_y"], icon="∞→",
        tags=["motor","figure8","lissajous","xy","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",50.0,0.0,90.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",25.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.1,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("freq_ratio","X/Y频率比","float",2.0,0.5,6.0,0.5,
                tooltip="控制 X 轴与 Y 轴频率的比值。偏离 1.0 产生复杂的利萨如图案"),
        ],
    ), _figure8_chase)

    # C4: 利萨如图形（通用，可调X/Y频率和相位）
    def _lissajous_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.5))
        amp_x    = float(params.get("amp_x", 50.0))
        amp_y    = float(params.get("amp_y", 50.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        freq_x   = float(params.get("freq_x", 3.0))
        freq_y   = float(params.get("freq_y", 2.0))
        xy_phase = float(params.get("xy_phase", 0.25))
        phase_diff= float(params.get("phase_diff", 0.08))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed + seq_i * phase_diff
            if en_x:
                vx = center_x + amp_x * math.sin(2 * math.pi * freq_x * phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_x"] = _clamp(vx)
            if en_y:
                vy = center_y + amp_y * math.sin(2 * math.pi * freq_y * phase + 2 * math.pi * xy_phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_y"] = _clamp(vy)
    reg(EffectPreset(
        id="motor_lissajous_chase_v35", name="利萨如追逐", category="电机/圆形",
        description="每台设备画利萨如图形，X/Y频率和相位可调，设备间相位差产生追逐感。",
        target_channels=["motor_x","motor_y"], icon="∿∿→",
        tags=["motor","lissajous","xy","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",50.0,0.0,90.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",50.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("freq_x","X频率","float",3.0,0.5,8.0,0.5,
                tooltip="控制 X 轴方向的波动频率。值越高水平方向波动越密集"),
            ParamSpec("freq_y","Y频率","float",2.0,0.5,8.0,0.5,
                tooltip="控制 Y 轴方向的波动频率。值越高垂直方向波动越密集"),
            ParamSpec("xy_phase","XY相位差","float",0.25,0.0,1.0,0.01,
                tooltip="控制 X 与 Y 轴运动的相位差。值越大两轴运动的不同步程度越高"),
            ParamSpec("phase_diff","设备间相位差","float",0.08,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _lissajous_chase)

    # C5: 螺旋扩散（半径随设备序号递增）
    def _spiral_spread(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.5))
        max_r    = float(params.get("max_radius", 60.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            r = max_r * ratio
            phase = t * speed + seq_i * phase_diff
            if en_x:
                vx = center_x + r * math.cos(2 * math.pi * phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_x"] = _clamp(vx)
            if en_y:
                vy = center_y + r * math.sin(2 * math.pi * phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_y"] = _clamp(vy)
    reg(EffectPreset(
        id="motor_spiral_spread_v35", name="螺旋扩散", category="电机/圆形",
        description="半径随设备序号递增，形成螺旋扩散效果。",
        target_channels=["motor_x","motor_y"], icon="🌀↗",
        tags=["motor","spiral","spread","xy","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("max_radius","最大半径(度)","float",60.0,0.0,90.0,1.0,
                tooltip="控制运动的最大半径限制。限制运动范围的外边界"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _spiral_spread)

    # C6: 圆形同步（所有设备同相位画圆）
    def _circle_sync(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.5))
        radius_x = float(params.get("radius_x", 40.0))
        radius_y = float(params.get("radius_y", 40.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed
            if en_x:
                vx = center_x + radius_x * math.cos(2 * math.pi * phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_x"] = _clamp(vx)
            if en_y:
                vy = center_y + radius_y * math.sin(2 * math.pi * phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_y"] = _clamp(vy)
    reg(EffectPreset(
        id="motor_circle_sync_v35", name="圆形同步", category="电机/圆形",
        description="所有设备同步画圆，XY可独立开关。",
        target_channels=["motor_x","motor_y"], icon="○=",
        tags=["motor","circle","sync","xy","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("radius_x","X半径(度)","float",40.0,0.0,90.0,1.0,
                tooltip="控制 X 轴方向的椭圆半径。决定水平方向上的运动范围"),
            ParamSpec("radius_y","Y半径(度)","float",40.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的椭圆半径。决定垂直方向上的运动范围"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
        ],
    ), _circle_sync)

    # C7: 圆形反向（奇偶设备反向画圆）
    def _circle_reverse(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.5))
        radius_x = float(params.get("radius_x", 40.0))
        radius_y = float(params.get("radius_y", 40.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.0))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            direction = 1.0 if seq_i % 2 == 0 else -1.0
            phase = t * speed * direction + seq_i * phase_diff
            if en_x:
                vx = center_x + radius_x * math.cos(2 * math.pi * phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_x"] = _clamp(vx)
            if en_y:
                vy = center_y + radius_y * math.sin(2 * math.pi * phase) * 255.0 / 360.0
                ef.values[orig_i]["motor_y"] = _clamp(vy)
    reg(EffectPreset(
        id="motor_circle_reverse_v35", name="圆形反向", category="电机/圆形",
        description="奇偶设备反向画圆，形成交叉旋转效果。",
        target_channels=["motor_x","motor_y"], icon="○↩",
        tags=["motor","circle","reverse","xy","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("radius_x","X半径(度)","float",40.0,0.0,90.0,1.0,
                tooltip="控制 X 轴方向的椭圆半径。决定水平方向上的运动范围"),
            ParamSpec("radius_y","Y半径(度)","float",40.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的椭圆半径。决定垂直方向上的运动范围"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _circle_reverse)

    # ═══════════════════════════════════════════════════════════════════════
    #  D. XY 波浪/图案系列（含整体漂移）
    # ═══════════════════════════════════════════════════════════════════════

    # D1: XY 双轴波浪（X/Y独立波浪，相位差可调）
    def _xy_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed_x  = float(params.get("speed_x", 1.0))
        speed_y  = float(params.get("speed_y", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 40.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        wl_x     = float(params.get("wavelength_x", 4.0))
        wl_y     = float(params.get("wavelength_y", 4.0))
        wtype_x  = params.get("wave_x", "正弦")
        wtype_y  = params.get("wave_y", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        drift_s  = float(params.get("drift_speed", 0.0))
        drift_amp= float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            if en_x:
                phase_x = t * speed_x - ratio * wl_x
                raw_x = _wave(wtype_x, phase_x)
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x * 127.0 / 90.0 + drift)
            if en_y:
                phase_y = t * speed_y - ratio * wl_y
                raw_y = _wave(wtype_y, phase_y)
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_wave_v35", name="XY双轴波浪", category="电机/波浪",
        description="X/Y轴独立波浪，速度/幅度/波长/波形均可独立调节，支持整体漂移。",
        target_channels=["motor_x","motor_y"], icon="≈≈",
        tags=["motor","xy","wave","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed_x","X速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制 X 轴方向的运动速度。值越大水平方向运动越快"),
            ParamSpec("speed_y","Y速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制 Y 轴方向的运动速度。值越大垂直方向运动越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("wavelength_x","X波长","float",4.0,0.5,20.0,0.5,
                tooltip="控制 X 轴方向的空间波长。值越大水平波形拉伸越长"),
            ParamSpec("wavelength_y","Y波长","float",4.0,0.5,20.0,0.5,
                tooltip="控制 Y 轴方向的空间波长。值越大垂直波形拉伸越长"),
            ParamSpec("wave_x","X波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择 X 轴方向的波形。决定水平运动的规律"),
            ParamSpec("wave_y","Y波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择 Y 轴方向的波形。决定垂直运动的规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度","float",0.0,0.0,60.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _xy_wave)

    # D2: XY 对称波浪（X/Y波浪对称，中心向两侧扩散）
    def _xy_sym_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 40.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        drift_s  = float(params.get("drift_speed", 0.0))
        drift_amp= float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = (seq_i - mid) / max(1.0, mid)  # -1..+1
            phase = t * speed - abs(dist) * 2.0
            raw = _wave(wtype, phase)
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw * 127.0 / 90.0 * dist + drift)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_sym_wave_v35", name="XY对称波浪", category="电机/波浪",
        description="从中心向两侧对称扩散的波浪，XY可独立开关。",
        target_channels=["motor_x","motor_y"], icon="≈|≈",
        tags=["motor","xy","wave","symmetric","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度","float",0.0,0.0,60.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _xy_sym_wave)

    # D3: XY 龙摆（X轴龙摆，Y轴可选同步或独立）
    def _xy_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 0.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        xy_phase = float(params.get("xy_phase_diff", 0.25))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", False))
        drift_s  = float(params.get("drift_speed", 0.0))
        drift_amp= float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            if en_x:
                raw_x = _wave(wtype, phase)
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x * 127.0 / 90.0 + drift)
            if en_y:
                raw_y = _wave(wtype, phase + xy_phase)
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_dragon_v35", name="XY龙摆", category="电机/龙摆",
        description="XY双轴龙摆，X/Y可独立开关，XY相位差可调，支持整体漂移。",
        target_channels=["motor_x","motor_y"], icon="≈XY",
        tags=["motor","xy","dragon","wave","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("xy_phase_diff","XY相位差","float",0.25,0.0,1.0,0.01,
                tooltip="控制 X 轴与 Y 轴运动的相位差值。决定了椭圆或利萨如图形的形状"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度","float",0.0,0.0,60.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _xy_dragon)

    # D4: XY 交叉龙摆（X轴龙摆，Y轴反向龙摆）
    def _xy_cross_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 40.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        xy_phase = float(params.get("xy_phase_diff", 0.5))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            if en_x:
                raw_x = _wave(wtype, phase)
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x * 127.0 / 90.0)
            if en_y:
                raw_y = _wave(wtype, phase + xy_phase)
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_cross_dragon_v35", name="XY交叉龙摆", category="电机/龙摆",
        description="X轴龙摆，Y轴反向龙摆，XY相位差=0.5时完全反向。",
        target_channels=["motor_x","motor_y"], icon="≈X≈",
        tags=["motor","xy","cross","dragon","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("xy_phase_diff","XY相位差","float",0.5,0.0,1.0,0.01,
                tooltip="控制 X 轴与 Y 轴运动的相位差值。决定了椭圆或利萨如图形的形状"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _xy_cross_dragon)

    # D5: XY 波浪漂移（主体整体左右漂移，波浪叠加）
    def _xy_wave_drift(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 50.0))
        amp_y    = float(params.get("amp_y", 30.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        drift_speed= float(params.get("drift_speed", 0.15))
        drift_amp = float(params.get("drift_amp", 40.0))
        drift_dir = float(params.get("drift_dir", 1.0))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        # 整体漂移：主体中心随时间左右移动
        drift = _sine_raw(t * drift_speed * drift_dir) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            if en_x:
                raw_x = _wave(wtype, phase)
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x * 127.0 / 90.0 + drift)
            if en_y:
                raw_y = _wave(wtype, phase + 0.25)
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_wave_drift_v35", name="XY波浪漂移", category="电机/波浪",
        description="XY波浪同时整体左右漂移，漂移速度/幅度/方向可调。",
        target_channels=["motor_x","motor_y"], icon="≈→≈",
        tags=["motor","xy","wave","drift","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","波浪速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",50.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.15,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
            ParamSpec("drift_dir","漂移方向","float",1.0,-1.0,1.0,0.1,
                tooltip="控制漂移运动的方向。决定漂移在哪个空间方向上进行"),
        ],
    ), _xy_wave_drift)

    # D6: XY 镜像波浪（左半和右半镜像）
    def _xy_mirror_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 0.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", False))
        drift_s  = float(params.get("drift_speed", 0.0))
        drift_amp= float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        mid = n // 2
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < mid:
                phase = t * speed - seq_i * phase_diff
            else:
                mirror_i = n - 1 - seq_i
                phase = t * speed - mirror_i * phase_diff
            if en_x:
                raw_x = _wave(wtype, phase)
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x * 127.0 / 90.0 + drift)
            if en_y:
                raw_y = _wave(wtype, phase + 0.25)
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_mirror_wave_v35", name="XY镜像波浪", category="电机/波浪",
        description="左半和右半镜像波浪，从两端向中心或从中心向两端传播。",
        target_channels=["motor_x","motor_y"], icon="≈|≈",
        tags=["motor","xy","mirror","wave","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _xy_mirror_wave)

    # D7: XY 双频叠加波浪
    def _xy_dual_freq_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed1   = float(params.get("speed1", 1.0))
        speed2   = float(params.get("speed2", 2.3))
        amp1     = float(params.get("amp1", 50.0))
        amp2     = float(params.get("amp2", 20.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.12))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", False))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            p1 = t * speed1 - seq_i * phase_diff
            p2 = t * speed2 - seq_i * phase_diff * 1.5
            raw = _sine_raw(p1) * amp1 + _sine_raw(p2) * amp2
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(center_x + raw * 127.0 / 90.0)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(center_y + raw * 0.5 * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_dual_freq_wave_v35", name="XY双频叠加波浪", category="电机/波浪",
        description="两个频率叠加的波浪，产生复杂水纹效果。",
        target_channels=["motor_x","motor_y"], icon="≈≈≈",
        tags=["motor","xy","wave","dual_freq","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed1","主频速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制第一层（低频）效果的速度。值越小越缓慢，值越大越急促"),
            ParamSpec("speed2","副频速度","float",2.3,0.0,10.0,0.1,
                tooltip="控制第二层（高频）效果的速度。值越小越缓慢，值越大越急促"),
            ParamSpec("amp1","主幅度(度)","float",50.0,0.0,180.0,1.0,
                tooltip="控制第一层效果的幅度大小。值越大该层影响越明显"),
            ParamSpec("amp2","副幅度(度)","float",20.0,0.0,90.0,1.0,
                tooltip="控制第二层效果的幅度大小。值越大该层影响越明显"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.12,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _xy_dual_freq_wave)

    # D8: XY 交叉波浪（X正向，Y反向）
    def _xy_cross_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 40.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            if en_x:
                raw_x = _wave(wtype, phase)
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x * 127.0 / 90.0)
            if en_y:
                raw_y = _wave(wtype, -phase)  # 反向
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_cross_wave_v35", name="XY交叉波浪", category="电机/波浪",
        description="X轴正向波浪，Y轴反向波浪，形成交叉水纹。",
        target_channels=["motor_x","motor_y"], icon="≈X≈",
        tags=["motor","xy","cross","wave","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _xy_cross_wave)

    # ═══════════════════════════════════════════════════════════════════════
    #  E. XY 追逐/龙摆变式
    # ═══════════════════════════════════════════════════════════════════════

    # E1: XY 双向追逐（X正向追逐，Y反向追逐）
    def _xy_dual_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.5))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 40.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        width    = float(params.get("width", 0.3))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ratio = seq_i / max(1, n - 1)
            # X轴：正向追逐
            pos_x = (t * speed) % 1.0
            dist_x = abs(ratio - pos_x)
            dist_x = min(dist_x, 1.0 - dist_x)
            raw_x = max(0.0, 1.0 - dist_x / max(0.01, width))
            # Y轴：反向追逐
            pos_y = 1.0 - (t * speed) % 1.0
            dist_y = abs(ratio - pos_y)
            dist_y = min(dist_y, 1.0 - dist_y)
            raw_y = max(0.0, 1.0 - dist_y / max(0.01, width))
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(center_x + (raw_x * 2.0 - 1.0) * amp_x * 127.0 / 90.0)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(center_y + (raw_y * 2.0 - 1.0) * amp_y * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_dual_chase_v35", name="XY双向追逐", category="电机/追逐",
        description="X轴正向追逐，Y轴反向追逐，形成交叉追逐效果。",
        target_channels=["motor_x","motor_y"], icon="→←XY",
        tags=["motor","xy","chase","dual","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.5,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("width","追逐宽度","float",0.3,0.01,1.0,0.01,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
        ],
    ), _xy_dual_chase)

    # E2: XY 中心扩散追逐
    def _xy_center_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 0.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", False))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1.0, mid)
            phase = t * speed - dist * phase_diff * n * 0.5
            if en_x:
                raw_x = _wave(wtype, phase)
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x * 127.0 / 90.0)
            if en_y:
                raw_y = _wave(wtype, phase + 0.25)
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * raw_y * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_center_chase_v35", name="XY中心扩散追逐", category="电机/追逐",
        description="从中心向两侧扩散的追逐效果，XY可独立开关。",
        target_channels=["motor_x","motor_y"], icon="|→→XY",
        tags=["motor","xy","center","chase","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",False,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _xy_center_chase)

    # E3: XY 螺旋龙摆（X龙摆+Y缓慢旋转）
    def _xy_spiral_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed_x  = float(params.get("speed_x", 1.0))
        speed_y  = float(params.get("speed_y", 0.2))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 30.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.15))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase_x = t * speed_x - seq_i * phase_diff
            phase_y = t * speed_y + seq_i * phase_diff * 0.5
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * _sine_raw(phase_x) * 127.0 / 90.0)
            if en_y:
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * _sine_raw(phase_y) * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_spiral_dragon_v35", name="XY螺旋龙摆", category="电机/龙摆",
        description="X轴快速龙摆，Y轴缓慢旋转，形成螺旋龙摆效果。",
        target_channels=["motor_x","motor_y"], icon="🌀≈",
        tags=["motor","xy","spiral","dragon","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed_x","X速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制 X 轴方向的运动速度。值越大水平方向运动越快"),
            ParamSpec("speed_y","Y速度","float",0.2,0.0,5.0,0.05,
                tooltip="控制 Y 轴方向的运动速度。值越大垂直方向运动越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.15,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _xy_spiral_dragon)

    # ═══════════════════════════════════════════════════════════════════════
    #  F. XY 开合/镜像系列
    # ═══════════════════════════════════════════════════════════════════════

    # F1: XY 同步开合（X轴展开，Y轴同步抬起）
    def _xy_sync_open(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.4))
        amp_x    = float(params.get("amp_x", 80.0))
        amp_y    = float(params.get("amp_y", 30.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        xy_phase = float(params.get("xy_phase_diff", 0.0))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        drift_s  = float(params.get("drift_speed", 0.0))
        drift_amp= float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = (seq_i - mid) / max(1.0, mid)  # -1..+1
            open_x = _wave(wtype, t * speed)
            open_y = _wave(wtype, t * speed + xy_phase)
            if en_x:
                angle_x = dist * amp_x * 0.5 * open_x + drift
                ef.values[orig_i]["motor_x"] = _deg2dmx(angle_x)
            if en_y:
                angle_y = abs(dist) * amp_y * 0.5 * open_y
                ef.values[orig_i]["motor_y"] = _deg2dmx(angle_y)
    reg(EffectPreset(
        id="motor_xy_sync_open_v35", name="XY同步开合", category="电机/开合",
        description="X轴展开，Y轴同步抬起，XY相位差可调，支持整体漂移。",
        target_channels=["motor_x","motor_y"], icon="XY<=>",
        tags=["motor","xy","open","close","sync","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.4,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("xy_phase_diff","XY相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制 X 轴与 Y 轴运动的相位差值。决定了椭圆或利萨如图形的形状"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _xy_sync_open)

    # F2: XY 交替开合（X开时Y合）
    def _xy_alt_open(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.4))
        amp_x    = float(params.get("amp_x", 80.0))
        amp_y    = float(params.get("amp_y", 60.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = (seq_i - mid) / max(1.0, mid)
            open_x = _wave(wtype, t * speed)
            open_y = _wave(wtype, t * speed + 0.5)  # 反相
            if en_x:
                ef.values[orig_i]["motor_x"] = _deg2dmx(dist * amp_x * 0.5 * open_x)
            if en_y:
                ef.values[orig_i]["motor_y"] = _deg2dmx(dist * amp_y * 0.5 * open_y)
    reg(EffectPreset(
        id="motor_xy_alt_open_v35", name="XY交替开合", category="电机/开合",
        description="X轴开时Y轴合，形成立体交替开合效果。",
        target_channels=["motor_x","motor_y"], icon="XY↔",
        tags=["motor","xy","open","close","alternate","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.4,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",80.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _xy_alt_open)

    # F3: XY 孔雀开屏（X轴展开，Y轴随序号抬起，形成立体扇形）
    def _xy_peacock(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.3))
        amp_x    = float(params.get("amp_x", 90.0))
        amp_y    = float(params.get("amp_y", 45.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        drift_s  = float(params.get("drift_speed", 0.0))
        drift_amp= float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = (seq_i - mid) / max(1.0, mid)
            open_amt = (_wave(wtype, t * speed) + 1.0) * 0.5  # 0..1
            if en_x:
                angle_x = dist * amp_x * 0.5 * open_amt + drift
                ef.values[orig_i]["motor_x"] = _deg2dmx(angle_x)
            if en_y:
                # Y轴：越靠外越高（孔雀尾巴弧度）
                angle_y = abs(dist) * amp_y * 0.5 * open_amt
                ef.values[orig_i]["motor_y"] = _deg2dmx(angle_y)
    reg(EffectPreset(
        id="motor_xy_peacock_v35", name="XY孔雀开屏", category="电机/开合",
        description="X轴展开，Y轴随序号抬起形成立体扇形，像孔雀开屏。",
        target_channels=["motor_x","motor_y"], icon="XY🦚",
        tags=["motor","xy","peacock","open","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.3,0.0,5.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",90.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",45.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _xy_peacock)

    # F4: XY 蝴蝶展翅（X轴对称摆，Y轴中心高两端低）
    def _xy_butterfly(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.5))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 30.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.1))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = (seq_i - mid) / max(1.0, mid)
            phase = t * speed - abs(dist) * phase_diff * n * 0.5
            wing = _sine_raw(phase)
            if en_x:
                ef.values[orig_i]["motor_x"] = _clamp(center_x + dist * amp_x * wing * 127.0 / 90.0)
            if en_y:
                # 中心高两端低
                ef.values[orig_i]["motor_y"] = _clamp(center_y + (1.0 - abs(dist)) * amp_y * wing * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_butterfly_v35", name="XY蝴蝶展翅", category="电机/造型",
        description="X轴对称摆动，Y轴中心高两端低，形成蝴蝶展翅效果。",
        target_channels=["motor_x","motor_y"], icon="XY🦋",
        tags=["motor","xy","butterfly","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","相位差","float",0.1,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _xy_butterfly)

    # F5: XY 海浪（X轴波浪，Y轴随波浪起伏）
    def _xy_ocean_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 0.8))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 20.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        phase_diff= float(params.get("phase_diff", 0.2))
        xy_phase = float(params.get("xy_phase_diff", 0.1))
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        drift_s  = float(params.get("drift_speed", 0.0))
        drift_amp= float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_s) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * phase_diff
            if en_x:
                raw_x = _sine_raw(phase)
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x * 127.0 / 90.0 + drift)
            if en_y:
                raw_y = _sine_raw(phase + xy_phase)
                ef.values[orig_i]["motor_y"] = _clamp(center_y + amp_y * abs(raw_y) * 127.0 / 90.0)
    reg(EffectPreset(
        id="motor_xy_ocean_wave_v35", name="XY海浪", category="电机/造型",
        description="X轴波浪，Y轴随波浪起伏，模拟海浪效果。",
        target_channels=["motor_x","motor_y"], icon="XY🌊",
        tags=["motor","xy","ocean","wave","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",0.8,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",20.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("phase_diff","设备间相位差","float",0.2,0.0,1.0,0.01,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("xy_phase_diff","XY相位差","float",0.1,0.0,1.0,0.01,
                tooltip="控制 X 轴与 Y 轴运动的相位差值。决定了椭圆或利萨如图形的形状"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _xy_ocean_wave)

    # ═══════════════════════════════════════════════════════════════════════
    #  G. 更多交叉变式（带漂移/反转/追逐）
    # ═══════════════════════════════════════════════════════════════════════

    # G1: 22交叉漂移（22交叉同时整体漂移）
    def _cross22_drift(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp      = float(params.get("amplitude", 60.0))
        center   = float(params.get("center", 128.0))
        gphase   = float(params.get("group_phase_diff", 0.0))
        drift_speed= float(params.get("drift_speed", 0.2))
        drift_amp= float(params.get("drift_amp", 40.0))
        drift_dir= float(params.get("drift_dir", 1.0))
        wtype    = params.get("wave", "正弦")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_speed * drift_dir) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            ef.values[orig_i][ch] = _clamp(center + amp * raw + drift)
    reg(EffectPreset(
        id="motor_cross22_drift_v35", name="22交叉漂移", category="电机/交叉",
        description="22交叉同时整体左右漂移，漂移速度/幅度/方向可调。",
        target_channels=["motor_x","motor_y"], icon="⇄2→",
        tags=["motor","cross","22","drift","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
            ParamSpec("drift_speed","漂移速度","float",0.2,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
            ParamSpec("drift_dir","漂移方向","float",1.0,-1.0,1.0,0.1,
                tooltip="控制漂移运动的方向。决定漂移在哪个空间方向上进行"),
        ],
    ), _cross22_drift)

    # G2: 44交叉漂移
    def _cross44_drift(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp      = float(params.get("amplitude", 60.0))
        center   = float(params.get("center", 128.0))
        gphase   = float(params.get("group_phase_diff", 0.0))
        drift_speed= float(params.get("drift_speed", 0.2))
        drift_amp= float(params.get("drift_amp", 40.0))
        drift_dir= float(params.get("drift_dir", 1.0))
        wtype    = params.get("wave", "正弦")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        drift = _sine_raw(t * drift_speed * drift_dir) * drift_amp
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 4
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            raw = _wave(wtype, phase) * direction
            ef.values[orig_i][ch] = _clamp(center + amp * raw + drift)
    reg(EffectPreset(
        id="motor_cross44_drift_v35", name="44交叉漂移", category="电机/交叉",
        description="44交叉同时整体左右漂移。",
        target_channels=["motor_x","motor_y"], icon="⇄4→",
        tags=["motor","cross","44","drift","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude","幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
            ParamSpec("drift_speed","漂移速度","float",0.2,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
            ParamSpec("drift_dir","漂移方向","float",1.0,-1.0,1.0,0.1,
                tooltip="控制漂移运动的方向。决定漂移在哪个空间方向上进行"),
        ],
    ), _cross44_drift)

    # G3: 22交叉XY开合（X轴22交叉，Y轴同步开合）
    def _cross22_xy_open(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        amp_x    = float(params.get("amp_x", 60.0))
        amp_y    = float(params.get("amp_y", 30.0))
        center_x = float(params.get("center_x", 128.0))
        center_y = float(params.get("center_y", 128.0))
        gphase   = float(params.get("group_phase_diff", 0.0))
        xy_phase = float(params.get("xy_phase_diff", 0.25))
        wtype    = params.get("wave", "正弦")
        en_x     = bool(params.get("enable_x", True))
        en_y     = bool(params.get("enable_y", True))
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            if en_x:
                raw_x = _wave(wtype, phase) * direction
                ef.values[orig_i]["motor_x"] = _clamp(center_x + amp_x * raw_x * 127.0 / 90.0)
            if en_y:
                dist = (seq_i - mid) / max(1.0, mid)
                open_y = _wave(wtype, t * speed + xy_phase)
                ef.values[orig_i]["motor_y"] = _deg2dmx(dist * amp_y * 0.5 * open_y)
    reg(EffectPreset(
        id="motor_cross22_xy_open_v35", name="22交叉XY开合", category="电机/交叉",
        description="X轴22交叉，Y轴同步开合，XY相位差可调。",
        target_channels=["motor_x","motor_y"], icon="⇄2XY<>",
        tags=["motor","cross","22","xy","open","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amp_x","X幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的幅度大小。值越大水平摆动范围越广"),
            ParamSpec("amp_y","Y幅度(度)","float",30.0,0.0,90.0,1.0,
                tooltip="控制 Y 轴方向的幅度大小。值越大垂直摆动范围越广"),
            ParamSpec("center_x","X中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 X 坐标。决定图案的水平基准位置"),
            ParamSpec("center_y","Y中心","float",128.0,0.0,255.0,1.0,
                tooltip="控制运动轨迹中心点的 Y 坐标。决定图案的垂直基准位置"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
            ParamSpec("xy_phase_diff","XY相位差","float",0.25,0.0,1.0,0.01,
                tooltip="控制 X 轴与 Y 轴运动的相位差值。决定了椭圆或利萨如图形的形状"),
        ],
    ), _cross22_xy_open)

    # G4: 扇形22交叉（扇形内部22交叉）
    def _fan_cross22(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 2)))
        speed       = float(params.get("speed", 1.0))
        fan_angle   = float(params.get("fan_angle", 60.0))
        cross_amp   = float(params.get("cross_amp", 30.0))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        cross_phase = float(params.get("cross_phase_diff", 0.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 扇形基础角度
            base_angle = local_ratio * fan_angle * 0.5
            # 扇内22交叉
            pair_idx = local_i // 2
            direction = 1.0 if pair_idx % 2 == 0 else -1.0
            cross_t = t * speed + fan_idx * fan_phase + pair_idx * cross_phase
            cross = _wave(wtype, cross_t) * direction * cross_amp
            ef.values[orig_i][ch] = _deg2dmx(base_angle + cross)
    reg(EffectPreset(
        id="motor_fan_cross22_v35", name="扇形内22交叉", category="电机/扇形",
        description="扇形展开的同时，扇内每2台进行22交叉摆动。",
        target_channels=["motor_x","motor_y"], icon="⊃⇄2⊂",
        tags=["motor","fan","cross","22","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",2,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cross_amp","交叉幅度(度)","float",30.0,0.0,90.0,1.0,
                tooltip="控制交叉混合区域的幅度。决定交叉部分的过渡强度"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("cross_phase_diff","交叉相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制交叉两部分的相位差。值越大两部分的异步程度越高"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _fan_cross22)

    # G5: 扇形44交叉（扇形内部44交叉）
    def _fan_cross44(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 2)))
        speed       = float(params.get("speed", 1.0))
        fan_angle   = float(params.get("fan_angle", 60.0))
        cross_amp   = float(params.get("cross_amp", 20.0))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            base_angle = local_ratio * fan_angle * 0.5
            group_idx = local_i // 4
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            cross_t = t * speed + fan_idx * fan_phase
            cross = _wave(wtype, cross_t) * direction * cross_amp
            ef.values[orig_i][ch] = _deg2dmx(base_angle + cross)
    reg(EffectPreset(
        id="motor_fan_cross44_v35", name="扇形内44交叉", category="电机/扇形",
        description="扇形展开的同时，扇内每4台进行44交叉摆动。",
        target_channels=["motor_x","motor_y"], icon="⊃⇄4⊂",
        tags=["motor","fan","cross","44","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",2,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("speed","速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cross_amp","交叉幅度(度)","float",20.0,0.0,90.0,1.0,
                tooltip="控制交叉混合区域的幅度。决定交叉部分的过渡强度"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _fan_cross44)

    # G6: 扇形整体漂移+交替开合（最终综合动作）
    def _fan_drift_alt(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(2, int(params.get("fans", 3)))
        open_speed  = float(params.get("open_speed", 0.5))
        fan_angle   = float(params.get("fan_angle", 70.0))
        drift_speed = float(params.get("drift_speed", 0.15))
        drift_amp   = float(params.get("drift_amp", 50.0))
        drift_dir   = float(params.get("drift_dir", 1.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        drift = _sine_raw(t * drift_speed * drift_dir) * drift_amp
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 奇偶扇子交替
            fan_phase = 0.5 if fan_idx % 2 == 1 else 0.0
            open_amt = _wave(wtype, t * open_speed + fan_phase)
            angle = local_ratio * fan_angle * 0.5 * open_amt + drift
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_drift_alt_v35", name="扇形漂移交替", category="电机/扇形",
        description="多扇交替开合，同时整体左右漂移，最终综合扇形效果。",
        target_channels=["motor_x","motor_y"], icon="⊃↔→⊂",
        tags=["motor","fan","drift","alternate","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",3,2,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",70.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("open_speed","开合速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制展开速度。值越大从闭合到完全展开越快"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.15,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",50.0,0.0,180.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
            ParamSpec("drift_dir","漂移方向","float",1.0,-1.0,1.0,0.1,
                tooltip="控制漂移运动的方向。决定漂移在哪个空间方向上进行"),
        ],
    ), _fan_drift_alt)

    # G7: 22交叉+扇形（22交叉同时整体扇形展开）
    def _cross22_fan(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        cross_amp= float(params.get("cross_amp", 40.0))
        fan_angle= float(params.get("fan_angle", 60.0))
        open_speed= float(params.get("open_speed", 0.3))
        center   = float(params.get("center", 128.0))
        gphase   = float(params.get("group_phase_diff", 0.0))
        wtype    = params.get("wave", "正弦")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 2
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            cross = _wave(wtype, phase) * direction * cross_amp
            # 扇形基础角度
            dist = (seq_i - mid) / max(1.0, mid)
            open_amt = (_wave(wtype, t * open_speed) + 1.0) * 0.5
            fan = dist * fan_angle * 0.5 * open_amt
            ef.values[orig_i][ch] = _deg2dmx(cross + fan)
    reg(EffectPreset(
        id="motor_cross22_fan_v35", name="22交叉+扇形", category="电机/交叉",
        description="22交叉摆动叠加整体扇形展开，双重效果叠加。",
        target_channels=["motor_x","motor_y"], icon="⇄2⊃⊂",
        tags=["motor","cross","22","fan","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","交叉速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cross_amp","交叉幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制交叉混合区域的幅度。决定交叉部分的过渡强度"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("open_speed","开合速度","float",0.3,0.0,5.0,0.05,
                tooltip="控制展开速度。值越大从闭合到完全展开越快"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
        ],
    ), _cross22_fan)

    # G8: 44交叉+扇形
    def _cross44_fan(ef, t, sorted_fx, fdefs, params, fps):
        speed    = float(params.get("speed", 1.0))
        cross_amp= float(params.get("cross_amp", 40.0))
        fan_angle= float(params.get("fan_angle", 60.0))
        open_speed= float(params.get("open_speed", 0.3))
        center   = float(params.get("center", 128.0))
        gphase   = float(params.get("group_phase_diff", 0.0))
        wtype    = params.get("wave", "正弦")
        ch       = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 4
            direction = 1.0 if group_idx % 2 == 0 else -1.0
            phase = t * speed + group_idx * gphase
            cross = _wave(wtype, phase) * direction * cross_amp
            dist = (seq_i - mid) / max(1.0, mid)
            open_amt = (_wave(wtype, t * open_speed) + 1.0) * 0.5
            fan = dist * fan_angle * 0.5 * open_amt
            ef.values[orig_i][ch] = _deg2dmx(cross + fan)
    reg(EffectPreset(
        id="motor_cross44_fan_v35", name="44交叉+扇形", category="电机/交叉",
        description="44交叉摆动叠加整体扇形展开。",
        target_channels=["motor_x","motor_y"], icon="⇄4⊃⊂",
        tags=["motor","cross","44","fan","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("speed","交叉速度","float",1.0,0.0,10.0,0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cross_amp","交叉幅度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制交叉混合区域的幅度。决定交叉部分的过渡强度"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("open_speed","开合速度","float",0.3,0.0,5.0,0.05,
                tooltip="控制展开速度。值越大从闭合到完全展开越快"),
            ParamSpec("center","中心值","float",128.0,0.0,255.0,1.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("group_phase_diff","组间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制组与组之间的起始相位差。值越大各组之间的时间错位越明显"),
        ],
    ), _cross44_fan)

    # G9: 扇形中心漂移（扇形中心随时间一拍一拍地移动）
    def _fan_center_drift(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 2)))
        fan_angle   = float(params.get("fan_angle", 60.0))
        open_speed  = float(params.get("open_speed", 0.5))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        drift_speed = float(params.get("drift_speed", 0.3))
        drift_amp   = float(params.get("drift_amp", 60.0))
        drift_steps = max(1, int(params.get("drift_steps", 4)))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        # 阶梯式漂移（一拍一拍）
        drift_phase = (t * drift_speed) % 1.0
        step = int(drift_phase * drift_steps) / max(1, drift_steps - 1)
        drift = (step * 2.0 - 1.0) * drift_amp
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            open_amt = _wave(wtype, t * open_speed + fan_idx * fan_phase)
            angle = local_ratio * fan_angle * 0.5 * open_amt + drift
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_center_drift_v35", name="扇形阶梯漂移", category="电机/扇形",
        description="扇形中心一拍一拍地向左/右移动，drift_steps控制步数。",
        target_channels=["motor_x","motor_y"], icon="⊃→|→⊂",
        tags=["motor","fan","drift","step","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",2,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("open_speed","开合速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制展开速度。值越大从闭合到完全展开越快"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.3,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
            ParamSpec("drift_steps","漂移步数","int",4,2,16,1,
                tooltip="控制漂移过程的离散步数。值越大漂移路径越平滑"),
        ],
    ), _fan_center_drift)

    # G10: 扇形渐变开合（每把扇子开合幅度渐变）
    def _fan_gradient_open(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(2, int(params.get("fans", 4)))
        speed       = float(params.get("speed", 0.5))
        fan_angle   = float(params.get("fan_angle", 60.0))
        min_amp     = float(params.get("min_amp", 0.1))
        max_amp     = float(params.get("max_amp", 1.0))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 每把扇子的幅度渐变
            amp_ratio = min_amp + (max_amp - min_amp) * fan_idx / max(1, fans - 1)
            open_amt = _wave(wtype, t * speed + fan_idx * fan_phase)
            angle = local_ratio * fan_angle * 0.5 * open_amt * amp_ratio
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_gradient_open_v35", name="扇形渐变开合", category="电机/扇形",
        description="每把扇子开合幅度渐变，从小到大或从大到小。",
        target_channels=["motor_x","motor_y"], icon="⊃↗⊂",
        tags=["motor","fan","gradient","open","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",4,2,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("min_amp","最小幅度比","float",0.1,0.0,1.0,0.05,
                tooltip="控制幅度的最小值下限。确保效果的幅度不低于此值"),
            ParamSpec("max_amp","最大幅度比","float",1.0,0.0,1.0,0.05,
                tooltip="控制幅度的最大值上限。限制效果的幅度不超过此值"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _fan_gradient_open)

    # G11: 扇形反转（奇数扇正开，偶数扇反开）
    def _fan_reverse_open(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(2, int(params.get("fans", 4)))
        speed       = float(params.get("speed", 0.5))
        fan_angle   = float(params.get("fan_angle", 60.0))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        wtype       = params.get("wave", "正弦")
        ch          = params.get("ch", "motor_x")
        n = len(sorted_fx)
        if n == 0:
            return
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            # 奇数扇反转
            if fan_idx % 2 == 1:
                local_ratio = -local_ratio
            open_amt = _wave(wtype, t * speed + fan_idx * fan_phase)
            angle = local_ratio * fan_angle * 0.5 * open_amt
            ef.values[orig_i][ch] = _deg2dmx(angle)
    reg(EffectPreset(
        id="motor_fan_reverse_open_v35", name="扇形反转开合", category="电机/扇形",
        description="奇数扇正开，偶数扇反开，形成镜像扇形效果。",
        target_channels=["motor_x","motor_y"], icon="⊃↩⊂",
        tags=["motor","fan","reverse","open","v35"],
        params=[
            ParamSpec("ch","通道","choice","motor_x",choices=MCH,
                tooltip="选择效果作用的通道索引或类型。决定输出数据写入哪个通道"),
            ParamSpec("fans","扇子数量","int",4,2,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("fan_angle","扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制扇形展开的总角度。值越大扇面覆盖越广"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
        ],
    ), _fan_reverse_open)

    # G12: 扇形双轴开合（X轴展开，Y轴同时展开，形成立体扇形）
    def _fan_dual_axis_open(ef, t, sorted_fx, fdefs, params, fps):
        fans        = max(1, int(params.get("fans", 2)))
        speed       = float(params.get("speed", 0.5))
        fan_angle_x = float(params.get("fan_angle_x", 60.0))
        fan_angle_y = float(params.get("fan_angle_y", 40.0))
        fan_phase   = float(params.get("fan_phase_diff", 0.0))
        xy_phase    = float(params.get("xy_phase_diff", 0.0))
        wtype       = params.get("wave", "正弦")
        en_x        = bool(params.get("enable_x", True))
        en_y        = bool(params.get("enable_y", True))
        drift_s     = float(params.get("drift_speed", 0.0))
        drift_amp   = float(params.get("drift_amp", 0.0))
        n = len(sorted_fx)
        if n == 0:
            return
        drift = _sine_raw(t * drift_s) * drift_amp
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_n = min(fan_size, n - fan_idx * fan_size)
            local_ratio = (local_i / max(1, local_n - 1)) * 2.0 - 1.0
            open_x = _wave(wtype, t * speed + fan_idx * fan_phase)
            open_y = _wave(wtype, t * speed + fan_idx * fan_phase + xy_phase)
            if en_x:
                angle_x = local_ratio * fan_angle_x * 0.5 * open_x + drift
                ef.values[orig_i]["motor_x"] = _deg2dmx(angle_x)
            if en_y:
                angle_y = local_ratio * fan_angle_y * 0.5 * open_y
                ef.values[orig_i]["motor_y"] = _deg2dmx(angle_y)
    reg(EffectPreset(
        id="motor_fan_dual_axis_open_v35", name="扇形双轴开合", category="电机/扇形",
        description="X/Y轴同时展开形成立体扇形，XY相位差可调，支持整体漂移。",
        target_channels=["motor_x","motor_y"], icon="⊃XY⊂3D",
        tags=["motor","fan","dual_axis","open","v35"],
        params=[
            ParamSpec("enable_x","启用X轴","bool",True,
                tooltip="切换 X 轴运动的开关状态。关闭后 X 轴锁定在当前值"),
            ParamSpec("enable_y","启用Y轴","bool",True,
                tooltip="切换 Y 轴运动的开关状态。关闭后 Y 轴锁定在当前值"),
            ParamSpec("fans","扇子数量","int",2,1,8,1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("speed","速度","float",0.5,0.0,10.0,0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fan_angle_x","X扇形角度(度)","float",60.0,0.0,180.0,1.0,
                tooltip="控制 X 轴方向的扇形展开角度。值越大水平扇面越宽"),
            ParamSpec("fan_angle_y","Y扇形角度(度)","float",40.0,0.0,180.0,1.0,
                tooltip="控制 Y 轴方向的扇形展开角度。值越大垂直扇面越宽"),
            ParamSpec("fan_phase_diff","扇间相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制扇叶之间的起始相位差。值越大各扇叶依序错开越明显"),
            ParamSpec("xy_phase_diff","XY相位差","float",0.0,0.0,1.0,0.01,
                tooltip="控制 X 轴与 Y 轴运动的相位差值。决定了椭圆或利萨如图形的形状"),
            ParamSpec("wave","波形","choice","正弦",choices=WAVE_CHOICES,
                tooltip="选择波形类型。不同波形产生不同的运动规律"),
            ParamSpec("drift_speed","漂移速度","float",0.0,0.0,5.0,0.05,
                tooltip="控制漂移运动的速度。值越大漂移在空间中移动越快"),
            ParamSpec("drift_amp","漂移幅度(度)","float",0.0,0.0,90.0,1.0,
                tooltip="控制漂移运动的幅度。值越大漂移往返的范围越广"),
        ],
    ), _fan_dual_axis_open)


# ═══════════════════════════════════════════════════════════════════════════
#  公开注册入口
# ═══════════════════════════════════════════════════════════════════════════
def register_v35(engine):
    """注册所有 v35 XY双轴联动电机动作"""
    _register_v35_presets(engine)
