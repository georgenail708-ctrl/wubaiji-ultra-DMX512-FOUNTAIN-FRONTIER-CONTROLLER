"""
matrix_pixel_engine_ext.py — 矩阵像素效果引擎扩展
==================================================
将 effect_presets_v27~v32 中所有矩阵效果移植为音频驱动的动态效果，
注入到 MatrixPixelEngine 中。

新增效果模式（全部支持音频实时驱动）：
  v27 移植：
    - MODE_MATRIX_PLASMA_AUDIO       等离子体（音频驱动速度/密度）
    - MODE_MATRIX_INTERFERENCE_AUDIO 干涉纹（音频驱动相位）
    - MODE_MATRIX_VORTEX_AUDIO       涡流（音频驱动旋转速度）
    - MODE_MATRIX_CHECKER_WAVE_AUDIO 棋盘波动（音频驱动频率）
    - MODE_MATRIX_DUAL_SOURCE_AUDIO  双波源（音频驱动波源位置）
    - MODE_MATRIX_RADIAL_WAVE_AUDIO  径向波（音频驱动幅度）
    - MODE_MATRIX_DIAMOND_AUDIO      菱形（音频驱动尺寸）
    - MODE_MATRIX_FLAME_AUDIO        火焰（音频驱动高度）
    - MODE_MATRIX_WATER_RIPPLE_AUDIO 水波纹（音频驱动波源数）
    - MODE_MATRIX_STANDING_WAVE_AUDIO 驻波（音频驱动节点数）
    - MODE_MATRIX_LISSAJOUS_AUDIO    李萨如（音频驱动频率比）
    - MODE_MATRIX_MOIRE_AUDIO        莫尔纹（音频驱动旋转）
    - MODE_MATRIX_ROSE_AUDIO         玫瑰线（音频驱动花瓣数）
    - MODE_MATRIX_FRACTAL_AUDIO      分形噪声（音频驱动湍流）
    - MODE_MATRIX_KALEIDOSCOPE_AUDIO 万花筒（音频驱动对称度）
  
  v28 移植：
    - MODE_MATRIX_DOUBLE_SPIRAL_AUDIO 双螺旋（音频驱动速度）
    - MODE_MATRIX_CROSS_WAVE_AUDIO   十字波（音频驱动幅度）
    - MODE_MATRIX_LIGHTNING_AUDIO    闪电（音频驱动触发）
    - MODE_MATRIX_BEAT_EXPLOSION_AUDIO 节拍爆炸（音频驱动）
    - MODE_MATRIX_WAVE_STACK_AUDIO   波浪叠加（音频驱动层数）
    - MODE_MATRIX_FAN_SCAN_AUDIO     扇形扫描（音频驱动角速度）
    - MODE_MATRIX_WATER_SPREAD_AUDIO 水纹扩散（音频驱动）
  
  v29 移植：
    - MODE_MATRIX_AUDIO_DIAMOND_V29  音频响应菱形
    - MODE_MATRIX_AUDIO_STAR_V29     音频响应星形爆炸
    - MODE_MATRIX_RAPID_FIRE_V29     快速连发十字
  
  v30/v31/v32 移植：
    - MODE_MATRIX_SCAN_LINE_AUDIO    扫描线（音频驱动）
    - MODE_MATRIX_CHECKER_BREATH_AUDIO 棋盘呼吸（音频驱动）
    - MODE_MATRIX_ROW_WAVE_AUDIO     行波（音频驱动）
    - MODE_MATRIX_DOUBLE_HELIX_AUDIO 双螺旋（音频驱动）
    - MODE_MATRIX_SURGE_AUDIO        涌浪（音频驱动）
    - MODE_MATRIX_NOISE_AUDIO        噪声（音频驱动）
    - MODE_MATRIX_ROTATE_SCAN_AUDIO  旋转扫描（音频驱动）
    - MODE_MATRIX_FRACTAL_V30_AUDIO  分形（音频驱动）
    - MODE_MATRIX_CELLULAR_AUDIO     细胞自动机（音频驱动）
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
#  新增效果模式常量
# ─────────────────────────────────────────────────────────────────────────────
# v27 移植
MODE_MATRIX_PLASMA_AUDIO        = "matrix_plasma_audio"
MODE_MATRIX_INTERFERENCE_AUDIO  = "matrix_interference_audio"
MODE_MATRIX_VORTEX_AUDIO        = "matrix_vortex_audio"
MODE_MATRIX_CHECKER_WAVE_AUDIO  = "matrix_checker_wave_audio"
MODE_MATRIX_DUAL_SOURCE_AUDIO   = "matrix_dual_source_audio"
MODE_MATRIX_RADIAL_WAVE_AUDIO   = "matrix_radial_wave_audio"
MODE_MATRIX_DIAMOND_AUDIO       = "matrix_diamond_audio"
MODE_MATRIX_FLAME_AUDIO         = "matrix_flame_audio"
MODE_MATRIX_WATER_RIPPLE_AUDIO  = "matrix_water_ripple_audio"
MODE_MATRIX_STANDING_WAVE_AUDIO = "matrix_standing_wave_audio"
MODE_MATRIX_LISSAJOUS_AUDIO     = "matrix_lissajous_audio"
MODE_MATRIX_MOIRE_AUDIO         = "matrix_moire_audio"
MODE_MATRIX_ROSE_AUDIO          = "matrix_rose_audio"
MODE_MATRIX_FRACTAL_AUDIO       = "matrix_fractal_audio"
MODE_MATRIX_KALEIDOSCOPE_AUDIO  = "matrix_kaleidoscope_audio"

# v28 移植
MODE_MATRIX_DOUBLE_SPIRAL_AUDIO   = "matrix_double_spiral_audio"
MODE_MATRIX_CROSS_WAVE_AUDIO      = "matrix_cross_wave_audio"
MODE_MATRIX_LIGHTNING_AUDIO       = "matrix_lightning_audio"
MODE_MATRIX_BEAT_EXPLOSION_AUDIO  = "matrix_beat_explosion_audio"
MODE_MATRIX_WAVE_STACK_AUDIO      = "matrix_wave_stack_audio"
MODE_MATRIX_FAN_SCAN_AUDIO        = "matrix_fan_scan_audio"
MODE_MATRIX_WATER_SPREAD_AUDIO    = "matrix_water_spread_audio"

# v29 移植
MODE_MATRIX_AUDIO_DIAMOND_V29 = "matrix_audio_diamond_v29"
MODE_MATRIX_AUDIO_STAR_V29    = "matrix_audio_star_v29"
MODE_MATRIX_RAPID_FIRE_V29    = "matrix_rapid_fire_v29"

# v30/v31/v32 移植
MODE_MATRIX_SCAN_LINE_AUDIO      = "matrix_scan_line_audio"
MODE_MATRIX_CHECKER_BREATH_AUDIO = "matrix_checker_breath_audio"
MODE_MATRIX_ROW_WAVE_AUDIO       = "matrix_row_wave_audio"
MODE_MATRIX_DOUBLE_HELIX_AUDIO   = "matrix_double_helix_audio"
MODE_MATRIX_SURGE_AUDIO          = "matrix_surge_audio"
MODE_MATRIX_NOISE_AUDIO          = "matrix_noise_audio"
MODE_MATRIX_ROTATE_SCAN_AUDIO    = "matrix_rotate_scan_audio"
MODE_MATRIX_FRACTAL_V30_AUDIO    = "matrix_fractal_v30_audio"
MODE_MATRIX_CELLULAR_AUDIO       = "matrix_cellular_audio"

# 编舞引擎专用模式
MODE_CHOREO_BLEND              = "choreo_blend"  # 编舞引擎混合输出

# ─────────────────────────────────────────────────────────────────────────────
#  新增效果分组（用于UI展示）
# ─────────────────────────────────────────────────────────────────────────────
PIXEL_MODES_EXT = {
    "矩阵图案(音频驱动)": [
        (MODE_MATRIX_PLASMA_AUDIO,        "等离子体(音频)"),
        (MODE_MATRIX_INTERFERENCE_AUDIO,  "〰 干涉纹(音频)"),
        (MODE_MATRIX_VORTEX_AUDIO,        "涡流(音频)"),
        (MODE_MATRIX_CHECKER_WAVE_AUDIO,  "棋盘波动(音频)"),
        (MODE_MATRIX_DUAL_SOURCE_AUDIO,   "双波源(音频)"),
        (MODE_MATRIX_RADIAL_WAVE_AUDIO,   "径向波(音频)"),
        (MODE_MATRIX_DIAMOND_AUDIO,       "菱形(音频)"),
        (MODE_MATRIX_FLAME_AUDIO,         "火焰(音频)"),
        (MODE_MATRIX_WATER_RIPPLE_AUDIO,  "水波纹(音频)"),
        (MODE_MATRIX_STANDING_WAVE_AUDIO, "驻波(音频)"),
        (MODE_MATRIX_LISSAJOUS_AUDIO,     "李萨如(音频)"),
        (MODE_MATRIX_MOIRE_AUDIO,         "莫尔纹(音频)"),
        (MODE_MATRIX_ROSE_AUDIO,          "玫瑰线(音频)"),
        (MODE_MATRIX_FRACTAL_AUDIO,       "分形噪声(音频)"),
        (MODE_MATRIX_KALEIDOSCOPE_AUDIO,  "万花筒(音频)"),
    ],
    "矩阵动态(音频驱动)": [
        (MODE_MATRIX_DOUBLE_SPIRAL_AUDIO,  "双螺旋(音频)"),
        (MODE_MATRIX_CROSS_WAVE_AUDIO,     "十字波(音频)"),
        (MODE_MATRIX_LIGHTNING_AUDIO,      "闪电(音频)"),
        (MODE_MATRIX_BEAT_EXPLOSION_AUDIO, "节拍爆炸(音频)"),
        (MODE_MATRIX_WAVE_STACK_AUDIO,     "波浪叠加(音频)"),
        (MODE_MATRIX_FAN_SCAN_AUDIO,       "扇形扫描(音频)"),
        (MODE_MATRIX_WATER_SPREAD_AUDIO,   "水纹扩散(音频)"),
        (MODE_MATRIX_AUDIO_DIAMOND_V29,    "音频菱形"),
        (MODE_MATRIX_AUDIO_STAR_V29,       "音频星形爆炸"),
        (MODE_MATRIX_RAPID_FIRE_V29,       "快速连发十字"),
    ],
    "矩阵结构(音频驱动)": [
        (MODE_MATRIX_SCAN_LINE_AUDIO,      "扫描线(音频)"),
        (MODE_MATRIX_CHECKER_BREATH_AUDIO, "棋盘呼吸(音频)"),
        (MODE_MATRIX_ROW_WAVE_AUDIO,       "行波(音频)"),
        (MODE_MATRIX_DOUBLE_HELIX_AUDIO,   "双螺旋(音频)"),
        (MODE_MATRIX_SURGE_AUDIO,          "涌浪(音频)"),
        (MODE_MATRIX_NOISE_AUDIO,          "噪声(音频)"),
        (MODE_MATRIX_ROTATE_SCAN_AUDIO,    "旋转扫描(音频)"),
        (MODE_MATRIX_FRACTAL_V30_AUDIO,    "分形(音频)"),
        (MODE_MATRIX_CELLULAR_AUDIO,       "细胞自动机(音频)"),
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
#  辅助函数
# ─────────────────────────────────────────────────────────────────────────────
def _perlin_like(x: float, y: float) -> float:
    """简化的Perlin噪声近似"""
    n = int(x) * 1619 + int(y) * 31337
    n = (n << 13) ^ n
    return (1.0 - ((n * (n * n * 15731 + 789221) + 1376312589) & 0x7fffffff) / 1073741824.0) * 0.5 + 0.5


def _hash2d(x: int, y: int, seed: int = 0) -> float:
    """2D哈希函数，返回[0,1]"""
    n = x * 1619 + y * 31337 + seed * 6791
    n = (n << 13) ^ n
    return ((n * (n * n * 15731 + 789221) + 1376312589) & 0x7fffffff) / 2147483647.0


def _perlin_like_vec(x, y):
    """`_perlin_like` 的向量化版本（R194/P1-9，与标量版逐位一致）。

    关键点：中间值 ~2^177 远超 int64，必须用 **uint64 显式回绕** 计算——
    加/乘/异或/左移都是模 2^64 同态的，最终 `& 0x7FFFFFFF` 只取低 31 位，
    与 Python 大整数逐位一致；int64 会溢出、负数左移在 C 里是 UB。
    `int(x)` 的截断语义用 `np.trunc().astype(int64)` 复刻（向零截断）。
    golden 证据：tests/test_r194_matrix_perf.py（perlin 类逐位一致断言）。
    """
    ix = np.trunc(np.asarray(x, dtype=np.float64)).astype(np.int64)
    iy = np.trunc(np.asarray(y, dtype=np.float64)).astype(np.int64)
    n = (ix * np.int64(1619) + iy * np.int64(31337)).view(np.uint64)
    n = (n << np.uint64(13)) ^ n  # uint64 回绕 = Python 大整数低 64 位，安全
    m = n * (n * n * np.uint64(15731) + np.uint64(789221)) + np.uint64(1376312589)
    masked = (m & np.uint64(0x7FFFFFFF)).astype(np.float64)
    return (1.0 - masked / 1073741824.0) * 0.5 + 0.5


# ─────────────────────────────────────────────────────────────────────────────
#  效果计算函数（移植自effect_presets_v27~v32，添加音频驱动）
# ─────────────────────────────────────────────────────────────────────────────

def compute_matrix_plasma_audio(engine) -> Dict[str, float]:
    """等离子体（音频驱动速度和密度）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    # 音频驱动：低频控制速度，中频控制密度
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    speed_mod = 0.5 + bass * 2.0
    density_mod = 2.0 + mid * 4.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        v1 = math.sin(nx * density_mod + t * speed_mod)
        v2 = math.sin(ny * density_mod + t * speed_mod * 0.7)
        v3 = math.sin((nx + ny) * density_mod * 0.8 + t * speed_mod * 1.3)
        v4 = math.sin(math.sqrt((nx - 0.5)**2 + (ny - 0.5)**2) * density_mod * 2.0 + t * speed_mod)
        val = (v1 + v2 + v3 + v4) / 4.0 * 0.5 + 0.5
        weights[fid] = val
    return weights


