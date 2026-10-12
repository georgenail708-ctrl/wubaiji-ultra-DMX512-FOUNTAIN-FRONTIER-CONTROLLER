# -*- coding: utf-8 -*-
"""设备组归属解析 —— 把"临时选中的设备集合"挂到它隶属的**已创建组**之下。

## 为什么需要（用户 2026-09-13 要求）

调度台监视器要按设备组做**子母文件夹**分类显示，并区分两种组：

    · **已创建的组**（设备组面板里存下来的 `project.fixture_groups`）→ 母文件夹
    · **临时选中的组**（TopView 框选 / 选择工具）→ 子文件夹，**隶属于某个已创建大组之下**

难点：`EffectNode.group_key` 是**按设备 id 内容寻址**的（`grp:id1|id2|...`），
所以"临时选中一批设备"与"已创建组的设备集合恰好相同"在总线上是**同一个 key**，
无法区分。且面板侧没有统一的组身份上报。

本模块用**设备集合的包含关系**反查隶属关系（纯函数、无 Qt、无副作用）：

    临时选中的 fixture 集合 ⊆ 某已创建组的 fixture 集合  ⇒  隶属该组
    多个候选（嵌套组）时取**最小的那个**（最贴切、避免挂到过大的父组）

★ 最小完整母集语义（用户 2026-09-13 明确举例）：
    一条弧线既有「整体组」又有「左半组」（左半 ⊆ 整体）。
      · 在左半里再选几台 → 归 **左半组**（最小完整母集），不是整体组
      · 跨左右选        → 左半装不下 → 升到 **整体组**
      · 左半组自身      → 是母，同时记录其上级「整体组」（支持嵌套）

匹配只用 `fixture_ids` 的实际成员。`base_order_fixture_ids` 是母序追溯，
只能为成员定序，不能扩充组归属或组计数；显式空组仍为空。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.core.fixture_selection import ordered_members


def _norm_ids(ids: Iterable) -> List[str]:
    """去重保序的 id 归一化（与各面板 _normalize_fixture_ids 同口径）。"""
    out: List[str] = []
    seen = set()
    for x in (ids or []):
        s = str(x)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _group_ids(g: dict) -> List[str]:
    """实际组成员按母序排列；母序中的非成员不参与归属判断。"""
    if not isinstance(g, dict):
        return []
    return ordered_members(g.get("fixture_ids"), g.get("base_order_fixture_ids"))


def resolve_parent_group(
    fixture_ids: Iterable,
    groups: Optional[Iterable[dict]],
    *,
    exclude_group_id: str = "",
) -> Tuple[str, str]:
    """反查某设备集合隶属的**已创建组**。

    参数:
      fixture_ids      该效果作用的设备 id（临时选中或已创建组都可能）
      groups           project.fixture_groups（list[dict]，字段 id/name/fixture_ids/…）
      exclude_group_id 排除自身（若该节点本身就是某已创建组，不必隶属自己）

    返回 (group_id, group_name)：找不到隶属则 ("", "")。

    规则：候选 = 设备集合是"本集合超集"的已创建组；取**设备数最少者**
    （最小包含组 = 最贴切的母组）。完全相等也视为隶属（临时选中整体等于某组）。
    """
    target = set(_norm_ids(fixture_ids))
    if not target or not groups:
        return "", ""
    best: Optional[Tuple[int, str, str]] = None
    for g in groups:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id", "") or "")
        if not gid or (exclude_group_id and gid == exclude_group_id):
            continue
        gids = _group_ids(g)
        if not gids:
            continue
        gset = set(gids)
        # 本集合必须是该组的子集（含相等）
        if not target.issubset(gset):
            continue
        size = len(gset)
        name = str(g.get("name", "") or "")
        if best is None or size < best[0]:
            best = (size, gid, name)
    if best is None:
        return "", ""
    return best[1], best[2]


def resolve_for_node(node, groups: Optional[Iterable[dict]]) -> None:
    """就地补齐一个 EffectNode 的组身份（调度台刷新时调用）。

    ★ 语义纪律：`group_id` 只在**面板明确"已加载某个创建组"**时才保留。
    面板用的伪 id（`__all__` / `__topview_live__` / `__selected__` / `__multi__`）
    不是创建组 → 一律清空并视为**临时选中/全局**，再解析它隶属的母组。

    这样"临时选中的组"与"已创建的组"在视图上必然分属不同层级：
        已创建组   → 母文件夹（直接格子）
        临时选中   → 子文件夹（挂在母文件夹下）

    ★★ 2026-09-13 补一条边界（实测踩到）：
      节点**没有**声明 group_id（如效果库面板从来只填设备集），但它覆盖的设备集
      **恰好等于**某个已创建组 —— 此时应当认作**那个创建组**（母），而不是
      "临时选中"。否则效果库的每一个节点都会被标成"临时选中"，把用户建好的组
      淹没在临时子条里（"侧翼"这种真实组反而不显示）。

      判据用**集合相等**而非"包含"：只有完全吻合才算"这就是那个组"；真子集
      （在左半里又选了设备）仍按临时选中处理 —— 这正是用户要的
      "最小完整母集"语义（见 resolve_parent_group 的文档）。
    """
    if node is None:
        return
    try:
        real = _real_group_index(groups)
        gid = str(getattr(node, "group_id", "") or "")
        if gid and gid in real:
            # 面板确认加载了创建组 → 保留身份；若该组嵌套于更大组，记录父组
            node.group_id = gid
            if not str(getattr(node, "group_name", "") or ""):
                node.group_name = real[gid]
            pid, pname = resolve_parent_group(
                getattr(node, "fixture_ids", []) or [], groups, exclude_group_id=gid)
            node.parent_group_id = pid
            node.parent_group_name = pname
            return
        # 伪 id / 空 → 通常不是创建组。但先看是否**恰好就是**某个创建组。
        exact = _exact_group_for(getattr(node, "fixture_ids", []) or [], groups)
        if exact is not None:
            node.group_id = str(exact.get("id", "") or "")
            node.group_name = str(exact.get("name", "") or node.group_id)
            pid, pname = resolve_parent_group(
                getattr(node, "fixture_ids", []) or [], groups,
                exclude_group_id=node.group_id)
            node.parent_group_id = pid
            node.parent_group_name = pname
            return
        node.group_id = ""
        pid, pname = resolve_parent_group(getattr(node, "fixture_ids", []) or [], groups)
        node.parent_group_id = pid
        node.parent_group_name = pname
    except Exception:
        pass


def _exact_group_for(fixture_ids: Sequence[str],
                     groups: Optional[Iterable[dict]]) -> Optional[dict]:
    """设备集**完全等于**某个已创建组时返回该组；否则 None。

    只认完全相等（双方元素一一对应）。空设备集不参与（"全部设备"不是某个组）。
    """
    want = {str(x) for x in (fixture_ids or []) if str(x)}
    if not want:
        return None
    for g in (groups or ()):
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id", "") or "")
        if not gid:
            continue
        have = {str(x) for x in (g.get("fixture_ids") or []) if str(x)}
        if have and have == want:
            return g
    return None


def _real_group_index(groups: Optional[Iterable[dict]]) -> Dict[str, str]:
    """{group_id: name} —— 真实存在的已创建组（用于甄别伪 id）。"""
    out: Dict[str, str] = {}
    for g in (groups or []):
        if isinstance(g, dict):
            gid = str(g.get("id", "") or "")
            if gid:
                out[gid] = str(g.get("name", "") or "")
    return out


def build_group_index(groups: Optional[Iterable[dict]]) -> Dict[str, Tuple[str, int]]:
    """{group_id: (name, size)}，供视图快速取文件夹标题。"""
    out: Dict[str, Tuple[str, int]] = {}
    for g in (groups or []):
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id", "") or "")
        if gid:
            out[gid] = (str(g.get("name", "") or ""), len(_group_ids(g)))
    return out
