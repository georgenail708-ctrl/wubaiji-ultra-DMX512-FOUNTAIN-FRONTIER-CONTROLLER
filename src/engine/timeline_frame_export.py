"""完整时间线帧按帧生成 JSON，避免磁盘缓存导出时再次物化全段数据。

直接复用 CPython JSONEncoder.iterencode（PSF，默认非 one-shot）：
https://github.com/python/cpython/blob/3.13/Lib/json/encoder.py#L188-L241
https://github.com/python/cpython/blob/3.13/Lib/json/encoder.py#L311-L384
字典 items 适配只生成当前帧；所有 JSON 编码语法由标准库处理。
"""
from __future__ import annotations

import base64
import json
import logging
import os

import numpy as np

log = logging.getLogger(__name__)


def _encode_entry(data, active) -> dict:
    """原导出格式的 512 字节截补与 512 位掩码，不改变 dtype/位次。"""
    data_arr = np.asarray(data, dtype=np.uint8).reshape(-1)
    data512 = np.zeros(512, dtype=np.uint8)
    n = min(data_arr.size, 512)
    if n > 0:
        data512[:n] = data_arr[:n]
    active_arr = np.asarray(active, dtype=bool).reshape(-1)
    mask = 0
    for ch in range(min(active_arr.size, 512)):
        if active_arr[ch]:
            mask |= 1 << ch
    return {"data": base64.b64encode(data512.tobytes()).decode("ascii"), "active": mask}


def _encode_frame(frame) -> dict:
    """保持各域原插入顺序及 int→str 地址规范。"""
    return {str(int(universe)): _encode_entry(*entry) for universe, entry in frame.items()}


class _EncodedFrames(dict):
    """供 stdlib 非 one-shot dict.items 遍历的惰性帧映射。

    底层 dict 不保留转换后的帧；不得把此对象传给 dumps/encode 的 one-shot 路径。
    """

    def __init__(self, cache):
        super().__init__()
        self._cache = cache
        self.max_universes = 0

    def __len__(self):
        return len(self._cache)

    def items(self):
        for idx in sorted(self._cache.keys()):
            frame = self._cache[idx] or {}
            self.max_universes = max(self.max_universes, len(frame))
            yield str(idx), _encode_frame(frame)


def _write_atomic_json(payload: dict, path) -> None:
    """完整关闭临时文件才替换目标；读取/编码/写入失败不得破坏旧文件。"""
    tmp_path = str(path) + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
            for chunk in encoder.iterencode(payload):
                fh.write(chunk)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass  # 清理失败不能遮蔽原始编码/IO异常，目标仍未替换。
        raise


def export_timeline_frames(engine, path) -> int:
    """导出既有 schema，内容仅保留当前帧，帧索引按原顺序排序。"""
    cache = getattr(engine, "_timeline_render_frame_cache", {}) or {}
    if not cache:
        raise ValueError("尚未渲染时间线缓存:请先锁定渲染缓存并完成逐帧渲染,再导出帧数据")
    interval = max(0.001, float(getattr(engine, "_timeline_render_frame_cache_interval", 0.023) or 0.023))
    end = max(0.0, float(getattr(engine, "_timeline_render_frame_cache_end", 0.0) or 0.0))
    frames = _EncodedFrames(cache)
    frame_count = len(cache)
    payload = {
        "format": "qascade-timeline-frames", "version": 1, "interval": interval,
        "end": end, "frame_count": frame_count, "frames": frames,
    }
    _write_atomic_json(payload, path)
    raw_bytes = frame_count * frames.max_universes * 512
    log.info("导出帧数据: %d 帧 × %d 域 × 512B ≈ %.1f MB(JSON+base64 约 %.1f MB)",
             frame_count, frames.max_universes, raw_bytes / 1048576.0, raw_bytes * 1.4 / 1048576.0)
    return frame_count