def compute_matrix_interference_audio(engine) -> Dict[str, float]:
    """干涉纹（音频驱动相位）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    high = min(1.0, (bands[5] + bands[6]) * engine.audio_amplitude) if len(bands) >= 7 else rms
    # 音频驱动：低频控制波源间距，高频控制相位速度
    freq1 = 3.0 + bass * 4.0
    freq2 = 4.0 + high * 3.0
    phase_speed = 1.0 + rms * 2.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        r1 = math.sqrt((nx - 0.3)**2 + (ny - 0.3)**2)
        r2 = math.sqrt((nx - 0.7)**2 + (ny - 0.7)**2)
        w1 = math.sin(r1 * freq1 * 2 * math.pi - t * phase_speed)
        w2 = math.sin(r2 * freq2 * 2 * math.pi - t * phase_speed * 0.8)
        val = (w1 + w2) / 2.0 * 0.5 + 0.5
        weights[fid] = val
    return weights


def compute_matrix_vortex_audio(engine) -> Dict[str, float]:
    """涡流（音频驱动旋转速度）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    # 音频驱动：低频控制旋转速度，RMS控制涡流强度
    omega = (0.5 + bass * 3.0) * engine.speed
    strength = 0.5 + rms * 2.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1) - 0.5
        ny = row / max(1, rows - 1) - 0.5
        r = math.sqrt(nx**2 + ny**2)
        angle = math.atan2(ny, nx)
        vortex_angle = angle + r * strength - t * omega
        val = math.sin(vortex_angle * 3.0) * 0.5 + 0.5
        weights[fid] = val
    return weights


def compute_matrix_checker_wave_audio(engine) -> Dict[str, float]:
    """棋盘波动（音频驱动频率）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    freq = 1.0 + mid * 3.0
    speed = 0.5 + bass * 2.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        checker = (col + row) % 2
        wave = math.sin(2 * math.pi * (col / max(1, cols) * freq + t * speed))
        if checker == 0:
            val = wave * 0.5 + 0.5
        else:
            val = -wave * 0.5 + 0.5
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_dual_source_audio(engine) -> Dict[str, float]:
    """双波源（音频驱动波源位置）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    high = min(1.0, (bands[5] + bands[6]) * engine.audio_amplitude) if len(bands) >= 7 else rms
    # 音频驱动：低频控制波源运动，高频控制波速
    s1x = 0.3 + 0.2 * math.sin(t * 0.3 * (1 + bass))
    s1y = 0.3 + 0.2 * math.cos(t * 0.2 * (1 + bass))
    s2x = 0.7 - 0.2 * math.sin(t * 0.3 * (1 + bass))
    s2y = 0.7 - 0.2 * math.cos(t * 0.2 * (1 + bass))
    wave_speed = 2.0 + high * 4.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        r1 = math.sqrt((nx - s1x)**2 + (ny - s1y)**2)
        r2 = math.sqrt((nx - s2x)**2 + (ny - s2y)**2)
        w = (math.sin(r1 * wave_speed * 2 * math.pi - t * 2.0) +
             math.sin(r2 * wave_speed * 2 * math.pi - t * 2.0)) / 2.0
        weights[fid] = w * 0.5 + 0.5
    return weights


def compute_matrix_radial_wave_audio(engine) -> Dict[str, float]:
    """径向波（音频驱动幅度）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    # 音频驱动：低频控制波幅，RMS控制波速
    amplitude = 0.3 + bass * 0.7
    speed = 1.0 + rms * 3.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1) - 0.5
        ny = row / max(1, rows - 1) - 0.5
        r = math.sqrt(nx**2 + ny**2)
        val = math.sin(r * 8.0 - t * speed) * amplitude * 0.5 + 0.5
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_diamond_audio(engine) -> Dict[str, float]:
    """菱形（音频驱动尺寸）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    # 音频驱动：低频控制菱形大小，节拍触发新菱形
    is_beat = engine._get_is_beat()
    state = engine._effect_state
    if 'diamond_size' not in state:
        state['diamond_size'] = 0.3
        state['diamond_decay'] = 0.0
    if is_beat or bass > 0.6:
        state['diamond_size'] = 0.1 + bass * 0.5
        state['diamond_decay'] = 0.0
    state['diamond_decay'] = min(1.0, state['diamond_decay'] + 0.02)
    size = state['diamond_size'] * (1.0 - state['diamond_decay'] * 0.5)
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = abs(col / max(1, cols - 1) - 0.5)
        ny = abs(row / max(1, rows - 1) - 0.5)
        d = nx + ny  # 曼哈顿距离（菱形）
        # 多个同心菱形
        val = 0.0
        for k in range(3):
            ring_d = (size + k * 0.2 + t * 0.3) % 0.8
            dist = abs(d - ring_d)
            val = max(val, max(0.0, 1.0 - dist * 8.0))
        weights[fid] = val
    return weights


