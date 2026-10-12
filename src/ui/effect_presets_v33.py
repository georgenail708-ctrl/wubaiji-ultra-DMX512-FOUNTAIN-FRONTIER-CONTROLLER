"""
effect_presets_v33.py — 普通效果极大量变式扩充 v33
====================================================
本扩展只新增普通效果的多种变式，刻意不包含 matrix / 矩阵类效果。
覆盖灯光亮度、灯光颜色、泵、阀门和电机常用编曲动作，避免偏向高级效果。
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .effect_engine import EffectEngine


def _register_v33_presets(engine: "EffectEngine"):
    """注册 v33 普通效果变式。"""
    from .effect_engine import EffectPreset, ParamSpec

    reg = engine._reg

    def _clamp(v, lo=0, hi=255):
        return max(lo, min(hi, int(round(v))))

    def _clampf(v, lo=0.0, hi=1.0):
        return max(lo, min(hi, float(v)))

    def _triangle(x):
        return 1.0 - abs(2.0 * (x % 1.0) - 1.0)

    def _smooth(x):
        x = _clampf(x)
        return x * x * (3.0 - 2.0 * x)

    def _hash01(seed):
        h = (int(seed) * 1103515245 + 12345) & 0x7fffffff
        h ^= (h >> 11)
        h = (h * 2654435761) & 0xffffffff
        return (h & 0xffff) / 65535.0

    def _wave(mode: str, phase: float, width: float, softness: float, seed: int = 0):
        x = phase % 1.0
        width = _clampf(width, 0.01, 0.99)
        softness = _clampf(softness, 0.0, 1.0)
        if mode == "sine":
            v = (math.sin(2.0 * math.pi * x) + 1.0) * 0.5
        elif mode == "triangle":
            v = _triangle(x)
        elif mode == "saw_up":
            v = x
        elif mode == "saw_down":
            v = 1.0 - x
        elif mode == "pulse":
            v = 1.0 if x < width else 0.0
        elif mode == "soft_pulse":
            v = max(0.0, 1.0 - abs(x - 0.5) / max(0.01, width * 0.5))
            v = _smooth(v)
        elif mode == "bounce":
            v = _smooth(_triangle(x))
        elif mode == "step4":
            v = int(x * 4.0) / 3.0
        elif mode == "step8":
            v = int(x * 8.0) / 7.0
        elif mode == "spark":
            v = 1.0 if _hash01(seed + int(x * 32.0)) > (1.0 - width) else 0.0
        elif mode == "flutter":
            v = 0.5 + 0.5 * math.sin(2.0 * math.pi * x)
            v *= 0.65 + 0.35 * math.sin(2.0 * math.pi * x * 7.0 + seed)
        else:
            v = x
        if softness > 0:
            v = v * (1.0 - softness) + _smooth(v) * softness
        return _clampf(v)

    # ────────────────────────────────────────────────────────────────
    # 普通强度类：灯光、泵、阀门、电机共用一套安全波形计算。
    # ────────────────────────────────────────────────────────────────
    intensity_specs = [
        # preset_id, 名称, 分类, 通道类别, 目标通道, 波形, 默认速度, 默认宽度, 默认分组, 默认相位错位, 图标
        ("light_plain_breath", "普通呼吸", "灯光/呼吸", "dimmer", ["dimmer", "R", "G", "B", "W"], "sine", 1.0, 0.5, 1, 0.00, "~"),
        ("light_plain_slow_breath", "慢速呼吸", "灯光/呼吸", "dimmer", ["dimmer", "R", "G", "B", "W"], "sine", 0.35, 0.5, 1, 0.00, "~"),
        ("light_plain_fast_breath", "快速呼吸", "灯光/呼吸", "dimmer", ["dimmer", "R", "G", "B", "W"], "sine", 2.4, 0.5, 1, 0.00, "~"),
        ("light_plain_triangle", "三角明暗", "灯光/亮度", "dimmer", ["dimmer", "R", "G", "B", "W"], "triangle", 1.2, 0.5, 1, 0.00, "/\\"),
        ("light_plain_soft_pulse", "柔和脉冲", "灯光/闪烁", "dimmer", ["dimmer", "R", "G", "B", "W"], "soft_pulse", 1.6, 0.32, 1, 0.00, "o"),
        ("light_plain_narrow_pulse", "窄脉冲", "灯光/闪烁", "dimmer", ["dimmer", "R", "G", "B", "W"], "pulse", 2.0, 0.12, 1, 0.00, "|"),
        ("light_plain_wide_pulse", "宽脉冲", "灯光/闪烁", "dimmer", ["dimmer", "R", "G", "B", "W"], "pulse", 1.4, 0.62, 1, 0.00, "▮"),
        ("light_plain_step4", "四级阶梯", "灯光/亮度", "dimmer", ["dimmer", "R", "G", "B", "W"], "step4", 0.9, 0.5, 1, 0.00, "1234"),
        ("light_plain_step8", "八级阶梯", "灯光/亮度", "dimmer", ["dimmer", "R", "G", "B", "W"], "step8", 1.1, 0.5, 1, 0.00, "123"),
        ("light_plain_saw_up", "线性渐亮", "灯光/亮度", "dimmer", ["dimmer", "R", "G", "B", "W"], "saw_up", 0.8, 0.5, 1, 0.00, "/"),
        ("light_plain_saw_down", "线性渐暗", "灯光/亮度", "dimmer", ["dimmer", "R", "G", "B", "W"], "saw_down", 0.8, 0.5, 1, 0.00, "\\"),
        ("light_plain_chase_soft", "柔和跑灯", "灯光/跑灯", "dimmer", ["dimmer", "R", "G", "B", "W"], "soft_pulse", 1.6, 0.22, 1, 0.08, ">"),
        ("light_plain_chase_wide", "宽带跑灯", "灯光/跑灯", "dimmer", ["dimmer", "R", "G", "B", "W"], "soft_pulse", 1.0, 0.45, 1, 0.10, ">>"),
        ("light_plain_chase_reverse", "反向跑灯", "灯光/跑灯", "dimmer", ["dimmer", "R", "G", "B", "W"], "soft_pulse", -1.2, 0.26, 1, 0.09, "<"),
        ("light_plain_group2", "双组明暗", "灯光/开合", "dimmer", ["dimmer", "R", "G", "B", "W"], "pulse", 1.2, 0.5, 2, 0.50, "2"),
        ("light_plain_group3", "三组明暗", "灯光/开合", "dimmer", ["dimmer", "R", "G", "B", "W"], "pulse", 1.1, 0.5, 3, 0.33, "3"),
        ("light_plain_group4", "四组明暗", "灯光/开合", "dimmer", ["dimmer", "R", "G", "B", "W"], "pulse", 1.0, 0.5, 4, 0.25, "4"),
        ("light_plain_flutter", "轻微闪动", "灯光/闪烁", "dimmer", ["dimmer", "R", "G", "B", "W"], "flutter", 2.0, 0.4, 1, 0.03, "*"),
        ("light_plain_sparkle", "普通星闪", "灯光/闪烁", "dimmer", ["dimmer", "R", "G", "B", "W"], "spark", 3.0, 0.15, 1, 0.04, "*"),
        ("light_plain_center_out", "中间向外", "灯光/跑灯", "dimmer", ["dimmer", "R", "G", "B", "W"], "soft_pulse", 1.1, 0.24, -2, 0.10, "<>"),

        ("pump_plain_chase", "普通跑泉", "泵/跑泉", "level", ["level", "pump"], "soft_pulse", 1.4, 0.22, 1, 0.08, ">"),
        ("pump_plain_slow_chase", "慢速跑泉", "泵/跑泉", "level", ["level", "pump"], "soft_pulse", 0.6, 0.28, 1, 0.08, ">"),
        ("pump_plain_fast_chase", "快速跑泉", "泵/跑泉", "level", ["level", "pump"], "soft_pulse", 2.8, 0.18, 1, 0.08, ">>"),
        ("pump_plain_reverse_chase", "反向跑泉", "泵/跑泉", "level", ["level", "pump"], "soft_pulse", -1.5, 0.22, 1, 0.08, "<"),
        ("pump_plain_breath", "喷泉呼吸", "泵/渐变", "level", ["level", "pump"], "sine", 0.9, 0.5, 1, 0.00, "~"),
        ("pump_plain_triangle", "喷泉三角波", "泵/波浪", "level", ["level", "pump"], "triangle", 1.1, 0.5, 1, 0.04, "/\\"),
        ("pump_plain_wave_soft", "柔和水波", "泵/波浪", "level", ["level", "pump"], "sine", 1.3, 0.5, 1, 0.08, "~~"),
        ("pump_plain_wave_wide", "宽幅水波", "泵/波浪", "level", ["level", "pump"], "sine", 0.8, 0.5, 1, 0.13, "~~~"),
        ("pump_plain_pulse", "普通喷泉脉冲", "泵/脉冲", "level", ["level", "pump"], "pulse", 1.8, 0.28, 1, 0.00, "|"),
        ("pump_plain_short_pulse", "短促喷泉脉冲", "泵/脉冲", "level", ["level", "pump"], "pulse", 2.2, 0.12, 1, 0.00, "|"),
        ("pump_plain_long_pulse", "长喷泉脉冲", "泵/脉冲", "level", ["level", "pump"], "pulse", 1.0, 0.62, 1, 0.00, "▮"),
        ("pump_plain_group2", "双组喷泉", "泵/跑泉", "level", ["level", "pump"], "pulse", 1.1, 0.45, 2, 0.50, "2"),
        ("pump_plain_group3", "三组喷泉", "泵/跑泉", "level", ["level", "pump"], "pulse", 1.1, 0.40, 3, 0.33, "3"),
        ("pump_plain_group4", "四组喷泉", "泵/跑泉", "level", ["level", "pump"], "pulse", 1.1, 0.35, 4, 0.25, "4"),
        ("pump_plain_step4", "四级水位", "泵/渐变", "level", ["level", "pump"], "step4", 0.7, 0.5, 1, 0.00, "1234"),
        ("pump_plain_step8", "八级水位", "泵/渐变", "level", ["level", "pump"], "step8", 0.8, 0.5, 1, 0.00, "123"),
        ("pump_plain_center_out", "中心向外喷泉", "泵/跑泉", "level", ["level", "pump"], "soft_pulse", 1.0, 0.25, -2, 0.08, "<>"),
        ("pump_plain_edge_in", "两侧向内喷泉", "泵/跑泉", "level", ["level", "pump"], "soft_pulse", -1.0, 0.25, -2, 0.08, "><"),
        ("pump_plain_spark", "随机小喷", "泵/脉冲", "level", ["level", "pump"], "spark", 2.5, 0.12, 1, 0.04, "*"),
        ("pump_plain_flutter", "水面抖动", "泵/波浪", "level", ["level", "pump"], "flutter", 1.8, 0.4, 1, 0.04, "~"),

        ("valve_plain_open_close", "普通开合", "阀门/开合", "valve", ["valve", "level", "pump"], "pulse", 1.0, 0.5, 1, 0.00, "[]"),
        ("valve_plain_slow_open_close", "慢速开合", "阀门/开合", "valve", ["valve", "level", "pump"], "pulse", 0.45, 0.5, 1, 0.00, "[]"),
        ("valve_plain_fast_open_close", "快速开合", "阀门/开合", "valve", ["valve", "level", "pump"], "pulse", 2.2, 0.5, 1, 0.00, "[]"),
        ("valve_plain_short_click", "短开阀", "阀门/脉冲", "valve", ["valve", "level", "pump"], "pulse", 1.8, 0.12, 1, 0.00, "|"),
        ("valve_plain_long_hold", "长开阀", "阀门/脉冲", "valve", ["valve", "level", "pump"], "pulse", 0.8, 0.75, 1, 0.00, "▮"),
        ("valve_plain_chase", "阀门跑动", "阀门/跑泉", "valve", ["valve", "level", "pump"], "soft_pulse", 1.4, 0.18, 1, 0.08, ">"),
        ("valve_plain_reverse_chase", "反向阀门跑动", "阀门/跑泉", "valve", ["valve", "level", "pump"], "soft_pulse", -1.2, 0.18, 1, 0.08, "<"),
        ("valve_plain_group2", "双组阀门", "阀门/开合", "valve", ["valve", "level", "pump"], "pulse", 1.0, 0.5, 2, 0.50, "2"),
        ("valve_plain_group3", "三组阀门", "阀门/开合", "valve", ["valve", "level", "pump"], "pulse", 1.0, 0.45, 3, 0.33, "3"),
        ("valve_plain_group4", "四组阀门", "阀门/开合", "valve", ["valve", "level", "pump"], "pulse", 1.0, 0.40, 4, 0.25, "4"),
        ("valve_plain_wave", "阀门波浪", "阀门/渐变", "valve", ["valve", "level", "pump"], "sine", 1.0, 0.5, 1, 0.08, "~"),
        ("valve_plain_step", "阀门阶梯", "阀门/渐变", "valve", ["valve", "level", "pump"], "step4", 0.8, 0.5, 1, 0.00, "1234"),

        ("motor_plain_dragon", "普通龙摆", "电机/龙摆", "speed", ["speed", "pan", "tilt"], "sine", 0.8, 0.5, 1, 0.08, "~"),
        ("motor_plain_slow_dragon", "慢速龙摆", "电机/龙摆", "speed", ["speed", "pan", "tilt"], "sine", 0.35, 0.5, 1, 0.08, "~"),
        ("motor_plain_fast_dragon", "快速龙摆", "电机/龙摆", "speed", ["speed", "pan", "tilt"], "sine", 1.6, 0.5, 1, 0.08, "~~"),
        ("motor_plain_cross", "普通交叉", "电机/交叉", "speed", ["speed", "pan", "tilt"], "triangle", 0.9, 0.5, 2, 0.50, "X"),
        ("motor_plain_open_close", "电机开合", "电机/开合", "speed", ["speed", "pan", "tilt"], "pulse", 0.8, 0.5, 2, 0.50, "[]"),
        ("motor_plain_wave", "电机波形", "电机/波形", "speed", ["speed", "pan", "tilt"], "sine", 1.0, 0.5, 1, 0.10, "~"),
        ("motor_plain_step", "电机阶梯", "电机/波形", "speed", ["speed", "pan", "tilt"], "step4", 0.7, 0.5, 1, 0.05, "1234"),
        ("motor_plain_chase", "电机顺序启动", "电机/龙摆", "speed", ["speed", "pan", "tilt"], "soft_pulse", 1.0, 0.24, 1, 0.08, ">"),
        ("motor_plain_reverse", "电机反向启动", "电机/龙摆", "speed", ["speed", "pan", "tilt"], "soft_pulse", -1.0, 0.24, 1, 0.08, "<"),
        ("motor_plain_group3", "三组电机", "电机/交叉", "speed", ["speed", "pan", "tilt"], "pulse", 0.9, 0.45, 3, 0.33, "3"),
        ("motor_plain_group4", "四组电机", "电机/交叉", "speed", ["speed", "pan", "tilt"], "pulse", 0.9, 0.4, 4, 0.25, "4"),
        ("motor_plain_flutter", "电机轻微抖动", "电机/波形", "speed", ["speed", "pan", "tilt"], "flutter", 1.4, 0.4, 1, 0.05, "~"),
    ]

    def _make_intensity_fn(mode: str, default_ch: str, def_speed: float, def_width: float, def_groups: int, def_phase: float):
        def _fn(ef, t, sorted_fx, fdefs, params, fps):
            ch = params.get("ch_type", default_ch)
            speed = float(params.get("speed", def_speed))
            width = float(params.get("width", def_width))
            lo = int(params.get("lo", 0))
            hi = int(params.get("hi", 255))
            phase_step = float(params.get("phase_step", def_phase))
            groups = int(params.get("groups", abs(def_groups) if def_groups else 1))
            softness = float(params.get("softness", 0.5))
            n = max(1, len(sorted_fx))
            center_mode = def_groups < 0 or bool(params.get("center_mode", False))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                if center_mode:
                    idx_norm = abs((seq_i / max(1, n - 1)) - 0.5) * 2.0
                    spatial = idx_norm
                elif groups > 1:
                    spatial = (seq_i % groups) / float(groups)
                else:
                    spatial = seq_i * phase_step
                seed = seq_i * 97 + int(t * abs(speed) * 37)
                v = _wave(mode, t * speed + spatial, width, softness, seed)
                ef.values[orig_i][ch] = _clamp(lo + v * (hi - lo))
        return _fn

    for pid, name, category, ch, targets, mode, speed, width, groups, phase, icon in intensity_specs:
        reg(EffectPreset(
            id=f"{pid}_v33", name=name, category=category,
            description=f"普通常用变式：{name}，适合快速编曲和一键随机配置。",
            target_channels=list(targets), icon=icon,
            tags=["ordinary", "normal", "v33", category.split('/')[0]],
            params=[
                ParamSpec("ch_type", "通道类别", "choice", ch, choices=list(targets),
                    tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
                ParamSpec("speed", "速度", "float", float(speed), -8.0, 8.0, 0.1,
                    tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                ParamSpec("width", "宽度/占空", "float", float(width), 0.02, 0.95, 0.01,
                    tooltip="控制效果在设备序列上的作用宽度。值越大覆盖的设备越多，视觉带越宽"),
                ParamSpec("phase_step", "相位错位", "float", float(phase), -0.50, 0.50, 0.01,
                    tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
                ParamSpec("groups", "分组数", "int", max(1, abs(int(groups))), 1, 16, 1,
                    tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
                ParamSpec("softness", "柔和度", "float", 0.5, 0.0, 1.0, 0.05,
                    tooltip="控制柔化程度。值越大边缘或过渡越柔和"),
                ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                    tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
                ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                    tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ],
        ), _make_intensity_fn(mode, ch, speed, width, groups, phase))

    # ────────────────────────────────────────────────────────────────
    # 普通颜色类：不依赖高级图案，只做常见颜色变式。
    # ────────────────────────────────────────────────────────────────
    palettes = [
        ("warm", "暖色轮换", [(255, 70, 20), (255, 140, 40), (255, 210, 100), (255, 90, 30)]),
        ("cool", "冷色轮换", [(30, 90, 255), (40, 180, 255), (80, 255, 210), (120, 120, 255)]),
        ("red_blue", "红蓝交替", [(255, 0, 20), (0, 80, 255), (255, 40, 80), (0, 160, 255)]),
        ("green_cyan", "绿青交替", [(0, 255, 80), (0, 220, 255), (80, 255, 160), (0, 150, 180)]),
        ("pastel", "柔彩轮换", [(255, 120, 180), (120, 180, 255), (180, 255, 160), (255, 220, 120)]),
        ("amber_white", "琥珀白轮换", [(255, 120, 20), (255, 220, 160), (255, 255, 255), (255, 160, 60)]),
        ("rgb_basic", "红绿蓝基础轮换", [(255, 0, 0), (0, 255, 0), (0, 80, 255), (255, 255, 255)]),
        ("pink_purple", "粉紫轮换", [(255, 40, 140), (180, 40, 255), (100, 80, 255), (255, 120, 220)]),
        ("sunset", "日落普通色", [(255, 60, 10), (255, 120, 40), (180, 40, 120), (60, 40, 160)]),
        ("aqua", "水蓝普通色", [(0, 80, 255), (0, 180, 255), (0, 255, 220), (120, 255, 255)]),
    ]
    color_modes = [
        ("step", "分段", "step4", 0.8, 0.00),
        ("chase", "顺跑", "step4", 1.2, 0.08),
        ("soft", "柔变", "sine", 0.7, 0.05),
        ("breath", "呼吸", "sine", 1.0, 0.00),
        ("reverse", "反跑", "step4", -1.0, 0.08),
        ("spark", "星跳", "spark", 2.0, 0.05),
    ]

    def _interp_color(pal, x):
        x = x % 1.0
        scaled = x * len(pal)
        i0 = int(scaled) % len(pal)
        i1 = (i0 + 1) % len(pal)
        f = scaled - int(scaled)
        return tuple(_clamp(pal[i0][c] * (1.0 - f) + pal[i1][c] * f) for c in range(3))

    def _make_color_fn(palette, mode):
        def _fn(ef, t, sorted_fx, fdefs, params, fps):
            speed = float(params.get("speed", 1.0))
            phase_step = float(params.get("phase_step", 0.06))
            brightness = float(params.get("brightness", 1.0))
            smooth_mix = bool(params.get("smooth_mix", mode in ("sine", "soft_pulse")))
            for seq_i, (orig_i, fi) in enumerate(sorted_fx):
                phase = t * speed + seq_i * phase_step
                if smooth_mix:
                    r, g, b = _interp_color(palette, phase)
                else:
                    idx = int((phase % 1.0) * len(palette)) % len(palette)
                    r, g, b = palette[idx]
                if mode == "spark" and _wave("spark", phase, 0.18, 0.0, seq_i) <= 0.0:
                    r = g = b = 0
                elif mode in ("sine", "soft_pulse"):
                    amp = 0.35 + 0.65 * _wave(mode, phase, 0.45, 0.5, seq_i)
                    r, g, b = r * amp, g * amp, b * amp
                ef.values[orig_i]["R"] = _clamp(r * brightness)
                ef.values[orig_i]["G"] = _clamp(g * brightness)
                ef.values[orig_i]["B"] = _clamp(b * brightness)
        return _fn

    for pal_id, pal_name, pal in palettes:
        for mode_id, mode_name, mode, speed, phase in color_modes:
            pid = f"light_color_{pal_id}_{mode_id}_v33"
            reg(EffectPreset(
                id=pid, name=f"{pal_name}{mode_name}", category="灯光/颜色",
                description=f"普通颜色变式：{pal_name}的{mode_name}效果。",
                target_channels=["R", "G", "B"], icon="RGB",
                tags=["ordinary", "normal", "color", "v33"],
                params=[
                    ParamSpec("speed", "速度", "float", float(speed), -8.0, 8.0, 0.1,
                        tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
                    ParamSpec("phase_step", "相位错位", "float", float(phase), -0.50, 0.50, 0.01,
                        tooltip="控制相位的递进步长。值越大每台设备的相位跳跃越大"),
                    ParamSpec("brightness", "亮度", "float", 1.0, 0.0, 1.0, 0.05,
                        tooltip="控制亮度明暗。值越低画面越暗，值越高画面越亮"),
                    ParamSpec("smooth_mix", "平滑混色", "bool", mode in ("sine", "soft_pulse"),
                        tooltip="控制混合平滑度。值越大不同信号源之间的过渡越自然"),
                ],
            ), _make_color_fn(tuple(pal), mode))
