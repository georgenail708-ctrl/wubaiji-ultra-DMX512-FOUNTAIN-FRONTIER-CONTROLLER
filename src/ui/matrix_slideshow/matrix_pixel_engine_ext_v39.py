from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Tuple

from .matrix_pixel_audio_modes import should_simulate_audio_env, versioned_audio_gain, versioned_audio_rms


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else float(v)


def _tri(x: float) -> float:
    p = float(x) % 1.0
    return 1.0 - abs(p * 2.0 - 1.0)


def _smooth(edge0: float, edge1: float, x: float) -> float:
    if abs(edge1 - edge0) < 1e-9:
        return 1.0 if x >= edge1 else 0.0
    t = _clamp01((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _band(distance: float, width: float, softness: float) -> float:
    distance = abs((float(distance) + 0.5) % 1.0 - 0.5)
    width = max(0.001, min(1.0, float(width)))
    softness = max(0.001, min(0.8, float(softness)))
    inner = width * max(0.0, 1.0 - softness)
    if distance <= inner:
        return 1.0
    return 1.0 - _smooth(inner, max(inner + 1e-6, width), distance)


def _hash01(a: int, b: int, c: int = 0) -> float:
    n = int(a) * 374761393 + int(b) * 668265263 + int(c) * 1442695041
    n = (n ^ (n >> 13)) * 1274126177
    return ((n ^ (n >> 16)) & 0xFFFFFFFF) / 4294967295.0


def _audio_env(engine, variant: int) -> tuple[float, float, float, float, bool]:
    use_audio = bool(getattr(engine, "pixel_audio_reactive", True))
    bands, rms = [], 0.0
    if use_audio:
        try:
            bands = [float(v) for v in engine._get_audio_bands()]
            rms = float(engine._get_audio_rms())
        except Exception:
            bands, rms = [], 0.0
    if should_simulate_audio_env(engine, bands):
        t = float(getattr(engine, "_t", 0.0))
        bass = 0.28 + 0.52 * math.sin(t * (0.42 + 0.015 * variant)) ** 2
        mid = 0.22 + 0.48 * _tri(t * (0.25 + 0.007 * (variant % 13)) + variant * 0.017)
        high = 0.15 + 0.42 * math.sin(t * (0.90 + 0.025 * (variant % 9)) + variant * 0.11) ** 2
        rms = (bass + mid + high) / 3.0
        return bass, mid, high, rms, _tri(t * 0.62 + variant * 0.021) > 0.92
    amp = versioned_audio_gain(engine, max(0.2, float(getattr(engine, "audio_amplitude", 2.0) or 2.0)))
    bass = min(1.0, sum(bands[:2]) / max(1, min(2, len(bands))) * amp)
    mid = min(1.0, sum(bands[2:5]) / max(1, min(3, max(0, len(bands) - 2))) * amp) if len(bands) > 2 else bass
    high = min(1.0, sum(bands[5:8]) / max(1, min(3, max(0, len(bands) - 5))) * amp) if len(bands) > 5 else mid
    try:
        beat = bool(engine._get_is_beat())
    except Exception:
        beat = False
    return bass, mid, high, versioned_audio_rms(engine, rms, amp), beat


def _grid_values(engine):
    grid = engine._grid
    if grid is None:
        return None
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    aspect = cols / max(1, rows)
    return grid, cols, rows, aspect


def _coords(grid, cols: int, rows: int, aspect: float, i: int, twist: float, phase: float):
    col, row = grid.get_col_row(i)
    nx = col / max(1, cols - 1)
    ny = row / max(1, rows - 1)
    x = (nx - 0.5) * max(1.0, aspect)
    y = (ny - 0.5) * max(1.0, 1.0 / max(0.001, aspect))
    rot = twist * 0.20 + phase * 0.07
    cr, sr = math.cos(rot), math.sin(rot)
    rx = x * cr - y * sr
    ry = x * sr + y * cr
    return col, row, nx, ny, rx, ry, math.atan2(ry, rx), math.hypot(rx, ry)


def _params(engine, variant: int):
    density = max(0.01, float(getattr(engine, "matrix_wave_density", 1.0) or 1.0))
    width = max(0.004, min(2.0, float(getattr(engine, "matrix_wave_width", 0.12) or 0.12)))
    twist = float(getattr(engine, "matrix_wave_twist", 0.0) or 0.0)
    mix = max(0.0, min(1.0, float(getattr(engine, "matrix_symmetry_mix", 0.5) or 0.5)))
    threshold = max(0.0, min(1.0, float(getattr(engine, "threshold", 0.5) or 0.5)))
    t = float(getattr(engine, "_t", 0.0))
    bass, mid, high, rms, beat = _audio_env(engine, variant)
    phase = t * (0.18 + (variant % 11) * 0.017 + bass * 0.10 + rms * 0.08) + (0.08 if beat else 0.0)
    freq = (1.2 + (variant % 17) * 0.13 + mid * 2.2) * density
    soft = max(0.01, min(0.65, width * (0.25 + high * 0.35)))
    return density, width, twist, mix, threshold, t, phase, freq, soft, bass, mid, high, rms, beat


def _asym_metric(family: int, nx: float, ny: float, x: float, y: float, angle: float,
                 r: float, t: float, phase: float, mix: float, bass: float, mid: float, high: float) -> float:
    shear = nx + (ny - 0.5) * (0.35 + mix) + 0.12 * math.sin(t + ny * math.tau)
    if family == 0:
        return shear
    if family == 1:
        return ny + (nx - 0.5) * (0.45 + mix) + 0.10 * math.cos(t + nx * math.tau)
    if family == 2:
        return nx * 1.7 - ny * 0.6 + 0.2 * math.sin((nx + ny) * math.tau + phase)
    if family == 3:
        return ny * 1.5 + nx * 0.4 + 0.22 * math.cos((nx - ny) * math.tau - phase)
    if family == 4:
        return r * (2.0 + bass * 2.0) + angle / math.tau * (0.7 + mix)
    if family == 5:
        return abs(x * (1.0 + mix) + y * 0.35) + 0.25 * math.sin(angle * 3.0 + t)
    if family == 6:
        return abs(y * (1.0 + mix) - x * 0.25) + 0.25 * math.cos(angle * 4.0 - t)
    if family == 7:
        return (math.floor((nx + phase * 0.05) * (4 + int(mid * 5))) + ny * 4.0) * 0.17
    if family == 8:
        return (math.floor((ny - phase * 0.04) * (5 + int(high * 5))) + nx * 3.0) * 0.19
    if family == 9:
        return min(math.hypot(nx - 0.15 - 0.25 * math.sin(t * 0.3), ny - 0.35), math.hypot(nx - 0.85, ny - 0.72 + 0.2 * math.cos(t * 0.2))) * 3.0
    if family == 10:
        return abs(math.sin((nx * ny + phase * 0.04) * math.pi * (6.0 + mix * 6.0)))
    if family == 11:
        return abs(math.cos(((nx - 0.5) * (ny - 0.5) - phase * 0.03) * math.pi * (10.0 + mix * 5.0)))
    if family == 12:
        return (nx + 0.18 * math.sin(ny * math.tau * (2.0 + bass))) * (2.0 + mix)
    if family == 13:
        return (ny + 0.18 * math.cos(nx * math.tau * (2.0 + mid))) * (2.0 + mix)
    if family == 14:
        return max(abs(x + y * 0.5), abs(y - x * 0.2)) * (2.5 + high * 2.0)
    if family == 15:
        return min(abs(x - 0.2 * math.sin(t)), abs(y + 0.2 * math.cos(t))) * (5.0 + mix * 3.0)
    if family == 16:
        return _hash01(int((nx + t * 0.03) * 12), int(ny * 9), int(phase * 7)) * 0.7 + nx * 0.8
    if family == 17:
        return _hash01(int(nx * 10), int((ny - t * 0.025) * 13), int(phase * 5)) * 0.7 + ny * 0.8
    if family == 18:
        return abs(math.sin((nx * 5.0 + ny * 2.0 + phase) * math.pi)) * (0.7 + mix)
    if family == 19:
        return abs(math.cos((ny * 5.0 - nx * 2.0 - phase) * math.pi)) * (0.7 + mix)
    if family == 20:
        return abs(nx - _tri(t * 0.11 + ny * 0.7)) * 2.0
    if family == 21:
        return abs(ny - _tri(t * 0.13 + nx * 0.6)) * 2.0
    if family == 22:
        return abs(math.sin((r * 3.0 + nx * 1.5 - phase) * math.pi))
    if family == 23:
        return abs(math.cos((r * 4.0 - ny * 1.5 + phase) * math.pi))
    if family == 24:
        return (abs(x) ** (1.0 + mix) + abs(y) ** (1.6 + high)) * (2.0 + bass)
    if family == 25:
        return (abs(y) ** (1.0 + mix) + abs(x) ** (1.6 + mid)) * (2.0 + high)
    if family == 26:
        return (math.floor((nx + ny + phase * 0.04) * 6.0) % 3) / 2.0 + r * mix
    if family == 27:
        return (math.floor((nx - ny - phase * 0.04) * 7.0) % 4) / 3.0 + abs(x + y) * mix
    if family == 28:
        return abs(math.sin((angle * 2.0 + r * (5.0 + mix * 3.0) - phase) * math.pi))
    return abs(math.cos((angle * 3.0 - r * (4.0 + mix * 3.0) + phase) * math.pi))


def _compute_asym(engine, _mode_id: str, variant: int) -> Dict[str, float]:
    gv = _grid_values(engine)
    if gv is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    grid, cols, rows, aspect = gv
    density, width, twist, mix, _threshold, t, phase, freq, soft, bass, mid, high, rms, beat = _params(engine, variant)
    family = variant % 30
    weights: Dict[str, float] = {}
    for i, fid in enumerate(engine._sorted_ids):
        _col, _row, nx, ny, x, y, angle, r = _coords(grid, cols, rows, aspect, i, twist, phase)
        metric = _asym_metric(family, nx, ny, x, y, angle, r, t, phase, mix, bass, mid, high)
        style = variant % 6
        if style == 0:
            val = _band(metric * freq - phase, width, soft)
        elif style == 1:
            val = _tri(metric * freq - phase) ** (1.0 + high * 1.8)
        elif style == 2:
            val = 1.0 if ((metric * freq - phase) % 1.0) < max(0.02, min(0.85, width * (1.4 + mix))) else 0.0
        elif style == 3:
            val = _clamp01(_band(metric * freq - phase, width, soft) * 0.75 + _hash01(int(nx * 12), int(ny * 11), variant + int(t * 5)) * (0.15 + rms * 0.25))
        elif style == 4:
            val = max(_band(metric * freq - phase, width, soft), _band(metric * (freq * 0.55) + phase * 0.7, width * 1.6, soft * 1.2) * (0.35 + mid))
        else:
            sweep = _smooth(0.0, 1.0, (metric * 0.5 + phase * 0.15) % 1.0)
            val = _clamp01(sweep * (0.35 + bass * 0.4) + _band(metric * freq - phase, width, soft) * 0.65)
        weights[fid] = _clamp01(val)
    return weights


def _particle_sources(variant: int, t: float, count: int, bass: float, mid: float, high: float):
    out = []
    speed = 0.12 + 0.04 * (variant % 7) + bass * 0.10
    for k in range(count):
        seed = variant * 101 + k * 37
        base_x = _hash01(seed, 1)
        base_y = _hash01(seed, 2)
        mode = variant % 10
        if mode == 0:
            x = (base_x + t * speed * (0.5 + _hash01(seed, 3))) % 1.0
            y = (base_y + 0.08 * math.sin(t * 0.7 + k)) % 1.0
        elif mode == 1:
            x = (base_x + 0.08 * math.sin(t + k)) % 1.0
            y = (base_y + t * speed * (0.5 + _hash01(seed, 4))) % 1.0
        elif mode == 2:
            ang = t * speed * 2.0 + k * math.tau / max(1, count)
            rad = 0.18 + 0.28 * _hash01(seed, 5) + mid * 0.08
            x = 0.5 + math.cos(ang) * rad
            y = 0.5 + math.sin(ang * (0.8 + high * 0.3)) * rad
        elif mode == 3:
            x = 0.5 + (base_x - 0.5) * _tri(t * speed + k * 0.13)
            y = 0.5 + (base_y - 0.5) * _tri(t * speed * 0.7 + k * 0.17)
        elif mode == 4:
            x = _tri(t * speed + base_x + k * 0.07)
            y = _tri(t * speed * 1.3 + base_y + k * 0.11)
        elif mode == 5:
            step = int(t * (3.0 + bass * 7.0) + k)
            x = _hash01(seed, step)
            y = _hash01(seed + 1, step)
        elif mode == 6:
            x = (base_x + math.sin(t * 0.5 + k) * 0.25) % 1.0
            y = (base_y + math.cos(t * 0.4 + k * 0.7) * 0.25) % 1.0
        elif mode == 7:
            x = 0.5 + math.cos(t * speed + k) * (0.10 + 0.35 * _tri(t * 0.2 + base_x))
            y = 0.5 + math.sin(t * speed + k) * (0.10 + 0.35 * _tri(t * 0.2 + base_y))
        elif mode == 8:
            x = (base_x + t * speed) % 1.0
            y = (base_y + t * speed * 0.73) % 1.0
        else:
            x = (base_x + math.sin(t * speed + base_y * math.tau) * 0.35) % 1.0
            y = (base_y + math.sin(t * speed * 1.4 + base_x * math.tau) * 0.35) % 1.0
        out.append((_clamp01(x), _clamp01(y), 0.6 + 0.4 * _hash01(seed, 9)))
    return out


def _compute_particle(engine, _mode_id: str, variant: int) -> Dict[str, float]:
    gv = _grid_values(engine)
    if gv is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    grid, cols, rows, aspect = gv
    density, width, twist, mix, _threshold, t, phase, _freq, soft, bass, mid, high, rms, beat = _params(engine, variant)
    count = max(3, min(48, int(5 + density * (4 + variant % 9) + rms * 10)))
    particles = _particle_sources(variant, t + phase * 0.2, count, bass, mid, high)
    gravity = 0.12 * math.sin(t * 0.6 + variant)
    weights: Dict[str, float] = {}
    for i, fid in enumerate(engine._sorted_ids):
        _col, _row, nx, ny, _x, _y, angle, r = _coords(grid, cols, rows, aspect, i, twist, phase)
        val = 0.0
        for pidx, (px, py, bright) in enumerate(particles):
            dx = nx - px
            dy = ny - py - gravity * mix
            if variant % 6 in {1, 4}:
                dx = abs(dx)
                dy = abs(dy)
            if variant % 6 == 2:
                dx += 0.08 * math.sin(angle * (2 + pidx % 3) + t)
                dy += 0.08 * math.cos(angle * (2 + pidx % 4) - t)
            dist = math.hypot(dx * aspect, dy)
            core = max(0.0, 1.0 - dist / max(0.015, width * (0.45 + mix * 0.65)))
            trail = max(0.0, 1.0 - abs((dx + dy) - phase * 0.08) / max(0.02, width * 1.8)) * 0.25
            val = max(val, (core ** (1.2 + high * 1.6) + trail * mix) * bright)
        if variant % 5 == 3:
            val = max(val, _hash01(int(nx * cols), int(ny * rows), int(t * 12) + variant) * rms * 0.55)
        if beat:
            val = max(val, _band(r * (2.0 + mix) - phase * 0.5, width * 1.2, soft) * 0.75)
        weights[fid] = _clamp01(val)
    return weights


def _shape_metric(family: int, nx: float, ny: float, x: float, y: float, angle: float,
                  r: float, t: float, phase: float, mix: float, bass: float, mid: float, high: float) -> float:
    circle = r
    diamond = abs(x) + abs(y)
    square = max(abs(x), abs(y))
    morph = circle * (1.0 - mix) + diamond * mix
    if family == 0:
        return morph + 0.12 * math.sin(angle * 4.0 + phase)
    if family == 1:
        return circle * (1.0 - mix) + square * mix + 0.10 * math.cos(angle * 6.0 - phase)
    if family == 2:
        return diamond * (1.0 - mix) + square * mix + 0.12 * math.sin((nx + ny) * math.tau + phase)
    if family == 3:
        return abs(math.sin(angle * (3 + int(bass * 5)) + phase)) * 0.35 + r
    if family == 4:
        return abs(math.cos(angle * (4 + int(mid * 5)) - phase)) * 0.35 + diamond
    if family == 5:
        return max(abs(x * math.cos(phase) + y * math.sin(phase)), abs(-x * math.sin(phase) + y * math.cos(phase))) + r * mix
    if family == 6:
        return min(abs(x), abs(y)) * (3.0 + mix * 3.0) + r * 0.4
    if family == 7:
        return max(abs(x), abs(y)) * (2.0 + mix * 2.0) - min(abs(x), abs(y)) * mix
    if family == 8:
        return abs(math.sin((diamond * (3.0 + bass * 3.0) + angle / math.tau) * math.pi - phase))
    if family == 9:
        return abs(math.cos((square * (4.0 + mid * 3.0) - angle / math.tau) * math.pi + phase))
    if family == 10:
        return min(math.hypot(nx - 0.25, ny - 0.25), math.hypot(nx - 0.75, ny - 0.75), math.hypot(nx - 0.25, ny - 0.75), math.hypot(nx - 0.75, ny - 0.25)) * (2.5 + mix)
    if family == 11:
        return min(math.hypot(nx - 0.5, ny - 0.18), math.hypot(nx - 0.82, ny - 0.5), math.hypot(nx - 0.5, ny - 0.82), math.hypot(nx - 0.18, ny - 0.5)) * (2.8 + mix)
    if family == 12:
        return abs(x + y) * (1.2 + mix) + abs(x - y) * (0.4 + high)
    if family == 13:
        return abs(x - y) * (1.2 + mix) + abs(x + y) * (0.4 + bass)
    if family == 14:
        return abs(math.sin((nx * (3 + int(mix * 5)) + ny * 2.0 + phase) * math.pi))
    if family == 15:
        return abs(math.cos((ny * (3 + int(mix * 5)) - nx * 2.0 - phase) * math.pi))
    if family == 16:
        return (circle + diamond + square) / 3.0 + 0.12 * math.sin(t + angle * 5.0)
    if family == 17:
        return abs((circle - diamond) * (2.0 + mix)) + square * 0.35
    if family == 18:
        return abs((square - circle) * (2.0 + mix)) + diamond * 0.25
    if family == 19:
        return abs(math.sin((r + 0.2 * math.sin(angle * 5.0 + phase)) * math.pi * (3.0 + mix * 4.0)))
    if family == 20:
        return abs(math.cos((diamond + 0.2 * math.cos(angle * 4.0 - phase)) * math.pi * (3.0 + mix * 4.0)))
    if family == 21:
        return max(0.0, abs(x) - 0.15 * math.sin(phase)) + max(0.0, abs(y) - 0.15 * math.cos(phase))
    if family == 22:
        return min(abs(nx - _tri(t * 0.14)), abs(ny - _tri(t * 0.11 + 0.33))) * (4.0 + mix * 4.0)
    if family == 23:
        return abs(math.sin((angle / math.tau + r * (2.0 + mix)) * math.tau + phase))
    return abs(math.cos((angle / math.tau - diamond * (2.0 + mix)) * math.tau - phase))


def _compute_shape(engine, _mode_id: str, variant: int) -> Dict[str, float]:
    gv = _grid_values(engine)
    if gv is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    grid, cols, rows, aspect = gv
    density, width, twist, mix, _threshold, t, phase, freq, soft, bass, mid, high, rms, beat = _params(engine, variant)
    family = variant % 25
    weights: Dict[str, float] = {}
    for i, fid in enumerate(engine._sorted_ids):
        _col, _row, nx, ny, x, y, angle, r = _coords(grid, cols, rows, aspect, i, twist, phase)
        metric = _shape_metric(family, nx, ny, x, y, angle, r, t, phase, mix, bass, mid, high)
        style = variant % 5
        if style == 0:
            val = _band(metric * freq - phase, width * (1.0 + mix), soft)
        elif style == 1:
            val = _tri(metric * freq - phase) ** (1.1 + high * 1.5)
        elif style == 2:
            val = 1.0 if ((metric * freq - phase) % 1.0) < max(0.02, min(0.85, width * 1.8)) else 0.0
        elif style == 3:
            inner = _band(metric * (freq * 0.55) + phase * 0.4, width * 1.8, soft * 1.2)
            outer = _band(metric * freq - phase, width, soft)
            val = max(outer, inner * (0.35 + mix * 0.45))
        else:
            morph_gate = _smooth(0.15 + mix * 0.25, 0.75 + mix * 0.20, _tri(metric * freq - phase))
            val = morph_gate * (0.65 + rms * 0.35)
        if beat:
            val = max(val, _band(r * (2.0 + density) - phase, width * 1.4, soft) * 0.65)
        weights[fid] = _clamp01(val)
    return weights


ASYM_MOTION_MODES: List[Tuple[str, str]] = [
    ("matrix_v39_asym_shear_current", "非对称剪切流"),
    ("matrix_v39_asym_vertical_current", "非对称竖流"),
    ("matrix_v39_asym_slant_push", "非对称斜推"),
    ("matrix_v39_asym_counter_push", "非对称反推"),
    ("matrix_v39_asym_orbit_drift", "非对称轨道漂移"),
    ("matrix_v39_asym_wind_rake", "非对称风梳"),
    ("matrix_v39_asym_crosswind", "非对称侧风"),
    ("matrix_v39_asym_step_slide", "非对称阶梯滑移"),
    ("matrix_v39_asym_row_pull", "非对称行拉伸"),
    ("matrix_v39_asym_dual_source", "非对称双源"),
    ("matrix_v39_asym_mesh_warp", "非对称网格扭动"),
    ("matrix_v39_asym_cell_shear", "非对称单元剪切"),
    ("matrix_v39_asym_ribbon_left", "非对称左带"),
    ("matrix_v39_asym_ribbon_top", "非对称顶带"),
    ("matrix_v39_asym_folded_plane", "非对称折面"),
    ("matrix_v39_asym_axis_cut", "非对称轴切"),
    ("matrix_v39_asym_noise_flow_x", "非对称噪声横流"),
    ("matrix_v39_asym_noise_flow_y", "非对称噪声纵流"),
    ("matrix_v39_asym_diagonal_ribbons", "非对称斜带"),
    ("matrix_v39_asym_counter_ribbons", "非对称反斜带"),
    ("matrix_v39_asym_column_snake", "非对称列蛇形"),
    ("matrix_v39_asym_row_snake", "非对称行蛇形"),
    ("matrix_v39_asym_radial_slip", "非对称径向滑移"),
    ("matrix_v39_asym_angular_slip", "非对称角向滑移"),
    ("matrix_v39_asym_power_slope_x", "非对称X幂坡"),
    ("matrix_v39_asym_power_slope_y", "非对称Y幂坡"),
    ("matrix_v39_asym_stair_fan", "非对称阶梯扇"),
    ("matrix_v39_asym_stair_fold", "非对称阶梯折"),
    ("matrix_v39_asym_spoke_drag", "非对称辐条拖曳"),
    ("matrix_v39_asym_spoke_return", "非对称辐条回卷"),
]

PARTICLE_PHYSICS_MODES: List[Tuple[str, str]] = [
    ("matrix_v39_particle_drift_dust", "粒子漂尘"),
    ("matrix_v39_particle_vertical_rain", "粒子竖雨"),
    ("matrix_v39_particle_orbit_motes", "粒子轨道微尘"),
    ("matrix_v39_particle_breathe_swarm", "粒子呼吸群"),
    ("matrix_v39_particle_bounce_field", "粒子弹跳场"),
    ("matrix_v39_particle_random_bursts", "粒子随机爆点"),
    ("matrix_v39_particle_sine_float", "粒子正弦漂浮"),
    ("matrix_v39_particle_ring_flock", "粒子环群"),
    ("matrix_v39_particle_diag_stream", "粒子斜向流"),
    ("matrix_v39_particle_lissajous", "粒子李萨如"),
    ("matrix_v39_particle_gravity_drops", "粒子重力滴"),
    ("matrix_v39_particle_wind_sparks", "粒子风火星"),
    ("matrix_v39_particle_magnetic_pull", "粒子磁吸"),
    ("matrix_v39_particle_magnetic_push", "粒子磁斥"),
    ("matrix_v39_particle_corner_emit", "粒子角发射"),
    ("matrix_v39_particle_edge_emit", "粒子边缘发射"),
    ("matrix_v39_particle_center_burst", "粒子中心爆散"),
    ("matrix_v39_particle_dual_comets", "粒子双彗尾"),
    ("matrix_v39_particle_starry_noise", "粒子星噪"),
    ("matrix_v39_particle_fireflies", "粒子萤火"),
    ("matrix_v39_particle_snow_shear", "粒子斜雪"),
    ("matrix_v39_particle_ripple_seeds", "粒子涟漪种子"),
    ("matrix_v39_particle_tracer_pack", "粒子追踪群"),
    ("matrix_v39_particle_stream_wake", "粒子流尾迹"),
    ("matrix_v39_particle_echo_cloud", "粒子回声云"),
    ("matrix_v39_particle_grid_sparks", "粒子格点火星"),
    ("matrix_v39_particle_column_fall", "粒子列坠"),
    ("matrix_v39_particle_row_sweep", "粒子行扫"),
    ("matrix_v39_particle_turbulence", "粒子湍流"),
    ("matrix_v39_particle_shower", "粒子阵雨"),
]

SHAPE_MORPH_MODES: List[Tuple[str, str]] = [
    ("matrix_v39_shape_circle_diamond", "形变圆转菱"),
    ("matrix_v39_shape_circle_square", "形变圆转方"),
    ("matrix_v39_shape_diamond_square", "形变菱转方"),
    ("matrix_v39_shape_petals_open", "形变花瓣打开"),
    ("matrix_v39_shape_petals_fold", "形变花瓣折叠"),
    ("matrix_v39_shape_rotating_box", "形变旋转方框"),
    ("matrix_v39_shape_cross_gap", "形变十字缝"),
    ("matrix_v39_shape_frame_pressure", "形变边框压力"),
    ("matrix_v39_shape_diamond_twist", "形变菱形旋拧"),
    ("matrix_v39_shape_square_twist", "形变方形旋拧"),
    ("matrix_v39_shape_corner_pull", "形变四角拉伸"),
    ("matrix_v39_shape_axis_pull", "形变轴线拉伸"),
    ("matrix_v39_shape_slash_fold", "形变斜向折叠"),
    ("matrix_v39_shape_backslash_fold", "形变反斜折叠"),
    ("matrix_v39_shape_grid_morph_x", "形变X向网格"),
    ("matrix_v39_shape_grid_morph_y", "形变Y向网格"),
    ("matrix_v39_shape_three_metric", "形变三指标"),
    ("matrix_v39_shape_circle_minus_diamond", "形变圆菱差"),
    ("matrix_v39_shape_square_minus_circle", "形变方圆差"),
    ("matrix_v39_shape_wobble_ring", "形变摇摆环"),
    ("matrix_v39_shape_wobble_diamond", "形变摇摆菱"),
    ("matrix_v39_shape_window_gate", "形变窗口门"),
    ("matrix_v39_shape_moving_crosshair", "形变移动准星"),
    ("matrix_v39_shape_spiral_shell", "形变螺旋壳"),
    ("matrix_v39_shape_folded_shell", "形变折叠壳"),
]


def _relabeled_modes(modes: List[Tuple[str, str]], labels: List[str]) -> List[Tuple[str, str]]:
    return [
        (mode_id, labels[idx % len(labels)] + f" {idx + 1:02d}")
        for idx, (mode_id, _old_label) in enumerate(modes)
    ]


def register_v39_matrix_ext(pixel_modes_ext: Dict[str, List[Tuple[str, str]]],
                            compute_map: Dict[str, Callable]) -> None:
    asym_labels = [
        "非对称剪切流", "非对称竖向流", "斜向推光", "反斜推光", "偏心轨道漂移",
        "风向梳理", "侧风拉带", "阶梯滑移", "行向拉伸", "双源偏移",
        "网格扭动", "单元剪切", "左侧光带", "顶部光带", "折面推进",
        "轴线切割", "噪声横流", "噪声纵流", "斜带拖曳", "反斜带拖曳",
        "列蛇形", "行蛇形", "径向滑移", "角向滑移", "X幂坡",
        "Y幂坡", "阶梯扇形", "阶梯折返", "辐条拖曳", "辐条回卷",
    ]
    particle_labels = [
        "粒子漂尘", "粒子竖雨", "轨道微尘", "呼吸粒群", "弹跳粒场",
        "随机爆点", "正弦漂浮", "环形粒群", "斜向粒流", "李萨如粒子",
        "重力滴落", "风向火星", "磁吸粒子", "磁斥粒子", "角发射",
        "边缘发射", "中心爆散", "双彗尾", "星点噪声", "微光漂移",
        "斜雪", "涟漪种子", "追踪粒群", "流尾迹", "回声云",
        "格点火星", "列坠落", "行扫粒", "湍流粒云", "阵雨粒子",
    ]
    shape_labels = [
        "圆转菱形", "圆转方形", "菱转方形", "花瓣打开", "花瓣折叠",
        "旋转方框", "十字缝开合", "边框压力", "菱形旋拧", "方形旋拧",
        "四角拉伸", "轴线拉伸", "斜向折叠", "反斜折叠", "X向网格形变",
        "Y向网格形变", "三指标形变", "圆菱差形", "方圆差形", "摇摆圆环",
        "摇摆菱形", "窗口门控", "移动准星", "螺旋外壳", "折叠外壳",
    ]
    pixel_modes_ext.setdefault("非对称运动-v39", []).extend(
        _relabeled_modes(ASYM_MOTION_MODES, asym_labels))
    pixel_modes_ext.setdefault("粒子物理-v39", []).extend(
        _relabeled_modes(PARTICLE_PHYSICS_MODES, particle_labels))
    pixel_modes_ext.setdefault("形状变形-v39", []).extend(
        _relabeled_modes(SHAPE_MORPH_MODES, shape_labels))

    compute_map.update({
        mode_id: (lambda engine, m=mode_id, v=idx: _compute_asym(engine, m, v))
        for idx, (mode_id, _label) in enumerate(ASYM_MOTION_MODES)
    })
    compute_map.update({
        mode_id: (lambda engine, m=mode_id, v=idx: _compute_particle(engine, m, v))
        for idx, (mode_id, _label) in enumerate(PARTICLE_PHYSICS_MODES)
    })
    compute_map.update({
        mode_id: (lambda engine, m=mode_id, v=idx: _compute_shape(engine, m, v))
        for idx, (mode_id, _label) in enumerate(SHAPE_MORPH_MODES)
    })
