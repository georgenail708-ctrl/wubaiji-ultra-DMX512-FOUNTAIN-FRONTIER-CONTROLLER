from __future__ import annotations

import math
from typing import Callable, Dict, List, Tuple

from .matrix_pixel_audio_modes import should_simulate_audio_env, versioned_audio_gain, versioned_audio_rms, versioned_v40_audio_levels


V40_MATRIX_MODE_COUNT = 1254


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else float(v)


def _tri(x: float) -> float:
    p = float(x) % 1.0
    return 1.0 - abs(p * 2.0 - 1.0)


def _smooth(edge0: float, edge1: float, x: float) -> float:
    if abs(edge1 - edge0) < 1e-9:
        return 1.0 if x >= edge1 else 0.0
    t = _clamp01((float(x) - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _dist_phase(a: float, b: float = 0.0) -> float:
    return abs(((float(a) - float(b) + 0.5) % 1.0) - 0.5)


def _band(metric: float, width: float, softness: float) -> float:
    p = _dist_phase(metric, 0.0)
    width = max(0.001, min(1.5, float(width)))
    softness = max(0.001, min(1.0, float(softness)))
    inner = width * max(0.0, 1.0 - softness)
    if p <= inner:
        return 1.0
    return 1.0 - _smooth(inner, max(inner + 1e-6, width), p)


def _hash01(a: int, b: int, c: int = 0) -> float:
    n = int(a) * 374761393 + int(b) * 668265263 + int(c) * 1442695041
    n = (n ^ (n >> 13)) * 1274126177
    return ((n ^ (n >> 16)) & 0xFFFFFFFF) / 4294967295.0


def _audio_env(engine, variant: int) -> Tuple[float, float, float, float, bool]:
    use_audio = bool(getattr(engine, "pixel_audio_reactive", True))
    bands = []
    rms = 0.0
    if use_audio:
        try:
            bands = [float(v) for v in engine._get_audio_bands()]
            rms = float(engine._get_audio_rms())
        except Exception:
            bands = []
            rms = 0.0
    if should_simulate_audio_env(engine, bands):
        t = float(getattr(engine, "_t", 0.0))
        bass = 0.28 + 0.55 * math.sin(t * (0.42 + 0.011 * variant)) ** 2
        mid = 0.20 + 0.50 * _tri(t * (0.21 + 0.009 * (variant % 17)) + variant * 0.031)
        high = 0.14 + 0.45 * math.sin(t * (0.80 + 0.021 * (variant % 13)) + variant * 0.13) ** 2
        rms = (bass + mid + high) / 3.0
        beat = _tri(t * 0.58 + variant * 0.019) > 0.93
        return bass, mid, high, rms, beat
    amp = versioned_audio_gain(engine, max(0.2, float(getattr(engine, "audio_amplitude", 2.0) or 2.0)))
    bass = min(1.0, sum(bands[:2]) / max(1, min(2, len(bands))) * amp)
    mid = min(1.0, sum(bands[2:5]) / max(1, min(3, max(0, len(bands) - 2))) * amp) if len(bands) > 2 else bass
    high = min(1.0, sum(bands[5:8]) / max(1, min(3, max(0, len(bands) - 5))) * amp) if len(bands) > 5 else mid
    try:
        beat = bool(engine._get_is_beat())
    except Exception:
        beat = False
    return bass, mid, high, versioned_audio_rms(engine, rms, amp), beat


def _grid_info(engine):
    grid = getattr(engine, "_grid", None)
    if grid is None:
        return None
    cols, rows = max(1, int(grid.cols)), max(1, int(grid.rows))
    aspect = cols / max(1, rows)
    return grid, cols, rows, aspect


def _coords(grid, cols: int, rows: int, aspect: float, index: int, twist: float, phase: float):
    col, row = grid.get_col_row(index)
    nx = col / max(1, cols - 1)
    ny = row / max(1, rows - 1)
    x = (nx - 0.5) * max(1.0, aspect)
    y = (ny - 0.5) * max(1.0, 1.0 / max(0.001, aspect))
    rot = twist * 0.22 + phase * 0.05
    cr, sr = math.cos(rot), math.sin(rot)
    rx = x * cr - y * sr
    ry = x * sr + y * cr
    return col, row, nx, ny, rx, ry, math.atan2(ry, rx), math.hypot(rx, ry)


def _params(engine, variant: int):
    density = max(0.01, float(getattr(engine, "matrix_wave_density", 1.0) or 1.0))
    width = max(0.004, min(2.0, float(getattr(engine, "matrix_wave_width", 0.12) or 0.12)))
    twist = float(getattr(engine, "matrix_wave_twist", 0.0) or 0.0)
    mix = max(0.0, min(1.0, float(getattr(engine, "matrix_symmetry_mix", 0.5) or 0.5)))
    speed = max(0.0, float(getattr(engine, "speed", 1.0) or 1.0))
    t = float(getattr(engine, "_t", 0.0))
    bass, mid, high, rms, beat = _audio_env(engine, variant)
    phase = t * speed * (0.13 + (variant % 17) * 0.006 + bass * 0.045 + rms * 0.035)
    phase += variant * 0.017 + (0.11 if beat else 0.0)
    freq = (1.0 + (variant % 23) * 0.08 + mid * 2.4) * density
    soft = max(0.01, min(0.85, width * (0.30 + high * 0.42)))
    return density, width, twist, mix, speed, t, phase, freq, soft, bass, mid, high, rms, beat


def _metric_family(family: int, nx: float, ny: float, x: float, y: float, angle: float,
                   r: float, t: float, phase: float, mix: float,
                   bass: float, mid: float, high: float, variant: int) -> float:
    diag = nx + ny
    anti = nx - ny
    diamond = abs(x) + abs(y)
    square = max(abs(x), abs(y))
    family %= 96
    if family == 0:
        return nx * (2.0 + mix * 3.0) + 0.18 * math.sin(ny * math.pi * 4.0 + phase)
    if family == 1:
        return ny * (2.0 + mix * 3.0) + 0.18 * math.cos(nx * math.pi * 4.0 - phase)
    if family == 2:
        return diag + 0.22 * math.sin(anti * math.pi * (3.0 + bass * 3.0))
    if family == 3:
        return anti + 0.22 * math.cos(diag * math.pi * (3.0 + mid * 3.0))
    if family == 4:
        return r * (2.4 + bass * 3.6) + angle / math.tau * (1.0 + mix)
    if family == 5:
        return angle / math.tau * (2.0 + mix * 5.0) + r * (1.4 + high * 2.6)
    if family == 6:
        return diamond * (3.0 + bass * 3.0) + 0.18 * math.sin(angle * 4.0 + t)
    if family == 7:
        return square * (3.0 + mid * 3.0) + 0.15 * math.cos(angle * 6.0 - t)
    if family == 8:
        return abs(math.sin((nx + phase * 0.03) * math.pi * (4 + variant % 7))) + ny * mix
    if family == 9:
        return abs(math.sin((ny - phase * 0.03) * math.pi * (4 + variant % 7))) + nx * mix
    if family == 10:
        return (math.floor(nx * (4 + variant % 8)) + math.floor(ny * (3 + variant % 7))) * 0.17
    if family == 11:
        return ((nx * (3 + variant % 7)) % 1.0) + ((ny * (5 + variant % 5)) % 1.0) * 0.35
    if family == 12:
        return min(abs(x), abs(y)) * (5.0 + bass * 5.0) + r * mix
    if family == 13:
        return max(abs(x), abs(y)) * (5.0 + mid * 4.0) - min(abs(x), abs(y)) * mix
    if family == 14:
        return abs(x * math.cos(phase) + y * math.sin(phase)) * (5.0 + high * 4.0)
    if family == 15:
        return abs(-x * math.sin(phase) + y * math.cos(phase)) * (5.0 + bass * 4.0)
    if family == 16:
        return r + 0.28 * math.sin(angle * (3 + variant % 9) + t * 0.7)
    if family == 17:
        return diamond + 0.24 * math.cos((nx * 2.0 - ny) * math.pi + phase)
    if family == 18:
        return square + 0.22 * math.sin((ny * 2.0 + nx) * math.pi - phase)
    if family == 19:
        return abs(math.sin((diag + phase * 0.02) * math.pi * (2 + variant % 8))) * 0.55 + r
    if family == 20:
        return abs(math.cos((anti - phase * 0.02) * math.pi * (2 + variant % 8))) * 0.55 + r
    if family == 21:
        return min(math.hypot(nx - 0.18, ny - 0.22), math.hypot(nx - 0.78, ny - 0.36), math.hypot(nx - 0.43, ny - 0.82)) * (4.0 + bass * 3.0)
    if family == 22:
        return (abs(x) ** (1.0 + mix) + abs(y) ** (1.4 + high)) * (2.6 + bass * 2.0)
    if family == 23:
        return (math.sin(nx * math.pi * (3 + variant % 6) + phase) + math.cos(ny * math.pi * (4 + variant % 5) - phase)) * 0.5
    if family == 24:
        return (math.floor((nx + ny + t * 0.03) * (4 + variant % 7)) % 2) + r * mix
    if family == 25:
        return (math.floor((nx - ny - t * 0.02) * (5 + variant % 6)) % 2) + diamond * mix
    if family == 26:
        return abs(math.sin((r * (4 + variant % 8) + angle / math.tau) * math.pi))
    if family == 27:
        return abs(math.cos((diamond * (3 + variant % 7) - angle / math.tau) * math.pi))
    if family == 28:
        return _hash01(int(nx * 12), int(ny * 12), variant) * 0.6 + r * 0.8
    if family == 29:
        return _hash01(int((nx + t * 0.02) * 16), int((ny - t * 0.02) * 16), variant) * 0.8 + diamond * 0.5
    if family == 30:
        return abs(nx - _tri(t * 0.13 + ny * 0.5)) * 2.0
    if family == 31:
        return abs(ny - _tri(t * 0.11 + nx * 0.6)) * 2.0
    if family == 32:
        return min(abs(nx - 0.25), abs(nx - 0.75)) * 3.0 + ny * mix
    if family == 33:
        return min(abs(ny - 0.25), abs(ny - 0.75)) * 3.0 + nx * mix
    if family == 34:
        return abs(math.sin((nx * ny + t * 0.03) * math.pi * (8 + variant % 11)))
    if family == 35:
        return abs(math.cos(((nx - 0.5) * (ny - 0.5) - t * 0.02) * math.pi * (10 + variant % 9)))
    if family == 36:
        return (abs(x + y) * 1.6 + abs(x - y) * 0.4) * (2.0 + mix)
    if family == 37:
        return (abs(x - y) * 1.6 + abs(x + y) * 0.4) * (2.0 + mix)
    if family == 38:
        return abs(math.sin((angle * (2 + variant % 7) + r * (5 + bass * 4.0)) * math.pi))
    if family == 39:
        return abs(math.cos((angle * (3 + variant % 8) - r * (4 + mid * 4.0)) * math.pi))
    if family == 40:
        return min(abs(nx - 0.5), abs(ny - 0.5)) * (5 + variant % 8)
    if family == 41:
        return max(abs(nx - 0.5), abs(ny - 0.5)) * (5 + variant % 8)
    if family == 42:
        return abs(math.sin((math.floor(nx * 8) + ny * 8 + t * 0.2) * math.pi * 0.5))
    if family == 43:
        return abs(math.cos((math.floor(ny * 8) + nx * 8 - t * 0.2) * math.pi * 0.5))
    if family == 44:
        return abs(math.sin(nx * math.pi * 6 + t)) * 0.35 + abs(math.sin(ny * math.pi * 4 - t * 0.7)) * 0.65
    if family == 45:
        return abs(math.cos(nx * math.pi * 5 - t * 0.8)) * 0.65 + abs(math.cos(ny * math.pi * 7 + t)) * 0.35
    if family == 46:
        return abs(math.sin((r + diamond * 0.35) * math.pi * (4 + variant % 7) - t))
    if family == 47:
        return abs(math.cos((square + r * 0.25) * math.pi * (5 + variant % 8) + t))
    if family == 48:
        return min(math.hypot(nx - 0.15 - 0.2 * math.sin(t), ny - 0.2), math.hypot(nx - 0.85, ny - 0.8 + 0.2 * math.cos(t))) * 4.0
    if family == 49:
        return min(math.hypot(nx - 0.25, ny - 0.75), math.hypot(nx - 0.75, ny - 0.25), math.hypot(nx - 0.5, ny - 0.5)) * 4.5
    if family == 50:
        return (abs(math.sin(diag * math.pi * 4.0 + phase)) + _hash01(int(nx * 8), int(ny * 8), variant) * mix) * 0.7
    if family == 51:
        return (abs(math.cos(anti * math.pi * 4.0 - phase)) + _hash01(int(nx * 9), int(ny * 7), variant) * mix) * 0.7
    if family == 52:
        return abs(math.sin((diamond + _hash01(int(nx * 5), int(ny * 5), variant) * 0.2) * math.pi * 5.0 - t))
    if family == 53:
        return abs(math.cos((square + _hash01(int(nx * 6), int(ny * 4), variant) * 0.2) * math.pi * 5.0 + t))
    if family == 54:
        return abs(math.sin((nx * (2 + variant % 6) + ny * (3 + variant % 7)) * math.pi + t))
    if family == 55:
        return abs(math.cos((nx * (3 + variant % 7) - ny * (2 + variant % 6)) * math.pi - t))
    if family == 56:
        return _tri((r + angle / math.tau * 0.4) * (3 + variant % 7) - t * 0.2)
    if family == 57:
        return _tri((diamond - angle / math.tau * 0.3) * (4 + variant % 6) + t * 0.2)
    if family == 58:
        return _hash01(int((nx + math.sin(t) * 0.03) * 18), int((ny + math.cos(t) * 0.03) * 18), variant)
    if family == 59:
        return _hash01(int(nx * (10 + variant % 9)), int(ny * (10 + variant % 7)), int(t * 4) + variant)
    if family < 72:
        arms = 2 + family % 10
        return abs(math.sin(angle * arms + r * (3.0 + mix * 5.0) - phase))
    if family < 84:
        cell = 3 + family % 9
        return _hash01(int((nx + phase * 0.03) * cell), int((ny - phase * 0.02) * (cell + 2)), variant) * 0.7 + r * 0.5
    arms = 3 + family % 8
    return abs(math.cos((angle * arms - diamond * (2.0 + mix * 3.0) + phase) * math.pi))


def _compute_pattern(engine, _mode_id: str, variant: int, family_offset: int = 0) -> Dict[str, float]:
    gv = _grid_info(engine)
    if gv is None:
        return {fid: 0.0 for fid in getattr(engine, "_sorted_ids", [])}
    grid, cols, rows, aspect = gv
    density, width, twist, mix, _speed, t, phase, freq, soft, bass, mid, high, rms, beat = _params(engine, variant)
    weights: Dict[str, float] = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row, nx, ny, x, y, angle, r = _coords(grid, cols, rows, aspect, i, twist, phase)
        metric = _metric_family(variant + family_offset, nx, ny, x, y, angle, r, t, phase, mix, bass, mid, high, variant)
        style = variant % 9
        if style == 0:
            val = _band(metric * freq - phase, width, soft)
        elif style == 1:
            val = _tri(metric * freq - phase) ** (1.0 + high * 2.0)
        elif style == 2:
            val = 1.0 if ((metric * freq - phase) % 1.0) < max(0.02, min(0.85, width * (1.4 + mix))) else 0.0
        elif style == 3:
            cell = _hash01(int(nx * (6 + variant % 11)), int(ny * (5 + variant % 9)), int(phase * 8) + variant)
            val = _clamp01(_band(metric * freq - phase, width, soft) * 0.72 + cell * (0.15 + high * 0.28))
        elif style == 4:
            primary = _band(metric * freq - phase, width, soft)
            secondary = _band(metric * (freq * 0.55 + 0.3) + phase * 0.7, width * 1.55, soft * 1.3)
            val = _clamp01(primary * (0.70 + bass * 0.25) + secondary * (0.18 + mid * 0.35))
        elif style == 5:
            dots = _hash01(int(nx * (8 + variant % 10)), int(ny * (7 + variant % 8)), int(t * (3 + variant % 5)) + variant)
            val = max(_band(metric * freq - phase, width, soft) * 0.65, dots if dots > 0.78 - mix * 0.18 else 0.0)
        elif style == 6:
            val = _clamp01((math.sin((metric * freq - phase) * math.tau) + math.cos((metric * freq * 0.37 + phase) * math.tau) + 2.0) * 0.25)
        elif style == 7:
            stripe = abs(math.sin((col + row + phase * 4.0) * math.pi / max(1, 2 + variant % 7)))
            val = max(_band(metric * freq - phase, width, soft), stripe * (0.25 + mix * 0.45))
        else:
            val = _smooth(0.08, 0.95, _tri(metric * freq - phase)) * (0.7 + rms * 0.3)
        weights[fid] = _clamp01(val)
    return weights


def _particle_sources(variant: int, t: float, count: int, bass: float, mid: float, high: float):
    out = []
    speed = 0.08 + 0.018 * (variant % 11) + bass * 0.10
    for k in range(count):
        seed = variant * 101 + k * 37
        bx = _hash01(seed, 1)
        by = _hash01(seed, 2)
        mode = variant % 16
        if mode == 0:
            x = (bx + t * speed * (0.5 + _hash01(seed, 3))) % 1.0
            y = (by + 0.08 * math.sin(t * 0.7 + k)) % 1.0
        elif mode == 1:
            x = (bx + 0.08 * math.sin(t + k)) % 1.0
            y = (by + t * speed * (0.5 + _hash01(seed, 4))) % 1.0
        elif mode == 2:
            ang = t * speed * 2.0 + k * math.tau / max(1, count)
            rad = 0.12 + 0.36 * _hash01(seed, 5) + mid * 0.08
            x = 0.5 + math.cos(ang) * rad
            y = 0.5 + math.sin(ang * (0.8 + high * 0.3)) * rad
        elif mode == 3:
            x = 0.5 + (bx - 0.5) * _tri(t * speed + k * 0.13)
            y = 0.5 + (by - 0.5) * _tri(t * speed * 0.7 + k * 0.17)
        elif mode == 4:
            x = _tri(t * speed + bx + k * 0.07)
            y = _tri(t * speed * 1.3 + by + k * 0.11)
        elif mode == 5:
            step = int(t * (3.0 + bass * 7.0) + k)
            x = _hash01(seed, step)
            y = _hash01(seed + 1, step)
        elif mode == 6:
            x = (bx + math.sin(t * 0.5 + k) * 0.25) % 1.0
            y = (by + math.cos(t * 0.4 + k * 0.7) * 0.25) % 1.0
        elif mode == 7:
            x = 0.5 + math.cos(t * speed + k) * (0.10 + 0.35 * _tri(t * 0.2 + bx))
            y = 0.5 + math.sin(t * speed + k) * (0.10 + 0.35 * _tri(t * 0.2 + by))
        elif mode == 8:
            x = (bx + t * speed) % 1.0
            y = (by + t * speed * 0.73) % 1.0
        elif mode == 9:
            x = (bx + math.sin(t * speed + by * math.tau) * 0.35) % 1.0
            y = (by + math.sin(t * speed * 1.4 + bx * math.tau) * 0.35) % 1.0
        elif mode == 10:
            x = 0.5 + math.cos(t * speed + bx * math.tau) * (0.2 + high * 0.2)
            y = by
        elif mode == 11:
            x = bx
            y = 0.5 + math.sin(t * speed + by * math.tau) * (0.2 + bass * 0.2)
        elif mode == 12:
            x = _tri(t * speed + bx)
            y = (by + math.sin(x * math.tau + t) * 0.12) % 1.0
        elif mode == 13:
            y = _tri(t * speed + by)
            x = (bx + math.cos(y * math.tau - t) * 0.12) % 1.0
        elif mode == 14:
            ang = k * math.tau / max(1, count) + t * speed
            rad = _tri(t * speed * 0.7 + bx) * 0.48
            x = 0.5 + math.cos(ang) * rad
            y = 0.5 + math.sin(ang) * rad
        else:
            x = (bx + math.sin(t * speed + k) * 0.2 + t * speed * 0.3) % 1.0
            y = (by + math.cos(t * speed * 0.8 + k) * 0.2 + t * speed * 0.2) % 1.0
        out.append((_clamp01(x), _clamp01(y), 0.55 + 0.45 * _hash01(seed, 9)))
    return out


def _compute_particle(engine, _mode_id: str, variant: int) -> Dict[str, float]:
    gv = _grid_info(engine)
    if gv is None:
        return {fid: 0.0 for fid in getattr(engine, "_sorted_ids", [])}
    grid, cols, rows, aspect = gv
    density, width, twist, mix, _speed, t, phase, _freq, soft, bass, mid, high, rms, beat = _params(engine, variant)
    count = max(3, min(72, int(5 + density * (4 + variant % 15) + rms * 16)))
    particles = _particle_sources(variant, t + phase * 0.2, count, bass, mid, high)
    weights: Dict[str, float] = {}
    gravity = 0.12 * math.sin(t * 0.6 + variant)
    sigma = max(0.012, width * (0.16 + (variant % 9) * 0.025 + high * 0.08))
    for i, fid in enumerate(engine._sorted_ids):
        _col, _row, nx, ny, _x, _y, angle, r = _coords(grid, cols, rows, aspect, i, twist, phase)
        val = 0.0
        for pidx, (px, py, bright) in enumerate(particles):
            dx = nx - px
            dy = ny - py - gravity * mix
            if variant % 6 in {1, 4}:
                dx = abs(dx)
            if variant % 7 in {2, 5}:
                dy = abs(dy)
            dist2 = dx * dx + dy * dy
            blob = math.exp(-dist2 / max(1e-6, sigma * sigma)) * bright
            if variant % 5 == 0:
                blob *= 0.65 + 0.35 * _tri(angle / math.tau * (3 + variant % 7) + pidx * 0.13)
            val += blob
        val = val / max(1.0, count * 0.36)
        if beat:
            val = max(val, _band(r * (2.0 + mix * 3.0) - phase, width * 1.4, soft) * 0.55)
        weights[fid] = _clamp01(val * (1.35 + bass * 0.45))
    return weights


def _shape_metric(kind: int, x: float, y: float, angle: float, r: float, t: float, phase: float, mix: float, variant: int) -> float:
    circle = r
    diamond = abs(x) + abs(y)
    square = max(abs(x), abs(y))
    cross = min(abs(x), abs(y)) * 2.0
    petal = abs(math.sin(angle * (3 + variant % 9) + phase)) * (0.4 + r)
    morph = 0.5 + 0.5 * math.sin(t * (0.25 + (variant % 7) * 0.02) + variant * 0.1)
    kind %= 40
    if kind == 0:
        return circle * (1.0 - morph) + diamond * morph
    if kind == 1:
        return circle * (1.0 - morph) + square * morph
    if kind == 2:
        return diamond * (1.0 - morph) + square * morph
    if kind == 3:
        return petal * (1.0 - mix * 0.35) + circle * mix * 0.35
    if kind == 4:
        return cross * (1.0 - morph) + diamond * morph
    if kind == 5:
        return abs(math.sin((circle * (4 + variant % 6) + angle / math.tau) * math.pi + phase))
    if kind == 6:
        return abs(math.cos((diamond * (3 + variant % 7) - angle / math.tau) * math.pi - phase))
    if kind == 7:
        return max(abs(x + math.sin(t) * 0.12), abs(y + math.cos(t) * 0.12))
    if kind == 8:
        return min(abs(x - 0.18 * math.sin(t)), abs(y + 0.18 * math.cos(t))) * (4.0 + mix * 4.0)
    if kind == 9:
        return abs(math.sin(angle * (2 + variant % 6) + r * (5.0 + mix * 4.0) - phase))
    if kind == 10:
        return abs(math.cos(angle * (3 + variant % 7) - r * (4.0 + mix * 4.0) + phase))
    if kind == 11:
        return circle + 0.20 * math.sin(angle * (4 + variant % 8) + phase)
    if kind == 12:
        return diamond + 0.20 * math.cos(angle * (5 + variant % 9) - phase)
    if kind == 13:
        return square + 0.18 * math.sin((x + y) * math.pi * (3 + variant % 6) + phase)
    if kind == 14:
        return abs(x * math.cos(phase) + y * math.sin(phase)) + abs(-x * math.sin(phase) + y * math.cos(phase)) * mix
    if kind == 15:
        return min(math.hypot(x - 0.22, y - 0.22), math.hypot(x + 0.22, y + 0.16), math.hypot(x - 0.06, y + 0.25)) * 2.4
    if kind == 16:
        return max(0.0, 0.55 - circle) + abs(math.sin(angle * (3 + variant % 5) + phase)) * 0.4
    if kind == 17:
        return abs(square - circle) * (2.0 + mix * 4.0)
    if kind == 18:
        return abs(diamond - circle) * (2.0 + mix * 4.0)
    if kind == 19:
        return abs(math.sin((square + diamond * mix) * math.pi * (3 + variant % 7) + phase))
    if kind == 20:
        return abs(math.cos((circle + cross * mix) * math.pi * (4 + variant % 8) - phase))
    if kind == 21:
        return min(abs(x - 0.32 * math.sin(t * 0.4)), abs(y - 0.32 * math.cos(t * 0.35))) * 3.5
    if kind == 22:
        return max(abs(x - 0.25 * math.sin(t)), abs(y - 0.25 * math.cos(t))) * 2.0
    if kind == 23:
        return abs(math.sin((angle * 2.0 + diamond * 3.0 + phase) * math.pi))
    if kind == 24:
        return abs(math.cos((angle * 2.5 - square * 3.0 - phase) * math.pi))
    if kind == 25:
        return (abs(x) ** (1.0 + morph) + abs(y) ** (1.0 + mix)) * 2.0
    if kind == 26:
        return abs(math.sin((x * y * (8.0 + variant % 9) + phase) * math.pi))
    if kind == 27:
        return abs(math.cos(((x + y) * (2.0 + mix) - phase) * math.pi))
    if kind == 28:
        return abs(math.sin(((x - y) * (2.0 + mix) + phase) * math.pi))
    if kind == 29:
        return min(abs(x + y), abs(x - y)) * (2.0 + mix * 4.0)
    return _metric_family(kind + 40, x + 0.5, y + 0.5, x, y, angle, r, t, phase, mix, 0.4, 0.4, 0.4, variant)


def _compute_shape(engine, _mode_id: str, variant: int) -> Dict[str, float]:
    gv = _grid_info(engine)
    if gv is None:
        return {fid: 0.0 for fid in getattr(engine, "_sorted_ids", [])}
    grid, cols, rows, aspect = gv
    density, width, twist, mix, _speed, t, phase, freq, soft, bass, mid, high, rms, beat = _params(engine, variant)
    weights: Dict[str, float] = {}
    for i, fid in enumerate(engine._sorted_ids):
        _col, _row, nx, ny, x, y, angle, r = _coords(grid, cols, rows, aspect, i, twist, phase)
        metric = _shape_metric(variant, x, y, angle, r, t, phase, mix, variant)
        edge = _band(metric * freq - phase, width * (0.9 + mix), soft)
        fill = 1.0 - _smooth(width * (1.0 + mix), width * (4.0 + mix * 3.0), metric)
        pulse = 0.35 + 0.65 * _tri(phase + r * (1.0 + density))
        val = max(edge, fill * pulse * (0.45 + bass * 0.35))
        if beat:
            val = max(val, _band(r * (2.0 + mix * 3.0) - phase, width * 1.5, soft) * 0.75)
        weights[fid] = _clamp01(val)
    return weights


def _audio_levels(engine, count: int, variant: int) -> List[float]:
    return versioned_v40_audio_levels(engine, count, variant)


def _compute_audio_bar(engine, _mode_id: str, variant: int) -> Dict[str, float]:
    gv = _grid_info(engine)
    if gv is None:
        return {fid: 0.0 for fid in getattr(engine, "_sorted_ids", [])}
    grid, cols, rows, aspect = gv
    density, width, twist, mix, _speed, t, phase, freq, soft, bass, mid, high, rms, beat = _params(engine, variant)
    orientation = variant % 8
    axis_count = rows if orientation in {1, 3, 5} else cols
    levels = _audio_levels(engine, axis_count, variant)
    peak_decay = 0.010 + (variant % 7) * 0.004
    state = getattr(engine, "_effect_state", None)
    peaks = None
    if isinstance(state, dict):
        key = f"v40_audio_peak_{variant}"
        peaks = state.get(key)
        if not isinstance(peaks, list) or len(peaks) != axis_count:
            peaks = [0.0] * axis_count
        peaks = [max(levels[i], max(0.0, peaks[i] - peak_decay)) for i in range(axis_count)]
        state[key] = peaks
    else:
        peaks = list(levels)
    weights: Dict[str, float] = {}
    for i, fid in enumerate(engine._sorted_ids):
        col, row, nx, ny, x, y, angle, r = _coords(grid, cols, rows, aspect, i, twist, phase)
        if orientation == 0:
            idx, coord = min(axis_count - 1, col), 1.0 - ny
        elif orientation == 1:
            idx, coord = min(axis_count - 1, row), nx
        elif orientation == 2:
            idx, coord = min(axis_count - 1, col), abs(ny - 0.5) * 2.0
        elif orientation == 3:
            idx, coord = min(axis_count - 1, row), abs(nx - 0.5) * 2.0
        elif orientation == 4:
            idx, coord = int((nx + ny) * 0.5 * max(1, axis_count - 1)), 1.0 - abs(nx - ny)
        elif orientation == 5:
            idx, coord = int(abs(nx - ny) * max(1, axis_count - 1)), 1.0 - (nx + ny) * 0.5
        elif orientation == 6:
            idx, coord = int((angle / math.tau % 1.0) * max(1, axis_count - 1)), 1.0 - min(1.0, r * 1.7)
        else:
            idx, coord = int(r * max(1, axis_count - 1)), 1.0 - (angle / math.tau % 1.0)
        idx = max(0, min(axis_count - 1, idx))
        level = levels[idx]
        peak = peaks[idx] if peaks else level
        fill = 1.0 if coord <= level else _smooth(level + width * 1.5, level, coord)
        cap = _band(coord - peak, max(width * 0.8, 0.025), soft)
        texture = _metric_family(variant + 12, nx, ny, x, y, angle, r, t, phase, mix, bass, mid, high, variant)
        tex_val = _band(texture * freq - phase, width * 1.2, soft) * (0.12 + mix * 0.35)
        val = max(fill * (0.65 + level * 0.35), cap * (0.55 + high * 0.35), tex_val)
        if variant % 5 == 0 and beat:
            val = max(val, _hash01(col, row, int(t * 12) + variant))
        weights[fid] = _clamp01(val)
    return weights


STYLE_NAMES = [
    "\u67d4\u8fb9", "\u7a84\u5cf0", "\u5bbd\u5e45", "\u5bc6\u96c6", "\u6162\u901f",
    "\u786c\u5207", "\u5c42\u53e0", "\u9519\u4f4d", "\u56de\u58f0", "\u9897\u7c92",
    "\u6df1\u5e45", "\u53cc\u5cf0", "\u9636\u68af", "\u95e8\u63a7", "\u626b\u63cf",
    "\u788e\u95ea", "\u8fb9\u7f18", "\u4e2d\u5fc3", "\u65cb\u8f6c", "\u5f39\u6027",
]

MODE_NAME_POOLS = {
    "asym_motion": [
        "\u504f\u8f74\u526a\u5207\u6d41", "\u659c\u5411\u63a8\u8fdb", "\u5355\u4fa7\u62c9\u4f38", "\u98ce\u5411\u68b3\u7406",
        "\u4fa7\u5411\u6ed1\u79fb", "\u9636\u68af\u6ed1\u52a8", "\u53cc\u6e90\u504f\u79fb", "\u5f27\u5f62\u62d6\u66f3",
        "\u884c\u5217\u86c7\u884c", "\u5f84\u5411\u6ed1\u79fb", "\u89d2\u5411\u56de\u5377", "\u5e42\u5761\u63a8\u5149",
    ],
    "particle_physics": [
        "\u7c92\u5b50\u6f02\u5c18", "\u7c92\u5b50\u7ad6\u96e8", "\u8f68\u9053\u5fae\u5c18", "\u547c\u5438\u7c92\u7fa4",
        "\u5f39\u8df3\u7c92\u573a", "\u968f\u673a\u7206\u70b9", "\u6b63\u5f26\u6f02\u6d6e", "\u73af\u5f62\u7c92\u7fa4",
        "\u659c\u5411\u7c92\u6d41", "\u91cd\u529b\u6ef4\u843d", "\u98ce\u5411\u706b\u661f", "\u78c1\u5438\u7c92\u5b50",
        "\u4e2d\u5fc3\u7206\u6563", "\u53cc\u5f57\u661f\u5c3e", "\u6e4d\u6d41\u7c92\u4e91",
    ],
    "shape_morph": [
        "\u5706\u8f6c\u83f1\u5f62", "\u5706\u8f6c\u65b9\u5f62", "\u83f1\u8f6c\u65b9\u5f62", "\u82b1\u74e3\u6253\u5f00",
        "\u82b1\u74e3\u6298\u53e0", "\u65cb\u8f6c\u65b9\u6846", "\u5341\u5b57\u7f1d\u5f00", "\u8fb9\u6846\u538b\u529b",
        "\u83f1\u5f62\u626d\u8f6c", "\u56db\u89d2\u62c9\u4f38", "\u79fb\u52a8\u51c6\u661f", "\u87ba\u65cb\u5916\u58f3",
    ],
    "space_dot": [
        "\u7a7a\u95f4\u9488\u96e8", "\u77ed\u5212\u70b9\u9635", "\u9636\u68af\u523b\u5ea6", "\u9519\u4f4d\u661f\u70b9",
        "\u659c\u73e0\u4e32\u884c", "\u6469\u65af\u70b9\u9635", "\u8f74\u5411\u523b\u70b9", "\u73af\u5f62\u523b\u70b9",
        "\u83f1\u5f62\u523b\u70b9", "\u50cf\u7d20\u706b\u661f", "\u8282\u62cd\u70b9\u77f3", "\u547c\u5438\u6807\u8bb0",
    ],
    "pixel_texture": [
        "\u50cf\u7d20\u659c\u5207\u6ce2", "\u7ad6\u5411\u62d6\u5f71", "\u659c\u5411\u96e8\u7eb9", "\u87ba\u65cb\u5761\u9762",
        "\u89d2\u5ea6\u626b\u63cf", "\u83f1\u5f62\u6d41\u52a8", "\u65b9\u6f6e\u8d77\u4f0f", "\u5217\u5411\u7ec7\u7eb9",
        "\u884c\u5411\u7ec7\u7eb9", "\u65cb\u5207\u7247", "\u7f16\u7ec7\u573a", "\u566a\u58f0\u6df1\u5ea6",
    ],
    "audio_bar": [
        "\u5b9e\u5fc3\u97f3\u9891\u6761", "\u4e2d\u5fc3\u7f0e\u5e26", "\u626b\u63cf\u9891\u6bb5\u6761", "\u70b9\u9635\u9636\u68af\u6761",
        "\u659c\u5411\u9891\u8c31\u7f51", "\u73af\u5f62\u5cf0\u503c", "\u5cf0\u503c\u4fdd\u6301\u7ebf", "\u626b\u5149\u5200\u6761",
        "\u4ea4\u66ff\u955c\u50cf\u6761", "\u6ce2\u7eb9\u586b\u5145\u6761", "\u6db2\u4f4d\u7f0e\u5e26", "\u8f90\u5c04\u97f3\u91cf\u8868",
    ],
    "scan_gate": [
        "\u5355\u5200\u626b\u63cf", "\u53cc\u5411\u626b\u63cf", "\u4e2d\u5fc3\u5f00\u95e8", "\u8fb9\u7f18\u6536\u95e8",
        "\u659c\u6760\u95e8\u63a7", "\u53cd\u659c\u95e8\u63a7", "\u68cb\u76d8\u95ea\u95e8", "\u9636\u68af\u626b\u5149",
        "\u5361\u987f\u95e8\u63a7", "\u591a\u6bb5\u95ea\u70c1", "\u4e09\u89d2\u95e8", "\u65b9\u6846\u95e8",
    ],
    "symmetric_wave": [
        "\u83f1\u5f62\u73af\u6ce2", "\u540c\u5fc3\u5706\u6ce2", "\u5341\u5b57\u955c\u50cf", "\u4e07\u82b1\u8f74\u6ce2",
        "\u82b1\u74e3\u6d8c\u52a8", "\u65b9\u6846\u547c\u5438", "\u8f74\u7ebf\u5f00\u5408", "\u56db\u6e90\u5e72\u6d89",
        "\u6c99\u6f0f\u5bf9\u79f0", "\u65cb\u8f6c\u7f51\u683c",
    ],
    "sample_post": [
        "\u91c7\u6837\u67d4\u5316", "\u91c7\u6837\u8fb9\u7f18\u589e\u5f3a", "\u91c7\u6837\u50cf\u7d20\u5316", "\u91c7\u6837\u626b\u5149",
        "\u91c7\u6837\u9897\u7c92\u5316", "\u91c7\u6837\u5cf0\u503c\u95e8", "\u91c7\u6837\u6ce2\u7eb9\u53e0\u52a0", "\u91c7\u6837\u65cb\u8f6c\u626d\u66f2",
    ],
}


def _mode_label(prefix: str, idx: int) -> str:
    pool = MODE_NAME_POOLS.get(prefix, ["\u5b9e\u7528\u52a8\u4f5c"])
    base = pool[idx % len(pool)]
    style = STYLE_NAMES[(idx // len(pool)) % len(STYLE_NAMES)]
    return f"{base}\u00b7{style} {idx + 1:03d}"


def _make_modes(prefix: str, label: str, count: int) -> List[Tuple[str, str]]:
    return [(f"matrix_v40_{prefix}_{idx:04d}", _mode_label(prefix, idx)) for idx in range(count)]


MODE_GROUPS: List[Tuple[str, str, int, str, int]] = [
    ("\u5168\u7c7b\u578b\u975e\u5bf9\u79f0\u8fd0\u52a8-v40", "asym_motion", 160, "\u975e\u5bf9\u79f0\u8fd0\u52a8", 0),
    ("\u5168\u7c7b\u578b\u7c92\u5b50\u7269\u7406-v40", "particle_physics", 180, "\u7c92\u5b50\u7269\u7406", 0),
    ("\u5168\u7c7b\u578b\u5f62\u72b6\u53d8\u5f62-v40", "shape_morph", 150, "\u5f62\u72b6\u53d8\u5f62", 0),
    ("\u5168\u7c7b\u578b\u7a7a\u95f4\u70b9\u9635-v40", "space_dot", 170, "\u7a7a\u95f4\u70b9\u9635", 24),
    ("\u5168\u7c7b\u578b\u50cf\u7d20\u7eb9\u7406-v40", "pixel_texture", 180, "\u50cf\u7d20\u7eb9\u7406", 48),
    ("\u5168\u7c7b\u578b\u97f3\u9891\u6761-v40", "audio_bar", 140, "\u97f3\u9891\u6761", 0),
    ("\u5168\u7c7b\u578b\u626b\u63cf\u95e8\u63a7-v40", "scan_gate", 120, "\u626b\u63cf\u95e8\u63a7", 60),
    ("\u5168\u7c7b\u578b\u5bf9\u79f0\u6ce2\u52a8-v40", "symmetric_wave", 90, "\u5bf9\u79f0\u6ce2\u52a8", 72),
    ("\u5168\u7c7b\u578b\u91c7\u6837\u540e\u5904\u7406-v40", "sample_post", 64, "\u91c7\u6837\u540e\u5904\u7406", 84),
]

V40_MODE_GROUPS: List[Tuple[str, str, List[Tuple[str, str]], str, int]] = [
    (group_label, prefix, _make_modes(prefix, label, count), label, offset)
    for group_label, prefix, count, label, offset in MODE_GROUPS
]


def register_v40_matrix_ext(pixel_modes_ext: Dict[str, List[Tuple[str, str]]],
                            compute_map: Dict[str, Callable]) -> None:
    from .matrix_pixel_audio_modes import compute_versioned_audio_bar
    total = 0
    for group_label, prefix, modes, _label, offset in V40_MODE_GROUPS:
        pixel_modes_ext.setdefault(group_label, []).extend(modes)
        total += len(modes)
        if prefix == "particle_physics":
            compute_map.update({
                mode_id: (lambda engine, m=mode_id, v=idx: _compute_particle(engine, m, v))
                for idx, (mode_id, _name) in enumerate(modes)
            })
        elif prefix == "shape_morph":
            compute_map.update({
                mode_id: (lambda engine, m=mode_id, v=idx: _compute_shape(engine, m, v))
                for idx, (mode_id, _name) in enumerate(modes)
            })
        elif prefix == "audio_bar":
            compute_map.update({
                mode_id: (lambda engine, m=mode_id, v=idx: compute_versioned_audio_bar(engine, m, v))
                for idx, (mode_id, _name) in enumerate(modes)
            })
        else:
            compute_map.update({
                mode_id: (lambda engine, m=mode_id, v=idx + offset: _compute_pattern(engine, m, v, offset))
                for idx, (mode_id, _name) in enumerate(modes)
            })
    if total != V40_MATRIX_MODE_COUNT:
        raise RuntimeError(f"v40 matrix mode count mismatch: {total}")
