# -*- coding: utf-8 -*-
"""工程体积优化：clip 帧数据的**紧凑二进制编解码**（R140）。

用户实报（2026-09-21）::

    "一个项目节目做多了之后…clip 也比较多了，它的保存文件会特别大…我需要你优化
     项目保存形式，让我的软件保存的项目轻量化。"

## 实测（真工程 `E:\\水仙十字.dmxt`，213.6 MB）

| 项 | 现状 | 压缩后 | 手段 |
|----|------|--------|------|
| clip 帧数据（54 个 clip） | **33.8 MB** | **1.2 MB** | 二进制打包 + zlib |

DMX 帧的**重复度极高**（同一设备角度在一个动作里往往连续几十帧不变），而原格式是
**逐帧 base64**：

    "frames": [{"universe":1,"timestamp":0.0,"data":"<512B 的 base64>"}, ...]

即每帧：`base64` 膨胀 33% + JSON 键名重复 + 数字文本化。zlib 压缩率实测 **1.8%~7.8%**。

## 设计（向后兼容是第一约束）

新格式把整个 clip 的帧打包成**一个** base64 blob：

    "frames_blob": {
      "v": 1,
      "n": <帧数>,
      "dt": <时间戳步长，恒定则只存一个数>,
      "ts": [<时间戳>, ...]        # 仅当步长不恒定时出现
      "unis": [1, 2],              # 出现过的域（有序）
      "m": [<每帧的域下标>, ...],   # 帧 → 域
      "b": "<所有帧体拼接后 zlib+base64>"
    }

帧体 = 每帧 512 字节**顺序拼接**（不做差分：zlib 已能吃掉跨帧冗余，差分反而增加复杂度
且对"整段跳动"型数据收益低）。

★ 兼容策略（三条同时满足）：
  1. **读**：同时认 `frames`（旧逐帧数组）与 `frames_blob`（新）—— 旧工程无需迁移即可打开；
  2. **写**：默认写 `frames_blob`；`Project` 加 `compact_frames` 开关，关闭时退回旧格式
     （供调试/需要人读存档时用）；
  3. **逐帧 `to_dict()`**：`DMXFrame.to_dict` / `_LazyDMXFrame.to_dict` **保持不变**
     （别处仍在用），只把"clip 整段帧"的落盘换成 blob。

## 为什么不用 numpy/msgpack 等外部依赖

纯 `zlib` + `struct` 是标准库，不引入新依赖，读回路径与既有 `_LazyDMXFrame`
（大工程懒加载）天然契合：blob 解出的 512 字节切片可直接构造懒帧。
"""
from __future__ import annotations

import base64
import logging
import struct
import zlib
from typing import List, Optional, Tuple

_log = logging.getLogger(__name__)

#: blob 版本（将来改格式时据此分派，旧版仍可读）
BLOB_VERSION = 1

#: 单帧字节数（DMX 域宽）
FRAME_BYTES = 512

#: zlib 压缩级别（6 = 默认；再高收益甚微而耗时明显上升）
_ZLEVEL = 6


def pack_frames(frames) -> Optional[dict]:
    """把 `frames`（DMXFrame / _LazyDMXFrame 列表）打包成紧凑 dict；无帧返回 None。

    任何异常都返回 None（调用方退回逐帧格式）—— 保存路径**绝不能**因为优化而失败。
    """
    if not frames:
        return None
    try:
        ts: List[float] = []
        unis: List[int] = []
        uni_index: dict = {}
        uni_map: List[int] = []
        body = bytearray()
        collected = _collect_frames(frames)
        if collected is None:
            return None
        ts, unis, uni_map, body = collected
        out = _blob_dict(ts, unis, uni_map, bytes(body))
        dt = _uniform_step(ts)
        if dt is None:
            out["ts"] = ts
        else:
            out["dt"] = dt
            out["t0"] = ts[0]
        return out
    except Exception:
        return None


