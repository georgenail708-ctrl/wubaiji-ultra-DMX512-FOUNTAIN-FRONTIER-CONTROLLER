"""
effect_presets_v28.py — 效果库超大扩充 v28
=============================================
重点扩充：
  A. 矩阵规律性图案（更多织物纹理、波形图案、数学图案变体）
  B. 矩阵音频关联效果（节拍驱动、频谱驱动、能量驱动变体）
  C. 泵矩阵大量扩充（纹理水型、波形水型）
  D. 灯光跑灯/闪烁/呼吸/颜色大量变式
  E. 泵跑泉/波浪/渐变大量变式
  F. 电机更多变式（延时阵列、形象、波形）
  G. 阀门效果扩充
"""
from __future__ import annotations
import math
import random
from typing import Dict, Any, List, TYPE_CHECKING
if TYPE_CHECKING:
    from .effect_engine import EffectEngine, EffectPreset, EffectFrame, ParamSpec


def _register_v28_presets(engine: "EffectEngine"):
    """注册 v28 效果库超大扩展预设"""
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

    def _get_grid(seq_i, cols):
        return seq_i // cols, seq_i % cols

    def _dist(x1, y1, x2, y2):
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)

    def _noise2d(x, y, t):
        """简单伪噪声"""
        return (math.sin(x * 1.7 + t * 1.1) * math.cos(y * 2.3 - t * 0.7)
                + math.sin(x * 3.1 - y * 1.9 + t * 0.5)) / 2.0

    def _perlin_like(x, y, t, freq=1.0):
        """类Perlin噪声"""
        v = (math.sin(x * freq * 2.3 + t) * 0.5
             + math.sin(y * freq * 1.7 - t * 0.8) * 0.3
             + math.sin((x + y) * freq * 1.1 + t * 0.6) * 0.2)
        return (v + 1.0) / 2.0

    # ═══════════════════════════════════════════════════════════════════
    #  A. 矩阵规律性图案扩充
    # ═══════════════════════════════════════════════════════════════════

    # --- A1: 矩阵锯齿纹 ---
#     def _matrix_zigzag(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         freq = float(params.get("freq", 3.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 锯齿：奇数行反向
#             if row % 2 == 0:
#                 x = col / max(1, cols - 1)
#             else:
#                 x = 1.0 - col / max(1, cols - 1)
#             phase = x * freq - t * speed
#             val = _triangle(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_zigzag_v28", name="矩阵锯齿纹", category="灯光/矩阵",
#         description="锯齿形流动纹理，奇偶行方向交替，形成Z字形图案",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦Z",
#         tags=["light", "matrix", "zigzag", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 5.0, 0.1),
#             ParamSpec("freq", "频率", "float", 3.0, 1.0, 10.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_zigzag)

    # --- A2: 矩阵波点扩散 ---
#     def _matrix_dot_expand(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         num_dots = max(1, int(params.get("num_dots", 3)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             px = col / max(1, cols - 1) if cols > 1 else 0.5
#             py = row / max(1, rows - 1) if rows > 1 else 0.5
#             total = 0.0
#             for i in range(num_dots):
#                 cx = (i + 0.5) / num_dots
#                 cy = 0.5 + 0.3 * math.sin(2 * math.pi * i / num_dots + t * speed * 0.3)
#                 r = (t * speed * 0.5 + i / num_dots) % 1.0
#                 d = _dist(px, py, cx, cy)
#                 ring = max(0.0, 1.0 - abs(d - r) * 8.0)
#                 total += ring
#             ef.values[orig_i][ch] = _clamp(min(1.0, total) * 255)
# 
#     reg(EffectPreset(
#         id="matrix_dot_expand_v28", name="矩阵波点扩散", category="灯光/矩阵",
#         description="多个圆形波点从各自中心向外扩散，形成涟漪叠加效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦●",
#         tags=["light", "matrix", "dot", "expand", "ripple"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("num_dots", "波点数", "int", 3, 1, 8, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_dot_expand)

    # --- A3: 矩阵网格脉冲 ---
#     def _matrix_grid_pulse(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         h_freq = float(params.get("h_freq", 3.0))
#         v_freq = float(params.get("v_freq", 3.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             h = _sine_raw(col / max(1, cols - 1) * h_freq - t * speed)
#             v = _sine_raw(row / max(1, rows - 1) * v_freq - t * speed * 0.8)
            # 网格交叉点高亮
#             val = ((h * h + v * v) / 2.0) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_grid_pulse_v28", name="矩阵网格脉冲", category="灯光/矩阵",
#         description="水平和垂直正弦波叠加，在交叉点形成高亮网格脉冲",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦#",
#         tags=["light", "matrix", "grid", "pulse"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.0, 5.0, 0.1),
#             ParamSpec("h_freq", "横向频率", "float", 3.0, 1.0, 10.0, 0.5),
#             ParamSpec("v_freq", "纵向频率", "float", 3.0, 1.0, 10.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_grid_pulse)

    # --- A4: 矩阵双螺旋 ---
#     def _matrix_double_spiral(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.4))
#         arms = max(2, int(params.get("arms", 2)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         cx = (cols - 1) / 2.0
#         cy = (rows - 1) / 2.0
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             dx = col - cx
#             dy = row - cy
#             angle = math.atan2(dy, dx)
#             r = _dist(col, row, cx, cy)
            # 螺旋：角度 - 半径*系数 - 时间 = 相位
#             for arm in range(arms):
#                 arm_angle = angle - r * 0.8 - t * speed * 2 * math.pi + arm * 2 * math.pi / arms
#                 val = _sine(arm_angle / (2 * math.pi))
#                 ef.values[orig_i][ch] = _clamp(max(ef.values[orig_i].get(ch, 0), val * 255))
# 
#     reg(EffectPreset(
#         id="matrix_double_spiral_v28", name="矩阵双螺旋", category="灯光/矩阵",
#         description="多臂螺旋图案旋转，类似银河旋臂效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦🌀",
#         tags=["light", "matrix", "spiral", "galaxy"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.4, 0.0, 3.0, 0.1),
#             ParamSpec("arms", "臂数", "int", 2, 1, 6, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_double_spiral)

    # --- A5: 矩阵同心方形 ---
#     def _matrix_concentric_square(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         cx = (cols - 1) / 2.0
#         cy = (rows - 1) / 2.0
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 切比雪夫距离（L∞范数）形成方形等高线
#             dist = max(abs(col - cx), abs(row - cy))
#             phase = dist - t * speed
#             val = _sine(phase * 0.5) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_concentric_square_v28", name="矩阵同心方形", category="灯光/矩阵",
#         description="同心方形向外扩散，使用切比雪夫距离形成方形等高线",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦□",
#         tags=["light", "matrix", "concentric", "square"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_concentric_square)

    # --- A6: 矩阵十字波 ---
#     def _matrix_cross_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         freq = float(params.get("freq", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         cx = (cols - 1) / 2.0
#         cy = (rows - 1) / 2.0
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 曼哈顿距离（L1范数）形成菱形/十字等高线
#             dist = abs(col - cx) + abs(row - cy)
#             phase = dist * freq * 0.3 - t * speed
#             val = _sine(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_cross_wave_v28", name="矩阵十字波", category="灯光/矩阵",
#         description="以曼哈顿距离为基础的十字/菱形波纹，从中心向四角扩散",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦✦",
#         tags=["light", "matrix", "cross", "wave", "manhattan"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("freq", "频率", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_cross_wave)

    # --- A7: 矩阵棋盘渐变 ---
