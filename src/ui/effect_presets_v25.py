"""
effect_presets_v25.py — 效果库大幅扩充 (v25)
=============================================
重点扩充：
  A. 电机高级动作（30+ 新效果）
     - 各种交叉：交叉摆、交叉龙摆、交叉旋转、交叉开合、交叉波浪
     - 各种波形：正弦波、三角波、锯齿波、方波、复合波、调频波、调幅波
     - 形象动作：飞鸟展翅、杨柳摆、孔雀开屏、蝴蝶、凤凰、天鹅、海浪
     - 高级复杂：多层嵌套、渐变序列、呼吸叠加、随机漫步、弹性回弹
  B. 灯光/泵/阀门补充效果
"""
from __future__ import annotations
import math
import random
from typing import Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .effect_engine import EffectEngine, EffectPreset, EffectFrame, ParamSpec


def _register_v25_presets(engine: "EffectEngine"):
    """注册 v25 效果库扩展预设"""
    from .effect_engine import EffectPreset, ParamSpec
    reg = engine._reg

    # ═══════════════════════════════════════════════════════════════════
    #  辅助工具
    # ═══════════════════════════════════════════════════════════════════
    def _clamp(v, lo=0, hi=255):
        return max(lo, min(hi, int(v)))

    def _ease_in_out(t):
        return t * t * (3.0 - 2.0 * t)

    def _triangle(t):
        return 1.0 - abs(2.0 * (t % 1.0) - 1.0)

    def _sawtooth(t):
        return t % 1.0

    def _sine(t):
        return (math.sin(2.0 * math.pi * t) + 1.0) / 2.0

    def _elastic(t, bounces=3, decay=4.0):
        """弹性回弹波形"""
        return math.exp(-decay * t) * abs(math.sin(bounces * math.pi * t))

    def _bezier_ease(t, p1=0.42, p2=0.58):
        """简化贝塞尔缓动"""
        t2 = t * t
        t3 = t2 * t
        return 3.0 * (1.0 - t) * (1.0 - t) * t * p1 + 3.0 * (1.0 - t) * t2 * p2 + t3

    def _perlin_like(t, seed=0):
        """伪柏林噪声（简化版）"""
        x = t * 4.0 + seed
        return (math.sin(x) + math.sin(x * 2.3 + 1.7) + math.sin(x * 3.7 + 2.9)) / 3.0 * 0.5 + 0.5

    # ═══════════════════════════════════════════════════════════════════
    #  A. 电机高级动作 — 交叉系列
    # ═══════════════════════════════════════════════════════════════════

    # --- A1: 交叉龙摆（相邻设备反向摆动）---
    def _motor_cross_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        phase_offset = float(params.get("phase_offset", 0.5))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 奇偶设备反向
            direction = 1.0 if seq_i % 2 == 0 else -1.0
            phase = t * speed + seq_i * phase_offset / n
            val = 128 + direction * amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_dragon_v25", name="交叉龙摆", category="电机/交叉",
        description="相邻设备反向龙摆，形成交叉波浪效果",
        target_channels=["motor_x", "motor_y"], icon="X~",
        tags=["motor", "cross", "dragon", "advanced"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("phase_offset", "相位偏移", "float", 0.5, 0.0, 2.0, 0.1,
                tooltip="控制整体相位的偏移量。值越大所有设备的起始相位越滞后"),
        ],
    ), _motor_cross_dragon)

    # --- A2: 交叉旋转（奇偶组反向旋转）---
    def _motor_cross_rotation(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        ch = params.get("ch_type", "motor_x")
        groups = max(2, int(params.get("groups", 2)))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i % groups
            direction = 1.0 if grp % 2 == 0 else -1.0
            phase = t * speed * direction
            val = 128 + 127 * math.sin(2 * math.pi * phase + seq_i * 0.3)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cross_rotation_v25", name="交叉旋转", category="电机/交叉",
        description="奇偶分组反向旋转，形成绞合效果",
        target_channels=["motor_x", "motor_y"], icon="X@",
        tags=["motor", "cross", "rotation"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("groups", "分组数", "int", 2, 2, 8, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
        ],
    ), _motor_cross_rotation)

    # --- A3: 交叉波浪（多层交错波浪）---
    def _motor_cross_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        wavelength = float(params.get("wavelength", 3.0))
        layers = max(1, int(params.get("layers", 2)))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            val = 0.0
            for layer in range(layers):
                freq = (layer + 1) * wavelength
                direction = 1.0 if layer % 2 == 0 else -1.0
                val += math.sin(2 * math.pi * (t * speed * direction + seq_i / max(1, n) * freq))
            val = (val / layers + 1.0) / 2.0
            ef.values[orig_i][ch] = _clamp(val * 255)

    reg(EffectPreset(
        id="motor_cross_wave_v25", name="交叉波浪", category="电机/交叉",
        description="多层交错波浪叠加，形成复杂水纹效果",
        target_channels=["motor_x", "motor_y"], icon="X≈",
        tags=["motor", "cross", "wave", "complex"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.6, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("wavelength", "波长", "float", 3.0, 0.5, 10.0, 0.5,
                tooltip="控制波形在设备序列上的空间波长。值越大一个完整波覆盖的设备越多"),
            ParamSpec("layers", "层数", "int", 2, 1, 5, 1,
                tooltip="控制叠加层数。值越大层数越多，效果越丰富厚重"),
        ],
    ), _motor_cross_wave)

    # --- A4: 交叉开合（左右交替开合）---
    def _motor_cross_open_close(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        mid = n // 2
        phase = (t * speed) % 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < mid:
                # 左半：phase 0~1 开, 1~2 合
                val = _sine(phase / 2.0) if phase < 1.0 else _sine(1.0 - (phase - 1.0) / 2.0)
            else:
                # 右半：反相
                val = _sine(1.0 - phase / 2.0) if phase < 1.0 else _sine((phase - 1.0) / 2.0)
            ef.values[orig_i][ch] = _clamp(val * 255)

    reg(EffectPreset(
        id="motor_cross_open_close_v25", name="交叉开合", category="电机/交叉",
        description="左右两组交替开合，形成呼吸般的交叉运动",
        target_channels=["motor_x", "motor_y"], icon="X<>",
        tags=["motor", "cross", "open", "close"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _motor_cross_open_close)

    # ═══════════════════════════════════════════════════════════════════
    #  B. 电机高级动作 — 波形系列
    # ═══════════════════════════════════════════════════════════════════

    # --- B1: 正弦波传播 ---
    def _motor_sine_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        wavelength = float(params.get("wavelength", 4.0))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i / max(1, n) * wavelength
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_sine_wave_v25", name="正弦波传播", category="电机/波形",
        description="正弦波沿设备序列传播，经典水波效果",
        target_channels=["motor_x", "motor_y"], icon="∿",
        tags=["motor", "wave", "sine"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("wavelength", "波长", "float", 4.0, 0.5, 20.0, 0.5,
                tooltip="控制波形在设备序列上的空间波长。值越大一个完整波覆盖的设备越多"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_sine_wave)

    # --- B2: 三角波传播 ---
    def _motor_triangle_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        wavelength = float(params.get("wavelength", 4.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i / max(1, n) * wavelength
            val = _triangle(phase) * 255
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_triangle_wave_v25", name="三角波传播", category="电机/波形",
        description="三角波沿设备序列传播，线性往返效果",
        target_channels=["motor_x", "motor_y"], icon="△",
        tags=["motor", "wave", "triangle"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("wavelength", "波长", "float", 4.0, 0.5, 20.0, 0.5,
                tooltip="控制波形在设备序列上的空间波长。值越大一个完整波覆盖的设备越多"),
        ],
    ), _motor_triangle_wave)

    # --- B3: 锯齿波传播 ---
    def _motor_sawtooth_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        wavelength = float(params.get("wavelength", 4.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i / max(1, n) * wavelength
            val = _sawtooth(phase) * 255
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_sawtooth_wave_v25", name="锯齿波传播", category="电机/波形",
        description="锯齿波沿设备序列传播，快升慢降效果",
        target_channels=["motor_x", "motor_y"], icon="⟋",
        tags=["motor", "wave", "sawtooth"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("wavelength", "波长", "float", 4.0, 0.5, 20.0, 0.5,
                tooltip="控制波形在设备序列上的空间波长。值越大一个完整波覆盖的设备越多"),
        ],
    ), _motor_sawtooth_wave)

    # --- B4: 复合波（正弦+谐波叠加）---
    def _motor_compound_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        harmonics = max(1, int(params.get("harmonics", 3)))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            val = 0.0
            for h in range(1, harmonics + 1):
                phase = t * speed * h - seq_i / max(1, n) * h * 2
                val += math.sin(2 * math.pi * phase) / h
            val = (val / harmonics + 1.0) / 2.0
            ef.values[orig_i][ch] = _clamp(val * 255)

    reg(EffectPreset(
        id="motor_compound_wave_v25", name="复合波", category="电机/波形",
        description="多次谐波叠加，形成复杂有机波形",
        target_channels=["motor_x", "motor_y"], icon="≋",
        tags=["motor", "wave", "compound", "harmonic"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.6, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("harmonics", "谐波数", "int", 3, 1, 8, 1,
                tooltip="控制谐波次数。值越高叠加的谐波越多，波形越复杂"),
        ],
    ), _motor_compound_wave)

    # --- B5: 调频波（FM合成）---
    def _motor_fm_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        mod_depth = float(params.get("mod_depth", 2.0))
        mod_freq = float(params.get("mod_freq", 0.3))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            carrier = t * speed - seq_i / max(1, n) * 3.0
            modulator = mod_depth * math.sin(2 * math.pi * mod_freq * t + seq_i * 0.5)
            val = (math.sin(2 * math.pi * carrier + modulator) + 1.0) / 2.0
            ef.values[orig_i][ch] = _clamp(val * 255)

    reg(EffectPreset(
        id="motor_fm_wave_v25", name="调频波(FM)", category="电机/波形",
        description="FM合成波形，产生丰富的泛音运动",
        target_channels=["motor_x", "motor_y"], icon="FM",
        tags=["motor", "wave", "fm", "synthesis"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("mod_depth", "调制深度", "float", 2.0, 0.1, 8.0, 0.1,
                tooltip="控制调制深度。值越大调制信号对载波的影响越强"),
            ParamSpec("mod_freq", "调制频率", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制调制频率。值越高调制信号的波动越快"),
        ],
    ), _motor_fm_wave)

    # ═══════════════════════════════════════════════════════════════════
    #  C. 电机高级动作 — 形象动作系列
    # ═══════════════════════════════════════════════════════════════════

    # --- C1: 飞鸟展翅 ---
    def _motor_bird_wings(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        amplitude = float(params.get("amplitude", 1.0))
        n = len(sorted_fx)
        mid = n // 2
        # 翅膀拍动：从中心向两侧对称，带缓动
        wing_phase = _ease_in_out(abs(math.sin(math.pi * t * speed)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist_from_center = abs(seq_i - mid) / max(1, mid)
            # 越远离中心，摆幅越大（翅尖运动更大）
            val_x = 128 + amplitude * 127 * wing_phase * dist_from_center * (1 if seq_i >= mid else -1)
            # Y轴：翅尖略微上扬
            val_y = 128 + amplitude * 60 * wing_phase * dist_from_center
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_bird_wings_v25", name="飞鸟展翅", category="电机/形象",
        description="模拟鸟类展翅拍动，从中心向两侧对称运动，翅尖幅度最大",
        target_channels=["motor_x", "motor_y"], icon="🕊",
        tags=["motor", "bird", "wings", "figurative"],
        params=[
            ParamSpec("speed", "拍翅速度", "float", 0.8, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_bird_wings)

    # --- C2: 杨柳摆风 ---
    def _motor_willow_breeze(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.4))
        sway = float(params.get("sway", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 柳枝：越靠末端摆幅越大，有延迟
            progress = seq_i / max(1, n - 1)
            delay = progress * 0.8
            # 主摆动 + 微风扰动
            main_swing = math.sin(2 * math.pi * (t * speed - delay)) * sway * progress
            breeze = 0.15 * math.sin(2 * math.pi * (t * speed * 3.7 + seq_i * 0.7))
            val = 128 + (main_swing + breeze) * 127
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_willow_breeze_v25", name="杨柳摆风", category="电机/形象",
        description="模拟杨柳随风摆动，末端幅度大、有延迟，带微风扰动",
        target_channels=["motor_x", "motor_y"], icon="🌿",
        tags=["motor", "willow", "breeze", "figurative"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "风速", "float", 0.4, 0.1, 2.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("sway", "摆幅", "float", 1.0, 0.1, 1.0, 0.05,
                tooltip="控制摆幅的比例。0 为最小，1.0 为最大"),
        ],
    ), _motor_willow_breeze)

    # --- C3: 孔雀开屏 ---
    def _motor_peacock_fan(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        n = len(sorted_fx)
        mid = n // 2
        # 开屏动画：从中心向两侧逐渐展开
        open_progress = min(1.0, (t * speed) % 2.0)
        if open_progress > 1.0:
            open_progress = 2.0 - open_progress  # 合屏
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1, mid)
            # 展开角度
            angle = dist * math.pi * 0.8 * open_progress
            # 每根羽毛微微颤动
            tremor = 0.05 * math.sin(t * 8.0 + seq_i * 1.3)
            val_x = 128 + 127 * math.sin(angle + tremor) * (1 if seq_i >= mid else -1)
            val_y = 128 + 80 * math.cos(angle) * open_progress
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_peacock_fan_v25", name="孔雀开屏", category="电机/形象",
        description="模拟孔雀开屏，从中心向两侧扇形展开，带羽毛颤动",
        target_channels=["motor_x", "motor_y"], icon="🦚",
        tags=["motor", "peacock", "fan", "figurative"],
        params=[
            ParamSpec("speed", "开合速度", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _motor_peacock_fan)

    # --- C4: 蝴蝶翩飞 ---
    def _motor_butterfly(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.2))
        n = len(sorted_fx)
        mid = n // 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1, mid)
            side = 1.0 if seq_i >= mid else -1.0
            # 蝴蝶翅膀：快速拍动 + 慢速飘移
            flap = math.sin(2 * math.pi * t * speed * 2.0) * dist
            drift = 0.3 * math.sin(2 * math.pi * t * speed * 0.3)
            val_x = 128 + side * (flap * 100 + drift * 40)
            val_y = 128 + 50 * math.sin(2 * math.pi * t * speed * 0.5 + dist * 2)
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_butterfly_v25", name="蝴蝶翩飞", category="电机/形象",
        description="模拟蝴蝶翅膀快速拍动并缓慢飘移",
        target_channels=["motor_x", "motor_y"], icon="🦋",
        tags=["motor", "butterfly", "figurative"],
        params=[
            ParamSpec("speed", "速度", "float", 1.2, 0.1, 4.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _motor_butterfly)

    # --- C5: 凤凰涅槃 ---
    def _motor_phoenix(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        n = len(sorted_fx)
        mid = n // 2
        cycle = (t * speed) % 4.0  # 4秒一个完整周期
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - mid) / max(1, mid)
            side = 1.0 if seq_i >= mid else -1.0
            if cycle < 1.0:
                # 收缩（涅槃前）
                val_x = 128 + side * dist * 80 * (1.0 - cycle)
                val_y = 128 - 60 * cycle
            elif cycle < 2.0:
                # 爆发（重生）
                burst = (cycle - 1.0)
                val_x = 128 + side * dist * 127 * burst
                val_y = 128 + 100 * burst * dist
            elif cycle < 3.5:
                # 展翅飞翔
                fly_t = (cycle - 2.0) / 1.5
                val_x = 128 + side * dist * 127 * math.cos(fly_t * math.pi)
                val_y = 128 + 80 * dist * math.sin(fly_t * math.pi * 2)
            else:
                # 缓慢回归
                ret = (cycle - 3.5) / 0.5
                val_x = 128 + side * dist * 80 * (1.0 - ret)
                val_y = 128 + 40 * (1.0 - ret)
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_phoenix_v25", name="凤凰涅槃", category="电机/形象",
        description="收缩→爆发→展翅→回归的完整凤凰涅槃动作序列",
        target_channels=["motor_x", "motor_y"], icon="🔥",
        tags=["motor", "phoenix", "figurative", "sequence"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 2.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _motor_phoenix)

    # --- C6: 天鹅湖 ---
    def _motor_swan_lake(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.6))
        grace = float(params.get("grace", 1.0))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            # 天鹅颈部S形曲线
            neck_curve = math.sin(progress * math.pi * 2 + t * speed) * grace
            # 优雅的缓动
            val_x = 128 + 100 * _bezier_ease(abs(neck_curve)) * (1 if neck_curve > 0 else -1)
            val_y = 128 + 60 * math.sin(t * speed * 0.5 + progress * math.pi)
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_swan_lake_v25", name="天鹅湖", category="电机/形象",
        description="优雅的天鹅颈部S形曲线运动，带贝塞尔缓动",
        target_channels=["motor_x", "motor_y"], icon="🦢",
        tags=["motor", "swan", "graceful", "figurative"],
        params=[
            ParamSpec("speed", "速度", "float", 0.6, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("grace", "优雅度", "float", 1.0, 0.1, 2.0, 0.1,
                tooltip="控制缓冲/过渡宽裕度。值越大变化越柔和"),
        ],
    ), _motor_swan_lake)

    # --- C7: 海浪翻涌 ---
    def _motor_ocean_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        intensity = float(params.get("intensity", 1.0))
        ch = params.get("ch_type", "motor_y")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            # 主浪 + 副浪 + 涟漪
            main_wave = math.sin(2 * math.pi * (t * speed - progress * 2)) * 0.6
            sub_wave = math.sin(2 * math.pi * (t * speed * 1.7 - progress * 3.5)) * 0.25
            ripple = math.sin(2 * math.pi * (t * speed * 4.3 + progress * 8)) * 0.1
            total = (main_wave + sub_wave + ripple) * intensity
            val = 128 + total * 127
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_ocean_wave_v25", name="海浪翻涌", category="电机/形象",
        description="多层海浪叠加（主浪+副浪+涟漪），模拟真实海浪运动",
        target_channels=["motor_x", "motor_y"], icon="🌊",
        tags=["motor", "ocean", "wave", "figurative"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_y",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "浪速", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("intensity", "强度", "float", 1.0, 0.1, 2.0, 0.1,
                tooltip="控制效果的整体强度。值为 0 时无效果，最大值对应最强烈的输出"),
        ],
    ), _motor_ocean_wave)

    # ═══════════════════════════════════════════════════════════════════
    #  D. 电机高级动作 — 高级复杂系列
    # ═══════════════════════════════════════════════════════════════════

    # --- D1: 弹性回弹 ---
    def _motor_elastic_bounce(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        bounces = max(1, int(params.get("bounces", 4)))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            delay = seq_i / max(1, n) * 0.5
            local_t = max(0.0, (t * speed - delay) % 2.0)
            if local_t < 1.0:
                val = _elastic(local_t, bounces) * 255
            else:
                val = _elastic(2.0 - local_t, bounces) * 255
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_elastic_bounce_v25", name="弹性回弹", category="电机/高级",
        description="弹性回弹效果，模拟弹簧或橡皮筋的物理运动",
        target_channels=["motor_x", "motor_y"], icon="⤴",
        tags=["motor", "elastic", "bounce", "physics"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("bounces", "弹跳次数", "int", 4, 1, 10, 1,
                tooltip="控制弹跳次数。值越大弹跳越多次才停止"),
        ],
    ), _motor_elastic_bounce)

    # --- D2: 随机漫步 ---
    def _motor_random_walk(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        smooth = float(params.get("smooth", 0.8))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            val = _perlin_like(t * speed, seed=seq_i * 7.3) * 255
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_random_walk_v25", name="随机漫步", category="电机/高级",
        description="伪随机漫步运动，每个设备有独立的柔和随机轨迹",
        target_channels=["motor_x", "motor_y"], icon="?~",
        tags=["motor", "random", "walk", "organic"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("smooth", "平滑度", "float", 0.8, 0.1, 1.0, 0.05,
                tooltip="控制平滑滤波的程度。值越大输出变化越平滑但响应越迟钝"),
        ],
    ), _motor_random_walk)

    # --- D3: 呼吸叠加 ---
    def _motor_breath_layer(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        layers = max(1, int(params.get("layers", 3)))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            val = 0.0
            for layer in range(layers):
                freq = 0.3 * (layer + 1)
                phase_shift = layer * 0.7 + seq_i * 0.2
                val += _ease_in_out((_sine(t * speed * freq + phase_shift) + 1.0) / 2.0)
            val = val / layers
            ef.values[orig_i][ch] = _clamp(val * 255)

    reg(EffectPreset(
        id="motor_breath_layer_v25", name="呼吸叠加", category="电机/高级",
        description="多层不同频率的呼吸效果叠加，产生有机的起伏运动",
        target_channels=["motor_x", "motor_y"], icon="≈B",
        tags=["motor", "breath", "layer", "organic"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("layers", "层数", "int", 3, 1, 6, 1,
                tooltip="控制叠加层数。值越大层数越多，效果越丰富厚重"),
        ],
    ), _motor_breath_layer)

    # --- D4: 渐变序列（级联延迟）---
    def _motor_cascade_sequence(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        delay_per = float(params.get("delay", 0.15))
        waveform = params.get("waveform", "sine")
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        wave_fn = {"sine": _sine, "triangle": _triangle, "sawtooth": _sawtooth}.get(waveform, _sine)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            local_t = t * speed - seq_i * delay_per
            val = wave_fn(local_t) * 255
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_cascade_sequence_v25", name="渐变序列", category="电机/高级",
        description="设备按序列依次延迟执行相同波形，形成级联瀑布效果",
        target_channels=["motor_x", "motor_y"], icon=">>",
        tags=["motor", "cascade", "sequence", "delay"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("delay", "延迟间隔", "float", 0.15, 0.01, 1.0, 0.01,
                tooltip="控制效果的延迟/错开时间。值越大效果启动越滞后"),
            ParamSpec("waveform", "波形", "choice", "sine",
                      choices=["sine", "triangle", "sawtooth"],
                tooltip="选择波形类型。正弦波平滑连续、方波突变、锯齿波渐升骤降、三角波对称升降"),
        ],
    ), _motor_cascade_sequence)

    # --- D5: 双轴利萨如图形 ---
    def _motor_lissajous(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        ratio_x = float(params.get("ratio_x", 3.0))
        ratio_y = float(params.get("ratio_y", 2.0))
        phase_diff = float(params.get("phase_diff", 0.5))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = seq_i / max(1, n) * math.pi * 2
            val_x = 128 + 127 * math.sin(ratio_x * (t * speed + offset))
            val_y = 128 + 127 * math.sin(ratio_y * (t * speed + offset) + phase_diff * math.pi)
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_lissajous_v25", name="利萨如图形", category="电机/高级",
        description="双轴利萨如图形运动，通过频率比产生各种封闭曲线",
        target_channels=["motor_x", "motor_y"], icon="∞",
        tags=["motor", "lissajous", "math", "complex"],
        params=[
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("ratio_x", "X频率比", "float", 3.0, 1.0, 8.0, 0.5,
                tooltip="控制 X 方向的比例系数。值越大 X 维度的缩放越明显"),
            ParamSpec("ratio_y", "Y频率比", "float", 2.0, 1.0, 8.0, 0.5,
                tooltip="控制 Y 方向的比例系数。值越大 Y 维度的缩放越明显"),
            ParamSpec("phase_diff", "相位差(π)", "float", 0.5, 0.0, 2.0, 0.1,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
        ],
    ), _motor_lissajous)

    # --- D6: 螺旋上升 ---
    def _motor_spiral_rise(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.6))
        turns = float(params.get("turns", 3.0))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            angle = turns * 2 * math.pi * progress + t * speed * 2 * math.pi
            radius = progress  # 半径随位置增大
            val_x = 128 + 127 * radius * math.cos(angle)
            val_y = 128 + 127 * radius * math.sin(angle)
            ef.values[orig_i]["motor_x"] = _clamp(val_x)
            ef.values[orig_i]["motor_y"] = _clamp(val_y)

    reg(EffectPreset(
        id="motor_spiral_rise_v25", name="螺旋上升", category="电机/高级",
        description="螺旋形上升运动，半径随位置增大",
        target_channels=["motor_x", "motor_y"], icon="🌀",
        tags=["motor", "spiral", "rise", "complex"],
        params=[
            ParamSpec("speed", "旋转速度", "float", 0.6, 0.1, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("turns", "圈数", "float", 3.0, 0.5, 10.0, 0.5,
                tooltip="控制旋转圈数。值越大旋转的总圈数越多"),
        ],
    ), _motor_spiral_rise)

    # --- D7: 钟摆阻尼 ---
    def _motor_pendulum_damped(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        damping = float(params.get("damping", 0.3))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            delay = seq_i * 0.1
            local_t = max(0.0, t * speed - delay)
            cycle_t = local_t % 4.0
            # 阻尼振荡
            amp = math.exp(-damping * cycle_t)
            val = 128 + 127 * amp * math.sin(2 * math.pi * cycle_t * 1.5)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_pendulum_damped_v25", name="钟摆阻尼", category="电机/高级",
        description="阻尼钟摆运动，振幅逐渐衰减后重新激发",
        target_channels=["motor_x", "motor_y"], icon="⏳",
        tags=["motor", "pendulum", "damped", "physics"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("damping", "阻尼系数", "float", 0.3, 0.05, 2.0, 0.05,
                tooltip="控制阻尼系数。值越大震动衰减越快，系统越稳定"),
        ],
    ), _motor_pendulum_damped)

    # --- D8: 群舞同步 ---
    def _motor_chorus_sync(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        pattern = params.get("pattern", "wave")
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            if pattern == "wave":
                val = _sine(t * speed - progress * 2) * 255
            elif pattern == "mirror":
                mirror_p = abs(progress - 0.5) * 2
                val = _sine(t * speed + mirror_p) * 255
            elif pattern == "alternate":
                direction = 1.0 if seq_i % 2 == 0 else -1.0
                val = 128 + direction * 127 * math.sin(2 * math.pi * t * speed)
            elif pattern == "cascade":
                val = _sine(t * speed - progress * 4) * 255
            else:
                val = _sine(t * speed) * 255
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="motor_chorus_sync_v25", name="群舞同步", category="电机/高级",
        description="多种群体同步运动模式：波浪/镜像/交替/级联",
        target_channels=["motor_x", "motor_y"], icon="👥",
        tags=["motor", "chorus", "sync", "group"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("pattern", "模式", "choice", "wave",
                      choices=["wave", "mirror", "alternate", "cascade"],
                tooltip="选择图案模板。不同图案决定矩阵灯阵的布局"),
        ],
    ), _motor_chorus_sync)

    # ═══════════════════════════════════════════════════════════════════
    #  E. 灯光/泵补充效果
    # ═══════════════════════════════════════════════════════════════════

    # --- E1: 呼吸灯（多种曲线）---
    def _light_breath_curves(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        curve = params.get("curve", "sine")
        ch = params.get("ch_type", "dimmer")
        delay_mode = params.get("delay_mode", "none")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if delay_mode == "cascade":
                local_t = t * speed - seq_i * 0.1
            elif delay_mode == "reverse":
                local_t = t * speed - (n - seq_i) * 0.1
            elif delay_mode == "center":
                mid = n // 2
                local_t = t * speed - abs(seq_i - mid) * 0.1
            else:
                local_t = t * speed
            if curve == "sine":
                val = _sine(local_t) * 255
            elif curve == "ease":
                val = _ease_in_out(_sine(local_t)) * 255
            elif curve == "exponential":
                raw = _sine(local_t)
                val = (math.exp(raw * 3) - 1) / (math.exp(3) - 1) * 255
            elif curve == "square_breath":
                raw = _sine(local_t)
                val = (raw ** 0.5) * 255
            else:
                val = _sine(local_t) * 255
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_breath_curves_v25", name="呼吸灯(多曲线)", category="灯光/呼吸",
        description="多种呼吸曲线+多种延迟模式组合",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="💨",
        tags=["light", "breath", "curve"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("curve", "曲线", "choice", "sine",
                      choices=["sine", "ease", "exponential", "square_breath"],
                tooltip="控制曲线的形状或缓动类型。不同曲线产生不同的变化节奏"),
            ParamSpec("delay_mode", "延迟模式", "choice", "none",
                      choices=["none", "cascade", "reverse", "center"],
                tooltip="选择延迟的应用模式。不同模式产生不同的时间错位效果"),
        ],
    ), _light_breath_curves)

    # --- E2: 泵压力脉冲（多种模式）---
    def _pump_pressure_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        pattern = params.get("pattern", "sync")
        attack = float(params.get("attack", 0.2))
        ch = "pump"
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if pattern == "sync":
                local_t = t * speed
            elif pattern == "cascade":
                local_t = t * speed - seq_i * 0.2
            elif pattern == "random":
                local_t = t * speed + (seq_i * 7.3 % 1.0)
            elif pattern == "alternate":
                local_t = t * speed + (0.5 if seq_i % 2 else 0.0)
            else:
                local_t = t * speed
            phase = local_t % 1.0
            if phase < attack:
                val = (phase / attack) * 255
            else:
                val = (1.0 - (phase - attack) / (1.0 - attack)) * 255
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_pressure_pulse_v25", name="压力脉冲(多模式)", category="泵/脉冲",
        description="多种脉冲触发模式：同步/级联/随机/交替",
        target_channels=["pump"], icon="⬆P",
        tags=["pump", "pressure", "pulse"],
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("pattern", "模式", "choice", "sync",
                      choices=["sync", "cascade", "random", "alternate"],
                tooltip="选择图案模板。不同图案决定矩阵灯阵的布局"),
            ParamSpec("attack", "上升时间比", "float", 0.2, 0.01, 0.9, 0.01,
                tooltip="控制效果起音/启动时间。值越小响应越迅速（适合打击感），值越大渐入越平滑"),
        ],
    ), _pump_pressure_pulse)