def compute_matrix_flame_audio(engine) -> Dict[str, float]:
    """火焰（音频驱动高度）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    # 音频驱动：低频控制火焰高度，RMS控制湍流
    flame_height = 0.3 + bass * 0.7
    turbulence = 1.0 + rms * 4.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = 1.0 - row / max(1, rows - 1)  # 从底部向上
        noise = _perlin_like(nx * turbulence + t * 0.3, ny * 2.0 + t)
        height_factor = max(0.0, flame_height - ny * 1.2)
        val = noise * height_factor
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_water_ripple_audio(engine) -> Dict[str, float]:
    """水波纹（音频驱动波源数和速度）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    # 音频驱动：低频控制波源数，中频控制速度
    sources = max(1, int(1 + bass * 4))
    speed = 1.0 + mid * 3.0
    source_positions = []
    for i in range(sources):
        angle = 2 * math.pi * i / sources + t * 0.1
        sx = 0.5 + 0.3 * math.cos(angle); sy = 0.5 + 0.3 * math.sin(angle)
        source_positions.append((sx, sy))
    if len(engine._sorted_ids) > 512 and len(source_positions) > 64: source_positions = source_positions[::len(source_positions) // 64]  #  R193/P1-9：大网格对波源点列 stride 降采样，压逐设备内层循环
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        val = 0.0
        for sx, sy in source_positions:
            r = math.sqrt((nx - sx)**2 + (ny - sy)**2)
            val += math.sin(2 * math.pi * (r * 5.0 - t * speed)) * math.exp(-r * 2.0)
        weights[fid] = val / sources * 0.5 + 0.5
    return weights


def compute_matrix_standing_wave_audio(engine) -> Dict[str, float]:
    """驻波（音频驱动节点数）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    # 音频驱动：中频控制节点数，RMS控制幅度
    nodes_x = max(1, int(2 + mid * 6))
    nodes_y = max(1, int(2 + mid * 4))
    amplitude = 0.5 + rms * 0.5
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        # 驻波：空间正弦×时间正弦
        wx = math.sin(nx * nodes_x * math.pi) * math.cos(t * 2.0)
        wy = math.sin(ny * nodes_y * math.pi) * math.cos(t * 1.5)
        val = (wx * wy * amplitude) * 0.5 + 0.5
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_lissajous_audio(engine) -> Dict[str, float]:
    """李萨如（音频驱动频率比）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    high = min(1.0, (bands[5] + bands[6]) * engine.audio_amplitude) if len(bands) >= 7 else rms
    # 音频驱动：低频控制X频率，高频控制Y频率
    fx = 2.0 + bass * 3.0
    fy = 3.0 + high * 2.0
    phase = t * 0.5
    width = 0.05 + rms * 0.05
    # 生成李萨如曲线上的点
    curve_x = [0.5 + 0.45 * math.sin(fx * s + phase) for s in np.linspace(0, 2*math.pi, 200)]
    curve_y = [0.5 + 0.45 * math.sin(fy * s) for s in np.linspace(0, 2*math.pi, 200)]
    if len(engine._sorted_ids) > 512 and len(curve_x) > 64: st = len(curve_x) // 64; curve_x = curve_x[::st]; curve_y = curve_y[::st]  #  R193/P1-9：大网格对曲线点 stride 降采样，压 O(设备×200) 距离循环
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        min_dist = min(math.sqrt((nx - cx)**2 + (ny - cy)**2) for cx, cy in zip(curve_x, curve_y))
        weights[fid] = max(0.0, 1.0 - min_dist / width) if min_dist < width * 2 else 0.0
    return weights


def compute_matrix_moire_audio(engine) -> Dict[str, float]:
    """莫尔纹（音频驱动旋转）- 移植自v27"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    # 音频驱动：中频控制旋转速度，RMS控制条纹密度
    rotation_speed = 0.3 + mid * 1.5
    freq = 4.0 + rms * 6.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1) - 0.5
        ny = row / max(1, rows - 1) - 0.5
        # 两组旋转条纹的叠加
        angle1 = t * rotation_speed
        angle2 = -t * rotation_speed * 0.7
        x1 = nx * math.cos(angle1) - ny * math.sin(angle1)
        x2 = nx * math.cos(angle2) - ny * math.sin(angle2)
        v1 = math.sin(x1 * freq * 2 * math.pi)
        v2 = math.sin(x2 * freq * 2 * math.pi)
        val = (v1 * v2) * 0.5 + 0.5
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_rose_audio(engine) -> Dict[str, float]:
    """玫瑰线（音频驱动花瓣数）- 移植自v27

    R194/P1-9 numpy 化：300 曲线点先向量化，设备坐标 (N,1) 广播减
    (1,300) 后 `min(dist, axis=1)`，取代全场最大的 设备×300 Python 内层循环。
    容差契约：sin/cos 类，×255 量化后 ±1（见 tests/test_r194_matrix_perf.py）。
    边界无害性：`min_dist == width*3` 附近两分支值连续（均落回 max(0,·)=0）。
    """
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    # 音频驱动：中频控制花瓣数，RMS控制大小
    petals = max(2, int(3 + mid * 5))
    size = 0.3 + rms * 0.2
    width = 0.04 + rms * 0.04
    # 生成玫瑰线点（向量化）
    thetas = np.linspace(0, 2 * math.pi, 300)
    ripple = np.cos(petals * thetas + t * 0.5)
    rose_x = 0.5 + size * ripple * np.cos(thetas)
    rose_y = 0.5 + size * ripple * np.sin(thetas)
    nx = grid.norm_x_np.astype(np.float64, copy=False)
    ny = grid.norm_y_np.astype(np.float64, copy=False)
    dist = np.sqrt((nx[:, None] - rose_x[None, :]) ** 2 + (ny[:, None] - rose_y[None, :]) ** 2)
    min_dist = dist.min(axis=1)
    val = np.where(min_dist < width * 3,
                   np.maximum(0.0, 1.0 - min_dist / (width * 2)), 0.0)
    return dict(zip(engine._sorted_ids, val.tolist()))


def compute_matrix_fractal_audio(engine) -> Dict[str, float]:
    """分形噪声（音频驱动湍流）- 移植自v27

    R194/P1-9 numpy 化：4 层 `_perlin_like` → `_perlin_like_vec`（uint64
    显式回绕）。坐标用 col/row 的 **float64** 除法逐位复刻原 Python 路径
    （norm_x_np 是 float32，会挪动哈希的 int() 截断点），乘加均正确舍入
    → 哈希输入与输出逐位一致；val 逐层左结合累加与标量版同序。
    """
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    # 音频驱动：低频控制湍流强度，RMS控制速度
    turbulence = 1.0 + bass * 5.0
    speed = 0.5 + rms * 2.0
    nx = grid.col_np.astype(np.float64)
    nx /= float(max(1, cols - 1))  # 逐位复刻 col / max(1, cols-1)
    ny = grid.row_np.astype(np.float64)
    ny /= float(max(1, rows - 1))
    val = np.zeros(nx.shape, dtype=np.float64)
    amp = 1.0
    freq = turbulence
    for _ in range(4):  # 多层分形噪声叠加（与标量版同序左结合）
        val += _perlin_like_vec(nx * freq + t * speed, ny * freq + t * speed * 0.7) * amp
        amp *= 0.5
        freq *= 2.0
    np.clip(val, 0.0, 1.0, out=val)
    return dict(zip(engine._sorted_ids, val.tolist()))


def compute_matrix_kaleidoscope_audio(engine) -> Dict[str, float]:
    """万花筒（音频驱动对称度）- 移植自v27

    R194/P1-9 numpy 化：从 col/row 一步广播（float64，理由同 fractal——
    哈希 int() 截断点不能被 float32 挪动）。`%` 折叠用 np.mod（同为"取除数
    符号"语义）；atan2/cos/sin 属超越函数，1ulp 差异只在 int() 整数边界
    （概率 ~2^-50/样本）才会翻转哈希，golden 测试按逐位一致验收。
    """
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    # 音频驱动：中频控制对称数，RMS控制旋转速度
    symmetry = max(3, int(4 + mid * 8))
    rot_speed = 0.3 + rms * 1.5
    nx = grid.col_np.astype(np.float64)
    nx /= float(max(1, cols - 1))
    nx -= 0.5
    ny = grid.row_np.astype(np.float64)
    ny /= float(max(1, rows - 1))
    ny -= 0.5
    r = np.sqrt(nx * nx + ny * ny)
    angle = np.arctan2(ny, nx) + t * rot_speed
    sector_span = 2 * math.pi / symmetry
    sector_angle = np.mod(angle, sector_span)  # 折叠到基本扇区
    sector_angle = np.where(sector_angle > math.pi / symmetry,
                            sector_span - sector_angle, sector_angle)
    # 在扇区内计算花纹
    sx = r * np.cos(sector_angle)
    sy = r * np.sin(sector_angle)
    val = _perlin_like_vec(sx * 4.0 + t * 0.5, sy * 4.0 + t * 0.3) * 0.5 + 0.5
    return dict(zip(engine._sorted_ids, val.tolist()))


# ── v28 移植 ─────────────────────────────────────────────────────────────────

def compute_matrix_double_spiral_audio(engine) -> Dict[str, float]:
    """双螺旋（音频驱动速度）- 移植自v28

    R194/P1-9 numpy 化：从 norm 数组一步广播（float32 上溯 float64，
    与标量 float64 差 ≤2^-25 相对值）。sin/cos/atan2 类：×255 量化后 ±1
    容差（tests/test_r194_matrix_perf.py）。
    """
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    speed = (0.5 + bass * 3.0) * engine.speed
    width = 0.06 + rms * 0.04
    nx = grid.norm_x_np.astype(np.float64)
    nx -= 0.5
    ny = grid.norm_y_np.astype(np.float64)
    ny -= 0.5
    r = np.sqrt(nx * nx + ny * ny)
    angle = np.arctan2(ny, nx)
    # 螺旋1 / 螺旋2（反向）
    spiral1 = np.mod(angle - r * 4.0 - t * speed, 2 * math.pi)
    spiral2 = np.mod(angle + r * 4.0 + t * speed, 2 * math.pi)
    falloff = math.pi * width * 10
    w1 = np.maximum(0.0, 1.0 - np.abs(spiral1 - math.pi) / falloff)
    w2 = np.maximum(0.0, 1.0 - np.abs(spiral2 - math.pi) / falloff)
    vals = np.maximum(w1, w2)
    return dict(zip(engine._sorted_ids, vals.tolist()))


def compute_matrix_cross_wave_audio(engine) -> Dict[str, float]:
    """十字波（音频驱动幅度）- 移植自v28"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    amp = 0.5 + bass * 0.5
    speed = 1.0 + mid * 2.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        # 水平波
        wh = math.sin(nx * 4 * math.pi - t * speed) * amp
        # 垂直波
        wv = math.sin(ny * 4 * math.pi - t * speed * 0.8) * amp
        val = (wh + wv) / 2.0 * 0.5 + 0.5
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_lightning_audio(engine) -> Dict[str, float]:
    """闪电（音频驱动触发）- 移植自v28"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    is_beat = engine._get_is_beat()
    state = engine._effect_state
    if 'lightning_bolts' not in state:
        state['lightning_bolts'] = []
        state['lightning_decay'] = []
    # 节拍触发新闪电
    if is_beat or rms > 0.7:
        rng = random.Random(int(t * 100))
        start_x = rng.random()
        bolt = [(start_x, 0.0)]
        for _ in range(rows):
            last_x = bolt[-1][0]
            new_x = max(0.0, min(1.0, last_x + rng.uniform(-0.2, 0.2)))
            bolt.append((new_x, len(bolt) / rows))
        state['lightning_bolts'] = [bolt]
        state['lightning_decay'] = [0.0]
    # 衰减
    for k in range(len(state['lightning_decay'])):
        state['lightning_decay'][k] = min(1.0, state['lightning_decay'][k] + 0.05)
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        val = 0.0
        for k, bolt in enumerate(state['lightning_bolts']):
            decay = state['lightning_decay'][k] if k < len(state['lightning_decay']) else 1.0
            for bx, by in bolt:
                if abs(ny - by) < 0.1:
                    dist = abs(nx - bx)
                    val = max(val, max(0.0, 1.0 - dist * 10.0) * (1.0 - decay))
        weights[fid] = val
    return weights


def compute_matrix_beat_explosion_audio(engine) -> Dict[str, float]:
    """节拍爆炸（音频驱动）- 移植自v28"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    is_beat = engine._get_is_beat()
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    state = engine._effect_state
    if 'explosion_t' not in state:
        state['explosion_t'] = -10.0
        state['explosion_strength'] = 0.0
    if is_beat or bass > 0.6:
        state['explosion_t'] = t
        state['explosion_strength'] = 0.5 + bass * 0.5
    elapsed = t - state['explosion_t']
    ring_radius = elapsed * 2.0
    ring_width = 0.15
    strength = state['explosion_strength'] * max(0.0, 1.0 - elapsed * 0.8)
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1) - 0.5
        ny = row / max(1, rows - 1) - 0.5
        r = math.sqrt(nx**2 + ny**2)
        dist = abs(r - ring_radius)
        val = max(0.0, 1.0 - dist / ring_width) * strength if dist < ring_width else 0.0
        weights[fid] = val
    return weights


def compute_matrix_wave_stack_audio(engine) -> Dict[str, float]:
    """波浪叠加（音频驱动层数）- 移植自v28"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    # 音频驱动：各频段驱动各层波浪
    layers = min(8, len(bands))
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        val = 0.0
        for k in range(layers):
            band_energy = bands[k] * engine.audio_amplitude if k < len(bands) else 0.0
            freq = 1.0 + k * 0.7
            speed = 0.5 + k * 0.3
            phase = k * math.pi / layers
            wave = math.sin(nx * freq * 2 * math.pi + ny * freq * math.pi - t * speed + phase)
            val += wave * band_energy
        val = val / max(1, layers) * 0.5 + 0.5
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_fan_scan_audio(engine) -> Dict[str, float]:
    """扇形扫描（音频驱动角速度）- 移植自v28"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    # 音频驱动：低频控制扫描速度，RMS控制扇形宽度
    omega = (0.5 + bass * 3.0) * engine.speed
    fan_width = 0.2 + rms * 0.4  # 扇形半角（弧度）
    scan_angle = t * omega
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1) - 0.5
        ny = row / max(1, rows - 1) - 0.5
        angle = math.atan2(ny, nx)
        # 角度差（考虑循环）
        diff = abs((angle - scan_angle + math.pi) % (2 * math.pi) - math.pi)
        val = max(0.0, 1.0 - diff / fan_width) if diff < fan_width else 0.0
        weights[fid] = val
    return weights


def compute_matrix_water_spread_audio(engine) -> Dict[str, float]:
    """水纹扩散（音频驱动）- 移植自v28"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    is_beat = engine._get_is_beat()
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    state = engine._effect_state
    if 'ripples' not in state:
        state['ripples'] = []  # [(cx, cy, start_t, strength)]
    # 节拍触发新涟漪
    if is_beat or bass > 0.5:
        rng = random.Random(int(t * 50))
        cx = rng.uniform(0.2, 0.8)
        cy = rng.uniform(0.2, 0.8)
        state['ripples'].append((cx, cy, t, 0.5 + bass * 0.5))
    # 清理过期涟漪
    state['ripples'] = [(cx, cy, st, s) for cx, cy, st, s in state['ripples']
                        if t - st < 3.0]
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        val = 0.0
        for cx, cy, st, strength in state['ripples']:
            elapsed = t - st
            r = math.sqrt((nx - cx)**2 + (ny - cy)**2)
            ring_r = elapsed * 1.5
            dist = abs(r - ring_r)
            ring_amp = strength * max(0.0, 1.0 - elapsed * 0.5)
            val = max(val, max(0.0, 1.0 - dist * 8.0) * ring_amp)
        weights[fid] = val
    return weights


# ── v29 移植 ─────────────────────────────────────────────────────────────────

def compute_matrix_audio_diamond_v29(engine) -> Dict[str, float]:
    """音频响应菱形 - 移植自v29"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    is_beat = engine._get_is_beat()
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    state = engine._effect_state
    if 'diamond_rings' not in state:
        state['diamond_rings'] = []
    if is_beat or bass > 0.55:
        state['diamond_rings'].append({'t': t, 'strength': 0.5 + bass * 0.5})
    state['diamond_rings'] = [r for r in state['diamond_rings'] if t - r['t'] < 2.5]
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = abs(col / max(1, cols - 1) - 0.5)
        ny = abs(row / max(1, rows - 1) - 0.5)
        d = nx + ny
        val = 0.0
        for ring in state['diamond_rings']:
            elapsed = t - ring['t']
            ring_d = elapsed * 0.6
            dist = abs(d - ring_d)
            amp = ring['strength'] * max(0.0, 1.0 - elapsed * 0.6)
            val = max(val, max(0.0, 1.0 - dist * 10.0) * amp)
        weights[fid] = val
    return weights


def compute_matrix_audio_star_v29(engine) -> Dict[str, float]:
    """音频响应星形爆炸 - 移植自v29"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    is_beat = engine._get_is_beat()
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    state = engine._effect_state
    if 'star_bursts' not in state:
        state['star_bursts'] = []
    if is_beat or bass > 0.6:
        rng = random.Random(int(t * 100))
        state['star_bursts'].append({'t': t, 'strength': 0.6 + bass * 0.4, 'points': rng.randint(5, 8)})
    state['star_bursts'] = [b for b in state['star_bursts'] if t - b['t'] < 2.0]
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1) - 0.5
        ny = row / max(1, rows - 1) - 0.5
        r = math.sqrt(nx**2 + ny**2)
        angle = math.atan2(ny, nx)
        val = 0.0
        for burst in state['star_bursts']:
            elapsed = t - burst['t']
            pts = burst['points']
            ring_r = elapsed * 0.8
            # 星形：在角度方向上有突出
            star_r = ring_r * (0.7 + 0.3 * abs(math.cos(angle * pts / 2)))
            dist = abs(r - star_r)
            amp = burst['strength'] * max(0.0, 1.0 - elapsed * 0.7)
            val = max(val, max(0.0, 1.0 - dist * 8.0) * amp)
        weights[fid] = val
    return weights


def compute_matrix_rapid_fire_v29(engine) -> Dict[str, float]:
    """快速连发十字 - 移植自v29"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    # 快速连发：高中频触发十字扫描
    fire_rate = 1.0 + mid * 6.0
    phase = (t * fire_rate) % 1.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        # 水平扫描线
        h_dist = abs(ny - phase)
        # 垂直扫描线（相位偏移0.5）
        v_dist = abs(nx - ((phase + 0.5) % 1.0))
        # R131：`* (0.5 + rms * 0.5)` 在无音频（rms=0）时把峰值**恰好压到 0.5**
        #   = 默认 threshold 判定 `w >= threshold` 永远只是临界、模式永远不出光。
        #   改为"无音频时不衰减"（有音频仍按 rms 提亮），与其它模式的语义一致。
        gain = 0.5 + (rms * 0.5 if rms > 0 else 1.0)
        val = max(
            max(0.0, 1.0 - h_dist * 8.0),
            max(0.0, 1.0 - v_dist * 8.0)
        ) * min(1.0, gain)
        weights[fid] = val
    return weights