#     def _matrix_checker_gradient(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             checker = (row + col) % 2
#             phase = t * speed + (0.5 if checker else 0.0)
#             val = _sine(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_checker_gradient_v28", name="矩阵棋盘渐变", category="灯光/矩阵",
#         description="棋盘格奇偶位置相位相反，形成交替呼吸效果",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦♟",
#         tags=["light", "matrix", "checker", "gradient"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_checker_gradient)

    # --- A8: 矩阵对角渐变流 ---
#     def _matrix_diagonal_flow(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.8))
#         freq = float(params.get("freq", 2.0))
#         direction = params.get("direction", "左上→右下")
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             if direction == "左上→右下":
#                 d = (col / max(1, cols - 1) + row / max(1, rows - 1)) * 0.5
#             elif direction == "右上→左下":
#                 d = ((cols - 1 - col) / max(1, cols - 1) + row / max(1, rows - 1)) * 0.5
#             elif direction == "中心→四角":
#                 cx = (cols - 1) / 2.0
#                 cy = (rows - 1) / 2.0
#                 d = _dist(col, row, cx, cy) / max(1, _dist(0, 0, cx, cy))
#             else:  # 四角→中心
#                 cx = (cols - 1) / 2.0
#                 cy = (rows - 1) / 2.0
#                 d = 1.0 - _dist(col, row, cx, cy) / max(1, _dist(0, 0, cx, cy))
#             phase = d * freq - t * speed
#             val = _sine(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_diagonal_flow_v28", name="矩阵对角流", category="灯光/矩阵",
#         description="沿对角线方向流动的波纹，可选四种方向",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦↗",
#         tags=["light", "matrix", "diagonal", "flow"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.8, 0.0, 5.0, 0.1),
#             ParamSpec("freq", "频率", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("direction", "方向", "choice", "左上→右下",
#                       choices=["左上→右下", "右上→左下", "中心→四角", "四角→中心"]),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_diagonal_flow)

    # --- A9: 矩阵扭曲波 ---
#     def _matrix_twisted_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.6))
#         twist = float(params.get("twist", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 每行有不同的相位偏移（扭曲效果）
#             row_norm = row / max(1, rows - 1)
#             col_norm = col / max(1, cols - 1)
#             twist_offset = _sine_raw(row_norm * twist + t * speed * 0.5) * 0.3
#             phase = col_norm + twist_offset - t * speed
#             val = _sine(phase) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_twisted_wave_v28", name="矩阵扭曲波", category="灯光/矩阵",
#         description="波形随行位置扭曲变形，形成有机流动感",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦〜",
#         tags=["light", "matrix", "twisted", "wave", "organic"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.6, 0.0, 5.0, 0.1),
#             ParamSpec("twist", "扭曲度", "float", 2.0, 0.0, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_twisted_wave)

    # --- A10: 矩阵三角波干涉 ---
#     def _matrix_triangle_interference(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.5))
#         freq1 = float(params.get("freq1", 2.0))
#         freq2 = float(params.get("freq2", 3.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             x = col / max(1, cols - 1)
#             y = row / max(1, rows - 1)
#             w1 = _triangle(x * freq1 - t * speed)
#             w2 = _triangle(y * freq2 - t * speed * 0.7)
#             val = (w1 * w2) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_triangle_interference_v28", name="矩阵三角波干涉", category="灯光/矩阵",
#         description="水平和垂直三角波相乘形成干涉图案，产生网格状高亮点",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦△",
#         tags=["light", "matrix", "triangle", "interference"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.5, 0.0, 5.0, 0.1),
#             ParamSpec("freq1", "横向频率", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("freq2", "纵向频率", "float", 3.0, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_triangle_interference)

    # --- A11: 矩阵噪声纹理 ---
#     def _matrix_noise_texture(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.3))
#         scale = float(params.get("scale", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             x = col / max(1, cols - 1) * scale
#             y = row / max(1, rows - 1) * scale
            # 多层噪声叠加
#             n1 = _perlin_like(x, y, t * speed, 1.0)
#             n2 = _perlin_like(x * 2, y * 2, t * speed * 1.5, 0.5) * 0.5
#             n3 = _perlin_like(x * 4, y * 4, t * speed * 2.0, 0.25) * 0.25
#             val = (n1 + n2 + n3) / 1.75 * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_noise_texture_v28", name="矩阵噪声纹理", category="灯光/矩阵",
#         description="多层Perlin噪声叠加，形成自然有机的流动纹理",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦≋",
#         tags=["light", "matrix", "noise", "organic", "texture"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.3, 0.0, 3.0, 0.1),
#             ParamSpec("scale", "缩放", "float", 2.0, 0.5, 6.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_noise_texture)

    # --- A12: 矩阵扇形扫描 ---
#     def _matrix_fan_scan(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         beam_width = float(params.get("beam_width", 0.3))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         cx = (cols - 1) / 2.0
#         cy = (rows - 1) / 2.0
#         scan_angle = (t * speed * 2 * math.pi) % (2 * math.pi)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             dx = col - cx
#             dy = row - cy
#             angle = math.atan2(dy, dx) % (2 * math.pi)
            # 角度差
#             diff = abs(angle - scan_angle)
#             diff = min(diff, 2 * math.pi - diff)
#             val = max(0.0, 1.0 - diff / (beam_width * math.pi)) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_fan_scan_v28", name="矩阵扇形扫描", category="灯光/矩阵",
#         description="雷达式扇形光束旋转扫描，光束宽度可调",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦📡",
#         tags=["light", "matrix", "fan", "scan", "radar"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("beam_width", "光束宽度", "float", 0.3, 0.05, 1.0, 0.05),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_fan_scan)

    # --- A13: 矩阵水纹扩散 ---
#     def _matrix_water_spread(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.2))
#         num_sources = max(1, int(params.get("num_sources", 2)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 多个水波源，位置随时间缓慢移动
#         sources = []
#         for i in range(num_sources):
#             sx = 0.5 + 0.4 * math.cos(2 * math.pi * i / num_sources + t * 0.1)
#             sy = 0.5 + 0.4 * math.sin(2 * math.pi * i / num_sources + t * 0.13)
#             sources.append((sx * (cols - 1), sy * (rows - 1)))
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             total = 0.0
#             for sx, sy in sources:
#                 d = _dist(col, row, sx, sy)
#                 phase = d * 0.8 - t * speed
#                 total += _sine(phase) * max(0.0, 1.0 - d / (cols + rows) * 2)
#             ef.values[orig_i][ch] = _clamp(_clampf(total / num_sources) * 255)
# 
#     reg(EffectPreset(
#         id="matrix_water_spread_v28", name="矩阵水纹扩散", category="灯光/矩阵",
#         description="多个水波源同时产生涟漪，叠加形成复杂水面纹理",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦💧",
#         tags=["light", "matrix", "water", "spread", "ripple"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.2, 0.1, 5.0, 0.1),
#             ParamSpec("num_sources", "波源数", "int", 2, 1, 6, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_water_spread)

    # --- A14: 矩阵闪电 ---
