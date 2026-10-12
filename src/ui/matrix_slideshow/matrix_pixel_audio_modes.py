"""Pixel audio capabilities and versioned parameter adaptation.

Reuse MatrixPixelEngine's band mapping, bar axes and fill APIs. Audio analysis
and transport remain owned by their existing services. v0.15.1 informed
silence semantics; no upstream implementation is copied.
"""
from __future__ import annotations

import math

import numpy as np

from .matrix_pixel_music_catalog import MUSIC_DEFAULTS, initialize_music_parameters


BAR_MODES = frozenset({
    "audio_fft", "audio_waveform", "audio_band_matrix", "audio_band_reverse",
    "audio_band_center", "audio_band_dots", "audio_vu_meter", "audio_eq_bars",
    "audio_eq_soft", "audio_eq_dots", "audio_eq_center", "audio_eq_scan",
})
_AUDIO_RUNTIME_KEYS = (
    "audio_controls_version", "audio_amplitude", "audio_norm_offset", "audio_device",
    "audio_bar_direction", "audio_bar_style", "audio_band_layout",
    "audio_bar_gamma", "audio_bar_softness", "audio_bar_smoothing",
    "audio_bar_motion", "audio_peak_hold", "audio_peak_decay",
    "matrix_audio_mod_target", "matrix_audio_mod_source", "matrix_audio_mod_amount",
    "matrix_audio_mod_floor", "matrix_audio_mod_attack_ms", "matrix_audio_mod_release_ms",
) + tuple(MUSIC_DEFAULTS)


def initialize_audio_controls(engine):
    engine.audio_controls_version = 1
    initialize_music_parameters(engine)


def audio_mode_family(mode):
    """Match the engine's source-audio contract while identifying real bar inputs."""
    if mode in BAR_MODES or mode.startswith(("matrix_v38_audio_bar_", "matrix_v40_audio_bar_")):
        return "bar"
    if mode.startswith(("audio_", "matrix_audio")) or "_audio" in mode:
        return "source"
    if any(word in mode for word in ("spectral", "bass", "vocal", "echo", "metered")):
        return "source"
    return "pattern"


def show_audio_mode_params(panel, mode):
    from .matrix_pixel_music_controls import configure_music_controls
    configure_music_controls(panel, mode)
    family = audio_mode_family(str(mode or ""))
    if family == "pattern":
        panel._stack.setCurrentWidget(panel._effect_page)
        return
    panel._stack.setCurrentWidget(panel._audio_page)
    for name in ("_audio_style_section", "_audio_peak_section"):
        section = getattr(panel, name, None)
        if section is not None:
            section.setVisible(family == "bar")
    configure_bar_controls(panel)


def configure_bar_controls(panel):
    """Texture motion only applies to the dot/scan styles in the existing API."""
    engine = getattr(panel, "_engine", None)
    if engine is None:
        return
    v40 = str(getattr(engine, "mode", "")).startswith("matrix_v40_audio_bar_")
    enabled = not v40 or engine._audio_bar_style() in {"dots", "scan"}
    for suffix in ("_caption", "_slider", "_spin"):
        control = getattr(panel, "_bar_motion" + suffix, None)
        if control is not None:
            control.setEnabled(enabled)
            control.setToolTip("纹理运动用于点阵和扫描纹理样式；其他样式不使用此参数。")


def restore_audio_controls(engine, params):
    """Missing version is legacy, including dispatch runtime-only recall."""
    for key in _AUDIO_RUNTIME_KEYS:
        if key in params:
            setattr(engine, key, params[key])
    try:
        engine.audio_controls_version = 1 if int(params.get("audio_controls_version", 0)) >= 1 else 0
    except (TypeError, ValueError):
        engine.audio_controls_version = 0


def _frame_dt(engine):
    return max(0.001, min(0.2, float(engine._effect_state.get("_audio_mod_dt", 0.04))))


def _real_audio_input_present(engine):
    frame = getattr(engine, "_last_audio_frame", None)
    if getattr(engine, "_analyzer", None) is not None and frame is not None:
        return bool(getattr(frame, "has_audio", True))
    return bool(getattr(getattr(engine, "_audio", None), "_running", False))


def should_simulate_audio_env(engine, bands):
    """Preserve legacy fallback; an actual quiet source is real in version 1."""
    if not bool(getattr(engine, "pixel_audio_reactive", True)):
        return True
    if int(getattr(engine, "audio_controls_version", 0)) >= 1:
        return not _real_audio_input_present(engine)
    return not bands or max([abs(float(value)) for value in bands] + [0.0]) < 0.002


def permit_synthetic_beat(engine):
    return int(getattr(engine, "audio_controls_version", 0)) < 1 or should_simulate_audio_env(engine, [])


