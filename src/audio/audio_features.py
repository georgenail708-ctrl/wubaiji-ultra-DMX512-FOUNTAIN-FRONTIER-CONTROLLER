# -*- coding: utf-8 -*-
"""音频预分析特征（音频调制轨道的数据源，R174 #7）。

设计（用户规格）：
  - **确定性**：同一音频文件 + 同参数 → 逐字节相同的特征结果；缓存键 =
    文件 sha256 + 分析参数版本号。
  - 特征全部落在 beat_this 节拍网格上：BPM/拍位/强拍（SOTA 实测 A/B/C ≥0.95）
    + 每拍能量/频带/onset 强度（逐拍采样，供调制曲线消费）+ 段落边界
    （section_boundaries binseg）。
  - 结果可 JSON 序列化（存 `Project` 内或独立缓存目录），加载零依赖重现。

离线预分析（秒级模型推理），绝不进实时播放热路径。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

#: 特征格式版本号——分析代码变更时递增，旧缓存自动失效
FEATURE_VERSION = 2


@dataclass
class AudioFeatures:
    """一首音频的逐拍特征与同次分析的连续曲线。"""

    bpm: float = 120.0
    beats_per_bar: int = 4
    #: 节拍时刻序列（秒，单调递增）
    beat_times: List[float] = field(default_factory=list)
    #: 每拍强度 0..1（拍点邻域 RMS 峰值归一化）
    beat_strength: List[float] = field(default_factory=list)
    #: 每拍频带能量 0..1（low/mid/high 三带，拍点邻域均值归一化）
    band_energy: List[List[float]] = field(default_factory=list)
    #: 每拍 onset 增量 0..1（相邻拍间 flux 增量）
    onset_flux: List[float] = field(default_factory=list)
    #: 段落边界时刻（binseg 编排跳变点）
    section_boundaries: List[float] = field(default_factory=list)
    #: 音频总时长（秒）
    duration: float = 0.0
    #: 连续曲线复用同一次 RMS/STFT；旧逐拍字段保持不变。
    curve_times: List[float] = field(default_factory=list)
    energy_curve: List[float] = field(default_factory=list)
    rms_curve: List[float] = field(default_factory=list)
    band_curve: List[List[float]] = field(default_factory=list)
    onset_curve: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": FEATURE_VERSION,
            "bpm": self.bpm,
            "beats_per_bar": self.beats_per_bar,
            "beat_times": self.beat_times,
            "beat_strength": self.beat_strength,
            "band_energy": self.band_energy,
            "onset_flux": self.onset_flux,
            "section_boundaries": self.section_boundaries,
            "duration": self.duration,
            "curve_times": self.curve_times,
            "energy_curve": self.energy_curve,
            "rms_curve": self.rms_curve,
            "band_curve": self.band_curve,
            "onset_curve": self.onset_curve,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AudioFeatures":
        if not isinstance(d, dict) or int(d.get("version", 0)) != FEATURE_VERSION:
            return None
        return cls(
            bpm=float(d.get("bpm", 120.0)),
            beats_per_bar=int(d.get("beats_per_bar", 4)),
            beat_times=_float_values(d, "beat_times"),
            beat_strength=_float_values(d, "beat_strength"),
            band_energy=_float_rows(d, "band_energy"),
            onset_flux=_float_values(d, "onset_flux"),
            section_boundaries=_float_values(d, "section_boundaries"),
            duration=float(d.get("duration", 0.0)),
            curve_times=_float_values(d, "curve_times"),
            energy_curve=_float_values(d, "energy_curve"),
            rms_curve=_float_values(d, "rms_curve"),
            band_curve=_float_rows(d, "band_curve"),
            onset_curve=_float_values(d, "onset_curve"),
        )


def _float_values(data, key):
    return [float(value) for value in (data.get(key) or [])]


def _float_rows(data, key):
    return [[float(value) for value in row] for row in (data.get(key) or [])]


def file_sha256(path: str) -> str:
    """大文件流式 sha256（缓存键；8MB 块）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(8 * 1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def cache_key(path: str) -> str:
    """特征缓存键 = sha256(文件内容) + 分析版本号。确定性唯一真源。"""
    return f"{file_sha256(path)}:v{FEATURE_VERSION}"


def analyze_audio(path: str, use_neural: bool = True) -> Optional[AudioFeatures]:
    """离线预分析入口：神经优先（beat_this），不可用回落 librosa beat_track。

    返回 None = 文件不可读 / 全分析失败。装配在 `_assemble_features`。
    """
    if not os.path.isfile(path):
        return None
    try:
        import soundfile as sf
    except ImportError:
        return None
    try:
        y, sr = sf.read(path, dtype="float32", always_2d=False)
    except Exception:
        logger.warning("analyze_audio: cannot read %s", path, exc_info=True)
        return None
    if y is None or len(y) == 0:
        return None
    if y.ndim > 1:
        y = y.mean(axis=1)
    beats, downbeats, bpm, beats_per_bar = _detect_grid(y, sr, use_neural)
    if not beats:
        return None
    grid = {"beats": beats, "bpm": bpm,
            "beats_per_bar": beats_per_bar, "sr": sr}
    continuous = {}
    per_beat = _per_beat_features(y, sr, beats, continuous=continuous)
    return _assemble_features(y, grid, per_beat, continuous=continuous)


def _detect_grid(y: np.ndarray, sr: int, use_neural: bool):
    """节拍网格：beat_this 优先，不可用回落 librosa beat_track。"""
    import librosa
    beats: List[float] = []
    downbeats: List[float] = []
    bpm = 120.0
    beats_per_bar = 4
    if use_neural:
        from .neural_beats import analyze_beats as _nb
        nb = _nb(y, sr)
    else:
        nb = None
    if nb is not None:
        return (list(nb["beat_times"]), list(nb["downbeat_times"]),
                float(nb["bpm"]), int(nb["meter"][0]))
    tempo, bt = librosa.beat.beat_track(y=y, sr=sr, units="time")
    bpm = float(np.atleast_1d(tempo)[0])
    beats = [float(t) for t in bt]
    downbeats = beats[::4]
    bpm = max(60.0, min(200.0, bpm))
    return beats, downbeats, bpm, 4


def _assemble_features(y: np.ndarray, grid: dict, per_beat: tuple,
                       *, continuous: Optional[dict] = None) -> Optional[AudioFeatures]:
    """拼装 AudioFeatures（含段落边界；binseg 失败为空表不算失败）。

    `per_beat` = (beat_strength, band_energy, onset_flux)（_per_beat_features 输出）。
    """
    beats = grid["beats"]
    bpm = grid["bpm"]
    beats_per_bar = grid["beats_per_bar"]
    sr = grid["sr"]
    beat_strength, band_energy, onset_flux = per_beat
    duration = len(y) / float(sr)
    boundaries: List[float] = []
    try:
        from .section_boundaries import detect_section_boundaries
        sec = detect_section_boundaries(y, sr, beats)
        if sec:
            boundaries = list(sec["boundaries"])
    except Exception:
        logger.debug("section boundaries unavailable", exc_info=True)
    return AudioFeatures(
        bpm=bpm,
        beats_per_bar=beats_per_bar,
        beat_times=beats,
        beat_strength=beat_strength,
        band_energy=band_energy,
        onset_flux=onset_flux,
        section_boundaries=boundaries,
        duration=duration,
        **(continuous or {}),
    )


def _per_beat_features(y: np.ndarray, sr: int, beats: List[float],
                       *, continuous: Optional[dict] = None):
    """逐拍采样三组特征（确定性：纯数值运算）。"""
    import librosa
    HOP = 512
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    S = np.abs(librosa.stft(y, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=(S.shape[0] - 1) * 2)
    low = S[(freqs >= 20) & (freqs < 250)].mean(axis=0)
    mid = S[(freqs >= 250) & (freqs < 2000)].mean(axis=0)
    high = S[(freqs >= 2000) & (freqs < 10000)].mean(axis=0)
    flux = np.sqrt(np.sum(np.diff(S, axis=1) ** 2, axis=0))  # 谱通量
    tfr = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=HOP)
    rms_t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=HOP)

    maxima = tuple(_feature_peak(v) for v in (rms, low, mid, high, flux))
    if continuous is not None:
        continuous.update(_continuous_curves(
            tfr, rms, (low, mid, high), flux, maxima=maxima))
    return _sample_per_beat(beats, {"rms": rms, "bands": (low, mid, high),
                                   "flux": flux, "times": tfr, "rms_t": rms_t,
                                   "maxima": maxima})


