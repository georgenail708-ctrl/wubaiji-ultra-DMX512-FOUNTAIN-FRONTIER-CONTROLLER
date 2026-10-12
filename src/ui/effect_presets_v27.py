"""
effect_presets_v27.py — 效果库超大扩充 v27
=============================================
重点扩充：
  A. 矩阵织物纹理效果（斜纹、格纹、菱形、人字纹、鱼骨纹、蜂巢纹等）
  B. 矩阵波形图案效果（正弦叠加、李萨如、驻波、干涉纹、调制波等）
  C. 矩阵数学图案效果（分形、莫尔纹、螺旋线、玫瑰线、棋盘变体等）
  D. 矩阵音频关联效果（频谱柱状、能量扩散、节拍闪光、音量映射等）
  E. 矩阵动态纹理效果（流动沙粒、液态金属、等离子体、细胞分裂等）
  F. 泵矩阵扩充（更多矩阵水型变体）
  G. 电机效果扩充变式
  H. 灯光效果扩充变式
  I. 泵效果扩充变式
"""
from __future__ import annotations
import math
import random
from typing import Dict, Any, List, TYPE_CHECKING
if TYPE_CHECKING:
    from .effect_engine import EffectEngine, EffectPreset, EffectFrame, ParamSpec


def _register_v27_presets(engine: "EffectEngine"):
    """注册 v27 效果库超大扩展预设"""
    from .effect_engine import EffectPreset, ParamSpec
    reg = engine._reg

    # ═══════════════════════════════════════════════════════════════════
    #  辅助工具
    # ═══════════════════════════════════════════════════════════════════
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

    def _sawtooth(t):
        return t % 1.0

    def _square(t, duty=0.5):
        return 1.0 if (t % 1.0) < duty else 0.0

    def _ease_in_out(t):
        t = t % 1.0
        return t * t * (3.0 - 2.0 * t)

    def _perlin_like(x, y=0.0, seed=0):
        """伪Perlin噪声（纯数学，无依赖）"""
        px = x * 3.7 + seed * 1.3
        py = y * 2.9 + seed * 0.7
        return (math.sin(px) * math.cos(py) +
                math.sin(px * 2.1 + 1.3) * math.cos(py * 1.7 + 0.9) * 0.5 +
                math.sin(px * 4.3 + 2.7) * math.cos(py * 3.1 + 1.5) * 0.25) / 1.75 * 0.5 + 0.5

    def _hash2d(ix, iy, seed=0):
        """2D整数哈希，返回0~1"""
        h = (ix * 1619 + iy * 31337 + seed * 6791) & 0xFFFFFF
        h = ((h >> 16) ^ h) * 0x45d9f3b & 0xFFFFFF
        return (h & 0xFFFF) / 65535.0

    def _dist(x1, y1, x2, y2):
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)

    def _get_grid(seq_i, cols):
        """根据序号和列数返回(row, col)"""
        return seq_i // cols, seq_i % cols

    # ═══════════════════════════════════════════════════════════════════
    #  A. 矩阵织物纹理效果
    # ═══════════════════════════════════════════════════════════════════

    # --- A1: 斜纹（Twill）---
#     def _matrix_twill(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         angle = float(params.get("angle", 45.0))
#         stripe_w = float(params.get("stripe_width", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         rad = math.radians(angle)
#         cos_a, sin_a = math.cos(rad), math.sin(rad)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 斜纹投影
#             proj = col * cos_a + row * sin_a
#             phase = (proj / stripe_w - t * speed) % 1.0
#             val = _triangle(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_twill_v27", name="矩阵斜纹", category="灯光/矩阵",
#         description="织物斜纹图案，可调角度和条纹宽度，随时间流动",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦╱",
#         tags=["light", "matrix", "twill", "fabric", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 5.0, 0.1),
#             ParamSpec("angle", "角度", "float", 45.0, 0.0, 90.0, 5.0),
#             ParamSpec("stripe_width", "条纹宽度", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_twill)

    # --- A2: 格纹（Plaid/Tartan）---
#     def _matrix_plaid(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.3))
#         h_freq = float(params.get("h_freq", 2.0))
#         v_freq = float(params.get("v_freq", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             h_wave = _sine_raw(col / max(1, cols) * h_freq - t * speed)
#             v_wave = _sine_raw(row / max(1, rows) * v_freq - t * speed * 0.7)
#             val = ((h_wave * v_wave) * 0.5 + 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_plaid_v27", name="矩阵格纹", category="灯光/矩阵",
#         description="苏格兰格纹图案，横纵波叠加形成格子效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦#",
#         tags=["light", "matrix", "plaid", "tartan", "fabric", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.3, 0.0, 3.0, 0.1),
#             ParamSpec("h_freq", "横向频率", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("v_freq", "纵向频率", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_plaid)

    # --- A3: 菱形纹（Diamond）---
#     def _matrix_diamond(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.4))
#         scale = float(params.get("scale", 3.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 菱形坐标变换
#             u = (col + row) / scale
#             v = (col - row) / scale
#             val = (_sine(u - t * speed) * _sine(v - t * speed * 0.8)) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_diamond_v27", name="矩阵菱形纹", category="灯光/矩阵",
#         description="菱形纹理图案，两方向正弦波叠加",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦◇",
#         tags=["light", "matrix", "diamond", "fabric", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.4, 0.0, 3.0, 0.1),
#             ParamSpec("scale", "缩放", "float", 3.0, 1.0, 10.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_diamond)

    # --- A4: 人字纹（Herringbone）---
#     def _matrix_herringbone(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         scale = float(params.get("scale", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 人字纹：偶数行和奇数行方向相反
#             if row % 2 == 0:
#                 proj = col + row * 0.5
#             else:
#                 proj = (cols - 1 - col) + row * 0.5
#             phase = (proj / scale - t * speed) % 1.0
#             val = _triangle(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_herringbone_v27", name="矩阵人字纹", category="灯光/矩阵",
#         description="人字纹织物图案，交替方向的斜纹",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⋀",
#         tags=["light", "matrix", "herringbone", "fabric", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 3.0, 0.1),
#             ParamSpec("scale", "缩放", "float", 2.0, 0.5, 6.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_herringbone)

    # --- A5: 蜂巢纹（Honeycomb）---
#     def _matrix_honeycomb(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.3))
#         scale = float(params.get("scale", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 六边形坐标近似
#             hx = col + (row % 2) * 0.5
#             hy = row * 0.866
            # 六边形距离近似