def simulated_symmetric_audio(engine, variant):
    """The original v35 fallback, extracted unchanged to keep its file bounded."""
    from .matrix_pixel_engine_ext import _triwave_v35
    time = float(getattr(engine, "_t", 0.0))
    base = 0.5 + 0.5 * math.sin(time * (0.72 + 0.03 * (variant % 7)))
    pulse = _triwave_v35(time * (0.28 + 0.017 * (variant % 5)))
    bands = [
        0.30 + 0.70 * base,
        0.25 + 0.65 * _triwave_v35(time * 0.21 + 0.11 * variant),
        0.20 + 0.55 * math.sin(time * 0.41 + variant) ** 2,
        0.18 + 0.50 * pulse,
        0.16 + 0.44 * _triwave_v35(time * 0.37 + 0.07 * variant),
        0.12 + 0.36 * math.sin(time * 0.83 + 0.37 * variant) ** 2,
        0.10 + 0.32 * _triwave_v35(time * 0.53 + 0.13 * variant),
        0.10 + 0.30 * math.sin(time * 0.97 + 0.19 * variant) ** 2,
    ]
    return bands, 0.22 + 0.58 * base


def versioned_audio_gain(engine, legacy_gain):
    """The old local capture bands have already received amplitude once."""
    if int(getattr(engine, "audio_controls_version", 0)) < 1:
        return legacy_gain
    unscaled = not bool(getattr(engine, "pixel_audio_reactive", True))
    unscaled |= getattr(engine, "_analyzer", None) is not None and getattr(engine, "_last_audio_frame", None) is not None
    return max(0.01, float(engine.audio_amplitude)) if unscaled else 1.0


def versioned_audio_rms(engine, rms, legacy_gain):
    gain = legacy_gain
    if int(getattr(engine, "audio_controls_version", 0)) >= 1:
        gain = max(0.01, float(engine.audio_amplitude))
    return min(1.0, rms * gain)


def _mapped_levels(engine, count, state_key="v40_controls"):
    """Use existing band layout/Gamma, with smoothing measured at the nominal 25 Hz."""
    bands = np.asarray(engine._get_audio_bands(), dtype=np.float32)
    amplitude = versioned_audio_gain(engine, max(0.01, float(engine.audio_amplitude)))
    values = np.clip(bands * amplitude, 0.0, 1.0)
    if values.size:
        # The low-frequency compensation control acts on the actual bass bands.
        values[:min(2, values.size)] *= math.pow(2.0, float(engine.audio_norm_offset))
    smooth = float(engine.audio_bar_smoothing)
    engine.audio_bar_smoothing = max(0.0, min(0.95, smooth)) ** (_frame_dt(engine) / 0.04)
    try:
        return engine._audio_bar_levels(values, count, state_key=state_key)
    finally:
        engine.audio_bar_smoothing = smooth


def _peak_levels(engine, levels):
    """Adapt existing peak hold model to real frame dt without changing old IDs."""
    state = engine._effect_state
    peaks = np.asarray(state.get("v40_control_peaks", np.zeros_like(levels)), dtype=np.float32)
    ages = np.asarray(state.get("v40_control_peak_ages", np.zeros_like(levels)), dtype=np.float32)
    if peaks.shape != levels.shape:
        peaks, ages = np.zeros_like(levels), np.zeros_like(levels)
    dt = _frame_dt(engine)
    rising = levels >= peaks
    peaks = np.maximum(peaks, levels)
    ages = np.where(rising, 0.0, ages + dt)
    falling = ages > max(0.0, float(engine.audio_peak_hold))
    decay = max(0.0, float(engine.audio_peak_decay)) * dt / 0.04
    peaks[falling] = np.maximum(levels[falling], peaks[falling] - decay)
    state["v40_control_peaks"], state["v40_control_peak_ages"] = peaks.copy(), ages
    return peaks


def _special_axes(engine, variant, base):
    """Keep diagonal and polar output capabilities beside ordinary editable bars."""
    count, index, coord, tolerance = base
    geometry = int(variant) % 8
    if geometry < 4:
        return base
    x, y = engine._grid.norm_x_np, engine._grid.norm_y_np
    direction = engine._audio_bar_direction()
    if direction in {"left", "right"}:
        x, y = y, x
    if direction in {"top", "right"}:
        y = 1.0 - y
    if geometry == 4:
        index, coord = (x + y) * 0.5, 1.0 - np.abs(x - y)
    elif geometry == 5:
        index, coord = np.abs(x - y), 1.0 - (x + y) * 0.5
    elif geometry == 6:
        angle = np.arctan2(y - 0.5, x - 0.5) / math.tau
        index, coord = angle % 1.0, 1.0 - np.minimum(1.0, np.hypot(x - 0.5, y - 0.5) * 1.7)
    else:
        index = np.hypot(x - 0.5, y - 0.5)
        coord = 1.0 - (np.arctan2(y - 0.5, x - 0.5) / math.tau % 1.0)
    return count, np.minimum(count - 1, np.maximum(0, (index * (count - 1)).astype(int))), coord, tolerance


