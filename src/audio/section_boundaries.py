# -*- coding: utf-8 -*-
"""离线段落边界检测（R178：ruptures binseg on 逐拍特征矩阵）。

背景（实测）：实时链状态机（music_structure_analyzer v26）在合成基准
hit≈0.29/伪事件高——它逐帧瞬时分类，天然看不见"编排级"结构变化。本模块在
beat_this 节拍网格上构建逐拍特征矩阵（rms+5 频带），用 ruptures Binseg(rbf)
找**编排跳变点**，实测合成基准 hit 13/16、spurious 7（探针
`_offline_render_work/r178c/cp_probe.py`）。

用途：音频调制轨道的段落预分析、离线烘焙。**不进实时播放热路径**
（beat_this 推理秒级 + binseg 全曲）。

依赖：ruptures（pip，纯 Python）、beat_this/torch（可选——不可用时整体 None）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_MIN_GAP_S = 8.0        # 相邻边界最小间隔
_BPB = 4                # 每小节拍数（网格来自 beat_this，标准 4/4 拍格）
_HOP = 256
_BANDS = ((20, 120), (120, 400), (400, 1600), (1600, 5000), (5000, 12000))


def _beat_feature_matrix(y: np.ndarray, sr: int, beats: List[float]) -> np.ndarray:
    """逐拍特征行：[rms, 5 频带能量]（拍点处采样），列标准化。"""
    import librosa
    rms = librosa.feature.rms(y=y, hop_length=_HOP)[0]
    S = librosa.stft(y, hop_length=_HOP)
    mag = np.abs(S)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=(mag.shape[0] - 1) * 2)
    tfr = librosa.frames_to_time(np.arange(mag.shape[1]), sr=sr, hop_length=_HOP)
    band_means = [float(mag[(freqs >= lo) & (freqs < hi)].mean()) for lo, hi in _BANDS]
    rows = []
    for t in beats:
        i = int(np.clip(np.searchsorted(tfr, t), 0, len(tfr) - 1))
        row = [float(rms[i])] + [float(mag[(freqs >= lo) & (freqs < hi), i].mean())
                                 for lo, hi in _BANDS]
        rows.append(row)
    X = np.array(rows, dtype=float)
    # 列标准化（带宽量纲差异大；按频带全局均值归一，保形状不保绝对能量）
    scale = np.array([band_means[0]] + band_means, dtype=float)
    X = X / (scale[np.newaxis, :] + 1e-9)
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-9)
    return X


def detect_section_boundaries(y: np.ndarray, sr: int,
                              beat_times: List[float]) -> Optional[Dict]:
    """beat 网格 + 波形 → {boundaries: [t...], confidence}；依赖缺失/太短给 None。

    约束：binseg 的段数 = 4 小节窗数-1（结构粒度先验）；相邻边界 ≥_MIN_GAP_S。
    """
    if len(beat_times) < 32:
        return None
    try:
        import ruptures as rpt
    except ImportError:
        return None
    try:
        X = _beat_feature_matrix(y, sr, beat_times)
        step = max(4, _BPB * 2)          # 半小节评估粒度
        algo = rpt.Binseg(model="rbf", jump=step).fit(X)
        bars = len(beat_times) // (_BPB * 4)
        k = max(1, bars - 1)
        bkps = algo.predict(n_bkps=k)
        out = sorted(beat_times[b - 1] for b in bkps[:-1] if 0 < b - 1 < len(beat_times))
        filt: List[float] = []
        for t in out:
            if not filt or t - filt[-1] >= _MIN_GAP_S:
                filt.append(float(t))
        return {"boundaries": filt, "confidence": 0.8}
    except Exception:
        logger.warning("detect_section_boundaries failed", exc_info=True)
        return None
