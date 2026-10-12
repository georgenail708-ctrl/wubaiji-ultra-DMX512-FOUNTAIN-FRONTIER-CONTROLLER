# -*- coding: utf-8 -*-
"""调度台逐格读取的**纯计算**层（`CellOps.read_cells_ex` 的实现主体）。

拆出理由（架构门禁：函数 ≤100 行 / CC ≤8 / 嵌套 ≤4）：
`read_cells_ex` 原把"收集地址 → 逐域取数 → 逐格汇总（聚合格/单设备格两路）"
全写在一个方法里（130 行 / CC 43 / 嵌套 6），是门禁的长期违例点。这里按数据流
切成四步，每步单独可读、可测：

    collect_refs(cells)          有效格（有域 / 有取样地址 / 有设备集）
    collect_by_universe(refs)    {域: {地址}} —— 每域一次取数（性能契约）
    read_caches(nm, by_uni)      {域: data} + {域: {addr: owned}} + {域: {addr: layers}}
    summarize_agg(...)           聚合格 → (key, spread, value, owned, fids, owners)
    summarize_plain(...)         单设备格 → 同上

**语义不在本模块定义**：值的口径（编辑器层混合输出、排除 clip）由 `read_caches`
的调用参数决定；"混合"判定规则见 `summarize_agg` 的说明（用户 2026-09-14 定案）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

Record = Tuple[Tuple[str, str], int, Optional[int], bool, Tuple[str, ...], Tuple[str, ...]]


def collect_refs(cells: Sequence[Any]) -> List[Any]:
    """筛出**可读**格：有域、有取样地址、或有设备集（三者任一）。"""
    return [c for c in (cells or ())
            if getattr(c, "universe", None) is not None
            or getattr(c, "src_addrs", None) or getattr(c, "fids", None)]


def collect_by_universe(refs: Sequence[Any]) -> Dict[int, set]:
    """{域: {0-based 地址}} —— 每域只取一次数（性能契约）。

    取样地址两种形态：单设备格 `(uni, addr)` 二元组；聚合格
    `(device_index, uni, addr)` 三元组（见 table_model.TableCell.src_addrs）。
    """
    by_uni: Dict[int, set] = {}
    for c in refs:
        if getattr(c, "universe", None) is not None and not getattr(c, "src_addrs", None):
            by_uni.setdefault(int(c.universe), set()).add(int(c.addr))
        for item in (getattr(c, "src_addrs", None) or ()):
            t = tuple(item)
            if len(t) >= 3:
                by_uni.setdefault(int(t[1]), set()).add(int(t[2]))
            elif len(t) == 2:
                by_uni.setdefault(int(t[0]), set()).add(int(t[1]))
    return by_uni


def read_caches(nm, by_uni: Dict[int, set], editor_universe_fn, ownership_fn,
                owners_fn) -> Tuple[Dict[int, Any], Dict[int, Dict[int, bool]],
                                    Dict[int, Dict[int, Tuple[str, ...]]]]:
    """逐域取三个缓存：混合值 / 归属 / 逐通道层名。

    ★ 三个函数注入（而不是在此 import NM）：调用方决定口径。
      调度台传的是"编辑器层混合输出 + 排除 clip 的归属"——
      用户 2026-09-14："不要管 clip 播放引起的好像这个设备有值，只在我真的在
      各个功能页面对该设备有编辑的情况下才出值"。
    """
    data_cache: Dict[int, Any] = {}
    own_cache: Dict[int, Dict[int, bool]] = {}
    owners_cache: Dict[int, Dict[int, Tuple[str, ...]]] = {}
    for uni, addrs in by_uni.items():
        chs = sorted(addrs)
        data_cache[uni] = editor_universe_fn(nm, uni)
        own_cache[uni] = ownership_fn(nm, uni, chs) or {}
        owners_cache[uni] = owners_fn(nm, uni, chs)
    return data_cache, own_cache, owners_cache


def _addr_of(item: Tuple) -> Tuple[int, int, int]:
    """取样条目 → (device_index, universe, addr)（二元组统一按设备 0 处理）。"""
    if len(item) >= 3:
        return int(item[0]), int(item[1]), int(item[2])
    return 0, int(item[0]), int(item[1])


def _value_at(data_cache: Dict[int, Any], uni: int, addr: int) -> Optional[int]:
    data = data_cache.get(uni)
    if data is None or not (0 <= addr < len(data)):
        return None
    return int(data[addr])


def _collect_samples(cell, data_cache, own_cache):
    """取样 → `(owned_any, vals, all_vals, per_dev, per_dev_owned)`。

    `vals` = **有效取样**（有归属，或值≠0）；`all_vals` = 全部取样（兜底显示）。
    虚拟亮度（dimmer）额外按设备取最大值（亮度语义），故另给 `per_dev*`。
    """
    virtual = bool(getattr(cell, "virtual", False))
    per_dev: Dict[int, Optional[int]] = {}
    per_dev_owned: Dict[int, bool] = {}
    vals: List[int] = []
    all_vals: List[int] = []
    owned_any = False
    for item in (getattr(cell, "src_addrs", None) or ()):
        di, uni, addr = _addr_of(item)
        v = _value_at(data_cache, uni, addr)
        own = bool(own_cache.get(uni, {}).get(addr, False))
        owned_any = owned_any or own
        if not virtual:
            _fold_plain(vals, all_vals, v, own)
        else:
            _fold_device_max(per_dev, per_dev_owned, di, v, own)
    if virtual:
        vals = [v for di, v in per_dev.items()
                if v is not None and (per_dev_owned.get(di) or v != 0)]
        all_vals = [v for v in per_dev.values() if v is not None]
    return owned_any, vals, all_vals


def _fold_plain(vals: List[int], all_vals: List[int], v: Optional[int],
                own: bool) -> None:
    """非虚拟取样折叠：全部值进 `all_vals`；**有效**值（有归属或 ≠0）另进 `vals`。"""
    if v is None:
        return
    all_vals.append(v)
    if own or v != 0:
        vals.append(v)


def _fold_device_max(per_dev: Dict[int, Optional[int]], per_dev_owned: Dict[int, bool],
                     di: int, v: Optional[int], own: bool) -> None:
    """虚拟亮度按设备折叠：取该设备各色通道的**最大值**（亮度语义）。"""
    cur = per_dev.get(di)
    per_dev[di] = v if cur is None else (max(cur, v) if v is not None else cur)
    per_dev_owned[di] = per_dev_owned.get(di, False) or own


def _owners_of(cell, owners_cache) -> Tuple[str, ...]:
    """该格被哪些混音层控制（去重保序）。"""
    owners: Tuple[str, ...] = ()
    for item in (getattr(cell, "src_addrs", None) or ()):
        _di, uni, addr = _addr_of(item)
        for lay in owners_cache.get(uni, {}).get(addr, ()) or ():
            if lay not in owners:
                owners = owners + (lay,)
    return owners


def summarize_agg(cell, data_cache, own_cache, owners_cache) -> Record:
    """聚合格汇总：**一格 = 一个设备类型 × 一个值类型**。

    取样 = 组内「每台设备 × 每个灯位」（见 table_build_agg._selection_channel_cells）。

    判定规则（用户 2026-09-14 语义，逐字落实）：
        "只要有其中一个设备有效果就填这个通道…直到被拆分开到无效果为止"
        "显示不区分"（不区分谁有效果、谁没有）。
      → 参与"一致/分歧"比较的只有**有效取样**；无归属的 0 是"这里没东西"，
        **不算**一个不同的值。
      → 虚拟亮度（dimmer）：按设备取 R/G/B/W 最大值（亮度语义）后再判。

    ★ 两个历史 bug 同根（用户 2026-09-14 实报）：原实现 `per_dev[di] = v` 对非虚拟
      是**覆盖**（复合设备只剩最后一个灯位），且把未编辑的 0 计入分歧 →
      · 只编辑 valve 第 1 组却显示「混合」（= "valve/pump 不显示值"）；
      · 展开条带后收回（或效果暂停值回落一致）该格停在「混合」不刷新。
    """
    key = (str(cell.fixture_id), str(cell.col_key))
    fids = tuple(str(f) for f in (getattr(cell, "fids", None) or ()))
    virtual = bool(getattr(cell, "virtual", False))
    owned_any, vals, all_vals = _collect_samples(cell, data_cache, own_cache)

    spread = len(set(vals))
    if virtual:
        best = max(vals) if vals else (max(all_vals) if all_vals else None)
    else:
        # 有效取样一致 → 该值；不一致 → 最小值（spread>1 显示「混合」）；
        # 全部无效 → 回退全部取样（真实输出值，如电机默认中位 128）。
        best = min(vals) if vals else (min(all_vals) if all_vals else None)
    return key, spread, best, owned_any, fids, _owners_of(cell, owners_cache)


def summarize_plain(cell, data_cache, own_cache, owners_cache) -> Record:
    """单设备格汇总（无 src_addrs）：直接读该设备的 (universe, addr)。"""
    key = (str(cell.fixture_id), str(cell.col_key))
    fids = tuple(str(f) for f in (getattr(cell, "fids", None) or ()))
    if getattr(cell, "addr", None) is None:
        return key, 0, None, False, fids, ()
    uni, addr = int(cell.universe), int(cell.addr)
    val = _value_at(data_cache, uni, addr)
    owned = bool(own_cache.get(uni, {}).get(addr, False))
    owners = tuple(owners_cache.get(uni, {}).get(addr, ()) or ())
    return key, (1 if val is not None else 0), val, owned, fids, owners


__all__ = ["collect_refs", "collect_by_universe", "read_caches",
           "summarize_agg", "summarize_plain"]
