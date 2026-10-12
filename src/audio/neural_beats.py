# -*- coding: utf-8 -*-
"""beat_this 封装（ISMIR 2024 SOTA 节拍/强拍联合跟踪，CPJKU/beat_this，TCN+torch）。

R178 引入背景：本项目 DSP 组合（librosa 三方法融合）实测在真实曲库全项 <90%
前提线；beat_this（论文 arXiv:2407.21658）在 16 数据集上 SOTA 且**不需要
madmom/DBN 后处理**（dbn=False）。

- 模型 checkpoint **随仓库分发**：`src/audio/models/beat_this-final0.ckpt`
  （77MB，final0）；缺失/无 torch 时返回 None，调用方回落 DSP 组合。
- 进程内单例（加载 ~秒级）；线程不安全 → 加锁串行推理。
- 推理 2~5s/分钟级音频：仅供**离线预分析**（音频调制轨道/烘焙），不在实时
  播放热路径调用。
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MODEL = None
_LOAD_FAILED = False

# 随仓库分发的 checkpoint（相对本文件）；File2Beats 直接吃路径
_CKPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "models", "beat_this-final0.ckpt")


def neural_beats_available() -> bool:
    """torch + beat_this + 本地 checkpoint 三者齐备才可用（缓存失败不反复试探）。"""
    global _LOAD_FAILED
    if _LOAD_FAILED:
        return False
    try:
        import torch  # noqa: F401
        from beat_this.inference import File2Beats  # noqa: F401
        return os.path.isfile(_CKPT)
    except Exception:
        _LOAD_FAILED = True
        return False


def _get_model():
    global _MODEL
    if _MODEL is None:
        from beat_this.inference import File2Beats
        _MODEL = File2Beats(checkpoint_path=_CKPT, device="cpu", dbn=False)
    return _MODEL


def _fold_bpm(bpm: float, lo: float = 70.0, hi: float = 180.0) -> float:
    """倍频折叠到 [lo,hi]（八度歧义消解；仅界外时动作）。"""
    while bpm < lo:
        bpm *= 2.0
    while bpm > hi:
        bpm *= 0.5
    return bpm


def _meter_of(beats: np.ndarray, downbeats: np.ndarray) -> int:
    """拍号 = 相邻强拍间隔拍数中位（夹 [2,7]；强拍不足 2 个回 4）。"""
    if len(downbeats) < 2:
        return 4
    idx = np.searchsorted(beats, downbeats)
    idx = idx[(idx > 0) & (idx < len(beats))]
    if len(idx) < 2:
        return 4
    gaps = np.diff(idx)
    return int(np.clip(round(float(np.median(gaps))), 2, 7))


def _postprocess(beats: np.ndarray, downbeats: np.ndarray) -> Optional[dict]:
    """模型输出 → BPM（间隔中位+折叠）/拍号（`_meter_of`）。"""
    ivs = np.diff(beats)
    ivs = ivs[(ivs > 0.15) & (ivs < 2.5)]
    if not len(ivs):
        return None
    return {
        "bpm": _fold_bpm(float(60.0 / float(np.median(ivs)))),
        "confidence": 0.95,
        "beat_times": beats.tolist(),
        "downbeat_times": downbeats.tolist(),
        "meter": (_meter_of(beats, downbeats), 4),
    }


def analyze_beats(y: np.ndarray, sr: int) -> Optional[dict]:
    """整段音频 → {bpm, beat_times, downbeat_times, meter, confidence}。

    失败/不可用返回 None（调用方回落 DSP 组合）。beat_this 吃文件路径——
    样本落临时 wav。数值后处理在 `_postprocess`。
    """
    if not neural_beats_available():
        return None
    import soundfile as sf

    with _LOCK:
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
            sf.write(path, np.asarray(y, dtype=np.float32), sr)
            beats, downbeats = _get_model()(path)
            beats = np.asarray(beats, dtype=float).ravel()
            downbeats = np.asarray(downbeats, dtype=float).ravel()
            if len(beats) < 4:
                return None
            return _postprocess(beats, downbeats)
        except Exception:
            logger.warning("neural_beats.analyze_beats failed", exc_info=True)
            return None
        finally:
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