#             qx = (hx / scale) % 1.0 - 0.5
#             qy = (hy / scale) % 1.0 - 0.5
#             hex_dist = max(abs(qx), abs(qy * 1.155 + qx * 0.5), abs(qy * 1.155 - qx * 0.5))
#             phase = hex_dist - t * speed * 0.1
#             val = _sine(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_honeycomb_v27", name="矩阵蜂巢纹", category="灯光/矩阵",
#         description="蜂巢六边形纹理图案",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⬡",
#         tags=["light", "matrix", "honeycomb", "hexagon", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.3, 0.0, 3.0, 0.1),
#             ParamSpec("scale", "缩放", "float", 2.0, 0.5, 6.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_honeycomb)

    # --- A6: 鱼骨纹（Fishbone）---
#     def _matrix_fishbone(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         spine_w = float(params.get("spine_width", 0.5))
#         branch_angle = float(params.get("branch_angle", 45.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         rad = math.radians(branch_angle)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 主干：中间列
#             center_col = cols / 2.0
#             dist_to_spine = abs(col - center_col)
            # 分支：从主干向两侧延伸的斜线
#             branch_phase = (row + (col - center_col) * math.tan(rad)) / 2.0
#             spine_val = max(0.0, 1.0 - dist_to_spine / spine_w)
#             branch_val = _sine(branch_phase - t * speed) * max(0.0, 1.0 - dist_to_spine / 3.0)
#             val = max(spine_val, branch_val) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_fishbone_v27", name="矩阵鱼骨纹", category="灯光/矩阵",
#         description="鱼骨纹理图案，主干加分支结构",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🐟",
#         tags=["light", "matrix", "fishbone", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 3.0, 0.1),
#             ParamSpec("spine_width", "主干宽度", "float", 0.5, 0.1, 2.0, 0.1),
#             ParamSpec("branch_angle", "分支角度", "float", 45.0, 15.0, 75.0, 5.0),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_fishbone)

    # --- A7: 波点纹（Polka Dot）---
#     def _matrix_polka_dot(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.3))
#         dot_size = float(params.get("dot_size", 0.4))
#         spacing = float(params.get("spacing", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 波点中心
#             dot_col = round(col / spacing) * spacing
#             dot_row = round(row / spacing) * spacing
#             dist = _dist(col, row, dot_col + math.sin(t * speed) * 0.3,
#                          dot_row + math.cos(t * speed * 0.7) * 0.3)
#             val = max(0.0, 1.0 - dist / dot_size) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_polka_dot_v27", name="矩阵波点纹", category="灯光/矩阵",
#         description="波点纹理图案，圆点阵列随时间轻微摆动",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦●",
#         tags=["light", "matrix", "polka", "dot", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.3, 0.0, 3.0, 0.1),
#             ParamSpec("dot_size", "点大小", "float", 0.4, 0.1, 1.5, 0.1),
#             ParamSpec("spacing", "间距", "float", 2.0, 1.0, 5.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_polka_dot)

    # ═══════════════════════════════════════════════════════════════════
    #  B. 矩阵波形图案效果
    # ═══════════════════════════════════════════════════════════════════

    # --- B1: 正弦叠加波（Harmonic Sum）---
#     def _matrix_harmonic_sum(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         harmonics = max(1, int(params.get("harmonics", 3)))
#         direction = params.get("direction", "horizontal")
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             if direction == "horizontal":
#                 x = col / max(1, cols - 1)
#             elif direction == "vertical":
#                 x = row / max(1, rows - 1)
#             else:
#                 x = (col + row) / max(2, cols + rows - 2)
#             val = 0.0
#             for h in range(1, harmonics + 1):
#                 val += math.sin(2 * math.pi * (x * h - t * speed * h)) / h
#             val = (val / harmonics * 0.5 + 0.5)
#             ef.values[orig_i][ch] = _clamp(val * 255)
#     reg(EffectPreset(
#         id="matrix_harmonic_sum_v27", name="矩阵谐波叠加", category="灯光/矩阵",
#         description="多次谐波正弦叠加，形成复杂波形图案",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦∿",
#         tags=["light", "matrix", "harmonic", "wave", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 5.0, 0.1),
#             ParamSpec("harmonics", "谐波数", "int", 3, 1, 8, 1),
#             ParamSpec("direction", "方向", "choice", "horizontal",
#                       choices=["horizontal", "vertical", "diagonal"]),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_harmonic_sum)

    # --- B2: 李萨如图形（Lissajous）---
#     def _matrix_lissajous(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         freq_a = float(params.get("freq_a", 3.0))
#         freq_b = float(params.get("freq_b", 2.0))
#         phase_diff = float(params.get("phase_diff", 0.25))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 归一化坐标
#             cx = col / max(1, cols - 1) * 2.0 - 1.0
#             cy = row / max(1, rows - 1) * 2.0 - 1.0
            # 李萨如轨迹点
#             lx = math.sin(2 * math.pi * (freq_a * t * speed))
#             ly = math.sin(2 * math.pi * (freq_b * t * speed + phase_diff))
#             dist = _dist(cx, cy, lx, ly)
#             val = max(0.0, 1.0 - dist * 2.0) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_lissajous_v27", name="矩阵李萨如", category="灯光/矩阵",
#         description="李萨如图形轨迹在矩阵中运动",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦∞",
#         tags=["light", "matrix", "lissajous", "wave", "math"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.1, 3.0, 0.1),
#             ParamSpec("freq_a", "X频率", "float", 3.0, 1.0, 8.0, 0.5),
#             ParamSpec("freq_b", "Y频率", "float", 2.0, 1.0, 8.0, 0.5),
#             ParamSpec("phase_diff", "相位差", "float", 0.25, 0.0, 1.0, 0.05),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_lissajous)

    # --- B3: 驻波（Standing Wave）---
#     def _matrix_standing_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         freq = float(params.get("freq", 2.0))
#         direction = params.get("direction", "horizontal")
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             if direction == "horizontal":
#                 x = col / max(1, cols - 1)
#                 y = row / max(1, rows - 1)
#             else:
#                 x = row / max(1, rows - 1)
#                 y = col / max(1, cols - 1)
            # 驻波 = sin(kx) * cos(wt)
#             spatial = math.sin(2 * math.pi * x * freq)
#             temporal = math.cos(2 * math.pi * t * speed)
#             val = (spatial * temporal * 0.5 + 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_standing_wave_v27", name="矩阵驻波", category="灯光/矩阵",
#         description="驻波图案，空间正弦与时间余弦的乘积",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦〜",
#         tags=["light", "matrix", "standing_wave", "physics"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("freq", "空间频率", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("direction", "方向", "choice", "horizontal",
#                       choices=["horizontal", "vertical"]),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_standing_wave)

    # --- B4: 干涉纹（Interference）---
#     def _matrix_interference(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         freq1 = float(params.get("freq1", 2.0))
#         freq2 = float(params.get("freq2", 2.3))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
            # 两个波源的干涉