def load_frames_into(clip, blob: dict, lazy: bool) -> int:
    """把紧凑帧 blob 解进 `clip.frames`，返回成功构造的帧数（★ R140）。

    一次解压整段（zlib 要吃跨帧冗余），再按 512 字节切片构造帧：
      · `lazy=True` → `_LazyDMXFrame`（只存原始字节，`data` 首次访问才切数组，
        与旧懒加载语义一致）；
      · `lazy=False` → `DMXFrame`（切出连续数组）。
    单帧异常跳过而非整段失败（与旧路径容错口径一致）。
    ★ 帧类在函数内 import：`models` 会用本模块，避免循环导入。
    """
    try:
        body, meta = unpack_frame_bytes(blob)
    except Exception:  # IO-05：首错 warning+计数（原 debug 静默）
        if not getattr(clip, "_blob_err_logged", False):
            clip._blob_err_logged = True; _log.warning("frames_blob 解码失败", exc_info=True)
        clip._blob_err_count = getattr(clip, "_blob_err_count", 0) + 1
        return 0
    try:
        from . import models as _m; _F = _m._LazyDMXFrame if lazy else _m.DMXFrame
    except Exception:
        return 0
    import numpy as _np
    made = 0
    body_mv = memoryview(body)   # ★ R183：零拷贝切片（逐帧 b64 编码已移除）
    for i, (uni, tstamp) in enumerate(meta):
        chunk = body_mv[i * 512:i * 512 + 512]
        if len(chunk) < 512:
            break
        try:
            if lazy:
                clip.frames.append(_F(universe=int(uni), timestamp=float(tstamp),
                                      raw_bytes=chunk))
            else:
                clip.frames.append(_F(universe=int(uni), timestamp=float(tstamp),
                                      data=_np.frombuffer(chunk, dtype=_np.uint8).copy()))
            made += 1
        except Exception:
            _log.debug("由 blob 构造帧失败，跳过该帧", exc_info=True)
    return made


def _collect_frames(frames):
    """一趟遍历收集 `(ts, unis, uni_map, body)`；任一帧形状异常 → None。"""
    ts, unis, uni_index, uni_map, body = [], [], {}, [], bytearray()
    for f in frames:
        raw = _frame_bytes(f)
        if raw is None:
            return None
        u = int(getattr(f, "universe", 1) or 1)
        if u not in uni_index:
            uni_index[u] = len(unis)
            unis.append(u)
        uni_map.append(uni_index[u])
        ts.append(float(getattr(f, "timestamp", 0.0) or 0.0))
        body += raw
    return ts, unis, uni_map, body


def _blob_dict(ts, unis, uni_map, body: bytes) -> dict:
    """组装 blob 的固定部分（时间戳列之外的一切）。"""
    return {
        "v": BLOB_VERSION,
        "n": len(ts),
        "unis": unis,
        "m": uni_map,
        "b": base64.b64encode(zlib.compress(body, _ZLEVEL)).decode("ascii"),
    }


def _frame_bytes(frame) -> Optional[bytes]:
    """单帧的 512 字节体；形状不对/取不到 → None（调用方退回逐帧格式）。"""
    data = getattr(frame, "data", None)
    if data is None:
        return None
    try:
        raw = bytes(data)
    except Exception:
        return None
    return raw if len(raw) == FRAME_BYTES else None


def _uniform_step(ts: List[float]) -> Optional[float]:
    """时间戳是否等步长（是则返回步长，否则 None）。

    等步长是常态（录制按固定 fps 量化）→ 只存 `t0`+`dt`，省掉整列浮点文本。
    """
    if len(ts) < 2:
        return 0.0 if ts else None
    dt = ts[1] - ts[0]
    if dt <= 0.0:
        return None
    for i in range(2, len(ts)):
        if abs((ts[i] - ts[i - 1]) - dt) > 1e-6:
            return None
    return dt


def unpack_timestamps(blob: dict) -> List[float]:
    """从 blob 还原时间戳列表（等步长或逐列）。"""
    n = int(blob.get("n", 0) or 0)
    if "ts" in blob:
        ts = [float(x) for x in (blob.get("ts") or [])]
        return ts[:n]
    t0 = float(blob.get("t0", 0.0) or 0.0)
    dt = float(blob.get("dt", 0.0) or 0.0)
    return [t0 + dt * i for i in range(n)]


def unpack_frame_bytes(blob: dict) -> Tuple[bytes, List[Tuple[int, float]]]:
    """解出 `(全部帧体拼接, [(universe, timestamp), ...])`。

    拼整体一次解压（而不是逐帧解压）是关键 —— zlib 才能吃掉跨帧冗余。
    """
    n = int(blob.get("n", 0) or 0)
    if n <= 0:
        return b"", []
    body = zlib.decompress(base64.b64decode(blob.get("b", "") or ""))
    unis = [int(u) for u in (blob.get("unis") or [])]
    uni_map = [int(x) for x in (blob.get("m") or [])]
    ts = unpack_timestamps(blob)
    meta: List[Tuple[int, float]] = []
    for i in range(n):
        ui = uni_map[i] if i < len(uni_map) else 0
        u = unis[ui] if 0 <= ui < len(unis) else 1
        meta.append((u, ts[i] if i < len(ts) else 0.0))
    return body, meta


def is_frame_blob(d) -> bool:
    """该 dict 是否是帧 blob（供 `Track.from_dict` 分派）。"""
    return isinstance(d, dict) and "b" in d and "n" in d