#     def _matrix_lightning(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 闪电：随机路径，每隔一段时间重新生成
#         flash_period = 1.0 / max(0.1, speed)
#         flash_phase = t % flash_period / flash_period
        # 用时间种子生成稳定的随机路径
#         seed = int(t / flash_period)
#         rng = random.Random(seed)
        # 从顶部到底部的闪电路径
#         path_cols = [rng.randint(0, cols - 1)]
#         for _ in range(rows - 1):
#             next_col = path_cols[-1] + rng.randint(-1, 1)
#             path_cols.append(max(0, min(cols - 1, next_col)))
        # 闪光强度
#         intensity = max(0.0, 1.0 - flash_phase * 3.0) if flash_phase < 0.33 else 0.0
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             if row < len(path_cols) and abs(col - path_cols[row]) <= 1:
#                 dist = abs(col - path_cols[row])
#                 val = intensity * (1.0 - dist * 0.5) * 255
#             else:
#                 val = 0.0
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_lightning_v28", name="矩阵闪电", category="灯光/矩阵",
#         description="随机闪电路径从顶部向底部延伸，定期重新生成",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⚡",
#         tags=["light", "matrix", "lightning", "random"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "闪电频率", "float", 2.0, 0.5, 10.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_lightning)

    # --- A15: 矩阵波浪叠加 ---
#     def _matrix_wave_stack(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 0.8))
#         layers = max(1, int(params.get("layers", 3)))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             x = col / max(1, cols - 1)
#             y = row / max(1, rows - 1)
#             total = 0.0
#             for i in range(layers):
#                 freq = 1.0 + i * 1.5
#                 angle = math.pi * i / layers
#                 dx = math.cos(angle)
#                 dy = math.sin(angle)
#                 phase = (x * dx + y * dy) * freq - t * speed * (1.0 + i * 0.3)
#                 total += _sine(phase) / layers
#             ef.values[orig_i][ch] = _clamp(total * 255)
# 
#     reg(EffectPreset(
#         id="matrix_wave_stack_v28", name="矩阵波浪叠加", category="灯光/矩阵",
#         description="多层不同方向和频率的波浪叠加，形成复杂干涉图案",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦≈",
#         tags=["light", "matrix", "wave", "stack", "interference"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 0.8, 0.0, 5.0, 0.1),
#             ParamSpec("layers", "叠加层数", "int", 3, 1, 6, 1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_wave_stack)

    # ═══════════════════════════════════════════════════════════════════
    #  B. 矩阵音频关联效果扩充
    # ═══════════════════════════════════════════════════════════════════

    # --- B1: 矩阵节拍爆炸 ---
#     def _matrix_beat_explosion(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 2.0))
#         decay = float(params.get("decay", 3.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         cx = (cols - 1) / 2.0
#         cy = (rows - 1) / 2.0
        # 节拍爆炸：每个节拍从中心爆炸向外
#         beat_phase = (t * speed) % 1.0
#         max_r = _dist(0, 0, cx, cy)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             d = _dist(col, row, cx, cy)
            # 爆炸波前
#             wave_r = beat_phase * max_r * 1.5
#             ring = max(0.0, 1.0 - abs(d - wave_r) / 1.5)
            # 衰减
#             val = ring * math.exp(-beat_phase * decay) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_beat_explosion_v28", name="矩阵节拍爆炸", category="灯光/矩阵",
#         description="每个节拍从中心爆炸向外扩散，模拟音频节拍冲击波",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦💥",
#         tags=["light", "matrix", "beat", "explosion", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "节拍速度", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("decay", "衰减速度", "float", 3.0, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_beat_explosion)

    # --- B2: 矩阵频率柱彩色 ---
#     def _matrix_spectrum_color(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.0))
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
            # 每列模拟不同频率能量
#             freq_phase = col / max(1, cols - 1)
#             energy = _sine(freq_phase * 2.3 + t * speed * (1.0 + freq_phase))
            # 能量高于行位置则点亮
#             row_norm = 1.0 - row / max(1, rows - 1)
#             val = 255 if row_norm <= energy else 0
#             ef.values[orig_i]["dimmer"] = _clamp(val)
            # 颜色随频率变化（低频红，高频蓝）
#             ef.values[orig_i]["R"] = _clamp((1.0 - freq_phase) * val)
#             ef.values[orig_i]["G"] = _clamp(energy * 0.5 * val)
#             ef.values[orig_i]["B"] = _clamp(freq_phase * val)
# 
#     reg(EffectPreset(
#         id="matrix_spectrum_color_v28", name="矩阵彩色频谱", category="灯光/矩阵",
#         description="彩色频谱柱状图，低频红色高频蓝色，模拟音频分析仪",
#         target_channels=["dimmer", "R", "G", "B"], icon="▦🌈",
#         tags=["light", "matrix", "spectrum", "color", "audio"],
#         params=[
#             ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_spectrum_color)

    # --- B3: 矩阵节拍行扫 ---
#     def _matrix_beat_row_scan(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 2.0))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
        # 节拍驱动行扫描
#         beat_phase = (t * speed) % 1.0
#         active_row = int(beat_phase * rows)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             dist = abs(row - active_row)
#             val = max(0.0, 1.0 - dist * 0.8) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_beat_row_scan_v28", name="矩阵节拍行扫", category="灯光/矩阵",
#         description="节拍驱动的行扫描，扫描线随节拍从上到下移动",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦—",
#         tags=["light", "matrix", "beat", "row", "scan", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "节拍速度", "float", 2.0, 0.5, 8.0, 0.5),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_beat_row_scan)

    # --- B4: 矩阵能量波动 ---
#     def _matrix_energy_wave(ef, t, sorted_fx, fdefs, params, fps):
#         speed = float(params.get("speed", 1.5))
#         ch = params.get("ch_type", "dimmer")
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         for seq_i, (orig_i, fi) in enumerate(sorted_fx):
#             row, col = _get_grid(seq_i, cols)
#             x = col / max(1, cols - 1)
#             y = row / max(1, rows - 1)
            # 模拟能量随时间的多频率波动