#             wave1 = math.sin(2 * math.pi * (cx * freq1 - t * speed))
#             wave2 = math.sin(2 * math.pi * (cy * freq2 - t * speed * 0.9))
#             val = ((wave1 + wave2) * 0.25 + 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_interference_v27", name="矩阵干涉纹", category="灯光/矩阵",
#         description="两列波干涉形成的莫尔纹效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦≋",
#         tags=["light", "matrix", "interference", "moire", "wave"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 3.0, 0.1),
#             ParamSpec("freq1", "波1频率", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("freq2", "波2频率", "float", 2.3, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_interference)

    # --- B5: 调制波（AM Modulation）---
#     def _matrix_am_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         carrier_freq = float(params.get("carrier_freq", 4.0))
#         mod_freq = float(params.get("mod_freq", 1.0))
#         direction = params.get("direction", "horizontal")
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             if direction == "horizontal":
#                 x = col / max(1, cols - 1)
#             else:
#                 x = row / max(1, rows - 1)
#             carrier = math.sin(2 * math.pi * (x * carrier_freq - t * speed))
#             modulator = (math.sin(2 * math.pi * (x * mod_freq - t * speed * 0.3)) + 1.0) / 2.0
#             val = (carrier * modulator * 0.5 + 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_am_wave_v27", name="矩阵调制波", category="灯光/矩阵",
#         description="幅度调制波，载波被低频包络调制",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦~",
#         tags=["light", "matrix", "am", "modulation", "wave"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("carrier_freq", "载波频率", "float", 4.0, 1.0, 12.0, 0.5),
#             ParamSpec("mod_freq", "调制频率", "float", 1.0, 0.1, 4.0, 0.1),
#             ParamSpec("direction", "方向", "choice", "horizontal",
#                       choices=["horizontal", "vertical"]),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_am_wave)

    # --- B6: 双向波叠加（Bidir Wave）---
#     def _matrix_bidir_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.8))
#         freq = float(params.get("freq", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
#             w1 = math.sin(2 * math.pi * (cx * freq - t * speed))
#             w2 = math.sin(2 * math.pi * (cy * freq - t * speed))
#             w3 = math.sin(2 * math.pi * ((cx + cy) * freq * 0.7 - t * speed * 1.3))
#             val = ((w1 + w2 + w3) / 3.0 * 0.5 + 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_bidir_wave_v27", name="矩阵双向波", category="灯光/矩阵",
#         description="横纵对角三方向波叠加，形成复杂图案",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⊕",
#         tags=["light", "matrix", "bidir", "wave", "complex"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1),
#             ParamSpec("freq", "频率", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_bidir_wave)

    # --- B7: 径向波（Radial Wave）---
#     def _matrix_radial_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         freq = float(params.get("freq", 3.0))
#         cx_offset = float(params.get("cx_offset", 0.5))
#         cy_offset = float(params.get("cy_offset", 0.5))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
#             r = _dist(cx, cy, cx_offset, cy_offset)
#             val = _sine(r * freq - t * speed) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_radial_wave_v27", name="矩阵径向波", category="灯光/矩阵",
#         description="从中心向外扩散的径向波纹",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦◎",
#         tags=["light", "matrix", "radial", "wave", "ripple"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("freq", "频率", "float", 3.0, 0.5, 10.0, 0.5),
#             ParamSpec("cx_offset", "中心X", "float", 0.5, 0.0, 1.0, 0.1),
#             ParamSpec("cy_offset", "中心Y", "float", 0.5, 0.0, 1.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_radial_wave)

    # --- B8: 双波源干涉（Dual Source）---
#     def _matrix_dual_source(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         freq = float(params.get("freq", 4.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
            # 两个波源
#             r1 = _dist(cx, cy, 0.25, 0.5)
#             r2 = _dist(cx, cy, 0.75, 0.5)
#             w1 = math.sin(2 * math.pi * (r1 * freq - t * speed))
#             w2 = math.sin(2 * math.pi * (r2 * freq - t * speed))
#             val = ((w1 + w2) * 0.25 + 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_dual_source_v27", name="矩阵双波源", category="灯光/矩阵",
#         description="两个波源的干涉图案，形成双缝衍射效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⊗",
#         tags=["light", "matrix", "dual", "source", "interference"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("freq", "频率", "float", 4.0, 1.0, 12.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_dual_source)

    # ═══════════════════════════════════════════════════════════════════
    #  C. 矩阵数学图案效果
    # ═══════════════════════════════════════════════════════════════════

    # --- C1: 莫尔纹（Moire）---
#     def _matrix_moire(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.1))
#         freq1 = float(params.get("freq1", 3.0))
#         angle_diff = float(params.get("angle_diff", 5.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         rad = math.radians(angle_diff)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
            # 两组略微不同角度的条纹
#             p1 = cx * freq1
#             p2 = (cx * math.cos(rad) + cy * math.sin(rad)) * freq1
#             w1 = _square(p1 - t * speed)
#             w2 = _square(p2 - t * speed * 1.02)
#             val = (w1 * w2) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_moire_v27", name="矩阵莫尔纹", category="灯光/矩阵",
#         description="两组略微旋转的条纹叠加形成莫尔纹",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦≡",
#         tags=["light", "matrix", "moire", "optical", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.1, 0.0, 1.0, 0.05),
#             ParamSpec("freq1", "条纹频率", "float", 3.0, 1.0, 8.0, 0.5),
#             ParamSpec("angle_diff", "角度差", "float", 5.0, 1.0, 30.0, 1.0),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_moire)

    # --- C2: 玫瑰线（Rose Curve）---
#     def _matrix_rose_curve(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.3))
#         petals = max(1, int(params.get("petals", 4)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1) * 2.0 - 1.0
#             cy = row / max(1, rows - 1) * 2.0 - 1.0
#             r = math.sqrt(cx * cx + cy * cy)
#             theta = math.atan2(cy, cx)
            # 玫瑰线 r = cos(k*theta)
#             rose_r = abs(math.cos(petals * (theta + t * speed * math.pi)))
#             val = max(0.0, 1.0 - abs(r - rose_r) * 3.0) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_rose_curve_v27", name="矩阵玫瑰线", category="灯光/矩阵",
#         description="旋转的玫瑰线图案，花瓣数可调",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🌹",
#         tags=["light", "matrix", "rose", "curve", "math"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.3, 0.0, 2.0, 0.1),
#             ParamSpec("petals", "花瓣数", "int", 4, 1, 8, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_rose_curve)

    # --- C3: 阿基米德螺旋（Archimedean Spiral）---
#     def _matrix_archimedean_spiral(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         turns = float(params.get("turns", 3.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1) * 2.0 - 1.0
#             cy = row / max(1, rows - 1) * 2.0 - 1.0
#             r = math.sqrt(cx * cx + cy * cy)
#             theta = math.atan2(cy, cx)
            # 阿基米德螺旋：r = a * theta
