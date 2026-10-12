"""
effect_presets_v26.py — 效果库大幅扩充 v26
=============================================
重点扩充：
  A. 电机交叉变体（两两交叉、四四交叉、三三交叉、N组交叉、渐进交叉等）
  B. 电机龙摆变体（单龙摆、双龙摆、三龙摆、反向龙摆、渐变龙摆、龙摆+旋转等）
  C. 电机其他变体（已有效果的新变式）
  D. 灯光矩阵效果（矩阵图案扫描、矩阵波浪、矩阵呼吸、矩阵文字等）
  E. 灯光亮度类扩充
  F. 灯光颜色类扩充
  G. 泵矩阵效果（矩阵水型图案、矩阵对称喷射、矩阵波浪等）
  H. 泵其他变体
"""
from __future__ import annotations
import math
import random
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .effect_engine import EffectEngine, EffectPreset, EffectFrame, ParamSpec

# R183 hsv 统一（本文件为 4 处定义之一）：共享实现（多前缀兜底兼容两种导入根）
try:
    from ..utils.color_math import hsv_to_rgb_float as _hsv_to_rgb_float
except ImportError:
    try:
        from utils.color_math import hsv_to_rgb_float as _hsv_to_rgb_float
    except ImportError:
        from src.utils.color_math import hsv_to_rgb_float as _hsv_to_rgb_float


