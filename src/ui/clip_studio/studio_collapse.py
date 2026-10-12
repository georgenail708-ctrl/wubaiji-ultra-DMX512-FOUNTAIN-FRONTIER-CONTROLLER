# -*- coding: utf-8 -*-
"""卡片折叠/最小化 + 子图收束的数据纯函数（R183 W2-ST #6/#14）—— 零 Qt。

一个 ``collapsed`` 标志承载两种语义（同一交互语言，折叠节点先例）：

* **最小化**（#14）：``collapsed=True``、无 ``children`` → 卡片只留标题条
  （高度 ``COLLAPSED_CARD_H``，坐标不动、连线端口按新高度重算）；
* **子图收束**（#6）：``collapsed=True`` + ``children=[子卡 id…]`` → 子卡在画布上
  隐藏（几何/命中/绘制全部跳过），父卡标题条显示「内部 N 张」徽标；展开 = 清
  ``collapsed``，子卡坐标原样还原（折叠期间从不写子卡坐标）。

**编译器只读**：折叠是纯 UI 层状态 —— `studio_compile` 不读 `collapsed/children`，
子卡照常参与编译（隐藏只是不显示），符合任务书「折叠纯 UI 层处理」。

schema（节点 dict 可选键，旧档无 → 全默认可见/正常高度）::

    node["collapsed"] = bool
    node["children"]  = [节点 id, …]      # 仅子图收束时有意义
"""
from __future__ import annotations

__all__ = ["COLLAPSED_CARD_H", "MAX_CHILDREN", "children_of", "is_collapsed",
           "hidden_ids", "can_collapse", "collapse_into", "expand_node",
           "collapsed_badge_text"]

#: 最小化后的卡片高度（世界单位；= header 文字档上限 + 上下留白）。
COLLAPSED_CARD_H = 26.0
#: 单卡 children 上限（防畸形存档塞入巨表）。
MAX_CHILDREN = 256


def children_of(node) -> list:
    """节点记录的子卡 id 表（非 list/畸形 → ``[]``；截断到上限）。"""
    if not isinstance(node, dict):
        return []
    raw = node.get("children")
    if not isinstance(raw, list):
        return []
    return [str(c) for c in raw[:MAX_CHILDREN] if isinstance(c, (str, int, float))]


def is_collapsed(node) -> bool:
    """节点是否处于折叠态（缺失/畸形 → False）。"""
    return bool(isinstance(node, dict) and node.get("collapsed"))


def _fold_once(by_id, hidden: set) -> bool:
    """一轮收敛：折叠父卡的 children 并入隐藏集；本轮有新命中 → True。"""
    grew = False
    for node in by_id.values():
        if not is_collapsed(node):
            continue
        for cid in children_of(node):
            if cid in by_id and cid not in hidden:
                hidden.add(cid)
                grew = True
    return grew


def hidden_ids(nodes) -> set:
    """当前被收起而隐藏的子卡 id 集合（折叠父卡的 children，传递闭包）。

    只收「折叠父卡」当前引用的 children；父卡被删/展开 → 自动恢复可见
    （不写子卡任何字段 = 展开即还原坐标）。
    """
    by_id = {str(n.get("id")): n for n in nodes or []
             if isinstance(n, dict) and n.get("id")}
    hidden: set = set()
    for _round in range(4):          # 有界传递闭包（防畸形环状 children 死循环）
        if not _fold_once(by_id, hidden):
            break
    return hidden


def _valid_members(by_id, parent_id, member_ids) -> list:
    """成员过滤：去自身、去不在图的（保持原序）。"""
    out = []
    for m in member_ids or ():
        mid = str(m)
        if mid != str(parent_id) and mid in by_id:
            out.append(mid)
    return out


def _member_reason(by_id, members) -> str:
    """成员集合法性（上限 / 嵌套）；通过 → ""。"""
    if len(members) > MAX_CHILDREN:
        return f"子卡超过上限 {MAX_CHILDREN}"
    if any(children_of(by_id[m]) for m in members):
        return "子卡自身已收束子图（不支持嵌套）"
    return ""


def can_collapse(nodes, parent_id, member_ids) -> str:
    """校验能否把 `member_ids` 收进 `parent_id`；返回拒绝原因（可折叠 → ""）。"""
    by_id = {str(n.get("id")): n for n in nodes or []
             if isinstance(n, dict) and n.get("id")}
    if str(parent_id) not in by_id:
        return "父卡不存在"
    members = _valid_members(by_id, parent_id, member_ids)
    if not members:
        return "没有可收束的卡（先多选其它卡）"
    reason = _member_reason(by_id, members)
    if reason:
        return reason
    if is_collapsed(by_id[str(parent_id)]):
        return "父卡已在折叠态"
    return ""


def collapse_into(nodes, parent_id, member_ids) -> list:
    """就地收束：父卡记 children + collapsed=True；返回实际收进的子卡 id 表。

    调用方先跑 `can_collapse`（本函数只做合法输入下的写入）。
    """
    by_id = {str(n.get("id")): n for n in nodes or []
             if isinstance(n, dict) and n.get("id")}
    parent = by_id.get(str(parent_id))
    if parent is None:
        return []
    members = [str(m) for m in member_ids or ()
               if str(m) != str(parent_id) and str(m) in by_id]
    if not members:
        return []
    parent["children"] = members[:MAX_CHILDREN]
    parent["collapsed"] = True
    return list(parent["children"])


def expand_node(node) -> bool:
    """展开（清 collapsed；children 记录保留 → 再折叠一键完成）。真变化 → True。"""
    if not is_collapsed(node):
        return False
    node["collapsed"] = False
    return True


def collapsed_badge_text(node) -> str:
    """折叠徽标文案（有 children → 「内部 N 张」；最小化无 children → 「已折叠」）。"""
    n = len([c for c in children_of(node)])
    return f"内部 {n} 张" if n else "已折叠"