# ── v30/v31/v32 移植 ─────────────────────────────────────────────────────────

def compute_matrix_scan_line_audio(engine) -> Dict[str, float]:
    """扫描线（音频驱动速度和宽度）- 移植自v30/v31/v32"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    # 音频驱动：低频控制速度，中频控制宽度
    speed = (0.5 + bass * 3.0) * engine.speed
    scan_width = 0.1 + mid * 0.3
    scan_pos = (t * speed) % 1.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        ny = row / max(1, rows - 1)
        dist = abs(ny - scan_pos)
        # 循环处理
        dist = min(dist, 1.0 - dist)
        val = max(0.0, 1.0 - dist / scan_width) if dist < scan_width else 0.0
        weights[fid] = val
    return weights


def compute_matrix_checker_breath_audio(engine) -> Dict[str, float]:
    """棋盘呼吸（音频驱动）- 移植自v30/v31/v32"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    # 音频驱动：低频控制呼吸速度，RMS控制幅度
    breath_speed = 0.5 + bass * 2.0
    breath_amp = 0.3 + rms * 0.7
    breath = math.sin(t * breath_speed * 2 * math.pi) * 0.5 + 0.5
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        checker = (col + row) % 2
        if checker == 0:
            val = breath * breath_amp
        else:
            val = (1.0 - breath) * breath_amp
        weights[fid] = val
    return weights