#             spiral_phase = (r * turns - theta / (2 * math.pi) - t * speed) % 1.0
#             val = _triangle(spiral_phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_archimedean_spiral_v27", name="矩阵阿基米德螺旋", category="灯光/矩阵",
#         description="旋转的阿基米德螺旋图案",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🌀",
#         tags=["light", "matrix", "spiral", "archimedean", "math"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 3.0, 0.1),
#             ParamSpec("turns", "圈数", "float", 3.0, 1.0, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_archimedean_spiral)

    # --- C4: 棋盘波动（Animated Checkerboard）---
#     def _matrix_animated_checker(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         size = max(1, int(params.get("size", 2)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             checker = ((row // size) + (col // size)) % 2
            # 棋盘格交替闪烁，带相位偏移
#             phase = t * speed + checker * 0.5
#             val = _sine(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_animated_checker_v27", name="矩阵棋盘波动", category="灯光/矩阵",
#         description="棋盘格交替波动，两色格子相位相反",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⬛",
#         tags=["light", "matrix", "checkerboard", "animated"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("size", "格子大小", "int", 2, 1, 6, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_animated_checker)

    # --- C5: 分形噪声（Fractal Noise）---
#     def _matrix_fractal_noise(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.2))
#         octaves = max(1, int(params.get("octaves", 3)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
#             val = 0.0
#             amp = 1.0
#             freq = 1.0
#             for o in range(octaves):
#                 val += _perlin_like(cx * freq + t * speed, cy * freq, o) * amp
#                 amp *= 0.5
#                 freq *= 2.0
#             val = val / (2.0 - 1.0 / (2 ** (octaves - 1)))
#             ef.values[orig_i][ch] = _clamp(_clampf(val) * 255)
#     reg(EffectPreset(
#         id="matrix_fractal_noise_v27", name="矩阵分形噪声", category="灯光/矩阵",
#         description="多倍频程叠加的分形噪声纹理",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⋯",
#         tags=["light", "matrix", "fractal", "noise", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.2, 0.0, 2.0, 0.05),
#             ParamSpec("octaves", "倍频程数", "int", 3, 1, 5, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_fractal_noise)

    # --- C6: 万花筒（Kaleidoscope）---
#     def _matrix_kaleidoscope(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.3))
#         segments = max(2, int(params.get("segments", 6)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         seg_angle = math.pi / segments
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1) * 2.0 - 1.0
#             cy = row / max(1, rows - 1) * 2.0 - 1.0
#             r = math.sqrt(cx * cx + cy * cy)
#             theta = math.atan2(cy, cx) + t * speed
            # 万花筒折叠
#             theta_folded = theta % (2 * seg_angle)
#             if theta_folded > seg_angle:
#                 theta_folded = 2 * seg_angle - theta_folded
#             val = _perlin_like(r * 3.0, theta_folded * 5.0) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_kaleidoscope_v27", name="矩阵万花筒", category="灯光/矩阵",
#         description="旋转的万花筒图案，可调对称段数",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦✦",
#         tags=["light", "matrix", "kaleidoscope", "symmetry"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.3, 0.0, 2.0, 0.1),
#             ParamSpec("segments", "对称段数", "int", 6, 2, 12, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_kaleidoscope)

    # ═══════════════════════════════════════════════════════════════════
    #  D. 矩阵音频关联效果
    # ═══════════════════════════════════════════════════════════════════

    # --- D1: 频谱柱状图（Spectrum Bar）---
#     def _matrix_spectrum_bar(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         cols = max(1, int(params.get("cols", 4)))
#         ch = params.get("ch_type", "dimmer")
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 模拟频谱：每列对应不同频率，用正弦模拟能量
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 每列的"频率能量"：用不同频率的正弦模拟
#             freq_energy = abs(math.sin(2 * math.pi * (col / max(1, cols) * 2.0 + t * speed * (1.0 + col * 0.3))))
            # 柱状图：从底部向上填充
#             bar_height = freq_energy * rows
#             if (rows - 1 - row) < bar_height:
#                 val = 255
#             else:
#                 val = 0
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_spectrum_bar_v27", name="矩阵频谱柱", category="灯光/矩阵",
#         description="频谱柱状图效果，每列对应不同频率能量",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦📊",
#         tags=["light", "matrix", "spectrum", "bar", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_spectrum_bar)

    # --- D2: 能量扩散（Energy Diffusion）---
#     def _matrix_energy_diffusion(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.5))
#         decay = float(params.get("decay", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 模拟节拍能量从中心扩散
#         beat_phase = (t * speed) % 1.0
#         energy = math.exp(-beat_phase * decay)
#         cx_center = cols / 2.0
#         cy_center = rows / 2.0
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             dist = _dist(col, row, cx_center, cy_center)
#             max_dist = _dist(0, 0, cx_center, cy_center)
            # 能量从中心向外扩散，随距离衰减
#             wave_r = beat_phase * max_dist * 2.0
#             wave_dist = abs(dist - wave_r)
#             val = energy * max(0.0, 1.0 - wave_dist / 1.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_energy_diffusion_v27", name="矩阵能量扩散", category="灯光/矩阵",
#         description="节拍能量从中心向外扩散的波纹效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦💥",
#         tags=["light", "matrix", "energy", "diffusion", "beat", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.5, 0.5, 8.0, 0.5),
#             ParamSpec("decay", "衰减", "float", 2.0, 0.5, 6.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_energy_diffusion)

    # --- D3: 节拍闪光（Beat Flash）---
#     def _matrix_beat_flash(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 2.0))
#         pattern = params.get("pattern", "all")
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         beat_phase = (t * speed) % 1.0
#         flash_val = math.exp(-beat_phase * 8.0) * 255
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             if pattern == "all":
#                 val = flash_val
#             elif pattern == "checker":
#                 if (row + col) % 2 == 0:
#                     val = flash_val
#                 else:
#                     val = max(0, 255 - flash_val)
#             elif pattern == "rows":
#                 if row % 2 == 0:
#                     val = flash_val
#                 else:
#                     val = max(0, 255 - flash_val)
#             elif pattern == "cols":
#                 if col % 2 == 0:
#                     val = flash_val
#                 else:
#                     val = max(0, 255 - flash_val)
#             else:
#                 val = flash_val
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_beat_flash_v27", name="矩阵节拍闪光", category="灯光/矩阵",
#         description="跟随节拍的矩阵闪光，支持多种图案",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⚡",
#         tags=["light", "matrix", "beat", "flash", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("pattern", "图案", "choice", "all",
#                       choices=["all", "checker", "rows", "cols"]),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_beat_flash)

    # --- D4: 音量映射（Volume Map）---
