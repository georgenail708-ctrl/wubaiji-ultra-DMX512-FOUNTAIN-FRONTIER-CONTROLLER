# -*- coding: utf-8 -*-
"""音频调制 scope 识别：Project 对象 → 范围内通道类型（R181，纯逻辑无 Qt）。

为什么单独成模块：`audio_modulation.collect_scope_channel_types` 的口径是
**dict 列表**（to_dict/旧编辑链给的数据），但真实工程里 clip 帧是未水合 blob、
业务对象上也没有稳定的 `ch_types` 字段——旧链（main_window._tracks_to_dicts）
在真机上识别恒空（审计 B3 的"双层死链"）。本模块把 Project 对象直接换算成
识别口径：clip 的 `active_channels_by_universe` 地址 → fixtures/fixture_defs
的「地址 → 通道类型」反查表，作为 ch_types 为空时的回退来源。

消费方：调制编辑页（打开时的 scope 通道注入兜底）与调制驱动（显式 targets
全空时的目标回退）。subtree/mute/solo 规则复用 audio_modulation 同函数。
"""
from __future__ import annotations

import weakref
from typing import Dict, List, Tuple

from .audio_mod_folder_scope import collect_scope_channel_types

#: 识别结果缓存（每帧调用安全）：工程弱引用 + 结构指纹 + scope 文件夹 → 通道表
_CACHE: dict = {"ref": None, "folder": None, "fp": None, "types": ()}


def collect_scope_channels_from_project(project, folder_id: str) -> List[str]:
    """Project 对象 → scope 识别通道类型（编辑页与调制驱动共用口径）。

    与 `collect_scope_channel_types` 同规则；额外把 fixtures+fixture_defs 换算
    成「地址 → 通道类型」反查表随带。异常时返回空表（识别只是增强，不阻断）。

    结果按（工程身份 + 结构指纹 + 文件夹）缓存——调制驱动逐帧消费，不能每帧
    重建全工程映射（P2 级性能纪律）。
    """
    if project is None:
        return []
    folder = str(folder_id or "")
    fp = _structure_fingerprint(project)
    ref = _CACHE.get("ref")
    if (ref is not None and ref() is project and _CACHE.get("folder") == folder
            and _CACHE.get("fp") == fp):
        return list(_CACHE["types"])
    types = _collect_uncached(project, folder)
    _CACHE.update(ref=weakref.ref(project), folder=folder, fp=fp,
                  types=tuple(types))
    return types


def invalidate_scope_cache() -> None:
    """缓存显式失效（换工程/调试/测试用；结构指纹变化会自动重建）。"""
    _CACHE.update(ref=None, folder=None, fp=None, types=())


def _collect_uncached(project, folder: str) -> List[str]:
    """实际识别（内部）：异常给空表。"""
    try:
        addr_types = project_addr_types(project)
        dicts = [track_scope_dict(t)
                 for t in (getattr(project, "tracks", []) or [])]
        return collect_scope_channel_types(dicts, folder, addr_types)
    except Exception:   # pragma: no cover - 防御：识别永不阻断编辑/播放
        return []


def _structure_fingerprint(project) -> Tuple:
    """结构指纹：**patch_rev** + 设备数/定义数 + 每轨（id/父级/mute/solo/clip 数）。

    任一变化 → 识别重算；不含帧内容（识别只用 ACU 地址与 ch_types）。
    ★ 清查⑦A P1-6：patch_rev 纳入指纹——改设备类型通道定义 / 配接地址后指纹
      此前不变 → 识别陈旧（照 `_ch_type_addr_map` 的 patch_rev 纪律同口径）。
    """
    return (
        int(getattr(project, "patch_rev", 0) or 0),
        len(getattr(project, "fixtures", []) or []),
        len(getattr(project, "fixture_defs", []) or []),
        tuple((str(getattr(t, "id", "") or ""), str(getattr(t, "parent_id", "") or ""),
               bool(getattr(t, "muted", False)), bool(getattr(t, "solo", False)),
               len(getattr(t, "clips", []) or []))
              for t in (getattr(project, "tracks", []) or [])),
    )


def project_addr_types(project) -> Dict[tuple, List[str]]:
    """fixtures+fixture_defs → {(universe, addr0): [通道类型, ...]}（地址反查表）。"""
    defs = {str(getattr(d, "id", "") or ""): d
            for d in (getattr(project, "fixture_defs", []) or [])}
    out: Dict[tuple, List[str]] = {}
    for fx in (getattr(project, "fixtures", []) or []):
        d = defs.get(str(getattr(fx, "def_id", "") or ""))
        if d is None:
            continue
        _add_fixture_addr_types(out, fx, d)
    return out


def _add_fixture_addr_types(out: Dict[tuple, List[str]], fx, d) -> None:
    """单台设备的「地址 → 通道类型」追加（helper：压复杂度）。"""
    uni = _to_int(getattr(fx, "universe", 1), 1)
    start = _to_int(getattr(fx, "start_channel", 1), 1) - 1
    for i, ch in enumerate(getattr(d, "channels", []) or []):
        ct = str(ch or "").strip()
        if ct:
            out.setdefault((uni, start + i), []).append(ct)


def track_scope_dict(track) -> dict:
    """Track 对象 → 识别口径 dict（含 clip 的 ACU 地址回退数据）。"""
    return {
        "id": str(getattr(track, "id", "") or ""),
        "parent_id": str(getattr(track, "parent_id", "") or ""),
        "is_folder": bool(getattr(track, "is_folder", False)),
        "muted": bool(getattr(track, "muted", False)),
        "solo": bool(getattr(track, "solo", False)),
        "clips": [_clip_scope_dict(c)
                  for c in (getattr(track, "clips", []) or [])],
    }


def _clip_scope_dict(clip) -> dict:
    """clip → 识别口径 dict：ch_types + ACU（frames 不读，真实工程未水合）。"""
    return {
        "ch_types": [str(t) for t in (getattr(clip, "ch_types", []) or []) if str(t)],
        "active_channels_by_universe": _acu_clean(
            getattr(clip, "active_channels_by_universe", None)),
    }


def _acu_clean(raw) -> Dict[int, List[int]]:
    """active_channels_by_universe 归一化：键域号 int、值 1-based 通道 int 列表。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[int, List[int]] = {}
    for uni, chans in raw.items():
        if not isinstance(chans, (list, tuple)):
            continue
        vals = [_to_int(c, 0) for c in chans]
        out[_to_int(uni, -1)] = [v for v in vals if v > 0]
    return out


def _to_int(v, default: int) -> int:
    """宽容 int（坏数据回退 default）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
