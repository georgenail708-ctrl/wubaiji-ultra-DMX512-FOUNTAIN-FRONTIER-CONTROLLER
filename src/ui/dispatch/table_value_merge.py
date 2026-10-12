# -*- coding: utf-8 -*-
"""表格视图的**值刷新合并逻辑**（纯计算，可单测；`table_view` 只做编排）。

拆出理由：
  · 架构门禁要求函数 ≤100 行 / CC ≤8 —— `update_values` 原把"合并 + 权威替换 +
    标记比对 + 脏区计算"挤在一起（54 行 / CC 17）。
  · 这段逻辑是**纯字典运算**，与 Qt 绘制无关；抽出来后可独立断言，不需要起 QApplication。

两条语义（用户 2026-09-14 实报，别改坏）：
  ① **权威替换**：`refreshed_keys` 给出"本次真正重读的格"时，这批键的分歧/来源
     以本次结果为准（缺失 = 该格已不再分歧 → **删除**旧条目）。原实现只 merge 不清，
     于是"展开条带 → 混合"之后收回条带（或效果暂停、值回落一致）会永远停在「混合」。
  ② **重绘收窄**：只重画**真正变化**的格（并集矩形）。拖动实时编辑一次只动几十格，
     重画整个视口纯属浪费。任一变化格取不到矩形 → 返回 None（保守回退整视口）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set, Tuple

Key = Tuple[str, str]


def merge_value_caches(states: dict, incoming: dict, *,
                       refreshed_keys: Optional[Set[Key]] = None) -> None:
    """把一批刷新结果并入（或**权威替换**）三个缓存字典（就地修改）。

    `states`   = {名: 状态字典}（就地修改的对象，如 values/spreads/sources 三个）
    `incoming` = {名: 本批新数据}（None = 本次不动该项）
    `refreshed_keys=None` → 纯合并（只覆盖可视范围时不能清掉离屏格的值）。
    `refreshed_keys` 给定 → 这批键以本次结果为准（缺失即删除）。
    """
    for name, state in states.items():
        data = incoming.get(name)
        if data:
            state.update(data)
        if refreshed_keys is None:
            continue
        # 权威语义：这批键以本次结果为准（缺失 = 删掉旧条目）
        for k in refreshed_keys:
            if not data or k not in data:
                state.pop(k, None)
            else:
                state[k] = data[k]


def apply_mark_set(cur: Set[Key], new) -> Tuple[Set[Key], bool]:
    """更新标记集；返回 `(新集, 是否变化)`。`new=None` 表示本次不动标记。"""
    if new is None:
        return cur, False
    s = set(new)
    return s, (s != cur)


def dirty_rect_for(incoming: dict, old: dict, rects: Dict[Key, Any], *,
                   refreshed_keys: Optional[Set[Key]] = None) -> Optional[Any]:
    """本次刷新**真正变化**的格的并集矩形；None = 未收窄（调用方整视口重绘）。

    「混合」↔ 数值 的切换不改值也必须重画，故分歧变化同样纳入；
    任一变化格取不到矩形 → None（保守回退，绝不漏画）。
    必须在**合并之前**调用（依赖旧值做比较）。
    """
    if not refreshed_keys:
        return None
    out = None
    st = _KeyDiff(incoming, old, rects)
    for k in refreshed_keys:
        if not st.changed(k):
            continue
        r = rects.get(k)
        if r is None:
            return None
        out = r if out is None else out.united(r)
    return out


class _KeyDiff:
    """单格"是否变化"判定（值变了，或「混合」标志翻转）。

    「混合」↔ 数值 的切换不改值也必须重画，故分歧变化同样纳入。
    """

    def __init__(self, incoming: dict, old: dict, rects: dict) -> None:
        self.values = incoming.get("values") or {}
        self.spreads = incoming.get("spreads") or {}
        self.old_values = old.get("values") or {}
        self.old_spreads = old.get("spreads") or {}

    def changed(self, k) -> bool:
        v = self.values.get(k)
        if v is not None and self.old_values.get(k) != v:
            return True
        return ((self.spreads.get(k) or 1) > 1) != ((self.old_spreads.get(k) or 1) > 1)


__all__ = ["merge_value_caches", "apply_mark_set", "dirty_rect_for"]
