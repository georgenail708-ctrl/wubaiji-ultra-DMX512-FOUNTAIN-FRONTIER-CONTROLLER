# -*- coding: utf-8 -*-
"""音频预分析特征缓存（进程内 + 磁盘两级，键 = sha256:版本，R174 #7）。

确定性保证：同文件同版本 → 同特征（键内含 sha256 与 FEATURE_VERSION）。
磁盘缓存目录 = `<仓库或exe目录>/.audio_feature_cache/<键>.json`；损坏条目
静默删除重建。磁盘不可写时退化为进程内缓存。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Dict, Optional

from .audio_features import (
    AudioFeatures,
    analyze_audio,
    cache_key,
    features_from_json,
    features_to_json,
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MEM: Dict[str, AudioFeatures] = {}
_MEM_MAX = 8          # LRU 简化：满则清最旧（按插入序）
_DIR: Optional[str] = None
_DIR_TRIED = False
#: 后台现算中/已失败的 key（R178 审计 P2-8）：防每帧重复起线程；失败进程内
#: 记忆（坏文件重试无意义，且会让 UI 线程每帧 kick 一个空转线程）。
_COMPUTING: set = set()
_FAILED: set = set()
_PATH_KEYS: dict = {}   # 已预热文件的绝对路径 -> (元数据stamp, 内容key)


def _cache_dir() -> Optional[str]:
    """磁盘缓存目录（懒建；不可写返回 None）。测试/隔离场景可用 set_cache_dir 覆盖。"""
    global _DIR, _DIR_TRIED
    if _DIR_TRIED:
        return _DIR
    _DIR_TRIED = True
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    d = os.path.join(root, ".audio_feature_cache")
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        _DIR = d
    except Exception:
        _DIR = None
    return _DIR


def set_cache_dir(d: Optional[str]) -> None:
    """显式指定磁盘缓存目录（测试隔离用；None=禁用磁盘层）。"""
    global _DIR, _DIR_TRIED
    _DIR = d
    _DIR_TRIED = True
    if d:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass


def get_features(path: str, use_neural: bool = True,
                 background_on_miss: bool = False) -> Optional[AudioFeatures]:
    """取音频预分析特征：内存 → 磁盘 → 现算（回填两级）。线程安全。

    `background_on_miss=True`（R178 审计 P2-8，UI 帧 tick 路径用）：两级
    miss 时**不在调用线程现算**（analyze_audio 数秒级，曾把 UI 线程锁在
    `_LOCK` 内）——起 daemon 线程现算回填，本帧返回 None（调用方跳过调制，
    下帧缓存命中）。默认 False 保持原同步语义（预分析管线/测试用）。
    """
    if not os.path.isfile(path):
        return None
    key = cache_key(path)
    with _LOCK:
        _register_path(path, key)
        hit = _mem_get(key)
        if hit is not None:
            return hit
        if key in _FAILED:
            return None
        if not background_on_miss:
            return _compute_and_store_locked(key, path, use_neural)
        if key not in _COMPUTING:
            _kick_background(key, path, use_neural)
        return None    # 现算中：本帧跳过调制，下帧缓存命中


def file_stamp(path: str) -> Optional[tuple]:
    """廉价文件身份，不计算内容hash；hash仍由原准备/预热入口执行。"""
    try:
        st = os.stat(path)
        return st.st_mtime_ns, st.st_size
    except (OSError, TypeError, ValueError):
        return None


def _register_path(path: str, key: str) -> None:
    if len(_PATH_KEYS) >= 32:
        _PATH_KEYS.clear()
    _PATH_KEYS[os.path.abspath(path)] = (file_stamp(path), key)


def peek_features(path: str) -> Optional[AudioFeatures]:
    """Render只读内存，未预热/文件已变/后台锁忙立即None；无hash/磁盘/worker。"""
    entry = _PATH_KEYS.get(os.path.abspath(path)) if path else None
    if entry is None or entry[0] != file_stamp(path):
        return None
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        return _MEM.get(entry[1])
    finally:
        _LOCK.release()


def feature_status(path: str) -> str:
    """Read existing cache lifecycle without hash, analysis, waits or worker creation."""
    if not path:
        return "file_unset"
    stamp = file_stamp(path)
    if stamp is None:
        return "file_missing"
    entry = _PATH_KEYS.get(os.path.abspath(path))
    if entry is None or entry[0] != stamp:
        return "file_unprepared"
    if not _LOCK.acquire(blocking=False):
        return "cache_busy"
    try:
        key = entry[1]
        if key in _FAILED:
            return "analysis_failed"
        if key in _COMPUTING:
            return "analysis_pending"
        return "ready" if key in _MEM else "file_unprepared"
    finally:
        _LOCK.release()


def _compute_and_store_locked(key: str, path: str,
                              use_neural: bool) -> Optional[AudioFeatures]:
    """磁盘 → 现算 → 回填两级（调用方持 `_LOCK`）。失败记入 `_FAILED`。"""
    feat = _disk_get(key) or analyze_audio(path, use_neural=use_neural)
    if feat is None:
        _FAILED.add(key)
        return None
    _mem_put(key, feat)
    _disk_put(key, feat)
    return feat


def _kick_background(key: str, path: str, use_neural: bool) -> None:
    """起 daemon 线程现算回填（持 `_LOCK` 调用；daemon 防进程退出挂住）。"""
    _COMPUTING.add(key)

    def _work():
        try:
            with _LOCK:
                _compute_and_store_locked(key, path, use_neural)
        except Exception:
            logger.debug("audio feature background compute failed: %s", path,
                         exc_info=True)
            with _LOCK:
                _FAILED.add(key)
        finally:
            with _LOCK:
                _COMPUTING.discard(key)

    threading.Thread(target=_work, name="audio-feature-compute",
                     daemon=True).start()


def _mem_get(key: str) -> Optional[AudioFeatures]:
    return _MEM.get(key)


def _disk_get(key: str) -> Optional[AudioFeatures]:
    d = _cache_dir()
    if not d:
        return None
    fp = os.path.join(d, key.replace(":", "_") + ".json")
    if not os.path.isfile(fp):
        return None
    try:
        with open(fp, encoding="utf-8") as f:
            return features_from_json(f.read())
    except Exception:
        logger.debug("audio feature cache read failed: %s", fp, exc_info=True)
        return None


def _disk_put(key: str, feat: AudioFeatures) -> None:
    d = _cache_dir()
    if not d:
        return
    fp = os.path.join(d, key.replace(":", "_") + ".json")
    try:
        with open(fp, "w", encoding="utf-8") as f:
            f.write(features_to_json(feat))
    except Exception:
        logger.debug("audio feature cache write failed: %s", fp, exc_info=True)


def _mem_put(key: str, feat: AudioFeatures) -> None:
    if len(_MEM) >= _MEM_MAX:
        oldest = next(iter(_MEM))
        _MEM.pop(oldest, None)
    _MEM[key] = feat