#     def _matrix_volume_map(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         mode = params.get("mode", "radial")
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 模拟音量：用正弦波模拟
#         volume = abs(math.sin(2 * math.pi * t * speed * 0.5))
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
#             if mode == "radial":
#                 r = _dist(cx, cy, 0.5, 0.5)
#                 val = max(0.0, volume - r) / max(0.01, volume) * 255
#             elif mode == "horizontal":
#                 val = max(0.0, volume - cx) / max(0.01, volume) * 255
#             elif mode == "vertical":
#                 val = max(0.0, volume - cy) / max(0.01, volume) * 255
#             elif mode == "diagonal":
#                 d = (cx + cy) / 2.0
#                 val = max(0.0, volume - d) / max(0.01, volume) * 255
#             else:
#                 val = volume * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_volume_map_v27", name="矩阵音量映射", category="灯光/矩阵",
#         description="音量大小映射到矩阵亮度分布",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🔊",
#         tags=["light", "matrix", "volume", "map", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("mode", "模式", "choice", "radial",
#                       choices=["radial", "horizontal", "vertical", "diagonal"]),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_volume_map)

    # --- D5: 节拍涟漪（Beat Ripple）---
#     def _matrix_beat_ripple(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 2.0))
#         ripple_count = max(1, int(params.get("ripple_count", 3)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
#             r = _dist(cx, cy, 0.5, 0.5)
#             val = 0.0
#             for i in range(ripple_count):
#                 phase_offset = i / ripple_count
#                 beat_t = (t * speed + phase_offset) % 1.0
#                 ring_r = beat_t * 0.8
#                 ring_dist = abs(r - ring_r)
#                 ring_amp = math.exp(-beat_t * 3.0)
#                 val += ring_amp * max(0.0, 1.0 - ring_dist * 8.0)
#             ef.values[orig_i][ch] = _clamp(_clampf(val) * 255)
#     reg(EffectPreset(
#         id="matrix_beat_ripple_v27", name="矩阵节拍涟漪", category="灯光/矩阵",
#         description="节拍触发的多圈涟漪效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦〰",
#         tags=["light", "matrix", "beat", "ripple", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("ripple_count", "涟漪数", "int", 3, 1, 6, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_beat_ripple)

    # --- D6: 频率扫描（Frequency Scan）---
#     def _matrix_freq_scan(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         scan_width = float(params.get("scan_width", 0.2))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         scan_pos = (t * speed) % 1.0
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 扫描线从左到右
#             cx = col / max(1, cols - 1)
#             dist_to_scan = abs(cx - scan_pos)
            # 每行有不同的"频率能量"
#             freq_energy = abs(math.sin(2 * math.pi * (row / max(1, rows) * 3.0 + t * 0.3)))
#             val = max(0.0, 1.0 - dist_to_scan / scan_width) * freq_energy * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_freq_scan_v27", name="矩阵频率扫描", category="灯光/矩阵",
#         description="扫描线扫过时显示各行频率能量",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦📡",
#         tags=["light", "matrix", "frequency", "scan", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("scan_width", "扫描宽度", "float", 0.2, 0.05, 0.5, 0.05),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_freq_scan)

    # ═══════════════════════════════════════════════════════════════════
    #  E. 矩阵动态纹理效果
    # ═══════════════════════════════════════════════════════════════════

    # --- E1: 等离子体（Plasma）---
#     def _matrix_plasma(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         scale = float(params.get("scale", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1) * scale
#             cy = row / max(1, rows - 1) * scale
            # 经典等离子体：多个正弦叠加
#             v1 = math.sin(cx + t * speed)
#             v2 = math.sin(cy + t * speed * 0.7)
#             v3 = math.sin((cx + cy) * 0.7 + t * speed * 1.3)
#             v4 = math.sin(math.sqrt(cx * cx + cy * cy) * 2.0 + t * speed)
#             v = (v1 + v2 + v3 + v4) / 4.0
#             val = (v * 0.5 + 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_plasma_v27", name="矩阵等离子体", category="灯光/矩阵",
#         description="经典等离子体效果，多正弦叠加形成有机流动纹理",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🌊",
#         tags=["light", "matrix", "plasma", "organic", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 3.0, 0.1),
#             ParamSpec("scale", "缩放", "float", 2.0, 0.5, 6.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_plasma)

    # --- E2: 细胞分裂（Cell Division）---
#     def _matrix_cell_division(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.3))
#         num_cells = max(2, int(params.get("num_cells", 5)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 动态细胞中心
#         cells = []
#         for i in range(num_cells):
#             angle = 2 * math.pi * i / num_cells + t * speed
#             r = 0.3 + 0.1 * math.sin(t * speed * 0.5 + i)
#             cx = 0.5 + r * math.cos(angle)
#             cy = 0.5 + r * math.sin(angle)
#             cells.append((cx, cy))
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             px = col / max(1, cols - 1)
#             py = row / max(1, rows - 1)
            # Voronoi距离：找最近细胞
#             min_d = float('inf')
#             for cx, cy in cells:
#                 d = _dist(px, py, cx, cy)
#                 if d < min_d:
#                     min_d = d
#             val = max(0.0, 1.0 - min_d * 3.0) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_cell_division_v27", name="矩阵细胞分裂", category="灯光/矩阵",
#         description="Voronoi细胞图案，细胞中心旋转运动",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🔬",
#         tags=["light", "matrix", "cell", "voronoi", "organic"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.3, 0.0, 2.0, 0.1),
#             ParamSpec("num_cells", "细胞数", "int", 5, 2, 12, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_cell_division)

    # --- E3: 液态金属（Liquid Metal）---
#     def _matrix_liquid_metal(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.4))
#         viscosity = float(params.get("viscosity", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
            # 液态金属：多层扭曲的正弦
#             twist_x = cx + math.sin(cy * viscosity + t * speed) * 0.3
#             twist_y = cy + math.cos(cx * viscosity + t * speed * 0.8) * 0.3
#             v1 = _perlin_like(twist_x * 3.0 + t * speed, twist_y * 3.0)
#             v2 = _perlin_like(twist_y * 2.0 - t * speed * 0.5, twist_x * 2.0)
#             val = (v1 * 0.6 + v2 * 0.4) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_liquid_metal_v27", name="矩阵液态金属", category="灯光/矩阵",
#         description="液态金属流动效果，扭曲的有机纹理",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦💧",
#         tags=["light", "matrix", "liquid", "metal", "organic"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.4, 0.0, 2.0, 0.1),
#             ParamSpec("viscosity", "粘度", "float", 2.0, 0.5, 6.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_liquid_metal)

    # --- E4: 流沙（Sand Flow）---
#     def _matrix_sand_flow(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.6))
#         direction = params.get("direction", "down")
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
#             if direction == "down":
#                 flow = cy + t * speed
#             elif direction == "up":
#                 flow = (1.0 - cy) + t * speed
#             elif direction == "right":
#                 flow = cx + t * speed
#             else:
#                 flow = (1.0 - cx) + t * speed
            # 沙粒：带随机扰动的流动
