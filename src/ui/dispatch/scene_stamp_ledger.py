# -*- coding: utf-8 -*-
"""场景 stamp 台账（R178 优先级压制的记账段，从 scene_takeover 拆出压行门禁）。

记录"哪个场景以什么优先级 stamp 过哪些地址"：
释放/清台时摘账；`stamped_by_higher(addr, prio)` 判定低优先级场景是否应
跳过 stamp（优先级压制）。语义与拆出前逐行一致。
"""
from __future__ import annotations

from typing import Dict, Iterable, Set, Tuple

from ...core.constants import EDITOR_LTP_LAYERS

Addr = Tuple[int, int]


def _stamp_layer(nm, layer: str, addrs: Iterable[Addr]) -> int:
    from .scene_takeover import _by_universe   # 环：函数级延迟导入
    """把该层在这些地址上的**当前值原样重写**（touch_ltp=True）→ 时间戳最新。

    只 stamp **该层确实有值**的地址（`get_layer_channel_values` 只返回 active 通道）
    —— 于是"不碰任何数值、只推时间戳"，也不会把别的层/别的场景的通道算进来。
    """
    n = 0
    for uni, chans in _by_universe(addrs).items():
        try:
            vals = nm.get_layer_channel_values(str(layer), int(uni), chans) or {}
        except Exception:
            continue
        if not vals:
            continue
        try:
            nm.update_layer_sparse(str(layer), int(uni), dict(vals), touch_ltp=True)
            n += len(vals)
        except Exception:
            continue
    return n


def _stamp_node(nm, node: dict, addrs: Set[Addr]) -> int:
    """把一个节点的所有输出层 stamp 一遍。"""
    from .scene_takeover import _OUTPUT_LAYERS   # 环：函数级延迟
    layers = _OUTPUT_LAYERS.get(str(node.get("panel", "") or ""), ())
    total = 0
    for layer in layers:
        total += _stamp_layer(nm, layer, addrs)
    return total


# ── 优先级压制（R178 革新）─────────────────────────────────────────────────
#: {场景 id: {"prio": int, "addrs": {（域, 地址）…}}} —— 各激活场景**已 stamp** 的
#: 通道记账。高优先级场景 stamp 过的通道，低优先级场景点亮时跳过 stamp（不抢）。
#: 场景释放 / 全局清台时必须摘账（见 `note_scene_released` / `clear_stamp_ledger`）。
_STAMP_LEDGER: Dict[str, dict] = {}


def _scene_priority(cid: str) -> int:
    """该场景的优先级档（未设/取不到 = 0，不参与压制）。"""
    try:
        from ...processing.dispatch import get_effect_cache_store
        c = get_effect_cache_store().get(str(cid or ""))
        return int(c.priority_level()) if c is not None else 0
    except Exception:
        return 0


def _active_ids(panel) -> set:
    """当前激活（亮着）的场景 id 集（取不到给空集 = 无从判定，不压制）。"""
    try:
        return {str(x) for x in (panel.list.active_ids() or ())}
    except Exception:
        return set()


def _blocked_addrs(panel, cid: str, want: Set[Addr]) -> Set[Addr]:
    """本场景想 stamp 的地址里，被**更高优先级的激活场景**已占用的那部分。

    规则（R178 定案）：仅当对方 ① 仍在激活（高亮）② 优先级**严格高于**本场景
    ③ 确实 stamp 过这些地址 时才压制 —— 同优先级（含都未设 = 0）维持既有
    "后发接管"语义不变。释放/清台未摘账的陈旧条目按①挡掉。
    """
    if not want:
        return set()
    my_prio = _scene_priority(cid)
    active = _active_ids(panel)
    blocked: Set[Addr] = set()
    for other, rec in _STAMP_LEDGER.items():
        if other == cid or other not in active:
            continue
        if int(rec.get("prio", 0)) <= my_prio:
            continue
        blocked |= (set(rec.get("addrs", ()) or ()) & want)
    return blocked


def _note_stamped(cid: str, prio: int, addrs: Set[Addr]) -> None:
    """把本场景刚 stamp 的地址记进账（合并；空集也记优先级，供后续压制判定）。"""
    if not cid:
        return
    rec = _STAMP_LEDGER.setdefault(str(cid), {"prio": int(prio), "addrs": set()})
    rec["prio"] = int(prio)
    rec["addrs"] |= set(addrs or ())


def note_scene_released(cid: str) -> None:
    """场景释放 → 摘掉它的 stamp 记账（其通道不再压制低优先级场景）。"""
    _STAMP_LEDGER.pop(str(cid or ""), None)


def note_scene_content_changed(cid: str) -> None:
    """★ R183 审计⑦B P2-9：场景**内容被替换**（覆盖更新 / 换 nodes）→ 摘账。

    修前 `EffectCacheStore.update_from_nodes` 只换 nodes，从不通报台账 —— 台账里
    记的是**旧 nodes** stamp 过的地址，而那些地址现在可能已不被本场景写入：
    残留条目会让 `_blocked_addrs` 用陈旧地址继续压制低优先级场景（那些通道
    **永远**抢不到，而"压制方"其实早就不输出它们）。
    """
    note_scene_released(cid)


def clear_stamp_ledger() -> None:
    """全局清台 / 库换新 → 记账整体作废（与激活高亮同步归零）。"""
    _STAMP_LEDGER.clear()


def ledger_snapshot() -> Dict[str, dict]:
    """台账只读快照（诊断/测试用；外部不得据此改写内部集合）。"""
    return {str(k): {"prio": int(v.get("prio", 0)),
                     "addrs": set(v.get("addrs", ()) or ())}
            for k, v in _STAMP_LEDGER.items()}


def stamp_scene_takeover(panel, cid: str) -> int:
    """把该场景**已写出的输出层**重新 stamp 一次 LTP（= 后发接管）。返回 stamp 数。

    必须在场景 recall **之后**调用（面板先把值写进层，这里才推时间戳）。
    取不到 NM / 场景（空槽、旧档）时静默返回 0 —— 接管是增强，不是前置条件。
    ★ R178：stamp 前先按优先级压制 —— 高优先级激活场景已 stamp 的通道跳过
      （本场景的值仍写在自己的层里，对方释放后会由回放链重新接管）。
    """
    cid = str(cid or "")
    if not cid:
        return 0
    from .scene_takeover import _nm_of   # 环：函数级延迟导入
    nm = _nm_of(panel)
    if nm is None:
        return 0
    total = 0
    stamped: Set[Addr] = set()
    from .scene_takeover import _node_addrs_to_stamp   # 环：函数级延迟导入
    for node, addrs in _node_addrs_to_stamp(panel, cid):
        live = set(addrs) - _blocked_addrs(panel, cid, set(addrs))
        if live:
            total += _stamp_node(nm, node, live)
            stamped |= live
    # ★ R183 审计⑦B P2-9：`total == 0`（该场景的层上其实没值 / 全部被压制）
    #   时**不记账** —— 修前无条件 `_note_stamped`，于是台账里出现"地址集非空
    #   但实际一个通道都没 stamp 过"的条目 → 它照样压制低优先级场景（幽灵压制）。
    if total > 0:
        _note_stamped(cid, _scene_priority(cid), stamped)
    return total