def _register_v26_presets(engine: "EffectEngine"):
    """注册 v26 效果库扩展预设"""
    from .effect_engine import EffectPreset, ParamSpec
    reg = engine._reg

    # ═══════════════════════════════════════════════════════════════════
    #  辅助工具
    # ═══════════════════════════════════════════════════════════════════
    def _clamp(v, lo=0, hi=255):
        return max(lo, min(hi, int(v)))

    def _sine(t):
        return (math.sin(2.0 * math.pi * t) + 1.0) / 2.0

    def _triangle(t):
        return 1.0 - abs(2.0 * (t % 1.0) - 1.0)

    def _sawtooth(t):
        return t % 1.0

    def _square(t, duty=0.5):
        return 1.0 if (t % 1.0) < duty else 0.0

    def _ease_in_out(t):
        return t * t * (3.0 - 2.0 * t)

    def _elastic(t, bounces=3, decay=4.0):
        return math.exp(-decay * (t % 1.0)) * abs(math.sin(bounces * math.pi * (t % 1.0)))

    def _perlin_like(t, seed=0):
        x = t * 4.0 + seed
        return (math.sin(x) + math.sin(x * 2.3 + 1.7) + math.sin(x * 3.7 + 2.9)) / 3.0 * 0.5 + 0.5

    # ═══════════════════════════════════════════════════════════════════
    #  A. 电机交叉变体
    # ═══════════════════════════════════════════════════════════════════

    # --- A1: 两两交叉 ---
    def _motor_cross_pair(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            pair_idx = seq_i // 2
            pos_in_pair = seq_i % 2
            direction = 1.0 if pos_in_pair == 0 else -1.0
            phase = t * speed + pair_idx * 0.3
            val = 128 + direction * amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_pair_v26", name="两两交叉", category="电机/交叉",
        description="每两台设备为一组，组内反向运动，组间有相位差",
        target_channels=["motor_x", "motor_y"], icon="⇄2",
        tags=["motor", "cross", "pair"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_cross_pair)

    # --- A2: 三三交叉 ---
    def _motor_cross_triple(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 3
            pos_in_group = seq_i % 3
            # 三个位置：0度、120度、240度
            phase_offset = pos_in_group * (2.0 * math.pi / 3.0)
            phase = t * speed + group_idx * 0.2
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase + phase_offset)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_triple_v26", name="三三交叉", category="电机/交叉",
        description="每三台设备为一组，组内120度相位差运动",
        target_channels=["motor_x", "motor_y"], icon="⇄3",
        tags=["motor", "cross", "triple"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_cross_triple)

    # --- A3: 四四交叉 ---
    def _motor_cross_quad(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // 4
            pos_in_group = seq_i % 4
            # 四个位置：0度、90度、180度、270度
            phase_offset = pos_in_group * (2.0 * math.pi / 4.0)
            phase = t * speed + group_idx * 0.15
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase + phase_offset)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_quad_v26", name="四四交叉", category="电机/交叉",
        description="每四台设备为一组，组内90度相位差运动",
        target_channels=["motor_x", "motor_y"], icon="⇄4",
        tags=["motor", "cross", "quad"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_cross_quad)

    # --- A4: N组交叉（可配置分组数）---
    def _motor_cross_n_group(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        groups = max(2, int(params.get("groups", 2)))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group_idx = seq_i // groups
            pos_in_group = seq_i % groups
            phase_offset = pos_in_group * (2.0 * math.pi / groups)
            phase = t * speed + group_idx * 0.2
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase + phase_offset)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_n_group_v26", name="N组交叉", category="电机/交叉",
        description="可配置每N台为一组的交叉运动，组内均匀相位差",
        target_channels=["motor_x", "motor_y"], icon="⇄N",
        tags=["motor", "cross", "configurable"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("groups", "每组数量", "int", 2, 2, 8, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ],
    ), _motor_cross_n_group)

    # --- A5: 渐进交叉（相位逐渐增大）---
    def _motor_cross_progressive(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        spread = float(params.get("spread", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            phase_offset = progress * math.pi * spread
            phase = t * speed
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase + phase_offset)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_progressive_v26", name="渐进交叉", category="电机/交叉",
        description="相位从第一台到最后一台逐渐增大，形成渐进交叉效果",
        target_channels=["motor_x", "motor_y"], icon="⇄↗",
        tags=["motor", "cross", "progressive"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("spread", "展开度", "float", 1.0, 0.1, 4.0, 0.1,
                tooltip="控制效果在空间上的扩散程度或相位展开范围。值越大覆盖越广"),
        ],
    ), _motor_cross_progressive)

    # --- A6: 对称交叉（从中间向两边反向）---
    def _motor_cross_symmetric(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        mid = n / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = (seq_i - mid) / max(1.0, mid)
            direction = 1.0 if seq_i < n // 2 else -1.0
            phase = t * speed + abs(dist) * 0.5
            val = 128 + direction * amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_symmetric_v26", name="对称交叉", category="电机/交叉",
        description="从中间向两边对称反向运动",
        target_channels=["motor_x", "motor_y"], icon="⇄⇆",
        tags=["motor", "cross", "symmetric"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_cross_symmetric)

    # --- A7: 交错交叉（奇数组和偶数组不同波形）---
    def _motor_cross_interleave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        wave_a = params.get("wave_a", "sine")
        wave_b = params.get("wave_b", "triangle")
        n = len(sorted_fx)
        wave_funcs = {"sine": _sine, "triangle": _triangle, "sawtooth": _sawtooth}
        fn_a = wave_funcs.get(wave_a, _sine)
        fn_b = wave_funcs.get(wave_b, _triangle)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i % 2 == 0:
                raw = fn_a(t * speed + seq_i * 0.1)
            else:
                raw = fn_b(t * speed + seq_i * 0.1)
            val = 128 + amplitude * (raw - 0.5) * 254
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_interleave_v26", name="交错交叉", category="电机/交叉",
        description="奇数位和偶数位使用不同波形交错运动",
        target_channels=["motor_x", "motor_y"], icon="⇄≈",
        tags=["motor", "cross", "interleave", "wave"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("wave_a", "波形A", "choice", "sine",
                      choices=["sine", "triangle", "sawtooth"],
                tooltip="控制第一种波形的配置。作用于效果的一部分设备"),
            ParamSpec("wave_b", "波形B", "choice", "triangle",
                      choices=["sine", "triangle", "sawtooth"],
                tooltip="控制第二种波形的配置。作用于效果的另一部分设备"),
        ],
    ), _motor_cross_interleave)

    # --- A8: 分半交叉（前半和后半反向）---
    def _motor_cross_half_split(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        delay = float(params.get("delay", 0.1))
        n = len(sorted_fx)
        half = n // 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < half:
                direction = 1.0
                local_delay = seq_i * delay
            else:
                direction = -1.0
                local_delay = (seq_i - half) * delay
            phase = t * speed + local_delay
            val = 128 + direction * amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_half_split_v26", name="分半交叉", category="电机/交叉",
        description="前半部分和后半部分反向运动，各自内部有延迟",
        target_channels=["motor_x", "motor_y"], icon="⇄½",
        tags=["motor", "cross", "half", "split"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("delay", "组内延迟", "float", 0.1, 0.0, 0.5, 0.02,
                tooltip="控制效果的延迟/错开时间。值越大效果启动越滞后"),
        ],
    ), _motor_cross_half_split)

    # ═══════════════════════════════════════════════════════════════════
    #  B. 电机龙摆变体
    # ═══════════════════════════════════════════════════════════════════

    # --- B1: 三龙摆（三段独立龙摆）---
    def _motor_triple_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        segment_size = max(1, n // 3)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            segment = min(2, seq_i // segment_size)
            local_i = seq_i - segment * segment_size
            local_n = segment_size
            progress = local_i / max(1, local_n - 1)
            # 每段龙摆方向交替
            direction = 1.0 if segment % 2 == 0 else -1.0
            phase = t * speed + progress * 0.8
            val = 128 + direction * amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_triple_dragon_v26", name="三龙摆", category="电机/龙摆",
        description="设备分为三段，每段独立龙摆且方向交替",
        target_channels=["motor_x", "motor_y"], icon="~3~",
        tags=["motor", "dragon", "triple"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_triple_dragon)

    # --- B2: 反向龙摆（从尾到头）---
    def _motor_reverse_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        amplitude = float(params.get("amplitude", 1.0))
        phase_spread = float(params.get("phase_spread", 0.5))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 反向：最后一个设备先动
            progress = 1.0 - (seq_i / max(1, n - 1))
            phase = t * speed + progress * phase_spread
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_reverse_dragon_v26", name="反向龙摆", category="电机/龙摆",
        description="从尾部向头部传递的龙摆效果",
        target_channels=["motor_x", "motor_y"], icon="~←",
        tags=["motor", "dragon", "reverse"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("phase_spread", "相位展开", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制相位在设备序列上的展开程度。值越大设备间的相位分布越广"),
        ],
    ), _motor_reverse_dragon)

    # --- B3: 渐变龙摆（幅度从小到大）---
    def _motor_gradient_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        ch = params.get("ch_type", "motor_x")
        phase_spread = float(params.get("phase_spread", 0.5))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            amp = 0.3 + 0.7 * progress  # 幅度从30%渐变到100%
            phase = t * speed + progress * phase_spread
            val = 128 + amp * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_gradient_dragon_v26", name="渐变龙摆", category="电机/龙摆",
        description="幅度从头到尾逐渐增大的龙摆",
        target_channels=["motor_x", "motor_y"], icon="~↗",
        tags=["motor", "dragon", "gradient"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("phase_spread", "相位展开", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制相位在设备序列上的展开程度。值越大设备间的相位分布越广"),
        ],
    ), _motor_gradient_dragon)

    # --- B4: 龙摆+旋转复合 ---
    def _motor_dragon_rotate(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.6))
        rotate_speed = float(params.get("rotate_speed", 0.3))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            # 龙摆基础
            dragon = math.sin(2 * math.pi * (t * speed + progress * 0.5))
            # 叠加旋转偏移
            rotate_offset = math.sin(2 * math.pi * t * rotate_speed) * 0.3
            val = 128 + amplitude * 127 * (dragon * 0.7 + rotate_offset)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_dragon_rotate_v26", name="龙摆+旋转", category="电机/龙摆",
        description="龙摆基础上叠加整体旋转偏移",
        target_channels=["motor_x", "motor_y"], icon="~⟳",
        tags=["motor", "dragon", "rotate", "compound"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "龙摆速度", "float", 0.6, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("rotate_speed", "旋转速度", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制旋转速度。值越大自转或公转越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_dragon_rotate)

    # --- B5: 加速龙摆（速度渐快）---
    def _motor_accel_dragon(ef, t, sorted_fx, fdefs, params, fps):
        base_speed = float(params.get("speed", 0.5))
        accel = float(params.get("accel", 0.3))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        # 使用周期性加速：速度在一个大周期内变化
        cycle = 8.0  # 8秒一个加速周期
        phase_in_cycle = (t % cycle) / cycle
        current_speed = base_speed + accel * phase_in_cycle
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            phase = t * current_speed + progress * 0.5
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_accel_dragon_v26", name="加速龙摆", category="电机/龙摆",
        description="龙摆速度周期性加速，形成渐快效果",
        target_channels=["motor_x", "motor_y"], icon="~⏩",
        tags=["motor", "dragon", "accelerate"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "基础速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("accel", "加速量", "float", 0.3, 0.0, 2.0, 0.1,
                tooltip="控制加速度或加速因子。值越大速度随时间的增长越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_accel_dragon)

    # --- B6: 中心扩散龙摆 ---
    def _motor_center_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1.0, mid)
            phase = t * speed + dist * 0.8
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_center_dragon_v26", name="中心扩散龙摆", category="电机/龙摆",
        description="从中心向两边扩散的龙摆效果",
        target_channels=["motor_x", "motor_y"], icon="~↔~",
        tags=["motor", "dragon", "center", "spread"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_center_dragon)

    # --- B7: 弹性龙摆 ---
    def _motor_elastic_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.6))
        amplitude = float(params.get("amplitude", 1.0))
        bounces = int(params.get("bounces", 3))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            local_t = (t * speed + progress * 0.4) % 2.0
            if local_t < 1.0:
                raw = _elastic(local_t, bounces=bounces, decay=3.0)
            else:
                raw = _elastic(local_t - 1.0, bounces=bounces, decay=3.0)
            val = 128 + amplitude * 127 * (raw * 2.0 - 1.0)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_elastic_dragon_v26", name="弹性龙摆", category="电机/龙摆",
        description="带弹性回弹效果的龙摆",
        target_channels=["motor_x", "motor_y"], icon="~⚡",
        tags=["motor", "dragon", "elastic", "bounce"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.6, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("bounces", "弹跳次数", "int", 3, 1, 8, 1,
                tooltip="控制弹跳次数。值越大弹跳越多次才停止"),
        ],
    ), _motor_elastic_dragon)

    # --- B8: 双向龙摆（X和Y同时不同相位）---
    def _motor_dual_axis_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.7))
        amplitude = float(params.get("amplitude", 1.0))
        phase_diff = float(params.get("phase_diff", 0.25))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            phase_x = t * speed + progress * 0.5
            phase_y = t * speed + progress * 0.5 + phase_diff
            val_x = 128 + amplitude * 127 * math.sin(2 * math.pi * phase_x)
            val_y = 128 + amplitude * 127 * math.sin(2 * math.pi * phase_y)
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_dual_axis_dragon_v26", name="双轴龙摆", category="电机/龙摆",
        description="X轴和Y轴同时进行龙摆，形成椭圆轨迹",
        target_channels=["motor_x", "motor_y"], icon="~XY",
        tags=["motor", "dragon", "dual_axis"],
        params=[
            ParamSpec("speed", "速度", "float", 0.7, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("phase_diff", "XY相位差", "float", 0.25, 0.0, 1.0, 0.05,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _motor_dual_axis_dragon)

    # ═══════════════════════════════════════════════════════════════════
    #  C. 电机其他变体
    # ═══════════════════════════════════════════════════════════════════

    # --- C1: 扇形开合变体（多段扇形）---
    def _motor_multi_fan(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.6))
        fans = max(2, int(params.get("fans", 2)))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        fan_size = max(1, n // fans)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            fan_idx = min(fans - 1, seq_i // fan_size)
            local_i = seq_i - fan_idx * fan_size
            local_progress = local_i / max(1, fan_size - 1)
            # 每个扇形从中心展开
            center_dist = abs(local_progress - 0.5) * 2.0
            phase = t * speed + fan_idx * 0.3
            open_amount = (_sine(phase) * 2.0 - 1.0) * amplitude
            val = 128 + open_amount * center_dist * 127
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_multi_fan_v26", name="多段扇形", category="电机/开合",
        description="设备分为多个扇形组，每组独立开合",
        target_channels=["motor_x", "motor_y"], icon="⊃⊂",
        tags=["motor", "fan", "multi", "open_close"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.6, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("fans", "扇形数", "int", 2, 2, 6, 1,
                tooltip="控制扇形展开的扇叶数量。值越大扇叶越多越密"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_multi_fan)

    # --- C2: 螺旋上升变体（带衰减）---
    def _motor_spiral_decay(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        decay = float(params.get("decay", 0.3))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            amp = 1.0 - decay * progress
            phase_x = t * speed + progress * 2.0
            phase_y = t * speed + progress * 2.0 + 0.25
            val_x = 128 + amp * 127 * math.sin(2 * math.pi * phase_x)
            val_y = 128 + amp * 127 * math.sin(2 * math.pi * phase_y)
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_spiral_decay_v26", name="螺旋衰减", category="电机/高级",
        description="螺旋运动幅度逐渐衰减",
        target_channels=["motor_x", "motor_y"], icon="🌀↓",
        tags=["motor", "spiral", "decay"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("decay", "衰减率", "float", 0.3, 0.0, 0.9, 0.05,
                tooltip="控制效果衰减时间。值越小回落越快声音越短促，值越大余韵越长"),
        ],
    ), _motor_spiral_decay)

    # --- C3: 钟摆阵列 ---
    def _motor_pendulum_array(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.7))
        amplitude = float(params.get("amplitude", 1.0))
        length_var = float(params.get("length_var", 0.5))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 每个钟摆有不同的"长度"（频率）
            progress = seq_i / max(1, n - 1)
            freq_mult = 1.0 + length_var * progress
            phase = t * speed * freq_mult
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_pendulum_array_v26", name="钟摆阵列", category="电机/高级",
        description="每台设备如不同长度的钟摆，频率逐渐变化",
        target_channels=["motor_x", "motor_y"], icon="⏱⏱",
        tags=["motor", "pendulum", "array"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "基础速度", "float", 0.7, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("length_var", "长度变化", "float", 0.5, 0.0, 2.0, 0.1,
                tooltip="控制长度变化程度。值越大长度变异越显著"),
        ],
    ), _motor_pendulum_array)

    # --- C4: 呼吸开合 ---
    def _motor_breath_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.4))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        mid = (n - 1) / 2.0
        breath = _sine(t * speed)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = (seq_i - mid) / max(1.0, mid)
            val = 128 + dist * breath * 127
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_breath_open_close_v26", name="呼吸开合", category="电机/开合",
        description="像呼吸一样缓慢开合的效果",
        target_channels=["motor_x", "motor_y"], icon="⊂⊃",
        tags=["motor", "breath", "open_close"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.4, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _motor_breath_open_close)

    # --- C5: 波浪追逐 ---
    def _motor_wave_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        wave_len = float(params.get("wave_len", 0.5))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            phase = t * speed - progress / wave_len
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_wave_chase_v26", name="波浪追逐", category="电机/高级",
        description="波浪从一端追逐到另一端",
        target_channels=["motor_x", "motor_y"], icon="≋→",
        tags=["motor", "wave", "chase"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("wave_len", "波长", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制波形的空间波长长度。值越大单个波覆盖的设备范围越广"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_wave_chase)

    # ═══════════════════════════════════════════════════════════════════
    #  D. 灯光矩阵效果
    # ═══════════════════════════════════════════════════════════════════

    # --- D1: 矩阵波浪扫描 ---
#     def _light_matrix_wave_scan(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         direction = params.get("direction", "horizontal")
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             if direction == "horizontal":
#                 progress = col / max(1, cols - 1)
#             elif direction == "vertical":
#                 progress = row / max(1, rows - 1)
#             elif direction == "diagonal":
#                 progress = (row + col) / max(1, rows + cols - 2)
#             else:
#                 progress = col / max(1, cols - 1)
#             phase = t * speed - progress
#             val = _sine(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_wave_scan_v26", name="矩阵波浪扫描", category="灯光/矩阵",
#         description="波浪在矩阵中沿指定方向扫描",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦≋",
#         tags=["light", "matrix", "wave", "scan"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#             ParamSpec("direction", "方向", "choice", "horizontal",
#                       choices=["horizontal", "vertical", "diagonal"]),
#         ],
#     ), _light_matrix_wave_scan)

    # --- D2: 矩阵同心圆扩散 ---
#     def _light_matrix_concentric(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
#         max_dist = math.sqrt(cx * cx + cy * cy)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             dist = math.sqrt((col - cx) ** 2 + (row - cy) ** 2) / max(1.0, max_dist)
#             phase = t * speed - dist * 2.0
#             val = _sine(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_concentric_v26", name="矩阵同心圆", category="灯光/矩阵",
#         description="从矩阵中心向外扩散的同心圆波浪",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦◎",
#         tags=["light", "matrix", "concentric", "radial"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _light_matrix_concentric)

    # --- D3: 矩阵棋盘闪烁 ---
#     def _light_matrix_checkerboard(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         on_val = int(params.get("on_val", 255))
#         off_val = int(params.get("off_val", 0))
#         n = len(sorted_fx)
        # 棋盘翻转
#         flip = int(t * speed) % 2
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             is_white = (row + col + flip) % 2 == 0
#             val = on_val if is_white else off_val
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_checkerboard_v26", name="矩阵棋盘", category="灯光/矩阵",
#         description="棋盘格图案交替闪烁",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⊞",
#         tags=["light", "matrix", "checkerboard", "pattern"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "翻转速度", "float", 2.0, 0.5, 10.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#             ParamSpec("on_val", "亮值", "int", 255, 0, 255, 1),
#             ParamSpec("off_val", "暗值", "int", 0, 0, 255, 1),
#         ],
#     ), _light_matrix_checkerboard)

    # --- D4: 矩阵螺旋 ---
#     def _light_matrix_spiral(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             dx, dy = col - cx, row - cy
#             angle = math.atan2(dy, dx) / (2 * math.pi) + 0.5
#             dist = math.sqrt(dx * dx + dy * dy)
#             phase = t * speed - angle - dist * 0.2
#             val = _sine(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_spiral_v26", name="矩阵螺旋", category="灯光/矩阵",
#         description="从中心向外的螺旋扫描效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🌀",
#         tags=["light", "matrix", "spiral"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _light_matrix_spiral)

    # --- D5: 矩阵雨滴 ---
#     def _light_matrix_rain(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 2.0))
#         density = float(params.get("density", 0.3))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
            # 每列有独立的下落相位
#             col_seed = (col * 7 + 3) % 13
#             drop_phase = (t * speed + col_seed * 0.7) % (rows + 2)
#             dist = abs(row - drop_phase)
#             if dist < 1.5:
#                 val = (1.0 - dist / 1.5) * 255
#             else:
#                 val = 0
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_rain_v26", name="矩阵雨滴", category="灯光/矩阵",
#         description="模拟雨滴从上往下落的效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🌧",
#         tags=["light", "matrix", "rain", "drop"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "下落速度", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("density", "密度", "float", 0.3, 0.1, 1.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _light_matrix_rain)

    # --- D6: 矩阵十字扫描 ---
#     def _light_matrix_cross_scan(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.5))
#         width = float(params.get("width", 1.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 十字线位置
#         h_pos = (t * speed) % cols
#         v_pos = (t * speed * 0.7) % rows
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             h_dist = abs(col - h_pos)
#             v_dist = abs(row - v_pos)
#             min_dist = min(h_dist, v_dist)
#             if min_dist < width:
#                 val = (1.0 - min_dist / width) * 255
#             else:
#                 val = 0
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_cross_scan_v26", name="矩阵十字扫描", category="灯光/矩阵",
#         description="十字光线在矩阵中扫描移动",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦✚",
#         tags=["light", "matrix", "cross", "scan"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.5, 0.1, 5.0, 0.1),
#             ParamSpec("width", "线宽", "float", 1.0, 0.3, 3.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _light_matrix_cross_scan)

    # --- D7: 矩阵呼吸波 ---
#     def _light_matrix_breath_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         mode = params.get("mode", "row_by_row")
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             if mode == "row_by_row":
#                 delay = row * 0.2
#             elif mode == "col_by_col":
#                 delay = col * 0.2
#             elif mode == "center_out":
#                 cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
#                 dist = math.sqrt((col - cx) ** 2 + (row - cy) ** 2)
#                 delay = dist * 0.15
#             else:
#                 delay = 0
#             val = _sine(t * speed - delay) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_breath_wave_v26", name="矩阵呼吸波", category="灯光/矩阵",
#         description="矩阵中的呼吸波浪，支持多种传播方式",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦💨",
#         tags=["light", "matrix", "breath", "wave"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#             ParamSpec("mode", "传播模式", "choice", "row_by_row",
#                       choices=["row_by_row", "col_by_col", "center_out"]),
#         ],
#     ), _light_matrix_breath_wave)

    # --- D8: 矩阵流星 ---
#     def _light_matrix_meteor(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 3.0))
#         tail_len = int(params.get("tail_len", 3))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         direction = params.get("direction", "down")
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             col_seed = (col * 11 + 5) % 7
#             if direction == "down":
#                 head_pos = (t * speed + col_seed) % (rows + tail_len + 2)
#                 dist = head_pos - row
#             elif direction == "up":
#                 head_pos = (rows - 1) - ((t * speed + col_seed) % (rows + tail_len + 2))
#                 dist = row - head_pos
#             elif direction == "left":
#                 head_pos = (cols - 1) - ((t * speed + (row * 3 % 5)) % (cols + tail_len + 2))
#                 dist = col - head_pos
#             else:  # right
#                 head_pos = (t * speed + (row * 3 % 5)) % (cols + tail_len + 2)
#                 dist = head_pos - col
#             if 0 <= dist <= tail_len:
#                 val = (1.0 - dist / tail_len) * 255
#             else:
#                 val = 0
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_meteor_v26", name="矩阵流星", category="灯光/矩阵",
#         description="流星拖尾效果在矩阵中移动",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦☄",
#         tags=["light", "matrix", "meteor", "tail"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 3.0, 0.5, 10.0, 0.5),
#             ParamSpec("tail_len", "尾巴长度", "int", 3, 1, 8, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#             ParamSpec("direction", "方向", "choice", "down",
#                       choices=["down", "up", "left", "right"]),
#         ],
#     ), _light_matrix_meteor)

    # --- D9: 矩阵心跳 ---
#     def _light_matrix_heartbeat(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.2))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
#         max_dist = math.sqrt(cx * cx + cy * cy)
        # 心跳波形：双峰
#         cycle = t * speed % 1.0
#         if cycle < 0.15:
#             beat = cycle / 0.15
#         elif cycle < 0.25:
#             beat = 1.0 - (cycle - 0.15) / 0.10
#         elif cycle < 0.35:
#             beat = (cycle - 0.25) / 0.10 * 0.7
#         elif cycle < 0.50:
#             beat = 0.7 * (1.0 - (cycle - 0.35) / 0.15)
#         else:
#             beat = 0.0
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             dist = math.sqrt((col - cx) ** 2 + (row - cy) ** 2) / max(1.0, max_dist)
#             val = beat * (1.0 - dist * 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_heartbeat_v26", name="矩阵心跳", category="灯光/矩阵",
#         description="从中心向外的心跳脉冲效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦♥",
#         tags=["light", "matrix", "heartbeat", "pulse"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "心跳速度", "float", 1.2, 0.3, 4.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _light_matrix_heartbeat)

    # --- D10: 矩阵随机闪烁 ---
#     def _light_matrix_random_sparkle(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 4.0))
#         density = float(params.get("density", 0.3))
#         ch = params.get("ch_type", "dimmer")
#         n = len(sorted_fx)
#         frame_idx = int(t * speed)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 伪随机：基于时间帧和位置
#             seed = (frame_idx * 31 + seq_i * 17) % 100
#             if seed < density * 100:
#                 val = 200 + (seed % 56)
#             else:
#                 val = 0
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_random_sparkle_v26", name="矩阵星闪", category="灯光/矩阵",
#         description="矩阵中随机位置闪烁如星星",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦✨",
#         tags=["light", "matrix", "random", "sparkle"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "闪烁速度", "float", 4.0, 1.0, 15.0, 0.5),
#             ParamSpec("density", "密度", "float", 0.3, 0.05, 0.8, 0.05),
#         ],
#     ), _light_matrix_random_sparkle)

    # --- D11: 矩阵对角线扫描 ---
#     def _light_matrix_diagonal(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.5))
#         width = float(params.get("width", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         max_sum = rows + cols - 2
#         scan_pos = (t * speed) % (max_sum + width * 2) - width
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row = seq_i // cols
#             col = seq_i % cols
#             diag_val = row + col
#             dist = abs(diag_val - scan_pos)
#             if dist < width:
#                 val = (1.0 - dist / width) * 255
#             else:
#                 val = 0
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_diagonal_v26", name="矩阵对角线", category="灯光/矩阵",
#         description="对角线光带在矩阵中扫描",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⟋",
#         tags=["light", "matrix", "diagonal", "scan"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.5, 0.1, 5.0, 0.1),
#             ParamSpec("width", "光带宽度", "float", 2.0, 0.5, 5.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _light_matrix_diagonal)

    # --- D12: 矩阵蛇形跑灯 ---
#     def _light_matrix_snake(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 2.0))
#         tail = int(params.get("tail", 4))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 蛇形排列：偶数行正序，奇数行反序
#         snake_order = []
#         for r in range(rows):
#             if r % 2 == 0:
#                 for c in range(cols):
#                     idx = r * cols + c
#                     if idx < n:
#                         snake_order.append(idx)
#             else:
#                 for c in range(cols - 1, -1, -1):
#                     idx = r * cols + c
#                     if idx < n:
#                         snake_order.append(idx)
#         total = len(snake_order)
#         head_pos = (t * speed) % (total + tail)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 找到该设备在蛇形中的位置
#             try:
#                 snake_pos = snake_order.index(seq_i)
#             except ValueError:
#                 snake_pos = seq_i
#             dist = head_pos - snake_pos
#             if 0 <= dist <= tail:
#                 val = (1.0 - dist / tail) * 255
#             else:
#                 val = 0
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="light_matrix_snake_v26", name="矩阵蛇形跑灯", category="灯光/矩阵",
#         description="沿蛇形路径移动的跑灯效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🐍",
#         tags=["light", "matrix", "snake", "chase"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("tail", "尾巴长度", "int", 4, 1, 10, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _light_matrix_snake)

    # ═══════════════════════════════════════════════════════════════════
    #  E. 灯光亮度类扩充
    # ═══════════════════════════════════════════════════════════════════

    # --- E1: 渐亮渐暗（多模式）---
    def _light_fade_modes(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        mode = params.get("mode", "all_sync")
        ch = params.get("ch_type", "dimmer")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            if mode == "all_sync":
                val = _sine(t * speed) * 255
            elif mode == "cascade_fwd":
                val = _sine(t * speed - progress * 0.5) * 255
            elif mode == "cascade_rev":
                val = _sine(t * speed - (1.0 - progress) * 0.5) * 255
            elif mode == "center_out":
                mid = abs(progress - 0.5) * 2.0
                val = _sine(t * speed - mid * 0.5) * 255
            elif mode == "random_phase":
                seed = (seq_i * 7 + 3) % 10 / 10.0
                val = _sine(t * speed + seed) * 255
            else:
                val = _sine(t * speed) * 255
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_fade_modes_v26", name="渐亮渐暗(多模式)", category="灯光/亮度",
        description="多种渐亮渐暗传播模式",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="◐◑",
        tags=["light", "fade", "brightness", "modes"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("mode", "模式", "choice", "all_sync",
                      choices=["all_sync", "cascade_fwd", "cascade_rev",
                               "center_out", "random_phase"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
        ],
    ), _light_fade_modes)

    # --- E2: 闪烁变体（多种闪烁模式）---
    def _light_strobe_variants(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 4.0))
        pattern = params.get("pattern", "sync")
        duty = float(params.get("duty", 0.3))
        ch = params.get("ch_type", "dimmer")
        on_val = int(params.get("on_val", 255))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if pattern == "sync":
                phase = t * speed
            elif pattern == "alternate":
                phase = t * speed + (0.5 if seq_i % 2 else 0.0)
            elif pattern == "cascade":
                phase = t * speed + seq_i * 0.1
            elif pattern == "random":
                phase = t * speed + (seq_i * 7.3 % 1.0)
            elif pattern == "group4":
                phase = t * speed + (seq_i // 4) * 0.25
            else:
                phase = t * speed
            val = on_val if (phase % 1.0) < duty else 0
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_strobe_variants_v26", name="闪烁变体", category="灯光/亮度",
        description="多种闪烁模式：同步/交替/级联/随机/分组",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="⚡⚡",
        tags=["light", "strobe", "variants", "brightness"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "频率", "float", 4.0, 0.5, 20.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("pattern", "模式", "choice", "sync",
                      choices=["sync", "alternate", "cascade", "random", "group4"],
                tooltip="选择图案模板。不同图案决定矩阵灯阵的布局"),
            ParamSpec("duty", "占空比", "float", 0.3, 0.05, 0.95, 0.05,
                tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"),
            ParamSpec("on_val", "亮值", "int", 255, 50, 255, 5,
                tooltip="控制开启/点亮时的输出值。效果激活时设备跳至此值"),
        ],
    ), _light_strobe_variants)

    # --- E3: 追逐变体（多种追逐模式）---
    def _light_chase_variants(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        mode = params.get("mode", "single")
        tail = int(params.get("tail", 3))
        ch = params.get("ch_type", "dimmer")
        n = len(sorted_fx)
        if mode == "single":
            head = (t * speed) % n
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                dist = (head - seq_i) % n
                if dist < tail:
                    val = (1.0 - dist / tail) * 255
                else:
                    val = 0
                ef.values[orig_i][ch] = _clamp(val)
        elif mode == "dual":
            head1 = (t * speed) % n
            head2 = (t * speed + n / 2) % n
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                dist1 = (head1 - seq_i) % n
                dist2 = (head2 - seq_i) % n
                v1 = (1.0 - dist1 / tail) * 255 if dist1 < tail else 0
                v2 = (1.0 - dist2 / tail) * 255 if dist2 < tail else 0
                ef.values[orig_i][ch] = _clamp(max(v1, v2))
        elif mode == "bounce":
            cycle = (t * speed) % (2 * n)
            head = cycle if cycle < n else 2 * n - cycle
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                dist = abs(head - seq_i)
                if dist < tail:
                    val = (1.0 - dist / tail) * 255
                else:
                    val = 0
                ef.values[orig_i][ch] = _clamp(val)
        elif mode == "scatter":
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                head = (t * speed + seq_i * 3.7) % n
                dist = abs(head - seq_i) % n
                if dist < tail:
                    val = (1.0 - dist / tail) * 255
                else:
                    val = 0
                ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_chase_variants_v26", name="追逐变体", category="灯光/亮度",
        description="多种追逐模式：单头/双头/弹跳/散射",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="→→",
        tags=["light", "chase", "variants", "brightness"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 2.0, 0.5, 10.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("mode", "模式", "choice", "single",
                      choices=["single", "dual", "bounce", "scatter"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
            ParamSpec("tail", "尾巴长度", "int", 3, 1, 10, 1,
                tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
        ],
    ), _light_chase_variants)

    # ═══════════════════════════════════════════════════════════════════
    #  F. 灯光颜色类扩充
    # ═══════════════════════════════════════════════════════════════════

    # --- F1: 颜色渐变波 ---
    def _light_color_gradient_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        palette = params.get("palette", "rainbow")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            hue = (t * speed + progress) % 1.0
            if palette == "rainbow":
                r, g, b = _hsv_to_rgb(hue, 1.0, 1.0)
            elif palette == "warm":
                r, g, b = _hsv_to_rgb(hue * 0.15, 1.0, 1.0)
            elif palette == "cool":
                r, g, b = _hsv_to_rgb(0.5 + hue * 0.2, 1.0, 1.0)
            elif palette == "fire":
                r = 1.0
                g = max(0, hue * 2.0 - 0.5) if hue < 0.5 else 1.0 - (hue - 0.5) * 2
                b = 0.0
            else:
                r, g, b = _hsv_to_rgb(hue, 1.0, 1.0)
            ef.values[orig_i]["R"] = _clamp(r * 255)
            ef.values[orig_i]["G"] = _clamp(g * 255)
            ef.values[orig_i]["B"] = _clamp(b * 255)

    # R183 hsv 统一（第 4 处定义）：薄委托模块级共享实现（浮点 0..1 口径，
    # s==0 → (v,v,v)）。原式与共享实现 20 万随机 + 边界网格逐位 0 差异。
    def _hsv_to_rgb(h, s, v):
        return _hsv_to_rgb_float(h, s, v)

    reg(EffectPreset(
        id="light_color_gradient_wave_v26", name="颜色渐变波", category="灯光/颜色",
        description="颜色沿设备序列渐变移动",
        target_channels=["R", "G", "B"], icon="🌈≋",
        tags=["light", "color", "gradient", "wave"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("palette", "色板", "choice", "rainbow",
                      choices=["rainbow", "warm", "cool", "fire"],
                tooltip="选择预设的调色板方案。不同方案提供不同的色彩组合"),
        ],
    ), _light_color_gradient_wave)

    # --- F2: 颜色脉冲 ---
    def _light_color_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        color_r = int(params.get("color_r", 255))
        color_g = int(params.get("color_g", 0))
        color_b = int(params.get("color_b", 128))
        mode = params.get("mode", "sync")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if mode == "sync":
                intensity = _sine(t * speed)
            elif mode == "cascade":
                progress = seq_i / max(1, n - 1)
                intensity = _sine(t * speed - progress * 0.5)
            elif mode == "alternate":
                offset = 0.5 if seq_i % 2 else 0.0
                intensity = _sine(t * speed + offset)
            else:
                intensity = _sine(t * speed)
            ef.values[orig_i]["R"] = _clamp(color_r * intensity)
            ef.values[orig_i]["G"] = _clamp(color_g * intensity)
            ef.values[orig_i]["B"] = _clamp(color_b * intensity)

    reg(EffectPreset(
        id="light_color_pulse_v26", name="颜色脉冲", category="灯光/颜色",
        description="指定颜色的脉冲闪烁效果",
        target_channels=["R", "G", "B"], icon="💜⚡",
        tags=["light", "color", "pulse"],
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("color_r", "红色", "int", 255, 0, 255, 5,
                tooltip="控制颜色的红色分量。0=无红色，255=满红"),
            ParamSpec("color_g", "绿色", "int", 0, 0, 255, 5,
                tooltip="控制颜色的绿色分量。0=无绿色，255=满绿"),
            ParamSpec("color_b", "蓝色", "int", 128, 0, 255, 5,
                tooltip="控制颜色的蓝色分量。0=无蓝色，255=满蓝"),
            ParamSpec("mode", "模式", "choice", "sync",
                      choices=["sync", "cascade", "alternate"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
        ],
    ), _light_color_pulse)

    # --- F3: 颜色矩阵渐变 ---
    def _light_color_matrix_gradient(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            hue = (t * speed + col / max(1, cols) * 0.5 + row / max(1, rows) * 0.5) % 1.0
            r, g, b = _hsv_to_rgb(hue, 1.0, 1.0)
            ef.values[orig_i]["R"] = _clamp(r * 255)
            ef.values[orig_i]["G"] = _clamp(g * 255)
            ef.values[orig_i]["B"] = _clamp(b * 255)

    reg(EffectPreset(
        id="light_color_matrix_gradient_v26", name="颜色矩阵渐变", category="灯光/颜色",
        description="矩阵中的二维颜色渐变",
        target_channels=["R", "G", "B"], icon="▦🌈",
        tags=["light", "color", "matrix", "gradient"],
        params=[
            ParamSpec("speed", "速度", "float", 0.3, 0.1, 2.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _light_color_matrix_gradient)

    # --- F4: 颜色闪电 ---
    def _light_color_lightning(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 3.0))
        color_r = int(params.get("color_r", 200))
        color_g = int(params.get("color_g", 200))
        color_b = int(params.get("color_b", 255))
        n = len(sorted_fx)
        frame_idx = int(t * speed * 10)
        # 闪电：随机短暂全亮
        is_flash = (frame_idx % 37 < 3) or (frame_idx % 53 < 2)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if is_flash:
                # 闪电时部分设备亮
                seed = (frame_idx + seq_i * 13) % 7
                if seed < 4:
                    ef.values[orig_i]["R"] = color_r
                    ef.values[orig_i]["G"] = color_g
                    ef.values[orig_i]["B"] = color_b
                else:
                    ef.values[orig_i]["R"] = 0
                    ef.values[orig_i]["G"] = 0
                    ef.values[orig_i]["B"] = 0
            else:
                ef.values[orig_i]["R"] = 0
                ef.values[orig_i]["G"] = 0
                ef.values[orig_i]["B"] = 0

    reg(EffectPreset(
        id="light_color_lightning_v26", name="颜色闪电", category="灯光/颜色",
        description="模拟闪电的随机短暂闪烁",
        target_channels=["R", "G", "B"], icon="⚡🎨",
        tags=["light", "color", "lightning", "random"],
        params=[
            ParamSpec("speed", "频率", "float", 3.0, 0.5, 10.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("color_r", "红色", "int", 200, 0, 255, 5,
                tooltip="控制颜色的红色分量。0=无红色，255=满红"),
            ParamSpec("color_g", "绿色", "int", 200, 0, 255, 5,
                tooltip="控制颜色的绿色分量。0=无绿色，255=满绿"),
            ParamSpec("color_b", "蓝色", "int", 255, 0, 255, 5,
                tooltip="控制颜色的蓝色分量。0=无蓝色，255=满蓝"),
        ],
    ), _light_color_lightning)

    # ═══════════════════════════════════════════════════════════════════
    #  G. 泵矩阵效果
    # ═══════════════════════════════════════════════════════════════════

    # --- G1: 泵矩阵波浪 ---
    def _pump_matrix_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        direction = params.get("direction", "horizontal")
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            if direction == "horizontal":
                progress = col / max(1, cols - 1)
            elif direction == "vertical":
                progress = row / max(1, rows - 1)
            elif direction == "diagonal":
                progress = (row + col) / max(1, rows + cols - 2)
            else:
                progress = col / max(1, cols - 1)
            phase = t * speed - progress
            val = _sine(phase) * 255
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_wave_v26", name="泵矩阵波浪", category="泵/矩阵",
        description="矩阵排列的泵形成波浪效果",
        target_channels=["pump"], icon="▦≋P",
        tags=["pump", "matrix", "wave"],
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("direction", "方向", "choice", "horizontal",
                      choices=["horizontal", "vertical", "diagonal"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
        ],
    ), _pump_matrix_wave)

    # --- G2: 泵矩阵同心圆 ---
    def _pump_matrix_concentric(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
        max_dist = math.sqrt(cx * cx + cy * cy)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            dist = math.sqrt((col - cx) ** 2 + (row - cy) ** 2) / max(1.0, max_dist)
            phase = t * speed - dist * 2.0
            val = _sine(phase) * 255
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_concentric_v26", name="泵矩阵同心圆", category="泵/矩阵",
        description="从矩阵中心向外扩散的同心圆水型",
        target_channels=["pump"], icon="▦◎P",
        tags=["pump", "matrix", "concentric"],
        params=[
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_concentric)

    # --- G3: 泵矩阵喷射图案 ---
    def _pump_matrix_pattern(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        pattern = params.get("pattern", "checkerboard")
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        flip = int(t * speed) % 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            if pattern == "checkerboard":
                on = (row + col + flip) % 2 == 0
            elif pattern == "stripes_h":
                on = (row + flip) % 2 == 0
            elif pattern == "stripes_v":
                on = (col + flip) % 2 == 0
            elif pattern == "cross":
                mid_r, mid_c = rows // 2, cols // 2
                on = (row == mid_r or col == mid_c) if flip == 0 else (row != mid_r and col != mid_c)
            elif pattern == "diamond":
                cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
                dist = abs(col - cx) + abs(row - cy)
                threshold = (t * speed) % (cx + cy + 1)
                on = dist < threshold
            else:
                on = True
            ef.values[orig_i]["pump"] = 255 if on else 0

    reg(EffectPreset(
        id="pump_matrix_pattern_v26", name="泵矩阵图案", category="泵/矩阵",
        description="矩阵泵按各种图案交替喷射",
        target_channels=["pump"], icon="▦⊞P",
        tags=["pump", "matrix", "pattern"],
        params=[
            ParamSpec("speed", "切换速度", "float", 2.0, 0.5, 8.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("pattern", "图案", "choice", "checkerboard",
                      choices=["checkerboard", "stripes_h", "stripes_v", "cross", "diamond"],
                tooltip="选择图案模板。不同图案决定矩阵灯阵的布局"),
        ],
    ), _pump_matrix_pattern)

    # --- G4: 泵矩阵螺旋 ---
    def _pump_matrix_spiral(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            dx, dy = col - cx, row - cy
            angle = math.atan2(dy, dx) / (2 * math.pi) + 0.5
            dist = math.sqrt(dx * dx + dy * dy)
            phase = t * speed - angle - dist * 0.2
            val = _sine(phase) * 255
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_spiral_v26", name="泵矩阵螺旋", category="泵/矩阵",
        description="螺旋形水型从中心向外旋转",
        target_channels=["pump"], icon="▦🌀P",
        tags=["pump", "matrix", "spiral"],
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_spiral)

    # --- G5: 泵矩阵对称喷射 ---
    def _pump_matrix_symmetric(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        axis = params.get("axis", "vertical")
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            if axis == "vertical":
                mirror_pos = abs(col - (cols - 1) / 2.0) / max(1.0, (cols - 1) / 2.0)
            elif axis == "horizontal":
                mirror_pos = abs(row - (rows - 1) / 2.0) / max(1.0, (rows - 1) / 2.0)
            elif axis == "both":
                mx = abs(col - (cols - 1) / 2.0) / max(1.0, (cols - 1) / 2.0)
                my = abs(row - (rows - 1) / 2.0) / max(1.0, (rows - 1) / 2.0)
                mirror_pos = (mx + my) / 2.0
            else:
                mirror_pos = 0
            phase = t * speed - mirror_pos
            val = _sine(phase) * 255
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_symmetric_v26", name="泵矩阵对称", category="泵/矩阵",
        description="沿轴线对称的水型效果",
        target_channels=["pump"], icon="▦⇔P",
        tags=["pump", "matrix", "symmetric"],
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("axis", "对称轴", "choice", "vertical",
                      choices=["vertical", "horizontal", "both"],
                tooltip="选择参考轴方向。决定效果沿哪个空间维度展开"),
        ],
    ), _pump_matrix_symmetric)

    # --- G6: 泵矩阵雨滴 ---
    def _pump_matrix_rain(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            col_seed = (col * 7 + 3) % 13
            drop_phase = (t * speed + col_seed * 0.7) % (rows + 2)
            dist = abs(row - drop_phase)
            if dist < 1.5:
                val = (1.0 - dist / 1.5) * 255
            else:
                val = 0
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_rain_v26", name="泵矩阵雨滴", category="泵/矩阵",
        description="模拟雨滴从上往下落的水型",
        target_channels=["pump"], icon="▦🌧P",
        tags=["pump", "matrix", "rain"],
        params=[
            ParamSpec("speed", "下落速度", "float", 2.0, 0.5, 8.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_rain)

    # --- G7: 泵矩阵心跳 ---
    def _pump_matrix_heartbeat(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        cx, cy = (cols - 1) / 2.0, (rows - 1) / 2.0
        max_dist = math.sqrt(cx * cx + cy * cy)
        cycle = t * speed % 1.0
        if cycle < 0.15:
            beat = cycle / 0.15
        elif cycle < 0.25:
            beat = 1.0 - (cycle - 0.15) / 0.10
        elif cycle < 0.35:
            beat = (cycle - 0.25) / 0.10 * 0.7
        elif cycle < 0.50:
            beat = 0.7 * (1.0 - (cycle - 0.35) / 0.15)
        else:
            beat = 0.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            dist = math.sqrt((col - cx) ** 2 + (row - cy) ** 2) / max(1.0, max_dist)
            val = beat * (1.0 - dist * 0.5) * 255
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_heartbeat_v26", name="泵矩阵心跳", category="泵/矩阵",
        description="从中心向外的心跳脉冲水型",
        target_channels=["pump"], icon="▦♥P",
        tags=["pump", "matrix", "heartbeat"],
        params=[
            ParamSpec("speed", "心跳速度", "float", 1.0, 0.3, 4.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_heartbeat)

    # --- G8: 泵矩阵流星 ---
    def _pump_matrix_meteor(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 3.0))
        tail_len = int(params.get("tail_len", 3))
        cols = max(1, int(params.get("cols", 4)))
        direction = params.get("direction", "up")
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            col_seed = (col * 11 + 5) % 7
            if direction == "up":
                head_pos = (rows - 1) - ((t * speed + col_seed) % (rows + tail_len + 2))
                dist = row - head_pos
            else:
                head_pos = (t * speed + col_seed) % (rows + tail_len + 2)
                dist = head_pos - row
            if 0 <= dist <= tail_len:
                val = (1.0 - dist / tail_len) * 255
            else:
                val = 0
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_meteor_v26", name="泵矩阵流星", category="泵/矩阵",
        description="流星拖尾水型在矩阵中移动",
        target_channels=["pump"], icon="▦☄P",
        tags=["pump", "matrix", "meteor"],
        params=[
            ParamSpec("speed", "速度", "float", 3.0, 0.5, 10.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("tail_len", "尾巴长度", "int", 3, 1, 8, 1,
                tooltip="控制拖尾的具体长度。值越大尾部延伸越远"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("direction", "方向", "choice", "up",
                      choices=["up", "down"],
                tooltip="控制效果的传播/应用方向。不同方向产生不同的空间动感"),
        ],
    ), _pump_matrix_meteor)

    # --- G9: 泵矩阵蛇形 ---
    def _pump_matrix_snake(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        tail = int(params.get("tail", 4))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        snake_order = []
        for r in range(rows):
            if r % 2 == 0:
                for c in range(cols):
                    idx = r * cols + c
                    if idx < n:
                        snake_order.append(idx)
            else:
                for c in range(cols - 1, -1, -1):
                    idx = r * cols + c
                    if idx < n:
                        snake_order.append(idx)
        total = len(snake_order)
        head_pos = (t * speed) % (total + tail)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            try:
                snake_pos = snake_order.index(seq_i)
            except ValueError:
                snake_pos = seq_i
            dist = head_pos - snake_pos
            if 0 <= dist <= tail:
                val = (1.0 - dist / tail) * 255
            else:
                val = 0
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_snake_v26", name="泵矩阵蛇形", category="泵/矩阵",
        description="沿蛇形路径移动的水型效果",
        target_channels=["pump"], icon="▦🐍P",
        tags=["pump", "matrix", "snake"],
        params=[
            ParamSpec("speed", "速度", "float", 2.0, 0.5, 8.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("tail", "尾巴长度", "int", 4, 1, 10, 1,
                tooltip="控制拖尾长度。值越大拖尾越长，视觉残留时间越久"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_snake)

    # --- G10: 泵矩阵十字 ---
    def _pump_matrix_cross(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        width = float(params.get("width", 1.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        h_pos = (t * speed) % cols
        v_pos = (t * speed * 0.7) % rows
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row = seq_i // cols
            col = seq_i % cols
            h_dist = abs(col - h_pos)
            v_dist = abs(row - v_pos)
            min_dist = min(h_dist, v_dist)
            if min_dist < width:
                val = (1.0 - min_dist / width) * 255
            else:
                val = 0
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_cross_v26", name="泵矩阵十字", category="泵/矩阵",
        description="十字水型在矩阵中扫描移动",
        target_channels=["pump"], icon="▦✚P",
        tags=["pump", "matrix", "cross", "scan"],
        params=[
            ParamSpec("speed", "速度", "float", 1.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("width", "线宽", "float", 1.0, 0.3, 3.0, 0.1,
                tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_cross)

    # ═══════════════════════════════════════════════════════════════════
    #  H. 泵其他变体
    # ═══════════════════════════════════════════════════════════════════

    # --- H1: 泵交替脉冲 ---
    def _pump_alternate_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        groups = max(2, int(params.get("groups", 2)))
        attack = float(params.get("attack", 0.3))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i % groups
            phase = (t * speed + grp / groups) % 1.0
            if phase < attack:
                val = (phase / attack) * 255
            else:
                val = max(0, (1.0 - (phase - attack) / (1.0 - attack)) * 255)
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_alternate_pulse_v26", name="泵交替脉冲", category="泵/脉冲",
        description="分组交替脉冲喷射",
        target_channels=["pump"], icon="⇄P",
        tags=["pump", "alternate", "pulse"],
        params=[
            ParamSpec("speed", "速度", "float", 1.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("groups", "分组数", "int", 2, 2, 8, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("attack", "上升时间比", "float", 0.3, 0.05, 0.9, 0.05,
                tooltip="控制效果起音/启动时间。值越小响应越迅速（适合打击感），值越大渐入越平滑"),
        ],
    ), _pump_alternate_pulse)

    # --- H2: 泵呼吸波 ---
    def _pump_breath_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        mode = params.get("mode", "cascade")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            if mode == "cascade":
                delay = progress * 0.5
            elif mode == "center_out":
                delay = abs(progress - 0.5) * 1.0
            elif mode == "sync":
                delay = 0
            else:
                delay = 0
            val = _sine(t * speed - delay) * 255
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_breath_wave_v26", name="泵呼吸波", category="泵/渐变",
        description="泵的呼吸波浪效果",
        target_channels=["pump"], icon="💨P",
        tags=["pump", "breath", "wave"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("mode", "模式", "choice", "cascade",
                      choices=["cascade", "center_out", "sync"],
                tooltip="选择效果的工作模式。不同模式产生截然不同的视觉表现"),
        ],
    ), _pump_breath_wave)

    # --- H3: 泵阶梯上升 ---
    def _pump_staircase_rise(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        steps = max(2, int(params.get("steps", 4)))
        n = len(sorted_fx)
        cycle_pos = (t * speed) % 1.0
        current_step = int(cycle_pos * steps)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            device_step = int((seq_i / max(1, n)) * steps)
            if device_step <= current_step:
                val = 255
            else:
                val = 0
            ef.values[orig_i]["pump"] = _clamp(val)

    reg(EffectPreset(
        id="pump_staircase_rise_v26", name="泵阶梯上升", category="泵/渐变",
        description="泵逐级阶梯式上升",
        target_channels=["pump"], icon="📶P",
        tags=["pump", "staircase", "rise"],
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("steps", "阶梯数", "int", 4, 2, 10, 1,
                tooltip="控制步进/分段/级数。值越大离散阶梯越精细"),
        ],
    ), _pump_staircase_rise)
