from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Tuple

from .matrix_pixel_audio_modes import (finish_v38_audio_bar, should_simulate_audio_env,
                                       v38_audio_levels, versioned_audio_gain, versioned_audio_rms)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else float(v)


def _tri(x: float) -> float:
    p = float(x) - math.floor(float(x))
    return 1.0 - abs(p * 2.0 - 1.0)


def _smooth(edge0: float, edge1: float, x: float) -> float:
    if abs(edge1 - edge0) < 1e-9:
        return 1.0 if x >= edge1 else 0.0
    t = _clamp01((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _band(metric: float, width: float, softness: float) -> float:
    p = _tri(metric)
    edge = max(0.02, min(0.98, 1.0 - max(0.005, width) * 2.8))
    soft = max(0.001, min(0.45, softness))
    return _smooth(edge - soft, edge + soft, p)


def _hash2(x: int, y: int, seed: int) -> float:
    n = int(x) * 374761393 + int(y) * 668265263 + int(seed) * 1442695041
    n = (n ^ (n >> 13)) * 1274126177
    return ((n ^ (n >> 16)) & 0xFFFFFFFF) / 4294967295.0


def _audio_env(engine, variant: int) -> Tuple[float, float, float, float, bool]:
    use_audio = bool(getattr(engine, "pixel_audio_reactive", True))
    bands = []
    rms = 0.0
    if use_audio:
        try:
            bands = list(engine._get_audio_bands())
            rms = float(engine._get_audio_rms())
        except Exception:
            bands = []
            rms = 0.0
    if should_simulate_audio_env(engine, bands):
        t = float(getattr(engine, "_t", 0.0))
        bass = 0.30 + 0.55 * math.sin(t * (0.65 + 0.02 * variant)) ** 2
        mid = 0.22 + 0.48 * _tri(t * (0.24 + 0.01 * (variant % 9)) + variant * 0.07)
        high = 0.16 + 0.42 * math.sin(t * (1.05 + 0.03 * (variant % 5)) + variant * 0.21) ** 2
        rms = (bass + mid + high) / 3.0
        return bass, mid, high, rms, _tri(t * 0.48 + variant * 0.03) > 0.91
    amp = versioned_audio_gain(engine, max(0.2, float(getattr(engine, "audio_amplitude", 2.0) or 2.0)))
    bass = min(1.0, sum(bands[:2]) / max(1, min(2, len(bands))) * amp)
    mid = min(1.0, sum(bands[2:5]) / max(1, min(3, max(0, len(bands) - 2))) * amp)
    high = min(1.0, sum(bands[5:8]) / max(1, min(3, max(0, len(bands) - 5))) * amp) if len(bands) > 5 else versioned_audio_rms(engine, rms, amp)
    try:
        beat = bool(engine._get_is_beat())
    except Exception:
        beat = False
    return bass, mid, high, versioned_audio_rms(engine, rms, amp), beat


def _metric(variant: int, nx: float, ny: float, x: float, y: float, angle: float,
            r: float, t: float, twist: float, mix: float,
            bass: float, mid: float, high: float) -> float:
    family = variant % 60
    diag = nx + ny
    anti = nx - ny
    diamond = abs(x) + abs(y)
    square = max(abs(x), abs(y))
    if family == 0:
        return nx * (2.0 + mix * 3.0) + 0.18 * math.sin(ny * math.pi * 4.0 + twist)
    if family == 1:
        return ny * (2.0 + mix * 3.0) + 0.18 * math.sin(nx * math.pi * 4.0 - twist)
    if family == 2:
        return diag + 0.20 * math.sin((nx - ny) * math.pi * (3.0 + bass * 3.0))
    if family == 3:
        return anti + 0.20 * math.cos((nx + ny) * math.pi * (3.0 + mid * 3.0))
    if family == 4:
        return r * (2.4 + bass * 3.5) + angle / math.tau * (1.0 + mix)
    if family == 5:
        return angle / math.tau * (2.0 + mix * 5.0) + r * (1.5 + high * 2.5)
    if family == 6:
        return diamond * (3.0 + bass * 3.0) + 0.18 * math.sin(angle * 4.0 + t)
    if family == 7:
        return square * (3.0 + mid * 3.0) + 0.15 * math.cos(angle * 6.0 - t)
    if family == 8:
        return abs(math.sin((nx + twist * 0.03) * math.pi * (4 + variant % 5))) + ny * mix
    if family == 9:
        return abs(math.sin((ny - twist * 0.03) * math.pi * (4 + variant % 5))) + nx * mix
    if family == 10:
        return (math.floor(nx * (4 + variant % 6)) + math.floor(ny * (3 + variant % 5))) * 0.17
    if family == 11:
        return ((nx * (3 + variant % 5)) % 1.0) + ((ny * (5 + variant % 4)) % 1.0) * 0.35
    if family == 12:
        return min(abs(x), abs(y)) * (5.0 + bass * 5.0) + r * mix
    if family == 13:
        return max(abs(x), abs(y)) * (5.0 + mid * 4.0) - min(abs(x), abs(y)) * mix
    if family == 14:
        return abs(x * math.cos(twist) + y * math.sin(twist)) * (5.0 + high * 4.0)
    if family == 15:
        return abs(-x * math.sin(twist) + y * math.cos(twist)) * (5.0 + bass * 4.0)
    if family == 16:
        return r + 0.28 * math.sin(angle * (3 + variant % 7) + t * 0.7)
    if family == 17:
        return diamond + 0.24 * math.cos((nx * 2.0 - ny) * math.pi + twist)
    if family == 18:
        return square + 0.22 * math.sin((ny * 2.0 + nx) * math.pi - twist)
    if family == 19:
        return abs(math.sin((diag + twist * 0.02) * math.pi * (2 + variant % 6))) * 0.55 + r
    if family == 20:
        return abs(math.cos((anti - twist * 0.02) * math.pi * (2 + variant % 6))) * 0.55 + r
    if family == 21:
        return min(math.hypot(nx - 0.18, ny - 0.22), math.hypot(nx - 0.78, ny - 0.36), math.hypot(nx - 0.43, ny - 0.82)) * (4.0 + bass * 3.0)
    if family == 22:
        return (abs(x) ** (1.0 + mix) + abs(y) ** (1.4 + high)) * (2.6 + bass * 2.0)
    if family == 23:
        return (math.sin(nx * math.pi * (3 + variant % 5) + twist) + math.cos(ny * math.pi * (4 + variant % 4) - twist)) * 0.5
    if family == 24:
        return (math.floor((nx + ny + t * 0.03) * (4 + variant % 5)) % 2) + r * mix
    if family == 25:
        return (math.floor((nx - ny - t * 0.02) * (5 + variant % 4)) % 2) + diamond * mix
    if family == 26:
        return abs(math.sin((r * (4 + variant % 6) + angle / math.tau) * math.pi))
    if family == 27:
        return abs(math.cos((diamond * (3 + variant % 5) - angle / math.tau) * math.pi))
    if family == 28:
        return _hash2(int(nx * 12), int(ny * 12), variant) * 0.6 + r * 0.8
    if family == 29:
        return _hash2(int((nx + t * 0.02) * 16), int((ny - t * 0.02) * 16), variant) * 0.8 + diamond * 0.5
    if family == 30:
        return abs(nx - _tri(t * 0.13 + ny * 0.5)) * 2.0
    if family == 31:
        return abs(ny - _tri(t * 0.11 + nx * 0.6)) * 2.0
    if family == 32:
        return min(abs(nx - 0.25), abs(nx - 0.75)) * 3.0 + ny * mix
    if family == 33:
        return min(abs(ny - 0.25), abs(ny - 0.75)) * 3.0 + nx * mix
    if family == 34:
        return abs(math.sin((nx * ny + t * 0.03) * math.pi * (8 + variant % 9)))
    if family == 35:
        return abs(math.cos(((nx - 0.5) * (ny - 0.5) - t * 0.02) * math.pi * (10 + variant % 7)))
    if family == 36:
        return (abs(x + y) * 1.6 + abs(x - y) * 0.4) * (2.0 + mix)
    if family == 37:
        return (abs(x - y) * 1.6 + abs(x + y) * 0.4) * (2.0 + mix)
    if family == 38:
        return abs(math.sin((angle * (2 + variant % 5) + r * (5 + bass * 4.0)) * math.pi))
    if family == 39:
        return abs(math.cos((angle * (3 + variant % 6) - r * (4 + mid * 4.0)) * math.pi))
    if family == 40:
        return min(abs(nx - 0.5), abs(ny - 0.5)) * (5 + variant % 6)
    if family == 41:
        return max(abs(nx - 0.5), abs(ny - 0.5)) * (5 + variant % 6)
    if family == 42:
        return abs(math.sin((math.floor(nx * 8) + ny * 8 + t * 0.2) * math.pi * 0.5))
    if family == 43:
        return abs(math.cos((math.floor(ny * 8) + nx * 8 - t * 0.2) * math.pi * 0.5))
    if family == 44:
        return (abs(math.sin(nx * math.pi * 6 + t)) * 0.35 + abs(math.sin(ny * math.pi * 4 - t * 0.7)) * 0.65)
    if family == 45:
        return (abs(math.cos(nx * math.pi * 5 - t * 0.8)) * 0.65 + abs(math.cos(ny * math.pi * 7 + t)) * 0.35)
    if family == 46:
        return abs(math.sin((r + diamond * 0.35) * math.pi * (4 + variant % 5) - t))
    if family == 47:
        return abs(math.cos((square + r * 0.25) * math.pi * (5 + variant % 6) + t))
    if family == 48:
        return min(math.hypot(nx - 0.15 - 0.2 * math.sin(t), ny - 0.2), math.hypot(nx - 0.85, ny - 0.8 + 0.2 * math.cos(t))) * 4.0
    if family == 49:
        return min(math.hypot(nx - 0.25, ny - 0.75), math.hypot(nx - 0.75, ny - 0.25), math.hypot(nx - 0.5, ny - 0.5)) * 4.5
    if family == 50:
        return (abs(math.sin(diag * math.pi * 4.0 + twist)) + _hash2(int(nx * 8), int(ny * 8), variant) * mix) * 0.7
    if family == 51:
        return (abs(math.cos(anti * math.pi * 4.0 - twist)) + _hash2(int(nx * 9), int(ny * 7), variant) * mix) * 0.7
    if family == 52:
        return abs(math.sin((diamond + _hash2(int(nx * 5), int(ny * 5), variant) * 0.2) * math.pi * 5.0 - t))
    if family == 53:
        return abs(math.cos((square + _hash2(int(nx * 6), int(ny * 4), variant) * 0.2) * math.pi * 5.0 + t))
    if family == 54:
        return abs(math.sin((nx * (2 + variant % 4) + ny * (3 + variant % 5)) * math.pi + t))
    if family == 55:
        return abs(math.cos((nx * (3 + variant % 5) - ny * (2 + variant % 4)) * math.pi - t))
    if family == 56:
        return _tri((r + angle / math.tau * 0.4) * (3 + variant % 5) - t * 0.2)
    if family == 57:
        return _tri((diamond - angle / math.tau * 0.3) * (4 + variant % 4) + t * 0.2)
    if family == 58:
        return _hash2(int((nx + math.sin(t) * 0.03) * 18), int((ny + math.cos(t) * 0.03) * 18), variant)
    return _hash2(int(nx * (10 + variant % 8)), int(ny * (10 + variant % 6)), int(t * 4) + variant)


def _compute_pattern(engine, _mode_id: str, variant: int) -> Dict[str, float]:
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    t = float(getattr(engine, "_t", 0.0))
    bass, mid, high, rms, beat = _audio_env(engine, variant)
    density = max(0.02, float(getattr(engine, "matrix_wave_density", 1.0) or 1.0))
    width = max(0.004, min(2.0, float(getattr(engine, "matrix_wave_width", 0.12) or 0.12)))
    twist = float(getattr(engine, "matrix_wave_twist", 0.0) or 0.0)
    mix = max(0.0, min(1.0, float(getattr(engine, "matrix_symmetry_mix", 0.5) or 0.5)))
    speed = 0.18 + 0.018 * (variant % 9) + bass * 0.16 + rms * 0.10
    freq = (1.4 + 0.16 * (variant % 11) + mid * 2.8) * density
    softness = 0.012 + width * (0.28 + high * 0.32)
    phase = t * speed + 0.13 * variant + (0.12 if beat else 0.0)
    aspect = cols / max(1, rows)
    weights: Dict[str, float] = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        x = (nx - 0.5) * max(1.0, aspect)
        y = (ny - 0.5) * max(1.0, 1.0 / max(0.001, aspect))
        rot = twist * 0.18 + phase * (0.12 + 0.01 * (variant % 5))
        cr, sr = math.cos(rot), math.sin(rot)
        rx, ry = x * cr - y * sr, x * sr + y * cr
        angle = math.atan2(ry, rx)
        r = math.hypot(rx, ry)
        metric = _metric(variant, nx, ny, rx, ry, angle, r, t, twist, mix, bass, mid, high)
        style = variant % 5
        if style == 0:
            val = _band(metric * freq - phase, width, softness)
        elif style == 1:
            val = _tri(metric * freq - phase) ** (1.1 + high * 1.8)
        elif style == 2:
            val = 1.0 if ((metric * freq - phase) % 1.0) < max(0.03, min(0.8, width * (1.6 + mix))) else 0.0
            val *= 0.75 + 0.25 * _tri(phase + r)
        elif style == 3:
            cell = _hash2(int(nx * (6 + variant % 7)), int(ny * (5 + variant % 6)), int(phase * 8) + variant)
            val = _clamp01(_band(metric * freq - phase, width, softness) * 0.75 + cell * (0.15 + high * 0.25))
        else:
            primary = _band(metric * freq - phase, width, softness)
            secondary = _band(metric * (freq * 0.55 + 0.3) + phase * 0.7, width * 1.5, softness * 1.3)
            val = _clamp01(primary * (0.70 + bass * 0.25) + secondary * (0.18 + mid * 0.35))
        if variant >= 30:
            cols_bucket = 5 + (variant % 7)
            rows_bucket = 4 + (variant % 6)
            dot_seed = int(phase * (5 + variant % 5))
            dot = _hash2(int(nx * cols_bucket), int(ny * rows_bucket), dot_seed + variant)
            marker = 1.0 if dot > (0.70 - 0.18 * mix) else 0.0
            grid_pulse = _band(
                (math.floor(nx * cols_bucket) + math.floor(ny * rows_bucket)) * 0.17
                - phase * (0.45 + 0.03 * (variant % 6)),
                max(width * 1.35, 0.11),
                max(softness, 0.04),
            )
            val = max(val, marker * (0.55 + 0.25 * rms), grid_pulse * (0.50 + 0.25 * mid))
        weights[fid] = _clamp01(val)
    return weights


def _resample_levels(values: List[float], count: int) -> List[float]:
    count = max(1, int(count or 1))
    if not values:
        return [0.0] * count
    if len(values) == 1:
        return [_clamp01(values[0])] * count
    out = []
    for i in range(count):
        x = i * (len(values) - 1) / max(1, count - 1)
        lo = int(math.floor(x))
        hi = min(len(values) - 1, lo + 1)
        frac = x - lo
        out.append(_clamp01(values[lo] * (1.0 - frac) + values[hi] * frac))
    return out


def _audio_bands(engine) -> List[float]:
    try:
        bands = [float(v) for v in engine._get_audio_bands()]
    except Exception:
        bands = []
    amp = max(0.1, float(getattr(engine, "audio_amplitude", 2.0) or 2.0))
    if not bands:
        return [0.0] * 8
    return [_clamp01(v * amp) for v in bands]


def _compute_audio_bar(engine, _mode_id: str, variant: int) -> Dict[str, float]:
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    t = float(getattr(engine, "_t", 0.0))
    rms = _clamp01(float(getattr(engine, "_get_audio_rms", lambda: 0.0)()) * max(0.1, float(getattr(engine, "audio_amplitude", 2.0) or 2.0)))
    beat = bool(getattr(engine, "_get_is_beat", lambda: False)())
    direction = str(getattr(engine, "audio_bar_direction", "bottom") or "bottom")
    axis_count = rows if direction in {"left", "right"} else cols
    levels = v38_audio_levels(engine, axis_count)
    motion = max(0.0, float(getattr(engine, "audio_bar_motion", 1.0) or 1.0))
    soft = max(0.01, float(getattr(engine, "audio_bar_softness", 0.08) or 0.08))
    peak = 0.20 + (0.18 if beat else 0.0)
    style = variant % 30
    weights: Dict[str, float] = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row = grid.get_col_row(i)
        nx = col / max(1, cols - 1)
        ny = row / max(1, rows - 1)
        if direction in {"left", "right"}:
            axis = row
            coord = nx if direction == "left" else (1.0 - nx)
        else:
            axis = col
            coord = 1.0 - ny if direction == "bottom" else ny
        level = levels[min(max(0, int(axis)), len(levels) - 1)]
        mirror = abs(coord - 0.5) * 2.0
        center_fill = max(0.0, level - mirror)
        fill = _smooth(coord - soft, coord + soft, level)
        stripe = 0.5 + 0.5 * math.sin((axis * 0.75 + coord * 8.0 - t * motion * 4.0) * math.tau)
        dot = 1.0 if ((math.floor(coord * max(2, rows if direction in {"bottom", "top"} else cols)) + axis + int(t * motion * 8.0)) % (2 + style % 4)) == 0 else 0.0
        diag = _band((nx + ny) * (2.0 + style % 5) - t * motion * 0.4 + level, 0.09 + level * 0.08, soft)
        anti = _band((nx - ny) * (2.0 + style % 5) + t * motion * 0.4 - level, 0.09 + level * 0.08, soft)
        ring = _band(math.hypot(nx - 0.5, ny - 0.5) * (3.0 + style % 6) - level - t * 0.2, 0.10 + level * 0.10, soft)
        if style == 0:
            val = fill
        elif style == 1:
            val = center_fill
        elif style == 2:
            val = fill * stripe
        elif style == 3:
            val = fill * dot
        elif style == 4:
            val = max(fill * 0.70, diag * level)
        elif style == 5:
            val = max(fill * 0.70, anti * level)
        elif style == 6:
            val = max(center_fill, ring * (0.35 + level))
        elif style == 7:
            val = fill * (0.55 + 0.45 * _tri(coord * (4 + style) - t * motion))
        elif style == 8:
            val = max(fill, peak * _band(coord - level, 0.02 + soft, soft))
        elif style == 9:
            val = fill * (0.35 + 0.65 * _hash2(axis, int(coord * 16), int(t * 10) + variant))
        elif style == 10:
            val = max(fill * 0.6, _band((coord + axis / max(1, axis_count)) * 4.0 - t * motion, 0.07, soft) * level)
        elif style == 11:
            val = max(center_fill, _band((coord - axis / max(1, axis_count)) * 4.0 + t * motion, 0.07, soft) * level)
        elif style == 12:
            val = fill if (axis + int(t * motion * 6.0)) % 2 == 0 else center_fill
        elif style == 13:
            val = max(fill * 0.75, dot * level * (0.4 + rms))
        elif style == 14:
            val = max(fill * 0.55, stripe * level * (0.5 + rms))
        elif style == 15:
            val = fill * (0.4 + 0.6 * math.sin((coord + level) * math.pi) ** 2)
        elif style == 16:
            val = max(fill * (0.4 + level * 0.6), ring * rms)
        elif style == 17:
            val = max(fill * 0.5, diag * 0.6, anti * 0.6) * (0.5 + level * 0.5)
        elif style == 18:
            val = _band(coord - level * (0.65 + 0.25 * math.sin(axis)), 0.05 + soft, soft) + fill * 0.25
        elif style == 19:
            val = max(fill * 0.8, _band(coord * (3 + axis % 4) - level - t, 0.08, soft) * level)
        elif style == 20:
            val = fill * (1.0 if coord < level else max(0.0, 1.0 - (coord - level) / max(soft, 1e-3)))
        elif style == 21:
            val = max(center_fill, fill * _tri(axis / max(1, axis_count) + t * 0.2))
        elif style == 22:
            val = fill * (0.25 + 0.75 * _tri((coord + axis / max(1, axis_count)) * 2.0))
        elif style == 23:
            val = max(fill * 0.7, _hash2(axis, int(coord * 12), variant) * level * 0.6)
        elif style == 24:
            val = fill * (0.5 + 0.5 * math.sin((coord * 12.0 + level * 4.0 - t * motion) * math.pi))
        elif style == 25:
            val = max(fill * 0.45, _band(abs(nx - ny) * (4 + style % 6) - level, 0.08, soft) * level)
        elif style == 26:
            val = max(fill * 0.45, _band(abs(nx + ny - 1.0) * (4 + style % 6) - level, 0.08, soft) * level)
        elif style == 27:
            val = max(fill * 0.5, _band(max(abs(nx - 0.5), abs(ny - 0.5)) * 4.0 - level, 0.08, soft) * (0.5 + rms))
        elif style == 28:
            val = max(fill * 0.5, _band((abs(nx - 0.5) + abs(ny - 0.5)) * 4.0 - level, 0.08, soft) * (0.5 + rms))
        else:
            val = max(fill * 0.55, _band(math.atan2(ny - 0.5, nx - 0.5) / math.tau * 4.0 + level - t * 0.2, 0.08, soft) * level)
        weights[fid] = _clamp01(val)
    return finish_v38_audio_bar(engine, weights, levels)


PIXEL_PATTERN_MODES: List[Tuple[str, str]] = [
    ("matrix_v38_pixel_shear_wave", "像素斜切波"),
    ("matrix_v38_pixel_vertical_smear", "像素竖向拖影"),
    ("matrix_v38_pixel_diagonal_rain", "像素斜雨"),
    ("matrix_v38_pixel_counter_rain", "像素反斜雨"),
    ("matrix_v38_pixel_spiral_ramp", "像素螺旋坡"),
    ("matrix_v38_pixel_angular_scan", "像素角度扫描"),
    ("matrix_v38_pixel_diamond_flow", "像素菱形流"),
    ("matrix_v38_pixel_square_tide", "像素方潮"),
    ("matrix_v38_pixel_column_lace", "像素列织纹"),
    ("matrix_v38_pixel_row_lace", "像素行织纹"),
    ("matrix_v38_pixel_block_clock", "像素块时钟"),
    ("matrix_v38_pixel_offset_tiles", "像素错位砖"),
    ("matrix_v38_pixel_cross_gate", "像素十字门"),
    ("matrix_v38_pixel_box_gate", "像素方框门"),
    ("matrix_v38_pixel_rotor_slice", "像素旋切片"),
    ("matrix_v38_pixel_rotor_bar", "像素旋转条"),
    ("matrix_v38_pixel_flower_drift", "像素花瓣漂移"),
    ("matrix_v38_pixel_diamond_warp", "像素菱形扭曲"),
    ("matrix_v38_pixel_square_warp", "像素方形扭曲"),
    ("matrix_v38_pixel_diag_lattice", "像素斜格"),
    ("matrix_v38_pixel_anti_lattice", "像素反斜格"),
    ("matrix_v38_pixel_three_source", "像素三源波"),
    ("matrix_v38_pixel_power_curve", "像素幂曲线"),
    ("matrix_v38_pixel_checker_step", "像素棋盘阶梯"),
    ("matrix_v38_pixel_slash_steps", "像素斜杠阶梯"),
    ("matrix_v38_pixel_orbit_ribs", "像素轨道筋"),
    ("matrix_v38_pixel_diamond_ribs", "像素菱形筋"),
    ("matrix_v38_pixel_noise_depth", "像素噪声深度"),
    ("matrix_v38_pixel_noise_drift", "像素噪声漂移"),
    ("matrix_v38_pixel_weave_field", "像素编织场"),
]

SPACE_DOT_MODES: List[Tuple[str, str]] = [
    ("matrix_v38_space_pin_rain", "空间点阵针雨"),
    ("matrix_v38_space_dash_lattice", "空间点阵短划格"),
    ("matrix_v38_space_stair_ticks", "空间点阵阶梯刻度"),
    ("matrix_v38_space_offset_stars", "空间点阵错位星"),
    ("matrix_v38_space_diagonal_beads", "空间点阵斜珠"),
    ("matrix_v38_space_counter_beads", "空间点阵反斜珠"),
    ("matrix_v38_space_morse_rows", "空间点阵摩斯行"),
    ("matrix_v38_space_morse_cols", "空间点阵摩斯列"),
    ("matrix_v38_space_cluster_walk", "空间点阵簇游走"),
    ("matrix_v38_space_corner_ticks", "空间点阵角刻"),
    ("matrix_v38_space_axis_ticks", "空间点阵轴刻"),
    ("matrix_v38_space_ring_ticks", "空间点阵环刻"),
    ("matrix_v38_space_diamond_ticks", "空间点阵菱刻"),
    ("matrix_v38_space_square_ticks", "空间点阵方刻"),
    ("matrix_v38_space_wave_markers", "空间点阵波标"),
    ("matrix_v38_space_phase_markers", "空间点阵相位标"),
    ("matrix_v38_space_shifting_grid", "空间点阵移格"),
    ("matrix_v38_space_shattered_grid", "空间点阵碎格"),
    ("matrix_v38_space_constellation_rows", "空间点阵星列"),
    ("matrix_v38_space_constellation_cols", "空间点阵星栏"),
    ("matrix_v38_space_pulse_motes", "空间点阵脉粒"),
    ("matrix_v38_space_echo_motes", "空间点阵回声粒"),
    ("matrix_v38_space_tilted_ticks", "空间点阵倾斜刻"),
    ("matrix_v38_space_folded_ticks", "空间点阵折叠刻"),
    ("matrix_v38_space_zipper", "空间点阵拉链"),
    ("matrix_v38_space_double_zipper", "空间点阵双拉链"),
    ("matrix_v38_space_pixel_snow", "空间点阵像素雪"),
    ("matrix_v38_space_pixel_sparks", "空间点阵火星"),
    ("matrix_v38_space_metered_pebbles", "空间点阵节拍石"),
    ("matrix_v38_space_breathing_marks", "空间点阵呼吸标"),
]

AUDIO_BAR_MODES: List[Tuple[str, str]] = [
    ("matrix_v38_audio_bar_solid_plus", "音频条 实心增强"),
    ("matrix_v38_audio_bar_center_ribbon", "音频条 中心缎带"),
    ("matrix_v38_audio_bar_scan_stripe", "音频条 扫描条纹"),
    ("matrix_v38_audio_bar_dot_steps", "音频条 点阵阶梯"),
    ("matrix_v38_audio_bar_diag_mesh", "音频条 斜向网格"),
    ("matrix_v38_audio_bar_antidiag_mesh", "音频条 反斜网格"),
    ("matrix_v38_audio_bar_ring_peak", "音频条 环形峰值"),
    ("matrix_v38_audio_bar_wave_fill", "音频条 波纹填充"),
    ("matrix_v38_audio_bar_peak_hold_line", "音频条 峰值保持线"),
    ("matrix_v38_audio_bar_noise_cells", "音频条 噪声格"),
    ("matrix_v38_audio_bar_sweep_blade", "音频条 扫光刀"),
    ("matrix_v38_audio_bar_center_blade", "音频条 中心光刀"),
    ("matrix_v38_audio_bar_alternate_mirror", "音频条 交替镜像"),
    ("matrix_v38_audio_bar_dot_burst", "音频条 点阵爆闪"),
    ("matrix_v38_audio_bar_stripe_burst", "音频条 条纹爆闪"),
    ("matrix_v38_audio_bar_sine_shade", "音频条 正弦阴影"),
    ("matrix_v38_audio_bar_ring_overlay", "音频条 环纹叠加"),
    ("matrix_v38_audio_bar_x_overlay", "音频条 X纹叠加"),
    ("matrix_v38_audio_bar_level_ribbon", "音频条 液位缎带"),
    ("matrix_v38_audio_bar_phase_marks", "音频条 相位刻痕"),
    ("matrix_v38_audio_bar_soft_cap", "音频条 柔边帽"),
    ("matrix_v38_audio_bar_tri_gate", "音频条 三角门"),
    ("matrix_v38_audio_bar_checker_gate", "音频条 棋盘门"),
    ("matrix_v38_audio_bar_random_peaks", "音频条 随机峰"),
    ("matrix_v38_audio_bar_wave_texture", "音频条 波纹纹理"),
    ("matrix_v38_audio_bar_slash_gate", "音频条 斜杠门"),
    ("matrix_v38_audio_bar_backslash_gate", "音频条 反斜杠门"),
    ("matrix_v38_audio_bar_square_frame", "音频条 方框峰"),
    ("matrix_v38_audio_bar_diamond_frame", "音频条 菱框峰"),
    ("matrix_v38_audio_bar_spoke_meter", "音频条 辐射表"),
]


def register_v38_matrix_ext(pixel_modes_ext: Dict[str, List[Tuple[str, str]]],
                            compute_map: Dict[str, Callable]) -> None:
    pixel_modes_ext.setdefault("像素花样-v38", []).extend(PIXEL_PATTERN_MODES)
    pixel_modes_ext.setdefault("空间点阵-v38", []).extend(SPACE_DOT_MODES)
    pixel_modes_ext.setdefault("音频条-v38", []).extend(AUDIO_BAR_MODES)

    pattern_modes = PIXEL_PATTERN_MODES + SPACE_DOT_MODES
    compute_map.update({
        mode_id: (lambda engine, m=mode_id, v=idx: _compute_pattern(engine, m, v))
        for idx, (mode_id, _label) in enumerate(pattern_modes)
    })
    compute_map.update({
        mode_id: (lambda engine, m=mode_id, v=idx: _compute_audio_bar(engine, m, v))
        for idx, (mode_id, _label) in enumerate(AUDIO_BAR_MODES)
    })