def compute_controlled_audio_bar(engine, variant):
    """Version 1 uses the visible controls; version 0 stays in its old function."""
    grid = engine._grid
    if grid is None:
        return {fid: 0.0 for fid in engine._sorted_ids}
    cols, rows = max(1, grid.cols), max(1, grid.rows)
    style = engine._audio_bar_style()
    axes = engine._audio_bar_axes_array(cols, rows, grid.col_np, grid.row_np, style=style)
    count, indices, coord, tolerance = _special_axes(engine, variant, axes)
    levels = _mapped_levels(engine, count)
    peaks = _peak_levels(engine, levels)
    energy = levels[indices]
    body = engine._audio_bar_fill_from_energy(energy, coord, indices, tolerance, style=style)
    body *= energy > 1e-6
    soft = max(0.001, float(engine.audio_bar_softness))
    cap = np.maximum(0.0, 1.0 - np.abs(coord - peaks[indices]) / max(tolerance, soft))
    # A zero source has no bar or cap; held peaks can finish their explicit decay.
    cap *= peaks[indices] > 1e-6
    values = np.clip(np.maximum(body, cap), 0.0, 1.0)
    return {fid: float(values[i]) for i, fid in enumerate(engine._sorted_ids)}


def compute_versioned_audio_bar(engine, mode, variant):
    if int(getattr(engine, "audio_controls_version", 0)) >= 1:
        return compute_controlled_audio_bar(engine, variant)
    from .matrix_pixel_engine_ext_v40 import _compute_audio_bar
    return _compute_audio_bar(engine, mode, variant)


def versioned_v40_audio_levels(engine, count, variant):
    if int(getattr(engine, "audio_controls_version", 0)) >= 1:
        return _mapped_levels(engine, count).tolist()
    from .matrix_pixel_engine_ext_v40 import _clamp01
    try:
        bands = [float(value) for value in engine._get_audio_bands()]
    except Exception:
        bands = []
    if not bands or max([abs(value) for value in bands] + [0.0]) < 0.002:
        time = float(getattr(engine, "_t", 0.0))
        return [_clamp01(0.18 + 0.72 * math.sin(time * (0.45 + i * 0.027) + variant * 0.11 + i * 0.31) ** 2)
                for i in range(max(1, count))]
    values = _legacy_resample(bands, count)
    amplitude = max(0.2, float(getattr(engine, "audio_amplitude", 2.0) or 2.0))
    return [_clamp01(value * amplitude) for value in values]


def _legacy_resample(bands, count):
    if len(bands) == count:
        return bands
    values = []
    for index in range(max(1, count)):
        source = index * (len(bands) - 1) / max(1, count - 1)
        lower = int(math.floor(source))
        upper = min(len(bands) - 1, lower + 1)
        fraction = source - lower
        values.append(bands[lower] * (1.0 - fraction) + bands[upper] * fraction)
    return values


def v38_audio_levels(engine, count):
    if int(getattr(engine, "audio_controls_version", 0)) >= 1:
        return _mapped_levels(engine, count).tolist()
    from .matrix_pixel_engine_ext_v38 import _audio_bands, _clamp01, _resample_levels
    levels = _resample_levels(_audio_bands(engine), count)
    gamma = max(0.05, float(getattr(engine, "audio_bar_gamma", 0.85) or 0.85))
    return [_clamp01(value ** gamma) for value in levels]


def finish_v38_audio_bar(engine, weights, levels):
    """Keep the original 30 textures, adding only their missing control layer."""
    if int(getattr(engine, "audio_controls_version", 0)) < 1:
        return weights
    grid = engine._grid
    style = engine._audio_bar_style()
    axes = engine._audio_bar_axes_array(grid.cols, grid.rows, grid.col_np, grid.row_np, style=style)
    _count, indices, coord, tolerance = axes
    levels = np.asarray(levels, dtype=np.float32)
    energy = levels[indices]
    body = np.array([weights[fid] for fid in engine._sorted_ids], dtype=np.float32)
    if style != "solid":
        body *= engine._audio_bar_fill_from_energy(energy, coord, indices, tolerance, style=style)
    body *= energy > 1e-6
    peaks = _peak_levels(engine, levels)[indices]
    softness = max(0.001, float(engine.audio_bar_softness))
    cap = np.maximum(0.0, 1.0 - np.abs(coord - peaks) / max(tolerance, softness))
    cap *= peaks > 1e-6
    values = np.clip(np.maximum(body, cap), 0.0, 1.0)
    return {fid: float(values[index]) for index, fid in enumerate(engine._sorted_ids)}
