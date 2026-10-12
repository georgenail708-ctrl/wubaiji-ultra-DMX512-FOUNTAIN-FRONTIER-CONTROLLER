"""R31 true-audio controls follow the capability of each registered kernel.

Reference: efx.cpp 1ccdab841c118b20058c89c04bee95df94e440f3:
algorithm-specific frequency/phase controls. Uses existing ParamSpec objects;
saved parameters and render kernels are never rewritten or filtered here.
"""
from __future__ import annotations

from dataclasses import replace


# Every kernel consumes these through _render_frame. Engine appends its own
# shared controls afterwards, including actual phase_spread and effect_width.
_BASE_KEYS = frozenset({"ch_type", "spread", "lo", "hi"})

# This table describes the existing kernels, not a second output implementation.
# rate only changes kernels consuming t; other features follow real audio.
_VARIANT_KEYS = {
    "rms_gate": ("threshold",),
    "rms_breath": ("rate",),
    "rms_gradient": (),
    "peak_flash": ("rate",),
    "band_low": (),
    "band_mid": (),
    "band_high": (),
    "beat_pulse": ("decay", "rate"),
    "beat_strobe": ("duty", "rate"),
    "beat_offbeat": ("rate",),
    "onset_accent": ("sensitivity",),
    "energy_meter": (),
    "energy_floor": ("floor",),
    "silence_blackout": (),
    "band_split_zone": (),
    "band_split_interleave": (),
    "band_8_scroll": (),
    "band_8_bars": (),
    "band_centroid_sweep": (),
    "beat_chase_sequential": ("rate",),
    "beat_pingpong": ("rate",),
    "beat_euclid_ring": ("pulses", "steps", "rate"),
    "bpm_sync_wave": ("rate",),
    "bpm_sync_scale": ("rate",),
    "onset_ripple": ("rate",),
    "transient_spark": ("sensitivity",),
    "energy_contour_shape": (),
    "loudness_zone": (),
    "crest_factor": (),
    "spectral_tilt": (),
    "pitch_height": (),
    "pitch_octave_fold": (),
    "pitch_glide_trail": (),
    "pitch_confidence_gate": ("threshold",),
    "chord_root_zone": (),
    "chord_quality_tint": (),
    "chord_change_accent": (),
    "harmonic_tension": (),
    "spectral_centroid_band": (),
    "spectral_flux_sweep": ("rate",),
    "multiband_mosaic": (),
    "dynamic_range_map": (),
    "energy_histogram": (),
    "low_mid_high_cascade": (),
    "melody_contour_track": (),
    "harmony_stack": (),
    "transient_density_heat": (),
    "beat_phase_spiral": ("rate",),
    "energy_slope_detector": (),
    "pitch_stability_hold": (),
    "full_spectrum_waterfall": ("rate",),
    "audio_signature_pulse": ("rate",),
}

_TEXT = {
    "sensitivity": ("起音阈值", "越高需要越强的起音才触发；降低可捕捉较弱的瞬态。"),
    "spread": ("尾部衰减", "沿设备序列逐渐降低亮度；0 保持亮度，2 使末台衰减到零。"),
    "rate": ("节奏倍率", "调整内部运动或节奏的时间倍率；音频来源和分析不变。"),
}


def audio_params_for(variant: str, specs: list) -> list:
    """Limit newly presented controls; legacy object values remain untouched."""
    keys = _VARIANT_KEYS.get(variant)
    if keys is None:
        raise ValueError(f"true-audio variant lacks a control schema: {variant}")
    allowed = _BASE_KEYS.union(keys)
    return [_with_text(spec) for spec in specs if spec.key in allowed]


def _with_text(spec):
    text = _TEXT.get(spec.key)
    return replace(spec, label=text[0], tooltip=text[1]) if text else spec
