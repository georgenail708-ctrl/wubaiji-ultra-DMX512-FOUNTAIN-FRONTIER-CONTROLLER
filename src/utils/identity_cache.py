"""
src/utils/identity_cache.py — 有界身份/内容缓存帮手（R173 性能阶段）

背景（见 ai-memory/identity-cache-discipline-2026-08-19.md）：项目里大量代码用
`id(obj)` 当缓存键，缺一不可的合规写法只有三种：
  1. id 键 + 强引用 + `is` 复核；
  2. 内容逐元素比对（适合入参每次新建列表的场景）；
  3. 纯版本号键。
本模块把写法 1 封装成一处 `IdentityCache`，把"键"和"内容签名"拆开：
  - `content_key`：普通可哈希值（可以含 id()，只用于哈希分桶，不作正确性依据）。
  - `guards`：一组需要 `is` 复核的长寿对象；缓存条目持有它们的**强引用**，
    只要还在表里就不会被 GC，其 `id()` 就不可能被别的对象复用——这是防止
    "id 回收复用 → 命中别人的分组 = 输出到错误设备"（P0）的关键。
命中要求 content_key 相同 **且** 每个 guard 都 `is` 复核通过；两者缺一都会
被当作未命中（宁可多算一次，也不能返回错的）。

`bounded_put` 把"满了逐出最旧一条"的写法收敛成一处，替代旧代码里到处出现的
`if len(cache) >= N: cache.clear()`（整表清空会击穿其它无关条目）。

用法::

    cache = IdentityCache(capacity=64)
    hit = cache.get((project,), content_key)
    if hit is None:
        value = ...compute...
        cache.put((project,), content_key, value)
    else:
        value = hit
"""

from __future__ import annotations

from typing import Any, Hashable, Optional, Sequence


def bounded_put(cache: dict, key: Hashable, value: Any, cap: int) -> None:
    """有界写入：容量已满且 key 未命中时，只逐出最久未插入的一条（FIFO）。

    与 `cache.clear()` 相比不会击穿表里其它仍然有效的条目；`cap <= 0` 时视为
    "不缓存"直接返回（防误用）。
    """
    if cap <= 0:
        return
    if key not in cache and len(cache) >= cap:
        oldest = next(iter(cache), None)
        if oldest is not None:
            del cache[oldest]
    cache[key] = value


def fixture_ids_signature(fixture_ids: Optional[Sequence[Any]]) -> tuple:
    """把设备/id 序列变成稳定的内容签名（tuple[str, ...]），替代易抖动的 id() 键。

    上游经常 `list(...)` / 推导式每次新建"内容相同"的列表 → `id()` 每次不同、
    但内容不变；用内容签名做缓存键可以让这类"churn 但等价"的调用真正命中。
    列表可以是 id 字符串，也可以是带 `.id` 属性的设备对象，两种都归一化成 str。
    """
    if not fixture_ids:
        return ()
    return tuple(str(getattr(fi, "id", fi) or "") for fi in fixture_ids)


class IdentityCache:
    """键 = 内容签名；值 = (guards, value)，guards 持强引用 + 逐个 `is` 复核。

    容量满时逐出最旧一条（`bounded_put`），不做整表清空。
    """

    __slots__ = ("_cache", "_capacity")

    def __init__(self, capacity: int = 64) -> None:
        self._cache: dict = {}
        self._capacity = max(1, int(capacity))

    def get(self, guards: Sequence[Any], content_key: Hashable) -> Optional[Any]:
        """命中返回缓存值；guards 数量或任一元素 `is` 复核失败一律当未命中。"""
        entry = self._cache.get(content_key)
        if entry is None:
            return None
        cached_guards, value = entry
        if len(cached_guards) != len(guards):
            return None
        for cached_guard, guard in zip(cached_guards, guards):
            if cached_guard is not guard:
                return None
        return value

    def put(self, guards: Sequence[Any], content_key: Hashable, value: Any) -> None:
        bounded_put(self._cache, content_key, (tuple(guards), value), self._capacity)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
