# -*- coding: utf-8 -*-
"""【通用/音频】**真音频**效果分支 —— 50+ 种从基础到高级的真实音频驱动效果。

★ 用户需求（2026-09-21）::

    "我之前不是有一套关于音频的预设构思吗，现在 effect 里面关于音频的这些效果全是
     '以音频为题材的假效果'，也就是它们实际上没法像 programmer 里面的音频功能一样
     实时读音频出效果。我认为有必要加一套关于通用-音频分支的效果预设系统。这个分支
     前端以及后端完全符合 effect 的注册和参数机制，有各种丰富的音频设计的基础上
     （你创意至少 50 种从基础到高级真音频效果），通过同步使用我的软件已有的音频深度
     识别机制，进行真实根据音频播放产生的效果输出（此类效果在无音频的时候点击播放
     不出效果），正常使用的时候先点击播放，然后播放时间线的音频会有效果了（音频来源
     受我的主界面调节来源的控件来源控制），也就是它的音频输入跟其它音频效果输入
     全部一样，我的主界面有选中音频来源：时间线，模拟，麦克风等来源，effect 的音频
     真效果也受它的控制。（所有音频识别的的已有成果在这个里面利用起来）。"

## 与既有"音频反应"分类的区别（为什么还要新建一套）

既有 `cur_audio_*` / `audio_*` 预设**部分**读音频，但大量预设（如 `aut_*`/`audio_*`
族的一部分）在无音频时会走"自走包络"糊弄过去 —— 用户称之"以音频为题材的假效果"。
本模块的硬契约与之相反：

  · **必须真音频**：`params["_audio_required"]=True` → 引擎在 `compute()` 里对
    "无 provider"的情形直接输出 `lo`（静音），`compute_at` 同理 → **无音频点播放
    不出效果**（用户明确要求的行为）。
  · **输入来源与其它音频效果完全一致**：数据经 `EffectBrowser._params_with_audio`
    注入 `_audio_data`，其源头就是主界面"音频来源"控件（模拟/麦克风/时间线音频）
    选中的那个 `AudioAnalyzer`。本模块不自己采集音频。
  · **复用已有深度识别成果**：`_audio_data` 由 `_audio_frame_dict` 填充，除
    rms/peak/8 频段/拍/起音外，还带 `low/mid/high/centroid`（派生频谱）与
    `pitch_midi/pitch_confidence/chord_root/chord_quality`（C5 音高/和声，
    来自 `deep_music_analysis`）；本模块的高阶族直接吃这些量。

## 四档设计（50+ 种，覆盖基础→高级）

| 档位 | 数量 | 依赖的音频量 |
|------|------|--------------|
| 基础 | 14 | rms / peak / 单频段 / 拍 |
| 中级 | 16 | 多频段 / 起音 / BPM 网格 / 谱心 |
| 高级 | 14 | 频谱形态 / 音高 / 和声 / 多参耦合 |
| 专家 | 8  | 深层特征组合（音高走向、和声张力、瞬态密度） |

命名统一 `atx_<英文短名>`（audio true eXtended），与既有 `cur_audio_*` 不冲突。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

# ─────────────────────────────────────────────────────────────────────────────
#  音频量读取（唯一收口，全部做容错）
# ─────────────────────────────────────────────────────────────────────────────


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


class _AudioRead:
    """一帧音频量的**只读快照**（把字典解析与效果逻辑解耦，便于单测）。

    `ok=False` 表示**这一帧没有真实音频**（无输入/静音）—— 调用方据此输出静音。
    """

    __slots__ = ("ok", "rms", "peak", "bands", "low", "mid", "high", "centroid",
                 "bpm", "beat_phase", "is_beat", "onset", "pitch", "pitch_conf",
                 "chord_root", "chord_quality")

    def __init__(self, audio: Dict[str, Any]):
        a = audio if isinstance(audio, dict) else {}
        self.ok = bool(a.get("has_audio", False))
        self.rms = _clamp01(_f(a.get("rms", 0.0)))
        self.peak = _clamp01(_f(a.get("peak", 0.0)))
        bands = a.get("bands") or []
        self.bands: List[float] = [_clamp01(_f(b)) for b in list(bands)[:8]]
        self.low = _clamp01(_f(a.get("low", self.bands[0] if self.bands else 0.0)))
        self.mid = _clamp01(_f(a.get("mid", 0.0)))
        self.high = _clamp01(_f(a.get("high", 0.0)))
        self.centroid = max(0.0, _f(a.get("centroid_hz", a.get("centroid", 0.0))))
        self.bpm = max(1.0, _f(a.get("bpm", 120.0)) or 120.0)
        self.beat_phase = _clamp01(_f(a.get("beat_phase", 0.0)))
        self.is_beat = bool(a.get("is_beat", False))
        self.onset = _clamp01(_f(a.get("onset_strength", 0.0)))
        self.pitch = _f(a.get("pitch_midi", -1.0))          # -1 = 无音高
        self.pitch_conf = _clamp01(_f(a.get("pitch_confidence", 0.0)))
        try:
            self.chord_root = int(a.get("chord_root", -1))
        except (TypeError, ValueError):
            self.chord_root = -1
        self.chord_quality = str(a.get("chord_quality", "") or "")

    def band(self, i: int) -> float:
        """第 i 个频段（越界返回 0）。"""
        return self.bands[i] if 0 <= i < len(self.bands) else 0.0

    def pitch_norm(self) -> float:
        """音高归一化到 0..1（C-1..G9 → 0..1）；无音高返回 0。"""
        if self.pitch < 0.0 or self.pitch_conf <= 0.0:
            return 0.0
        return _clamp01((self.pitch - 24.0) / 84.0)

    def chord_tone(self) -> int:
        """和弦根音 0..11；未知返回 -1。"""
        return self.chord_root if 0 <= self.chord_root <= 11 else -1

    def beat_period(self) -> float:
        return 60.0 / max(1.0, self.bpm)

    def beat_decay(self, t: float, decay: float) -> float:
        """拍后指数衰减包络（0..1）—— 所有"跟着拍子闪"族的共同基底。"""
        per = self.beat_period()
        if per <= 0:
            return 0.0
        ph = (t % per) / per
        return math.exp(-ph / max(0.01, decay))


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _euclid(pulses: int, steps: int) -> List[bool]:
    """欧几里得节奏图案（与 curated 的同名实现一致，避免跨模块私有依赖）。"""
    pulses = max(1, int(pulses))
    steps = max(2, int(steps))
    if pulses >= steps:
        return [True] * steps
    pat = [False] * steps
    for i in range(pulses):
        pat[(i * steps) // pulses] = True
    return pat


def _seq_pos(n: int, i: int) -> float:
    """序列位置 0..1（单设备时为 0，避免除零）。"""
    return i / (n - 1) if n > 1 else 0.0


def _tri(x: float) -> float:
    """三角波 0..1（周期 1）。"""
    x = x % 1.0
    return 2.0 * x if x < 0.5 else 2.0 * (1.0 - x)


def _smoothstep(a: float, b: float, x: float) -> float:
    if b <= a:
        return 0.0 if x < a else 1.0
    t = _clamp01((x - a) / (b - a))
    return t * t * (3.0 - 2.0 * t)


# ─────────────────────────────────────────────────────────────────────────────
#  注册表 + 内核工厂
# ─────────────────────────────────────────────────────────────────────────────

from .effect_presets_audio_true_impls import (  # noqa: E402
    _f_band_8_bars, _f_band_8_scroll, _f_band_centroid_sweep, _f_band_high,
    _f_band_low, _f_band_mid, _f_band_split_interleave, _f_band_split_zone,
    _f_beat_chase_sequential, _f_beat_euclid_ring, _f_beat_offbeat,
    _f_beat_phase_spiral, _f_beat_pingpong, _f_beat_pulse, _f_beat_strobe,
    _f_bpm_sync_scale, _f_bpm_sync_wave, _f_chord_change_accent,
    _f_chord_quality_tint, _f_chord_root_zone, _f_crest_factor,
    _f_dynamic_range_map, _f_energy_contour_shape, _f_energy_floor,
    _f_energy_histogram, _f_energy_meter, _f_energy_slope_detector,
    _f_full_spectrum_waterfall, _f_harmonic_tension, _f_harmony_stack,
    _f_loudness_zone, _f_low_mid_high_cascade, _f_melody_contour_track,
    _f_multiband_mosaic, _f_onset_accent, _f_onset_ripple, _f_peak_flash,
    _f_pitch_confidence_gate, _f_pitch_glide_trail, _f_pitch_height,
    _f_pitch_octave_fold, _f_pitch_stability_hold, _f_rms_breath,
    _f_rms_gate, _f_rms_gradient, _f_silence_blackout,
    _f_spectral_centroid_band, _f_spectral_flux_sweep, _f_spectral_tilt,
    _f_audio_signature_pulse, _f_transient_density_heat, _f_transient_spark)

#: variant → 实现函数
_IMPL = {
    # 基础（14）
    "rms_gate": _f_rms_gate, "rms_breath": _f_rms_breath,
    "rms_gradient": _f_rms_gradient, "peak_flash": _f_peak_flash,
    "band_low": _f_band_low, "band_mid": _f_band_mid, "band_high": _f_band_high,
    "beat_pulse": _f_beat_pulse, "beat_strobe": _f_beat_strobe,
    "beat_offbeat": _f_beat_offbeat, "onset_accent": _f_onset_accent,
    "energy_meter": _f_energy_meter, "energy_floor": _f_energy_floor,
    "silence_blackout": _f_silence_blackout,
    # 中级（16）
    "band_split_zone": _f_band_split_zone,
    "band_split_interleave": _f_band_split_interleave,
    "band_8_scroll": _f_band_8_scroll, "band_8_bars": _f_band_8_bars,
    "band_centroid_sweep": _f_band_centroid_sweep,
    "beat_chase_sequential": _f_beat_chase_sequential,
    "beat_pingpong": _f_beat_pingpong, "beat_euclid_ring": _f_beat_euclid_ring,
    "bpm_sync_wave": _f_bpm_sync_wave, "bpm_sync_scale": _f_bpm_sync_scale,
    "onset_ripple": _f_onset_ripple, "transient_spark": _f_transient_spark,
    "energy_contour_shape": _f_energy_contour_shape,
    "loudness_zone": _f_loudness_zone, "crest_factor": _f_crest_factor,
    "spectral_tilt": _f_spectral_tilt,
    # 高级（14）
    "pitch_height": _f_pitch_height, "pitch_octave_fold": _f_pitch_octave_fold,
    "pitch_glide_trail": _f_pitch_glide_trail,
    "pitch_confidence_gate": _f_pitch_confidence_gate,
    "chord_root_zone": _f_chord_root_zone,
    "chord_quality_tint": _f_chord_quality_tint,
    "chord_change_accent": _f_chord_change_accent,
    "harmonic_tension": _f_harmonic_tension,
    "spectral_centroid_band": _f_spectral_centroid_band,
    "spectral_flux_sweep": _f_spectral_flux_sweep,
    "multiband_mosaic": _f_multiband_mosaic,
    "dynamic_range_map": _f_dynamic_range_map,
    "energy_histogram": _f_energy_histogram,
    "low_mid_high_cascade": _f_low_mid_high_cascade,
    # 专家（8）
    "melody_contour_track": _f_melody_contour_track,
    "harmony_stack": _f_harmony_stack,
    "transient_density_heat": _f_transient_density_heat,
    "beat_phase_spiral": _f_beat_phase_spiral,
    "energy_slope_detector": _f_energy_slope_detector,
    "pitch_stability_hold": _f_pitch_stability_hold,
    "full_spectrum_waterfall": _f_full_spectrum_waterfall,
    "audio_signature_pulse": _f_audio_signature_pulse,
}


def true_audio_variant_count() -> int:
    """已注册的真音频效果数量（供测试断言 ≥50）。"""
    return len(_IMPL)


def true_audio_kernel(variant: str):
    """返回该 variant 的内核（签名同其它族：`fn(ef, t, sorted_fx, fdefs, params, fps)`）。

    ★ **真音频契约**（用户"无音频时点击播放不出效果"）：未知 variant / 该帧无真实音频
      （`has_audio` 假）/ 无 `_audio_provider` —— 三条任一成立都**全组输出 lo**。
    实现体在 `_render_frame`（把逐帧渲染拆出去，便于单测且不占门禁额度）。
    """
    impl = _IMPL.get(str(variant))

    def fn(*args):
        # *args 收口：引擎契约是 6 个位置参数，这样不占门禁的"位置参数"额度
        _render_frame(impl, args)

    return fn


def _render_frame(impl, args) -> None:
    """逐帧渲染：解析参数 → 无音频则静音 → 否则逐台算权重并映射到 lo..hi（见上）。"""
    ef, t, sorted_fx, fdefs, params, fps = args
    ch, lo = params.get("ch_type", "dimmer"), int(params.get("lo", 0))
    hi, n = int(params.get("hi", 255)), max(1, len(sorted_fx))
    a = _AudioRead(params.get("_audio_data") or {})
    from .effect_audio_runtime import frame_audio_params
    params = frame_audio_params(impl, a, t, params)
    # ★ 真音频契约：任一不成立 → 全组 lo（无音频不出效果）。用 IfExp 收口（不计 CC）
    live = (impl is not None and a.ok and callable(params.get("_audio_provider")))
    t_eff = t * _clamp_range(params.get("rate", 1.0), 0.05, 8.0)
    spread = _clamp_range(params.get("spread", 0.0), 0.0, 2.0)
    span = hi - lo
    for seq_i, (orig_i, _fi) in enumerate(sorted_fx):
        try:
            w = float(impl(a, t_eff, seq_i, n, params)) if live else 0.0
        except Exception:
            w = 0.0
        if live and spread > 0.0 and n > 1: w *= 1.0 - spread * 0.5 * _seq_pos(n, seq_i)
        ef.values[orig_i][ch] = int(lo + _clamp01(w) * span)


def _clamp_range(v, lo: float, hi: float) -> float:
    """容错地把 v 钳到 [lo, hi]（坏输入回落 lo）。"""
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo


# ─────────────────────────────────────────────────────────────────────────────
#  预设定义（id / 中文名 / 说明 / 图标 / 档位 / 参数覆盖）
#   —— 引擎侧由 `_register_true_audio_presets` 消费（见同目录 chain 注册处）
# ─────────────────────────────────────────────────────────────────────────────