def compute_matrix_row_wave_audio(engine) -> Dict[str, float]:
    """行波（音频驱动）- 移植自v30/v31/v32"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    # 各频段驱动各行
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        ny = row / max(1, rows - 1)
        nx = col / max(1, cols - 1)
        # 行索引对应频段
        band_idx = min(len(bands) - 1, int(ny * len(bands))) if bands else 0
        band_energy = bands[band_idx] * engine.audio_amplitude if band_idx < len(bands) else rms
        # 行内波动
        wave = math.sin(nx * 2 * math.pi * 2.0 - t * (1.0 + band_energy * 3.0))
        val = wave * band_energy * 0.5 + band_energy * 0.5
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_double_helix_audio(engine) -> Dict[str, float]:
    """双螺旋（音频驱动）- 移植自v30/v31/v32"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    speed = (1.0 + bass * 3.0) * engine.speed
    width = 0.06 + mid * 0.06
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        norm_row = row / max(1, rows - 1)
        norm_col = col / max(1, cols - 1)
        # 螺旋1
        helix1_x = 0.5 + 0.35 * math.sin(norm_row * 4.0 * math.pi - t * speed)
        # 螺旋2（相位偏移π）
        helix2_x = 0.5 + 0.35 * math.sin(norm_row * 4.0 * math.pi - t * speed + math.pi)
        dist1 = abs(norm_col - helix1_x)
        dist2 = abs(norm_col - helix2_x)
        w1 = max(0.0, 1.0 - dist1 / width) if dist1 < width else 0.0
        w2 = max(0.0, 1.0 - dist2 / width) if dist2 < width else 0.0
        # 横档
        bar_period = max(2, rows // 4)
        if row % bar_period == 0:
            bar_left = min(helix1_x, helix2_x)
            bar_right = max(helix1_x, helix2_x)
            if bar_left <= norm_col <= bar_right:
                w1 = max(w1, 0.5 * rms)
        weights[fid] = max(w1, w2)
    return weights


def compute_matrix_surge_audio(engine) -> Dict[str, float]:
    """涌浪（音频驱动）- 移植自v30/v31/v32"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    # 涌浪：多层波叠加，向一侧传播
    speed = (0.5 + bass * 2.0) * engine.speed
    n_layers = 3
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        val = 0.0
        for k in range(n_layers):
            phase_offset = k * 0.3
            freq = 1.5 + k * 0.5
            layer_amp = (1.0 - k * 0.25) * (0.5 + mid * 0.5)
            wave = math.sin(nx * freq * 2 * math.pi - t * speed + phase_offset * 2 * math.pi)
            val += wave * layer_amp
        val = val / n_layers * 0.5 + 0.5
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_noise_audio(engine) -> Dict[str, float]:
    """噪声（音频驱动）- 移植自v30/v31/v32"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    # 音频驱动：低频控制噪声速度，RMS控制强度
    speed = 0.3 + bass * 2.0
    scale = 2.0 + rms * 4.0
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        val = _perlin_like(nx * scale + t * speed, ny * scale + t * speed * 0.7)
        weights[fid] = max(0.0, min(1.0, val))
    return weights


def compute_matrix_rotate_scan_audio(engine) -> Dict[str, float]:
    """旋转扫描（音频驱动）- 移植自v30/v31/v32"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    mid = min(1.0, (bands[3] + bands[4]) * engine.audio_amplitude) if len(bands) >= 5 else rms
    omega = (0.3 + bass * 2.0) * engine.speed
    scan_width = 0.15 + mid * 0.25
    scan_angle = t * omega
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1) - 0.5
        ny = row / max(1, rows - 1) - 0.5
        angle = math.atan2(ny, nx)
        diff = abs((angle - scan_angle + math.pi) % (2 * math.pi) - math.pi)
        val = max(0.0, 1.0 - diff / scan_width) if diff < scan_width else 0.0
        weights[fid] = val
    return weights


def compute_matrix_fractal_v30_audio(engine) -> Dict[str, float]:
    """分形（音频驱动）- 移植自v30"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    bass = min(1.0, (bands[0] + bands[1]) * engine.audio_amplitude) if len(bands) >= 2 else rms
    # 简化分形：Julia集近似
    zoom = 1.5 + bass * 1.0
    cx = -0.7 + math.sin(t * 0.2) * 0.3
    cy = 0.27 + math.cos(t * 0.15) * 0.2
    max_iter = 12
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = (col / max(1, cols - 1) - 0.5) * zoom * 2
        ny = (row / max(1, rows - 1) - 0.5) * zoom * 2
        zx, zy = nx, ny
        iteration = 0
        for _ in range(max_iter):
            if zx * zx + zy * zy > 4.0:
                break
            zx, zy = zx * zx - zy * zy + cx, 2 * zx * zy + cy
            iteration += 1
        val = iteration / max_iter
        weights[fid] = val
    return weights


def compute_matrix_cellular_audio(engine) -> Dict[str, float]:
    """细胞自动机（音频驱动）- 移植自v30"""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    bands = engine._get_audio_bands()
    rms = engine._get_audio_rms()
    t = engine._t
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    is_beat = engine._get_is_beat()
    state = engine._effect_state
    if 'cell_grid' not in state or state.get('cell_cols') != cols or state.get('cell_rows') != rows:
        # 初始化随机细胞
        rng = random.Random(42)
        state['cell_grid'] = [[rng.random() > 0.5 for _ in range(cols)] for _ in range(rows)]
        state['cell_cols'] = cols
        state['cell_rows'] = rows
        state['cell_timer'] = 0.0
    # 节拍时更新细胞
    update_interval = max(0.1, 0.5 - rms * 0.4)
    state['cell_timer'] = state.get('cell_timer', 0.0) + 0.025
    if state['cell_timer'] >= update_interval or is_beat:
        state['cell_timer'] = 0.0
        old_grid = state['cell_grid']
        new_grid = [[False] * cols for _ in range(rows)]
        for r in range(rows):
            for c in range(cols):
                neighbors = sum(
                    old_grid[(r + dr) % rows][(c + dc) % cols]
                    for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                    if (dr, dc) != (0, 0)
                )
                alive = old_grid[r][c]
                if alive:
                    new_grid[r][c] = neighbors in (2, 3)
                else:
                    new_grid[r][c] = neighbors == 3
        state['cell_grid'] = new_grid
    weights = {}
    cell_grid = state['cell_grid']
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        r = min(rows - 1, row)
        c = min(cols - 1, col)
        val = 1.0 if cell_grid[r][c] else 0.0
        weights[fid] = val * (0.5 + rms * 0.5)
    return weights


# ─────────────────────────────────────────────────────────────────────────────
#  注入函数：将所有扩展效果注入到MatrixPixelEngine
# ─────────────────────────────────────────────────────────────────────────────

# 效果函数映射表
_EXT_COMPUTE_MAP = {
    MODE_MATRIX_PLASMA_AUDIO:        compute_matrix_plasma_audio,
    MODE_MATRIX_INTERFERENCE_AUDIO:  compute_matrix_interference_audio,
    MODE_MATRIX_VORTEX_AUDIO:        compute_matrix_vortex_audio,
    MODE_MATRIX_CHECKER_WAVE_AUDIO:  compute_matrix_checker_wave_audio,
    MODE_MATRIX_DUAL_SOURCE_AUDIO:   compute_matrix_dual_source_audio,
    MODE_MATRIX_RADIAL_WAVE_AUDIO:   compute_matrix_radial_wave_audio,
    MODE_MATRIX_DIAMOND_AUDIO:       compute_matrix_diamond_audio,
    MODE_MATRIX_FLAME_AUDIO:         compute_matrix_flame_audio,
    MODE_MATRIX_WATER_RIPPLE_AUDIO:  compute_matrix_water_ripple_audio,
    MODE_MATRIX_STANDING_WAVE_AUDIO: compute_matrix_standing_wave_audio,
    MODE_MATRIX_LISSAJOUS_AUDIO:     compute_matrix_lissajous_audio,
    MODE_MATRIX_MOIRE_AUDIO:         compute_matrix_moire_audio,
    MODE_MATRIX_ROSE_AUDIO:          compute_matrix_rose_audio,
    MODE_MATRIX_FRACTAL_AUDIO:       compute_matrix_fractal_audio,
    MODE_MATRIX_KALEIDOSCOPE_AUDIO:  compute_matrix_kaleidoscope_audio,
    MODE_MATRIX_DOUBLE_SPIRAL_AUDIO:  compute_matrix_double_spiral_audio,
    MODE_MATRIX_CROSS_WAVE_AUDIO:     compute_matrix_cross_wave_audio,
    MODE_MATRIX_LIGHTNING_AUDIO:      compute_matrix_lightning_audio,
    MODE_MATRIX_BEAT_EXPLOSION_AUDIO: compute_matrix_beat_explosion_audio,
    MODE_MATRIX_WAVE_STACK_AUDIO:     compute_matrix_wave_stack_audio,
    MODE_MATRIX_FAN_SCAN_AUDIO:       compute_matrix_fan_scan_audio,
    MODE_MATRIX_WATER_SPREAD_AUDIO:   compute_matrix_water_spread_audio,
    MODE_MATRIX_AUDIO_DIAMOND_V29:    compute_matrix_audio_diamond_v29,
    MODE_MATRIX_AUDIO_STAR_V29:       compute_matrix_audio_star_v29,
    MODE_MATRIX_RAPID_FIRE_V29:       compute_matrix_rapid_fire_v29,
    MODE_MATRIX_SCAN_LINE_AUDIO:      compute_matrix_scan_line_audio,
    MODE_MATRIX_CHECKER_BREATH_AUDIO: compute_matrix_checker_breath_audio,
    MODE_MATRIX_ROW_WAVE_AUDIO:       compute_matrix_row_wave_audio,
    MODE_MATRIX_DOUBLE_HELIX_AUDIO:   compute_matrix_double_helix_audio,
    MODE_MATRIX_SURGE_AUDIO:          compute_matrix_surge_audio,
    MODE_MATRIX_NOISE_AUDIO:          compute_matrix_noise_audio,
    MODE_MATRIX_ROTATE_SCAN_AUDIO:    compute_matrix_rotate_scan_audio,
    MODE_MATRIX_FRACTAL_V30_AUDIO:    compute_matrix_fractal_v30_audio,
    MODE_MATRIX_CELLULAR_AUDIO:       compute_matrix_cellular_audio,
}


# ─────────────────────────────────────────────────────────────────────────────
#  v33 极大量音频识别动态图案：频段/节拍/瞬态/人声/声像识别响应
# ─────────────────────────────────────────────────────────────────────────────
def _compute_matrix_audio_recognition_v33(engine, mode_id: str, variant: int) -> Dict[str, float]:
    """通用音频识别动态图案计算器。

    该函数把频段能量、RMS、低频节拍、中频人声感、高频瞬态感映射到矩阵空间图案。
    大量 mode 共享稳定计算核心，通过 variant 产生不同动态几何、扫描方向和触发特性。
    """
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    try:
        bands = list(engine._get_audio_bands())
    except Exception:
        bands = []
    if not bands:
        bands = [0.0] * 8
    while len(bands) < 8:
        bands.append(bands[-1] if bands else 0.0)
    amp = float(getattr(engine, 'audio_amplitude', 1.0))
    rms = min(1.0, max(0.0, float(getattr(engine, '_get_audio_rms', lambda: 0.0)()) * amp))
    bass = min(1.0, (bands[0] + bands[1]) * 0.65 * amp)
    low_mid = min(1.0, (bands[2] + bands[3]) * 0.60 * amp)
    vocal = min(1.0, (bands[3] + bands[4] + bands[5]) * 0.42 * amp)
    treble = min(1.0, (bands[6] + bands[7]) * 0.75 * amp)
    transient = min(1.0, max(0.0, treble * 0.75 + bass * 0.35 + rms * 0.25))
    energy = min(1.0, rms * 0.5 + bass * 0.25 + vocal * 0.15 + treble * 0.10)
    t = float(engine._t)
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    weights: Dict[str, float] = {}
    family = variant % 16
    spin = (variant % 7 + 1) * 0.35
    phase = t * (0.65 + energy * 2.25 + (variant % 5) * 0.13)
    density = 2.0 + (variant % 9) * 0.55 + vocal * 3.0
    threshold_bias = 0.10 * ((variant % 5) - 2)

    def clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        cx, cy = nx - 0.5, ny - 0.5
        r = math.sqrt(cx * cx + cy * cy)
        a = math.atan2(cy, cx)
        band_idx = int((nx * 0.65 + ny * 0.35) * (len(bands) - 1))
        local_band = min(1.0, bands[max(0, min(len(bands) - 1, band_idx))] * amp)

        if family == 0:      # 低频径向爆点识别
            v = math.sin((r * (10.0 + bass * 8.0) - phase * (1.0 + bass)) * math.pi)
            val = (v * 0.5 + 0.5) * (0.35 + bass * 0.95)
        elif family == 1:    # 中频人声扫描栅格
            v = math.sin((nx * density + ny * (1.5 + vocal * 3.0) + phase) * math.pi)
            val = (v * 0.5 + 0.5) * (0.25 + vocal)
        elif family == 2:    # 高频瞬态星点
            seed = int(t * (5 + variant % 6)) + variant * 31
            sparkle = 1.0 if _hash2d(col, row, seed) < (0.08 + transient * 0.32) else 0.0
            val = sparkle * (0.3 + transient * 0.9)
        elif family == 3:    # 频谱阶梯条纹
            level = local_band * (0.6 + energy)
            bar = 1.0 if (1.0 - ny) <= level else max(0.0, 1.0 - abs((1.0 - ny) - level) * rows)
            val = bar * (0.35 + local_band)
        elif family == 4:    # 声像旋涡
            v = math.sin((a * (2.0 + variant % 6) + r * density * 4.0 - phase * spin))
            val = (v * 0.5 + 0.5) * (0.25 + energy + bass * 0.35)
        elif family == 5:    # 节拍十字门控
            cross = max(0.0, 1.0 - min(abs(nx - 0.5), abs(ny - 0.5)) * (8 + bass * 10))
            pulse = 0.5 + 0.5 * math.sin(phase * math.pi * (1.5 + bass))
            val = cross * (0.25 + bass) * (0.45 + pulse)
        elif family == 6:    # 人声唇形开合
            mouth = 1.0 - abs((abs(cy) * (2.2 + vocal * 2.8)) - (0.25 + vocal * 0.55))
            gate = 1.0 - abs(cx) * (1.8 + low_mid * 2.0)
            val = clamp(mouth) * clamp(gate) * (0.35 + vocal)
        elif family == 7:    # 鼓点方块扩张
            cell = (int(nx * (4 + variant % 6)) + int(ny * (4 + variant % 6))) % 2
            wave = math.sin((r * (8 + bass * 12) - phase * 2.0) * math.pi)
            val = (0.45 + 0.55 * cell) * (wave * 0.5 + 0.5) * (0.25 + bass)
        elif family == 8:    # 瞬态闪电折线
            line = abs(nx - (0.5 + 0.23 * math.sin(ny * 13.0 + phase * 3.0 + variant)))
            val = clamp(1.0 - line * (18 + treble * 24)) * (0.25 + transient)
        elif family == 9:    # 频带同心环
            target = (local_band * 0.75 + 0.08 * (variant % 4)) % 0.75
            val = clamp(1.0 - abs(r - target) * (10 + energy * 12)) * (0.25 + local_band + rms * 0.4)
        elif family == 10:   # 左右声像假想扫描
            sweep = (0.5 + 0.5 * math.sin(phase + (bass - treble) * 2.0))
            val = clamp(1.0 - abs(nx - sweep) * (6 + vocal * 10)) * (0.3 + energy)
        elif family == 11:   # 音色棋盘解码
            checker = ((col + row + int(phase * (1 + variant % 3))) % 2)
            tone = bass * (1 - nx) + vocal * (1 - abs(nx - 0.5) * 2) + treble * nx
            val = (0.35 + 0.65 * checker) * tone
        elif family == 12:   # 短音粒子喷泉
            fountain = clamp(1.0 - abs(nx - 0.5) * (3 + treble * 5)) * clamp(1.0 - ny + local_band)
            grains = 1.0 if _hash2d(col, row, int(t * 12) + variant) < (0.15 + transient * 0.35) else 0.35
            val = fountain * grains * (0.25 + energy)
        elif family == 13:   # 旋转频谱扇区
            sector = (a / (2 * math.pi) + 0.5 + phase * 0.08) % 1.0
            b = bands[int(sector * (len(bands) - 1))] * amp
            val = clamp(1.0 - abs(r - (0.15 + b * 0.6)) * (8 + b * 10)) * (0.3 + min(1.0, b))
        elif family == 14:   # 低中高三角能量场
            p1 = clamp(1.0 - math.sqrt((nx - 0.15) ** 2 + (ny - 0.82) ** 2) * (3.8 + bass * 4)) * bass
            p2 = clamp(1.0 - math.sqrt((nx - 0.50) ** 2 + (ny - 0.18) ** 2) * (3.8 + vocal * 4)) * vocal
            p3 = clamp(1.0 - math.sqrt((nx - 0.85) ** 2 + (ny - 0.82) ** 2) * (3.8 + treble * 4)) * treble
            val = p1 + p2 + p3
        else:                # 复合音频指纹噪声云
            n = _perlin_like((nx + phase * 0.08) * (4 + variant % 8), (ny - phase * 0.05) * (4 + variant % 6))
            wave = 0.5 + 0.5 * math.sin((nx * bass + ny * vocal + r * treble) * 12.0 + phase)
            val = (0.45 * n + 0.55 * wave) * (0.25 + energy + local_band * 0.4)
        weights[fid] = clamp(val + threshold_bias)
    return weights


_AUDIO_RECOGNITION_MODE_GROUPS_V33 = {
    "音频识别-节拍鼓点": [
        ("matrix_ar_kick_bloom", "低频鼓点花开"), ("matrix_ar_kick_tunnel", "低频鼓点隧道"),
        ("matrix_ar_snare_cross", "军鼓十字识别"), ("matrix_ar_hat_spark", "镲片星火识别"),
        ("matrix_ar_beat_gate", "节拍门控方阵"), ("matrix_ar_drop_flash", "Drop爆闪识别"),
        ("matrix_ar_groove_blocks", "律动方块识别"), ("matrix_ar_rhythm_ladder", "节奏阶梯识别"),
        ("matrix_ar_pulse_core", "脉冲核心识别"), ("matrix_ar_drum_orbit", "鼓组轨道识别"),
    ],
    "音频识别-频段频谱": [
        ("matrix_ar_sub_columns", "超低频柱阵"), ("matrix_ar_bass_ridge", "低频山脊"),
        ("matrix_ar_lowmid_grid", "低中频网格"), ("matrix_ar_mid_ribbons", "中频缎带"),
        ("matrix_ar_vocal_bars", "人声频段柱"), ("matrix_ar_presence_scan", "存在感扫描"),
        ("matrix_ar_treble_rain", "高频雨线"), ("matrix_ar_air_sparks", "空气感星点"),
        ("matrix_ar_full_eq_mosaic", "全频均衡马赛克"), ("matrix_ar_spectrum_fan", "频谱扇形识别"),
    ],
    "音频识别-人声旋律": [
        ("matrix_ar_vocal_mouth", "人声唇形开合"), ("matrix_ar_vocal_halo", "人声光环"),
        ("matrix_ar_melody_snake", "旋律游蛇"), ("matrix_ar_note_ripple", "音符涟漪"),
        ("matrix_ar_phrase_wave", "乐句波门"), ("matrix_ar_chord_bloom", "和弦花冠"),
        ("matrix_ar_lead_spiral", "主旋律螺旋"), ("matrix_ar_call_response", "呼应扫描"),
        ("matrix_ar_vowel_grid", "元音网格"), ("matrix_ar_harmony_lattice", "和声晶格"),
    ],
    "音频识别-瞬态粒子": [
        ("matrix_ar_transient_stars", "瞬态星爆"), ("matrix_ar_click_pixels", "点击粒子识别"),
        ("matrix_ar_attack_needles", "起音针阵"), ("matrix_ar_noise_comet", "噪声彗尾"),
        ("matrix_ar_spark_fountain", "星火喷泉"), ("matrix_ar_glitch_burst", "毛刺爆点"),
        ("matrix_ar_shimmer_cloud", "闪烁云"), ("matrix_ar_crash_sheet", "Crash片状识别"),
        ("matrix_ar_tick_train", "Tick列车"), ("matrix_ar_microburst", "微爆粒子"),
    ],
    "音频识别-空间声像": [
        ("matrix_ar_pan_sweep", "声像左右扫"), ("matrix_ar_stereo_gate", "立体门阵"),
        ("matrix_ar_center_vocal", "中央人声锁定"), ("matrix_ar_side_spark", "侧向星点"),
        ("matrix_ar_depth_tunnel", "深度隧道"), ("matrix_ar_width_breathe", "宽度呼吸"),
        ("matrix_ar_orbit_meter", "轨道声像表"), ("matrix_ar_phase_warp", "相位扭曲"),
        ("matrix_ar_echo_ring", "回声环识别"), ("matrix_ar_reverb_mist", "混响雾场"),
    ],
    "音频识别-几何图案": [
        ("matrix_ar_tri_energy", "三角能量场"), ("matrix_ar_hex_pulse", "六边脉冲"),
        ("matrix_ar_diamond_meter", "菱形音量计"), ("matrix_ar_circle_bins", "环形频段"),
        ("matrix_ar_cross_meter", "十字能量计"), ("matrix_ar_xray_wave", "X光波纹"),
        ("matrix_ar_spiral_bins", "螺旋分频"), ("matrix_ar_checker_tone", "棋盘音色识别"),
        ("matrix_ar_barcode_fft", "条码频谱"), ("matrix_ar_radar_audio", "雷达音频识别"),
    ],
    "音频识别-舞台动态": [
        ("matrix_ar_crowd_jump", "全场跳动识别"), ("matrix_ar_chorus_wall", "副歌能量墙"),
        ("matrix_ar_build_riser", "Build上升识别"), ("matrix_ar_break_void", "Break留白识别"),
        ("matrix_ar_drop_tunnel", "Drop隧道"), ("matrix_ar_strobe_fan", "频闪扇面"),
        ("matrix_ar_laser_grid", "激光网格识别"), ("matrix_ar_wave_curtain", "声浪幕布"),
        ("matrix_ar_stage_sweep", "舞台扫光识别"), ("matrix_ar_final_blast", "终止爆点识别"),
    ],
    "音频识别-复合纹理": [
        ("matrix_ar_plasma_voice", "人声等离子"), ("matrix_ar_bass_fractal", "低频分形"),
        ("matrix_ar_treble_lace", "高频蕾丝"), ("matrix_ar_noise_nebula", "噪声星云"),
        ("matrix_ar_grain_field", "颗粒场识别"), ("matrix_ar_tone_cloud", "音色云图"),
        ("matrix_ar_energy_topography", "能量地形"), ("matrix_ar_spectral_cells", "频谱细胞"),
        ("matrix_ar_moire_voice", "人声莫尔纹"), ("matrix_ar_fingerprint", "音频指纹图"),
    ],
}

_AUDIO_RECOGNITION_MODES_V33: List[Tuple[str, str]] = []
for _group, _items in _AUDIO_RECOGNITION_MODE_GROUPS_V33.items():
    _display_items = [(mode_id, f"音频识别·{label}") for mode_id, label in _items]
    PIXEL_MODES_EXT.setdefault(_group, []).extend(_display_items)
    _AUDIO_RECOGNITION_MODES_V33.extend(_items)

_EXT_COMPUTE_MAP.update({
    mode_id: (lambda engine, m=mode_id, v=idx: _compute_matrix_audio_recognition_v33(engine, m, v))
    for idx, (mode_id, _label) in enumerate(_AUDIO_RECOGNITION_MODES_V33)
})


# ─────────────────────────────────────────────────────────────────────────────
#  v34 大量规则几何音频识别空间联动：三角 / 菱形 / 点阵 / 斜方波 / 空间波
# ─────────────────────────────────────────────────────────────────────────────
def _compute_matrix_ordered_space_audio_v34(engine, mode_id: str, variant: int) -> Dict[str, float]:
    """规则几何音频识别空间效果计算器。

    设计目标不是随机噪声，而是有秩序的空间点缀：斜向方波、三角格、菱形格、
    规则点阵、阶梯斜波、回声菱形、三角箭头、菱形雨等。低频控制尺度和推进，
    中频/人声控制形体填充，高频/瞬态控制点缀闪烁，整体能量控制可见度。
    """
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}

    try:
        bands = list(engine._get_audio_bands())
    except Exception:
        bands = []
    if not bands:
        bands = [0.0] * 8
    while len(bands) < 8:
        bands.append(bands[-1] if bands else 0.0)

    amp = float(getattr(engine, 'audio_amplitude', 1.0))
    rms = min(1.0, max(0.0, float(getattr(engine, '_get_audio_rms', lambda: 0.0)()) * amp))
    bass = min(1.0, (bands[0] + bands[1]) * 0.68 * amp)
    low_mid = min(1.0, (bands[2] + bands[3]) * 0.58 * amp)
    vocal = min(1.0, (bands[3] + bands[4] + bands[5]) * 0.40 * amp)
    treble = min(1.0, (bands[6] + bands[7]) * 0.72 * amp)
    transient = min(1.0, max(0.0, treble * 0.68 + bass * 0.22 + rms * 0.30))
    energy = min(1.0, rms * 0.48 + bass * 0.24 + vocal * 0.18 + treble * 0.10)

    t = float(engine._t)
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    family = variant % 24
    angle_family = variant % 8
    slope = [0.55, 0.75, 1.00, 1.25, -0.65, -0.90, -1.15, -1.45][angle_family]
    scale = 3.0 + (variant % 9) * 0.42 + bass * 2.0
    beat_phase = t * (0.55 + bass * 2.25 + energy * 1.15 + (variant % 5) * 0.08)
    diag_phase = beat_phase + variant * 0.173
    lattice_shift = int(beat_phase * (1.0 + bass * 2.0))
    dot_period = max(2, int(3 + (variant % 6)))
    dot_size = 0.09 + treble * 0.10 + (variant % 3) * 0.015

    def clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    def tri_wave(x: float) -> float:
        x = x % 1.0
        return 1.0 - abs(x * 2.0 - 1.0)

    def square_wave(x: float, duty: float = 0.50) -> float:
        return 1.0 if (x % 1.0) < duty else 0.0

    def soft_line(distance: float, width: float) -> float:
        return clamp(1.0 - abs(distance) / max(1e-5, width))

    def diamond(cx: float, cy: float, nx: float, ny: float, radius: float, width: float) -> float:
        d = abs(nx - cx) + abs(ny - cy)
        return soft_line(d - radius, width)

    def triangle_outline(nx: float, ny: float, cx: float, cy: float, radius: float, width: float, flip: bool = False) -> float:
        # 等腰三角近似：三条边的软线叠加。flip=True 时倒三角。
        yy = (cy - ny) if flip else (ny - cy)
        left = abs((nx - cx) + yy * 0.58) - radius * 0.50
        right = abs((nx - cx) - yy * 0.58) - radius * 0.50
        base_y = yy + radius * 0.46
        side = max(soft_line(left, width), soft_line(right, width)) * clamp(1.0 - abs(yy) / max(radius, 1e-5))
        base = soft_line(base_y, width) * clamp(1.0 - abs(nx - cx) / max(radius * 0.62, 1e-5))
        return max(side, base)

    def regular_dot(col: int, row: int, nx: float, ny: float, period: int, phase_i: int = 0) -> float:
        gx = (col + phase_i) % period
        gy = (row + phase_i * 2) % period
        near = min(gx, period - gx, gy, period - gy) / max(1, period)
        core = 1.0 if (gx == 0 and gy == 0) else max(0.0, 1.0 - near * 5.5)
        return core

    weights: Dict[str, float] = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        cx, cy = nx - 0.5, ny - 0.5
        r = math.sqrt(cx * cx + cy * cy)
        a = math.atan2(cy, cx)
        band_idx = int(clamp(nx * 0.55 + ny * 0.45) * (len(bands) - 1))
        local = min(1.0, bands[max(0, min(len(bands) - 1, band_idx))] * amp)
        diag = nx * scale + ny * scale * slope + diag_phase
        anti = nx * scale - ny * scale * slope - diag_phase * 0.72
        manhattan = abs(cx) + abs(cy)

        if family == 0:      # 斜方波空间带：低频推动斜向硬边
            duty = 0.34 + bass * 0.22
            gate = square_wave(diag, duty)
            edge = tri_wave(diag) ** (1.8 + bass * 1.6)
            val = max(gate * 0.78, edge * 0.45) * (0.28 + bass + energy * 0.35)
        elif family == 1:    # 双向斜方波交错：菱形节奏网
            g1 = square_wave(diag, 0.42 + vocal * 0.12)
            g2 = square_wave(anti, 0.38 + bass * 0.12)
            val = (0.38 * g1 + 0.62 * g1 * g2) * (0.30 + energy + local * 0.25)
        elif family == 2:    # 斜向阶梯波：像楼梯一样随鼓点推进
            step = int((diag + bass * 2.0) * 3.0) % 4
            val = (1.0 if step in (0, 1) else 0.22) * tri_wave(diag * 0.50) * (0.32 + bass + low_mid * 0.4)
        elif family == 3:    # 三角点阵：规律三角装饰点随瞬态闪
            period = dot_period + int(bass * 2)
            tri_cell = ((col + row * 2 + lattice_shift) % period == 0) or ((col * 2 - row + lattice_shift) % (period + 1) == 0)
            sparkle = 1.0 if tri_cell else 0.18 * regular_dot(col, row, nx, ny, period)
            val = sparkle * (0.25 + transient * 0.85 + vocal * 0.25)
        elif family == 4:    # 菱形点缀网：规则 diamond 节点
            period = dot_period + 1
            dgrid = ((abs((col + lattice_shift) % period - period / 2) + abs((row - lattice_shift) % period - period / 2)) < 1.15)
            val = (1.0 if dgrid else 0.0) * (0.30 + local + treble * 0.55)
        elif family == 5:    # 菱形呼吸外框：中频扩大，低频推进
            radius = 0.16 + bass * 0.20 + 0.04 * math.sin(beat_phase * math.pi)
            val = diamond(0.5, 0.5, nx, ny, radius, 0.035 + vocal * 0.045) * (0.34 + energy + vocal * 0.4)
        elif family == 6:    # 多重菱形回声：规则层层回声
            rings = 0.0
            for k in range(3):
                rr = 0.12 + k * (0.13 + bass * 0.03) + 0.04 * tri_wave(beat_phase * 0.4 + k * 0.2)
                rings = max(rings, soft_line(manhattan - rr, 0.025 + treble * 0.025))
            val = rings * (0.28 + energy + local * 0.45)
        elif family == 7:    # 三角箭头波：斜向三角箭头列
            cellx = (nx * (4 + variant % 5) + beat_phase * 0.35) % 1.0
            celly = (ny * (3 + variant % 4) + beat_phase * 0.18) % 1.0
            tri = triangle_outline(cellx, celly, 0.5, 0.52, 0.55, 0.08 + bass * 0.04, flip=(variant % 2 == 0))
            val = tri * (0.28 + bass + vocal * 0.35)
        elif family == 8:    # 倒三角低频门阵
            tri = triangle_outline(nx, ny, 0.5, 0.5, 0.70 * (0.5 + bass), 0.035 + bass * 0.04, flip=True)
            gate = square_wave(diag * 0.75, 0.48)
            val = tri * (0.45 + 0.55 * gate) * (0.30 + bass + energy * 0.3)
        elif family == 9:    # 菱形雨：斜向落下的规则菱形点
            period = dot_period + 2
            rx = (col + int(beat_phase * (1 + bass * 4))) % period
            ry = (row + int(beat_phase * (2 + treble * 4))) % period
            rain = 1.0 if abs(rx - period // 2) + abs(ry - period // 2) <= 1 else 0.0
            val = rain * (0.30 + transient + local * 0.35)
        elif family == 10:   # 斜向三角镶嵌：规则 tessellation
            cell = (int((nx + ny * 0.5 + beat_phase * 0.05) * (5 + variant % 6)) + int(ny * (4 + variant % 5))) % 3
            edge = tri_wave(diag * 0.55)
            val = (0.24 + 0.38 * cell) * (edge ** 2.0) * (0.28 + vocal + energy * 0.35)
        elif family == 11:   # 菱形棋盘：人声填充、低频翻转
            checker = (int((nx + beat_phase * 0.06) * (5 + variant % 6)) + int((ny - beat_phase * 0.04) * (5 + variant % 6))) % 2
            d = tri_wave((abs(cx) + abs(cy)) * (4.5 + bass * 4.0) - beat_phase * 0.28)
            val = (0.30 + 0.70 * checker) * d * (0.25 + vocal + bass * 0.35)
        elif family == 12:   # 规则星点点缀：非随机、有网格呼吸
            period = dot_period + int(vocal * 3)
            dot = regular_dot(col, row, nx, ny, period, lattice_shift)
            breath = 0.55 + 0.45 * math.sin((col + row) * 0.33 + beat_phase * math.pi)
            val = dot * breath * (0.25 + treble + rms * 0.35)
        elif family == 13:   # 三频三角岛：低中高三个三角角点
            p1 = triangle_outline(nx, ny, 0.18, 0.78, 0.28 + bass * 0.16, 0.035 + bass * 0.035, False) * bass
            p2 = triangle_outline(nx, ny, 0.50, 0.22, 0.24 + vocal * 0.18, 0.035 + vocal * 0.035, True) * vocal
            p3 = triangle_outline(nx, ny, 0.82, 0.78, 0.22 + treble * 0.16, 0.035 + treble * 0.035, False) * treble
            val = (p1 + p2 + p3) * (0.55 + energy)
        elif family == 14:   # 斜向空间波：软硬结合的有序波面
            wave = tri_wave(diag * (0.65 + bass * 0.35))
            gate = square_wave(diag * 0.45, 0.52)
            val = (wave ** (1.2 + treble)) * (0.55 + 0.45 * gate) * (0.30 + energy + local * 0.3)
        elif family == 15:   # 菱形声像仪：左右声像假想驱动中心偏移
            pan = 0.5 + 0.22 * math.sin(beat_phase + (treble - bass) * 1.7)
            radius = 0.18 + vocal * 0.18
            val = diamond(pan, 0.5, nx, ny, radius, 0.030 + energy * 0.045) * (0.30 + energy + vocal * 0.4)
        elif family == 16:   # 三角扫描扇：从一侧斜向展开
            sweep = (beat_phase * (0.12 + bass * 0.18)) % 1.0
            fan = triangle_outline(nx, ny, sweep, 0.5, 0.52, 0.045 + bass * 0.035, flip=False)
            val = fan * (0.28 + bass + transient * 0.45)
        elif family == 17:   # 菱形脉冲列：沿斜线排布的重复菱形
            center_line = abs((ny - 0.5) - (nx - 0.5) * slope * 0.55)
            pulse = tri_wave((nx + ny + beat_phase * 0.4) * (3 + variant % 5))
            val = soft_line(center_line, 0.06 + bass * 0.05) * (pulse ** 1.4) * (0.30 + energy + bass * 0.35)
        elif family == 18:   # 三角波边框：整屏规则边框随中频开合
            edge = max(triangle_outline(nx, ny, 0.5, 0.5, 0.72, 0.028 + vocal * 0.05, False),
                       triangle_outline(nx, ny, 0.5, 0.5, 0.52, 0.024 + vocal * 0.04, True))
            val = edge * (0.26 + vocal + rms * 0.35)
        elif family == 19:   # 斜菱格频谱：局部频段点亮规律菱格
            lattice = tri_wave((abs(cx) + abs(cy)) * (5.0 + variant % 6) + diag_phase * 0.35)
            diag_gate = square_wave(diag * 0.55, 0.40 + local * 0.20)
            val = lattice * diag_gate * (0.25 + local + energy * 0.35)
        elif family == 20:   # 规则短划点缀：类似舞台空间 marker
            period = dot_period + 2
            dash = (((col + lattice_shift) % period) in (0, 1)) and (((row - lattice_shift) % period) == 0)
            val = (1.0 if dash else 0.0) * (0.30 + transient * 0.75 + treble * 0.35)
        elif family == 21:   # 菱形低频地砖：低频让地砖跳动
            tile = (int(nx * (4 + variant % 5)) + int(ny * (4 + variant % 5)) + lattice_shift) % 2
            d = tri_wave((abs((nx * (3 + variant % 4)) % 1.0 - 0.5) + abs((ny * (3 + variant % 4)) % 1.0 - 0.5)) * 1.8)
            val = (0.25 + 0.75 * tile) * d * (0.28 + bass + low_mid * 0.35)
        elif family == 22:   # 三角-菱形混合点缀：规则装饰花纹
            tri = triangle_outline((nx * 2.0) % 1.0, (ny * 2.0) % 1.0, 0.5, 0.5, 0.48, 0.06 + vocal * 0.02, flip=(variant % 2 == 1))
            dia = diamond(0.5, 0.5, (nx * 3.0 + beat_phase * 0.03) % 1.0, (ny * 3.0) % 1.0, 0.34, 0.055 + treble * 0.025)
            val = max(tri * vocal, dia * treble) * (0.35 + energy)
        else:                # 斜方波点阵混合：用户指定“斜方波感觉”的综合模式
            gate = square_wave(diag, 0.44 + bass * 0.12)
            dot = regular_dot(col, row, nx, ny, dot_period + 1, lattice_shift)
            dia = soft_line((abs(cx) + abs(cy)) - (0.20 + 0.20 * tri_wave(beat_phase * 0.25)), 0.035 + vocal * 0.04)
            val = max(gate * dot, dia * 0.75) * (0.30 + energy + transient * 0.35)

        # 规则效果仍需有“音频识别”感：低频增强整体、瞬态补亮点缀，静音时保留很弱预览。
        audio_gain = 0.18 + energy * 0.82 + local * 0.20
        accent = 0.0
        if transient > 0.18 and ((col + row + lattice_shift + variant) % max(2, dot_period) == 0):
            accent = transient * 0.22
        weights[fid] = clamp(val * audio_gain + accent)
    return weights


_ORDERED_SPACE_AUDIO_MODE_GROUPS_V34 = {
    "音频空间-斜方波": [
        ("matrix_os_diag_square_bass", "斜方波低频带"), ("matrix_os_diag_square_cross", "交错斜方波"),
        ("matrix_os_diag_step_wave", "斜向阶梯波"), ("matrix_os_diag_space_wave", "斜向空间波"),
        ("matrix_os_hard_edge_scan", "硬边斜扫"), ("matrix_os_slanted_gate_rows", "斜门控行列"),
        ("matrix_os_diagonal_marker_wave", "斜向标记波"), ("matrix_os_offset_square_field", "错位方波场"),
        ("matrix_os_bass_tilt_wall", "低频倾斜墙"), ("matrix_os_syncopated_saw_blocks", "切分锯齿块"),
        ("matrix_os_phase_step_lattice", "相位阶梯格"), ("matrix_os_slash_wave_stairs", "斜杠波阶梯"),
    ],
    "音频空间-三角点缀": [
        ("matrix_os_tri_dot_lattice", "三角点阵"), ("matrix_os_tri_arrow_wave", "三角箭头波"),
        ("matrix_os_inverted_tri_gate", "倒三角低频门"), ("matrix_os_tri_tessellation", "斜向三角镶嵌"),
        ("matrix_os_three_band_tri_islands", "三频三角岛"), ("matrix_os_tri_scan_fan", "三角扫描扇"),
        ("matrix_os_tri_border_wave", "三角波边框"), ("matrix_os_tri_diamond_ornament", "三角菱形花纹"),
        ("matrix_os_vocal_tri_prism", "人声三角棱镜"), ("matrix_os_snare_tri_sparks", "军鼓三角星点"),
        ("matrix_os_tri_compass", "三角罗盘"), ("matrix_os_tri_flag_rows", "三角旗行列"),
    ],
    "音频空间-菱形规则": [
        ("matrix_os_diamond_dot_net", "菱形点缀网"), ("matrix_os_diamond_breathe", "菱形呼吸框"),
        ("matrix_os_diamond_echo", "多重菱形回声"), ("matrix_os_diamond_rain", "菱形雨"),
        ("matrix_os_diamond_checker", "菱形棋盘"), ("matrix_os_pan_diamond_meter", "声像菱形仪"),
        ("matrix_os_diamond_pulse_chain", "菱形脉冲列"), ("matrix_os_spectral_diamond_grid", "频谱斜菱格"),
        ("matrix_os_bass_floor_tiles", "低频菱形地砖"), ("matrix_os_echo_diamond_steps", "回声菱形阶梯"),
        ("matrix_os_diamond_corner_marks", "菱形角标"), ("matrix_os_diamond_chorus_wall", "副歌菱形墙"),
    ],
    "音频空间-规则点阵": [
        ("matrix_os_ordered_stars", "规则星点点缀"), ("matrix_os_marker_dash_grid", "短划标记点阵"),
        ("matrix_os_square_dot_mix", "斜方波点阵混合"), ("matrix_os_transient_pin_grid", "瞬态针点网"),
        ("matrix_os_vocal_pearl_rows", "人声珍珠行"), ("matrix_os_treble_constellation", "高频星座格"),
        ("matrix_os_bass_anchor_points", "低频锚点"), ("matrix_os_metered_space_ticks", "节拍空间刻度"),
        ("matrix_os_regular_confetti", "规律纸屑点缀"), ("matrix_os_grid_breath_marks", "网格呼吸标记"),
        ("matrix_os_phase_pinwheel", "相位针轮"), ("matrix_os_ordered_echo_pixels", "规则回声像素"),
    ],
}

_ORDERED_SPACE_AUDIO_MODES_V34: List[Tuple[str, str]] = []
for _group, _items in _ORDERED_SPACE_AUDIO_MODE_GROUPS_V34.items():
    _display_items = [(mode_id, f"音频空间·{label}") for mode_id, label in _items]
    PIXEL_MODES_EXT.setdefault(_group, []).extend(_display_items)
    _ORDERED_SPACE_AUDIO_MODES_V34.extend(_items)

_EXT_COMPUTE_MAP.update({
    mode_id: (lambda engine, m=mode_id, v=idx: _compute_matrix_ordered_space_audio_v34(engine, m, v))
    for idx, (mode_id, _label) in enumerate(_ORDERED_SPACE_AUDIO_MODES_V34)
})


# ─────────────────────────────────────────────────────────────────────────────
#  v35 对称波动效果：参考菱形波，复用主页面翼/块，支持音频/自走双模式与多维参数
# ─────────────────────────────────────────────────────────────────────────────

def _clamp01_v35(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else float(v)


def _smoothstep_v35(edge0: float, edge1: float, x: float) -> float:
    if abs(edge1 - edge0) < 1e-9:
        return 1.0 if x >= edge1 else 0.0
    t = _clamp01_v35((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _triwave_v35(x: float) -> float:
    p = x - math.floor(x)
    return 1.0 - abs(p * 2.0 - 1.0)


def _periodic_line_v35(metric: float, freq: float, phase: float, width: float) -> float:
    # 周期线带，width 越小边越硬，越大越宽。
    band = _triwave_v35(metric * max(0.05, freq) - phase)
    return _smoothstep_v35(max(0.0, 1.0 - width * 8.0), 1.0, band)


def _sym_audio_env_v35(engine, variant: int):
    """返回 bass/mid/high/rms/beat；关闭音频或无输入时使用自走包络。"""
    from .matrix_pixel_audio_modes import should_simulate_audio_env, versioned_audio_gain, versioned_audio_rms, permit_synthetic_beat, simulated_symmetric_audio
    use_audio = bool(getattr(engine, 'pixel_audio_reactive', True))
    bands, rms = [], 0.0
    if use_audio:
        try:
            bands, rms = list(engine._get_audio_bands()), float(engine._get_audio_rms())
        except Exception:
            bands, rms = [], 0.0
    if should_simulate_audio_env(engine, bands):
        bands, rms = simulated_symmetric_audio(engine, variant)
    amp = versioned_audio_gain(engine, max(0.2, float(getattr(engine, 'audio_amplitude', 2.0))))
    bass = min(1.0, sum(bands[:2]) / max(1, min(2, len(bands))) * amp)
    mid = min(1.0, sum(bands[2:5]) / max(1, min(3, max(0, len(bands) - 2))) * amp)
    high = min(1.0, sum(bands[5:8]) / max(1, min(3, max(0, len(bands) - 5))) * amp) if len(bands) > 5 else versioned_audio_rms(engine, rms, amp)
    beat = bool(getattr(engine, '_get_is_beat', lambda: False)()) if use_audio else False
    if not beat and permit_synthetic_beat(engine):
        beat = _triwave_v35(float(getattr(engine, '_t', 0.0)) * (0.50 + 0.04 * (variant % 4))) > 0.92
    return bass, mid, high, versioned_audio_rms(engine, rms, amp), beat


def _metric_for_symmetric_wave_v35(mode_id: str, x: float, y: float, angle: float, r: float, lobes: int, mix: float, twist: float, phase: float, variant: int) -> float:
    ax, ay = abs(x), abs(y)
    diamond = ax + ay
    square = max(ax, ay) * 1.35
    circle = r * 1.72
    cross = min(ax, ay) * 2.7 + 0.08 * max(ax, ay)
    axis = min(abs(x), abs(y)) * 2.5
    diag_axis = min(abs(x - y), abs(x + y)) * 1.8
    lobe = r * (1.0 + 0.32 * math.cos(angle * max(1, lobes) + twist * 2.0 + phase))
    flower = r * (1.0 + 0.24 * abs(math.cos(angle * max(2, lobes * 2) - phase)))
    lattice = (abs(math.sin((x + twist * 0.08) * math.pi * lobes)) + abs(math.sin((y - twist * 0.08) * math.pi * lobes))) * 0.5
    corner = min(
        math.hypot(x - 0.5, y - 0.5), math.hypot(x + 0.5, y - 0.5),
        math.hypot(x - 0.5, y + 0.5), math.hypot(x + 0.5, y + 0.5)
    ) * 1.55
    hourglass = abs(ax - ay) * 1.8 + 0.18 * (ax + ay)
    family = variant % 24
    if family == 0:   # 标准菱形环波
        return diamond
    if family == 1:   # 双菱形，菱形与方框混合
        return diamond * (1.0 - mix) + square * mix + 0.08 * math.sin(angle * lobes + phase)
    if family == 2:   # 十字镜像波
        return cross * (1.0 - mix) + diamond * mix
    if family == 3:   # 横纵轴门波
        return axis * (1.0 - mix) + circle * mix
    if family == 4:   # 八角/多瓣波
        return lobe * (1.0 - mix * 0.5) + square * mix * 0.35
    if family == 5:   # 方形同心波
        return square * (1.0 - mix) + diamond * mix
    if family == 6:   # X 镜像斜轴波
        return diag_axis * (1.0 - mix) + diamond * mix
    if family == 7:   # 径向点阵波
        return circle * 0.55 + lattice * 0.45
    if family == 8:   # 万花瓣波
        return flower
    if family == 9:   # 菱形棋盘波
        return diamond + 0.22 * lattice
    if family == 10:  # 四角回声波
        return corner * (1.0 - mix) + diamond * mix
    if family == 11:  # 中心膨胀波
        return circle * (1.0 - mix) + diamond * mix
    if family == 12:  # 沙漏对称波
        return hourglass * (1.0 - mix) + diamond * mix
    if family == 13:  # 嵌套边框波
        return min(0.5 - ax, 0.5 - ay) * -1.9 + 0.2 * diamond
    if family == 14:  # 四源干涉波
        return 0.5 * (corner + circle) + 0.08 * math.sin((x + y) * math.pi * lobes + phase)
    if family == 15:  # 曼陀罗花瓣波
        return r * (1.0 + 0.38 * math.cos(angle * max(2, lobes * 2) + phase))
    if family == 16:  # 分裂镜像波
        return abs(ax - mix * ay) + 0.35 * axis
    if family == 17:  # 旋转条纹对称波
        return abs(math.sin((x * math.cos(twist) + y * math.sin(twist)) * math.pi * lobes))
    if family == 18:  # 旋转菱形涡波
        return diamond + 0.18 * math.sin(angle * lobes + r * 8.0 + phase)
    if family == 19:  # 呼吸砖格波
        return max(ax, ay) + 0.18 * lattice
    if family == 20:  # 同心十字波
        return 0.55 * circle + 0.45 * cross
    if family == 21:  # 旋转网格波
        ca, sa = math.cos(twist + phase * 0.2), math.sin(twist + phase * 0.2)
        rx, ry = x * ca - y * sa, x * sa + y * ca
        return 0.5 * (abs(math.sin(rx * math.pi * lobes)) + abs(math.sin(ry * math.pi * lobes)))
    if family == 22:  # 相位镜面波
        return 0.5 * (abs(x) + abs(y)) + 0.25 * abs(math.sin(angle * lobes - phase))
    return flower * (1.0 - mix * 0.3) + diamond * mix * 0.3


def _compute_matrix_symmetric_wave_v35(engine, mode_id: str, variant: int) -> Dict[str, float]:
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    t = float(getattr(engine, '_t', 0.0))
    wings = max(1, int(getattr(engine, 'wings', 1)))
    block_size = max(1, int(getattr(engine, 'block_size', 1)))
    bass, mid, high, rms, beat = _sym_audio_env_v35(engine, variant)
    density = max(0.35, float(getattr(engine, 'matrix_wave_density', 1.0)))
    width = max(0.03, min(0.40, float(getattr(engine, 'matrix_wave_width', 0.12))))
    twist = float(getattr(engine, 'matrix_wave_twist', 0.0))
    mix = max(0.0, min(1.0, float(getattr(engine, 'matrix_symmetry_mix', 0.5))))

    lobes = max(1, wings)
    freq = (2.2 + 0.32 * lobes + 0.08 * block_size + bass * 2.2 + (variant % 4) * 0.18) * density
    phase = t * (0.34 + 0.06 * (variant % 5) + bass * 0.42) + mid * 0.35
    hard = width * (0.82 + high * 0.62)
    pulse_gain = 0.12 + rms * 0.32 + (0.18 if beat else 0.0)
    aspect = cols / max(1, rows)
    weights = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = (col / max(1, cols - 1)) - 0.5
        ny = (row / max(1, rows - 1)) - 0.5
        # 方阵/长阵保持视觉比例稳定。
        x = nx * max(1.0, aspect)
        y = ny * max(1.0, 1.0 / max(0.001, aspect))
        # 块参数作为基本单元：在公式层面轻量量化，最终仍交给主引擎后处理同步。
        if block_size > 1:
            qx = round(x * cols / block_size) * block_size / max(1, cols)
            qy = round(y * rows / block_size) * block_size / max(1, rows)
            x = x * 0.60 + qx * 0.40
            y = y * 0.60 + qy * 0.40
        angle = math.atan2(y, x) + twist * 0.45 + 0.08 * math.sin(t * 0.31 + variant)
        r = math.hypot(x, y)
        metric = _metric_for_symmetric_wave_v35(mode_id, x, y, angle, r, lobes, mix, twist, phase, variant)
        primary = _periodic_line_v35(metric, freq, phase, hard)
        secondary = _periodic_line_v35(metric + 0.18 * math.sin(angle * lobes + phase), freq * (0.52 + mix * 0.55), -phase * 0.72, hard * 1.25)
        core = _smoothstep_v35(0.42 + 0.12 * mix, 0.0, r) * (0.18 + 0.20 * bass)
        val = primary * (0.72 + pulse_gain) + secondary * (0.18 + 0.30 * mid) + core
        # 保持强对称图形的清晰边界，但允许高频使边缘闪亮。
        val = val / (1.05 + 0.28 * high)
        weights[fid] = _clamp01_v35(val)
    return weights


_SYMMETRIC_WAVE_MODE_GROUPS_V35 = {
    "对称波动-菱形与同心": [
        ("matrix_sw_diamond_rings", "菱形环波"),
        ("matrix_sw_double_diamond", "双菱形波"),
        ("matrix_sw_square_ripple", "方框同心波"),
        ("matrix_sw_center_bloom", "中心绽放波"),
        ("matrix_sw_nested_frame", "嵌套边框波"),
        ("matrix_sw_vortex_diamond", "旋转菱形波"),
    ],
    "对称波动-轴线与镜像": [
        ("matrix_sw_cross_mirror", "十字镜像波"),
        ("matrix_sw_axis_gate", "轴线门控波"),
        ("matrix_sw_x_mirror_wave", "X 镜像波"),
        ("matrix_sw_hourglass_wave", "沙漏对称波"),
        ("matrix_sw_split_mirror", "分裂镜面波"),
        ("matrix_sw_phase_mirror", "相位镜面波"),
    ],
    "对称波动-花瓣与万花": [
        ("matrix_sw_octagon_pulse", "多瓣脉冲波"),
        ("matrix_sw_kaleido_spokes", "万花轴波"),
        ("matrix_sw_mandala_petals", "曼陀罗瓣波"),
        ("matrix_sw_waveflower", "花冠波纹"),
        ("matrix_sw_concentric_cross", "同心十字波"),
        ("matrix_sw_barber_pole_sym", "旋向条纹波"),
    ],
    "对称波动-规则点阵": [
        ("matrix_sw_radial_lattice", "径向点阵波"),
        ("matrix_sw_checker_diamond", "菱形棋盘波"),
        ("matrix_sw_corner_echo", "四角回声波"),
        ("matrix_sw_four_source_interference", "四源干涉波"),
        ("matrix_sw_breathing_tiles", "呼吸砖格波"),
        ("matrix_sw_rotated_grid", "旋转网格波"),
    ],
}

_SYMMETRIC_WAVE_MODES_V35: List[Tuple[str, str]] = []
for _group, _items in _SYMMETRIC_WAVE_MODE_GROUPS_V35.items():
    PIXEL_MODES_EXT.setdefault(_group, []).extend([(mode_id, label) for mode_id, label in _items])
    _SYMMETRIC_WAVE_MODES_V35.extend(_items)

_EXT_COMPUTE_MAP.update({
    mode_id: (lambda engine, m=mode_id, v=idx: _compute_matrix_symmetric_wave_v35(engine, m, v))
    for idx, (mode_id, _label) in enumerate(_SYMMETRIC_WAVE_MODES_V35)
})

# 最终展示清单：主界面只保留高质量、对称性强、可用翼/块与多维参数调整的矩阵像素效果。
# 旧的泛化效果不再进入下拉框，但底层计算函数继续保留，避免破坏历史工程兼容。
PIXEL_MODES_EXT = {
    "对称波动-菱形与同心": list(_SYMMETRIC_WAVE_MODE_GROUPS_V35["对称波动-菱形与同心"]),
    "对称波动-轴线与镜像": list(_SYMMETRIC_WAVE_MODE_GROUPS_V35["对称波动-轴线与镜像"]),
    "对称波动-花瓣与万花": list(_SYMMETRIC_WAVE_MODE_GROUPS_V35["对称波动-花瓣与万花"]),
    "对称波动-规则点阵": list(_SYMMETRIC_WAVE_MODE_GROUPS_V35["对称波动-规则点阵"]),
    "空间点阵-菱形补充": [
        ("matrix_os_diamond_dot_net", "菱形点缀网"),
        ("matrix_os_diamond_breathe", "菱形呼吸框"),
        ("matrix_os_diamond_echo", "多重菱形回声"),
        ("matrix_os_diamond_checker", "菱形棋盘"),
        ("matrix_os_diamond_pulse_chain", "菱形脉冲列"),
        ("matrix_os_spectral_diamond_grid", "频谱斜菱格"),
        ("matrix_os_echo_diamond_steps", "回声菱形阶梯"),
        ("matrix_os_diamond_chorus_wall", "副歌菱形墙"),
    ],
    "空间点阵-斜波与三角": [
        ("matrix_os_diag_square_bass", "斜方波低频带"),
        ("matrix_os_diag_step_wave", "斜向阶梯波"),
        ("matrix_os_diag_space_wave", "斜向空间波"),
        ("matrix_os_phase_step_lattice", "相位阶梯格"),
        ("matrix_os_tri_dot_lattice", "三角点阵"),
        ("matrix_os_tri_tessellation", "三角镶嵌"),
        ("matrix_os_tri_compass", "三角罗盘"),
        ("matrix_os_tri_diamond_ornament", "三角菱形花纹"),
    ],
    "空间点阵-规则标记": [
        ("matrix_os_ordered_stars", "规则星点"),
        ("matrix_os_marker_dash_grid", "短划标记点阵"),
        ("matrix_os_transient_pin_grid", "瞬态针点网"),
        ("matrix_os_vocal_pearl_rows", "人声珍珠行"),
        ("matrix_os_metered_space_ticks", "节拍空间刻度"),
        ("matrix_os_grid_breath_marks", "网格呼吸标记"),
        ("matrix_os_phase_pinwheel", "相位针轮"),
        ("matrix_os_ordered_echo_pixels", "规则回声像素"),
    ],
    "对称波动-经典补充": [
        (MODE_MATRIX_DIAMOND_AUDIO, "经典菱形波"),
        (MODE_MATRIX_RADIAL_WAVE_AUDIO, "径向同心波"),
        (MODE_MATRIX_STANDING_WAVE_AUDIO, "对称驻波"),
        (MODE_MATRIX_KALEIDOSCOPE_AUDIO, "万花筒折叠"),
        (MODE_MATRIX_CROSS_WAVE_AUDIO, "交叉镜像波"),
        (MODE_MATRIX_WAVE_STACK_AUDIO, "分层波浪叠加"),
        (MODE_MATRIX_CHECKER_BREATH_AUDIO, "棋盘呼吸波"),
        (MODE_MATRIX_AUDIO_DIAMOND_V29, "响应菱形波"),
    ],
}

try:
    from .matrix_pixel_engine_ext_v38 import register_v38_matrix_ext
    register_v38_matrix_ext(PIXEL_MODES_EXT, _EXT_COMPUTE_MAP)
except Exception:
    pass

try:
    from .matrix_pixel_engine_ext_v39 import register_v39_matrix_ext
    register_v39_matrix_ext(PIXEL_MODES_EXT, _EXT_COMPUTE_MAP)
except Exception:
    pass

try:
    from .matrix_pixel_engine_ext_v40 import register_v40_matrix_ext
    register_v40_matrix_ext(PIXEL_MODES_EXT, _EXT_COMPUTE_MAP)
except Exception:
    pass


from .matrix_pixel_music_catalog import register_music_arrangements
register_music_arrangements(PIXEL_MODES_EXT, _EXT_COMPUTE_MAP)


def patch_matrix_pixel_engine(engine_class):
    """
    将所有扩展效果注入到MatrixPixelEngine类中。
    在MatrixPixelEngine导入后调用此函数。
    """
    # 1. 添加计算方法
    for mode_id, func in _EXT_COMPUTE_MAP.items():
        method_name = f"_compute_{mode_id}"
        setattr(engine_class, method_name, lambda self, f=func: f(self))
    
    # 2. 扩展tick方法（猴子补丁）
    original_tick = engine_class.tick
    
    def extended_tick(self, dt: float) -> Dict[str, bool]:
        # 先尝试扩展效果
        if self._mode in _EXT_COMPUTE_MAP:
            if not self._fixtures or not self._running:
                return {}
            self._t += dt * self.speed
            compute_func = _EXT_COMPUTE_MAP[self._mode]
            weights = compute_func(self)
            if hasattr(self, '_apply_wings_blocks'):
                weights = self._apply_wings_blocks(weights)
            result = {}
            for fid, w in weights.items():
                on = w >= self.threshold
                result[fid] = (not on) if self.invert else on
            return result
        # 否则调用原始tick
        return original_tick(self, dt)
    
    engine_class.tick = extended_tick
    
    # 3. 扩展PIXEL_MODES字典
    try:
        from src.ui.matrix_slideshow.matrix_pixel_engine import PIXEL_MODES
        PIXEL_MODES.update(PIXEL_MODES_EXT)
    except Exception:
        pass
    
    return engine_class


def get_all_ext_modes():
    """返回所有扩展效果模式的列表"""
    return list(_EXT_COMPUTE_MAP.keys())