#             noise = _perlin_like(cx * 5.0, cy * 5.0 + t * speed * 0.3)
#             val = _sine(flow * 2.0 + noise * 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_sand_flow_v27", name="矩阵流沙", category="灯光/矩阵",
#         description="流沙效果，带随机扰动的定向流动",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⏳",
#         tags=["light", "matrix", "sand", "flow", "organic"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.6, 0.0, 3.0, 0.1),
#             ParamSpec("direction", "方向", "choice", "down",
#                       choices=["down", "up", "right", "left"]),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_sand_flow)

    # --- E5: 涡流（Vortex）---
#     def _matrix_vortex(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         tightness = float(params.get("tightness", 3.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1) * 2.0 - 1.0
#             cy = row / max(1, rows - 1) * 2.0 - 1.0
#             r = math.sqrt(cx * cx + cy * cy)
#             theta = math.atan2(cy, cx)
            # 涡流：角度随半径扭曲
#             twisted_theta = theta + r * tightness - t * speed * 2 * math.pi
#             val = _sine(twisted_theta / (2 * math.pi) + r) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_vortex_v27", name="矩阵涡流", category="灯光/矩阵",
#         description="旋转涡流效果，角度随半径扭曲",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🌪",
#         tags=["light", "matrix", "vortex", "rotation", "organic"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 3.0, 0.1),
#             ParamSpec("tightness", "紧密度", "float", 3.0, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_vortex)

    # --- E6: 火焰（Flame）---
#     def _matrix_flame(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         turbulence = float(params.get("turbulence", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = 1.0 - row / max(1, rows - 1)  # 从底部向上
            # 火焰：底部亮，顶部暗，带横向扰动
#             noise = _perlin_like(cx * turbulence + t * speed * 0.3, cy * 2.0 + t * speed)
#             height_factor = max(0.0, 1.0 - cy * 1.5)
#             val = (noise * height_factor) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_flame_v27", name="矩阵火焰", category="灯光/矩阵",
#         description="火焰效果，底部亮顶部暗，带湍流扰动",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🔥",
#         tags=["light", "matrix", "flame", "fire", "organic"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("turbulence", "湍流", "float", 2.0, 0.5, 6.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_flame)

    # --- E7: 水波纹（Water Ripple）---
#     def _matrix_water_ripple(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         sources = max(1, int(params.get("sources", 2)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 多个波源
#         source_positions = []
#         for i in range(sources):
#             angle = 2 * math.pi * i / sources + t * speed * 0.1
#             sx = 0.5 + 0.3 * math.cos(angle)
#             sy = 0.5 + 0.3 * math.sin(angle)
#             source_positions.append((sx, sy))
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             cx = col / max(1, cols - 1)
#             cy = row / max(1, rows - 1)
#             val = 0.0
#             for sx, sy in source_positions:
#                 r = _dist(cx, cy, sx, sy)
#                 val += math.sin(2 * math.pi * (r * 5.0 - t * speed)) * math.exp(-r * 2.0)
#             val = (val / sources * 0.5 + 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_water_ripple_v27", name="矩阵水波纹", category="灯光/矩阵",
#         description="多波源水波纹干涉效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦💦",
#         tags=["light", "matrix", "water", "ripple", "organic"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("sources", "波源数", "int", 2, 1, 5, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_water_ripple)

    # --- E8: 星空（Starfield）---
#     def _matrix_starfield(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         density = float(params.get("density", 0.3))
#         twinkle = float(params.get("twinkle", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 每个位置有固定的"星星概率"
#             star_prob = _hash2d(col, row, 42)
#             if star_prob < density:
                # 星星闪烁
