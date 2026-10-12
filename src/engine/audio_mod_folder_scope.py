# -*- coding: utf-8 -*-
"""音频调制文件夹 scope 语义（M1 自 audio_modulation.py 拆出；纯逻辑无 Qt）。

拆分原因：`audio_modulation.py` 为基线后新文件受 500 行门禁约束，M1 增补
拖动纯函数后超限；本块（文件夹唯一主控 + 范围通道自动识别）自成一体，
函数逐位原样搬移。`audio_modulation` 底部兼容再导出两个公开入口，旧
import 路径（audio_mod_scope / main_window / audio_mod_track / 测试）不变。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .audio_modulation import _to_int, normalize_state


def enforce_single_owner(state: dict, folder_id: str) -> Optional[dict]:
    """文件夹唯一主控约束：把 state 的 scope 绑到 folder_id。

    语义：一个文件夹范围内只能存在一条调制轨（"额外的主控"）。调制 state 是
    单例（Project.audio_modulation_state），scope_folder_id 即唯一归属——
    换文件夹（勾选别的文件夹）= 整条调制轨迁到新范围（clips 保留，时间轴不
    变；如需清空由调用方决定）。folder_id 为空 → 不生效（返回原 state）。
    """
    s = normalize_state(state)
    if not folder_id:
        return s
    s["scope_folder_id"] = str(folder_id)
    return s


def collect_scope_channel_types(tracks: List[dict], folder_id: str,
                                addr_types: Optional[Dict[tuple, List[str]]] = None
                                ) -> List[str]:
    """文件夹 scope 内全部普通轨 clip 的通道类型并集（自动识别调制对象）。

    `tracks` = Project.tracks 的 dict 列表（to_dict 口径）。规则（用户规格）：
    画了调制 clip 的文件夹，其范围内（该文件夹 + 全部后代普通轨）所有录制
    clip 的通道类型都是识别与调制对象。有序去重保持稳定输出。

    R181：`addr_types` = {(universe, addr0): [通道类型]} 地址反查表。真实工程
    的 clip 帧 blob 未水合、也没有 ch_types 字段，此时按 clip 的
    active_channels_by_universe 地址回退识别（见 `_used_channel_types`）。
    """
    wanted = _folder_subtree_track_ids(tracks, folder_id)
    audible_ids = _audible_track_ids(tracks)
    seen: List[str] = []
    for tr in tracks:
        if not _is_scope_target(tr, wanted, audible_ids):
            continue
        for clip in (tr.get("clips") or []):
            for ct in _clip_channel_types(clip, addr_types):
                if ct and ct not in seen:
                    seen.append(ct)
    return seen


def _is_scope_target(track: dict, wanted: set, audible_ids: set) -> bool:
    """普通轨 + 在 scope 子树内 + 可出声。"""
    tid = str(track.get("id", "") or "")
    return (tid in wanted and tid in audible_ids
            and not bool(track.get("is_folder", False)))


def _folder_subtree_track_ids(tracks: List[dict], folder_id: str) -> set:
    """folder_id 及其全部后代轨 id 集（parent_id 链；空 folder_id = 空集）。"""
    if not folder_id:
        return set()
    wanted = {str(folder_id)}
    changed = True
    while changed:
        changed = False
        for tr in tracks:
            tid = str(tr.get("id", "") or "")
            parent = str(tr.get("parent_id", "") or "")
            if parent in wanted and tid not in wanted:
                wanted.add(tid)
                changed = True
    return wanted


def _chain_flag(by_id: dict, track: dict, key: str) -> bool:
    """self-or-ancestor 链上 key 标志是否置位（timeline 权威口径）。"""
    cur = track
    seen = set()
    while True:
        if bool(cur.get(key, False)):
            return True
        tid = str(cur.get("id", "") or "")
        if tid in seen:
            return False
        seen.add(tid)
        parent = str(cur.get("parent_id", "") or "")
        if not parent or parent not in by_id:
            return False
        cur = by_id[parent]


def _audible_track_ids(tracks: List[dict]) -> set:
    """可出声普通轨 id 集——**与 timeline_controller.render_mix 权威口径一致**：

    muted(self-or-ancestor) 排除；存在任一 solo(self-or-ancestor，含文件夹) 时
    只保留 soloed(self-or-ancestor) 的轨。R178 审计 P1-3 修正：旧实现 solo
    语义写反（AND 全链）且跳过文件夹节点，与播放实听不符。
    """
    normal = [t for t in tracks if not bool(t.get("is_folder", False))]
    by_id = {str(t.get("id", "") or ""): t for t in tracks if t.get("id")}
    any_solo = any(_chain_flag(by_id, t, "solo") for t in tracks)
    out = set()
    for t in normal:
        if _chain_flag(by_id, t, "muted"):
            continue
        if any_solo and not _chain_flag(by_id, t, "solo"):
            continue
        out.add(str(t.get("id", "") or ""))
    return out


def _clip_channel_types(clip: dict, addr_types=None) -> List[str]:
    """clip 通道类型来源：显式 ch_types → frames 首帧键 → ACU 地址反查（R181）。"""
    cts = clip.get("ch_types") if isinstance(clip, dict) else None
    if isinstance(cts, list) and cts:
        return [str(c) for c in cts if str(c)]
    frames = clip.get("frames") if isinstance(clip, dict) else None
    if isinstance(frames, list) and frames:
        first = frames[0]
        if isinstance(first, dict):
            return sorted(str(k) for k in first.keys())
    return _used_channel_types(clip, addr_types)


def _used_channel_types(clip, addr_types) -> List[str]:
    """帧未水合时的回退：clip 的 active_channels_by_universe 地址反查通道类型。"""
    if not addr_types or not isinstance(clip, dict):
        return []
    usage = clip.get("active_channels_by_universe")
    if not isinstance(usage, dict):
        return []
    out: List[str] = []
    for uni, chans in usage.items():
        for ch in (chans if isinstance(chans, (list, tuple)) else []):
            for ct in addr_types.get((_to_int(uni, -1), _to_int(ch, 0) - 1), ()):
                if ct not in out:
                    out.append(ct)
    return out
