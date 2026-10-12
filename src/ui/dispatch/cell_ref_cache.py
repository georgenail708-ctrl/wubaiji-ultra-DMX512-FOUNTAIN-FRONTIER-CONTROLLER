# -*- coding: utf-8 -*-
"""逐格 `CellRef` 的**身份缓存**（调度台刷新热路径）。

★ 为什么需要（2026-09-19 实测归因）：
  聚合格要把整组设备 id 归一化成 `tuple[str, ...]`（`fids`）。2000 台组 ×
  每屏约 50 格 = 每次 `refresh_monitor` 10 万次 `str()`。实测分解（2000 台
  世界，`_refs_for_cells` 单次 7.9ms）:
      fids 归一化   5.788ms   ← 本模块消除
      src_addrs     0.388ms
      addr_lookup   0.006ms
  而调度台每 400ms 就刷一次，是"任何操作都卡"的头号成本。

  格的这些静态字段由表模型**构造时**写定、此后不再变化（见
  `table_build_agg`），因此可按格对象身份复用 —— 命中路径只剩一次 dict
  查找，`refresh_monitor` 中位 16.6ms → 0.8ms。

★ 正确性: 命中时用 `ent[0] is cell` 复核身份。entry 持有格的**强引用**，
  故 `id()` 不可能被其它对象复用（这是"用 id 当键"唯一危险的来源，已被
  强引用堵死）。地址随设备补丁变化时，由调用方按设备信息内容令牌整批重置
  （与 `TableHostMixin._fixture_info` 同一条失效链）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from .cell_ops import CellRef, refs_from_cells

#: 缓存硬上限（正常调用由 `invalidate_ref_cache` 重置，达不到；仅防御
#: 「表模型频繁重建而设备信息令牌不变」时死格在缓存里无界堆积）。
_REF_CACHE_CAP = 4096


def _cache_get(cache: Dict[int, Any], cell: Any) -> Optional[CellRef]:
    """命中（且身份复核通过）才返回 CellRef；否则 None。"""
    ent = cache.get(id(cell))
    if ent is None or ent[0] is not cell:
        return None
    return ent[1]


def refs_for_cells_cached(owner: Any, cells: Sequence[Any],
                          addr_lookup: Callable[[Any], Optional[tuple]],
                          virtual_lookup: Optional[Callable[[Any], bool]] = None
                          ) -> List[CellRef]:
    """`refs_from_cells` 的缓存版；缓存挂在 `owner.__dict__["_ref_cache"]`。

    `owner` 是持面板生命周期的对象（UI 混合类），缓存随它一起消亡 ——
    不需要额外清理路径。
    """
    seq = [] if not cells else list(cells)
    if not seq:
        return []
    cache = owner.__dict__.get("_ref_cache")
    if cache is None:
        cache = {}
        owner._ref_cache = cache
    if len(cache) > _REF_CACHE_CAP:
        cache.clear()
    out: List[Optional[CellRef]] = []
    misses: List[int] = []
    for i, c in enumerate(seq):
        hit = _cache_get(cache, c)
        if hit is None:
            misses.append(i)
        out.append(hit)
    if misses:
        fresh = refs_from_cells([seq[i] for i in misses], addr_lookup,
                                virtual_lookup)
        for i, ref in zip(misses, fresh):
            out[i] = ref
            cache[id(seq[i])] = (seq[i], ref)
    return out                      # type: ignore[return-value]


def invalidate_ref_cache(owner: Any) -> None:
    """丢掉逐格缓存（设备信息令牌变化 / 表格结构重建时调用）。"""
    try:
        owner.__dict__.pop("_ref_cache", None)
    except Exception:
        pass


def fixtures_token_of(owner: Any, default: Any = None) -> Any:
    """设备表 / 定义表的**内容令牌**（`owner._fixtures_token` 的取数封装）。

    ★★ R136：`view.set_nodes` 的短路签名必须并入本令牌，否则"打补丁改域/起始地址"
      不会让视图重建 → 聚合格的 `src_addrs`（建表时按设备表写死）仍是旧地址 →
      读写清全落旧 DMX 地址。与 `fixture_info_cached` **同一份令牌**（不新造第二把尺子）。
    未注入 provider / 取数失败 → `default`（调用方据此跳过重建，与原行为一致）。
    """
    try:
        fixtures = list(owner._fixtures_provider()
                        if getattr(owner, "_fixtures_provider", None) else []) or []
        defs_map = dict(owner._defs_provider()
                        if getattr(owner, "_defs_provider", None) else {}) or {}
        return owner._fixtures_token(fixtures, defs_map)
    except Exception:
        return default


def fixture_info_cached(owner: Any) -> dict:
    """设备信息表（格地址换算用）—— 按**内容令牌**缓存到 `owner` 上。

    ★ 缓存键必须用内容令牌，不能用 `id(owner._fixtures_provider)`：该 provider
      是长期存活的函数对象，`id` 永不改变 → 缓存在面板生命周期内**永不失效**。
      于是用户重打补丁（改域/起始地址）后本表仍返回旧地址，写值/清空落到
      **旧 DMX 地址**上（静默写错域）。改用与 `_fixtures_token` 同款的廉价
      内容令牌（长度 + 首尾 id + 定义样本），地址一变令牌即变 → 重建。
      取设备列表只是引用拷贝（O(n) 但不逐台计算）；真正贵的
      `build_fixture_info` 仍只在令牌变化时才跑。

    令牌变化同时让逐格 CellRef 缓存失效（同一坑的第二层）。
    """
    from .panel_table_host import build_fixture_info      # 延迟导入防环
    token = fixtures_token_of(owner)
    if (getattr(owner, "_info_cache_key", None) == token
            and getattr(owner, "_info_cache", None)):
        return owner._info_cache
    fixtures = list(owner._fixtures_provider()
                    if getattr(owner, "_fixtures_provider", None) else []) or []
    defs_map = dict(owner._defs_provider()
                    if getattr(owner, "_defs_provider", None) else {}) or {}
    owner._info_cache = build_fixture_info(fixtures, defs_map)
    owner._info_cache_key = token
    invalidate_ref_cache(owner)
    return owner._info_cache


def refs_for_cells_of(owner: Any, cells: Sequence[Any],
                      channel_address_at: Callable[..., Any]
                      ) -> List[CellRef]:
    """把面板的格序列转成 CellRef（取址回调用该面板的设备信息表）。"""
    info = fixture_info_cached(owner)
    return refs_for_cells_cached(
        owner, cells,
        lambda c: (None if (c.virtual or c.agg)
                   else channel_address_at(info.get(c.fixture_id) or {}, c.ch_type)),
        lambda c: bool(c.virtual))


__all__ = ["refs_for_cells_cached", "refs_for_cells_of", "fixture_info_cached",
           "fixtures_token_of", "invalidate_ref_cache"]
