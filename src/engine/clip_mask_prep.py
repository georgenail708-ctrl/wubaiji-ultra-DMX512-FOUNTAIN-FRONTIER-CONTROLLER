# -*- coding: utf-8 -*-
"""掩码帧准备缓存（R56c；R178 从 PlaybackEngine._clip_mask_frames_cached 搬出）。

``_clip_current_channel_active_mask`` 需要某 clip 在某域的「按时间排序的帧序列」
来判定零值通道是否在本帧真实变化。过滤+排序+数据矩阵与 source_t 无关，一次构建
后按 searchsorted 取帧。R178 起直播路径也读本缓存（原逐次计算每调用全量过滤+
排序+线性扫全部帧，TTM 实测占纯 Python 单帧 83%），直播与离线同一取帧语义。

缓存纪律（id 键 + 值存强引用 + 命中 ``is`` 复核）：
  * 键 ``(id(clip), universe)``；命中需 clip / 帧列表对象同一、长度一致、首末帧
    对象同一（录制追加帧改长度 → 重建）。
  * 另持构建时的 ``engine._clip_static_signature(clip)`` 结果对象并 ``is`` 复核：
    invalidate_clip_cache 弹出该 clip 签名、invalidate_all_caches 清空签名表 →
    签名对象换新 → 必重建。平滑等原地改帧必经这两条失效（编译缓存同源），
    新鲜度与渲染数据本身一致。
  * 「该域 ≤1 帧」的否定结果同样缓存（纯函数，同一复核条件）。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

_CAPACITY = 4096
_ZERO_ROW = np.zeros(512, dtype=np.uint8)
_ZERO_ROW.setflags(write=False)


def _frame_row(raw) -> np.ndarray:
    """帧数据 → u8[512]（None 按全零；与原逐次计算 _frame_data 同义）。"""
    if raw is None:
        return _ZERO_ROW
    arr = np.asarray(raw, dtype=np.uint8)
    if arr.shape == (512,):
        return arr
    fixed = np.zeros(512, dtype=np.uint8)
    m = min(arr.size, 512)
    if m > 0:
        fixed[:m] = arr.reshape(-1)[:m]
    return fixed


def build_mask_frames(frames_raw, universe: int) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """过滤本域帧 + 稳定排序 → (ts f64[n], data u8[n,512])；本域 ≤1 帧返回 None。

    过滤/排序键与原逐次计算逐字节一致：缺省或假值 universe 视为本域；
    时间戳假值按 0.0。
    """
    uni = int(universe)
    frames = [
        f for f in frames_raw
        if int(getattr(f, "universe", uni) or uni) == uni
    ]
    if len(frames) <= 1:
        return None
    frames.sort(key=lambda f: float(getattr(f, "timestamp", 0.0) or 0.0))
    ts_arr = np.array(
        [float(getattr(f, "timestamp", 0.0) or 0.0) for f in frames],
        dtype=np.float64)
    data_arr = np.stack([_frame_row(getattr(f, "data", None)) for f in frames])
    return ts_arr, data_arr


def mask_frames_cached(engine, clip, universe: int):
    """带复核的缓存读取；返回 (ts_arr, data_arr) 或 None（不可用/本域 ≤1 帧）。"""
    frames_raw = getattr(clip, "frames", None) or []
    if len(frames_raw) <= 1:
        return None
    cache = getattr(engine, "_clip_mask_prep_cache", None)
    if cache is None:
        cache = {}
        engine._clip_mask_prep_cache = cache
    sig = engine._clip_static_signature(clip)
    key = (id(clip), int(universe))
    hit = cache.get(key)
    if (
        hit is not None
        and hit[1] is clip
        and hit[2] is frames_raw
        and hit[3] == len(frames_raw)
        and frames_raw[0] is hit[4]
        and frames_raw[-1] is hit[5]
        and hit[7] is sig
    ):
        return hit[6]
    built = build_mask_frames(frames_raw, int(universe))
    while len(cache) >= _CAPACITY:
        engine._evict_oldest(cache)
    cache[key] = (key, clip, frames_raw, len(frames_raw),
                  frames_raw[0], frames_raw[-1], built, sig)
    return built
