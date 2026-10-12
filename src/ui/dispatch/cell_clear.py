# -*- coding: utf-8 -*-
"""调度台"清空/释放"的展开与落层计算（纯函数 + 一次 NM 调用）。

从 `cell_ops` 拆出（架构门禁：单文件 <=500 行）。这里只做三件事：
  ① 把一组格**展开**成 `(fixture_id, cell, universe, addr)`（聚合格展开到组内设备）；
  ② 按域聚合地址并逐层清空（`layer=None` = 清全部**编辑层**）；
  ③ 清行缓存（防 keepalive 重建时把旧值灌回）。

★ 语义要点（用户 2026-09-14 定案，勿改坏）：
  · 释放必须覆盖**全部编辑层**（BASE + SHAPE），只清 `fader` 会漏
    （programmer/effect/matrix 仍持有 → 监控区继续显示"有效果"）；
  · 但**不得**清 clip 播放层（"监控区与 clip 无关"）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ...core.constants import (
    BASE_LAYERS as _BASE_LAYERS,
    SHAPE_LAYERS as _SHAPE_LAYERS,
)
from ...processing.dispatch.fixture_table import (
    channel_addrs_for,
    is_virtual_dimmer,
)

#: 释放覆盖的层 = 与 `NetworkManager.compute_editor_active_mask` **同源**
#: （BASE + SHAPE，即除 clip 播放层外的全部编辑层）。两处必须一致：
#: 监控区读数取自该 mask，释放就得清该 mask 覆盖的层。
CLEAR_LAYERS: tuple = tuple(_BASE_LAYERS + _SHAPE_LAYERS)


def expand_clear_targets(cells, info_by_id=None) -> List[Tuple[str, Any, Optional[int], Optional[int]]]:
    """要清空的 `(fixture_id, cell, universe, addr)` 列表。

    单设备格用自身 (universe, addr)；聚合格按 **`fids × ch_type` 现算地址**展开
    （`info_by_id` = 设备信息表，换算走 `fixture_table` 既有函数）。越界的
    device_index、算不出地址的设备都跳过（宁少清一格，也不能把值清到别的设备上）。

    ★★ R136 用户实报（"大组清空只清前 64 台"）：聚合格的 `src_addrs` 被
      `table_build_agg._AGG_ADDR_CAP` 截断（只留 64 个取样地址——那是给"判一致/
      混合"用的），而清空完全依赖它；"写值"却用 `fids` 展开到**全组** →
      两者覆盖范围不对称。实测 100 台的组清空后剩 36 台仍有值
      （探针 `probe_r136/probe_2_clear_truncated.py`）。
      修法：**只有取样里有该设备时才沿用取样**（语义与显示严格一致），
      取样里没有的（被截断掉、或该设备本就没这条通道的取样）一律现算。
    """
    targets: List[Tuple[str, Any, Optional[int], Optional[int]]] = []
    for c in (cells or ()):
        targets.extend(_targets_of_cell(c, info_by_id))
    return targets


def _targets_of_cell(c, info_by_id=None) -> List[Tuple[str, Any, Optional[int], Optional[int]]]:
    """单格 → 目标列表。无 fids = 单设备格（用自身地址）；有 fids = 聚合格（展开）。"""
    fids = tuple(getattr(c, "fids", None) or ())
    if not fids:
        return [(str(c.fixture_id), c,
                 getattr(c, "universe", None), getattr(c, "addr", None))]
    return _agg_targets(c, fids, info_by_id or {})


def _agg_targets(c, fids, info_by_id) -> List[Tuple[str, Any, Optional[int], Optional[int]]]:
    """聚合格 → 逐台设备的清空目标（见 `expand_clear_targets`）。"""
    samples = _samples_by_device(getattr(c, "src_addrs", None) or ())
    out: List[Tuple[str, Any, Optional[int], Optional[int]]] = []
    for di, fid in enumerate(fids):
        sfid = str(fid)
        hit = samples.get(di)
        if hit:
            out.extend((sfid, c, uni, addr) for uni, addr in hit)
            continue
        for uni, addr in _computed_addrs(info_by_id.get(sfid), c):
            out.append((sfid, c, uni, addr))
    return out


def _samples_by_device(src_addrs) -> Dict[int, List[Tuple[int, int]]]:
    """取样地址 → {设备下标: [(universe, addr), ...]}（兼容二元组/三元组两种形态）。"""
    out: Dict[int, List[Tuple[int, int]]] = {}
    for item in (src_addrs or ()):
        try:
            t = tuple(item)
            if len(t) >= 3:
                out.setdefault(int(t[0]), []).append((int(t[1]), int(t[2])))
            else:
                out.setdefault(0, []).append((int(t[0]), int(t[1])))
        except Exception:
            continue
    return out


def _computed_addrs(info, c) -> List[Tuple[Optional[int], Optional[int]]]:
    """该设备在该值类型上的**全部**地址（现算；虚拟亮度无物理地址 → [(None, None)]）。

    `occ > 0` 表示这是"复合设备某一灯位"的子行格 → 只取该次出现的地址；
    `occ == 0`（监控区的"设备类型 × 值类型"格）→ 取该类型的全部出现（灯位在格内合并，
    与 `table_build_agg._selection_samples` 同一套取样规则）。
    """
    ct = str(getattr(c, "ch_type", "") or "")
    if not info or not ct:
        return []
    if is_virtual_dimmer(info, ct):
        return [(None, None)]           # 虚拟通道：只清行缓存，不去清层（防误清新地址）
    occ = int(getattr(c, "occ", 0) or 0)
    if occ > 0:
        one = _nth_addr(info, ct, occ)
        return [one] if one else []
    return [(int(u), int(a)) for u, a in channel_addrs_for(info, ct)]


def _nth_addr(info, ct, occ):
    """该设备第 `occ` 个（0-based）该值类型的地址（没有返回 None）。"""
    addrs = channel_addrs_for(info, ct)
    return addrs[occ] if 0 <= occ < len(addrs) else None


def clear_layer_addrs(nm, targets, layer=None) -> int:
    """层清空：按域聚合地址，每域每层一次调用。返回清掉的地址数。

    `layer=None` = 清**全部编辑层**（`_CLEAR_LAYERS`）；显式传层则只清该层。
    虚拟通道无物理地址，跳过（只有行缓存可清）。
    """
    if nm is None or not hasattr(nm, "clear_layer_channels"):
        return 0
    by_uni = _addrs_by_universe(targets)
    layers = (str(layer),) if layer else CLEAR_LAYERS
    total = 0
    for uni, addrs in by_uni.items():
        for lname in layers:
            try:
                nm.clear_layer_channels(str(lname), int(uni), sorted(addrs))
            except Exception:
                continue
        total += len(addrs)
    return total


def _addrs_by_universe(targets) -> Dict[int, set]:
    """{域: {地址}}（虚拟通道无物理地址 → 跳过，只有行缓存可清）。"""
    by_uni: Dict[int, set] = {}
    for _fid, c, uni, addr in targets:
        if addr is None or getattr(c, "virtual", False):
            continue
        by_uni.setdefault(int(uni), set()).add(int(addr))
    return by_uni


def clear_row_stores(row_of, targets) -> None:
    """清行缓存（防 keepalive 重建时把旧值灌回）。按 (设备, 值类型) 去重。

    `row_of(fid)` = 该设备所在的 Programmer 行（无则跳过）。
    """
    seen = set()
    for fid, c, _uni, _addr in targets:
        ch_type = str(getattr(c, "ch_type", "") or "")
        if not ch_type or (fid, ch_type) in seen:
            continue
        seen.add((fid, ch_type))
        row = row_of(fid)
        if row is None:
            continue
        try:
            row.clear_cell_store(fid, ch_type)
        except Exception:
            continue


__all__ = ["CLEAR_LAYERS", "expand_clear_targets", "clear_layer_addrs",
           "clear_row_stores"]
