# -*- coding: utf-8 -*-
"""
deep_music_analysis.py — 离线深度音乐识别（纯逻辑，无 Qt）
============================================================
职责：离线把整曲音乐榨干，产出密集多维特征时间线 + 数据驱动段落。

设计原则
  - 纯函数模块：只依赖 librosa / numpy，绝不起线程、绝不依赖 Qt。
    调用方（L2/L3）负责把 analyze() 丢到后台线程。
  - 确定性：agglomerative / pyin 默认确定；本模块无随机数；任何随机派生吃 seed。
  - 一次榨干：HPSS 谐波/打击分离 → beat 网格 → 4 频带 onset → pyin 旋律 →
    chroma 和弦根音 → rms/centroid → 数据驱动结构边界 → 每段聚合 CharacterSegment。

性能
  - pyin 是大头（谐波成分上跑），允许慢。实测 22050Hz、183s 曲约 85s。
  - 全程 sr=22050、hop=512 降采样，够灯光分析用。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

try:
    import librosa
    _LIBROSA_AVAILABLE = True
except ImportError:
    librosa = None  # type: ignore
    _LIBROSA_AVAILABLE = False

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------
SR = 22050
HOP = 512

# 和弦质量编码
CHORD_MAJOR = 0
CHORD_MINOR = 1


# ----------------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------------
@dataclass
class DenseFeatureTimeline:
    """密集逐帧特征时间线。所有逐帧数组按 min(len) 截断对齐到 n_frames。"""
    sr: int = SR
    hop: int = HOP
    n_frames: int = 0
    duration: float = 0.0
    tempo: float = 0.0
    # 逐帧 (n,)
    times: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    rms: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    rms_harm: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    rms_perc: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    centroid: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    flux: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    f0_midi: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    voiced_prob: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    # 多频带 onset (4,n): sub / bass / mid / high
    onset_bands: np.ndarray = field(default_factory=lambda: np.zeros((4, 0), dtype=np.float64))
    # 色度 (12,n)
    chroma: np.ndarray = field(default_factory=lambda: np.zeros((12, 0), dtype=np.float64))
    # 节拍掩码 (n,) bool —— 该帧附近是否落在 beat 网格上
    beat_mask: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    # 和弦根音 (n,) int16 (0-11, -1=未定)
    chord_root: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int16))
    # 调性major得分 (n,) float64
    key_major_score: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    # 调性minor得分 (n,) float64
    key_minor_score: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float64))
    # 强拍掩码 (n,) bool
    downbeat_mask: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))


@dataclass
class CharacterSegment:
    """数据驱动段落 + 情感画像。"""
    t_start: float = 0.0
    t_end: float = 0.0
    energy: float = 0.0           # 该段平均能量(归一前的 rms 均值)
    energy_rank: float = 0.0      # 0-1 在全曲段落中的能量分位
    centroid: float = 0.0         # 平均谱心(Hz)
    perc_density: float = 0.0     # 打击密度(onset 高频带强度)
    rising: bool = False          # 能量在段内是否上升
    has_melody: bool = False      # 是否有旋律(voiced 均值>0.5)
    is_build: bool = False        # 是否 build(渐强+上升)
    is_drop: bool = False         # 是否 drop(前段低/本段爆发)
    dominant_chord_root: int = -1 # 主导和弦根音 0-11
    dominant_chord_quality: int = CHORD_MAJOR  # 0 大 / 1 小
    character: str = ""           # 中文情感标签串


@dataclass
class DeepAnalysis:
    timeline: DenseFeatureTimeline = field(default_factory=DenseFeatureTimeline)
    events: List[Dict] = field(default_factory=list)        # {t,type,strength}
    segments: List[CharacterSegment] = field(default_factory=list)
    tempo: float = 0.0
    duration: float = 0.0
    audio_sig: str = ""
    seed: int = 0


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def audio_signature(path: str) -> str:
    """路径 + size + mtime 指纹。os.stat 失败回退纯路径。"""
    try:
        st = os.stat(path)
        return f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return os.path.abspath(path) if path else ""


def _norm01(arr: np.ndarray) -> np.ndarray:
    """归一到 0-1（鲁棒：用 max，全零返回零）。"""
    arr = np.asarray(arr, dtype=np.float64)
    if arr.size == 0:
        return arr
    mx = float(np.max(arr))
    if mx <= 1e-12:
        return np.zeros_like(arr)
    return arr / mx


# 主音 chroma 模板（大三和弦 / 小三和弦），用于估计和弦根音与质量
_MAJOR_TPL = np.array([1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0], dtype=np.float64)
_MINOR_TPL = np.array([1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0], dtype=np.float64)


def _estimate_chord_root(chroma_col: np.ndarray):
    """对单帧/单段 12 维 chroma 估计 (root, quality)。返回 (int, int)。"""
    c = np.asarray(chroma_col, dtype=np.float64)
    if c.size != 12 or float(np.sum(c)) <= 1e-9:
        return -1, CHORD_MAJOR
    best_root = -1
    best_q = CHORD_MAJOR
    best_score = -1.0
    for root in range(12):
        maj = np.roll(_MAJOR_TPL, root)
        minr = np.roll(_MINOR_TPL, root)
        s_maj = float(np.dot(c, maj))
        s_min = float(np.dot(c, minr))
        if s_maj > best_score:
            best_score, best_root, best_q = s_maj, root, CHORD_MAJOR
        if s_min > best_score:
            best_score, best_root, best_q = s_min, root, CHORD_MINOR
    return best_root, best_q


# Krumhansl-Schmuckler key profiles (major and minor)
_K_PROFILE_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                             2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_K_PROFILE_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                             2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def detect_key(chroma_sequence: np.ndarray) -> Tuple[str, float, float, float]:
    """Krumhansl-Schmuckler调性检测。

    Args:
        chroma_sequence: (12, n_frames) chroma矩阵

    Returns:
        (key_name, major_score, minor_score, confidence)
    """
    if chroma_sequence is None or chroma_sequence.size == 0:
        return ("C major", 0.5, 0.0, 0.3)

    # 取整个序列的均值chroma
    mean_chroma = np.mean(chroma_sequence, axis=1)
    if mean_chroma.sum() < 1e-9:
        return ("C major", 0.5, 0.0, 0.3)
    mean_chroma = mean_chroma / mean_chroma.sum()

    best_major_score = -1.0
    best_minor_score = -1.0
    best_major_key = 0
    best_minor_key = 0

    for root in range(12):
        # 旋转profile到对应调性
        major_profile = np.roll(_K_PROFILE_MAJOR, root)
        minor_profile = np.roll(_K_PROFILE_MINOR, root)
        major_corr = np.corrcoef(mean_chroma, major_profile)[0, 1]
        minor_corr = np.corrcoef(mean_chroma, minor_profile)[0, 1]
        if major_corr > best_major_score:
            best_major_score = major_corr
            best_major_key = root
        if minor_corr > best_minor_score:
            best_minor_score = minor_corr
            best_minor_key = root

    key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    # 选major还是minor
    if best_major_score >= best_minor_score:
        key_name = f"{key_names[best_major_key]} major"
        confidence = float(np.clip(best_major_score, 0.0, 1.0))
        return (key_name, float(best_major_score), float(best_minor_score), confidence)
    else:
        key_name = f"{key_names[best_minor_key]} minor"
        confidence = float(np.clip(best_minor_score, 0.0, 1.0))
        return (key_name, float(best_major_score), float(best_minor_score), confidence)


def _character_label(energy_rank: float, perc_density_rank: float,
                     centroid_rank: float, has_melody: bool,
                     rising: bool) -> str:
    """按规则组合中文情感标签串。"""
    parts: List[str] = []
    # 能量主基调
    if energy_rank < 0.35:
        parts.append("宁静温柔")
    elif energy_rank > 0.7:
        parts.append("激昂澎湃")
    else:
        parts.append("平稳推进")
    # 打击
    if perc_density_rank > 0.6:
        parts.append("强打击")
    # 旋律
    if has_melody:
        parts.append("歌唱")
    # 谱心明暗
    if centroid_rank > 0.66:
        parts.append("明亮")
    elif centroid_rank < 0.33:
        parts.append("低沉")
    # 渐强
    if rising:
        parts.append("渐强build")
    return "·".join(parts)


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def analyze(audio_path: str, seed: int = 0,
            progress: Optional[Callable[[float, str], None]] = None,
            log: Optional[Callable[[str], None]] = None) -> DeepAnalysis:
    """
    离线榨干整曲音乐识别。纯函数，调用方负责后台线程。

    Args:
        audio_path: 音频文件路径。
        seed: 随机种子（本模块无随机，仅入参留存供上层派生）。
        progress: 可选回调 progress(frac0to1, message)。
        log: 可选回调 log(message)。

    Returns:
        DeepAnalysis
    """
    if not _LIBROSA_AVAILABLE:
        raise ImportError("librosa is required for audio analysis but is not installed")

    def _p(frac: float, msg: str):
        if progress is not None:
            try:
                progress(float(frac), str(msg))
            except Exception:
                logger.debug("progress callback failed")

    def _l(msg: str):
        if log is not None:
            try:
                log(str(msg))
            except Exception:
                logger.debug("log callback failed")

    sig = audio_signature(audio_path)

    # 1) 加载（降采样）
    _p(0.02, "加载音频")
    try:
        y, sr = librosa.load(audio_path, sr=SR, mono=True)
    except Exception as e:
        logger.debug("librosa.load failed: %s", e)
        raise RuntimeError(f"Failed to load audio file '{audio_path}': {e}") from e
    duration = float(len(y)) / float(sr) if sr else 0.0
    _l(f"loaded {duration:.1f}s @ {sr}Hz")

    # 2) HPSS 谐波/打击分离
    _p(0.10, "HPSS 谐波/打击分离")
    try:
        y_harm, y_perc = librosa.effects.hpss(y)
    except Exception as e:
        logger.debug("librosa.effects.hpss failed: %s", e)
        y_harm, y_perc = y.copy(), np.zeros_like(y)

    # 3) beat 网格（用打击成分）
    _p(0.20, "节拍跟踪")
    tempo_raw, beat_times = librosa.beat.beat_track(y=y_perc, sr=sr, units='time')
    tempo = float(np.atleast_1d(tempo_raw)[0]) if tempo_raw is not None else 0.0
    _l(f"tempo={tempo:.1f} beats={len(beat_times)}")

    # ── 多方法 BPM 交叉验证（enhanced_bpm 模块 — 仅日志, 不覆盖）──
    try:
        from .enhanced_bpm import detect_bpm_multi
        multi = detect_bpm_multi(y_perc, sr)
        if multi.confidence > 0.7 and abs(multi.bpm - tempo) > max(10.0, tempo * 0.12):
            # 偏差 > 12% 且有高置信度 → 记录警告但保留 librosa 原始值
            _l(f"BPM DISAGREEMENT: librosa={tempo:.1f} vs consensus={multi.bpm:.1f} (conf={multi.confidence:.2f}) — keeping librosa")
    except ImportError:
        pass

    # 4) 逐帧基础特征
    _p(0.30, "RMS / 谱心 / flux")
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    rms_harm = librosa.feature.rms(y=y_harm, hop_length=HOP)[0]
    rms_perc = librosa.feature.rms(y=y_perc, hop_length=HOP)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=HOP)[0]
    # 谱通量(onset_strength 即谱通量包络)
    flux = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)

    # 5) 4 频带 onset（用打击成分）: sub / bass / mid / high
    _p(0.40, "分频带 onset 包络")
    onset_multi = librosa.onset.onset_strength_multi(
        y=y_perc, sr=sr, hop_length=HOP, channels=[0, 4, 16, 48, 128])
    # channels 给 N+1 个边界 → N 个频带，这里 5 个边界 → 4 频带
    onset_bands = np.asarray(onset_multi, dtype=np.float64)
    if onset_bands.shape[0] > 4:
        onset_bands = onset_bands[:4]
    elif onset_bands.shape[0] < 4:
        pad = np.zeros((4 - onset_bands.shape[0], onset_bands.shape[1]), dtype=np.float64)
        onset_bands = np.vstack([onset_bands, pad])

    # 6) chroma 和弦
    _p(0.50, "色度 / 和弦根音")
    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr, hop_length=HOP)

    # 7) MFCC（结构边界用）
    _p(0.55, "MFCC")
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=HOP, n_mfcc=13)

    # 8) pyin 旋律（谐波成分，大头）
    _p(0.60, "pyin 旋律提取（较慢）")
    try:
        f0, vflag, vprob = librosa.pyin(
            y_harm, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C6'),
            sr=sr, hop_length=HOP)
    except Exception as e:
        logger.debug("librosa.pyin failed: %s", e)
        f0 = np.full(rms.shape[0], np.nan, dtype=np.float64)
        vprob = np.zeros(rms.shape[0], dtype=np.float64)
    if f0 is None:
        f0 = np.full(rms.shape[0], np.nan, dtype=np.float64)
    if vprob is None:
        vprob = np.zeros(rms.shape[0], dtype=np.float64)
    # f0 → midi（无声为 0）
    f0_arr = np.asarray(f0, dtype=np.float64)
    f0_midi = np.zeros_like(f0_arr)
    valid = np.isfinite(f0_arr) & (f0_arr > 0)
    if np.any(valid):
        f0_midi[valid] = librosa.hz_to_midi(f0_arr[valid])
    voiced_prob = np.nan_to_num(np.asarray(vprob, dtype=np.float64), nan=0.0)

    # ------------------------------------------------------------------
    # 9) 帧对齐
    # ------------------------------------------------------------------
    _p(0.80, "对齐特征帧")
    # 取所有逐帧数组的最小长度
    lengths = [rms.shape[0], rms_harm.shape[0], rms_perc.shape[0],
               centroid.shape[0], flux.shape[0], f0_midi.shape[0],
               voiced_prob.shape[0], onset_bands.shape[1], chroma.shape[1],
               mfcc.shape[1]]
    n = int(min(lengths)) if lengths else 0

    def _cut(a):
        return np.asarray(a, dtype=np.float64)[:n]

    rms = _cut(rms)
    rms_harm = _cut(rms_harm)
    rms_perc = _cut(rms_perc)
    centroid = _cut(centroid)
    flux = _cut(flux)
    f0_midi = _cut(f0_midi)
    voiced_prob = _cut(voiced_prob)
    onset_bands = np.asarray(onset_bands, dtype=np.float64)[:, :n]
    chroma = np.asarray(chroma, dtype=np.float64)[:, :n]
    mfcc = np.asarray(mfcc, dtype=np.float64)[:, :n]
    times = librosa.frames_to_time(np.arange(n), sr=sr, hop_length=HOP)

    # beat_mask：把 beat 时间映射到最近帧
    beat_mask = np.zeros(n, dtype=bool)
    if n > 0 and len(beat_times) > 0:
        beat_frames = librosa.time_to_frames(beat_times, sr=sr, hop_length=HOP)
        beat_frames = beat_frames[(beat_frames >= 0) & (beat_frames < n)]
        beat_mask[beat_frames] = True

    # 逐帧和弦根音
    chord_root = np.full(n, -1, dtype=np.int16)
    for i in range(n):
        r, _q = _estimate_chord_root(chroma[:, i])
        chord_root[i] = r

    timeline = DenseFeatureTimeline(
        sr=sr, hop=HOP, n_frames=n, duration=duration, tempo=tempo,
        times=times, rms=rms, rms_harm=rms_harm, rms_perc=rms_perc,
        centroid=centroid, flux=flux, f0_midi=f0_midi, voiced_prob=voiced_prob,
        onset_bands=onset_bands, chroma=chroma, beat_mask=beat_mask,
        chord_root=chord_root)

    # Key detection (Krumhansl-Schmuckler)
    try:
        key_name, key_major, key_minor, key_conf = detect_key(timeline.chroma)
        timeline.key_major_score = np.full(timeline.n_frames, key_major, dtype=np.float64)
        timeline.key_minor_score = np.full(timeline.n_frames, key_minor, dtype=np.float64)
    except Exception:
        timeline.key_major_score = np.full(timeline.n_frames, 0.5, dtype=np.float64)
        timeline.key_minor_score = np.full(timeline.n_frames, 0.0, dtype=np.float64)
        logger.debug("detect_key failed, using defaults")

    # ------------------------------------------------------------------
    # 10) 数据驱动结构边界
    # ------------------------------------------------------------------
    _p(0.88, "结构分段")
    # 结构层细分:标杆实测动作起点平均间隔 2.2s。结构段作为情感骨架取更细粒度
    # (~每 5s 一段),段内再由 L3 按各声部节奏粒度二次切短动作。
    nseg = max(12, min(64, int(duration / 5.0))) if duration > 0 else 12
    segments: List[CharacterSegment] = []
    bound_frames = np.array([0, n], dtype=int)
    if n >= nseg and nseg >= 2:
        feat = np.vstack([
            librosa.util.normalize(mfcc, axis=1),
            librosa.util.normalize(chroma, axis=1),
        ])
        bounds = librosa.segment.agglomerative(feat, nseg)
        bounds = np.asarray(bounds, dtype=int)
        # 规整为递增边界，补首尾
        bset = sorted(set(int(b) for b in bounds if 0 <= b < n))
        if not bset or bset[0] != 0:
            bset = [0] + bset
        if bset[-1] != n:
            bset = bset + [n]
        bound_frames = np.array(bset, dtype=int)

    # ------------------------------------------------------------------
    # 11) 每段聚合 → CharacterSegment（含情感标签）
    # ------------------------------------------------------------------
    rms_n = _norm01(rms)
    perc_n = _norm01(rms_perc)
    cen_n = _norm01(centroid)
    high_band = onset_bands[3] if onset_bands.shape[0] > 3 else np.zeros(n)
    high_n = _norm01(high_band)

    seg_raw = []  # (t0,t1,energy,centroid,perc,rising,has_mel,chord_root,chord_q)
    for k in range(len(bound_frames) - 1):
        a, b = int(bound_frames[k]), int(bound_frames[k + 1])
        if b <= a:
            continue
        e = float(np.mean(rms_n[a:b]))
        cen = float(np.mean(centroid[a:b]))
        cen_rank = float(np.mean(cen_n[a:b]))
        perc_rank = float(np.mean(high_n[a:b]))
        vmean = float(np.mean(voiced_prob[a:b]))
        has_mel = vmean > 0.5
        # 段内能量是否上升：后半段均值 > 前半段
        mid = (a + b) // 2
        first = float(np.mean(rms_n[a:mid])) if mid > a else e
        second = float(np.mean(rms_n[mid:b])) if b > mid else e
        rising = second > first * 1.12
        # 段主导和弦
        cmean = np.mean(chroma[:, a:b], axis=1)
        croot, cq = _estimate_chord_root(cmean)
        seg_raw.append(dict(
            t0=float(times[a]) if a < n else duration,
            t1=float(times[b - 1]) if (b - 1) < n else duration,
            energy=e, centroid=cen, cen_rank=cen_rank, perc_rank=perc_rank,
            rising=rising, has_mel=has_mel, croot=croot, cq=cq))

    # 能量分位
    energies = np.array([s["energy"] for s in seg_raw], dtype=np.float64) if seg_raw else np.zeros(0)
    if energies.size > 1:
        order = np.argsort(np.argsort(energies))
        ranks = order / float(energies.size - 1)
    else:
        ranks = np.zeros(energies.size)

    for idx, s in enumerate(seg_raw):
        e_rank = float(ranks[idx]) if idx < len(ranks) else 0.0
        rising = bool(s["rising"])
        # build：渐强 + 偏中高能量
        is_build = rising and 0.3 <= e_rank <= 0.85
        # drop：相对前一段能量大跳升 + 本段高能 + 强打击
        is_drop = False
        if idx > 0:
            prev_e = float(ranks[idx - 1]) if (idx - 1) < len(ranks) else 0.0
            if e_rank > 0.6 and (e_rank - prev_e) > 0.3 and s["perc_rank"] > 0.45:
                is_drop = True
        character = _character_label(
            energy_rank=e_rank, perc_density_rank=s["perc_rank"],
            centroid_rank=s["cen_rank"], has_melody=s["has_mel"],
            rising=rising)
        segments.append(CharacterSegment(
            t_start=s["t0"], t_end=s["t1"], energy=s["energy"],
            energy_rank=e_rank, centroid=s["centroid"],
            perc_density=s["perc_rank"], rising=rising,
            has_melody=s["has_mel"], is_build=is_build, is_drop=is_drop,
            dominant_chord_root=int(s["croot"]),
            dominant_chord_quality=int(s["cq"]), character=character))

    # ------------------------------------------------------------------
    # 12) 事件提取（beat / onset 峰 / 段落边界 / drop）
    # ------------------------------------------------------------------
    _p(0.95, "事件提取")
    events: List[Dict] = []
    # beat 事件
    for bt in beat_times:
        events.append({"t": float(bt), "type": "beat", "strength": 1.0})
    # onset 峰事件（flux 上的峰）
    if n > 0:
        try:
            onset_frames = librosa.onset.onset_detect(
                onset_envelope=flux, sr=sr, hop_length=HOP, delta=0.2, wait=3)
            for of in onset_frames:
                if 0 <= of < n:
                    events.append({
                        "t": float(times[of]), "type": "onset",
                        "strength": float(_norm01(flux)[of])})
        except Exception:
            logger.debug("onset_detect failed, skipping onset events")
    for seg in segments:
        events.append({"t": float(seg.t_start), "type": "segment",
                       "strength": float(seg.energy_rank)})
        if seg.is_drop:
            events.append({"t": float(seg.t_start), "type": "drop",
                           "strength": 1.0})
        if seg.is_build:
            events.append({"t": float(seg.t_start), "type": "build",
                           "strength": float(seg.energy_rank)})
    events.sort(key=lambda d: d["t"])

    _p(1.0, "完成")
    return DeepAnalysis(
        timeline=timeline, events=events, segments=segments,
        tempo=tempo, duration=duration, audio_sig=sig, seed=int(seed))


# ----------------------------------------------------------------------------
# DeepAnalysisCache — in-memory + optional disk cache for analysis results
# ----------------------------------------------------------------------------
import hashlib
import pickle
import threading
from pathlib import Path


class DeepAnalysisCache:
    """Process-level + optional disk cache for DeepAnalysis results.

    Keyed by audio_signature(path) = path + size + mtime.
    Disk cache uses pickle files in a configurable directory.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self._cache: Dict[str, DeepAnalysis] = {}
        self._lock = threading.Lock()
        self._cache_dir: Optional[Path] = None
        if cache_dir:
            self._cache_dir = Path(cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _sig_key(self, sig: str) -> str:
        return hashlib.sha256(sig.encode()).hexdigest()[:16]

    def _disk_path(self, sig: str) -> Optional[Path]:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{self._sig_key(sig)}.pkl"

    def get(self, audio_path: str) -> Optional[DeepAnalysis]:
        sig = audio_signature(audio_path)
        with self._lock:
            if sig in self._cache:
                return self._cache[sig]
        # try disk
        dp = self._disk_path(sig)
        if dp and dp.is_file():
            try:
                with open(dp, "rb") as fh:
                    obj = pickle.load(fh)
                if isinstance(obj, DeepAnalysis):
                    with self._lock:
                        self._cache[sig] = obj
                    return obj
            except Exception:
                logger.debug("failed to load cached analysis from disk")
                pass
        return None

    def put(self, audio_path: str, analysis: DeepAnalysis):
        sig = audio_signature(audio_path)
        with self._lock:
            self._cache[sig] = analysis
        # async disk write
        dp = self._disk_path(sig)
        if dp:
            def _write():
                try:
                    with open(dp, "wb") as fh:
                        pickle.dump(analysis, fh)
                except Exception:
                    logger.debug("failed to write analysis cache to disk")
            threading.Thread(target=_write, daemon=True).start()

    def clear(self):
        with self._lock:
            self._cache.clear()


# singleton
_cache_singleton: Optional[DeepAnalysisCache] = None


def get_analysis_cache(cache_dir: Optional[str] = None) -> DeepAnalysisCache:
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = DeepAnalysisCache(cache_dir)
    return _cache_singleton


def analyze_cached(audio_path: str, tier: str = "fast",
                   progress=None, log=None, seed: int = 0) -> DeepAnalysis:
    """Get DeepAnalysis from cache or compute it. tier='fast' skips pyin (~20s vs ~85s)."""
    cache = get_analysis_cache()
    cached = cache.get(audio_path)
    if cached is not None:
        if log:
            log("(cached analysis)")
        return cached
    if tier == "fast":
        result = _analyze_fast(audio_path, seed=seed, progress=progress, log=log)
    else:
        result = analyze(audio_path, seed=seed, progress=progress, log=log)
    cache.put(audio_path, result)
    return result


def _fast_segment(t_start, t_end, energy, centroid, *, perc_rank, rising, centroid_rank):
    """Build the original fast segment with the shared full-analysis labels."""
    return CharacterSegment(
        t_start=t_start, t_end=t_end, energy=energy, energy_rank=energy,
        centroid=centroid, perc_density=perc_rank, rising=rising,
        has_melody=False, is_build=False, is_drop=False,
        dominant_chord_root=-1, dominant_chord_quality=CHORD_MAJOR,
        character=_character_label(
            energy_rank=energy, perc_density_rank=perc_rank,
            centroid_rank=centroid_rank, has_melody=False, rising=rising))


def _analyze_fast(audio_path: str, seed: int = 0,
                  progress=None, log=None) -> DeepAnalysis:
    """Faster analysis skipping pyin melody + chroma chord (~20s vs ~85s).

    Produces: BPM, beat_times, RMS, centroid, flux, onset_bands, MFCC,
    structure segments with energy/percussion labels (no melody/chord data).
    """
    if not _LIBROSA_AVAILABLE:
        raise ImportError("librosa is required for audio analysis but is not installed")

    sig = audio_signature(audio_path)
    try:
        y, sr = librosa.load(audio_path, sr=SR, mono=True)
    except Exception as e:
        logger.debug("librosa.load failed (fast): %s", e)
        raise RuntimeError(f"Failed to load audio file '{audio_path}': {e}") from e
    duration = float(len(y)) / float(sr) if sr else 0.0
    if log:
        log(f"fast analysis: {duration:.1f}s @ {sr}Hz (skip pyin/chroma)")

    try:
        y_harm, y_perc = librosa.effects.hpss(y)
    except Exception as e:
        logger.debug("librosa.effects.hpss failed (fast): %s", e)
        y_harm, y_perc = y.copy(), np.zeros_like(y)
    tempo_raw, beat_times = librosa.beat.beat_track(y=y_perc, sr=sr, units='time')
    tempo = float(np.atleast_1d(tempo_raw)[0]) if tempo_raw is not None else 0.0

    # cross-validate BPM
    try:
        from .enhanced_bpm import detect_bpm_multi
        multi = detect_bpm_multi(y_perc, sr)
        if multi.confidence > 0.7 and abs(multi.bpm - tempo) > max(10.0, tempo * 0.12):
            if log:
                log(f"BPM DISAGREEMENT: librosa={tempo:.1f} vs consensus={multi.bpm:.1f}")
    except ImportError:
        pass

    # basic features
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    rms_harm = librosa.feature.rms(y=y_harm, hop_length=HOP)[0]
    rms_perc = librosa.feature.rms(y=y_perc, hop_length=HOP)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=HOP)[0]
    flux = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)

    # 4-band onset
    onset_multi = librosa.onset.onset_strength_multi(
        y=y_perc, sr=sr, hop_length=HOP, channels=[0, 4, 16, 48, 128])
    onset_bands = np.asarray(onset_multi, dtype=np.float64)
    if onset_bands.shape[0] > 4:
        onset_bands = onset_bands[:4]

    # MFCC for segmentation
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=HOP, n_mfcc=13)

    # frame alignment
    lengths = [rms.shape[0], rms_harm.shape[0], rms_perc.shape[0],
               centroid.shape[0], flux.shape[0],
               onset_bands.shape[1], mfcc.shape[1]]
    n = int(min(lengths)) if lengths else 0

    def _cut(a):
        return np.asarray(a, dtype=np.float64)[:n]

    rms = _cut(rms)
    rms_harm = _cut(rms_harm)
    rms_perc = _cut(rms_perc)
    centroid = _cut(centroid)
    flux = _cut(flux)
    onset_bands = np.asarray(onset_bands, dtype=np.float64)[:, :n] if onset_bands.shape[1] >= n else onset_bands
    if onset_bands.shape[1] < n:
        onset_bands = np.pad(onset_bands, ((0, 0), (0, n - onset_bands.shape[1])))
    mfcc = np.asarray(mfcc, dtype=np.float64)[:, :n]
    times = librosa.frames_to_time(np.arange(n), sr=sr, hop_length=HOP)

    # beat mask
    beat_mask = np.zeros(n, dtype=bool)
    if n > 0 and len(beat_times) > 0:
        beat_frames = librosa.time_to_frames(beat_times, sr=sr, hop_length=HOP)
        beat_frames = beat_frames[(beat_frames >= 0) & (beat_frames < n)]
        beat_mask[beat_frames] = True

    # placeholder arrays for skipped features
    f0_midi = np.zeros(n, dtype=np.float64)
    voiced_prob = np.zeros(n, dtype=np.float64)
    chroma = np.zeros((12, n), dtype=np.float64)
    chord_root = np.full(n, -1, dtype=np.int16)

    timeline = DenseFeatureTimeline(
        sr=sr, hop=HOP, n_frames=n, duration=duration, tempo=tempo,
        times=times, rms=rms, rms_harm=rms_harm, rms_perc=rms_perc,
        centroid=centroid, flux=flux, f0_midi=f0_midi, voiced_prob=voiced_prob,
        onset_bands=onset_bands, chroma=chroma, beat_mask=beat_mask,
        chord_root=chord_root)

    # structure segmentation
    nseg = max(12, min(64, int(duration / 5.0))) if duration > 0 else 12
    segments: list = []
    bound_frames = np.array([0, n], dtype=int)
    if n >= nseg and nseg >= 2:
        feat = np.vstack([
            librosa.util.normalize(mfcc, axis=1),
            np.zeros((12, n), dtype=np.float64),  # no chroma → zero pad
        ])
        bounds = librosa.segment.agglomerative(feat, nseg)
        bounds = np.asarray(bounds, dtype=int)
        bset = sorted(set(int(b) for b in bounds if 0 <= b < n))
        if not bset or bset[0] != 0:
            bset = [0] + bset
        if bset[-1] != n:
            bset = bset + [n]
        bound_frames = np.array(bset, dtype=int)

    # segment aggregation
    rms_n = _norm01(rms)
    perc_n = _norm01(rms_perc)
    cen_n = _norm01(centroid)
    high_band = onset_bands[3] if onset_bands.shape[0] > 3 else np.zeros(n)
    high_n = _norm01(high_band)

    for k in range(len(bound_frames) - 1):
        a, b = int(bound_frames[k]), int(bound_frames[k + 1])
        if b <= a:
            continue
        e = float(np.mean(rms_n[a:b]))
        mid = (a + b) // 2
        first = float(np.mean(rms_n[a:mid])) if mid > a else e
        second = float(np.mean(rms_n[mid:b])) if b > mid else e
        rising = second > first * 1.12
        perc_rank = float(np.mean(high_n[a:b]))
        seg = _fast_segment(float(times[a]), float(times[min(b, n - 1)]),
                            e, float(np.mean(centroid[a:b])), perc_rank=perc_rank,
                            rising=rising, centroid_rank=float(np.mean(cen_n[a:b])))
        segments.append(seg)

    # post-process segments: mark build/drop
    for i, seg in enumerate(segments):
        if i > 0 and seg.energy > segments[i - 1].energy * 1.3 and seg.rising:
            seg.is_build = True
        if i > 0 and seg.energy > segments[i - 1].energy * 1.5 and seg.perc_density > 0.4:
            seg.is_drop = True

    events: list = []
    for seg in segments:
        if seg.is_drop:
            events.append({"t": float(seg.t_start), "type": "drop", "strength": 1.0})
        if seg.is_build:
            events.append({"t": float(seg.t_start), "type": "build", "strength": float(seg.energy_rank)})
    events.sort(key=lambda d: d["t"])

    return DeepAnalysis(
        timeline=timeline, events=events, segments=segments,
        tempo=tempo, duration=duration, audio_sig=sig, seed=int(seed))