def _feature_peak(values):
    return (float(values.max()) or 1.0) if len(values) else 1.0


def _feature_frame_index(times, t):
    return int(np.clip(np.searchsorted(times, t), 0, len(times) - 1))


def _sample_per_beat(beats, frames):
    """原逐拍采样职责；连续曲线不改变原邻域/归一化口径。"""
    rms, flux = frames["rms"], frames["flux"]
    low, mid, high = frames["bands"]
    rms_max, low_max, mid_max, high_max, flux_max = frames["maxima"]
    strengths, band_rows, fluxes = [], [], []
    for i, t in enumerate(beats):
        # 拍点邻域（到下一拍的中点）内 RMS 峰值 → 拍强度
        t_end = beats[i + 1] if i + 1 < len(beats) else t + 0.5
        a = _feature_frame_index(frames["rms_t"], t)
        b = _feature_frame_index(frames["rms_t"], t_end)
        seg = rms[a:b + 1] if b > a else rms[a:a + 1]
        strengths.append(float(seg.max()) / rms_max if len(seg) else 0.0)
        fi = _feature_frame_index(frames["times"], t)
        band_rows.append([float(low[fi]) / low_max, float(mid[fi]) / mid_max,
                          float(high[fi]) / high_max])
        fj = _feature_frame_index(frames["times"], t_end)
        seg_flux = float(flux[fi:fj + 1].max()) if fj > fi else float(flux[fi])
        fluxes.append(seg_flux / flux_max)
    return strengths, band_rows, fluxes


def _continuous_curves(times, rms, bands, flux, *, maxima: tuple) -> dict:
    """同一分析的全时标曲线，补首帧零flux且保真实RMS用于静音判定。"""
    count = len(times)
    energy = np.asarray(rms[:count], dtype=np.float64)
    rows = np.column_stack([v[:count] / m for v, m in zip(bands, maxima[1:4])])
    onset = np.concatenate(([0.0], flux))[:count] / maxima[4]
    return {"curve_times": times.tolist(), "energy_curve": (energy / maxima[0]).tolist(),
            "rms_curve": energy.tolist(), "band_curve": rows.tolist(),
            "onset_curve": onset.tolist()}


def features_to_json(features: AudioFeatures) -> str:
    return json.dumps(features.to_dict(), ensure_ascii=False)


def features_from_json(text: str) -> Optional[AudioFeatures]:
    try:
        return AudioFeatures.from_dict(json.loads(text))
    except Exception:
        return None
