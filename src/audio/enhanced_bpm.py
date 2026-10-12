"""
src/audio/enhanced_bpm.py — 多方法 BPM 检测增强模块
整合 BeatNetLite (CRNN) + aubio (实时 onset) + chordscope (多方法融合) 的算法精华

为 MusicStructureAnalyzer 提供可插拔的 BPM 检测后端，不影响现有逻辑。

用法:
    from src.audio.enhanced_bpm import detect_bpm_multi, BPMResult

    result = detect_bpm_multi(audio_samples, sr=44100)
    print(f"Consensus BPM: {result.bpm:.1f} (confidence: {result.confidence:.2f})")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── 延迟导入可选依赖 ──────────────────────────────────────────────

_LIBROSA_OK: Optional[bool] = None


def _has_librosa() -> bool:
    global _LIBROSA_OK
    if _LIBROSA_OK is None:
        try:
            import librosa  # noqa: F401
            _LIBROSA_OK = True
        except ImportError:
            _LIBROSA_OK = False
    return _LIBROSA_OK


# ── 数据类型 ──────────────────────────────────────────────────────

@dataclass
class BPMResult:
    """多方法融合的 BPM 检测结果。"""
    bpm: float = 120.0
    confidence: float = 0.0
    method: str = "unknown"
    candidates: List[float] = field(default_factory=list)
    beat_times: List[float] = field(default_factory=list)
    downbeat_times: List[float] = field(default_factory=list)
    meter: Tuple[int, int] = (4, 4)  # (beats_per_bar, beat_unit)


# ── 方法1: librosa 频谱 onset → 自相关 BPM ────────────────────────

def _bpm_librosa_onset(y: np.ndarray, sr: int) -> Optional[Tuple[float, float]]:
    """基于 librosa onset_strength + autocorrelation 的 BPM 检测。

    适用: 节拍清晰的音乐（EDM/pop/rock）
    """
    if not _has_librosa():
        return None
    import librosa

    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
        bpm = float(tempo[0] if hasattr(tempo, "__getitem__") else tempo)

        # 置信度: 自相关峰值与平均值的比率
        ac = librosa.autocorrelate(onset_env, max_size=int(sr))
        ac_peak = float(np.max(ac[len(ac) // 4 : -1]))
        ac_mean = float(np.mean(np.abs(ac)))
        confidence = min(1.0, max(0.0, (ac_peak / max(ac_mean, 1e-6) - 1.0) / 3.0))

        return (bpm, confidence)
    except Exception:
        logger.debug("enhanced_bpm: librosa onset BPM detection failed")
        return None


# ── 方法2: aubio 风格 — 实时 onset 间隔 BPM ──────────────────────

def _bpm_onset_interval(y: np.ndarray, sr: int, hop_size: int = 512) -> Optional[Tuple[float, float]]:
    """基于 onset 间隔统计的 BPM 检测（aubio 算法精髓）。

    适用: 实时音频流、打击乐为主的音乐
    """
    if not _has_librosa():
        return None
    import librosa

    try:
        onset_frames = librosa.onset.onset_detect(
            y=y, sr=sr, hop_length=hop_size,
            backtrack=True, units="frames",
        )

        if len(onset_frames) < 3:
            return None

        # 帧 → 时间
        onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop_size)

        # onset 间隔 → 直方图 → BPM
        intervals_s = np.diff(onset_times)
        # 过滤: 0.2s (300 BPM) ~ 2.0s (30 BPM)
        mask = (intervals_s > 0.2) & (intervals_s < 2.0)
        valid = intervals_s[mask]

        if len(valid) < 2:
            return None

        # 取中位数间隔 → BPM
        median_interval = float(np.median(valid))
        bpm = 60.0 / median_interval

        # 置信度：间隔一致性（低方差=高一致性）
        cv = float(np.std(valid) / max(np.mean(valid), 1e-6))
        confidence = min(1.0, max(0.0, 1.0 - cv))

        return (bpm, confidence)
    except Exception:
        logger.debug("enhanced_bpm: onset interval BPM detection failed")
        return None


# ── 方法3: 梳状滤波器共振 BPM ────────────────────────────────────

def _bpm_resonator(y: np.ndarray, sr: int, bpm_min: float = 60.0, bpm_max: float = 200.0) -> Optional[Tuple[float, float]]:
    """梳状滤波器 BPM 检测 — 用能量包络与候选速度做共振匹配。

    适用: 复杂混音、多节奏层叠
    来源: chordscope 多方法共识 + BeatNetLite 梳状滤波
    """
    if not _has_librosa():
        return None
    import librosa

    try:
        # 能量包络
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        rms = rms / max(np.max(rms), 1e-6)

        # 候选 BPM 列表
        bpms = np.arange(bpm_min, bpm_max + 0.5, 0.5)
        scores = np.zeros_like(bpms)

        # 对每个候选 BPM 计算梳状滤波器响应
        for i, bpm in enumerate(bpms):
            period_frames = int(sr * 60.0 / bpm / 512)
            if period_frames < 2 or period_frames >= len(rms):
                continue
            # 二倍/半速共振检测
            scores[i] = _comb_score(rms, period_frames)

        order = np.argsort(scores)[::-1]
        best_idx = int(order[0])
        best_bpm = float(bpms[best_idx])
        return (best_bpm, _peak_prominence(scores, bpms, best_idx))
    except Exception:
        logger.debug("enhanced_bpm: comb resonator BPM detection failed")
        return None


def _peak_prominence(scores: np.ndarray, bpms: np.ndarray, best_idx: int) -> float:
    """置信度 = 主峰相对次高峰（间隔 ≥8BPM）的突出度；无次峰给 0.6 基础分。

    ★ R178 修：旧 confidence=best/max(scores) 恒 1（best 就是 max），污染中位
    融合——改为真突出度。"""
    best = max(float(scores[best_idx]), 1e-6)
    second = 0.0
    for j in np.argsort(scores)[::-1][1:]:
        if abs(bpms[j] - bpms[best_idx]) >= 8.0 and scores[j] > second:
            second = float(scores[j])
    if second <= 0.0:
        return 0.6
    return float(np.clip(1.0 - second / best, 0.0, 1.0))


def _comb_score(rms: np.ndarray, period: int) -> float:
    """单候选 BPM 的得分 = 能量包络在 lag=period 的**归一化自相关**。

    ★ R178 修：旧"梳状采样"得分 ∝ mean·(n/period)²——对稳态包络随周期单调
    递减，恒选区间内最高 BPM（实测 199/200 常量伪影）。自相关对相位不敏感、
    在真实节拍周期处出峰，值域 [-1,1] 可跨候选比较。
    """
    if period < 2 or len(rms) <= period + 2:
        return 0.0
    x = rms - float(rms.mean())
    a = x[:-period]
    b = x[period:]
    denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    if denom <= 1e-9:
        return 0.0
    return float(np.dot(a, b) / denom)


# ── 多方法融合 ────────────────────────────────────────────────────

def _try_neural(y: np.ndarray, sr: int) -> Optional["BPMResult"]:
    """beat_this（SOTA）优先直通；不可用/失败 None → 回落 DSP 组合。"""
    try:
        from .neural_beats import analyze_beats
        neural = analyze_beats(np.asarray(y, dtype=np.float32), int(sr))
    except Exception:
        neural = None
    if neural is None:
        return None
    return BPMResult(
        bpm=float(neural["bpm"]),
        confidence=float(neural["confidence"]),
        method="beat_this",
        candidates=[float(neural["bpm"])],
        beat_times=list(neural["beat_times"]),
        downbeat_times=list(neural["downbeat_times"]),
        meter=tuple(neural.get("meter", (4, 4))),
    )


def _beats_and_downbeats(y: np.ndarray, sr: int, consensus_bpm: float):
    """DSP 回落路径的节拍/强拍检测（librosa beat_track + 拍号×相位择优）。"""
    beat_times: List[float] = []
    downbeat_times: List[float] = []
    meter = (4, 4)
    if not _has_librosa():
        return beat_times, downbeat_times, meter
    import librosa
    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset_env, sr=sr, bpm=consensus_bpm)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        beat_times, downbeat_times, meter = _pick_meter_phase(
            onset_env, sr, beat_times)
    except Exception:
        logger.debug("enhanced_bpm: failed to detect beat/downbeat times")
    return beat_times, downbeat_times, meter


def _pick_meter_phase(onset_env, sr: int, beat_times: List[float]):
    """强拍 = 拍号 {4,3} × 相位 的 onset 强度和择优（R178 修：旧 ::4 硬编码）。"""
    import librosa
    downbeat_times: List[float] = []
    meter = (4, 4)
    fps = float(librosa.time_to_frames(1.0, sr=sr))
    env = np.asarray(onset_env, dtype=float)
    best = (0.0, 4, 0)
    for bpb in (4, 3):
        for ph in range(bpb):
            idx = [int(round(t * fps)) for t in beat_times[ph::bpb]]
            idx = [i for i in idx if 0 <= i < len(env)]
            if len(idx) < 2:
                continue
            score = float(env[idx].mean())
            if score > best[0]:
                best = (score, bpb, ph)
    if best[1]:
        meter = (best[1], 4)
        downbeat_times = beat_times[best[2]::best[1]]
    return beat_times, downbeat_times, meter


def detect_bpm_multi(
    y: np.ndarray,
    sr: int = 44100,
    *,
    methods: str = "all",
) -> BPMResult:
    """多方法 BPM 检测共识 — 3 种算法投票 + 加权融合。

    Args:
        y: 音频样本 (float32, shape=(n,))
        sr: 采样率
        methods: "all" | "onset" | "interval" | "resonator"

    Returns:
        BPMResult with consensus BPM, confidence, and per-method candidates
    """
    neural = _try_neural(y, sr)
    if neural is not None:
        return neural

    results: List[Tuple[float, float, str]] = []
    methods_to_run = {
        "onset": methods in ("all", "onset"),
        "interval": methods in ("all", "interval"),
        "resonator": methods in ("all", "resonator"),
    }

    if methods_to_run["onset"]:
        r = _bpm_librosa_onset(y, sr)
        if r:
            results.append((r[0], r[1], "librosa_onset"))

    if methods_to_run["interval"]:
        r = _bpm_onset_interval(y, sr)
        if r:
            results.append((r[0], r[1], "onset_interval"))

    if methods_to_run["resonator"]:
        r = _bpm_resonator(y, sr)
        if r:
            results.append((r[0], r[1], "comb_resonator"))

    if not results:
        return BPMResult(bpm=120.0, confidence=0.0, method="fallback")

    # ── 共识融合：加权平均（置信度作为权重）──
    bpms = np.array([r[0] for r in results])
    confs = np.array([r[1] for r in results])
    candidates = bpms.tolist()

    # 过滤离群值（偏差 > 15% 中位数的剔除）
    median_bpm = float(np.median(bpms))
    mask = np.abs(bpms - median_bpm) / max(median_bpm, 1e-6) < 0.15
    if mask.sum() >= 1:
        bpms = bpms[mask]
        confs = confs[mask]

    # ★ R178 修：八度折叠到 [70, 180]（喷泉曲库先验；×2/÷2 是最常见的单错）
    def _fold(b: float) -> float:
        while b < 70.0:
            b *= 2.0
        while b > 180.0:
            b *= 0.5
        return b

    bpms = np.array([_fold(float(b)) for b in bpms])
    candidates = bpms.tolist()

    weights = confs / max(confs.sum(), 1e-6)
    consensus_bpm = _fold(float(np.average(bpms, weights=weights)))
    confidence = float(np.mean(confs))

    # 方法名：取置信度最高者
    best_idx = int(np.argmax(confs))
    method_name = results[best_idx][2] if best_idx < len(results) else "consensus"

    beat_times, downbeat_times, meter = _beats_and_downbeats(
        y, sr, consensus_bpm)
    return BPMResult(
        bpm=consensus_bpm,
        confidence=confidence,
        method=method_name,
        candidates=candidates,
        beat_times=beat_times,
        downbeat_times=downbeat_times,
        meter=meter,
    )


# ── 快速单方法 BPM（零拷贝、实时兼容）───────────────────────────

def detect_bpm_fast(y: np.ndarray, sr: int) -> float:
    """快速单次 BPM 检测 — 40ms 以内，适合实时 DMX tick。

    使用 librosa onset + 自相关，单方法不融合。
    """
    r = _bpm_librosa_onset(y, sr)
    if r:
        return r[0]
    r = _bpm_onset_interval(y, sr)
    if r:
        return r[0]
    return 120.0  # 默认值