#                 twinkle_phase = _hash2d(col, row, 7) + t * twinkle
#                 brightness = _sine(twinkle_phase)
#                 val = brightness * 255
#             else:
#                 val = 0
#             ef.values[orig_i][ch] = _clamp(val)
#     reg(EffectPreset(
#         id="matrix_starfield_v27", name="矩阵星空", category="灯光/矩阵",
#         description="随机分布的闪烁星星效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦✨",
#         tags=["light", "matrix", "star", "twinkle", "random"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("density", "密度", "float", 0.3, 0.05, 0.8, 0.05),
#             ParamSpec("twinkle", "闪烁速度", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_starfield)

    # ═══════════════════════════════════════════════════════════════════
    #  F. 泵矩阵扩充
    # ═══════════════════════════════════════════════════════════════════

    # --- F1: 泵矩阵等离子体 ---
    def _pump_matrix_plasma(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        scale = float(params.get("scale", 2.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            cx = col / max(1, cols - 1) * scale
            cy = row / max(1, rows - 1) * scale
            v1 = math.sin(cx + t * speed)
            v2 = math.sin(cy + t * speed * 0.7)
            v3 = math.sin(math.sqrt(cx * cx + cy * cy) * 2.0 + t * speed)
            v = (v1 + v2 + v3) / 3.0
            val = (v * 0.5 + 0.5) * 255
            ef.values[orig_i]["pump"] = _clamp(val)
    reg(EffectPreset(
        id="pump_matrix_plasma_v27", name="泵矩阵等离子体", category="泵/矩阵",
        description="等离子体水型效果，有机流动图案",
        target_channels=["pump"], icon="▦🌊P",
        tags=["pump", "matrix", "plasma", "organic"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.0, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("scale", "缩放", "float", 2.0, 0.5, 6.0, 0.5,
                tooltip="控制缩放比例。值越大视觉元素放大越多"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_plasma)

    # --- F2: 泵矩阵干涉纹 ---
    def _pump_matrix_interference(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        freq1 = float(params.get("freq1", 2.0))
        freq2 = float(params.get("freq2", 2.5))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            cx = col / max(1, cols - 1)
            cy = row / max(1, rows - 1)
            w1 = math.sin(2 * math.pi * (cx * freq1 - t * speed))
            w2 = math.sin(2 * math.pi * (cy * freq2 - t * speed * 0.9))
            val = ((w1 + w2) * 0.25 + 0.5) * 255
            ef.values[orig_i]["pump"] = _clamp(val)
    reg(EffectPreset(
        id="pump_matrix_interference_v27", name="泵矩阵干涉", category="泵/矩阵",
        description="两列波干涉形成的矩阵水型图案",
        target_channels=["pump"], icon="▦≋P",
        tags=["pump", "matrix", "interference", "wave"],
        params=[
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("freq1", "波1频率", "float", 2.0, 0.5, 6.0, 0.5,
                tooltip="控制第一层效果的波动频率。值越高波动越急促"),
            ParamSpec("freq2", "波2频率", "float", 2.5, 0.5, 6.0, 0.5,
                tooltip="控制第二层效果的波动频率。值越高波动越急促"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_interference)

    # --- F3: 泵矩阵涡流 ---
    def _pump_matrix_vortex(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        tightness = float(params.get("tightness", 3.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            cx = col / max(1, cols - 1) * 2.0 - 1.0
            cy = row / max(1, rows - 1) * 2.0 - 1.0
            r = math.sqrt(cx * cx + cy * cy)
            theta = math.atan2(cy, cx)
            twisted_theta = theta + r * tightness - t * speed * 2 * math.pi
            val = _sine(twisted_theta / (2 * math.pi) + r) * 255
            ef.values[orig_i]["pump"] = _clamp(val)
    reg(EffectPreset(
        id="pump_matrix_vortex_v27", name="泵矩阵涡流", category="泵/矩阵",
        description="旋转涡流水型效果",
        target_channels=["pump"], icon="▦🌪P",
        tags=["pump", "matrix", "vortex", "rotation"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.0, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("tightness", "紧密度", "float", 3.0, 0.5, 8.0, 0.5,
                tooltip="控制紧致度。值越大元素排列越紧凑，间隙越小"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_vortex)

    # --- F4: 泵矩阵棋盘波动 ---
    def _pump_matrix_checker_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        size = max(1, int(params.get("size", 2)))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            checker = ((row // size) + (col // size)) % 2
            phase = t * speed + checker * 0.5
            val = _sine(phase) * 255
            ef.values[orig_i]["pump"] = _clamp(val)
    reg(EffectPreset(
        id="pump_matrix_checker_wave_v27", name="泵矩阵棋盘波动", category="泵/矩阵",
        description="棋盘格交替波动水型",
        target_channels=["pump"], icon="▦⬛P",
        tags=["pump", "matrix", "checkerboard", "wave"],
        params=[
            ParamSpec("speed", "速度", "float", 1.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("size", "格子大小", "int", 2, 1, 6, 1,
                tooltip="控制效果元素的尺寸大小。值越大单个元素的视觉面积越大"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_checker_wave)

    # --- F5: 泵矩阵斜纹 ---
    def _pump_matrix_twill(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        angle = float(params.get("angle", 45.0))
        stripe_w = float(params.get("stripe_width", 2.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        rad = math.radians(angle)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            proj = col * cos_a + row * sin_a
            phase = (proj / stripe_w - t * speed) % 1.0
            val = _triangle(phase) * 255
            ef.values[orig_i]["pump"] = _clamp(val)
    reg(EffectPreset(
        id="pump_matrix_twill_v27", name="泵矩阵斜纹", category="泵/矩阵",
        description="斜纹水型图案，可调角度",
        target_channels=["pump"], icon="▦╱P",
        tags=["pump", "matrix", "twill", "diagonal"],
        params=[
            ParamSpec("speed", "速度", "float", 0.8, 0.0, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("angle", "角度", "float", 45.0, 0.0, 90.0, 5.0,
                tooltip="控制角度位置或展开角度。值越大角度偏转越明显"),
            ParamSpec("stripe_width", "条纹宽度", "float", 2.0, 0.5, 6.0, 0.5,
                tooltip="控制条纹的宽度。值越大单个条纹越宽"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_twill)

    # --- F6: 泵矩阵双波源 ---
    def _pump_matrix_dual_source(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.2))
        freq = float(params.get("freq", 4.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            cx = col / max(1, cols - 1)
            cy = row / max(1, rows - 1)
            r1 = _dist(cx, cy, 0.25, 0.5)
            r2 = _dist(cx, cy, 0.75, 0.5)
            w1 = math.sin(2 * math.pi * (r1 * freq - t * speed))
            w2 = math.sin(2 * math.pi * (r2 * freq - t * speed))
            val = ((w1 + w2) * 0.25 + 0.5) * 255
            ef.values[orig_i]["pump"] = _clamp(val)
    reg(EffectPreset(
        id="pump_matrix_dual_source_v27", name="泵矩阵双波源", category="泵/矩阵",
        description="两个波源干涉的矩阵水型",
        target_channels=["pump"], icon="▦⊗P",
        tags=["pump", "matrix", "dual", "source", "interference"],
        params=[
            ParamSpec("speed", "速度", "float", 1.2, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("freq", "频率", "float", 4.0, 1.0, 10.0, 0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_dual_source)

    # --- F7: 泵矩阵径向波 ---
    def _pump_matrix_radial_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        freq = float(params.get("freq", 4.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            cx = col / max(1, cols - 1)
            cy = row / max(1, rows - 1)
            r = _dist(cx, cy, 0.5, 0.5)
            val = _sine(r * freq - t * speed) * 255
            ef.values[orig_i]["pump"] = _clamp(val)
    reg(EffectPreset(
        id="pump_matrix_radial_wave_v27", name="泵矩阵径向波", category="泵/矩阵",
        description="从中心向外扩散的径向波纹水型",
        target_channels=["pump"], icon="▦◎P",
        tags=["pump", "matrix", "radial", "wave"],
        params=[
            ParamSpec("speed", "速度", "float", 1.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("freq", "频率", "float", 4.0, 1.0, 10.0, 0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_radial_wave)

    # --- F8: 泵矩阵菱形 ---
    def _pump_matrix_diamond(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        scale = float(params.get("scale", 3.0))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            u = (col + row) / scale
            v = (col - row) / scale
            val = (_sine(u - t * speed) * _sine(v - t * speed * 0.8)) * 255
            ef.values[orig_i]["pump"] = _clamp(val)
    reg(EffectPreset(
        id="pump_matrix_diamond_v27", name="泵矩阵菱形", category="泵/矩阵",
        description="菱形水型图案",
        target_channels=["pump"], icon="▦◇P",
        tags=["pump", "matrix", "diamond", "pattern"],
        params=[
            ParamSpec("speed", "速度", "float", 0.8, 0.0, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("scale", "缩放", "float", 3.0, 1.0, 8.0, 0.5,
                tooltip="控制缩放比例。值越大视觉元素放大越多"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_diamond)

    # ═══════════════════════════════════════════════════════════════════
    #  G. 电机效果扩充变式
    # ═══════════════════════════════════════════════════════════════════

    # --- G1: 蠕动龙摆（Caterpillar Dragon）---
    def _motor_caterpillar_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        segments = max(2, int(params.get("segments", 4)))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seg = seq_i // max(1, n // segments)
            pos_in_seg = (seq_i % max(1, n // segments)) / max(1, n // segments)
            # 蠕动：每段有延迟，且段内有弯曲
            seg_phase = t * speed - seg * 0.4
            bend = math.sin(2 * math.pi * (seg_phase + pos_in_seg * 0.5))
            val = 128 + amplitude * 127 * bend
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_caterpillar_dragon_v27", name="蠕动龙摆", category="电机/龙摆",
        description="分段蠕动的龙摆，模拟毛毛虫爬行",
        target_channels=["motor_x", "motor_y"], icon="🐛",
        tags=["motor", "dragon", "caterpillar", "segment"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("segments", "段数", "int", 4, 2, 8, 1,
                tooltip="控制设备分割的段数。值越大分段越细，变化越丰富"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.5, 0.1,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_caterpillar_dragon)

    # --- G2: 鱼摆（Fish Swing）---
    def _motor_fish_swing(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        tail_factor = float(params.get("tail_factor", 2.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            # 鱼摆：头部幅度小，尾部幅度大
            local_amp = amplitude * (0.3 + progress * tail_factor * 0.7)
            phase = t * speed - progress * 0.8
            val = 128 + local_amp * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_fish_swing_v27", name="鱼摆", category="电机/龙摆",
        description="模拟鱼游动，头部幅度小尾部幅度大",
        target_channels=["motor_x", "motor_y"], icon="🐟",
        tags=["motor", "dragon", "fish", "swing"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.5, 0.1,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("tail_factor", "尾部放大", "float", 2.0, 1.0, 4.0, 0.5,
                tooltip="控制拖尾的放大倍率。值越大拖尾被叠加放大的程度越高"),
        ],
    ), _motor_fish_swing)

    # --- G3: 蛇形波（Serpentine）---
    def _motor_serpentine(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        wavelength = float(params.get("wavelength", 4.0))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 蛇形：正弦波沿设备序列传播
            phase = t * speed - seq_i / wavelength
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_serpentine_v27", name="蛇形波", category="电机/龙摆",
        description="正弦波沿设备序列传播，形成蛇形效果",
        target_channels=["motor_x", "motor_y"], icon="🐍",
        tags=["motor", "dragon", "serpentine", "wave"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("wavelength", "波长（设备数）", "float", 4.0, 1.0, 12.0, 0.5,
                tooltip="控制波形在设备序列上的空间波长。值越大一个完整波覆盖的设备越多"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.5, 0.1,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_serpentine)

    # --- G4: 旋转波（Rotation Wave）---
    def _motor_rotation_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        wave_speed = float(params.get("wave_speed", 0.5))
        ch_x = "motor_x"
        ch_y = "motor_y"
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            # X轴旋转，Y轴有波浪传播
            x_phase = t * speed
            y_phase = t * speed - progress * wave_speed * 4
            x_val = 128 + 127 * math.sin(2 * math.pi * x_phase)
            y_val = 128 + 127 * math.cos(2 * math.pi * y_phase)
            ef.values[orig_i][ch_x] = _clamp(x_val)
            ef.values[orig_i][ch_y] = _clamp(y_val)
    reg(EffectPreset(
        id="motor_rotation_wave_v27", name="旋转波", category="电机/高级",
        description="X轴同步旋转，Y轴波浪传播，形成螺旋效果",
        target_channels=["motor_x", "motor_y"], icon="🌀",
        tags=["motor", "rotation", "wave", "spiral"],
        params=[
            ParamSpec("speed", "旋转速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("wave_speed", "波浪速度", "float", 0.5, 0.0, 3.0, 0.1,
                tooltip="控制波形在空间中的传播速度。值越大波在设备间传递越快"),
        ],
    ), _motor_rotation_wave)

    # --- G5: 弹跳（Bounce）---
    def _motor_bounce(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.5))
        elasticity = float(params.get("elasticity", 3.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            delay = seq_i / max(1, n) * 0.5
            phase = (t * speed - delay) % 1.0
            # 弹跳：快速下落，弹性回弹
            if phase < 0.5:
                val = phase / 0.5
            else:
                bounce_phase = (phase - 0.5) / 0.5
                val = abs(math.sin(bounce_phase * math.pi * elasticity)) * (1.0 - bounce_phase)
            ef.values[orig_i][ch] = _clamp(val * 255)
    reg(EffectPreset(
        id="motor_bounce_v27", name="弹跳", category="电机/高级",
        description="模拟弹跳效果，带弹性衰减",
        target_channels=["motor_x", "motor_y"], icon="🏀",
        tags=["motor", "bounce", "elastic"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("elasticity", "弹性", "float", 3.0, 1.0, 8.0, 0.5,
                tooltip="控制弹性系数。值越大形变后恢复原状的弹跳越明显"),
        ],
    ), _motor_bounce)

    # --- G6: 梳状波（Comb Wave）---
    def _motor_comb_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        teeth = max(2, int(params.get("teeth", 4)))
        duty = float(params.get("duty", 0.5))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            progress = seq_i / max(1, n - 1)
            # 梳状：每隔一定间距有一个脉冲
            comb_phase = (progress * teeth - t * speed) % 1.0
            val = 255 if comb_phase < duty else 0
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_comb_wave_v27", name="梳状波", category="电机/高级",
        description="梳状脉冲波，可调齿数和占空比",
        target_channels=["motor_x", "motor_y"], icon="⚙",
        tags=["motor", "comb", "pulse", "wave"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("teeth", "齿数", "int", 4, 2, 12, 1,
                tooltip="控制齿状突起的数量。值越大齿越密集"),
            ParamSpec("duty", "占空比", "float", 0.5, 0.1, 0.9, 0.1,
                tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"),
        ],
    ), _motor_comb_wave)

    # --- G7: 双龙对摆（Dual Dragon Opposite）---
    def _motor_dual_dragon_opposite(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        half = n // 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < half:
                # 前半段：正向龙摆
                progress = seq_i / max(1, half - 1)
                phase = t * speed - progress * 2.0
            else:
                # 后半段：反向龙摆（从后往前）
                progress = (n - 1 - seq_i) / max(1, half - 1)
                phase = t * speed - progress * 2.0 + 0.5  # 半周期相位差
            val = 128 + amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_dual_dragon_opposite_v27", name="双龙对摆", category="电机/龙摆",
        description="前后两段龙摆方向相反，形成对称效果",
        target_channels=["motor_x", "motor_y"], icon="🐉🐉",
        tags=["motor", "dragon", "dual", "opposite", "symmetric"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.5, 0.1,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_dual_dragon_opposite)

    # --- G8: 五五交叉（5-5 Cross）---
    def _motor_cross_five(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 1.0))
        ch = params.get("ch_type", "motor_x")
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            group = seq_i // 5
            pos_in_group = seq_i % 5
            direction = 1.0 if pos_in_group < 3 else -1.0  # 3正2反
            phase = t * speed + group * 0.25
            val = 128 + direction * amplitude * 127 * math.sin(2 * math.pi * phase)
            ef.values[orig_i][ch] = _clamp(val)
    reg(EffectPreset(
        id="motor_cross_five_v27", name="五五交叉",
        category="电机/交叉",
        description="每 5 台为一组，组内前 3 台正相、后 2 台反相，呈现 3-2 交叉摆动",
        target_channels=["motor_x", "motor_y"], icon="⚡",
        tags=["motor", "cross", "five", "3-2"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "motor_x",
                      choices=["motor_x", "motor_y", "motor_z"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 1.0, 0.1, 1.5, 0.1,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
        ],
    ), _motor_cross_five)