#             e1 = _sine(x * 3 + t * speed)
#             e2 = _sine(y * 2 - t * speed * 0.7)
#             e3 = _sine((x + y) * 2.5 + t * speed * 1.3)
#             val = (e1 * 0.4 + e2 * 0.3 + e3 * 0.3) * 255
#             ef.values[orig_i][ch] = _clamp(val)
# 
#     reg(EffectPreset(
#         id="matrix_energy_wave_v28", name="矩阵能量波动", category="灯光/矩阵",
#         description="多频率能量波叠加，模拟音频能量在矩阵中的传播",
#         target_channels=["dimmer", "R", "G", "B", "W"], icon="▦⚡",
#         tags=["light", "matrix", "energy", "wave", "audio"],
#         params=[
#             ParamSpec("ch_type", "通道", "choice", "dimmer",
#                       choices=["dimmer", "R", "G", "B", "W"]),
#             ParamSpec("speed", "速度", "float", 1.5, 0.1, 6.0, 0.1),
#             ParamSpec("cols", "列数", "int", 4, 2, 16, 1),
#         ],
#     ), _matrix_energy_wave)

    # ═══════════════════════════════════════════════════════════════════
    #  C. 泵矩阵效果大量扩充
    # ═══════════════════════════════════════════════════════════════════

    # --- C1: 泵矩阵锯齿纹 ---
    def _pump_matrix_zigzag(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        freq = float(params.get("freq", 3.0))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            if row % 2 == 0:
                x = col / max(1, cols - 1)
            else:
                x = 1.0 - col / max(1, cols - 1)
            phase = x * freq - t * speed
            val = lo + _triangle(phase) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_zigzag_v28", name="泵矩阵锯齿纹", category="泵/矩阵",
        description="泵矩阵锯齿形流动，奇偶行方向交替",
        target_channels=["pump", "valve", "dimmer"], icon="▦Z",
        tags=["pump", "matrix", "zigzag"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.0, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("freq", "频率", "float", 3.0, 1.0, 10.0, 0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_zigzag)

    # --- C2: 泵矩阵同心方形 ---
    def _pump_matrix_concentric_square(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        cx = (cols - 1) / 2.0
        cy = (rows - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            dist = max(abs(col - cx), abs(row - cy))
            phase = dist - t * speed
            val = lo + _sine(phase * 0.5) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_concentric_sq_v28", name="泵矩阵同心方形", category="泵/矩阵",
        description="泵矩阵同心方形向外扩散水型",
        target_channels=["pump", "valve", "dimmer"], icon="▦□",
        tags=["pump", "matrix", "concentric", "square"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_concentric_square)

    # --- C3: 泵矩阵对角流 ---
    def _pump_matrix_diagonal(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        freq = float(params.get("freq", 2.0))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            d = (col / max(1, cols - 1) + row / max(1, rows - 1)) * 0.5
            phase = d * freq - t * speed
            val = lo + _sine(phase) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_diagonal_v28", name="泵矩阵对角流", category="泵/矩阵",
        description="泵矩阵沿对角线方向流动的波纹水型",
        target_channels=["pump", "valve", "dimmer"], icon="▦↗",
        tags=["pump", "matrix", "diagonal"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.0, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("freq", "频率", "float", 2.0, 0.5, 8.0, 0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_diagonal)

    # --- C4: 泵矩阵格纹 ---
    def _pump_matrix_plaid(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        h_freq = float(params.get("h_freq", 2.0))
        v_freq = float(params.get("v_freq", 2.0))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            h = _sine_raw(col / max(1, cols - 1) * h_freq - t * speed)
            v = _sine_raw(row / max(1, rows - 1) * v_freq - t * speed * 0.7)
            val = lo + ((h * v) * 0.5 + 0.5) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_plaid_v28", name="泵矩阵格纹", category="泵/矩阵",
        description="泵矩阵格纹水型，水平垂直波叠加",
        target_channels=["pump", "valve", "dimmer"], icon="▦#",
        tags=["pump", "matrix", "plaid", "grid"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.0, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("h_freq", "横向频率", "float", 2.0, 0.5, 8.0, 0.5,
                tooltip="控制水平方向的波动频率。值越高水平方向波动越快"),
            ParamSpec("v_freq", "纵向频率", "float", 2.0, 0.5, 8.0, 0.5,
                tooltip="控制垂直方向的波动频率。值越高垂直方向波动越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_plaid)

    # --- C5: 泵矩阵等离子体 ---
    def _pump_matrix_plasma(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        scale = float(params.get("scale", 2.0))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            x = col / max(1, cols - 1) * scale
            y = row / max(1, rows - 1) * scale
            p1 = _sine_raw(x + t * speed)
            p2 = _sine_raw(y + t * speed * 0.8)
            p3 = _sine_raw((x + y) * 0.7 + t * speed * 1.2)
            cx = x + 0.5 * _sine_raw(t * speed * 0.3)
            cy = y + 0.5 * _sine_raw(t * speed * 0.4)
            p4 = _sine_raw(math.sqrt(cx * cx + cy * cy + 0.001) * 2 - t * speed)
            plasma = (p1 + p2 + p3 + p4) / 4.0
            val = lo + (plasma * 0.5 + 0.5) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_plasma_v28", name="泵矩阵等离子体", category="泵/矩阵",
        description="泵矩阵等离子体水型，有机流动纹理",
        target_channels=["pump", "valve", "dimmer"], icon="▦🌊",
        tags=["pump", "matrix", "plasma", "organic"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.0, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("scale", "缩放", "float", 2.0, 0.5, 6.0, 0.5,
                tooltip="控制缩放比例。值越大视觉元素放大越多"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_plasma)

    # --- C6: 泵矩阵节拍爆炸 ---
    def _pump_matrix_beat_explosion(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        cx = (cols - 1) / 2.0
        cy = (rows - 1) / 2.0
        beat_phase = (t * speed) % 1.0
        max_r = _dist(0, 0, cx, cy) + 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            d = _dist(col, row, cx, cy)
            wave_r = beat_phase * max_r * 1.5
            ring = max(0.0, 1.0 - abs(d - wave_r) / 1.5)
            decay = math.exp(-beat_phase * 3.0)
            val = lo + ring * decay * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_beat_explosion_v28", name="泵矩阵节拍爆炸", category="泵/矩阵",
        description="节拍驱动从中心爆炸向外扩散的水型",
        target_channels=["pump", "valve", "dimmer"], icon="▦💥",
        tags=["pump", "matrix", "beat", "explosion"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "节拍速度", "float", 2.0, 0.5, 8.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_beat_explosion)

    # --- C7: 泵矩阵扇形扫描 ---
    def _pump_matrix_fan_scan(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        beam_width = float(params.get("beam_width", 0.4))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        cx = (cols - 1) / 2.0
        cy = (rows - 1) / 2.0
        scan_angle = (t * speed * 2 * math.pi) % (2 * math.pi)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            dx = col - cx
            dy = row - cy
            angle = math.atan2(dy, dx) % (2 * math.pi)
            diff = abs(angle - scan_angle)
            diff = min(diff, 2 * math.pi - diff)
            intensity = max(0.0, 1.0 - diff / (beam_width * math.pi))
            val = lo + intensity * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_fan_scan_v28", name="泵矩阵扇形扫描", category="泵/矩阵",
        description="雷达式扇形扫描水型，旋转光束",
        target_channels=["pump", "valve", "dimmer"], icon="▦📡",
        tags=["pump", "matrix", "fan", "scan", "radar"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("beam_width", "光束宽度", "float", 0.4, 0.05, 1.0, 0.05,
                tooltip="控制光束宽度的比例。0 为最小，1.0 为最大"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_fan_scan)

    # --- C8: 泵矩阵水纹扩散 ---
    def _pump_matrix_water_spread(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.2))
        num_sources = max(1, int(params.get("num_sources", 2)))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        sources = []
        for i in range(num_sources):
            sx = 0.5 + 0.4 * math.cos(2 * math.pi * i / num_sources + t * 0.1)
            sy = 0.5 + 0.4 * math.sin(2 * math.pi * i / num_sources + t * 0.13)
            sources.append((sx * (cols - 1), sy * (rows - 1)))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            total = 0.0
            for sx, sy in sources:
                d = _dist(col, row, sx, sy)
                phase = d * 0.8 - t * speed
                total += _sine(phase) * max(0.0, 1.0 - d / (cols + rows) * 2)
            val = lo + _clampf(total / num_sources) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_water_spread_v28", name="泵矩阵水纹扩散", category="泵/矩阵",
        description="多个水波源同时产生涟漪叠加的水型",
        target_channels=["pump", "valve", "dimmer"], icon="▦💧",
        tags=["pump", "matrix", "water", "spread"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.2, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("num_sources", "波源数", "int", 2, 1, 6, 1,
                tooltip="控制信号源的数量。值越大辐射起点越多"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_water_spread)

    # --- C9: 泵矩阵双螺旋 ---
    def _pump_matrix_double_spiral(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.4))
        arms = max(2, int(params.get("arms", 2)))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        cx = (cols - 1) / 2.0
        cy = (rows - 1) / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            dx = col - cx
            dy = row - cy
            angle = math.atan2(dy, dx)
            r = _dist(col, row, cx, cy)
            best = 0.0
            for arm in range(arms):
                arm_angle = angle - r * 0.8 - t * speed * 2 * math.pi + arm * 2 * math.pi / arms
                best = max(best, _sine(arm_angle / (2 * math.pi)))
            val = lo + best * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_double_spiral_v28", name="泵矩阵双螺旋", category="泵/矩阵",
        description="泵矩阵多臂螺旋旋转水型",
        target_channels=["pump", "valve", "dimmer"], icon="▦🌀",
        tags=["pump", "matrix", "spiral", "galaxy"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.4, 0.0, 3.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("arms", "臂数", "int", 2, 1, 6, 1,
                tooltip="控制旋臂数量。值越大旋臂越多，生成的图案越复杂"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_double_spiral)

    # --- C10: 泵矩阵波浪叠加 ---
    def _pump_matrix_wave_stack(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        layers = max(1, int(params.get("layers", 3)))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        cols = max(1, int(params.get("cols", 4)))
        n = len(sorted_fx)
        rows = max(1, (n + cols - 1) // cols)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            row, col = _get_grid(seq_i, cols)
            x = col / max(1, cols - 1)
            y = row / max(1, rows - 1)
            total = 0.0
            for i in range(layers):
                freq = 1.0 + i * 1.5
                angle = math.pi * i / layers
                dx = math.cos(angle)
                dy = math.sin(angle)
                phase = (x * dx + y * dy) * freq - t * speed * (1.0 + i * 0.3)
                total += _sine(phase) / layers
            val = lo + total * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_matrix_wave_stack_v28", name="泵矩阵波浪叠加", category="泵/矩阵",
        description="多层不同方向波浪叠加的泵矩阵水型",
        target_channels=["pump", "valve", "dimmer"], icon="▦≈",
        tags=["pump", "matrix", "wave", "stack"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.0, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("layers", "叠加层数", "int", 3, 1, 6, 1,
                tooltip="控制叠加层数。值越大层数越多，效果越丰富厚重"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
        ],
    ), _pump_matrix_wave_stack)

    # ═══════════════════════════════════════════════════════════════════
    #  D. 灯光效果大量变式扩充
    # ═══════════════════════════════════════════════════════════════════

    # --- D1: 灯光三组正跑 ---
    def _light_triple_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.2))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            local = (seq_i / n - phase) % 1.0
            val = hi if local < 0.333 else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_triple_chase_v28", name="三组正跑", category="灯光/跑灯",
        description="三组灯光依次追逐，1/3占空比",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="→→→",
        tags=["light", "chase", "triple"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.2, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_triple_chase)

    # --- D2: 灯光双向追逐 ---
    def _light_bidirectional_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        groups = max(2, int(params.get("groups", 4)))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        half = n // 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < half:
                # 前半段正向
                local = (seq_i / max(1, half) - phase) % 1.0
            else:
                # 后半段反向
                rev_i = n - 1 - seq_i
                local = (rev_i / max(1, half) - phase) % 1.0
            grp_size = 1.0 / groups
            val = hi if local < grp_size else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_bidirectional_chase_v28", name="双向追逐", category="灯光/跑灯",
        description="前半段正向追逐，后半段反向追逐，形成对称效果",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="←→",
        tags=["light", "chase", "bidirectional"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("groups", "分组数", "int", 4, 2, 16, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_bidirectional_chase)

    # --- D3: 灯光中心扩散跑 ---
    def _light_center_expand_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        groups = max(2, int(params.get("groups", 4)))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        center = n / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - center) / center
            local = (dist - phase) % 1.0
            grp_size = 1.0 / groups
            val = hi if local < grp_size else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_center_expand_v28", name="中心扩散跑", category="灯光/跑灯",
        description="从中心向两端扩散的追逐效果",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="↔",
        tags=["light", "chase", "center", "expand"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("groups", "分组数", "int", 4, 2, 16, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_center_expand_chase)

    # --- D4: 灯光随机跑 ---
    def _light_random_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        density = float(params.get("density", 0.3))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        # 用时间种子生成稳定的随机序列
        seed = int(t * speed * 10)
        rng = random.Random(seed)
        active = set(rng.sample(range(n), max(1, int(n * density))))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            val = hi if seq_i in active else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_random_chase_v28", name="随机跑灯", category="灯光/跑灯",
        description="随机点亮一定比例的灯，每个节拍重新随机",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="?→",
        tags=["light", "chase", "random"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 2.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("density", "密度", "float", 0.3, 0.1, 0.9, 0.05,
                tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_random_chase)

    # --- D5: 灯光脉冲闪烁 ---
    def _light_pulse_strobe(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 4.0))
        duty = float(params.get("duty", 0.3))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        val = hi if phase < duty else lo
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_pulse_strobe_v28", name="脉冲闪烁", category="灯光/闪烁",
        description="全体同步脉冲闪烁，可调占空比",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="⚡",
        tags=["light", "strobe", "pulse"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 4.0, 0.5, 20.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("duty", "占空比", "float", 0.3, 0.05, 0.95, 0.05,
                tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_pulse_strobe)

    # --- D6: 灯光交替闪烁 ---
    def _light_alternate_strobe(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 3.0))
        groups = max(2, int(params.get("groups", 2)))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = int(t * speed) % groups
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i % groups
            val = hi if grp == phase else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_alternate_strobe_v28", name="交替闪烁", category="灯光/闪烁",
        description="多组灯光依次交替闪烁，形成跳动感",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="⚡⚡",
        tags=["light", "strobe", "alternate"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 3.0, 0.5, 20.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("groups", "分组数", "int", 2, 2, 8, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_alternate_strobe)

    # --- D7: 灯光渐变呼吸 ---
    def _light_gradient_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        phase_offset = float(params.get("phase_offset", 0.3))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = seq_i / n * phase_offset
            val = lo + _sine(t * speed + offset) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_gradient_breath_v28", name="渐变呼吸", category="灯光/呼吸",
        description="每盏灯有不同相位偏移的呼吸效果，形成流动感",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="~",
        tags=["light", "breath", "gradient"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("phase_offset", "相位偏移", "float", 0.3, 0.0, 2.0, 0.1,
                tooltip="控制整体相位的偏移量。值越大所有设备的起始相位越滞后"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_gradient_breath)

    # --- D8: 灯光快速呼吸 ---
    def _light_fast_breath(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 3.0))
        ch = params.get("ch_type", "dimmer")
        lo = int(params.get("lo", 0))
        hi = int(params.get("hi", 255))
        val = lo + _sine(t * speed) * (hi - lo)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="light_fast_breath_v28", name="快速呼吸", category="灯光/呼吸",
        description="全体同步快速呼吸，适合节拍感强的音乐",
        target_channels=["dimmer", "R", "G", "B", "W"], icon="~~",
        tags=["light", "breath", "fast"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "dimmer",
                      choices=["dimmer", "R", "G", "B", "W"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 3.0, 0.5, 10.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _light_fast_breath)

    # --- D9: 灯光彩虹追逐 ---
    def _light_rainbow_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        cycles = float(params.get("cycles", 1.0))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            hue = (seq_i / n * cycles + t * speed) % 1.0
            # HSV to RGB (S=1, V=1)
            h6 = hue * 6.0
            i = int(h6)
            f = h6 - i
            p, q, tv = 0.0, 1.0 - f, f
            if i == 0:
                r, g, b = 1.0, tv, p
            elif i == 1:
                r, g, b = q, 1.0, p
            elif i == 2:
                r, g, b = p, 1.0, tv
            elif i == 3:
                r, g, b = p, q, 1.0
            elif i == 4:
                r, g, b = tv, p, 1.0
            else:
                r, g, b = 1.0, p, q
            ef.values[orig_i]["R"] = _clamp(r * 255)
            ef.values[orig_i]["G"] = _clamp(g * 255)
            ef.values[orig_i]["B"] = _clamp(b * 255)

    reg(EffectPreset(
        id="light_rainbow_chase_v28", name="彩虹追逐", category="灯光/颜色",
        description="彩虹色沿灯光序列流动追逐",
        target_channels=["R", "G", "B"], icon="🌈→",
        tags=["light", "rainbow", "chase", "color"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("cycles", "彩虹周期数", "float", 1.0, 0.5, 5.0, 0.5,
                tooltip="控制完整周期的数量。值越大循环次数越多"),
        ],
    ), _light_rainbow_chase)

    # --- D10: 灯光颜色爆炸 ---
    def _light_color_explosion(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        n = len(sorted_fx)
        if n == 0:
            return
        # 每个节拍随机切换颜色
        beat_period = 1.0 / max(0.1, speed)
        seed = int(t / beat_period)
        rng = random.Random(seed)
        hue = rng.random()
        h6 = hue * 6.0
        i = int(h6)
        f = h6 - i
        p, q, tv = 0.0, 1.0 - f, f
        if i == 0:
            r, g, b = 1.0, tv, p
        elif i == 1:
            r, g, b = q, 1.0, p
        elif i == 2:
            r, g, b = p, 1.0, tv
        elif i == 3:
            r, g, b = p, q, 1.0
        elif i == 4:
            r, g, b = tv, p, 1.0
        else:
            r, g, b = 1.0, p, q
        # 爆炸强度
        beat_phase = (t % beat_period) / beat_period
        intensity = max(0.0, 1.0 - beat_phase * 2.0)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ef.values[orig_i]["R"] = _clamp(r * intensity * 255)
            ef.values[orig_i]["G"] = _clamp(g * intensity * 255)
            ef.values[orig_i]["B"] = _clamp(b * intensity * 255)

    reg(EffectPreset(
        id="light_color_explosion_v28", name="颜色爆炸", category="灯光/颜色",
        description="每个节拍随机切换颜色并快速衰减，形成颜色爆炸感",
        target_channels=["R", "G", "B"], icon="💥🌈",
        tags=["light", "color", "explosion", "random"],
        params=[
            ParamSpec("speed", "节拍速度", "float", 1.0, 0.1, 8.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
        ],
    ), _light_color_explosion)

    # --- D11: 灯光颜色波浪 ---
    def _light_color_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        freq = float(params.get("freq", 1.0))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            hue = (seq_i / n * freq + t * speed) % 1.0
            h6 = hue * 6.0
            i = int(h6)
            f = h6 - i
            p, q, tv = 0.0, 1.0 - f, f
            if i == 0:
                r, g, b = 1.0, tv, p
            elif i == 1:
                r, g, b = q, 1.0, p
            elif i == 2:
                r, g, b = p, 1.0, tv
            elif i == 3:
                r, g, b = p, q, 1.0
            elif i == 4:
                r, g, b = tv, p, 1.0
            else:
                r, g, b = 1.0, p, q
            ef.values[orig_i]["R"] = _clamp(r * 255)
            ef.values[orig_i]["G"] = _clamp(g * 255)
            ef.values[orig_i]["B"] = _clamp(b * 255)

    reg(EffectPreset(
        id="light_color_wave_v28", name="颜色波浪", category="灯光/颜色",
        description="颜色沿灯光序列形成彩色波浪流动",
        target_channels=["R", "G", "B"], icon="🌈~",
        tags=["light", "color", "wave"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("freq", "频率", "float", 1.0, 0.5, 5.0, 0.5,
                tooltip="控制效果波动的频率。值越高每秒完整波动次数越多，动态越密集"),
        ],
    ), _light_color_wave)

    # --- D12: 灯光双色交替 ---
    def _light_dual_color_alternate(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        r1 = int(params.get("r1", 255))
        g1 = int(params.get("g1", 0))
        b1 = int(params.get("b1", 0))
        r2 = int(params.get("r2", 0))
        g2 = int(params.get("g2", 0))
        b2 = int(params.get("b2", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = (t * speed) % 1.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            # 奇偶交替，随时间切换
            is_color1 = ((seq_i % 2) == int(phase * 2) % 2)
            if is_color1:
                ef.values[orig_i]["R"] = _clamp(r1)
                ef.values[orig_i]["G"] = _clamp(g1)
                ef.values[orig_i]["B"] = _clamp(b1)
            else:
                ef.values[orig_i]["R"] = _clamp(r2)
                ef.values[orig_i]["G"] = _clamp(g2)
                ef.values[orig_i]["B"] = _clamp(b2)

    reg(EffectPreset(
        id="light_dual_color_alternate_v28", name="双色交替", category="灯光/颜色",
        description="两种颜色奇偶交替闪烁，形成跳动感",
        target_channels=["R", "G", "B"], icon="🔴🔵",
        tags=["light", "color", "dual", "alternate"],
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 8.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("r1", "颜色1-R", "int", 255, 0, 255, 1,
                tooltip="控制第一种颜色的红色通道强度。0=无红色，255=满红"),
            ParamSpec("g1", "颜色1-G", "int", 0, 0, 255, 1,
                tooltip="控制第一种颜色的绿色通道强度。0=无绿色，255=满绿"),
            ParamSpec("b1", "颜色1-B", "int", 0, 0, 255, 1,
                tooltip="控制第一种颜色的蓝色通道强度。0=无蓝色，255=满蓝"),
            ParamSpec("r2", "颜色2-R", "int", 0, 0, 255, 1,
                tooltip="控制第二种颜色的红色通道强度。0=无红色，255=满红"),
            ParamSpec("g2", "颜色2-G", "int", 0, 0, 255, 1,
                tooltip="控制第二种颜色的绿色通道强度。0=无绿色，255=满绿"),
            ParamSpec("b2", "颜色2-B", "int", 255, 0, 255, 1,
                tooltip="控制第二种颜色的蓝色通道强度。0=无蓝色，255=满蓝"),
        ],
    ), _light_dual_color_alternate)

    # ═══════════════════════════════════════════════════════════════════
    #  E. 泵效果大量变式扩充
    # ═══════════════════════════════════════════════════════════════════

    # --- E1: 泵双向波浪 ---
    def _pump_bidirectional_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        waves = max(1, int(params.get("waves", 2)))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        half = n // 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < half:
                phase = seq_i / max(1, half) * waves - t * speed
            else:
                rev = n - 1 - seq_i
                phase = rev / max(1, half) * waves - t * speed
            val = lo + _sine(phase) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_bidirectional_wave_v28", name="泵双向波浪", category="泵/波浪",
        description="前半段正向波浪，后半段反向波浪，形成对称水型",
        target_channels=["pump", "valve", "dimmer"], icon="~←→~",
        tags=["pump", "wave", "bidirectional"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("waves", "波浪数", "int", 2, 1, 8, 1,
                tooltip="控制波的数量。值越大同一时刻并存的波峰越多"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_bidirectional_wave)

    # --- E2: 泵三角波跑 ---
    def _pump_triangle_wave_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.6))
        waves = max(1, int(params.get("waves", 3)))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = seq_i / n * waves - t * speed
            val = lo + _triangle(phase) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_triangle_wave_chase_v28", name="泵三角波跑", category="泵/波浪",
        description="三角波形跑泉，波形更尖锐",
        target_channels=["pump", "valve", "dimmer"], icon="△→",
        tags=["pump", "wave", "triangle", "chase"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.6, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("waves", "波浪数", "int", 3, 1, 10, 1,
                tooltip="控制波的数量。值越大同一时刻并存的波峰越多"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_triangle_wave_chase)

    # --- E3: 泵锯齿波跑 ---
    def _pump_sawtooth_wave_chase(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.6))
        waves = max(1, int(params.get("waves", 3)))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = (seq_i / n * waves - t * speed) % 1.0
            val = lo + _sawtooth(phase) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_sawtooth_wave_chase_v28", name="泵锯齿波跑", category="泵/波浪",
        description="锯齿波形跑泉，快速上升缓慢下降",
        target_channels=["pump", "valve", "dimmer"], icon="⊿→",
        tags=["pump", "wave", "sawtooth", "chase"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.6, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("waves", "波浪数", "int", 3, 1, 10, 1,
                tooltip="控制波的数量。值越大同一时刻并存的波峰越多"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_sawtooth_wave_chase)

    # --- E4: 泵中心扩散跑 ---
    def _pump_center_expand(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.8))
        waves = max(1, int(params.get("waves", 2)))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        center = n / 2.0
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            dist = abs(seq_i - center) / center
            phase = dist * waves - t * speed
            val = lo + _sine(phase) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_center_expand_v28", name="泵中心扩散", category="泵/跑泉",
        description="从中心向两端扩散的波浪跑泉",
        target_channels=["pump", "valve", "dimmer"], icon="↔~",
        tags=["pump", "center", "expand", "wave"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.8, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("waves", "波浪数", "int", 2, 1, 8, 1,
                tooltip="控制波的数量。值越大同一时刻并存的波峰越多"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_center_expand)

    # --- E5: 泵随机脉冲 ---
    def _pump_random_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        density = float(params.get("density", 0.4))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        seed = int(t * speed * 10)
        rng = random.Random(seed)
        active = set(rng.sample(range(n), max(1, int(n * density))))
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            val = hi if seq_i in active else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_random_pulse_v28", name="泵随机脉冲", category="泵/脉冲",
        description="随机点亮一定比例的泵，每个节拍重新随机",
        target_channels=["pump", "valve", "dimmer"], icon="?~",
        tags=["pump", "random", "pulse"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 2.0, 0.1, 10.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("density", "密度", "float", 0.4, 0.1, 0.9, 0.05,
                tooltip="控制单位空间内的元素密度。值越大视觉元素越密集"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_random_pulse)

    # --- E6: 泵渐变全升 ---
    def _pump_gradient_rise(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.3))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            offset = seq_i / n * 0.5
            val = lo + _sine(t * speed + offset) * (hi - lo)
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_gradient_rise_v28", name="泵渐变全升", category="泵/渐变",
        description="全体泵带相位偏移的渐变升降，形成流动感",
        target_channels=["pump", "valve", "dimmer"], icon="↑~",
        tags=["pump", "gradient", "rise"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 0.3, 0.05, 3.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_gradient_rise)

    # --- E7: 泵快速脉冲 ---
    def _pump_fast_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 4.0))
        duty = float(params.get("duty", 0.3))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        phase = (t * speed) % 1.0
        val = hi if phase < duty else lo
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_fast_pulse_v28", name="泵快速脉冲", category="泵/脉冲",
        description="全体泵同步快速脉冲，适合节拍感强的音乐",
        target_channels=["pump", "valve", "dimmer"], icon="⚡~",
        tags=["pump", "pulse", "fast"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 4.0, 0.5, 20.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("duty", "占空比", "float", 0.3, 0.05, 0.95, 0.05,
                tooltip="控制占空比——波形高电平部分占整个周期的比例。值越大高电平持续时间越长"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_fast_pulse)

    # --- E8: 泵交替脉冲 ---
    def _pump_alternate_pulse(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 2.0))
        groups = max(2, int(params.get("groups", 2)))
        ch = params.get("ch_type", "pump")
        lo = int(params.get("lo", 30))
        hi = int(params.get("hi", 255))
        n = len(sorted_fx)
        if n == 0:
            return
        phase = int(t * speed) % groups
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            grp = seq_i % groups
            val = hi if grp == phase else lo
            ef.values[orig_i][ch] = _clamp(val)

    reg(EffectPreset(
        id="pump_alternate_pulse_v28", name="泵交替脉冲", category="泵/脉冲",
        description="多组泵依次交替脉冲，形成节奏感",
        target_channels=["pump", "valve", "dimmer"], icon="⚡⚡",
        tags=["pump", "pulse", "alternate"],
        params=[
            ParamSpec("ch_type", "通道", "choice", "pump",
                      choices=["pump", "valve", "dimmer"],
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("speed", "速度", "float", 2.0, 0.5, 10.0, 0.5,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("groups", "分组数", "int", 2, 2, 8, 1,
                tooltip="控制设备分组的数量。值越大分组越细，每组成员越少"),
            ParamSpec("lo", "低值", "int", 30, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ],
    ), _pump_alternate_pulse)

    # ═══════════════════════════════════════════════════════════════════
    #  F. 电机效果变式扩充
    # ═══════════════════════════════════════════════════════════════════

    # --- F1: 电机延时阵列 ---
    def _motor_delay_array(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        delay = float(params.get("delay", 0.2))
        amplitude = float(params.get("amplitude", 80.0))
        center = float(params.get("center", 128.0))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = t * speed - seq_i * delay
            val = center + amplitude * _sine_raw(phase)
            ef.values[orig_i]["motor_x"] = _clamp(val)

    reg(EffectPreset(
        id="motor_delay_array_v28", name="延时阵列", category="电机/高级",
        description="每个电机有固定延时，形成波浪传播效果",
        target_channels=["motor_x", "motor_y"], icon="→→→",
        tags=["motor", "delay", "array", "wave"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("delay", "延时步长", "float", 0.2, 0.05, 1.0, 0.05,
                tooltip="控制效果的延迟/错开时间。值越大效果启动越滞后"),
            ParamSpec("amplitude", "幅度", "float", 80.0, 10.0, 120.0, 5.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center", "中心值", "float", 128.0, 64.0, 192.0, 4.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _motor_delay_array)

    # --- F2: 电机镜像龙摆 ---
    def _motor_mirror_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        amplitude = float(params.get("amplitude", 80.0))
        center = float(params.get("center", 128.0))
        n = len(sorted_fx)
        if n == 0:
            return
        half = n // 2
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            if seq_i < half:
                phase = seq_i / max(1, half) - t * speed
            else:
                mirror_i = n - 1 - seq_i
                phase = mirror_i / max(1, half) - t * speed
            val = center + amplitude * _sine_raw(phase * 2 * math.pi)
            ef.values[orig_i]["motor_x"] = _clamp(val)

    reg(EffectPreset(
        id="motor_mirror_dragon_v28", name="镜像龙摆", category="电机/龙摆",
        description="前后两段镜像对称的龙摆，形成对称波浪",
        target_channels=["motor_x", "motor_y"], icon="↔~",
        tags=["motor", "dragon", "mirror", "symmetric"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 80.0, 10.0, 120.0, 5.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center", "中心值", "float", 128.0, 64.0, 192.0, 4.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _motor_mirror_dragon)

    # --- F3: 电机分段龙摆 ---
    def _motor_segment_dragon(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        segments = max(2, int(params.get("segments", 3)))
        amplitude = float(params.get("amplitude", 80.0))
        center = float(params.get("center", 128.0))
        n = len(sorted_fx)
        if n == 0:
            return
        seg_size = max(1, n // segments)
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            seg = seq_i // seg_size
            local = (seq_i % seg_size) / seg_size
            # 每段有不同的相位偏移
            phase = local - t * speed + seg * (1.0 / segments)
            val = center + amplitude * _sine_raw(phase * 2 * math.pi)
            ef.values[orig_i]["motor_x"] = _clamp(val)

    reg(EffectPreset(
        id="motor_segment_dragon_v28", name="分段龙摆", category="电机/龙摆",
        description="将电机分成多段，每段独立龙摆，相位依次错开",
        target_channels=["motor_x", "motor_y"], icon="~|~|~",
        tags=["motor", "dragon", "segment"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("segments", "分段数", "int", 3, 2, 8, 1,
                tooltip="控制设备分割的段数。值越大分段越细，变化越丰富"),
            ParamSpec("amplitude", "幅度", "float", 80.0, 10.0, 120.0, 5.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("center", "中心值", "float", 128.0, 64.0, 192.0, 4.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _motor_segment_dragon)

    # --- F4: 电机双轴波 ---
    def _motor_dual_axis_wave(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 0.5))
        x_amplitude = float(params.get("x_amplitude", 60.0))
        y_amplitude = float(params.get("y_amplitude", 60.0))
        phase_diff = float(params.get("phase_diff", 0.25))
        center = float(params.get("center", 128.0))
        n = len(sorted_fx)
        if n == 0:
            return
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase_x = seq_i / n - t * speed
            phase_y = seq_i / n - t * speed + phase_diff
            import math as _math
            vx = center + x_amplitude * _math.sin(2 * _math.pi * phase_x)
            vy = center + y_amplitude * _math.sin(2 * _math.pi * phase_y)
            ef.values[orig_i]["motor_x"] = max(0, min(255, int(vx)))
            ef.values[orig_i]["motor_y"] = max(0, min(255, int(vy)))

    reg(EffectPreset(
        id="motor_dual_axis_wave_v28", name="双轴波", category="电机/高级",
        description="X轴和Y轴同时做正弦波，相位差可调，形成椭圆/圆形轨迹",
        target_channels=["motor_x", "motor_y"], icon="⊙~",
        tags=["motor", "dual", "axis", "wave", "circle"],
        params=[
            ParamSpec("speed", "速度", "float", 0.5, 0.05, 5.0, 0.05,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("x_amplitude", "X幅度", "float", 60.0, 10.0, 120.0, 5.0,
                tooltip="控制X幅度的值。范围 10.0~120.0，值越大越强"),
            ParamSpec("y_amplitude", "Y幅度", "float", 60.0, 10.0, 120.0, 5.0,
                tooltip="控制Y幅度的值。范围 10.0~120.0，值越大越强"),
            ParamSpec("phase_diff", "相位差", "float", 0.25, 0.0, 1.0, 0.05,
                tooltip="控制相邻设备之间的相位差。值越大设备间的时间错位越明显，波浪感越强"),
            ParamSpec("center", "中心值", "float", 128.0, 64.0, 192.0, 4.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _motor_dual_axis_wave)

    # --- F5: 电机弹性弹跳 ---
    def _motor_elastic_bounce(ef, t, sorted_fx, fdefs, params, fps):
        speed = float(params.get("speed", 1.0))
        amplitude = float(params.get("amplitude", 80.0))
        elasticity = float(params.get("elasticity", 3.0))
        center = float(params.get("center", 128.0))
        n = len(sorted_fx)
        if n == 0:
            return
        import math as _math
        for seq_i, (orig_i, fi) in enumerate(sorted_fx):
            phase = (t * speed + seq_i / max(1, n)) % 1.0
            # 弹性：快速下降，弹起衰减
            if phase < 0.5:
                raw = 1.0 - 2 * phase
            else:
                raw = abs(_math.sin((phase - 0.5) * elasticity * _math.pi)) * (1.0 - phase) * 2
            val = center + amplitude * (raw * 2 - 1)
            ef.values[orig_i]["motor_x"] = max(0, min(255, int(val)))

    reg(EffectPreset(
        id="motor_elastic_bounce_v28", name="弹性弹跳", category="电机/高级",
        description="模拟弹性球弹跳，快速下落后多次弹起衰减",
        target_channels=["motor_x", "motor_y"], icon="⊙↑",
        tags=["motor", "elastic", "bounce"],
        params=[
            ParamSpec("speed", "速度", "float", 1.0, 0.1, 5.0, 0.1,
                tooltip="控制效果运行/变化的速度。值越小变化越缓慢平滑，值越大动态响应越快"),
            ParamSpec("amplitude", "幅度", "float", 80.0, 10.0, 120.0, 5.0,
                tooltip="控制效果幅度的总体大小。值越大变化范围越广，视觉冲击越强"),
            ParamSpec("elasticity", "弹性", "float", 3.0, 1.0, 8.0, 0.5,
                tooltip="控制弹性系数。值越大形变后恢复原状的弹跳越明显"),
            ParamSpec("center", "中心值", "float", 128.0, 64.0, 192.0, 4.0,
                tooltip="控制效果的中心参考值或居中位置。决定变化围绕的基准点"),
        ],
    ), _motor_elastic_bounce)
